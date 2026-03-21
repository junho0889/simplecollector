-- ============================================================================
-- Simple Collector - Database Schema
-- ============================================================================

-- Enable TimescaleDB extension
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Main data table
CREATE TABLE IF NOT EXISTS plc_data_integrated (
    timestamp       TIMESTAMPTZ       NOT NULL,
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    v_bool          BOOLEAN,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    quality_code    SMALLINT          DEFAULT 1
);

-- Convert to hypertable
SELECT create_hypertable('plc_data_integrated', 'timestamp', if_not_exists => TRUE);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_plc_data_plc_tag
    ON plc_data_integrated (plc_id, tag_id, source_time DESC);

CREATE INDEX IF NOT EXISTS idx_plc_data_source_time
    ON plc_data_integrated (source_time DESC);

-- Grant permissions
GRANT ALL PRIVILEGES ON TABLE plc_data_integrated TO collector;

-- Verification
DO $$
BEGIN
    RAISE NOTICE 'Database initialized successfully!';
END $$;
