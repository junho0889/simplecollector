-- ============================================================================
-- Simple Collector - Database Initialization Script
-- ============================================================================
-- TimescaleDB용 스키마 및 테이블 생성
-- ============================================================================

-- TimescaleDB 확장 활성화
CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;

-- ============================================================================
-- 메인 데이터 테이블
-- ============================================================================

-- 통합 PLC 데이터 테이블
-- 타입별 컬럼으로 다양한 데이터 타입을 효율적으로 저장
CREATE TABLE IF NOT EXISTS plc_data_integrated (
    source_time     TIMESTAMPTZ       NOT NULL,           -- PLC 소스 타임스탬프
    server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(), -- 서버 처리 타임스탬프
    plc_id          SMALLINT          NOT NULL,           -- PLC 번호 (1~100)
    tag_id          INTEGER           NOT NULL,           -- 태그 식별자

    -- 타입별 값 컬럼
    v_bool          BOOLEAN,                              -- Bit, Contact (M, P, K 영역 등)
    v_byte          SMALLINT,                             -- Raw 바이트 (0~255, 항상 저장)
    v_int           INTEGER,                              -- Word(2B) / DWord(4B) 정수
    v_bigint        BIGINT,                               -- LWord(8B) / 누적 카운터
    v_float         DOUBLE PRECISION,                     -- Real, LReal (실수형)
    v_text          TEXT,                                 -- 문자열, 에러 코드

    quality_code    SMALLINT          DEFAULT 1           -- 데이터 품질 (1:정상, 0:통신이상)
);

-- TimescaleDB 하이퍼테이블로 변환
-- 시간 기반 파티셔닝 (1일 단위 청크)
SELECT create_hypertable(
    'plc_data_integrated',
    'source_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 공간 파티셔닝 추가 (PLC ID 기반, 선택적)
-- SELECT add_dimension('plc_data_integrated', 'plc_id', number_partitions => 4);

-- ============================================================================
-- 인덱스
-- ============================================================================

-- PLC별 시간 기반 조회 인덱스
CREATE INDEX IF NOT EXISTS idx_plc_time
ON plc_data_integrated (plc_id, source_time DESC);

-- 태그별 조회 인덱스
CREATE INDEX IF NOT EXISTS idx_tag_time
ON plc_data_integrated (tag_id, source_time DESC);

-- 복합 인덱스 (PLC + 태그 + 시간)
CREATE INDEX IF NOT EXISTS idx_plc_tag_time
ON plc_data_integrated (plc_id, tag_id, source_time DESC);

-- ============================================================================
-- 데이터 보존 정책 (선택적)
-- ============================================================================

-- 90일 이상 된 데이터 자동 삭제 정책
-- 주석 해제하여 활성화
-- SELECT add_retention_policy('plc_data_integrated', INTERVAL '90 days');

-- ============================================================================
-- 압축 정책 (선택적)
-- ============================================================================

-- 7일 이상 된 데이터 자동 압축
-- 주석 해제하여 활성화
-- ALTER TABLE plc_data_integrated SET (
--     timescaledb.compress,
--     timescaledb.compress_segmentby = 'plc_id, tag_id'
-- );
-- SELECT add_compression_policy('plc_data_integrated', INTERVAL '7 days');

-- ============================================================================
-- 연속 집계 (선택적 - 대시보드용)
-- ============================================================================

-- 1시간 단위 집계 뷰
-- CREATE MATERIALIZED VIEW IF NOT EXISTS plc_data_hourly
-- WITH (timescaledb.continuous) AS
-- SELECT
--     time_bucket('1 hour', source_time) AS bucket,
--     plc_id,
--     tag_id,
--     AVG(value) AS avg_value,
--     MIN(value) AS min_value,
--     MAX(value) AS max_value,
--     COUNT(*) AS sample_count
-- FROM plc_data_integrated
-- GROUP BY bucket, plc_id, tag_id
-- WITH NO DATA;

-- 자동 새로고침 정책
-- SELECT add_continuous_aggregate_policy('plc_data_hourly',
--     start_offset => INTERVAL '3 hours',
--     end_offset => INTERVAL '1 hour',
--     schedule_interval => INTERVAL '1 hour'
-- );

-- ============================================================================
-- 마스터 테이블 (설정 동기화용)
-- ============================================================================
-- 수집기 시작 시 config 파일(YAML/CSV)에서 읽어서 전체 교체(TRUNCATE + INSERT)
-- ============================================================================

-- 스키마 생성 (마스터 테이블용)
CREATE SCHEMA IF NOT EXISTS master;

-- PLC 마스터 테이블
-- 수집기 시작 시 설정 파일에서 자동 동기화
CREATE TABLE IF NOT EXISTS master.plc_master (
    plc_id          SMALLINT        PRIMARY KEY,
    plc_name        VARCHAR(100)    NOT NULL,
    protocol_type   VARCHAR(50)     NOT NULL,       -- modbus, mc_protocol, demo 등
    host            VARCHAR(255),                   -- 연결 호스트
    port            INTEGER,                        -- 연결 포트
    description     TEXT,
    collect_yn      CHAR(1)         DEFAULT 'Y',    -- Y: 수집, N: 미수집
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

-- 태그 마스터 테이블
-- 수집기 시작 시 태그 설정 파일(CSV)에서 자동 동기화
CREATE TABLE IF NOT EXISTS master.tag_master (
    plc_id          SMALLINT        NOT NULL,
    tag_id          INTEGER         NOT NULL,
    tag_name        VARCHAR(100)    NOT NULL,
    memory          VARCHAR(10),                    -- 메모리 영역 (D, M, W, L, X, Y 등)
    address         INTEGER         NOT NULL,       -- 주소 번호
    data_type       VARCHAR(30)     NOT NULL,       -- 데이터 타입 (uint16, int16, uint32, float32, bool, string)
    scale           DOUBLE PRECISION DEFAULT 1.0,
    offset_value    DOUBLE PRECISION DEFAULT 0.0,
    decimals        SMALLINT,                       -- 소수점 자릿수
    word_length     SMALLINT,                       -- 워드 수 (문자열용)
    format          VARCHAR(30),                    -- 변환 포맷 (word→float32 등)
    unit            VARCHAR(30),
    description     TEXT,
    collection_group VARCHAR(50)    DEFAULT 'default',
    collect_yn      CHAR(1)         DEFAULT 'Y',    -- Y: 수집, N: 미수집
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW(),
    PRIMARY KEY (plc_id, tag_id)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_tag_master_plc ON master.tag_master (plc_id);
CREATE INDEX IF NOT EXISTS idx_tag_master_group ON master.tag_master (collection_group);
CREATE INDEX IF NOT EXISTS idx_tag_master_address ON master.tag_master (plc_id, memory, address);

-- ============================================================================
-- 데이터 품질 마스터 테이블
-- ============================================================================
-- quality_code 정의 (시작 시 동기화)

CREATE TABLE IF NOT EXISTS master.quality_master (
    quality_code    SMALLINT        PRIMARY KEY,
    quality_name    VARCHAR(50)     NOT NULL,       -- 품질 이름
    description     TEXT,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

-- 기본 데이터 품질 코드 (시작 시 교체됨)
INSERT INTO master.quality_master (quality_code, quality_name, description) VALUES
    (0, 'BAD', '통신 이상 또는 데이터 없음'),
    (1, 'GOOD', '정상 데이터'),
    (2, 'UNCERTAIN', '불확실한 데이터'),
    (3, 'TIMEOUT', '타임아웃'),
    (4, 'ERROR', '에러 발생'),
    (5, 'MANUAL', '수동 입력값'),
    (6, 'SIMULATED', '시뮬레이션 값')
ON CONFLICT (quality_code) DO NOTHING;

-- ============================================================================
-- 이벤트/알람 마스터 테이블
-- ============================================================================
-- 비트 주소와 이벤트 정의를 매핑 (L0 ON → "시스템 준비완료")
-- CSV 파일에서 로드하여 시작 시 전체 교체

CREATE TABLE IF NOT EXISTS master.event_master (
    event_id        INTEGER         PRIMARY KEY,    -- 이벤트 고유 ID
    plc_id          SMALLINT        NOT NULL,       -- PLC 번호
    memory          VARCHAR(10)     NOT NULL,       -- 메모리 영역 (L, M, X, Y)
    address         INTEGER         NOT NULL,       -- 주소 번호
    event_type      VARCHAR(30)     NOT NULL,       -- alarm, warning, info, status
    severity        SMALLINT        DEFAULT 2,      -- 1=info, 2=warning, 3=critical, 4=fatal
    event_name      VARCHAR(100)    NOT NULL,       -- 이벤트 이름
    message_on      TEXT,                           -- ON 시 메시지
    message_off     TEXT,                           -- OFF 시 메시지
    auto_reset      BOOLEAN         DEFAULT FALSE,  -- 자동 리셋 여부
    collection_group VARCHAR(50)    DEFAULT 'event',-- 수집 그룹
    enabled         BOOLEAN         DEFAULT TRUE,   -- 활성화 여부
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_event_master_plc ON master.event_master (plc_id);
CREATE INDEX IF NOT EXISTS idx_event_master_address ON master.event_master (plc_id, memory, address);
CREATE INDEX IF NOT EXISTS idx_event_master_type ON master.event_master (event_type);

-- ============================================================================
-- 컬렉션 그룹 마스터 테이블
-- ============================================================================
-- YAML에서 정의한 수집 그룹 정보 (시작 시 동기화)

CREATE TABLE IF NOT EXISTS master.collection_group_master (
    plc_id          SMALLINT        NOT NULL,
    group_name      VARCHAR(50)     NOT NULL,
    group_type      VARCHAR(30)     NOT NULL,       -- periodic, on_change
    interval_ms     INTEGER         NOT NULL,       -- 수집 주기 (ms)
    target_table    VARCHAR(100),                   -- 저장 대상 테이블
    enabled         BOOLEAN         DEFAULT TRUE,
    description     TEXT,
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    PRIMARY KEY (plc_id, group_name)
);

-- ============================================================================
-- 그룹별 데이터 테이블
-- ============================================================================
-- 각 수집 그룹별로 별도 테이블 생성 (성능 최적화)

-- fast 그룹용 테이블 (예: 1초 주기)
CREATE TABLE IF NOT EXISTS plc_data_fast (
    source_time     TIMESTAMPTZ       NOT NULL,
    server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    v_bool          BOOLEAN,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    quality_code    SMALLINT          DEFAULT 1
);

SELECT create_hypertable(
    'plc_data_fast',
    'source_time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_plc_data_fast_plc_time ON plc_data_fast (plc_id, source_time DESC);
CREATE INDEX IF NOT EXISTS idx_plc_data_fast_tag_time ON plc_data_fast (tag_id, source_time DESC);

-- slow 그룹용 테이블 (예: 10초 주기)
CREATE TABLE IF NOT EXISTS plc_data_slow (
    source_time     TIMESTAMPTZ       NOT NULL,
    server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    v_bool          BOOLEAN,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    quality_code    SMALLINT          DEFAULT 1
);

SELECT create_hypertable(
    'plc_data_slow',
    'source_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_plc_data_slow_plc_time ON plc_data_slow (plc_id, source_time DESC);
CREATE INDEX IF NOT EXISTS idx_plc_data_slow_tag_time ON plc_data_slow (tag_id, source_time DESC);

-- medium 그룹용 테이블 (예: 5초 주기)
CREATE TABLE IF NOT EXISTS plc_data_medium (
    source_time     TIMESTAMPTZ       NOT NULL,
    server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    v_bool          BOOLEAN,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    quality_code    SMALLINT          DEFAULT 1
);

SELECT create_hypertable(
    'plc_data_medium',
    'source_time',
    chunk_time_interval => INTERVAL '3 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_plc_data_medium_plc_time ON plc_data_medium (plc_id, source_time DESC);
CREATE INDEX IF NOT EXISTS idx_plc_data_medium_tag_time ON plc_data_medium (tag_id, source_time DESC);

-- ============================================================================
-- 이벤트 히스토리 테이블 (on_change 그룹용)
-- ============================================================================
-- 값 변경 시에만 저장 (이전 값과 비교)

CREATE TABLE IF NOT EXISTS event_history (
    event_time      TIMESTAMPTZ       NOT NULL,
    server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    event_id        INTEGER,                          -- event_master 참조 (NULL 가능)
    prev_value      DOUBLE PRECISION,                 -- 이전 값
    curr_value      DOUBLE PRECISION,                 -- 현재 값
    prev_bool       BOOLEAN,
    curr_bool       BOOLEAN,
    quality_code    SMALLINT          DEFAULT 1
);

SELECT create_hypertable(
    'event_history',
    'event_time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE
);

CREATE INDEX IF NOT EXISTS idx_event_history_plc_time ON event_history (plc_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_event_history_tag_time ON event_history (tag_id, event_time DESC);
CREATE INDEX IF NOT EXISTS idx_event_history_event_id ON event_history (event_id, event_time DESC);

-- ============================================================================
-- 알람 히스토리 테이블 (발생/해제 쌍)
-- ============================================================================
-- 알람 발생(start) → 해제(end) 쌍으로 관리
-- end_time이 NULL이면 현재 활성 알람

CREATE TABLE IF NOT EXISTS alarm_history (
    alarm_id        BIGSERIAL         PRIMARY KEY,
    plc_id          SMALLINT          NOT NULL,
    event_id        INTEGER           NOT NULL,       -- event_master 참조
    memory          VARCHAR(10)       NOT NULL,       -- 메모리 영역
    address         INTEGER           NOT NULL,       -- 주소 번호

    -- 발생 정보
    start_time      TIMESTAMPTZ       NOT NULL,       -- 알람 발생 시간
    start_value     BOOLEAN           DEFAULT TRUE,   -- 발생 시 값 (보통 TRUE)

    -- 해제 정보
    end_time        TIMESTAMPTZ,                      -- 알람 해제 시간 (NULL = 활성중)
    end_value       BOOLEAN,                          -- 해제 시 값 (보통 FALSE)

    -- 지속 시간 (초)
    duration_sec    INTEGER GENERATED ALWAYS AS (
        CASE WHEN end_time IS NOT NULL
             THEN EXTRACT(EPOCH FROM (end_time - start_time))::INTEGER
             ELSE NULL
        END
    ) STORED,

    -- 알람 정보 (event_master에서 복사, 빠른 조회용)
    event_type      VARCHAR(30),
    severity        SMALLINT,
    event_name      VARCHAR(100),
    message         TEXT,

    -- 확인 정보
    acknowledged    BOOLEAN           DEFAULT FALSE,
    ack_time        TIMESTAMPTZ,
    ack_user        VARCHAR(50),
    ack_comment     TEXT,

    created_at      TIMESTAMPTZ       DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_alarm_history_plc ON alarm_history (plc_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_alarm_history_event ON alarm_history (event_id, start_time DESC);
CREATE INDEX IF NOT EXISTS idx_alarm_history_active ON alarm_history (plc_id) WHERE end_time IS NULL;
CREATE INDEX IF NOT EXISTS idx_alarm_history_unack ON alarm_history (acknowledged) WHERE acknowledged = FALSE;
CREATE INDEX IF NOT EXISTS idx_alarm_history_severity ON alarm_history (severity, start_time DESC) WHERE end_time IS NULL;

-- ============================================================================
-- 활성 알람 뷰 (현재 발생중인 알람)
-- ============================================================================

CREATE OR REPLACE VIEW active_alarms AS
SELECT
    ah.alarm_id,
    ah.plc_id,
    pm.plc_name,
    ah.event_id,
    ah.memory,
    ah.address,
    ah.start_time,
    EXTRACT(EPOCH FROM (NOW() - ah.start_time))::INTEGER AS active_duration_sec,
    ah.event_type,
    ah.severity,
    ah.event_name,
    ah.message,
    ah.acknowledged,
    ah.ack_time,
    ah.ack_user
FROM alarm_history ah
LEFT JOIN master.plc_master pm ON ah.plc_id = pm.plc_id
WHERE ah.end_time IS NULL
ORDER BY ah.severity DESC, ah.start_time DESC;

-- ============================================================================
-- 알람 통계 뷰 (일별)
-- ============================================================================

CREATE OR REPLACE VIEW alarm_daily_stats AS
SELECT
    DATE(start_time) AS alarm_date,
    plc_id,
    event_type,
    severity,
    COUNT(*) AS alarm_count,
    COUNT(*) FILTER (WHERE end_time IS NOT NULL) AS resolved_count,
    AVG(duration_sec) FILTER (WHERE duration_sec IS NOT NULL) AS avg_duration_sec,
    MAX(duration_sec) FILTER (WHERE duration_sec IS NOT NULL) AS max_duration_sec
FROM alarm_history
GROUP BY DATE(start_time), plc_id, event_type, severity
ORDER BY alarm_date DESC, plc_id, severity DESC;

-- ============================================================================
-- 권한 설정 (선택적)
-- ============================================================================

-- 읽기 전용 사용자 생성 (대시보드용)
-- CREATE USER IF NOT EXISTS dashboard_user WITH PASSWORD 'dashboard_password';
-- GRANT SELECT ON ALL TABLES IN SCHEMA public TO dashboard_user;
-- GRANT SELECT ON ALL TABLES IN SCHEMA master TO dashboard_user;

-- ============================================================================
-- 유틸리티 함수
-- ============================================================================

-- 알람 해제 처리 함수
CREATE OR REPLACE FUNCTION close_alarm(
    p_plc_id SMALLINT,
    p_memory VARCHAR(10),
    p_address INTEGER,
    p_end_time TIMESTAMPTZ DEFAULT NOW()
) RETURNS INTEGER AS $$
DECLARE
    v_updated INTEGER;
BEGIN
    UPDATE alarm_history
    SET end_time = p_end_time,
        end_value = FALSE
    WHERE plc_id = p_plc_id
      AND memory = p_memory
      AND address = p_address
      AND end_time IS NULL;

    GET DIAGNOSTICS v_updated = ROW_COUNT;
    RETURN v_updated;
END;
$$ LANGUAGE plpgsql;

-- 알람 확인 처리 함수
CREATE OR REPLACE FUNCTION acknowledge_alarm(
    p_alarm_id BIGINT,
    p_user VARCHAR(50),
    p_comment TEXT DEFAULT NULL
) RETURNS BOOLEAN AS $$
BEGIN
    UPDATE alarm_history
    SET acknowledged = TRUE,
        ack_time = NOW(),
        ack_user = p_user,
        ack_comment = p_comment
    WHERE alarm_id = p_alarm_id
      AND acknowledged = FALSE;

    RETURN FOUND;
END;
$$ LANGUAGE plpgsql;

-- 그룹별 테이블 이름 반환 함수
CREATE OR REPLACE FUNCTION get_data_table_name(p_group_name VARCHAR(50))
RETURNS VARCHAR(100) AS $$
BEGIN
    RETURN 'plc_data_' || LOWER(p_group_name);
END;
$$ LANGUAGE plpgsql IMMUTABLE;

-- ============================================================================
-- 완료 메시지
-- ============================================================================
DO $$
BEGIN
    RAISE NOTICE '============================================';
    RAISE NOTICE 'Database initialization completed!';
    RAISE NOTICE '============================================';
    RAISE NOTICE 'Created schemas: master';
    RAISE NOTICE 'Master tables: plc_master, tag_master, event_master, quality_master, collection_group_master';
    RAISE NOTICE 'Data tables: plc_data_integrated, plc_data_fast, plc_data_slow, plc_data_medium';
    RAISE NOTICE 'Event tables: event_history, alarm_history';
    RAISE NOTICE 'Views: active_alarms, alarm_daily_stats';
    RAISE NOTICE '============================================';
END $$;
