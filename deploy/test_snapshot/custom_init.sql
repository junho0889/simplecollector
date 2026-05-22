-- ============================================================================
-- test_snapshot 커스텀 SQL
-- ============================================================================
-- deploy/jem/custom_init.sql에서 테스트에 필요한 부분만 추출
-- 플레이스홀더: {schema} → test_snapshot, {group} → 각 그룹

-- ===== 1. 그룹별 뷰 =====
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


-- ============================================================================
-- 2. 생산 리셋 기록 + 알람 통계
-- ============================================================================

-- 2-1. production_reset_log 테이블
CREATE TABLE IF NOT EXISTS {schema}.production_reset_log (
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
    '{schema}.production_reset_log', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 2-2. alm_statistics 테이블
CREATE TABLE IF NOT EXISTS {schema}.alm_statistics (
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    plc_id SMALLINT NOT NULL,
    trigger_tag_id INTEGER,
    total_alarms INTEGER,
    active_alarms INTEGER,
    alarm_rate DOUBLE PRECISION
);

SELECT create_hypertable(
    '{schema}.alm_statistics', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 2-3. production_reset_snapshot 테이블
-- PLC-D(4) 전용: 리셋 시점의 주요 생산 지표 캡처
CREATE TABLE IF NOT EXISTS {schema}.production_reset_snapshot (
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
    '{schema}.production_reset_snapshot', 'timestamp',
    chunk_time_interval => INTERVAL '30 days',
    if_not_exists => TRUE
);

-- 2-4. 커스텀 테이블 압축/보관 정책 (1일 압축, 3년 보관)
ALTER TABLE {schema}.production_reset_log SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.production_reset_log', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.production_reset_log', INTERVAL '3 years', if_not_exists => TRUE);

ALTER TABLE {schema}.alm_statistics SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.alm_statistics', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.alm_statistics', INTERVAL '3 years', if_not_exists => TRUE);

ALTER TABLE {schema}.production_reset_snapshot SET (timescaledb.compress, timescaledb.compress_segmentby = 'plc_id');
SELECT add_compression_policy('{schema}.production_reset_snapshot', INTERVAL '1 day', if_not_exists => TRUE);
SELECT add_retention_policy('{schema}.production_reset_snapshot', INTERVAL '3 years', if_not_exists => TRUE);

-- 2-6. 트리거 함수
CREATE OR REPLACE FUNCTION {schema}.fn_production_reset_check()
RETURNS TRIGGER AS $fn$
DECLARE
    v_old_val BIGINT;
    v_new_val BIGINT;
    v_monitor_type VARCHAR(20);
    v_now TIMESTAMPTZ;
BEGIN
    v_new_val := COALESCE(NEW.v_int::bigint, NEW.v_bigint, -1);
    v_old_val := COALESCE(OLD.v_int::bigint, OLD.v_bigint, -1);

    IF v_new_val != 0 THEN
        RETURN NEW;
    END IF;

    IF v_old_val <= 0 THEN
        RETURN NEW;
    END IF;

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

    INSERT INTO {schema}.production_reset_log (
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

    INSERT INTO {schema}.alm_statistics (
        timestamp, plc_id, trigger_tag_id,
        total_alarms, active_alarms, alarm_rate
    )
    SELECT v_now, NEW.plc_id, NEW.tag_id,
        count(*),
        count(*) FILTER (WHERE v_bool = TRUE),
        count(*) FILTER (WHERE v_bool = TRUE)::double precision / NULLIF(count(*), 0)
    FROM {schema}.alm_latest
    WHERE plc_id = NEW.plc_id;

    -- 3. production_reset_snapshot: PLC-D(4) 주요 생산 지표 캡처
    IF NEW.plc_id IN (4, 8, 10) THEN
        INSERT INTO {schema}.production_reset_snapshot (
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

-- 2-7. 트리거
CREATE TRIGGER trg_plc_data_production_reset
    BEFORE UPDATE ON {schema}.plc_data_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_production_reset_check();
