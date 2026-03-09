-- ============================================================================
-- 시간별 생산 통계 + 일/주/월/연 뷰
-- ============================================================================
-- 수동 실행용. {schema} → jem_jh02 로 치환하여 사용.
--
-- 의존 테이블 (이미 존재):
--   production_hourly      (continuous aggregate, plc_data_integrated 기반)
--   plc_data_reset_log     (리셋 시 last_value 기록)
--   plc_data_master        (D200 태그 매핑)
--   alm_duration_log       (알람 ON/OFF 지속시간)
--
-- 직행률 태그 (하드코딩):
--   중간직행률: plc_id=4 (PLC-D), tag_id=334 (D820)
--   최종직행률: plc_id=8 (PLC-J), tag_id=175 (D1244)
-- ============================================================================


-- ============================================================
-- 1. 시간별 생산 통계 테이블
-- ============================================================

CREATE TABLE IF NOT EXISTS {schema}.production_hourly_stats (
    bucket          TIMESTAMPTZ      NOT NULL,
    plc_id          SMALLINT         NOT NULL,
    shift_type      VARCHAR(5)       NOT NULL,   -- 'day' / 'night'

    -- 생산수 (D200 기반, 리셋 보정)
    production_qty  BIGINT           NOT NULL DEFAULT 0,
    reset_count     INT              NOT NULL DEFAULT 0,

    -- 직행률 (라인 전체 대표값)
    middle_yield    DOUBLE PRECISION,   -- 중간직행률 평균 (PLC-D D820)
    final_yield     DOUBLE PRECISION,   -- 최종직행률 평균 (PLC-J D1244)

    -- 정지 시간 (알람 기반)
    alarm_stop_sec  DOUBLE PRECISION NOT NULL DEFAULT 0,
    alarm_count     INT              NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),

    PRIMARY KEY (bucket, plc_id)
);

CREATE INDEX IF NOT EXISTS idx_phs_plc_bucket
    ON {schema}.production_hourly_stats (plc_id, bucket DESC);
CREATE INDEX IF NOT EXISTS idx_phs_shift
    ON {schema}.production_hourly_stats (shift_type, bucket DESC);


-- ============================================================
-- 2. 시간별 집계 함수 (최적화: production_hourly + reset_log 활용)
-- ============================================================
-- 호출: SELECT {schema}.fn_aggregate_hourly('2026-03-05 09:00:00+09');
--
-- 기존 LAG() 방식 대비 개선:
--   production_hourly (continuous aggregate)에서 first_val/last_val 읽기
--   + plc_data_reset_log에서 리셋 보정값 합산
--   → 시간당 1~2행만 읽으면 됨 (vs 3,600건 스캔)

CREATE OR REPLACE FUNCTION {schema}.fn_aggregate_hourly(
    p_bucket TIMESTAMPTZ
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_bucket_end  TIMESTAMPTZ := p_bucket + INTERVAL '1 hour';
    v_shift       VARCHAR(5);
    v_hour        INT;
    v_minute      INT;
    rec           RECORD;
BEGIN
    -- 교대 판정 (KST 기준: 08:30~20:30 = day)
    v_hour   := EXTRACT(HOUR FROM p_bucket AT TIME ZONE 'Asia/Seoul');
    v_minute := EXTRACT(MINUTE FROM p_bucket AT TIME ZONE 'Asia/Seoul');

    IF (v_hour > 8 OR (v_hour = 8 AND v_minute >= 30))
       AND (v_hour < 20 OR (v_hour = 20 AND v_minute < 30)) THEN
        v_shift := 'day';
    ELSE
        v_shift := 'night';
    END IF;

    -- ── PLC별 생산량 집계 ──
    -- production_hourly(continuous aggregate) + plc_data_reset_log 활용
    -- 생산량 = (last_val - first_val) + SUM(리셋 직전 값)
    FOR rec IN
        WITH d200_tags AS (
            SELECT plc_id, tag_id
            FROM {schema}.plc_data_master
            WHERE tag_name = 'D200'
        ),
        hourly AS (
            SELECT
                ph.plc_id,
                ph.first_val,
                ph.last_val
            FROM {schema}.production_hourly ph
            JOIN d200_tags t ON ph.plc_id = t.plc_id AND ph.tag_id = t.tag_id
            WHERE ph.bucket = p_bucket
        ),
        resets AS (
            SELECT
                r.plc_id,
                SUM(r.last_value) AS reset_sum,
                COUNT(*)          AS reset_cnt
            FROM {schema}.plc_data_reset_log r
            JOIN d200_tags t ON r.plc_id = t.plc_id AND r.trigger_tag_id = t.tag_id
            WHERE r.timestamp >= p_bucket
              AND r.timestamp <  v_bucket_end
            GROUP BY r.plc_id
        )
        SELECT
            h.plc_id,
            GREATEST(
                (h.last_val - h.first_val) + COALESCE(r.reset_sum, 0),
                0
            ) AS production_qty,
            COALESCE(r.reset_cnt, 0) AS reset_count
        FROM hourly h
        LEFT JOIN resets r ON h.plc_id = r.plc_id
    LOOP
        INSERT INTO {schema}.production_hourly_stats
            (bucket, plc_id, shift_type, production_qty, reset_count)
        VALUES
            (p_bucket, rec.plc_id, v_shift, rec.production_qty, rec.reset_count)
        ON CONFLICT (bucket, plc_id) DO UPDATE SET
            shift_type     = EXCLUDED.shift_type,
            production_qty = EXCLUDED.production_qty,
            reset_count    = EXCLUDED.reset_count,
            created_at     = now();
    END LOOP;

    -- ── PLC 4 행 보장 (D200 태그 없음 → FOR LOOP에서 생성 안 됨) ──
    INSERT INTO {schema}.production_hourly_stats
        (bucket, plc_id, shift_type, production_qty, reset_count)
    VALUES (p_bucket, 4, v_shift, 0, 0)
    ON CONFLICT (bucket, plc_id) DO NOTHING;

    -- ── 직행률 ──
    -- 0 값 제외 (리셋 시 0이 찍히므로)
    -- 중간직행률: PLC-D(4) D820(334) — PLC 4만 적용
    UPDATE {schema}.production_hourly_stats phs
    SET middle_yield = sub.avg_yield
    FROM (
        SELECT AVG(COALESCE(v_float, v_int::double precision, v_bigint::double precision)) AS avg_yield
        FROM {schema}.plc_data_integrated
        WHERE plc_id = 4 AND tag_id = 334
          AND timestamp >= p_bucket AND timestamp < v_bucket_end
          AND COALESCE(v_float, v_int::double precision, v_bigint::double precision) > 0
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = 4 AND sub.avg_yield IS NOT NULL;

    -- 최종직행률: PLC-J(8) D1244(175) — PLC 8만 적용
    UPDATE {schema}.production_hourly_stats phs
    SET final_yield = sub.avg_yield
    FROM (
        SELECT AVG(COALESCE(v_float, v_int::double precision, v_bigint::double precision)) AS avg_yield
        FROM {schema}.plc_data_integrated
        WHERE plc_id = 8 AND tag_id = 175
          AND timestamp >= p_bucket AND timestamp < v_bucket_end
          AND COALESCE(v_float, v_int::double precision, v_bigint::double precision) > 0
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = 8 AND sub.avg_yield IS NOT NULL;

    -- ── 알람 정지 시간 (PLC별) ──
    -- COALESCE(alarm_off, now()): 진행 중 알람도 포함
    -- LEAST/GREATEST: 시간 경계 넘는 알람 클리핑
    UPDATE {schema}.production_hourly_stats phs
    SET alarm_stop_sec = COALESCE(sub.total_dur, 0),
        alarm_count    = COALESCE(sub.cnt, 0)
    FROM (
        SELECT
            plc_id,
            SUM(
                EXTRACT(EPOCH FROM (
                    LEAST(COALESCE(alarm_off, now()), v_bucket_end)
                    - GREATEST(alarm_on, p_bucket)
                ))
            ) AS total_dur,
            COUNT(*) AS cnt
        FROM {schema}.alm_duration_log
        WHERE alarm_on < v_bucket_end
          AND COALESCE(alarm_off, now()) > p_bucket
        GROUP BY plc_id
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = sub.plc_id;

END;
$$;


-- ============================================================
-- 3. 일괄 실행 함수 (교대 기준 1일 = 08:00 ~ 다음날 08:00)
-- ============================================================
-- 호출: SELECT {schema}.fn_aggregate_day('2026-03-05');

CREATE OR REPLACE FUNCTION {schema}.fn_aggregate_day(
    p_date DATE
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_start TIMESTAMPTZ;
    v_hour  TIMESTAMPTZ;
BEGIN
    -- 08:00 KST부터 24시간 (08:00~08:30은 야간에 포함됨)
    v_start := (p_date::timestamp + TIME '08:00') AT TIME ZONE 'Asia/Seoul';

    FOR i IN 0..23 LOOP
        v_hour := v_start + (i * INTERVAL '1 hour');
        PERFORM {schema}.fn_aggregate_hourly(v_hour);
    END LOOP;
END;
$$;


-- ============================================================
-- 4. 일별 뷰 (교대별)
-- ============================================================

CREATE OR REPLACE VIEW {schema}.v_production_daily AS
SELECT
    (bucket AT TIME ZONE 'Asia/Seoul')::date AS work_date,
    plc_id,
    shift_type,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM {schema}.production_hourly_stats
GROUP BY work_date, plc_id, shift_type
ORDER BY work_date, plc_id, shift_type;


-- 일별 합산 (교대 무관 + 주/야 분리 컬럼)
CREATE OR REPLACE VIEW {schema}.v_production_daily_total AS
SELECT
    (bucket AT TIME ZONE 'Asia/Seoul')::date AS work_date,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count,
    SUM(CASE WHEN shift_type = 'day'   THEN production_qty ELSE 0 END) AS day_production,
    SUM(CASE WHEN shift_type = 'night' THEN production_qty ELSE 0 END) AS night_production
FROM {schema}.production_hourly_stats
GROUP BY work_date, plc_id
ORDER BY work_date, plc_id;


-- ============================================================
-- 5. 주간 / 월간 / 연간 뷰
-- ============================================================

CREATE OR REPLACE VIEW {schema}.v_production_weekly AS
SELECT
    date_trunc('week', (bucket AT TIME ZONE 'Asia/Seoul')::date)::date AS week_start,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM {schema}.production_hourly_stats
GROUP BY week_start, plc_id
ORDER BY week_start, plc_id;


CREATE OR REPLACE VIEW {schema}.v_production_monthly AS
SELECT
    date_trunc('month', (bucket AT TIME ZONE 'Asia/Seoul')::date)::date AS month_start,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM {schema}.production_hourly_stats
GROUP BY month_start, plc_id
ORDER BY month_start, plc_id;


CREATE OR REPLACE VIEW {schema}.v_production_yearly AS
SELECT
    EXTRACT(YEAR FROM (bucket AT TIME ZONE 'Asia/Seoul'))::int AS year,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM {schema}.production_hourly_stats
GROUP BY year, plc_id
ORDER BY year, plc_id;


-- ============================================================
-- 6. pg_cron 스케줄 (수동 설정)
-- ============================================================
-- 매시 5분에 직전 시간 집계 (데이터 수집 완료 보장)
--
-- SELECT cron.schedule(
--     'hourly_production_stats',
--     '5 * * * *',
--     $$SELECT jem_jh02.fn_aggregate_hourly(
--         date_trunc('hour', now() - INTERVAL '1 hour')
--     )$$
-- );
--
-- pg_cron 없으면 OS cron:
-- 5 * * * * psql -U user -d neurosense -c "SELECT jem_jh02.fn_aggregate_hourly(date_trunc('hour', now() - INTERVAL '1 hour'));"


-- ============================================================
-- 7. 과거 데이터 일괄 집계 (최초 1회)
-- ============================================================
-- production_hourly(continuous aggregate)가 refresh 완료된 후 실행할 것
--
-- DO $$
-- DECLARE
--     d DATE;
-- BEGIN
--     FOR d IN SELECT generate_series('2026-02-24'::date, '2026-03-09'::date, '1 day') LOOP
--         PERFORM jem_jh02.fn_aggregate_day(d);
--         RAISE NOTICE 'Done: %', d;
--     END LOOP;
-- END;
-- $$;
