-- ============================================================
-- jem_ble 기본 스키마 (custom_init.sql 제외) — schema=jem_jh02
-- groups=['plc_data', 'alm', 'log', 'action', 'plc_setting_data', 'plc_product_data', 'tl_data', 'ble_data']
-- device_types=['plc', 'ble']
-- 생성: publisher schema_init (auto_init_schema 동등)
-- ============================================================

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE SCHEMA IF NOT EXISTS jem_jh02;

CREATE TABLE IF NOT EXISTS jem_jh02.plc_master (
                plc_id        SMALLINT        PRIMARY KEY,
                plc_name      VARCHAR(100)    NOT NULL,
                protocol_type   VARCHAR(50)     NOT NULL,
                host            VARCHAR(255),
                port            INTEGER,
                site            VARCHAR(100),
                area            VARCHAR(100),
                line            VARCHAR(100),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW()
            );

ALTER TABLE jem_jh02.plc_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

ALTER TABLE jem_jh02.plc_master ADD COLUMN IF NOT EXISTS site VARCHAR(100);

ALTER TABLE jem_jh02.plc_master ADD COLUMN IF NOT EXISTS area VARCHAR(100);

ALTER TABLE jem_jh02.plc_master ADD COLUMN IF NOT EXISTS line VARCHAR(100);

CREATE TABLE IF NOT EXISTS jem_jh02.quality_master (
            quality_code    SMALLINT        PRIMARY KEY,
            quality_name    VARCHAR(50)     NOT NULL,
            description     TEXT,
            created_at      TIMESTAMPTZ     DEFAULT NOW()
        );

INSERT INTO jem_jh02.quality_master (quality_code, quality_name, description) VALUES
            (0, 'BAD', '통신 이상 또는 데이터 없음'),
            (1, 'GOOD', '정상 데이터'),
            (2, 'UNCERTAIN', '불확실한 데이터'),
            (3, 'TIMEOUT', '타임아웃'),
            (4, 'ERROR', '에러 발생'),
            (5, 'MANUAL', '수동 입력값'),
            (6, 'SIMULATED', '시뮬레이션 값')
        ON CONFLICT (quality_code) DO NOTHING;

CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

CREATE SCHEMA IF NOT EXISTS jem_jh02;

CREATE TABLE IF NOT EXISTS jem_jh02.ble_master (
                ble_id        SMALLINT        PRIMARY KEY,
                ble_name      VARCHAR(100)    NOT NULL,
                mac_address     VARCHAR(20),
                device_profile  VARCHAR(50),
                site            VARCHAR(100),
                area            VARCHAR(100),
                line            VARCHAR(100),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW()
            );

ALTER TABLE jem_jh02.ble_master ADD COLUMN IF NOT EXISTS site VARCHAR(100);

ALTER TABLE jem_jh02.ble_master ADD COLUMN IF NOT EXISTS area VARCHAR(100);

ALTER TABLE jem_jh02.ble_master ADD COLUMN IF NOT EXISTS line VARCHAR(100);

CREATE TABLE IF NOT EXISTS jem_jh02.quality_master (
            quality_code    SMALLINT        PRIMARY KEY,
            quality_name    VARCHAR(50)     NOT NULL,
            description     TEXT,
            created_at      TIMESTAMPTZ     DEFAULT NOW()
        );

INSERT INTO jem_jh02.quality_master (quality_code, quality_name, description) VALUES
            (0, 'BAD', '통신 이상 또는 데이터 없음'),
            (1, 'GOOD', '정상 데이터'),
            (2, 'UNCERTAIN', '불확실한 데이터'),
            (3, 'TIMEOUT', '타임아웃'),
            (4, 'ERROR', '에러 발생'),
            (5, 'MANUAL', '수동 입력값'),
            (6, 'SIMULATED', '시뮬레이션 값')
        ON CONFLICT (quality_code) DO NOTHING;

CREATE TABLE IF NOT EXISTS jem_jh02.plc_data_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_plc_data_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.plc_data_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_plc_data_master_address ON jem_jh02.plc_data_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.plc_data_latest (
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                timestamp       TIMESTAMPTZ       NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1,
                updated_at      TIMESTAMPTZ       DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id)
            );

CREATE INDEX IF NOT EXISTS idx_plc_data_latest_updated ON jem_jh02.plc_data_latest (updated_at DESC);

CREATE TABLE IF NOT EXISTS jem_jh02.plc_data_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.plc_data_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_plc_data_int_plc_tag_time ON jem_jh02.plc_data_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.plc_data_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.plc_data_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.plc_data_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.plc_data_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.plc_data_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.alm_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_alm_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.alm_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_alm_master_address ON jem_jh02.alm_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.alm_latest (
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                timestamp       TIMESTAMPTZ       NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1,
                updated_at      TIMESTAMPTZ       DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id)
            );

CREATE INDEX IF NOT EXISTS idx_alm_latest_updated ON jem_jh02.alm_latest (updated_at DESC);

CREATE TABLE IF NOT EXISTS jem_jh02.alm_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.alm_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_alm_int_plc_tag_time ON jem_jh02.alm_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.alm_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.alm_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.alm_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.alm_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.alm_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.alm_history (
            timestamp       TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
            plc_id        SMALLINT          NOT NULL,
            tag_id          INTEGER           NOT NULL,
            v_bool          BOOLEAN,
            v_int           INTEGER,
            v_bigint        BIGINT,
            v_float         DOUBLE PRECISION,
            v_text          TEXT
        );

SELECT create_hypertable(
            'jem_jh02.alm_history', 'timestamp',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );

CREATE INDEX IF NOT EXISTS idx_alm_history_plc_tag_time ON jem_jh02.alm_history (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.alm_history SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'plc_id, tag_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        );

SELECT remove_compression_policy('jem_jh02.alm_history', if_exists => TRUE);

SELECT add_compression_policy(
            'jem_jh02.alm_history',
            INTERVAL '1 day'
        );

SELECT remove_retention_policy('jem_jh02.alm_history', if_exists => TRUE);

SELECT add_retention_policy(
                'jem_jh02.alm_history',
                INTERVAL '3 years'
            );

CREATE OR REPLACE FUNCTION jem_jh02.fn_alm_history_on_update()
        RETURNS TRIGGER AS $fn$
        BEGIN
            IF (OLD.v_bool IS DISTINCT FROM NEW.v_bool) THEN
                INSERT INTO jem_jh02.alm_history (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                ) VALUES (
                    NOW(), NEW.plc_id, NEW.tag_id,
                    NEW.v_bool, NEW.v_int, NEW.v_bigint, NEW.v_float, NEW.v_text
                );
            END IF;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION jem_jh02.fn_alm_history_on_insert()
        RETURNS TRIGGER AS $fn$
        BEGIN
            INSERT INTO jem_jh02.alm_history (
                timestamp, plc_id, tag_id,
                v_bool, v_int, v_bigint, v_float, v_text
            ) VALUES (
                NOW(), NEW.plc_id, NEW.tag_id,
                NEW.v_bool, NEW.v_int, NEW.v_bigint, NEW.v_float, NEW.v_text
            );
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_alm_history_update ON jem_jh02.alm_latest;

CREATE TRIGGER trg_alm_history_update
            BEFORE UPDATE ON jem_jh02.alm_latest
            FOR EACH ROW
            EXECUTE FUNCTION jem_jh02.fn_alm_history_on_update();

DROP TRIGGER IF EXISTS trg_alm_history_insert ON jem_jh02.alm_latest;

CREATE TRIGGER trg_alm_history_insert
            AFTER INSERT ON jem_jh02.alm_latest
            FOR EACH ROW
            EXECUTE FUNCTION jem_jh02.fn_alm_history_on_insert();

CREATE TABLE IF NOT EXISTS jem_jh02.log_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_log_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.log_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_log_master_address ON jem_jh02.log_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.log_latest (
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                timestamp       TIMESTAMPTZ       NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1,
                updated_at      TIMESTAMPTZ       DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id)
            );

CREATE INDEX IF NOT EXISTS idx_log_latest_updated ON jem_jh02.log_latest (updated_at DESC);

CREATE TABLE IF NOT EXISTS jem_jh02.log_snapshot (
            timestamp       TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
            plc_id        SMALLINT          NOT NULL,
            tag_id          INTEGER           NOT NULL,
            v_bool          BOOLEAN,
            v_int           INTEGER,
            v_bigint        BIGINT,
            v_float         DOUBLE PRECISION,
            v_text          TEXT
        );

SELECT create_hypertable(
            'jem_jh02.log_snapshot', 'timestamp',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE,
            migrate_data => TRUE
        );

CREATE INDEX IF NOT EXISTS idx_log_snapshot_plc_tag_time ON jem_jh02.log_snapshot (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.log_snapshot SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'plc_id, tag_id',
            timescaledb.compress_orderby = 'timestamp DESC'
        );

SELECT remove_compression_policy('jem_jh02.log_snapshot', if_exists => TRUE);

SELECT add_compression_policy(
            'jem_jh02.log_snapshot',
            INTERVAL '1 day'
        );

SELECT remove_retention_policy('jem_jh02.log_snapshot', if_exists => TRUE);

SELECT add_retention_policy(
                'jem_jh02.log_snapshot',
                INTERVAL '3 years'
            );

CREATE OR REPLACE FUNCTION jem_jh02.fn_log_snapshot_on_update()
        RETURNS TRIGGER AS $fn$
        DECLARE
            v_now TIMESTAMPTZ;
        BEGIN
            v_now := NOW();
            
            IF (NEW.tag_id = 3000
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (1, 2, 3, 5, 6, 7, 9, 10)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3001, 3002, 3003, 3004, 3005);
            END IF;

            IF (NEW.tag_id = 3005
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (4)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3008, 3009, 3010, 3011, 3012, 3013, 3014, 3015, 3016, 3017, 3018, 3019, 3020, 3021, 3022, 3023, 3024, 3025, 3026, 3027, 3028, 3029, 3030, 3031, 3032, 3033, 3034, 3035, 3036, 3037, 3038, 3039, 3040, 3041, 3042, 3043, 3044, 3045, 3046, 3047, 3048, 3049, 3050, 3051, 3052, 3053, 3054, 3055);
            END IF;

            IF (NEW.tag_id = 3006
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (4)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3000, 3001, 3002, 3003, 3004);
            END IF;

            IF (NEW.tag_id = 3007
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (4)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3056, 3057, 3058, 3059, 3060, 3061, 3062, 3063, 3064, 3065, 3066, 3067, 3068, 3069, 3070, 3071, 3072, 3073, 3074, 3075);
            END IF;

            IF (NEW.tag_id = 3000
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (8)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3001, 3002, 3003, 3004, 3005, 3006, 3007, 3008, 3009, 3010, 3011, 3012, 3013, 3014, 3015, 3016, 3017, 3018, 3019, 3020, 3021, 3022, 3023, 3024, 3025, 3026, 3027, 3028, 3029, 3030, 3031, 3032, 3033, 3034, 3035, 3036);
            END IF;

            IF (NEW.tag_id = 3037
                AND NEW.v_bool = TRUE
                AND OLD.v_bool IS DISTINCT FROM TRUE
                AND NEW.plc_id IN (8)) THEN
                INSERT INTO jem_jh02.log_snapshot (
                    timestamp, plc_id, tag_id,
                    v_bool, v_int, v_bigint, v_float, v_text
                )
                SELECT
                    v_now, l.plc_id, l.tag_id,
                    l.v_bool, l.v_int, l.v_bigint, l.v_float, l.v_text
                FROM jem_jh02.log_latest l
                WHERE l.plc_id = NEW.plc_id
                  AND l.tag_id IN (3038, 3039, 3040, 3041, 3042);
            END IF;
            RETURN NEW;
        END;
        $fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_log_snapshot_update ON jem_jh02.log_latest;

CREATE TRIGGER trg_log_snapshot_update
            BEFORE UPDATE ON jem_jh02.log_latest
            FOR EACH ROW
            EXECUTE FUNCTION jem_jh02.fn_log_snapshot_on_update();

CREATE TABLE IF NOT EXISTS jem_jh02.action_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_action_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.action_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_action_master_address ON jem_jh02.action_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.action_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.action_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_action_int_plc_tag_time ON jem_jh02.action_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.action_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.action_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.action_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.action_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.action_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.plc_setting_data_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_plc_setting_data_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.plc_setting_data_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_plc_setting_data_master_address ON jem_jh02.plc_setting_data_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.plc_setting_data_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.plc_setting_data_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_plc_setting_data_int_plc_tag_time ON jem_jh02.plc_setting_data_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.plc_setting_data_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.plc_setting_data_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.plc_setting_data_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.plc_setting_data_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.plc_setting_data_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.plc_product_data_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_plc_product_data_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.plc_product_data_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_plc_product_data_master_address ON jem_jh02.plc_product_data_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.plc_product_data_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.plc_product_data_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_plc_product_data_int_plc_tag_time ON jem_jh02.plc_product_data_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.plc_product_data_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.plc_product_data_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.plc_product_data_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.plc_product_data_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.plc_product_data_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.tl_data_master (
                plc_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                memory          VARCHAR(10),
                address         INTEGER         NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                word_length     SMALLINT,
                format          VARCHAR(30),
                unit            VARCHAR(30),
                mac_address     VARCHAR(20),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id),
                CONSTRAINT fk_tl_data_master_plc FOREIGN KEY (plc_id)
                    REFERENCES jem_jh02.plc_master (plc_id) ON DELETE CASCADE
            );

ALTER TABLE jem_jh02.tl_data_master ADD COLUMN IF NOT EXISTS mac_address VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_tl_data_master_address ON jem_jh02.tl_data_master (plc_id, memory, address);

CREATE TABLE IF NOT EXISTS jem_jh02.tl_data_latest (
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                timestamp       TIMESTAMPTZ       NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1,
                updated_at      TIMESTAMPTZ       DEFAULT NOW(),
                PRIMARY KEY (plc_id, tag_id)
            );

CREATE INDEX IF NOT EXISTS idx_tl_data_latest_updated ON jem_jh02.tl_data_latest (updated_at DESC);

CREATE TABLE IF NOT EXISTS jem_jh02.tl_data_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                plc_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.tl_data_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_tl_data_int_plc_tag_time ON jem_jh02.tl_data_integrated (plc_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.tl_data_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'plc_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.tl_data_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.tl_data_integrated',
                INTERVAL '1 day'
            );

SELECT remove_retention_policy('jem_jh02.tl_data_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.tl_data_integrated',
                    INTERVAL '3 years'
                );

CREATE TABLE IF NOT EXISTS jem_jh02.ble_data_master (
                ble_id        SMALLINT        NOT NULL,
                tag_id          INTEGER         NOT NULL,
                tag_name        VARCHAR(100)    NOT NULL,
                data_type       VARCHAR(30)     NOT NULL,
                scale           DOUBLE PRECISION DEFAULT 1.0,
                offset_value    DOUBLE PRECISION DEFAULT 0.0,
                decimals        SMALLINT,
                unit            VARCHAR(30),
                description     TEXT,
                collect_yn      CHAR(1)         DEFAULT 'Y',
                created_at      TIMESTAMPTZ     DEFAULT NOW(),
                updated_at      TIMESTAMPTZ     DEFAULT NOW(),
                PRIMARY KEY (ble_id, tag_id),
                CONSTRAINT fk_ble_data_master_ble FOREIGN KEY (ble_id)
                    REFERENCES jem_jh02.ble_master (ble_id) ON DELETE CASCADE
            );

CREATE TABLE IF NOT EXISTS jem_jh02.ble_data_latest (
                ble_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                timestamp       TIMESTAMPTZ       NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1,
                updated_at      TIMESTAMPTZ       DEFAULT NOW(),
                PRIMARY KEY (ble_id, tag_id)
            );

CREATE INDEX IF NOT EXISTS idx_ble_data_latest_updated ON jem_jh02.ble_data_latest (updated_at DESC);

CREATE TABLE IF NOT EXISTS jem_jh02.ble_data_integrated (
                timestamp       TIMESTAMPTZ       NOT NULL,
                ble_id        SMALLINT          NOT NULL,
                tag_id          INTEGER           NOT NULL,
                v_bool          BOOLEAN,
                v_int           INTEGER,
                v_bigint        BIGINT,
                v_float         DOUBLE PRECISION,
                v_text          TEXT,
                quality_code    SMALLINT          DEFAULT 1
            );

SELECT create_hypertable(
                'jem_jh02.ble_data_integrated', 'timestamp',
                chunk_time_interval => INTERVAL '1 day',
                if_not_exists => TRUE
            );

CREATE INDEX IF NOT EXISTS idx_ble_data_int_ble_tag_time ON jem_jh02.ble_data_integrated (ble_id, tag_id, timestamp DESC);

ALTER TABLE jem_jh02.ble_data_integrated SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'ble_id, tag_id',
                timescaledb.compress_orderby = 'timestamp DESC'
            );

SELECT remove_compression_policy('jem_jh02.ble_data_integrated', if_exists => TRUE);

SELECT add_compression_policy(
                'jem_jh02.ble_data_integrated',
                INTERVAL '7 days'
            );

SELECT remove_retention_policy('jem_jh02.ble_data_integrated', if_exists => TRUE);

SELECT add_retention_policy(
                    'jem_jh02.ble_data_integrated',
                    INTERVAL '3 years'
                );

