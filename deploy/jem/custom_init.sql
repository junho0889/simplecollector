-- ============================================================================
-- JEM 커스텀 SQL
-- ============================================================================
-- 스키마 생성 후 자동 실행됩니다.
-- 플레이스홀더:
--   {schema}  → 설정된 스키마명 (예: jem_jh02)
-- ============================================================================


-- ============================================================================
-- 1. API 읽기 전용 계정
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
-- 2. 사이트 계층 구조 (회사 → 공장 → 라인 → PLC)
-- ============================================================================

-- 2-1. 회사
CREATE TABLE IF NOT EXISTS {schema}.company (
    company_id    SERIAL PRIMARY KEY,
    company_name  VARCHAR(100) NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-2. 공장
CREATE TABLE IF NOT EXISTS {schema}.factory (
    factory_id    SERIAL PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES {schema}.company(company_id),
    factory_name  VARCHAR(100) NOT NULL,
    address       TEXT,
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-3. 라인
CREATE TABLE IF NOT EXISTS {schema}.line (
    line_id       SERIAL PRIMARY KEY,
    factory_id    INTEGER NOT NULL REFERENCES {schema}.factory(factory_id),
    line_name     VARCHAR(100) NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-4. plc_master에 line_id FK 추가
ALTER TABLE {schema}.plc_master
    ADD COLUMN IF NOT EXISTS line_id INTEGER REFERENCES {schema}.line(line_id);


-- ============================================================================
-- 3. 교대 설정
-- ============================================================================

-- 3-1. 교대 시간
CREATE TABLE IF NOT EXISTS {schema}.shift_config (
    shift_type    VARCHAR(10) PRIMARY KEY,  -- 'day' / 'night'
    start_time    TIME NOT NULL,
    end_time      TIME NOT NULL,
    description   TEXT
);

INSERT INTO {schema}.shift_config (shift_type, start_time, end_time, description) VALUES
    ('day',   '08:30', '20:30', '주간'),
    ('night', '20:30', '08:30', '야간')
ON CONFLICT (shift_type) DO NOTHING;

-- ============================================================================
-- 4. 목표 생산수량 (라인별)
-- ============================================================================
CREATE TABLE IF NOT EXISTS {schema}.production_target (
    line_id       INTEGER NOT NULL REFERENCES {schema}.line(line_id),
    target_type   VARCHAR(10) NOT NULL,     -- 'day' / 'night' / 'daily'
    target_qty    INTEGER NOT NULL,
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (line_id, target_type)
);
