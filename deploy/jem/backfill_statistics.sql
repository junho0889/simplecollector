-- ============================================================================
-- 과거 통계 백필 스크립트
-- ============================================================================
-- 스키마: jem_jh02
-- 소스: plc_data_integrated, alm_history, shift_config
-- 대상: production_shift_history → production_daily → 뷰 자동 반영
--       production_hourly
--
-- 실행:
--   psql -h <host> -U <user> -d neurosense -f backfill_statistics.sql
--
-- 전제 조건:
--   - shift_config 설정 완료
--   - production_shift_history 비어있을 것 (데이터 있으면 중단)
--   - plc_data_master에 '생산수량' description 태그 존재
--
-- 한계:
--   - PLC 누적값 리셋: last < first 시 last만 사용 (근사값)
--   - 동시 알람 정지시간: 구간 병합으로 처리 (정확도 높음)
--   - production_hourly의 정지시간: 시간 단위 정밀 복원 불가 → 0
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
    -- production_shift_history 비어있는지 확인
    SELECT COUNT(*) INTO v_cnt FROM jem_jh02.production_shift_history;
    IF v_cnt > 0 THEN
        RAISE EXCEPTION 'production_shift_history에 이미 % 행 존재. TRUNCATE 후 재실행하세요.', v_cnt;
    END IF;

    -- plc_data_integrated 데이터 확인
    SELECT MIN(timestamp), MAX(timestamp) INTO v_min_ts, v_max_ts
    FROM jem_jh02.plc_data_integrated;
    IF v_min_ts IS NULL THEN
        RAISE EXCEPTION 'plc_data_integrated에 데이터가 없습니다.';
    END IF;

    -- shift_config 확인
    SELECT COUNT(*) INTO v_cnt FROM jem_jh02.shift_config;
    IF v_cnt = 0 THEN
        RAISE EXCEPTION 'shift_config가 설정되지 않았습니다.';
    END IF;

    RAISE NOTICE '';
    RAISE NOTICE '=== 과거 통계 백필 시작 ===';
    RAISE NOTICE '데이터 범위: % ~ %', v_min_ts, v_max_ts;
END $$;


-- ============================================================================
-- 1. 교대 구간 생성 (temp)
-- ============================================================================
-- shift_config × 데이터 범위 날짜 → 모든 교대 구간
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
    -- 마지막 교대 order (일별 집계 판별용)
    (SELECT MAX(s2.shift_order) FROM jem_jh02.shift_config s2) AS max_shift_order
FROM dates dt
CROSS JOIN jem_jh02.shift_config sc
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
    RAISE NOTICE '[1/7] 교대 구간 생성: % 개', v_cnt;
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
    RAISE NOTICE '[2/7] 태그 식별 — 생산수량: % 개, NG수량: % 개', v_prod, v_ng;
    IF v_prod = 0 THEN
        RAISE EXCEPTION '생산수량 태그를 찾을 수 없습니다 (plc_data_master.description LIKE ''%%생산수량%%'')';
    END IF;
END $$;


-- ============================================================================
-- 3. 교대별 생산/NG 첫값·마지막값 집계
-- ============================================================================
-- 교대 구간의 첫 값(first_val)과 마지막 값(last_val)을 구하여
-- shift_production = last_val - first_val 계산에 사용
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
    JOIN jem_jh02.plc_data_integrated i
        ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        AND i.timestamp >= sp.shift_start AND i.timestamp < sp.shift_end
    GROUP BY sp.shift_type, sp.shift_start, sp.shift_end,
             sp.shift_order, sp.max_shift_order,
             t.plc_id, t.tag_id, t.tag_type
)
SELECT
    b.plc_id,
    b.shift_type,
    b.shift_start,
    b.shift_end,
    b.shift_order,
    b.max_shift_order,
    b.tag_type,
    -- 첫 값 (교대 시작 시점의 누적값)
    (SELECT COALESCE(v_float, v_int::numeric, 0)
     FROM jem_jh02.plc_data_integrated
     WHERE plc_id = b.plc_id AND tag_id = b.tag_id AND timestamp = b.first_ts
     LIMIT 1) AS first_val,
    -- 마지막 값 (교대 종료 시점의 누적값)
    (SELECT COALESCE(v_float, v_int::numeric, 0)
     FROM jem_jh02.plc_data_integrated
     WHERE plc_id = b.plc_id AND tag_id = b.tag_id AND timestamp = b.last_ts
     LIMIT 1) AS last_val
FROM bounds b;

CREATE INDEX idx_tmp_shift_prod ON tmp_shift_prod (plc_id, shift_start, tag_type);

DO $$
DECLARE v_cnt INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_cnt FROM tmp_shift_prod WHERE tag_type = 'prod';
    RAISE NOTICE '[3/7] 교대별 생산 데이터: % 건', v_cnt;
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
    -- 4-1. 알람 ON/OFF 페어링 (LEAD로 다음 이벤트 매칭)
    alarm_events AS (
        SELECT
            plc_id, tag_id, timestamp, v_bool,
            LEAD(timestamp) OVER (PARTITION BY plc_id, tag_id ORDER BY timestamp) AS next_ts,
            LEAD(v_bool) OVER (PARTITION BY plc_id, tag_id ORDER BY timestamp) AS next_bool
        FROM jem_jh02.alm_history
    ),
    -- TRUE → FALSE 페어만 추출
    alarm_periods AS (
        SELECT
            plc_id,
            timestamp AS alarm_on,
            CASE WHEN next_bool = FALSE THEN next_ts ELSE NULL END AS alarm_off
        FROM alarm_events
        WHERE v_bool = TRUE
    ),
    -- 4-2. 교대 구간에 클리핑 (교대 경계로 자르기)
    alarm_in_shifts AS (
        SELECT
            sp.shift_type, sp.shift_start, sp.shift_end,
            ap.plc_id,
            GREATEST(ap.alarm_on, sp.shift_start) AS seg_start,
            LEAST(COALESCE(ap.alarm_off, sp.shift_end), sp.shift_end) AS seg_end
        FROM tmp_shifts sp
        JOIN alarm_periods ap
            ON ap.alarm_on < sp.shift_end
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
    RAISE NOTICE '[4/7] 교대별 알람 정지시간: % 건', v_cnt;

EXCEPTION WHEN OTHERS THEN
    -- alm_history 없거나 에러 시 빈 테이블 생성
    CREATE TEMP TABLE tmp_alm_downtime (
        plc_id INTEGER, shift_start TIMESTAMPTZ, shift_end TIMESTAMPTZ,
        shift_type VARCHAR(10), alm_downtime_min NUMERIC
    );
    RAISE NOTICE '[4/7] 알람 정지시간 계산 스킵 (0으로 처리): %', SQLERRM;
END $$;


-- ============================================================================
-- 5. production_shift_history 백필
-- ============================================================================
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    INSERT INTO jem_jh02.production_shift_history (
        plc_id, shift_type, shift_start, shift_end, target_qty,
        shift_production, shift_ng_qty,
        alm_downtime_min, action_downtime_min, total_downtime_min, target_time_min,
        achievement_rate, first_pass_yield,
        alm_operating_rate, action_operating_rate, operating_rate
    )
    WITH
    -- 교대별 생산수량
    prod_data AS (
        SELECT plc_id, shift_type, shift_start, shift_end,
               shift_order, max_shift_order,
               -- PLC 리셋 처리: last < first 이면 리셋 → last만 사용
               CASE WHEN last_val >= first_val
                    THEN last_val - first_val
                    ELSE last_val
               END AS shift_production
        FROM tmp_shift_prod
        WHERE tag_type = 'prod'
    ),
    -- 교대별 NG수량
    ng_data AS (
        SELECT plc_id, shift_start,
               CASE WHEN last_val >= first_val
                    THEN last_val - first_val
                    ELSE last_val
               END AS shift_ng_qty
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
        LEFT JOIN jem_jh02.production_target pt
            ON pt.line_id = pm.line_id AND pt.target_type = p.shift_type
    )
    SELECT
        plc_id, shift_type, shift_start, shift_end, target_qty,
        shift_production, shift_ng_qty,
        alm_downtime_min, action_downtime_min, total_downtime_min, target_time_min,
        -- 달성률
        CASE WHEN target_qty > 0
            THEN ROUND((shift_production / target_qty * 100)::numeric, 2)
            ELSE 0 END,
        -- 직행률
        CASE WHEN shift_production > 0
            THEN ROUND(((shift_production - shift_ng_qty) / shift_production * 100)::numeric, 2)
            ELSE 0 END,
        -- 알람 가동률
        CASE WHEN target_time_min > 0
            THEN ROUND(((target_time_min - alm_downtime_min) / target_time_min * 100)::numeric, 2)
            ELSE 0 END,
        -- 액션 가동률
        CASE WHEN target_time_min > 0
            THEN ROUND(((target_time_min - action_downtime_min) / target_time_min * 100)::numeric, 2)
            ELSE 0 END,
        -- 전체 가동률
        CASE WHEN target_time_min > 0
            THEN ROUND(((target_time_min - total_downtime_min) / target_time_min * 100)::numeric, 2)
            ELSE 0 END
    FROM combined
    ORDER BY plc_id, shift_start;

    GET DIAGNOSTICS v_cnt = ROW_COUNT;
    RAISE NOTICE '[5/7] production_shift_history: % 행 INSERT', v_cnt;
END $$;


-- ============================================================================
-- 6. production_hourly 백필
-- ============================================================================
-- plc_data_integrated에서 시간별 누적값 추출 → 교대 컨텍스트 매핑
-- 정지시간(hour_downtime_min, cumul_downtime_min)은 시간 단위 복원 불가 → 0
DO $$
DECLARE
    v_cnt INTEGER;
BEGIN
    INSERT INTO jem_jh02.production_hourly (
        plc_id, snapshot_at, shift_type,
        hour_production, hour_ng_qty, hour_downtime_min,
        cumul_production, cumul_ng_qty, cumul_downtime_min,
        achievement_rate, first_pass_yield, operating_rate
    )
    WITH
    -- 시간별 생산 누적값 (MAX = 누적값이므로 시간 내 최대 = 마지막값)
    hourly_prod AS (
        SELECT
            t.plc_id,
            date_trunc('hour', i.timestamp) AS snapshot_at,
            MAX(COALESCE(i.v_float, i.v_int::numeric, 0)) AS accumulated
        FROM tmp_tags t
        JOIN jem_jh02.plc_data_integrated i
            ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        WHERE t.tag_type = 'prod'
        GROUP BY t.plc_id, date_trunc('hour', i.timestamp)
    ),
    -- 시간별 NG 누적값
    hourly_ng AS (
        SELECT
            t.plc_id,
            date_trunc('hour', i.timestamp) AS snapshot_at,
            MAX(COALESCE(i.v_float, i.v_int::numeric, 0)) AS accumulated
        FROM tmp_tags t
        JOIN jem_jh02.plc_data_integrated i
            ON i.plc_id = t.plc_id AND i.tag_id = t.tag_id
        WHERE t.tag_type = 'ng'
        GROUP BY t.plc_id, date_trunc('hour', i.timestamp)
    ),
    -- 교대 컨텍스트 매핑
    with_shift AS (
        SELECT
            hp.plc_id,
            hp.snapshot_at,
            sp.shift_type,
            sp.shift_start,
            hp.accumulated AS prod_val,
            COALESCE(hn.accumulated, 0) AS ng_val,
            -- 교대 시작 시 첫 누적값 (shift_production 계산 기준)
            COALESCE(ps.first_val, 0) AS prod_shift_start,
            COALESCE(ns.first_val, 0) AS ng_shift_start,
            -- 목표수량
            COALESCE(pt.target_qty, 0) AS target_qty
        FROM hourly_prod hp
        -- 이 시간이 속한 교대 찾기
        JOIN tmp_shifts sp
            ON hp.snapshot_at >= sp.shift_start
            AND hp.snapshot_at < sp.shift_end
        LEFT JOIN hourly_ng hn
            ON hn.plc_id = hp.plc_id AND hn.snapshot_at = hp.snapshot_at
        -- 교대 시작 누적값
        LEFT JOIN tmp_shift_prod ps
            ON ps.plc_id = hp.plc_id AND ps.shift_start = sp.shift_start AND ps.tag_type = 'prod'
        LEFT JOIN tmp_shift_prod ns
            ON ns.plc_id = hp.plc_id AND ns.shift_start = sp.shift_start AND ns.tag_type = 'ng'
        -- 목표수량
        LEFT JOIN jem_jh02.plc_master pm ON pm.plc_id = hp.plc_id
        LEFT JOIN jem_jh02.production_target pt
            ON pt.line_id = pm.line_id AND pt.target_type = sp.shift_type
    ),
    -- 교대 시작부터 누적값 계산
    with_cumul AS (
        SELECT *,
            CASE
                WHEN prod_val >= prod_shift_start
                THEN prod_val - prod_shift_start
                ELSE prod_val  -- PLC 리셋
            END AS cumul_production,
            CASE
                WHEN ng_val >= ng_shift_start
                THEN ng_val - ng_shift_start
                ELSE ng_val
            END AS cumul_ng_qty
        FROM with_shift
    ),
    -- 시간별 델타 계산 (LAG: 같은 교대 내 이전 스냅샷과의 차이)
    with_delta AS (
        SELECT *,
            GREATEST(
                cumul_production - COALESCE(
                    LAG(cumul_production) OVER (PARTITION BY plc_id, shift_start ORDER BY snapshot_at),
                    0
                ), 0
            ) AS hour_production,
            GREATEST(
                cumul_ng_qty - COALESCE(
                    LAG(cumul_ng_qty) OVER (PARTITION BY plc_id, shift_start ORDER BY snapshot_at),
                    0
                ), 0
            ) AS hour_ng_qty
        FROM with_cumul
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
        -- 달성률
        CASE WHEN target_qty > 0
            THEN ROUND((cumul_production / target_qty * 100)::numeric, 2)
            ELSE 0 END,
        -- 직행률
        CASE WHEN cumul_production > 0
            THEN ROUND(((cumul_production - cumul_ng_qty) / cumul_production * 100)::numeric, 2)
            ELSE 0 END,
        -- 가동률 (정지시간 없으므로 0)
        0 AS operating_rate
    FROM with_delta
    ON CONFLICT (plc_id, snapshot_at) DO NOTHING;

    GET DIAGNOSTICS v_cnt = ROW_COUNT;
    RAISE NOTICE '[6/7] production_hourly: % 행 INSERT', v_cnt;
END $$;


-- ============================================================================
-- 7. production_daily 백필 (기존 함수 재활용)
-- ============================================================================
DO $$
DECLARE
    v_result INTEGER;
BEGIN
    SELECT jem_jh02.fn_backfill_daily_statistics() INTO v_result;
    RAISE NOTICE '[7/7] production_daily: % 행 UPSERT', v_result;
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
    v_sh INTEGER; v_hr INTEGER; v_dy INTEGER;
BEGIN
    SELECT COUNT(*) INTO v_sh FROM jem_jh02.production_shift_history;
    SELECT COUNT(*) INTO v_hr FROM jem_jh02.production_hourly;
    SELECT COUNT(*) INTO v_dy FROM jem_jh02.production_daily;

    RAISE NOTICE '';
    RAISE NOTICE '=== 백필 완료 ===';
    RAISE NOTICE 'production_shift_history: % 행', v_sh;
    RAISE NOTICE 'production_hourly:        % 행', v_hr;
    RAISE NOTICE 'production_daily:         % 행', v_dy;
    RAISE NOTICE '';
    RAISE NOTICE '뷰 자동 반영:';
    RAISE NOTICE '  v_production_weekly / monthly / yearly';
    RAISE NOTICE '  v_production_shift_weekly / shift_monthly';
    RAISE NOTICE '  v_alarm_ranking_daily / weekly / monthly (alm_history 직접 조회)';
END $$;

COMMIT;
