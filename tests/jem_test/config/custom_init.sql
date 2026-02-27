-- ============================================================================
-- JEM 커스텀 SQL — alm_history
-- ============================================================================
-- 스키마 생성 후 자동 실행됩니다.
-- 플레이스홀더:
--   {schema}  → 설정된 스키마명 (예: jem_jh02)
--   {group}   → 수집 그룹명 (plc_data, alm)
--              {group} 포함 시 모든 그룹에 대해 반복 실행
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
-- 3. alm_history 테이블 + 트리거
-- ============================================================================
-- 목적: alm 그룹 태그의 v_bool 값이 변경될 때마다 이력을 기록한다.
--       alm_latest 테이블의 UPSERT 시 이전 v_bool과 비교하여,
--       값이 달라진 경우에만 alm_history에 INSERT 한다.
--       시간순 정렬 시 이전 행의 v_bool이 곧 prev 역할을 하므로
--       prev/curr 분리 없이 현재 값만 저장한다.
--
-- 동작 방식:
--   1) alm_latest에 UPSERT(INSERT ... ON CONFLICT UPDATE) 발생
--   2) BEFORE UPDATE 트리거가 OLD.v_bool과 NEW.v_bool을 비교
--   3) v_bool이 변경되면 alm_history에 INSERT
--   4) INSERT 시에는 AFTER INSERT 트리거로 첫 값 기록
-- ============================================================================

-- alm_history 테이블
CREATE TABLE IF NOT EXISTS {schema}.alm_history (
    timestamp       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),  -- 변경 감지 시각
    plc_id          SMALLINT        NOT NULL,
    tag_id          INTEGER         NOT NULL,
    v_bool          BOOLEAN                                  -- 알람 상태 (TRUE=발생, FALSE=해제)
);

-- 인덱스: 시간순 전체 조회 (최근 알람 이력)
CREATE INDEX IF NOT EXISTS idx_alm_history_time
    ON {schema}.alm_history (timestamp DESC);
-- 인덱스: PLC+태그별 시간순 조회
CREATE INDEX IF NOT EXISTS idx_alm_history_plc_tag
    ON {schema}.alm_history (plc_id, tag_id, timestamp DESC);

-- TimescaleDB hypertable 변환 (대량 이력 저장 시 성능 최적화)
SELECT create_hypertable(
    '{schema}.alm_history', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE,
    migrate_data => TRUE
);

-- 압축 정책 (1일 후 자동 압축)
ALTER TABLE {schema}.alm_history SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'plc_id, tag_id',
    timescaledb.compress_orderby   = 'timestamp DESC'
);
SELECT add_compression_policy(
    '{schema}.alm_history',
    INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 보관 정책 (1년)
SELECT add_retention_policy(
    '{schema}.alm_history',
    INTERVAL '1 year',
    if_not_exists => TRUE
);

-- 트리거 함수: alm_latest UPDATE 시 v_bool 변경 감지 → alm_history INSERT
CREATE OR REPLACE FUNCTION {schema}.fn_alm_history_on_update()
RETURNS TRIGGER AS $$
BEGIN
    IF (OLD.v_bool IS DISTINCT FROM NEW.v_bool) THEN
        INSERT INTO {schema}.alm_history (timestamp, plc_id, tag_id, v_bool)
        VALUES (NOW(), NEW.plc_id, NEW.tag_id, NEW.v_bool);
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 함수: alm_latest INSERT 시 첫 값 기록
CREATE OR REPLACE FUNCTION {schema}.fn_alm_history_on_insert()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO {schema}.alm_history (timestamp, plc_id, tag_id, v_bool)
    VALUES (NOW(), NEW.plc_id, NEW.tag_id, NEW.v_bool);

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 트리거 등록 (alm_latest 테이블)
DROP TRIGGER IF EXISTS trg_alm_history_update ON {schema}.alm_latest;
CREATE TRIGGER trg_alm_history_update
    BEFORE UPDATE ON {schema}.alm_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_alm_history_on_update();

DROP TRIGGER IF EXISTS trg_alm_history_insert ON {schema}.alm_latest;
CREATE TRIGGER trg_alm_history_insert
    AFTER INSERT ON {schema}.alm_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_alm_history_on_insert();


-- ============================================================================
-- 4. API 읽기 전용 계정
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
