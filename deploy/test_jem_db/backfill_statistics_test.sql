-- ============================================================================
-- 과거 통계 백필 스크립트
-- ============================================================================
-- 스키마: jem_test (소스: jem_jh02)
-- 소스: plc_data_integrated, alm_history, tb_info_shift
-- 대상: tb_prod_shift_history → tb_prod_daily → 뷰 자동 반영
--       tb_prod_hourly, tb_prod_mode_change
--
-- 실행:
--   psql -h <host> -U <user> -d neurosense -f backfill_statistics.sql
--
-- 전제 조건:
--   - tb_info_shift 설정 완료
--   - tb_prod_shift_history 비어있을 것 (데이터 있으면 중단)
--   - plc_data_master에 '생산수량' description 태그 존재
--
-- 한계:
--   - PLC 누적값 리셋: LAG 기반 delta-sum 방식으로 정확 처리
--   - 동시 알람 정지시간: 구간 병합으로 처리 (정확도 높음)
--   - tb_prod_hourly의 정지시간: 시간 단위 정밀 복원 불가 → 0
--   - action_downtime: action_history 없으면 0
-- ============================================================================

BEGIN;


-- ============================================================================
-- 0. 사전 검증
-- ============================================================================
DO $$
DECLARE
    v_cnt       INTEGER;
    v_min_ts    TIMESTAMPTZ;
    v_max_ts    TIMESTAMPTZ;
BEGIN
    -- 기존 데이터 자동 정리 (재실행 안전장치)
    SELECT COUNT(*) INTO v_cnt
    FROM jem_test.tb_prod_shift_history;
    IF v_cnt > 0 THEN
        RAISE NOTICE '기존 데이터 정리 중...';
        TRUNCATE jem_test.tb_prod_shift_history;
        TRUNCATE jem_test.tb_prod_hourly;
        TRUNCATE jem_test.tb_prod_daily;
        TRUNCATE jem_test.tb_prod_mode_change;
        RAISE NOTICE '  TRUNCATE 완료 (shift_history: % 행 삭제)', v_cnt;
    END IF;

    -- plc_data_integrated 데이터 확인
    SELECT MIN(timestamp), MAX(timestamp) INTO v_min_ts, v_max_ts
    FROM jem_jh02.plc_data_integrated;
    IF v_min_ts IS NULL THEN
        RAISE EXCEPTION 'plc_data_integrated에 데이터가 없습니다.';
    END IF;

    -- tb_info_shift 확인
    SELECT COUNT(*) INTO v_cnt FROM jem_test.tb_info_shift;
    IF v_cnt = 0 THEN
        RAISE EXCEPTION 'tb_info_shift가 설정되지 않았습니다.';
    END IF;

    RAISE NOTICE '';
    RAISE NOTICE '=== 과거 통계 백필 시작 ===';
    RAISE NOTICE '데이터 범위: % ~ %', v_min_ts, v_max_ts;
END $$;


-- ============================================================================
-- 1. 교대 구간 생성 (temp)
-- ============================================================================
-- tb_info_shift × 데이터 범위 날짜 → 모든 교대 구간
CREATE TEMP TABLE tmp_shifts AS
WITH data_range AS (
    SELECT
        (MIN(timestamp) AT TIME ZONE 'Asia/Seoul')::date AS start_date,
        (MAX(timestamp) AT TIME ZONE 'Asia/Seoul')::date AS end_date
    FROM jem_jh02.plc_data_integrated
),
dates AS (
    SELECT generate_series(
        (SELECT start_date FROM data_range),
        (SELECT end_date FROM data_range),
        '1 day'::interval
    )::date AS d
)
SELECT
    sc.shift_type,
    sc.shift_order,
    -- shift_start (TIMESTAMPTZ)
    CASE
        WHEN sc.start_time < sc.end_time THEN
            (dt.d || ' ' || sc.start_time)::timestamp AT TIME ZONE 'Asia/Seoul'
        ELSE
            (dt.d || ' ' || sc.start_time)::timestamp AT TIME ZONE 'Asia/Seoul'
    END AS shift_start,
    -- shift_end (TIMESTAMPTZ)
    CASE
        WHEN sc.start_time < sc.end_time THEN
            (dt.d || ' ' || sc.end_time)::timestamp AT TIME ZONE 'Asia/Seoul'
        ELSE
            ((dt.d + 1) || ' ' || sc.end_time)::timestamp AT TIME ZONE 'Asia/Seoul'
    END AS shift_end,
    sc.line_id,
    -- 마지막 교대 order (일별 집계 판별용)
    (SELECT MAX(s2.shift_order) FROM jem_test.tb_info_shift s2 WHERE s2.line_id = sc.line_id) AS max_shift_order
FROM dates dt
CROSS JOIN jem_test.tb_info_shift sc
WHERE
    -- 미래 교대 제외
    CASE
        WHEN sc.start_time < sc.end_time THEN
            (dt.d || ' ' || sc.start_time)::timestamp AT TIME ZONE 'Asia/Seoul'
        ELSE
            (dt.d || ' ' || sc.start_time)::timestamp AT TIME ZONE 'Asia/Seoul'
    END < NOW();

CREATE INDEX idx_tmp_shifts ON tmp_shifts (shift_start, shift_end);

DO $$
DECLARE v_cnt INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_cnt FROM tmp_shifts;
    RAISE NOTICE '[1/8] 교대 구간 생성: % 개', v_cnt;
END $$;


-- ============================================================================
-- 2. 생산/NG 태그 식별
-- ============================================================================
CREATE TEMP TABLE tmp_tags AS
SELECT plc_id, tag_id,
    CASE
        WHEN description LIKE '%생산수량%' THEN 'prod'
        WHEN description LIKE '%NG%수량%' THEN 'ng'
    END AS tag_type
FROM jem_jh02.plc_data_master
WHERE description LIKE '%생산수량%' OR description LIKE '%NG%수량%';

DO $$
DECLARE v_prod INTEGER; v_ng INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_prod FROM tmp_tags WHERE tag_type = 'prod';
    SELECT COUNT(*) INTO v_ng  FROM tmp_tags WHERE tag_type = 'ng';
    RAISE NOTICE '[2/8] 태그 식별 — 생산수량: % 개, NG수량: % 개', v_prod, v_ng;
    IF v_prod = 0 THEN
        RAISE EXCEPTION '생산수량 태그를 찾을 수 없습니다 (plc_data_master.description LIKE ''%%생산수량%%'')';
    END IF;
END $$;


-- ============================================================================
-- 3. 교대별 생산/NG 첫값·마지막값 집계
-- ============================================================================
-- 교대 구간별 생산/NG 집계
-- first_val: hourly 백필에서 교대 시작 기준값으로 사용
-- delta_sum: 연속 값 비교로 리셋 감지, 실제 생산량 합산 (shift_history 백필용)
CREATE TEMP TABLE tmp_shift_prod AS
WITH bounds AS (
    -- 각 교대 구간 × 각 태그의 min/max timestamp
    SELECT
        sp.shift_type, sp.shift_start, sp.shift_end,
        sp.shift_order, sp.max_shift_order,
        t.plc_id, t.tag_id, t.tag_type,
        MIN(i.timestamp) AS first_ts,
        MAX(i.timestamp) AS last_ts
    FROM tmp_shifts sp
    JOIN tmp_tags t ON TRUE
    JOIN jem_jh02.plc_master pm ON pm.plc_id = t.plc_id
    JOIN jem_jh02.plc_data_integrated i
        ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        AND i.timestamp >= sp.shift_start AND i.timestamp < sp.shift_end
    WHERE sp.line_id = pm.line_id
    GROUP BY sp.shift_type, sp.shift_start, sp.shift_end,
             sp.shift_order, sp.max_shift_order,
             t.plc_id, t.tag_id, t.tag_type
),
-- 연속 값 비교로 리셋 감지하여 delta 합산
-- NOTE: 비정상 값(>100000) 필터 — PLC 통신 오류로 인한 이상값 방지
raw_with_lag AS (
    SELECT
        t.plc_id, t.tag_id, t.tag_type,
        sp.shift_start,
        COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0) AS val,
        LAG(COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0))
            OVER (PARTITION BY t.plc_id, t.tag_id, sp.shift_start ORDER BY i.timestamp) AS prev_val
    FROM tmp_shifts sp
    JOIN tmp_tags t ON TRUE
    JOIN jem_jh02.plc_master pm ON pm.plc_id = t.plc_id
    JOIN jem_jh02.plc_data_integrated i
        ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        AND i.timestamp >= sp.shift_start AND i.timestamp < sp.shift_end
    WHERE sp.line_id = pm.line_id
      AND COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0) < 100000
),
delta_sums AS (
    SELECT plc_id, tag_type, shift_start,
        SUM(GREATEST(
            CASE
                WHEN val >= COALESCE(prev_val, val)
                THEN val - COALESCE(prev_val, val)
                ELSE val  -- 리셋: 리셋 후 누적값 = 해당 구간 생산량
            END, 0
        )) AS delta_sum
    FROM raw_with_lag
    GROUP BY plc_id, tag_type, shift_start
)
SELECT
    b.plc_id,
    b.shift_type,
    b.shift_start,
    b.shift_end,
    b.shift_order,
    b.max_shift_order,
    b.tag_type,
    -- 첫 값 (교대 시작 시점의 누적값, hourly 백필용)
    (SELECT COALESCE(v_float, v_bigint::numeric, v_int::numeric, 0)
     FROM jem_jh02.plc_data_integrated
     WHERE plc_id = b.plc_id AND tag_id = b.tag_id AND timestamp = b.first_ts
     LIMIT 1) AS first_val,
    -- 마지막 값 (참조용)
    (SELECT COALESCE(v_float, v_bigint::numeric, v_int::numeric, 0)
     FROM jem_jh02.plc_data_integrated
     WHERE plc_id = b.plc_id AND tag_id = b.tag_id AND timestamp = b.last_ts
     LIMIT 1) AS last_val,
    -- 리셋 감지 delta 합산 (실제 생산량)
    COALESCE(ds.delta_sum, 0) AS delta_sum
FROM bounds b
LEFT JOIN delta_sums ds
    ON ds.plc_id = b.plc_id AND ds.tag_type = b.tag_type AND ds.shift_start = b.shift_start;

CREATE INDEX idx_tmp_shift_prod ON tmp_shift_prod (plc_id, shift_start, tag_type);

DO $$
DECLARE v_cnt INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_cnt FROM tmp_shift_prod WHERE tag_type = 'prod';
    RAISE NOTICE '[3/8] 교대별 생산 데이터: % 건', v_cnt;
END $$;


-- ============================================================================
-- 4. 알람 정지시간 (교대별, 중복 구간 병합)
-- ============================================================================
-- alm_history의 TRUE→FALSE 페어를 교대 구간에 클리핑하고
-- 동시 발생 알람은 구간 병합(gaps-and-islands)으로 처리
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    CREATE TEMP TABLE tmp_alm_downtime AS
    WITH
    -- 4-0. publisher 초기화 일괄 OFF 시점 감지
    -- 같은 plc_id에서 1초 이내에 10개 이상 태그가 동시에 OFF → 초기화 시점
    bulk_off_times AS (
        SELECT plc_id, date_trunc('second', timestamp) AS bulk_sec
        FROM jem_jh02.alm_history
        WHERE v_bool = FALSE
        GROUP BY plc_id, date_trunc('second', timestamp)
        HAVING COUNT(DISTINCT tag_id) >= 10
    ),
    -- 4-1. 알람 ON/OFF 페어링 (원본 데이터 전체 사용)
    alarm_events AS (
        SELECT
            plc_id, tag_id, timestamp, v_bool,
            LEAD(timestamp) OVER (PARTITION BY plc_id, tag_id ORDER BY timestamp) AS next_ts,
            LEAD(v_bool) OVER (PARTITION BY plc_id, tag_id ORDER BY timestamp) AS next_bool
        FROM jem_jh02.alm_history
    ),
    -- TRUE → FALSE 페어 추출 후, bulk OFF 시점의 OFF는 해당 시점으로 잘라줌
    -- OFF가 없는 알람(next_bool != FALSE)은 다음 bulk OFF 시점을 alarm_off로 사용
    alarm_periods AS (
        SELECT
            ae.plc_id,
            ae.timestamp AS alarm_on,
            CASE
                -- 정상 OFF가 있으면 그대로 사용
                WHEN ae.next_bool = FALSE THEN ae.next_ts
                -- OFF 없으면 해당 알람 이후 가장 가까운 bulk OFF 시점을 종료로 사용
                ELSE (SELECT MIN(b.bulk_sec)
                      FROM bulk_off_times b
                      WHERE b.plc_id = ae.plc_id AND b.bulk_sec > ae.timestamp)
            END AS alarm_off
        FROM alarm_events ae
        WHERE ae.v_bool = TRUE
    ),
    -- 4-2. 교대 구간에 클리핑 (교대 경계로 자르기)
    alarm_in_shifts AS (
        SELECT
            sp.shift_type, sp.shift_start, sp.shift_end,
            ap.plc_id,
            GREATEST(ap.alarm_on, sp.shift_start) AS seg_start,
            LEAST(COALESCE(ap.alarm_off, sp.shift_end), sp.shift_end) AS seg_end
        FROM alarm_periods ap
        JOIN jem_jh02.plc_master pm ON pm.plc_id = ap.plc_id
        JOIN tmp_shifts sp
            ON sp.line_id = pm.line_id
            AND ap.alarm_on < sp.shift_end
            AND COALESCE(ap.alarm_off, '9999-12-31'::timestamptz) > sp.shift_start
        WHERE GREATEST(ap.alarm_on, sp.shift_start)
            < LEAST(COALESCE(ap.alarm_off, sp.shift_end), sp.shift_end)
    ),
    -- 4-3. 중복 구간 병합 (gaps-and-islands)
    -- 동시 발생 알람의 겹치는 정지 구간을 하나로 합침
    with_groups AS (
        SELECT *,
            CASE
                WHEN seg_start > MAX(seg_end) OVER (
                    PARTITION BY plc_id, shift_start
                    ORDER BY seg_start
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) THEN 1
                ELSE 0
            END AS new_group
        FROM alarm_in_shifts
    ),
    grouped AS (
        SELECT *,
            SUM(new_group) OVER (
                PARTITION BY plc_id, shift_start
                ORDER BY seg_start
            ) AS grp
        FROM with_groups
    ),
    merged AS (
        SELECT
            plc_id, shift_start, shift_end, shift_type,
            MIN(seg_start) AS m_start,
            MAX(seg_end)   AS m_end
        FROM grouped
        GROUP BY plc_id, shift_start, shift_end, shift_type, grp
    )
    -- 교대별 총 알람 정지시간 (분)
    SELECT
        plc_id,
        shift_start,
        shift_end,
        shift_type,
        ROUND(SUM(EXTRACT(EPOCH FROM (m_end - m_start)) / 60.0)::numeric, 2) AS alm_downtime_min
    FROM merged
    GROUP BY plc_id, shift_start, shift_end, shift_type;

    SELECT COUNT(*) INTO v_cnt FROM tmp_alm_downtime;
    RAISE NOTICE '[4/8] 교대별 알람 정지시간: % 건', v_cnt;

EXCEPTION WHEN OTHERS THEN
    -- alm_history 없거나 에러 시 빈 테이블 생성
    CREATE TEMP TABLE tmp_alm_downtime (
        plc_id INTEGER, shift_start TIMESTAMPTZ, shift_end TIMESTAMPTZ,
        shift_type VARCHAR(10), alm_downtime_min NUMERIC
    );
    RAISE NOTICE '[4/8] 알람 정지시간 계산 스킵 (0으로 처리): %', SQLERRM;
END $$;


-- ============================================================================
-- 5. tb_prod_shift_history 백필
-- ============================================================================
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    INSERT INTO jem_test.tb_prod_shift_history (
        plc_id, shift_type, shift_start, shift_end, target_qty,
        shift_production, shift_ng_qty,
        alm_downtime_min, action_downtime_min, total_downtime_min, target_time_min,
        auto_run_min,
        production_rate, first_pass_yield,
        alm_operating_rate, action_operating_rate, time_operating_rate, qty_operating_rate,
        run_operating_rate, oee
    )
    WITH
    -- 교대별 생산수량 (delta_sum: 리셋 감지하여 실제 생산량 합산)
    prod_data AS (
        SELECT plc_id, shift_type, shift_start, shift_end,
               shift_order, max_shift_order,
               delta_sum AS shift_production
        FROM tmp_shift_prod
        WHERE tag_type = 'prod'
    ),
    -- 교대별 NG수량
    ng_data AS (
        SELECT plc_id, shift_start,
               delta_sum AS shift_ng_qty
        FROM tmp_shift_prod
        WHERE tag_type = 'ng'
    ),
    -- 결합
    combined AS (
        SELECT
            p.plc_id,
            p.shift_type,
            p.shift_start,
            p.shift_end,
            -- 목표수량
            COALESCE(pt.target_qty, 0) AS target_qty,
            -- 생산/NG
            p.shift_production,
            COALESCE(n.shift_ng_qty, 0) AS shift_ng_qty,
            -- 정지시간
            COALESCE(ad.alm_downtime_min, 0) AS alm_downtime_min,
            0::numeric AS action_downtime_min,
            COALESCE(ad.alm_downtime_min, 0) AS total_downtime_min,
            -- 목표 생산시간 (교대 시간, 분)
            ROUND((EXTRACT(EPOCH FROM (p.shift_end - p.shift_start)) / 60.0)::numeric, 2) AS target_time_min
        FROM prod_data p
        LEFT JOIN ng_data n
            ON n.plc_id = p.plc_id AND n.shift_start = p.shift_start
        LEFT JOIN tmp_alm_downtime ad
            ON ad.plc_id = p.plc_id AND ad.shift_start = p.shift_start
        LEFT JOIN jem_jh02.plc_master pm
            ON pm.plc_id = p.plc_id
        LEFT JOIN jem_test.tb_info_target pt
            ON pt.line_id = pm.line_id AND pt.target_type = p.shift_type
    )
    SELECT
        plc_id, shift_type, shift_start, shift_end, target_qty,
        shift_production, shift_ng_qty,
        alm_downtime_min, action_downtime_min, total_downtime_min, target_time_min,
        0::numeric,  -- auto_run_min (과거 데이터 복원 불가, 실시간부터 누적)
        -- 생산률 (NUMERIC(8,2) overflow 방지: LEAST 클램프)
        CASE WHEN target_qty > 0
            THEN GREATEST(LEAST(ROUND((shift_production / target_qty * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 직행률: (생산 - NG) / 생산 × 100
        CASE WHEN shift_production > 0
            THEN GREATEST(LEAST(ROUND(((shift_production - shift_ng_qty) / shift_production * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 알람 가동률
        CASE WHEN target_time_min > 0
            THEN GREATEST(LEAST(ROUND(((target_time_min - alm_downtime_min) / target_time_min * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 액션 가동률
        CASE WHEN target_time_min > 0
            THEN GREATEST(LEAST(ROUND(((target_time_min - action_downtime_min) / target_time_min * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 시간 가동률
        CASE WHEN target_time_min > 0
            THEN GREATEST(LEAST(ROUND(((target_time_min - total_downtime_min) / target_time_min * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 수량 가동률: 실제생산 / 이론생산 × 100 (이론생산 = 가동시간 × 60 / 사이클타임)
        CASE WHEN (target_time_min - total_downtime_min) >= 10 THEN
            GREATEST(LEAST(ROUND((shift_production / ((target_time_min - total_downtime_min) * 60 / COALESCE(
                (SELECT cycle_time_sec FROM jem_test.tb_info_cycle_time
                 WHERE line_id = (SELECT line_id FROM jem_jh02.plc_master WHERE plc_id = combined.plc_id)),
                1)) * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE NULL END,
        0,  -- run_operating_rate (과거 데이터 복원 불가)
        -- OEE = (가동시간/교대시간) × (실제생산/이론생산) × ((생산-NG)/생산) × 100
        -- 중간 곱셈 overflow 방지를 위해 비율(0~1)로 계산 후 × 100
        CASE WHEN (target_time_min - total_downtime_min) >= 10
                  AND target_time_min > 0
                  AND shift_production > 0
            THEN GREATEST(LEAST(ROUND((
                ((target_time_min - total_downtime_min) / target_time_min)
                * (shift_production / ((target_time_min - total_downtime_min) * 60 / COALESCE(
                    (SELECT cycle_time_sec FROM jem_test.tb_info_cycle_time
                     WHERE line_id = (SELECT line_id FROM jem_jh02.plc_master WHERE plc_id = combined.plc_id)),
                    1)))
                * ((shift_production - shift_ng_qty) / shift_production)
                * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE NULL END
    FROM combined
    ORDER BY plc_id, shift_start;

    GET DIAGNOSTICS v_cnt = ROW_COUNT;
    RAISE NOTICE '[5/8] tb_prod_shift_history: % 행 INSERT', v_cnt;
END $$;


-- ============================================================================
-- 6. tb_prod_hourly 백필
-- ============================================================================
-- plc_data_integrated에서 시간별 누적값 추출 → 교대 컨텍스트 매핑
-- 정지시간(hour_downtime_min, cumul_downtime_min)은 시간 단위 복원 불가 → 0
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    INSERT INTO jem_test.tb_prod_hourly (
        plc_id, snapshot_at, shift_type,
        hour_production, hour_ng_qty, hour_downtime_min,
        cumul_production, cumul_ng_qty, cumul_downtime_min,
        auto_run_min,
        production_rate, first_pass_yield, time_operating_rate, qty_operating_rate,
        run_operating_rate, oee
    )
    WITH
    -- 시간별 delta-sum: LAG를 서브쿼리로 분리 후 SUM 집계
    -- NOTE: 비정상 값(>100000) 필터 — PLC 통신 오류로 인한 이상값 방지
    -- 교대 시작 기준 1시간 슬롯으로 snapshot_at 계산
    -- 예) shift_start=8:30, timestamp=10:45 → 8:30 + floor(135min/60)*1h = 10:30
    raw_with_lag AS (
        SELECT
            t.plc_id,
            t.tag_id,
            t.tag_type,
            i.timestamp,
            sp.shift_type,
            sp.shift_start,
            sp.shift_start
                + (FLOOR(EXTRACT(EPOCH FROM (i.timestamp - sp.shift_start)) / 3600) * INTERVAL '1 hour')
                AS snapshot_at,
            COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0) AS val,
            LAG(COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0))
                OVER (PARTITION BY t.plc_id, t.tag_id ORDER BY i.timestamp) AS prev_val,
            COALESCE(pt.target_qty, 0) AS target_qty
        FROM tmp_tags t
        JOIN jem_jh02.plc_data_integrated i
            ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        JOIN jem_jh02.plc_master pm ON pm.plc_id = t.plc_id
        JOIN tmp_shifts sp
            ON sp.line_id = pm.line_id
            AND i.timestamp >= sp.shift_start AND i.timestamp < sp.shift_end
        LEFT JOIN jem_test.tb_info_target pt
            ON pt.line_id = pm.line_id AND pt.target_type = sp.shift_type
        WHERE COALESCE(i.v_float, i.v_bigint::numeric, i.v_int::numeric, 0) < 100000
    ),
    hourly_delta AS (
        SELECT
            plc_id,
            tag_type,
            snapshot_at,
            shift_type,
            shift_start,
            target_qty,
            SUM(GREATEST(
                CASE
                    WHEN val >= COALESCE(prev_val, val)
                    THEN val - COALESCE(prev_val, val)
                    ELSE val  -- 리셋 후 값
                END, 0
            )) AS hour_delta
        FROM raw_with_lag
        GROUP BY plc_id, tag_type, tag_id, snapshot_at, shift_type, shift_start, target_qty
    ),
    -- 시간별 생산/NG delta 집계
    hourly_agg AS (
        SELECT
            plc_id,
            snapshot_at,
            shift_type,
            shift_start,
            target_qty,
            COALESCE(SUM(CASE WHEN tag_type = 'prod' THEN hour_delta END), 0) AS hour_production,
            COALESCE(SUM(CASE WHEN tag_type = 'ng' THEN hour_delta END), 0) AS hour_ng_qty
        FROM hourly_delta
        GROUP BY plc_id, snapshot_at, shift_type, shift_start, target_qty
    ),
    -- 교대 컨텍스트 (이미 매핑됨)
    with_shift AS (
        SELECT
            plc_id,
            snapshot_at,
            shift_type,
            shift_start,
            hour_production,
            hour_ng_qty,
            target_qty
        FROM hourly_agg
    ),
    -- 교대 시작부터 누적 (시간별 delta의 running sum)
    with_cumul AS (
        SELECT ws.*,
            SUM(hour_production) OVER (PARTITION BY plc_id, shift_start ORDER BY snapshot_at) AS cumul_production,
            SUM(hour_ng_qty) OVER (PARTITION BY plc_id, shift_start ORDER BY snapshot_at) AS cumul_ng_qty
        FROM with_shift ws
    )
    SELECT
        plc_id,
        snapshot_at,
        shift_type,
        hour_production,
        hour_ng_qty,
        0 AS hour_downtime_min,
        cumul_production,
        cumul_ng_qty,
        0 AS cumul_downtime_min,
        0 AS auto_run_min,  -- 과거 데이터 복원 불가
        -- 생산률 (NUMERIC(8,2) overflow 방지)
        CASE WHEN target_qty > 0
            THEN GREATEST(LEAST(ROUND((cumul_production / target_qty * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 직행률: (생산 - NG) / 생산 × 100
        CASE WHEN cumul_production > 0
            THEN GREATEST(LEAST(ROUND(((cumul_production - cumul_ng_qty) / cumul_production * 100)::numeric, 2), 999999.99), -999999.99)
            ELSE 0 END,
        -- 가동률 (정지시간/자동운전 없으므로 0)
        0 AS time_operating_rate,
        0 AS qty_operating_rate,
        0 AS run_operating_rate,
        NULL::numeric AS oee
    FROM with_cumul
    ON CONFLICT (plc_id, snapshot_at) DO NOTHING;

    GET DIAGNOSTICS v_cnt = ROW_COUNT;
    RAISE NOTICE '[6/8] tb_prod_hourly: % 행 INSERT', v_cnt;
END $$;


-- ============================================================================
-- 7. tb_prod_daily 백필 (기존 함수 재활용)
-- ============================================================================
DO $$
DECLARE
    v_result INTEGER;
BEGIN
    SELECT jem_test.fn_backfill_daily_statistics() INTO v_result;
    RAISE NOTICE '[7/8] tb_prod_daily: % 행 UPSERT', v_result;
END $$;


-- ============================================================================
-- 8. tb_prod_mode_change 백필 (자동/수동 전환 이력 + 전체 TL 스냅샷)
-- ============================================================================
-- plc_data_integrated에서 Y401/Y402의 v_bool 변경을 LAG로 감지
-- 변경 시점에 해당 PLC의 Y40C/Y40D/Y40E TL 상태 캡처
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    INSERT INTO jem_test.tb_prod_mode_change (
        ts, plc_id, change_type,
        is_auto_mode, is_manual_mode,
        shift_type, tl_red, tl_green, tl_yellow
    )
    WITH
    -- Y401/Y402/Y40C/Y40D/Y40E를 PLC별 시간순으로 피벗 (1회 스캔)
    pivoted AS (
        SELECT
            i.plc_id,
            i.timestamp AS event_ts,
            bool_or(CASE WHEN m.tag_name = 'Y401' THEN i.v_bool END) AS y401,
            bool_or(CASE WHEN m.tag_name = 'Y402' THEN i.v_bool END) AS y402,
            bool_or(CASE WHEN m.tag_name = 'Y40C' THEN i.v_bool END) AS y40c,
            bool_or(CASE WHEN m.tag_name = 'Y40D' THEN i.v_bool END) AS y40d,
            bool_or(CASE WHEN m.tag_name = 'Y40E' THEN i.v_bool END) AS y40e
        FROM jem_jh02.plc_data_integrated i
        JOIN jem_jh02.plc_data_master m
            ON m.plc_id = i.plc_id AND m.tag_id = i.tag_id
        WHERE m.tag_name IN ('Y401', 'Y402', 'Y40C', 'Y40D', 'Y40E')
        GROUP BY i.plc_id, i.timestamp
    ),
    -- NULL carry-forward용 그룹 번호 (non-NULL이 나올 때마다 그룹 증가)
    with_groups AS (
        SELECT *,
            COUNT(y401) OVER (PARTITION BY plc_id ORDER BY event_ts) AS g401,
            COUNT(y402) OVER (PARTITION BY plc_id ORDER BY event_ts) AS g402,
            COUNT(y40c) OVER (PARTITION BY plc_id ORDER BY event_ts) AS g40c,
            COUNT(y40d) OVER (PARTITION BY plc_id ORDER BY event_ts) AS g40d,
            COUNT(y40e) OVER (PARTITION BY plc_id ORDER BY event_ts) AS g40e
        FROM pivoted
    ),
    -- NULL 채우기: 각 그룹의 FIRST_VALUE = 가장 최근 non-NULL 값
    filled AS (
        SELECT
            plc_id, event_ts,
            FIRST_VALUE(y401) OVER (PARTITION BY plc_id, g401 ORDER BY event_ts) AS y401,
            FIRST_VALUE(y402) OVER (PARTITION BY plc_id, g402 ORDER BY event_ts) AS y402,
            FIRST_VALUE(y40c) OVER (PARTITION BY plc_id, g40c ORDER BY event_ts) AS y40c,
            FIRST_VALUE(y40d) OVER (PARTITION BY plc_id, g40d ORDER BY event_ts) AS y40d,
            FIRST_VALUE(y40e) OVER (PARTITION BY plc_id, g40e ORDER BY event_ts) AS y40e
        FROM with_groups
    ),
    -- 이전 Y401/Y402 값으로 변경 감지
    with_prev AS (
        SELECT *,
            LAG(y401) OVER w AS prev_y401,
            LAG(y402) OVER w AS prev_y402
        FROM filled
        WINDOW w AS (PARTITION BY plc_id ORDER BY event_ts)
    ),
    -- Y401 또는 Y402가 변경된 시점만 필터
    changes AS (
        SELECT *,
            CASE
                WHEN y401 IS DISTINCT FROM prev_y401 THEN
                    CASE WHEN y401 THEN 'auto_on' ELSE 'auto_off' END
                WHEN y402 IS DISTINCT FROM prev_y402 THEN
                    CASE WHEN y402 THEN 'manual_on' ELSE 'manual_off' END
            END AS change_type
        FROM with_prev
        WHERE (y401 IS DISTINCT FROM prev_y401 AND prev_y401 IS NOT NULL)
           OR (y402 IS DISTINCT FROM prev_y402 AND prev_y402 IS NOT NULL)
    ),
    -- 교대 매핑
    with_shift AS (
        SELECT c.*, sp.shift_type
        FROM changes c
        LEFT JOIN jem_jh02.plc_master pm ON pm.plc_id = c.plc_id
        LEFT JOIN tmp_shifts sp
            ON sp.line_id = pm.line_id
            AND c.event_ts >= sp.shift_start AND c.event_ts < sp.shift_end
    )
    SELECT
        event_ts, plc_id, change_type,
        y401 AS is_auto_mode,
        y402 AS is_manual_mode,
        shift_type, y40c AS tl_red, y40d AS tl_green, y40e AS tl_yellow
    FROM with_shift
    ORDER BY event_ts;

    GET DIAGNOSTICS v_cnt = ROW_COUNT;
    RAISE NOTICE '[8/8] tb_prod_mode_change: % 행 INSERT', v_cnt;
END $$;


-- ============================================================================
-- 정리 + 결과 요약
-- ============================================================================
DROP TABLE IF EXISTS tmp_shifts;
DROP TABLE IF EXISTS tmp_tags;
DROP TABLE IF EXISTS tmp_shift_prod;
DROP TABLE IF EXISTS tmp_alm_downtime;

DO $$
DECLARE
    v_sh INTEGER; v_hr INTEGER; v_dy INTEGER; v_mc INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_sh FROM jem_test.tb_prod_shift_history;
    SELECT COUNT(*) INTO v_hr FROM jem_test.tb_prod_hourly;
    SELECT COUNT(*) INTO v_dy FROM jem_test.tb_prod_daily;
    SELECT COUNT(*) INTO v_mc FROM jem_test.tb_prod_mode_change;

    RAISE NOTICE '';
    RAISE NOTICE '=== 백필 완료 ===';
    RAISE NOTICE 'tb_prod_shift_history: % 행', v_sh;
    RAISE NOTICE 'tb_prod_hourly:        % 행', v_hr;
    RAISE NOTICE 'tb_prod_daily:         % 행', v_dy;
    RAISE NOTICE 'tb_prod_mode_change:   % 행', v_mc;
    RAISE NOTICE '';
    RAISE NOTICE '뷰 자동 반영:';
    RAISE NOTICE '  vw_prod_weekly / monthly / yearly';
    RAISE NOTICE '  vw_prod_shift_weekly / shift_monthly';
    RAISE NOTICE '  vw_alarm_ranking_daily / weekly / monthly (alm_history 직접 조회)';
END $$;

COMMIT;
