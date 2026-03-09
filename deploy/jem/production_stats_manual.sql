-- ============================================================================
-- 시간별 생산 통계 — 수동 실행용 (jem_jh02 스키마)
-- ============================================================================
-- psql -U postgres -d neurosense -f production_stats_manual.sql
-- ============================================================================


-- ============================================================
-- 1. 테이블 + 인덱스
-- ============================================================

CREATE TABLE IF NOT EXISTS jem_jh02.production_hourly_stats (
    bucket          TIMESTAMPTZ      NOT NULL,
    plc_id          SMALLINT         NOT NULL,
    shift_type      VARCHAR(5)       NOT NULL,

    production_qty  BIGINT           NOT NULL DEFAULT 0,
    reset_count     INT              NOT NULL DEFAULT 0,

    middle_yield    DOUBLE PRECISION,
    final_yield     DOUBLE PRECISION,

    alarm_stop_sec  DOUBLE PRECISION NOT NULL DEFAULT 0,
    alarm_count     INT              NOT NULL DEFAULT 0,

    created_at      TIMESTAMPTZ      NOT NULL DEFAULT now(),

    PRIMARY KEY (bucket, plc_id)
);

CREATE INDEX IF NOT EXISTS idx_phs_plc_bucket
    ON jem_jh02.production_hourly_stats (plc_id, bucket DESC);
CREATE INDEX IF NOT EXISTS idx_phs_shift
    ON jem_jh02.production_hourly_stats (shift_type, bucket DESC);


-- ============================================================
-- 2. 시간별 집계 함수
-- ============================================================

CREATE OR REPLACE FUNCTION jem_jh02.fn_aggregate_hourly(
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
    v_hour   := EXTRACT(HOUR FROM p_bucket AT TIME ZONE 'Asia/Seoul');
    v_minute := EXTRACT(MINUTE FROM p_bucket AT TIME ZONE 'Asia/Seoul');

    IF (v_hour > 8 OR (v_hour = 8 AND v_minute >= 30))
       AND (v_hour < 20 OR (v_hour = 20 AND v_minute < 30)) THEN
        v_shift := 'day';
    ELSE
        v_shift := 'night';
    END IF;

    FOR rec IN
        WITH d200_tags AS (
            SELECT plc_id, tag_id
            FROM jem_jh02.plc_data_master
            WHERE tag_name = 'D200'
        ),
        hourly AS (
            SELECT ph.plc_id, ph.first_val, ph.last_val
            FROM jem_jh02.production_hourly ph
            JOIN d200_tags t ON ph.plc_id = t.plc_id AND ph.tag_id = t.tag_id
            WHERE ph.bucket = p_bucket
        ),
        resets AS (
            SELECT
                r.plc_id,
                SUM(r.last_value) AS reset_sum,
                COUNT(*)          AS reset_cnt
            FROM jem_jh02.plc_data_reset_log r
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
        INSERT INTO jem_jh02.production_hourly_stats
            (bucket, plc_id, shift_type, production_qty, reset_count)
        VALUES
            (p_bucket, rec.plc_id, v_shift, rec.production_qty, rec.reset_count)
        ON CONFLICT (bucket, plc_id) DO UPDATE SET
            shift_type     = EXCLUDED.shift_type,
            production_qty = EXCLUDED.production_qty,
            reset_count    = EXCLUDED.reset_count,
            created_at     = now();
    END LOOP;

    -- PLC 4 행 보장 (D200 태그 없음 → FOR LOOP에서 생성 안 됨)
    INSERT INTO jem_jh02.production_hourly_stats
        (bucket, plc_id, shift_type, production_qty, reset_count)
    VALUES (p_bucket, 4, v_shift, 0, 0)
    ON CONFLICT (bucket, plc_id) DO NOTHING;

    -- middle_yield: PLC 4만
    UPDATE jem_jh02.production_hourly_stats phs
    SET middle_yield = sub.avg_yield
    FROM (
        SELECT AVG(COALESCE(v_float, v_int::double precision, v_bigint::double precision)) AS avg_yield
        FROM jem_jh02.plc_data_integrated
        WHERE plc_id = 4 AND tag_id = 334
          AND timestamp >= p_bucket AND timestamp < v_bucket_end
          AND COALESCE(v_float, v_int::double precision, v_bigint::double precision) > 0
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = 4 AND sub.avg_yield IS NOT NULL;

    -- final_yield: PLC 8만
    UPDATE jem_jh02.production_hourly_stats phs
    SET final_yield = sub.avg_yield
    FROM (
        SELECT AVG(COALESCE(v_float, v_int::double precision, v_bigint::double precision)) AS avg_yield
        FROM jem_jh02.plc_data_integrated
        WHERE plc_id = 8 AND tag_id = 175
          AND timestamp >= p_bucket AND timestamp < v_bucket_end
          AND COALESCE(v_float, v_int::double precision, v_bigint::double precision) > 0
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = 8 AND sub.avg_yield IS NOT NULL;

    UPDATE jem_jh02.production_hourly_stats phs
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
        FROM jem_jh02.alm_duration_log
        WHERE alarm_on < v_bucket_end
          AND COALESCE(alarm_off, now()) > p_bucket
        GROUP BY plc_id
    ) sub
    WHERE phs.bucket = p_bucket AND phs.plc_id = sub.plc_id;

END;
$$;


-- ============================================================
-- 3. 일괄 실행 함수
-- ============================================================

CREATE OR REPLACE FUNCTION jem_jh02.fn_aggregate_day(
    p_date DATE
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_start TIMESTAMPTZ;
    v_hour  TIMESTAMPTZ;
BEGIN
    v_start := (p_date::timestamp + TIME '08:00') AT TIME ZONE 'Asia/Seoul';

    FOR i IN 0..23 LOOP
        v_hour := v_start + (i * INTERVAL '1 hour');
        PERFORM jem_jh02.fn_aggregate_hourly(v_hour);
    END LOOP;
END;
$$;


-- ============================================================
-- 4. 뷰 (일/주/월/연)
-- ============================================================

CREATE OR REPLACE VIEW jem_jh02.v_production_daily AS
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
FROM jem_jh02.production_hourly_stats
GROUP BY work_date, plc_id, shift_type
ORDER BY work_date, plc_id, shift_type;


CREATE OR REPLACE VIEW jem_jh02.v_production_daily_total AS
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
FROM jem_jh02.production_hourly_stats
GROUP BY work_date, plc_id
ORDER BY work_date, plc_id;


CREATE OR REPLACE VIEW jem_jh02.v_production_weekly AS
SELECT
    date_trunc('week', (bucket AT TIME ZONE 'Asia/Seoul')::date)::date AS week_start,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM jem_jh02.production_hourly_stats
GROUP BY week_start, plc_id
ORDER BY week_start, plc_id;


CREATE OR REPLACE VIEW jem_jh02.v_production_monthly AS
SELECT
    date_trunc('month', (bucket AT TIME ZONE 'Asia/Seoul')::date)::date AS month_start,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM jem_jh02.production_hourly_stats
GROUP BY month_start, plc_id
ORDER BY month_start, plc_id;


CREATE OR REPLACE VIEW jem_jh02.v_production_yearly AS
SELECT
    EXTRACT(YEAR FROM (bucket AT TIME ZONE 'Asia/Seoul'))::int AS year,
    plc_id,
    SUM(production_qty)     AS production_qty,
    SUM(reset_count)        AS reset_count,
    AVG(middle_yield) FILTER (WHERE middle_yield IS NOT NULL) AS avg_middle_yield,
    AVG(final_yield)  FILTER (WHERE final_yield IS NOT NULL)  AS avg_final_yield,
    SUM(alarm_stop_sec)     AS alarm_stop_sec,
    SUM(alarm_count)        AS alarm_count
FROM jem_jh02.production_hourly_stats
GROUP BY year, plc_id
ORDER BY year, plc_id;


-- ============================================================
-- 5. 잘못 들어간 데이터 정리 (plc_id 필터 없이 전체 적용된 직행률)
-- ============================================================

UPDATE jem_jh02.production_hourly_stats SET middle_yield = NULL WHERE plc_id != 4;
UPDATE jem_jh02.production_hourly_stats SET final_yield = NULL WHERE plc_id != 8;


-- ============================================================
-- 6. 과거 데이터 백필 재실행 (2026-02-24 ~ 오늘)
-- ============================================================
-- production_hourly(continuous aggregate)가 refresh된 상태에서 실행

DO $$ DECLARE d DATE;
BEGIN
    FOR d IN SELECT generate_series('2026-02-24'::date, CURRENT_DATE, '1 day') LOOP
        PERFORM jem_jh02.fn_aggregate_day(d);
    END LOOP;
END; $$;


-- ============================================================
-- 7. 확인 쿼리
-- ============================================================

-- 집계 결과 확인
SELECT * FROM jem_jh02.production_hourly_stats ORDER BY bucket DESC, plc_id LIMIT 30;

-- 일별 요약
SELECT * FROM jem_jh02.v_production_daily ORDER BY work_date DESC, plc_id LIMIT 30;

-- 일별 합산 (주/야 분리)
SELECT * FROM jem_jh02.v_production_daily_total ORDER BY work_date DESC, plc_id LIMIT 20;
