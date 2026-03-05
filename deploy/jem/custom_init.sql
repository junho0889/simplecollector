-- ============================================================================
-- JEM 커스텀 SQL
-- ============================================================================
-- 스키마 생성 후 자동 실행됩니다.
-- 플레이스홀더:
--   {schema}  → 설정된 스키마명 (예: jem_jh02)
--   {group}   → 수집 그룹명 (plc_data, alm)
--              {group} 포함 시 모든 그룹에 대해 반복 실행
--
-- NOTE: alm_history는 extensions.history로 이전됨 (publisher YAML 설정)
-- ============================================================================


-- ===== 1. 그룹별 뷰: {group}_latest + {group}_master 조인 =====
-- 용도: 최신값 조회 시 태그 메타정보(이름, 단위, 설명)를 함께 표시
CREATE OR REPLACE VIEW {schema}.{group}_latest_view AS
SELECT
    l.plc_id,
    l.tag_id,
    m.tag_name,
    m.data_type,
    m.unit,
    m.description,
    l.timestamp,
    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text,
    l.quality_code,
    l.updated_at
FROM {schema}.{group}_latest l
LEFT JOIN {schema}.{group}_master m
    ON l.plc_id = m.plc_id AND l.tag_id = m.tag_id;


-- ===== 2. {group}_master updated_at 자동 갱신 트리거 =====
-- 용도: master 테이블 수정 시 updated_at 자동 업데이트
CREATE OR REPLACE FUNCTION {schema}.{group}_master_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_{group}_master_updated_at
    BEFORE UPDATE ON {schema}.{group}_master
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.{group}_master_updated_at();


-- ============================================================================
-- 3. API 읽기 전용 계정
-- ============================================================================
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'api_reader') THEN
        CREATE ROLE api_reader WITH LOGIN PASSWORD 'neuro0901';
    END IF;
END
$$;

GRANT CONNECT ON DATABASE neurosense TO api_reader;
GRANT USAGE ON SCHEMA {schema} TO api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO api_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO api_reader;


-- ============================================================================
-- 4. 생산 리셋 기록
-- ============================================================================
-- 생산수량 또는 총생산수가 0이 되면 (리셋 감지):
--   1. 리셋 직전 생산량을 plc_data_reset_log에 기록
--   2. PLC-D/J/L은 plc_data_reset_snapshot에 주요 지표 캡처
--
-- 대상 태그: plc_data_master의 description이 '생산수량' 또는 '총 생산수'인 태그
-- 트리거: plc_data_latest BEFORE UPDATE (값이 non-zero → 0 전환 시)

-- 4-1. plc_data_reset_log 테이블
CREATE TABLE IF NOT EXISTS {schema}.plc_data_reset_log (
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plc_id SMALLINT NOT NULL,
    trigger_tag_id INTEGER NOT NULL,
    trigger_type VARCHAR(20),
    last_value BIGINT,
    production_qty BIGINT,
    ok_qty BIGINT,
    ng_qty BIGINT,
    total_production BIGINT
);

SELECT create_hypertable(
    '{schema}.plc_data_reset_log', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 4-2. plc_data_reset_snapshot 테이블
-- PLC-D(4), PLC-J(8), PLC-L(10) 전용: 리셋 시점의 주요 생산 지표 캡처
-- 직행율, 생산수량, 총생산수, NG수량_PCS, 사이클시간
CREATE TABLE IF NOT EXISTS {schema}.plc_data_reset_snapshot (
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plc_id SMALLINT NOT NULL,
    trigger_tag_id INTEGER NOT NULL,
    trigger_type VARCHAR(20),
    first_pass_yield DOUBLE PRECISION,
    production_qty BIGINT,
    total_production BIGINT,
    ng_qty BIGINT,
    cycle_time BIGINT
);

SELECT create_hypertable(
    '{schema}.plc_data_reset_snapshot', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 4-3. 커스텀 테이블 압축/보관 정책 (1일 압축, 3년 보관)
ALTER TABLE {schema}.plc_data_reset_log SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.plc_data_reset_log', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.plc_data_reset_log', INTERVAL '3 years', if_not_exists => TRUE);

ALTER TABLE {schema}.plc_data_reset_snapshot SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.plc_data_reset_snapshot', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.plc_data_reset_snapshot', INTERVAL '3 years', if_not_exists => TRUE);

-- 4-4. 트리거 함수
-- plc_data_latest UPDATE 시 생산수량/총생산수가 0이 되면:
--   - plc_data_reset_log에 리셋 직전 값 + 생산 태그 스냅샷
--   - plc_data_reset_snapshot에 PLC-D/J/L 주요 지표 캡처
CREATE OR REPLACE FUNCTION {schema}.fn_production_reset_check()
RETURNS TRIGGER AS $fn$
DECLARE
    v_old_val BIGINT;
    v_new_val BIGINT;
    v_monitor_type VARCHAR(20);
    v_now TIMESTAMPTZ;
BEGIN
    -- 값 추출 (uint32 → v_int 또는 v_bigint)
    v_new_val := COALESCE(NEW.v_int::bigint, NEW.v_bigint, -1);
    v_old_val := COALESCE(OLD.v_int::bigint, OLD.v_bigint, -1);

    -- Fast path: 새 값이 0이 아니면 스킵 (대부분의 경우)
    IF v_new_val != 0 THEN
        RETURN NEW;
    END IF;

    -- 이전 값이 이미 0 이하이면 스킵 (리셋이 아님)
    IF v_old_val <= 0 THEN
        RETURN NEW;
    END IF;

    -- 이 태그가 생산 모니터링 대상인지 확인
    SELECT
        CASE
            WHEN m.description LIKE '%생산수량%' THEN '생산수량'
            WHEN m.description LIKE '%총%생산수%' THEN '총생산수'
            ELSE NULL
        END INTO v_monitor_type
    FROM {schema}.plc_data_master m
    WHERE m.plc_id = NEW.plc_id AND m.tag_id = NEW.tag_id;

    IF v_monitor_type IS NULL THEN
        RETURN NEW;
    END IF;

    v_now := NOW();

    -- 1. plc_data_reset_log: 리셋 직전 값 + 생산 태그 스냅샷
    INSERT INTO {schema}.plc_data_reset_log (
        timestamp, plc_id, trigger_tag_id, trigger_type, last_value,
        production_qty, ok_qty, ng_qty, total_production
    )
    SELECT v_now, NEW.plc_id, NEW.tag_id, v_monitor_type, v_old_val,
        MAX(CASE WHEN m.description LIKE '%생산수량%'
            THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
        MAX(CASE WHEN m.description LIKE '%OK%수량%'
            THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
        MAX(CASE WHEN m.description LIKE '%NG%수량%'
            THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
        MAX(CASE WHEN m.description LIKE '%총%생산수%'
            THEN COALESCE(l.v_int::bigint, l.v_bigint) END)
    FROM {schema}.plc_data_master m
    LEFT JOIN {schema}.plc_data_latest l
        ON l.plc_id = m.plc_id AND l.tag_id = m.tag_id
    WHERE m.plc_id = NEW.plc_id;

    -- 2. plc_data_reset_snapshot: PLC-D(4)/J(8)/L(10) 주요 생산 지표 캡처
    IF NEW.plc_id IN (4, 8, 10) THEN
        INSERT INTO {schema}.plc_data_reset_snapshot (
            timestamp, plc_id, trigger_tag_id, trigger_type,
            first_pass_yield, production_qty, total_production, ng_qty, cycle_time
        )
        SELECT v_now, NEW.plc_id, NEW.tag_id, v_monitor_type,
            MAX(CASE WHEN m.description LIKE '%직행%'
                THEN l.v_float END),
            MAX(CASE WHEN m.description LIKE '%생산수량%'
                THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
            MAX(CASE WHEN m.description LIKE '%총%생산수%'
                THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
            MAX(CASE WHEN m.description LIKE '%NG%수량%'
                THEN COALESCE(l.v_int::bigint, l.v_bigint) END),
            MAX(CASE WHEN m.description LIKE '%사이클 시간%'
                THEN COALESCE(l.v_int::bigint, l.v_bigint) END)
        FROM {schema}.plc_data_master m
        LEFT JOIN {schema}.plc_data_latest l
            ON l.plc_id = m.plc_id AND l.tag_id = m.tag_id
        WHERE m.plc_id = NEW.plc_id;
    END IF;

    RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

-- 4-5. 트리거 (plc_data_latest UPDATE 시)
CREATE TRIGGER trg_plc_data_production_reset
    BEFORE UPDATE ON {schema}.plc_data_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_production_reset_check();


-- ============================================================================
-- 5. 알람 교대별 통계 (alm_shift_summary)
-- ============================================================================
-- 매일 오전 8:30에 주간/야간 알람 통계를 집계
-- 주간: 08:30 ~ 20:30 (12시간)
-- 야간: 20:30 ~ 08:30 (12시간)
--
-- 데이터 소스: alm_history (extensions.history 자동 생성)
-- 집계: pg_cron 또는 외부 스케줄러에서 fn_daily_alm_check() 호출
--   SELECT {schema}.fn_daily_alm_check();

-- 5-1. alm_shift_summary 테이블
-- 교대별 태그당 알람 발생 빈도, 누적 시간, 추이
CREATE TABLE IF NOT EXISTS {schema}.alm_shift_summary (
    timestamp           TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    shift_date          DATE              NOT NULL,
    shift_type          VARCHAR(5)        NOT NULL,     -- 'day' / 'night'
    plc_id              SMALLINT          NOT NULL,
    tag_id              INTEGER           NOT NULL,
    tag_name            VARCHAR(50),
    alarm_count         INTEGER           DEFAULT 0,    -- 발생 횟수 (FALSE→TRUE 전환)
    total_duration_sec  DOUBLE PRECISION  DEFAULT 0,    -- 누적 활성 시간 (초)
    max_duration_sec    DOUBLE PRECISION  DEFAULT 0,    -- 최장 연속 활성 시간 (초)
    first_alarm_at      TIMESTAMPTZ,                    -- 근무 중 첫 발생
    last_alarm_at       TIMESTAMPTZ                     -- 근무 중 마지막 발생
);

SELECT create_hypertable(
    '{schema}.alm_shift_summary', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 5-2. 압축/보관 정책
ALTER TABLE {schema}.alm_shift_summary SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.alm_shift_summary', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.alm_shift_summary', INTERVAL '3 years', if_not_exists => TRUE);

-- 5-3. 교대별 집계 함수
-- alm_history에서 해당 교대 시간의 알람 통계를 계산하여 alm_shift_summary에 INSERT
CREATE OR REPLACE FUNCTION {schema}.fn_compute_alm_shift_summary(
    p_shift_start TIMESTAMPTZ,
    p_shift_end   TIMESTAMPTZ,
    p_shift_type  VARCHAR,
    p_shift_date  DATE
) RETURNS void AS $fn$
BEGIN
    INSERT INTO {schema}.alm_shift_summary (
        timestamp, shift_date, shift_type, plc_id, tag_id, tag_name,
        alarm_count, total_duration_sec, max_duration_sec,
        first_alarm_at, last_alarm_at
    )
    WITH transitions AS (
        SELECT
            plc_id, tag_id, timestamp AS ts, v_bool,
            LEAD(timestamp) OVER (
                PARTITION BY plc_id, tag_id ORDER BY timestamp
            ) AS next_ts
        FROM {schema}.alm_history
        WHERE timestamp >= p_shift_start
          AND timestamp < p_shift_end
          AND v_bool IS NOT NULL
    )
    SELECT
        NOW(), p_shift_date, p_shift_type,
        t.plc_id, t.tag_id, m.tag_name,
        COUNT(*) FILTER (WHERE t.v_bool = TRUE),
        COALESCE(SUM(
            CASE WHEN t.v_bool = TRUE THEN
                EXTRACT(EPOCH FROM
                    LEAST(COALESCE(t.next_ts, p_shift_end), p_shift_end) - t.ts
                )
            END
        ), 0),
        COALESCE(MAX(
            CASE WHEN t.v_bool = TRUE THEN
                EXTRACT(EPOCH FROM
                    LEAST(COALESCE(t.next_ts, p_shift_end), p_shift_end) - t.ts
                )
            END
        ), 0),
        MIN(CASE WHEN t.v_bool = TRUE THEN t.ts END),
        MAX(CASE WHEN t.v_bool = TRUE THEN t.ts END)
    FROM transitions t
    LEFT JOIN {schema}.alm_master m
        ON m.plc_id = t.plc_id AND m.tag_id = t.tag_id
    GROUP BY t.plc_id, t.tag_id, m.tag_name
    HAVING COUNT(*) FILTER (WHERE t.v_bool = TRUE) > 0;
END;
$fn$ LANGUAGE plpgsql;

-- 5-4. 일일 집계 함수 (매일 08:30에 호출)
-- pg_cron: SELECT cron.schedule('daily-alm-check', '30 8 * * *', $$SELECT {schema}.fn_daily_alm_check()$$);
CREATE OR REPLACE FUNCTION {schema}.fn_daily_alm_check()
RETURNS void AS $fn$
DECLARE
    v_today     DATE := CURRENT_DATE;
    v_yesterday DATE := CURRENT_DATE - 1;
BEGIN
    -- 주간 (전일): 어제 08:30 ~ 어제 20:30
    PERFORM {schema}.fn_compute_alm_shift_summary(
        (v_yesterday + TIME '08:30')::timestamptz,
        (v_yesterday + TIME '20:30')::timestamptz,
        'day',
        v_yesterday
    );

    -- 야간 (전일~금일): 어제 20:30 ~ 오늘 08:30
    PERFORM {schema}.fn_compute_alm_shift_summary(
        (v_yesterday + TIME '20:30')::timestamptz,
        (v_today + TIME '08:30')::timestamptz,
        'night',
        v_yesterday
    );
END;
$fn$ LANGUAGE plpgsql;


-- ============================================================================
-- 6. 통계/분석 테이블 (Analytics)
-- ============================================================================
-- 시간별 생산 통계, 알람 지속시간, 가동율 집계
-- Continuous Aggregate: 자동 갱신 (production_hourly)
-- Stored Procedure + pg_cron: alm_duration, operating_rate
-- ============================================================================

-- 6-1. production_hourly (Continuous Aggregate)
-- plc_data_integrated에서 시간별 first/last 값 자동 집계
-- 생산량 = (last - first) + reset_log 보정 (API 조회 시 JOIN)
DO $do$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
        WHERE view_schema = '{schema}' AND view_name = 'production_hourly'
    ) THEN
        EXECUTE $ca$
            CREATE MATERIALIZED VIEW {schema}.production_hourly
            WITH (timescaledb.continuous) AS
            SELECT
                time_bucket('1 hour', timestamp) AS bucket,
                plc_id, tag_id,
                first(COALESCE(v_int::bigint, v_bigint, 0), timestamp) AS first_val,
                last(COALESCE(v_int::bigint, v_bigint, 0), timestamp) AS last_val,
                max(COALESCE(v_int::bigint, v_bigint, 0)) AS max_val,
                count(*) AS sample_count
            FROM {schema}.plc_data_integrated
            GROUP BY bucket, plc_id, tag_id
            WITH NO DATA
        $ca$;
    END IF;
END $do$;

SELECT add_continuous_aggregate_policy('{schema}.production_hourly',
    start_offset => INTERVAL '2 hours',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE);

ALTER MATERIALIZED VIEW {schema}.production_hourly SET (
    timescaledb.compress = true);
SELECT add_compression_policy('{schema}.production_hourly',
    compress_after => INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.production_hourly',
    drop_after => INTERVAL '3 years', if_not_exists => TRUE);


-- 6-2. alm_duration_log (알람 지속시간 기록)
-- alm_history의 ON→OFF 페어링으로 개별 알람 지속시간 저장
CREATE TABLE IF NOT EXISTS {schema}.alm_duration_log (
    alarm_on     TIMESTAMPTZ      NOT NULL,
    alarm_off    TIMESTAMPTZ,
    plc_id       SMALLINT         NOT NULL,
    tag_id       INTEGER          NOT NULL,
    duration_sec DOUBLE PRECISION
);

SELECT create_hypertable(
    '{schema}.alm_duration_log', 'alarm_on',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

ALTER TABLE {schema}.alm_duration_log SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.alm_duration_log', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.alm_duration_log', INTERVAL '3 years', if_not_exists => TRUE);


-- 6-3. operating_rate_hourly (시간별 가동율)
-- 생산수량 태그의 1분 단위 값 변화 유무로 가동/비가동 판정
CREATE TABLE IF NOT EXISTS {schema}.operating_rate_hourly (
    bucket          TIMESTAMPTZ      NOT NULL,
    plc_id          SMALLINT         NOT NULL,
    operating_minutes  DOUBLE PRECISION,
    idle_minutes       DOUBLE PRECISION,
    operating_rate     DOUBLE PRECISION,
    production_qty     BIGINT,
    PRIMARY KEY (bucket, plc_id)
);

SELECT create_hypertable(
    '{schema}.operating_rate_hourly', 'bucket',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

ALTER TABLE {schema}.operating_rate_hourly SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.operating_rate_hourly', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.operating_rate_hourly', INTERVAL '3 years', if_not_exists => TRUE);


-- 6-4. 알람 지속시간 계산 프로시저
-- alm_history에서 ON→OFF 페어링하여 alm_duration_log에 INSERT
CREATE OR REPLACE PROCEDURE {schema}.sp_calc_alm_duration(
    p_from TIMESTAMPTZ,
    p_to   TIMESTAMPTZ
)
LANGUAGE plpgsql AS $proc$
BEGIN
    DELETE FROM {schema}.alm_duration_log
    WHERE alarm_on >= p_from AND alarm_on < p_to;

    INSERT INTO {schema}.alm_duration_log (alarm_on, alarm_off, plc_id, tag_id, duration_sec)
    WITH paired AS (
        SELECT
            plc_id, tag_id, timestamp AS ts, v_bool,
            LEAD(timestamp) OVER (
                PARTITION BY plc_id, tag_id ORDER BY timestamp
            ) AS next_ts
        FROM {schema}.alm_history
        WHERE timestamp >= p_from
          AND timestamp < p_to + INTERVAL '1 hour'
    )
    SELECT
        ts, next_ts, plc_id, tag_id,
        EXTRACT(EPOCH FROM next_ts - ts)
    FROM paired
    WHERE v_bool = TRUE
      AND ts >= p_from AND ts < p_to
      AND next_ts IS NOT NULL;
END;
$proc$;


-- 6-5. 가동율 계산 프로시저
-- 1분 단위 생산수량 변화 감지 → 시간별 가동율 산출
CREATE OR REPLACE PROCEDURE {schema}.sp_calc_operating_rate(
    p_from TIMESTAMPTZ,
    p_to   TIMESTAMPTZ
)
LANGUAGE plpgsql AS $proc$
BEGIN
    INSERT INTO {schema}.operating_rate_hourly
        (bucket, plc_id, operating_minutes, idle_minutes, operating_rate, production_qty)
    WITH production_tags AS (
        SELECT plc_id, tag_id
        FROM {schema}.plc_data_master
        WHERE description LIKE '%생산수량%'
    ),
    minute_check AS (
        SELECT
            time_bucket('1 minute', i.timestamp) AS minute_bucket,
            i.plc_id,
            CASE WHEN max(COALESCE(i.v_int::bigint, i.v_bigint, 0))
                      > min(COALESCE(i.v_int::bigint, i.v_bigint, 0))
                 THEN 1 ELSE 0 END AS is_active
        FROM {schema}.plc_data_integrated i
        JOIN production_tags pt USING (plc_id, tag_id)
        WHERE i.timestamp >= p_from AND i.timestamp < p_to
        GROUP BY minute_bucket, i.plc_id
    ),
    hourly_agg AS (
        SELECT
            time_bucket('1 hour', minute_bucket) AS bucket,
            plc_id,
            sum(is_active)::double precision AS op_min,
            (60 - sum(is_active))::double precision AS idle_min
        FROM minute_check
        GROUP BY bucket, plc_id
    ),
    hourly_production AS (
        SELECT
            time_bucket('1 hour', i.timestamp) AS bucket,
            i.plc_id,
            (last(COALESCE(i.v_int::bigint, i.v_bigint, 0), i.timestamp)
             - first(COALESCE(i.v_int::bigint, i.v_bigint, 0), i.timestamp))
            + COALESCE(r.reset_sum, 0) AS qty
        FROM {schema}.plc_data_integrated i
        JOIN production_tags pt USING (plc_id, tag_id)
        LEFT JOIN (
            SELECT time_bucket('1 hour', timestamp) AS bucket, plc_id,
                   sum(last_value) AS reset_sum
            FROM {schema}.plc_data_reset_log
            WHERE timestamp >= p_from AND timestamp < p_to
            GROUP BY 1, 2
        ) r ON r.bucket = time_bucket('1 hour', i.timestamp)
           AND r.plc_id = i.plc_id
        WHERE i.timestamp >= p_from AND i.timestamp < p_to
        GROUP BY time_bucket('1 hour', i.timestamp), i.plc_id, r.reset_sum
    )
    SELECT
        h.bucket, h.plc_id, h.op_min, h.idle_min,
        round(h.op_min / 60.0 * 100, 2),
        p.qty
    FROM hourly_agg h
    LEFT JOIN hourly_production p USING (bucket, plc_id)
    ON CONFLICT (bucket, plc_id) DO UPDATE SET
        operating_minutes = EXCLUDED.operating_minutes,
        idle_minutes      = EXCLUDED.idle_minutes,
        operating_rate    = EXCLUDED.operating_rate,
        production_qty    = EXCLUDED.production_qty;
END;
$proc$;


-- 6-6. pg_cron 스케줄 (수동 설정 필요)
-- 아래 SQL을 DB에서 직접 실행하여 스케줄 등록:
--
-- SELECT cron.schedule('calc-alm-duration', '10 * * * *',
--     $$CALL {schema}.sp_calc_alm_duration(
--         date_trunc('hour', now() - interval '1 hour'),
--         date_trunc('hour', now())
--     )$$);
--
-- SELECT cron.schedule('calc-operating-rate', '15 * * * *',
--     $$CALL {schema}.sp_calc_operating_rate(
--         date_trunc('hour', now() - interval '1 hour'),
--         date_trunc('hour', now())
--     )$$);
