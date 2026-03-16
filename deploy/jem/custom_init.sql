-- ============================================================================
-- JEM 커스텀 SQL
-- ============================================================================
-- publisher의 auto_init_schema 이후 자동 실행됩니다.
-- publisher YAML의 custom_sql_path에 이 파일을 지정합니다.
--
-- 플레이스홀더:
--   {schema}  → publisher YAML의 schema_name (예: jem_jh02)
--   {group}   → 수집 그룹명 (plc_data, alm, log)
--              {group} 포함 시 모든 그룹에 대해 반복 실행
--
-- 실행 순서:
--   1. publisher가 글로벌/그룹 테이블 자동 생성 (schema_init.py)
--   2. 이 파일이 실행됨 (커스텀 테이블/권한 추가)
--
-- 이 파일에서 생성하는 객체:
--   [설정] company, factory, line          — 사이트 계층 구조
--   [설정] shift_config                    — N교대 시간 (2/3/4교대 유동 대응)
--   [설정] production_target               — 라인별 목표 생산수량
--   [이력] production_target_history       — 목표수량 변경 이력 (트리거 자동 기록)
--   [이력] shift_config_history            — 교대 시간 변경 이력 (트리거 자동 기록)
--   [실시간] production_shift_current      — PLC별 현재 교대 생산 실적 (트리거 갱신)
--   [이력] production_shift_history        — 교대 완료 시 실적 스냅샷
--   [통계] production_hourly              — 시간별 생산 스냅샷 (정시 캡처)
--   [통계] production_daily               — 일별 생산 집계 (마지막 교대 종료 시 UPSERT)
--   [뷰] v_production_weekly              — 주간 통계 (production_daily 기반, 전체 합산)
--   [뷰] v_production_monthly             — 월간 통계 (production_daily 기반, 전체 합산)
--   [뷰] v_production_yearly              — 연간 통계 (production_daily 기반, 전체 합산)
--   [뷰] v_production_shift_weekly        — 교대별 주간 통계 (production_shift_history 기반)
--   [뷰] v_production_shift_monthly       — 교대별 월간 통계 (production_shift_history 기반)
--   [뷰] v_alarm_downtime                 — 알람 정지 구간 (alm_history TRUE/FALSE 페어링)
--   [뷰] v_alarm_ranking_daily            — 일별 알람 순위 (교대별, 정지시간/발생횟수)
--   [뷰] v_alarm_ranking_weekly           — 주별 알람 순위
--   [뷰] v_alarm_ranking_monthly          — 월별 알람 순위
--   [함수] fn_get_current_shift()          — 현재 교대 판정 헬퍼
--   [트리거] fn_production_shift_tracker() — 생산수량/NG수량 변경 감지 + 교대 전환
--   [트리거] fn_downtime_tracker()         — 알람/액션 정지시간 누적 (중복 제거)
--   [트리거] fn_hourly_snapshot()          — 정시 시간별 스냅샷 캡처
--   [트리거] fn_daily_aggregate()          — 마지막 교대 종료 시 일별 집계
--   [로그] custom_init_log                — SQL 실행 로그 (성공/에러 기록)
--   [hypertable] production_shift_history — 1일 압축, 3년 보관
--   [hypertable] production_hourly       — 1일 압축, 3년 보관
--   [hypertable] production_daily        — 1일 압축, 3년 보관
--   [백필] fn_backfill_daily_statistics() — 누락된 일별 통계 자동 복구
--   [뷰] {group}_latest_view              — latest + master 조인 뷰 (그룹별)
--   [트리거] {group}_master_updated_at    — master 수정 시 updated_at 자동 갱신
--   [권한] api_reader                      — API 조회용 읽기 전용 계정
-- ============================================================================


-- ============================================================================
-- 그룹별 공통 객체 ({group} 플레이스홀더 — 모든 그룹에 대해 반복 실행)
-- ============================================================================

-- {group}_latest + {group}_master 조인 뷰
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

-- {group}_master 수정 시 updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION {schema}.{group}_master_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_{group}_master_updated_at ON {schema}.{group}_master;
CREATE TRIGGER trg_{group}_master_updated_at
    BEFORE UPDATE ON {schema}.{group}_master
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.{group}_master_updated_at();


-- ============================================================================
-- 1. API 읽기 전용 계정
-- ============================================================================
-- 용도: 외부 앱(HMI, 대시보드 등)에서 DB를 조회할 때 사용하는 읽기 전용 계정
-- 권한: 스키마 내 모든 테이블 SELECT만 허용, INSERT/UPDATE/DELETE 불가
-- 비밀번호 변경 시: ALTER ROLE api_reader WITH PASSWORD 'new_password';
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
-- 향후 생성되는 테이블에도 자동으로 SELECT 권한 부여
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO api_reader;


-- ============================================================================
-- 1-2. 실행 로그 테이블
-- ============================================================================
-- 용도: custom_init.sql 실행 시 각 단계의 성공/실패를 기록
-- hypertable 변환, 백필 등 에러 발생 가능 구간을 추적
CREATE TABLE IF NOT EXISTS {schema}.custom_init_log (
    executed_at     TIMESTAMPTZ DEFAULT NOW(),
    step_name       TEXT NOT NULL,              -- 실행 단계명
    status          TEXT NOT NULL,              -- 'success' / 'error' / 'skip'
    message         TEXT                        -- 에러 메시지 또는 결과 설명
);

CREATE OR REPLACE FUNCTION {schema}.fn_log_init(
    p_step TEXT, p_status TEXT, p_message TEXT DEFAULT NULL
) RETURNS VOID AS $$
BEGIN
    INSERT INTO {schema}.custom_init_log (step_name, status, message)
    VALUES (p_step, p_status, p_message);
END;
$$ LANGUAGE plpgsql;


-- ============================================================================
-- 2. 사이트 계층 구조 (회사 → 공장 → 라인 → PLC)
-- ============================================================================
-- 용도: 설비의 물리적 위치를 계층으로 관리
-- 관계: company(1) → factory(N) → line(N) → plc_master(N)
-- 예시: NASDAQ(회사) → JH공장(공장) → JH02(라인) → PLC-A~L(10대)
--
-- plc_master는 publisher가 자동 생성하는 테이블이며,
-- 여기서는 line_id FK 컬럼만 추가하여 라인과 연결합니다.

-- 2-1. 회사
CREATE TABLE IF NOT EXISTS {schema}.company (
    company_id    SERIAL PRIMARY KEY,
    company_name  VARCHAR(100) NOT NULL,       -- 회사명
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-2. 공장
CREATE TABLE IF NOT EXISTS {schema}.factory (
    factory_id    SERIAL PRIMARY KEY,
    company_id    INTEGER NOT NULL REFERENCES {schema}.company(company_id),
    factory_name  VARCHAR(100) NOT NULL,       -- 공장명
    address       TEXT,                         -- 공장 주소
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-3. 라인
CREATE TABLE IF NOT EXISTS {schema}.line (
    line_id       SERIAL PRIMARY KEY,
    factory_id    INTEGER NOT NULL REFERENCES {schema}.factory(factory_id),
    line_name     VARCHAR(100) NOT NULL,       -- 라인명 (예: JH02)
    description   TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- 2-4. plc_master에 line_id FK 추가
-- 기존 plc_master의 site/area/line VARCHAR 컬럼 대신 정규화된 참조
ALTER TABLE {schema}.plc_master
    ADD COLUMN IF NOT EXISTS line_id INTEGER REFERENCES {schema}.line(line_id);

-- 2-5. 초기 데이터: 회사 → 공장 → 라인 (이미 존재하면 스킵)
INSERT INTO {schema}.company (company_id, company_name)
VALUES (1, '진영전기')
ON CONFLICT (company_id) DO NOTHING;

INSERT INTO {schema}.factory (factory_id, company_id, factory_name)
VALUES (1, 1, '마산자유무역지역')
ON CONFLICT (factory_id) DO NOTHING;

INSERT INTO {schema}.line (line_id, factory_id, line_name)
VALUES (1, 1, 'JH02')
ON CONFLICT (line_id) DO NOTHING;

-- 2-6. PLC 10대를 JH02 라인에 매핑
UPDATE {schema}.plc_master SET line_id = 1 WHERE line_id IS NULL;


-- ============================================================================
-- 3. 교대 설정
-- ============================================================================
-- 용도: N교대 시간을 DB에서 관리 (2교대/3교대/4교대 유동 대응)
-- 앱에서 이 테이블을 참조하여 현재 교대 판정, 생산량 집계 시간 구간 결정
--
-- shift_order: 하루 내 교대 순서 (1=첫 교대, 2=둘째, ...). 마지막 교대 종료 시 일별 집계 실행
-- 자정을 넘는 교대: end_time < start_time (예: 22:00 → 06:00)
--
-- 2교대 예시:
--   ('day',   1, '08:30', '20:30', '주간')
--   ('night', 2, '20:30', '08:30', '야간')
-- 3교대 예시:
--   ('day',     1, '06:00', '14:00', '주간')
--   ('swing',   2, '14:00', '22:00', '중간')
--   ('night',   3, '22:00', '06:00', '야간')

CREATE TABLE IF NOT EXISTS {schema}.shift_config (
    shift_type    VARCHAR(10) PRIMARY KEY,     -- 교대 식별자 (자유 지정)
    shift_order   INTEGER NOT NULL UNIQUE,     -- 하루 내 교대 순서 (1, 2, 3, ...)
    start_time    TIME NOT NULL,               -- 교대 시작 시각 (KST)
    end_time      TIME NOT NULL,               -- 교대 종료 시각 (KST)
    description   TEXT
);

-- 초기 데이터: 2교대 (이미 존재하면 스킵)
INSERT INTO {schema}.shift_config (shift_type, shift_order, start_time, end_time, description) VALUES
    ('day',   1, '08:30', '20:30', '주간'),
    ('night', 2, '20:30', '08:30', '야간')
ON CONFLICT (shift_type) DO NOTHING;

-- 3-2. 교대 시간 변경 이력
-- 용도: 교대 시간이 언제, 어떻게 변경되었는지 전체 이력 보관
-- 통계 시 특정 시점의 유효 교대 시간을 조회할 수 있음
-- 조회 예시 (2026-03-12 시점의 주간 교대 시간):
--   SELECT start_time, end_time FROM shift_config_history
--   WHERE shift_type = 'day' AND changed_at <= '2026-03-12'
--   ORDER BY changed_at DESC LIMIT 1;
CREATE TABLE IF NOT EXISTS {schema}.shift_config_history (
    id            SERIAL PRIMARY KEY,
    shift_type    VARCHAR(10) NOT NULL,        -- 'day' / 'night'
    start_time    TIME NOT NULL,               -- 변경된 교대 시작 시각
    end_time      TIME NOT NULL,               -- 변경된 교대 종료 시각
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- 변경 시각
);

-- 시점별 교대 시간 조회 최적화 인덱스
CREATE INDEX IF NOT EXISTS idx_shift_config_history_lookup
    ON {schema}.shift_config_history (shift_type, changed_at DESC);

-- 3-3. 트리거: shift_config 변경 시 이력 자동 기록
CREATE OR REPLACE FUNCTION {schema}.fn_shift_config_history()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO {schema}.shift_config_history (shift_type, start_time, end_time)
    VALUES (NEW.shift_type, NEW.start_time, NEW.end_time);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_shift_config_history ON {schema}.shift_config;
CREATE TRIGGER trg_shift_config_history
    AFTER INSERT OR UPDATE ON {schema}.shift_config
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_shift_config_history();


-- ============================================================================
-- 4. 목표 생산수량 (라인별)
-- ============================================================================
-- 용도: 라인별 교대/일일 목표 생산수량 설정
-- 앱에서 이 값을 참조하여 달성률(실적/목표) 계산
--
-- 설정 예시:
--   INSERT INTO production_target VALUES (1, 'day', 600);    -- JH02 주간 600개
--   INSERT INTO production_target VALUES (1, 'night', 400);  -- JH02 야간 400개
--   INSERT INTO production_target VALUES (1, 'daily', 1000); -- JH02 일일 1000개
--
-- 변경 시: UPDATE production_target SET target_qty = 700 WHERE line_id = 1 AND target_type = 'day';
--   → 트리거에 의해 production_target_history에 자동 기록됨

-- 4-1. 현재 목표수량 (UPSERT용)
CREATE TABLE IF NOT EXISTS {schema}.production_target (
    line_id       INTEGER NOT NULL REFERENCES {schema}.line(line_id),
    target_type   VARCHAR(10) NOT NULL,        -- 'day': 주간, 'night': 야간, 'daily': 일일
    target_qty    INTEGER NOT NULL,            -- 목표 수량
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (line_id, target_type)
);

-- 4-2. 목표수량 변경 이력
-- 용도: 목표수량이 언제, 얼마로 변경되었는지 전체 이력 보관
-- 통계 시 특정 시점의 유효 목표를 조회할 수 있음
-- 조회 예시 (2026-03-12 주간 교대의 목표):
--   SELECT target_qty FROM production_target_history
--   WHERE line_id = 1 AND target_type = 'day'
--     AND changed_at <= '2026-03-12 08:30+09'
--   ORDER BY changed_at DESC LIMIT 1;
CREATE TABLE IF NOT EXISTS {schema}.production_target_history (
    id            SERIAL PRIMARY KEY,
    line_id       INTEGER NOT NULL,
    target_type   VARCHAR(10) NOT NULL,        -- 'day' / 'night' / 'daily'
    target_qty    INTEGER NOT NULL,            -- 변경된 목표 수량
    changed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()  -- 변경 시각
);

-- 시점별 목표 조회 최적화 인덱스
CREATE INDEX IF NOT EXISTS idx_target_history_lookup
    ON {schema}.production_target_history (line_id, target_type, changed_at DESC);

-- 4-3. 트리거: production_target 변경 시 이력 자동 기록
-- production_target에 INSERT 또는 UPDATE가 발생하면
-- 변경된 값을 production_target_history에 자동으로 INSERT
CREATE OR REPLACE FUNCTION {schema}.fn_production_target_history()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO {schema}.production_target_history (line_id, target_type, target_qty)
    VALUES (NEW.line_id, NEW.target_type, NEW.target_qty);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_production_target_history ON {schema}.production_target;
CREATE TRIGGER trg_production_target_history
    AFTER INSERT OR UPDATE ON {schema}.production_target
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_production_target_history();

-- 4-4. 초기 데이터: JH02 라인 목표수량 (이미 존재하면 스킵)
INSERT INTO {schema}.production_target (line_id, target_type, target_qty) VALUES
    (1, 'day',   20000),
    (1, 'night', 0)
ON CONFLICT (line_id, target_type) DO NOTHING;


-- ============================================================================
-- 5. 교대별 생산 실적 (실시간 + 이력)
-- ============================================================================
-- 용도: PLC별 현재 교대의 생산수량/NG수량/정지시간을 실시간 추적하고,
--       교대 종료 시 이력 테이블에 스냅샷을 남김
--
-- 동작 원리:
--   1. plc_data_latest에 '생산수량' 태그 UPDATE 발생
--      → fn_production_shift_tracker()가 누적값 비교로 교대 생산량 계산
--   2. alm_latest / action_latest에 v_bool 변경 발생
--      → fn_downtime_tracker()가 알람/액션별 정지시간 누적
--   3. 교대 시간이 바뀌면 현재 실적을 history에 저장하고 current를 리셋
--
-- 누적값 패턴 (accumulated + run_start):
--   PLC의 생산수량은 누적 카운터이며, 교대 시작 시 리셋되지 않을 수 있음
--   예) 20:29(주간) 생산수량=400 → 20:31(야간) 생산수량=500
--       → run_start=400으로 설정, 야간 실적 = 누적값 - 400 = 100
--   accumulated: PLC에서 마지막으로 수신한 누적값
--   run_start:   교대 시작 시점의 누적값 (베이스라인)
--   shift_production = accumulated - run_start
--
-- 태그 매칭:
--   plc_data_master.description LIKE '%생산수량%' → 생산수량 태그
--   plc_data_master.description LIKE '%NG%수량%'  → NG수량 태그
--   (PLC별로 레지스터 주소가 다르므로 description 기반 매칭 사용)
--
-- 정지시간 중복 제거:
--   alm과 action이 동시에 활성화되면 정지시간이 이중 집계되지 않도록
--   active_count 패턴 사용. 각 소스별 활성 개수를 추적하고,
--   전체 합산 정지시간은 "어느 하나라도 활성" 구간만 측정

-- 5-1. 현재 교대 실적 (PLC별 1행, 트리거가 실시간 갱신)
CREATE TABLE IF NOT EXISTS {schema}.production_shift_current (
    plc_id              INTEGER NOT NULL PRIMARY KEY,

    -- 교대 정보
    shift_type          VARCHAR(10),                -- 'day' / 'night'
    shift_start         TIMESTAMPTZ,                -- 교대 시작 시각
    shift_end           TIMESTAMPTZ,                -- 교대 종료 시각 (예정)
    target_qty          INTEGER DEFAULT 0,          -- 해당 교대의 목표수량 (shift_config + production_target 참조)
    target_time_min     NUMERIC(8,2) DEFAULT 0,     -- 목표 생산시간 (교대시간, 분)

    -- 태그 매핑 (최초 조회 후 캐싱)
    prod_tag_id         INTEGER,                    -- '생산수량' 태그의 tag_id
    ng_tag_id           INTEGER,                    -- 'NG 수량' 태그의 tag_id (없으면 NULL)

    -- 생산수량 추적
    prod_accumulated    NUMERIC(12,2) DEFAULT 0,    -- PLC 누적 생산수량 (마지막 수신값)
    prod_run_start      NUMERIC(12,2) DEFAULT 0,    -- 교대 시작 시점 누적값 (베이스라인)
    shift_production    NUMERIC(12,2) DEFAULT 0,    -- 교대 생산수량 = accumulated - run_start

    -- NG수량 추적
    ng_accumulated      NUMERIC(12,2) DEFAULT 0,    -- PLC 누적 NG수량 (마지막 수신값)
    ng_run_start        NUMERIC(12,2) DEFAULT 0,    -- 교대 시작 시점 NG 누적값
    shift_ng_qty        NUMERIC(12,2) DEFAULT 0,    -- 교대 NG수량 = ng_accumulated - ng_run_start

    -- 알람 정지시간 (alm_latest v_bool 기반)
    alm_active_count    INTEGER DEFAULT 0,          -- 현재 활성 알람 수
    alm_downtime_start  TIMESTAMPTZ,                -- 알람 정지 시작 시각 (active_count 0→1 전환 시)
    alm_downtime_min    NUMERIC(10,2) DEFAULT 0,    -- 알람 누적 정지시간 (분)

    -- 액션 정지시간 (action_latest v_bool 기반)
    action_active_count INTEGER DEFAULT 0,          -- 현재 활성 액션 수
    action_downtime_start TIMESTAMPTZ,              -- 액션 정지 시작 시각
    action_downtime_min NUMERIC(10,2) DEFAULT 0,    -- 액션 누적 정지시간 (분)

    -- 합산 정지시간 (알람 OR 액션, 중복 제거)
    total_downtime_start TIMESTAMPTZ,               -- 전체 정지 시작 시각 (any active 0→1)
    total_downtime_min  NUMERIC(10,2) DEFAULT 0,    -- 전체 누적 정지시간 (분)

    -- 계산값 (트리거에서 자동 갱신)
    achievement_rate    NUMERIC(6,2) DEFAULT 0,     -- 달성률(%) = shift_production / target_qty * 100
    first_pass_yield    NUMERIC(6,2) DEFAULT 0,     -- 직행률(%) = shift_production / (shift_production + shift_ng_qty) * 100
    alm_operating_rate  NUMERIC(6,2) DEFAULT 0,     -- 알람 가동률(%) = (target_time - alm_downtime) / target_time * 100
    action_operating_rate NUMERIC(6,2) DEFAULT 0,   -- 액션 가동률(%)
    operating_rate      NUMERIC(6,2) DEFAULT 0,     -- 전체 가동률(%) = (target_time - total_downtime) / target_time * 100

    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 5-2. 교대 완료 이력 (교대 전환 시 current → history로 스냅샷)
-- 조회 예시: 특정 PLC의 최근 7일 교대 실적
--   SELECT * FROM production_shift_history
--   WHERE plc_id = 1 AND shift_start >= NOW() - INTERVAL '7 days'
--   ORDER BY shift_start DESC;
CREATE TABLE IF NOT EXISTS {schema}.production_shift_history (
    plc_id              INTEGER NOT NULL,

    -- 교대 정보
    shift_type          VARCHAR(10),                -- 'day' / 'night'
    shift_start         TIMESTAMPTZ,                -- 교대 시작 시각
    shift_end           TIMESTAMPTZ,                -- 교대 종료 시각 (실제)
    target_qty          INTEGER DEFAULT 0,          -- 해당 교대의 목표수량

    -- 생산 실적
    shift_production    NUMERIC(12,2) DEFAULT 0,    -- 교대 생산수량
    shift_ng_qty        NUMERIC(12,2) DEFAULT 0,    -- 교대 NG수량

    -- 정지시간 (분)
    alm_downtime_min    NUMERIC(10,2) DEFAULT 0,    -- 알람 정지시간
    action_downtime_min NUMERIC(10,2) DEFAULT 0,    -- 액션 정지시간
    total_downtime_min  NUMERIC(10,2) DEFAULT 0,    -- 전체 정지시간 (중복 제거)
    target_time_min     NUMERIC(8,2) DEFAULT 0,     -- 목표 생산시간 (분)

    -- 계산값
    achievement_rate    NUMERIC(6,2) DEFAULT 0,     -- 달성률(%)
    first_pass_yield    NUMERIC(6,2) DEFAULT 0,     -- 직행률(%)
    alm_operating_rate  NUMERIC(6,2) DEFAULT 0,     -- 알람 가동률(%)
    action_operating_rate NUMERIC(6,2) DEFAULT 0,   -- 액션 가동률(%)
    operating_rate      NUMERIC(6,2) DEFAULT 0,     -- 전체 가동률(%)

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 이력 조회 최적화 인덱스
CREATE INDEX IF NOT EXISTS idx_shift_history_lookup
    ON {schema}.production_shift_history (plc_id, shift_start DESC);


-- ============================================================================
-- 6. 헬퍼 함수 + 트리거
-- ============================================================================

-- 6-1. 현재 교대 판정 함수
-- shift_config의 모든 교대를 순회하여 현재 시각에 해당하는 교대를 반환
-- 자정을 넘는 교대(end_time < start_time) 자동 처리
-- 사용: SELECT * FROM fn_get_current_shift();
CREATE OR REPLACE FUNCTION {schema}.fn_get_current_shift()
RETURNS TABLE (
    shift_type  VARCHAR(10),
    shift_start TIMESTAMPTZ,
    shift_end   TIMESTAMPTZ
) AS $$
DECLARE
    v_now       TIMESTAMPTZ := NOW();
    v_today     DATE := (v_now AT TIME ZONE 'Asia/Seoul')::DATE;
    v_time_now  TIME := (v_now AT TIME ZONE 'Asia/Seoul')::TIME;
    v_rec       RECORD;
    v_matched   BOOLEAN := FALSE;
BEGIN
    -- shift_config의 모든 교대를 순회하며 현재 시각 매칭
    FOR v_rec IN
        SELECT sc.shift_type, sc.start_time, sc.end_time
        FROM {schema}.shift_config sc
        ORDER BY sc.shift_order
    LOOP
        IF v_rec.start_time < v_rec.end_time THEN
            -- 자정을 넘지 않는 교대 (예: 06:00~14:00)
            IF v_time_now >= v_rec.start_time AND v_time_now < v_rec.end_time THEN
                shift_type  := v_rec.shift_type;
                shift_start := (v_today || ' ' || v_rec.start_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                shift_end   := (v_today || ' ' || v_rec.end_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                v_matched := TRUE;
                EXIT;
            END IF;
        ELSE
            -- 자정을 넘는 교대 (예: 22:00~06:00)
            IF v_time_now >= v_rec.start_time THEN
                -- 자정 이전 (예: 23:00 → 시작=오늘 22:00, 종료=내일 06:00)
                shift_type  := v_rec.shift_type;
                shift_start := (v_today || ' ' || v_rec.start_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                shift_end   := ((v_today + 1) || ' ' || v_rec.end_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                v_matched := TRUE;
                EXIT;
            ELSIF v_time_now < v_rec.end_time THEN
                -- 자정 이후 (예: 03:00 → 시작=어제 22:00, 종료=오늘 06:00)
                shift_type  := v_rec.shift_type;
                shift_start := ((v_today - 1) || ' ' || v_rec.start_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                shift_end   := (v_today || ' ' || v_rec.end_time)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';
                v_matched := TRUE;
                EXIT;
            END IF;
        END IF;
    END LOOP;

    -- 매칭 실패 시 (shift_config 미설정) NULL 반환
    IF NOT v_matched THEN
        shift_type  := NULL;
        shift_start := NULL;
        shift_end   := NULL;
    END IF;

    RETURN NEXT;
END;
$$ LANGUAGE plpgsql;


-- 6-2. 생산수량/NG수량 추적 트리거
-- plc_data_latest UPDATE 시 실행
-- 동작:
--   1. 변경된 태그가 '생산수량' 또는 'NG 수량'인지 description으로 판별
--   2. 현재 교대와 current 테이블의 교대가 다르면 → 교대 전환 처리
--      - 기존 실적을 history에 저장
--      - current를 새 교대로 리셋 (run_start = 현재 누적값)
--   3. accumulated 갱신, shift_production/shift_ng_qty 재계산
--   4. 달성률/직행률 등 계산값 갱신
CREATE OR REPLACE FUNCTION {schema}.fn_production_shift_tracker()
RETURNS TRIGGER AS $$
DECLARE
    v_tag_desc   TEXT;
    v_is_prod    BOOLEAN := FALSE;
    v_is_ng      BOOLEAN := FALSE;
    v_cur        RECORD;
    v_shift      RECORD;
    v_new_val    NUMERIC(12,2);
    v_line_id    INTEGER;
    v_target     INTEGER;
    v_target_min NUMERIC(8,2);
BEGIN
    -- 변경된 태그의 description 조회
    SELECT m.description INTO v_tag_desc
    FROM {schema}.plc_data_master m
    WHERE m.plc_id = NEW.plc_id AND m.tag_id = NEW.tag_id;

    IF v_tag_desc IS NULL THEN
        RETURN NEW;
    END IF;

    -- 태그 종류 판별
    IF v_tag_desc LIKE '%생산수량%' THEN
        v_is_prod := TRUE;
    ELSIF v_tag_desc LIKE '%NG%수량%' THEN
        v_is_ng := TRUE;
    ELSE
        RETURN NEW;  -- 관심 태그가 아니면 스킵
    END IF;

    -- 현재 교대 정보 조회
    SELECT * INTO v_shift FROM {schema}.fn_get_current_shift();

    -- 수신값 (v_float, v_bigint, v_int 순으로 사용 — uint32는 v_bigint에 저장됨)
    v_new_val := COALESCE(NEW.v_float, NEW.v_bigint::NUMERIC, NEW.v_int::NUMERIC, 0);

    -- current 행 조회 (없으면 INSERT)
    SELECT * INTO v_cur
    FROM {schema}.production_shift_current
    WHERE plc_id = NEW.plc_id;

    IF NOT FOUND THEN
        -- 최초 진입: 태그 매핑 조회
        -- line_id 조회 (plc_master에서)
        SELECT pm.line_id INTO v_line_id
        FROM {schema}.plc_master pm
        WHERE pm.plc_id = NEW.plc_id;

        -- 목표수량 조회
        SELECT pt.target_qty INTO v_target
        FROM {schema}.production_target pt
        WHERE pt.line_id = v_line_id AND pt.target_type = v_shift.shift_type;
        v_target := COALESCE(v_target, 0);

        -- 목표 생산시간 (교대 시간, 분)
        v_target_min := EXTRACT(EPOCH FROM (v_shift.shift_end - v_shift.shift_start)) / 60.0;

        INSERT INTO {schema}.production_shift_current (
            plc_id, shift_type, shift_start, shift_end,
            target_qty, target_time_min,
            prod_tag_id, ng_tag_id,
            prod_accumulated, prod_run_start, shift_production,
            ng_accumulated, ng_run_start, shift_ng_qty
        )
        SELECT
            NEW.plc_id, v_shift.shift_type, v_shift.shift_start, v_shift.shift_end,
            v_target, v_target_min,
            -- 생산수량 태그 조회
            (SELECT m.tag_id FROM {schema}.plc_data_master m
             WHERE m.plc_id = NEW.plc_id AND m.description LIKE '%생산수량%' LIMIT 1),
            -- NG수량 태그 조회 (없으면 NULL)
            (SELECT m.tag_id FROM {schema}.plc_data_master m
             WHERE m.plc_id = NEW.plc_id AND m.description LIKE '%NG%수량%' LIMIT 1),
            -- 생산수량 초기값
            CASE WHEN v_is_prod THEN v_new_val ELSE 0 END,
            CASE WHEN v_is_prod THEN v_new_val ELSE 0 END,
            0,
            -- NG수량 초기값
            CASE WHEN v_is_ng THEN v_new_val ELSE 0 END,
            CASE WHEN v_is_ng THEN v_new_val ELSE 0 END,
            0;

        RETURN NEW;
    END IF;

    -- 교대 전환 감지: current의 교대 != 현재 교대
    IF v_cur.shift_type IS DISTINCT FROM v_shift.shift_type
       OR v_cur.shift_start IS DISTINCT FROM v_shift.shift_start THEN

        -- 진행 중인 정지시간 정산 (활성 상태에서 교대 전환 시)
        IF v_cur.alm_active_count > 0 AND v_cur.alm_downtime_start IS NOT NULL THEN
            v_cur.alm_downtime_min := v_cur.alm_downtime_min
                + EXTRACT(EPOCH FROM (NOW() - v_cur.alm_downtime_start)) / 60.0;
        END IF;
        IF v_cur.action_active_count > 0 AND v_cur.action_downtime_start IS NOT NULL THEN
            v_cur.action_downtime_min := v_cur.action_downtime_min
                + EXTRACT(EPOCH FROM (NOW() - v_cur.action_downtime_start)) / 60.0;
        END IF;
        IF (v_cur.alm_active_count > 0 OR v_cur.action_active_count > 0)
           AND v_cur.total_downtime_start IS NOT NULL THEN
            v_cur.total_downtime_min := v_cur.total_downtime_min
                + EXTRACT(EPOCH FROM (NOW() - v_cur.total_downtime_start)) / 60.0;
        END IF;

        -- 기존 교대 실적을 history에 저장
        INSERT INTO {schema}.production_shift_history (
            plc_id, shift_type, shift_start, shift_end, target_qty,
            shift_production, shift_ng_qty,
            alm_downtime_min, action_downtime_min, total_downtime_min, target_time_min,
            achievement_rate, first_pass_yield,
            alm_operating_rate, action_operating_rate, operating_rate
        ) VALUES (
            v_cur.plc_id, v_cur.shift_type, v_cur.shift_start, NOW(), v_cur.target_qty,
            v_cur.shift_production, v_cur.shift_ng_qty,
            v_cur.alm_downtime_min, v_cur.action_downtime_min, v_cur.total_downtime_min,
            v_cur.target_time_min,
            v_cur.achievement_rate, v_cur.first_pass_yield,
            v_cur.alm_operating_rate, v_cur.action_operating_rate, v_cur.operating_rate
        );

        -- 새 교대로 리셋
        SELECT pm.line_id INTO v_line_id
        FROM {schema}.plc_master pm WHERE pm.plc_id = NEW.plc_id;

        SELECT pt.target_qty INTO v_target
        FROM {schema}.production_target pt
        WHERE pt.line_id = v_line_id AND pt.target_type = v_shift.shift_type;
        v_target := COALESCE(v_target, 0);

        v_target_min := EXTRACT(EPOCH FROM (v_shift.shift_end - v_shift.shift_start)) / 60.0;

        UPDATE {schema}.production_shift_current SET
            shift_type       = v_shift.shift_type,
            shift_start      = v_shift.shift_start,
            shift_end        = v_shift.shift_end,
            target_qty       = v_target,
            target_time_min  = v_target_min,
            -- run_start = 현재 누적값 (베이스라인), shift_production = 0
            prod_accumulated = CASE WHEN v_is_prod THEN v_new_val ELSE v_cur.prod_accumulated END,
            prod_run_start   = CASE WHEN v_is_prod THEN v_new_val ELSE v_cur.prod_accumulated END,
            shift_production = 0,
            ng_accumulated   = CASE WHEN v_is_ng THEN v_new_val ELSE v_cur.ng_accumulated END,
            ng_run_start     = CASE WHEN v_is_ng THEN v_new_val ELSE v_cur.ng_accumulated END,
            shift_ng_qty     = 0,
            -- 정지시간 리셋
            alm_active_count    = 0,
            alm_downtime_start  = NULL,
            alm_downtime_min    = 0,
            action_active_count = 0,
            action_downtime_start = NULL,
            action_downtime_min = 0,
            total_downtime_start = NULL,
            total_downtime_min  = 0,
            -- 계산값 리셋
            achievement_rate    = 0,
            first_pass_yield    = 0,
            alm_operating_rate  = 0,
            action_operating_rate = 0,
            operating_rate      = 0,
            updated_at          = NOW()
        WHERE plc_id = NEW.plc_id;

        RETURN NEW;
    END IF;

    -- 동일 교대 내 값 갱신
    IF v_is_prod THEN
        UPDATE {schema}.production_shift_current SET
            prod_accumulated = v_new_val,
            shift_production = v_new_val - prod_run_start,
            -- 달성률 갱신
            achievement_rate = CASE
                WHEN target_qty > 0 THEN ROUND((v_new_val - prod_run_start)::NUMERIC / target_qty * 100, 2)
                ELSE 0 END,
            -- 직행률 갱신 (생산수량 변경 시 재계산): 생산 / (생산 + NG) × 100
            first_pass_yield = CASE
                WHEN ((v_new_val - prod_run_start) + shift_ng_qty) > 0
                THEN ROUND((v_new_val - prod_run_start)::NUMERIC
                           / ((v_new_val - prod_run_start) + shift_ng_qty) * 100, 2)
                ELSE 0 END,
            updated_at = NOW()
        WHERE plc_id = NEW.plc_id;
    ELSIF v_is_ng THEN
        UPDATE {schema}.production_shift_current SET
            ng_accumulated = v_new_val,
            shift_ng_qty   = v_new_val - ng_run_start,
            -- 직행률 갱신 (NG 변경 시 재계산): 생산 / (생산 + NG) × 100
            first_pass_yield = CASE
                WHEN (shift_production + (v_new_val - ng_run_start)) > 0
                THEN ROUND(shift_production::NUMERIC
                           / (shift_production + (v_new_val - ng_run_start)) * 100, 2)
                ELSE 0 END,
            updated_at = NOW()
        WHERE plc_id = NEW.plc_id;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- 6-3. 정지시간 추적 트리거
-- alm_latest 또는 action_latest의 v_bool 변경 시 실행
-- 동작:
--   1. v_bool FALSE→TRUE (알람/액션 발생): active_count 증가
--      - 0→1 전환 시 downtime_start 기록
--   2. v_bool TRUE→FALSE (알람/액션 해제): active_count 감소
--      - 1→0 전환 시 경과시간을 downtime_min에 누적
--   3. 합산 정지시간: alm_active + action_active 중 하나라도 > 0이면 정지 상태
--      - 전체 0→1 전환 시 total_downtime_start 기록
--      - 전체 1→0 전환 시 total_downtime_min에 누적
--   4. 가동률 재계산
--
-- 매개변수: TG_ARGV[0] = 'alm' 또는 'action' (트리거 등록 시 지정)
CREATE OR REPLACE FUNCTION {schema}.fn_downtime_tracker()
RETURNS TRIGGER AS $$
DECLARE
    v_source         TEXT := TG_ARGV[0];  -- 'alm' or 'action'
    v_cur            RECORD;
    v_old_bool       BOOLEAN;
    v_new_bool       BOOLEAN;
    v_was_any_active BOOLEAN;
    v_is_any_active  BOOLEAN;
    v_elapsed        NUMERIC;
BEGIN
    -- v_bool 변경 여부 확인
    v_old_bool := OLD.v_bool;
    v_new_bool := NEW.v_bool;

    IF v_old_bool IS NOT DISTINCT FROM v_new_bool THEN
        RETURN NEW;  -- v_bool 변경 없으면 스킵
    END IF;

    -- current 행 조회
    SELECT * INTO v_cur
    FROM {schema}.production_shift_current
    WHERE plc_id = NEW.plc_id;

    IF NOT FOUND THEN
        RETURN NEW;  -- 생산 추적이 시작되지 않은 PLC
    END IF;

    -- 이전 전체 활성 상태
    v_was_any_active := (v_cur.alm_active_count > 0 OR v_cur.action_active_count > 0);

    IF v_source = 'alm' THEN
        IF v_new_bool = TRUE AND (v_old_bool = FALSE OR v_old_bool IS NULL) THEN
            -- 알람 발생: active_count 증가
            v_cur.alm_active_count := v_cur.alm_active_count + 1;
            IF v_cur.alm_active_count = 1 THEN
                -- 0→1: 정지 시작
                v_cur.alm_downtime_start := NOW();
            END IF;
        ELSIF v_new_bool = FALSE AND v_old_bool = TRUE THEN
            -- 알람 해제: active_count 감소
            v_cur.alm_active_count := GREATEST(v_cur.alm_active_count - 1, 0);
            IF v_cur.alm_active_count = 0 AND v_cur.alm_downtime_start IS NOT NULL THEN
                -- 1→0: 경과시간 누적
                v_elapsed := EXTRACT(EPOCH FROM (NOW() - v_cur.alm_downtime_start)) / 60.0;
                v_cur.alm_downtime_min := v_cur.alm_downtime_min + v_elapsed;
                v_cur.alm_downtime_start := NULL;
            END IF;
        END IF;
    ELSIF v_source = 'action' THEN
        IF v_new_bool = TRUE AND (v_old_bool = FALSE OR v_old_bool IS NULL) THEN
            v_cur.action_active_count := v_cur.action_active_count + 1;
            IF v_cur.action_active_count = 1 THEN
                v_cur.action_downtime_start := NOW();
            END IF;
        ELSIF v_new_bool = FALSE AND v_old_bool = TRUE THEN
            v_cur.action_active_count := GREATEST(v_cur.action_active_count - 1, 0);
            IF v_cur.action_active_count = 0 AND v_cur.action_downtime_start IS NOT NULL THEN
                v_elapsed := EXTRACT(EPOCH FROM (NOW() - v_cur.action_downtime_start)) / 60.0;
                v_cur.action_downtime_min := v_cur.action_downtime_min + v_elapsed;
                v_cur.action_downtime_start := NULL;
            END IF;
        END IF;
    END IF;

    -- 합산 정지시간 (중복 제거)
    v_is_any_active := (v_cur.alm_active_count > 0 OR v_cur.action_active_count > 0);

    IF NOT v_was_any_active AND v_is_any_active THEN
        -- 전체 0→1: 정지 시작
        v_cur.total_downtime_start := NOW();
    ELSIF v_was_any_active AND NOT v_is_any_active THEN
        -- 전체 1→0: 경과시간 누적
        IF v_cur.total_downtime_start IS NOT NULL THEN
            v_elapsed := EXTRACT(EPOCH FROM (NOW() - v_cur.total_downtime_start)) / 60.0;
            v_cur.total_downtime_min := v_cur.total_downtime_min + v_elapsed;
            v_cur.total_downtime_start := NULL;
        END IF;
    END IF;

    -- 가동률 계산
    IF v_cur.target_time_min > 0 THEN
        v_cur.alm_operating_rate := ROUND(
            (v_cur.target_time_min - v_cur.alm_downtime_min) / v_cur.target_time_min * 100, 2);
        v_cur.action_operating_rate := ROUND(
            (v_cur.target_time_min - v_cur.action_downtime_min) / v_cur.target_time_min * 100, 2);
        v_cur.operating_rate := ROUND(
            (v_cur.target_time_min - v_cur.total_downtime_min) / v_cur.target_time_min * 100, 2);
    END IF;

    -- UPDATE 반영
    UPDATE {schema}.production_shift_current SET
        alm_active_count      = v_cur.alm_active_count,
        alm_downtime_start    = v_cur.alm_downtime_start,
        alm_downtime_min      = v_cur.alm_downtime_min,
        action_active_count   = v_cur.action_active_count,
        action_downtime_start = v_cur.action_downtime_start,
        action_downtime_min   = v_cur.action_downtime_min,
        total_downtime_start  = v_cur.total_downtime_start,
        total_downtime_min    = v_cur.total_downtime_min,
        alm_operating_rate    = v_cur.alm_operating_rate,
        action_operating_rate = v_cur.action_operating_rate,
        operating_rate        = v_cur.operating_rate,
        updated_at            = NOW()
    WHERE plc_id = NEW.plc_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- 6-4. 트리거 등록
-- plc_data_latest: 생산수량/NG수량 변경 감지
DROP TRIGGER IF EXISTS trg_production_shift ON {schema}.plc_data_latest;
CREATE TRIGGER trg_production_shift
    AFTER UPDATE ON {schema}.plc_data_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_production_shift_tracker();

-- alm_latest: 알람 v_bool 변경 → 정지시간 추적
DROP TRIGGER IF EXISTS trg_downtime_alm ON {schema}.alm_latest;
CREATE TRIGGER trg_downtime_alm
    AFTER UPDATE ON {schema}.alm_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_downtime_tracker('alm');

-- action_latest: 액션 v_bool 변경 → 정지시간 추적
DROP TRIGGER IF EXISTS trg_downtime_action ON {schema}.action_latest;
CREATE TRIGGER trg_downtime_action
    AFTER UPDATE ON {schema}.action_latest
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_downtime_tracker('action');


-- ============================================================================
-- 7. 통계 테이블 + 뷰 + 트리거
-- ============================================================================
-- 용도: 시간별/일별 생산 통계를 자동 집계하여 추세 분석 및 리포트 제공
--
-- 구조:
--   production_hourly   — 시간별 스냅샷 (트리거: production_shift_current UPDATE 시 정시 캡처)
--   production_daily    — 일별 집계 (트리거: production_shift_history INSERT 시 마지막 교대 종료 기준 집계)
--   v_production_weekly — 주간 통계 (뷰: production_daily 기반)
--   v_production_monthly — 월간 통계 (뷰: production_daily 기반)
--
-- 데이터 흐름:
--   plc_data_latest UPDATE → production_shift_current UPDATE (기존 트리거)
--                          → fn_hourly_snapshot() 정시 감지 → production_hourly INSERT
--   교대 전환 → production_shift_history INSERT (기존 트리거)
--             → fn_daily_aggregate() 마지막 교대 종료 감지 → production_daily UPSERT


-- 7-1. 시간별 생산 스냅샷
-- 정시(매 시각 00분)마다 현재 교대 실적의 스냅샷을 캡처
-- hour_* 컬럼: 해당 시간의 델타값 (이번 정시 - 이전 정시)
-- cumul_* 컬럼: 교대 시작부터의 누적값
CREATE TABLE IF NOT EXISTS {schema}.production_hourly (
    plc_id              INTEGER NOT NULL,
    snapshot_at         TIMESTAMPTZ NOT NULL,            -- 정시 기준 시각 (예: 09:00, 10:00)
    shift_type          VARCHAR(10),                     -- 'day' / 'night'

    -- 시간별 델타 (이번 시간 동안의 변화량)
    hour_production     NUMERIC(12,2) DEFAULT 0,         -- 시간별 생산수량
    hour_ng_qty         NUMERIC(12,2) DEFAULT 0,         -- 시간별 NG수량
    hour_downtime_min   NUMERIC(10,2) DEFAULT 0,         -- 시간별 정지시간 (분)

    -- 교대 시작부터 누적
    cumul_production    NUMERIC(12,2) DEFAULT 0,         -- 누적 생산수량
    cumul_ng_qty        NUMERIC(12,2) DEFAULT 0,         -- 누적 NG수량
    cumul_downtime_min  NUMERIC(10,2) DEFAULT 0,         -- 누적 정지시간 (분)

    -- 계산값
    achievement_rate    NUMERIC(6,2) DEFAULT 0,          -- 달성률(%)
    first_pass_yield    NUMERIC(6,2) DEFAULT 0,          -- 직행률(%)
    operating_rate      NUMERIC(6,2) DEFAULT 0,          -- 가동률(%)

    created_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 시간별 조회 인덱스 (PLC별 시간 역순)
CREATE INDEX IF NOT EXISTS idx_hourly_lookup
    ON {schema}.production_hourly (plc_id, snapshot_at DESC);

-- 동일 PLC/시간에 중복 INSERT 방지
CREATE UNIQUE INDEX IF NOT EXISTS idx_hourly_unique
    ON {schema}.production_hourly (plc_id, snapshot_at);


-- 7-2. 일별 생산 통계
-- 마지막 교대 종료 시 해당 일자의 전체 교대 실적을 합산하여 UPSERT
-- prod_date 기준: 첫 교대(shift_order=1)의 시작일
-- 교대별 상세는 production_shift_history에서 조회 가능
CREATE TABLE IF NOT EXISTS {schema}.production_daily (
    plc_id              INTEGER NOT NULL,
    prod_date           DATE NOT NULL,                   -- 생산일자 (KST 기준)

    -- 일별 합계 (전체 교대 합산)
    daily_production    NUMERIC(12,2) DEFAULT 0,         -- 일 생산수량
    daily_ng_qty        NUMERIC(12,2) DEFAULT 0,         -- 일 NG수량
    daily_target_qty    INTEGER DEFAULT 0,               -- 일 목표수량

    -- 정지시간 (분)
    daily_alm_downtime_min    NUMERIC(10,2) DEFAULT 0,   -- 알람 정지시간
    daily_action_downtime_min NUMERIC(10,2) DEFAULT 0,   -- 액션 정지시간
    daily_total_downtime_min  NUMERIC(10,2) DEFAULT 0,   -- 전체 정지시간
    daily_target_time_min     NUMERIC(10,2) DEFAULT 0,   -- 일 목표시간 (분)

    -- 계산값
    achievement_rate    NUMERIC(6,2) DEFAULT 0,          -- 달성률(%)
    first_pass_yield    NUMERIC(6,2) DEFAULT 0,          -- 직행률(%)
    alm_operating_rate  NUMERIC(6,2) DEFAULT 0,          -- 알람 가동률(%)
    action_operating_rate NUMERIC(6,2) DEFAULT 0,        -- 액션 가동률(%)
    operating_rate      NUMERIC(6,2) DEFAULT 0,          -- 전체 가동률(%)

    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

-- 일별 조회 인덱스 (PLC별 날짜 역순)
CREATE INDEX IF NOT EXISTS idx_daily_lookup
    ON {schema}.production_daily (plc_id, prod_date DESC);

-- 동일 PLC/날짜에 중복 방지 (UPSERT용)
CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_unique
    ON {schema}.production_daily (plc_id, prod_date);


-- 7-3. 주간 통계 뷰 (전체 합산)
-- production_daily를 주 단위로 집계
-- 주 시작일: 월요일 (ISO week)
CREATE OR REPLACE VIEW {schema}.v_production_weekly AS
SELECT
    plc_id,
    DATE_TRUNC('week', prod_date)::DATE  AS week_start,
    SUM(daily_production)                AS weekly_production,
    SUM(daily_ng_qty)                    AS weekly_ng_qty,
    SUM(daily_target_qty)                AS weekly_target_qty,
    SUM(daily_alm_downtime_min)          AS weekly_alm_downtime_min,
    SUM(daily_action_downtime_min)       AS weekly_action_downtime_min,
    SUM(daily_total_downtime_min)        AS weekly_total_downtime_min,
    SUM(daily_target_time_min)           AS weekly_target_time_min,
    -- 계산값 (합산 기준 재계산)
    CASE WHEN SUM(daily_target_qty) > 0
        THEN ROUND(SUM(daily_production) / SUM(daily_target_qty) * 100, 2)
        ELSE 0 END                       AS achievement_rate,
    CASE WHEN (SUM(daily_production) + SUM(daily_ng_qty)) > 0
        THEN ROUND(SUM(daily_production) / (SUM(daily_production) + SUM(daily_ng_qty)) * 100, 2)
        ELSE 0 END                       AS first_pass_yield,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_alm_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS alm_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_action_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS action_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_total_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS operating_rate,
    COUNT(*)                             AS working_days
FROM {schema}.production_daily
GROUP BY plc_id, DATE_TRUNC('week', prod_date)
ORDER BY plc_id, week_start DESC;


-- 7-4. 월간 통계 뷰 (전체 합산)
-- production_daily를 월 단위로 집계
CREATE OR REPLACE VIEW {schema}.v_production_monthly AS
SELECT
    plc_id,
    DATE_TRUNC('month', prod_date)::DATE AS month_start,
    SUM(daily_production)                AS monthly_production,
    SUM(daily_ng_qty)                    AS monthly_ng_qty,
    SUM(daily_target_qty)                AS monthly_target_qty,
    SUM(daily_alm_downtime_min)          AS monthly_alm_downtime_min,
    SUM(daily_action_downtime_min)       AS monthly_action_downtime_min,
    SUM(daily_total_downtime_min)        AS monthly_total_downtime_min,
    SUM(daily_target_time_min)           AS monthly_target_time_min,
    -- 계산값
    CASE WHEN SUM(daily_target_qty) > 0
        THEN ROUND(SUM(daily_production) / SUM(daily_target_qty) * 100, 2)
        ELSE 0 END                       AS achievement_rate,
    CASE WHEN (SUM(daily_production) + SUM(daily_ng_qty)) > 0
        THEN ROUND(SUM(daily_production) / (SUM(daily_production) + SUM(daily_ng_qty)) * 100, 2)
        ELSE 0 END                       AS first_pass_yield,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_alm_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS alm_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_action_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS action_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_total_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS operating_rate,
    COUNT(*)                             AS working_days
FROM {schema}.production_daily
GROUP BY plc_id, DATE_TRUNC('month', prod_date)
ORDER BY plc_id, month_start DESC;


-- 7-5. 연간 통계 뷰 (전체 합산)
-- production_daily를 년 단위로 집계
CREATE OR REPLACE VIEW {schema}.v_production_yearly AS
SELECT
    plc_id,
    DATE_TRUNC('year', prod_date)::DATE  AS year_start,
    SUM(daily_production)                AS yearly_production,
    SUM(daily_ng_qty)                    AS yearly_ng_qty,
    SUM(daily_target_qty)                AS yearly_target_qty,
    SUM(daily_alm_downtime_min)          AS yearly_alm_downtime_min,
    SUM(daily_action_downtime_min)       AS yearly_action_downtime_min,
    SUM(daily_total_downtime_min)        AS yearly_total_downtime_min,
    SUM(daily_target_time_min)           AS yearly_target_time_min,
    -- 계산값
    CASE WHEN SUM(daily_target_qty) > 0
        THEN ROUND(SUM(daily_production) / SUM(daily_target_qty) * 100, 2)
        ELSE 0 END                       AS achievement_rate,
    CASE WHEN (SUM(daily_production) + SUM(daily_ng_qty)) > 0
        THEN ROUND(SUM(daily_production) / (SUM(daily_production) + SUM(daily_ng_qty)) * 100, 2)
        ELSE 0 END                       AS first_pass_yield,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_alm_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS alm_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_action_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS action_operating_rate,
    CASE WHEN SUM(daily_target_time_min) > 0
        THEN ROUND((SUM(daily_target_time_min) - SUM(daily_total_downtime_min)) / SUM(daily_target_time_min) * 100, 2)
        ELSE 0 END                       AS operating_rate,
    COUNT(*)                             AS working_days
FROM {schema}.production_daily
GROUP BY plc_id, DATE_TRUNC('year', prod_date)
ORDER BY plc_id, year_start DESC;


-- 7-6. 교대별 주간 통계 뷰
-- production_shift_history를 주 단위 + 교대별로 집계
-- 2교대→3교대 변경 시 같은 주에 day/night + morning/afternoon/night 공존 가능
CREATE OR REPLACE VIEW {schema}.v_production_shift_weekly AS
SELECT
    h.plc_id,
    DATE_TRUNC('week', (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE)::DATE AS week_start,
    h.shift_type,
    SUM(h.shift_production)              AS weekly_production,
    SUM(h.shift_ng_qty)                  AS weekly_ng_qty,
    SUM(h.target_qty)                    AS weekly_target_qty,
    SUM(h.alm_downtime_min)              AS weekly_alm_downtime_min,
    SUM(h.action_downtime_min)           AS weekly_action_downtime_min,
    SUM(h.total_downtime_min)            AS weekly_total_downtime_min,
    SUM(h.target_time_min)               AS weekly_target_time_min,
    -- 계산값
    CASE WHEN SUM(h.target_qty) > 0
        THEN ROUND(SUM(h.shift_production) / SUM(h.target_qty) * 100, 2)
        ELSE 0 END                       AS achievement_rate,
    CASE WHEN SUM(h.shift_production) > 0
        THEN ROUND((SUM(h.shift_production) - SUM(h.shift_ng_qty)) / SUM(h.shift_production) * 100, 2)
        ELSE 0 END                       AS first_pass_yield,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.alm_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS alm_operating_rate,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.action_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS action_operating_rate,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.total_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS operating_rate,
    COUNT(*)                             AS shift_count
FROM {schema}.production_shift_history h
GROUP BY h.plc_id, DATE_TRUNC('week', (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE), h.shift_type
ORDER BY h.plc_id, week_start DESC, h.shift_type;


-- 7-7. 교대별 월간 통계 뷰
-- production_shift_history를 월 단위 + 교대별로 집계
CREATE OR REPLACE VIEW {schema}.v_production_shift_monthly AS
SELECT
    h.plc_id,
    DATE_TRUNC('month', (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE)::DATE AS month_start,
    h.shift_type,
    SUM(h.shift_production)              AS monthly_production,
    SUM(h.shift_ng_qty)                  AS monthly_ng_qty,
    SUM(h.target_qty)                    AS monthly_target_qty,
    SUM(h.alm_downtime_min)              AS monthly_alm_downtime_min,
    SUM(h.action_downtime_min)           AS monthly_action_downtime_min,
    SUM(h.total_downtime_min)            AS monthly_total_downtime_min,
    SUM(h.target_time_min)               AS monthly_target_time_min,
    -- 계산값
    CASE WHEN SUM(h.target_qty) > 0
        THEN ROUND(SUM(h.shift_production) / SUM(h.target_qty) * 100, 2)
        ELSE 0 END                       AS achievement_rate,
    CASE WHEN SUM(h.shift_production) > 0
        THEN ROUND((SUM(h.shift_production) - SUM(h.shift_ng_qty)) / SUM(h.shift_production) * 100, 2)
        ELSE 0 END                       AS first_pass_yield,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.alm_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS alm_operating_rate,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.action_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS action_operating_rate,
    CASE WHEN SUM(h.target_time_min) > 0
        THEN ROUND((SUM(h.target_time_min) - SUM(h.total_downtime_min)) / SUM(h.target_time_min) * 100, 2)
        ELSE 0 END                       AS operating_rate,
    COUNT(*)                             AS shift_count
FROM {schema}.production_shift_history h
GROUP BY h.plc_id, DATE_TRUNC('month', (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE), h.shift_type
ORDER BY h.plc_id, month_start DESC, h.shift_type;


-- ============================================================================
-- 8. 알람 통계 뷰
-- ============================================================================
-- 원본: alm_history (publisher extensions.history 자동 생성)
-- alm_latest의 v_bool 변경 시 자동 INSERT
--   v_bool=TRUE  → 알람 발생
--   v_bool=FALSE → 알람 해제
-- TRUE/FALSE 페어를 매칭하여 알람별 정지 구간 및 순위를 산출

-- 8-1. 알람 정지 구간 뷰
-- alm_history의 TRUE(발생) → FALSE(해제)를 페어링하여 1건=1행
-- shift_type은 production_shift_history의 shift_start~shift_end 구간 매칭
CREATE OR REPLACE VIEW {schema}.v_alarm_downtime AS
SELECT
    a.plc_id,
    a.tag_id,
    m.tag_name,
    m.description                        AS tag_desc,
    a.alarm_start,
    a.alarm_end,
    CASE
        WHEN a.alarm_end IS NOT NULL
        THEN ROUND(EXTRACT(EPOCH FROM (a.alarm_end - a.alarm_start)) / 60.0, 2)
        ELSE NULL
    END                                  AS duration_min,
    sh.shift_type
FROM (
    -- TRUE(발생) 행에 다음 FALSE(해제) 시각을 LEAD로 매칭
    SELECT
        plc_id,
        tag_id,
        "timestamp"                      AS alarm_start,
        LEAD("timestamp") OVER (
            PARTITION BY plc_id, tag_id
            ORDER BY "timestamp"
        )                                AS alarm_end,
        v_bool
    FROM {schema}.alm_history
) a
LEFT JOIN {schema}.alm_master m
    ON m.plc_id = a.plc_id AND m.tag_id = a.tag_id
LEFT JOIN {schema}.production_shift_history sh
    ON sh.plc_id = a.plc_id
    AND a.alarm_start >= sh.shift_start
    AND a.alarm_start < sh.shift_end
WHERE a.v_bool = TRUE;


-- 8-2. 일별 알람 순위 뷰
-- 교대별 + 태그별 발생횟수/총정지시간/평균정지시간 + 순위
CREATE OR REPLACE VIEW {schema}.v_alarm_ranking_daily AS
SELECT
    (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE AS prod_date,
    ad.plc_id,
    ad.shift_type,
    ad.tag_id,
    ad.tag_name,
    ad.tag_desc,
    COUNT(*)                             AS occurrence_count,
    ROUND(COALESCE(SUM(ad.duration_min), 0), 2)  AS total_duration_min,
    ROUND(COALESCE(AVG(ad.duration_min), 0), 2)  AS avg_duration_min,
    RANK() OVER (
        PARTITION BY (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE, ad.plc_id, ad.shift_type
        ORDER BY COALESCE(SUM(ad.duration_min), 0) DESC
    )                                    AS rank_by_duration,
    RANK() OVER (
        PARTITION BY (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE, ad.plc_id, ad.shift_type
        ORDER BY COUNT(*) DESC
    )                                    AS rank_by_count
FROM {schema}.v_alarm_downtime ad
WHERE ad.duration_min IS NOT NULL
GROUP BY (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE,
         ad.plc_id, ad.shift_type, ad.tag_id, ad.tag_name, ad.tag_desc;


-- 8-3. 주별 알람 순위 뷰
CREATE OR REPLACE VIEW {schema}.v_alarm_ranking_weekly AS
SELECT
    DATE_TRUNC('week', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE)::DATE AS week_start,
    ad.plc_id,
    ad.shift_type,
    ad.tag_id,
    ad.tag_name,
    ad.tag_desc,
    COUNT(*)                             AS occurrence_count,
    ROUND(COALESCE(SUM(ad.duration_min), 0), 2)  AS total_duration_min,
    ROUND(COALESCE(AVG(ad.duration_min), 0), 2)  AS avg_duration_min,
    RANK() OVER (
        PARTITION BY DATE_TRUNC('week', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE), ad.plc_id, ad.shift_type
        ORDER BY COALESCE(SUM(ad.duration_min), 0) DESC
    )                                    AS rank_by_duration,
    RANK() OVER (
        PARTITION BY DATE_TRUNC('week', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE), ad.plc_id, ad.shift_type
        ORDER BY COUNT(*) DESC
    )                                    AS rank_by_count
FROM {schema}.v_alarm_downtime ad
WHERE ad.duration_min IS NOT NULL
GROUP BY DATE_TRUNC('week', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE),
         ad.plc_id, ad.shift_type, ad.tag_id, ad.tag_name, ad.tag_desc;


-- 8-4. 월별 알람 순위 뷰
CREATE OR REPLACE VIEW {schema}.v_alarm_ranking_monthly AS
SELECT
    DATE_TRUNC('month', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE)::DATE AS month_start,
    ad.plc_id,
    ad.shift_type,
    ad.tag_id,
    ad.tag_name,
    ad.tag_desc,
    COUNT(*)                             AS occurrence_count,
    ROUND(COALESCE(SUM(ad.duration_min), 0), 2)  AS total_duration_min,
    ROUND(COALESCE(AVG(ad.duration_min), 0), 2)  AS avg_duration_min,
    RANK() OVER (
        PARTITION BY DATE_TRUNC('month', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE), ad.plc_id, ad.shift_type
        ORDER BY COALESCE(SUM(ad.duration_min), 0) DESC
    )                                    AS rank_by_duration,
    RANK() OVER (
        PARTITION BY DATE_TRUNC('month', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE), ad.plc_id, ad.shift_type
        ORDER BY COUNT(*) DESC
    )                                    AS rank_by_count
FROM {schema}.v_alarm_downtime ad
WHERE ad.duration_min IS NOT NULL
GROUP BY DATE_TRUNC('month', (ad.alarm_start AT TIME ZONE 'Asia/Seoul')::DATE),
         ad.plc_id, ad.shift_type, ad.tag_id, ad.tag_name, ad.tag_desc;


-- 8-5. 트리거 함수: 시간별 스냅샷
-- production_shift_current가 UPDATE될 때마다 호출
-- 현재 시각의 정시(hour)가 마지막 스냅샷과 다르면 새 스냅샷 INSERT
CREATE OR REPLACE FUNCTION {schema}.fn_hourly_snapshot()
RETURNS TRIGGER AS $$
DECLARE
    v_now           TIMESTAMPTZ := NOW();
    v_current_hour  TIMESTAMPTZ;
    v_last_snapshot RECORD;
    v_total_down    NUMERIC(10,2);
    v_pending       NUMERIC(10,2);
BEGIN
    -- 교대 정보 없으면 스킵
    IF NEW.shift_type IS NULL OR NEW.shift_start IS NULL THEN
        RETURN NEW;
    END IF;

    -- 현재 정시 계산 (분/초 버림)
    v_current_hour := DATE_TRUNC('hour', v_now);

    -- 교대 시작 전이면 스킵
    IF v_current_hour < DATE_TRUNC('hour', NEW.shift_start) THEN
        RETURN NEW;
    END IF;

    -- 이미 이 시간에 스냅샷 있으면 스킵
    PERFORM 1 FROM {schema}.production_hourly
    WHERE plc_id = NEW.plc_id AND snapshot_at = v_current_hour;
    IF FOUND THEN
        RETURN NEW;
    END IF;

    -- 현재 진행중인 정지시간 반영 (아직 종료 안된 구간)
    v_pending := 0;
    IF NEW.total_downtime_start IS NOT NULL THEN
        v_pending := EXTRACT(EPOCH FROM (v_now - NEW.total_downtime_start)) / 60.0;
    END IF;
    v_total_down := NEW.total_downtime_min + v_pending;

    -- 이전 스냅샷 조회 (같은 교대 내)
    SELECT cumul_production, cumul_ng_qty, cumul_downtime_min
    INTO v_last_snapshot
    FROM {schema}.production_hourly
    WHERE plc_id = NEW.plc_id
      AND snapshot_at >= DATE_TRUNC('hour', NEW.shift_start)
    ORDER BY snapshot_at DESC
    LIMIT 1;

    -- INSERT 스냅샷
    INSERT INTO {schema}.production_hourly (
        plc_id, snapshot_at, shift_type,
        hour_production, hour_ng_qty, hour_downtime_min,
        cumul_production, cumul_ng_qty, cumul_downtime_min,
        achievement_rate, first_pass_yield, operating_rate
    ) VALUES (
        NEW.plc_id,
        v_current_hour,
        NEW.shift_type,
        -- 시간별 델타 (이전 스냅샷 없으면 교대 시작부터 누적 = 현재값)
        NEW.shift_production  - COALESCE(v_last_snapshot.cumul_production, 0),
        NEW.shift_ng_qty      - COALESCE(v_last_snapshot.cumul_ng_qty, 0),
        v_total_down          - COALESCE(v_last_snapshot.cumul_downtime_min, 0),
        -- 교대 시작부터 누적
        NEW.shift_production,
        NEW.shift_ng_qty,
        v_total_down,
        -- 계산값
        NEW.achievement_rate,
        NEW.first_pass_yield,
        NEW.operating_rate
    );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- 8-6. 트리거 함수: 일별 집계
-- production_shift_history에 INSERT 시 호출
-- 마지막 교대(shift_order 최대) 종료 시 해당 일자의 전체 교대 실적을 합산하여 production_daily에 UPSERT
-- 생산일자(prod_date) = 첫 교대(shift_order=1)의 시작일 기준
CREATE OR REPLACE FUNCTION {schema}.fn_daily_aggregate()
RETURNS TRIGGER AS $$
DECLARE
    v_max_order     INTEGER;
    v_cur_order     INTEGER;
    v_first_start   TIME;
    v_prod_date     DATE;
    v_agg           RECORD;
    v_daily_target  INTEGER;
BEGIN
    -- 현재 INSERT된 교대의 shift_order 조회
    SELECT sc.shift_order INTO v_cur_order
    FROM {schema}.shift_config sc
    WHERE sc.shift_type = NEW.shift_type;

    -- 마지막 교대(최대 shift_order) 조회
    SELECT MAX(sc.shift_order) INTO v_max_order
    FROM {schema}.shift_config sc;

    -- 마지막 교대가 아니면 스킵
    IF v_cur_order IS NULL OR v_cur_order != v_max_order THEN
        RETURN NEW;
    END IF;

    -- 첫 교대(shift_order=1)의 start_time으로 생산일자 결정
    SELECT sc.start_time INTO v_first_start
    FROM {schema}.shift_config sc
    WHERE sc.shift_order = 1;

    -- 생산일자 = 첫 교대 시작일
    -- 예: 3교대(06-14-22-06) → 첫 교대 06:00 기준
    -- 예: 2교대(08:30-20:30-08:30) → 첫 교대 08:30 기준
    v_prod_date := (NEW.shift_start AT TIME ZONE 'Asia/Seoul')::DATE;
    -- 마지막 교대가 자정을 넘으면 shift_start 날짜가 전날일 수 있으므로
    -- 첫 교대 시작 시간 기준으로 보정
    IF v_first_start IS NOT NULL THEN
        -- 마지막 교대의 shift_start 시각이 첫 교대 시작 이전이면 전날 소속
        IF (NEW.shift_start AT TIME ZONE 'Asia/Seoul')::TIME < v_first_start THEN
            v_prod_date := v_prod_date - 1;
        END IF;
    END IF;

    -- 해당 일자의 전체 교대 실적 합산 (production_shift_history에서)
    -- 현재 INSERT된 레코드 포함 (AFTER INSERT 트리거이므로 이미 테이블에 존재)
    SELECT
        COALESCE(SUM(shift_production), 0)    AS production,
        COALESCE(SUM(shift_ng_qty), 0)        AS ng_qty,
        COALESCE(SUM(target_qty), 0)          AS target_qty,
        COALESCE(SUM(alm_downtime_min), 0)    AS alm_down,
        COALESCE(SUM(action_downtime_min), 0) AS action_down,
        COALESCE(SUM(total_downtime_min), 0)  AS total_down,
        COALESCE(SUM(target_time_min), 0)     AS target_time
    INTO v_agg
    FROM {schema}.production_shift_history h
    WHERE h.plc_id = NEW.plc_id
      AND h.shift_start >= (v_prod_date || ' ' || v_first_start)::TIMESTAMP AT TIME ZONE 'Asia/Seoul'
      AND h.shift_start < ((v_prod_date + 1) || ' ' || v_first_start)::TIMESTAMP AT TIME ZONE 'Asia/Seoul';

    -- daily 목표수량 (production_target에 'daily' 타입이 있으면 사용, 없으면 교대 합산)
    SELECT pt.target_qty INTO v_daily_target
    FROM {schema}.production_target pt
    JOIN {schema}.plc_master pm ON pm.line_id = pt.line_id
    WHERE pm.plc_id = NEW.plc_id AND pt.target_type = 'daily';
    v_daily_target := COALESCE(v_daily_target, v_agg.target_qty);

    -- UPSERT
    INSERT INTO {schema}.production_daily (
        plc_id, prod_date,
        daily_production, daily_ng_qty, daily_target_qty,
        daily_alm_downtime_min, daily_action_downtime_min, daily_total_downtime_min,
        daily_target_time_min,
        achievement_rate, first_pass_yield,
        alm_operating_rate, action_operating_rate, operating_rate,
        updated_at
    ) VALUES (
        NEW.plc_id,
        v_prod_date,
        v_agg.production,
        v_agg.ng_qty,
        v_daily_target,
        v_agg.alm_down,
        v_agg.action_down,
        v_agg.total_down,
        v_agg.target_time,
        -- 달성률
        CASE WHEN v_daily_target > 0
            THEN ROUND(v_agg.production / v_daily_target * 100, 2)
            ELSE 0 END,
        -- 직행률: 생산 / (생산 + NG) × 100
        CASE WHEN (v_agg.production + v_agg.ng_qty) > 0
            THEN ROUND(v_agg.production / (v_agg.production + v_agg.ng_qty) * 100, 2)
            ELSE 0 END,
        -- 알람 가동률
        CASE WHEN v_agg.target_time > 0
            THEN ROUND((v_agg.target_time - v_agg.alm_down) / v_agg.target_time * 100, 2)
            ELSE 0 END,
        -- 액션 가동률
        CASE WHEN v_agg.target_time > 0
            THEN ROUND((v_agg.target_time - v_agg.action_down) / v_agg.target_time * 100, 2)
            ELSE 0 END,
        -- 전체 가동률
        CASE WHEN v_agg.target_time > 0
            THEN ROUND((v_agg.target_time - v_agg.total_down) / v_agg.target_time * 100, 2)
            ELSE 0 END,
        NOW()
    )
    ON CONFLICT (plc_id, prod_date) DO UPDATE SET
        daily_production          = EXCLUDED.daily_production,
        daily_ng_qty              = EXCLUDED.daily_ng_qty,
        daily_target_qty          = EXCLUDED.daily_target_qty,
        daily_alm_downtime_min    = EXCLUDED.daily_alm_downtime_min,
        daily_action_downtime_min = EXCLUDED.daily_action_downtime_min,
        daily_total_downtime_min  = EXCLUDED.daily_total_downtime_min,
        daily_target_time_min     = EXCLUDED.daily_target_time_min,
        achievement_rate          = EXCLUDED.achievement_rate,
        first_pass_yield          = EXCLUDED.first_pass_yield,
        alm_operating_rate        = EXCLUDED.alm_operating_rate,
        action_operating_rate     = EXCLUDED.action_operating_rate,
        operating_rate            = EXCLUDED.operating_rate,
        updated_at                = NOW();

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- 8-7. 트리거 등록

-- production_shift_current UPDATE → 시간별 스냅샷
DROP TRIGGER IF EXISTS trg_hourly_snapshot ON {schema}.production_shift_current;
CREATE TRIGGER trg_hourly_snapshot
    AFTER UPDATE ON {schema}.production_shift_current
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_hourly_snapshot();

-- production_shift_history INSERT → 일별 집계
DROP TRIGGER IF EXISTS trg_daily_aggregate ON {schema}.production_shift_history;
CREATE TRIGGER trg_daily_aggregate
    AFTER INSERT ON {schema}.production_shift_history
    FOR EACH ROW
    EXECUTE FUNCTION {schema}.fn_daily_aggregate();


-- ============================================================================
-- 9. Hypertable 변환 + 압축/보관 정책 (publisher와 동일 설정)
-- ============================================================================
-- production_shift_history, production_hourly, production_daily를 hypertable로 변환
-- 압축: 1일 후, 보관: 3년 (publisher YAML의 retention_period/compression_after와 동일)
-- 기존 테이블에 id SERIAL PK가 있으면 제거 후 변환 (마이그레이션 대응)

-- 9-1. production_shift_history → hypertable
DO $$
BEGIN
    -- 기존 id 컬럼 제거 (마이그레이션)
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '{schema}'
        AND table_name = 'production_shift_history'
        AND column_name = 'id'
    ) THEN
        ALTER TABLE {schema}.production_shift_history DROP COLUMN id;
    END IF;

    -- Hypertable 변환
    PERFORM create_hypertable(
        '{schema}.production_shift_history', 'created_at',
        if_not_exists => TRUE,
        migrate_data => TRUE
    );

    -- 압축 정책
    ALTER TABLE {schema}.production_shift_history SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'plc_id',
        timescaledb.compress_orderby = 'created_at DESC'
    );
    PERFORM add_compression_policy(
        '{schema}.production_shift_history',
        INTERVAL '1 day',
        if_not_exists => TRUE
    );

    -- 보관 정책 (3년)
    PERFORM add_retention_policy(
        '{schema}.production_shift_history',
        INTERVAL '3 years',
        if_not_exists => TRUE
    );

    PERFORM {schema}.fn_log_init('hypertable_shift_history', 'success');
EXCEPTION WHEN OTHERS THEN
    PERFORM {schema}.fn_log_init('hypertable_shift_history', 'error', SQLERRM);
END $$;

-- 9-2. production_hourly → hypertable
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '{schema}'
        AND table_name = 'production_hourly'
        AND column_name = 'id'
    ) THEN
        ALTER TABLE {schema}.production_hourly DROP COLUMN id;
    END IF;

    PERFORM create_hypertable(
        '{schema}.production_hourly', 'snapshot_at',
        if_not_exists => TRUE,
        migrate_data => TRUE
    );

    ALTER TABLE {schema}.production_hourly SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'plc_id',
        timescaledb.compress_orderby = 'snapshot_at DESC'
    );
    PERFORM add_compression_policy(
        '{schema}.production_hourly',
        INTERVAL '1 day',
        if_not_exists => TRUE
    );

    PERFORM add_retention_policy(
        '{schema}.production_hourly',
        INTERVAL '3 years',
        if_not_exists => TRUE
    );

    PERFORM {schema}.fn_log_init('hypertable_hourly', 'success');
EXCEPTION WHEN OTHERS THEN
    PERFORM {schema}.fn_log_init('hypertable_hourly', 'error', SQLERRM);
END $$;

-- 9-3. production_daily → hypertable
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = '{schema}'
        AND table_name = 'production_daily'
        AND column_name = 'id'
    ) THEN
        ALTER TABLE {schema}.production_daily DROP COLUMN id;
    END IF;

    PERFORM create_hypertable(
        '{schema}.production_daily', 'prod_date',
        if_not_exists => TRUE,
        migrate_data => TRUE
    );

    ALTER TABLE {schema}.production_daily SET (
        timescaledb.compress,
        timescaledb.compress_segmentby = 'plc_id',
        timescaledb.compress_orderby = 'prod_date DESC'
    );
    PERFORM add_compression_policy(
        '{schema}.production_daily',
        INTERVAL '1 day',
        if_not_exists => TRUE
    );

    PERFORM add_retention_policy(
        '{schema}.production_daily',
        INTERVAL '3 years',
        if_not_exists => TRUE
    );

    PERFORM {schema}.fn_log_init('hypertable_daily', 'success');
EXCEPTION WHEN OTHERS THEN
    PERFORM {schema}.fn_log_init('hypertable_daily', 'error', SQLERRM);
END $$;


-- ============================================================================
-- 10. 백필 함수 (재시작 시 누락 통계 복구)
-- ============================================================================
-- 용도: publisher 재시작 후 production_shift_history는 있지만 production_daily가
--       누락된 경우, history에서 일별 통계를 재집계하여 UPSERT
-- 동작:
--   1. production_daily에서 PLC별 최신 prod_date 조회
--   2. 그 이후의 production_shift_history 데이터를 일별로 집계
--   3. production_daily에 UPSERT (없으면 INSERT, 있으면 UPDATE)
--   4. 테이블이 비어있으면 전체 history에서 재집계
--   5. 결과를 custom_init_log에 기록

CREATE OR REPLACE FUNCTION {schema}.fn_backfill_daily_statistics()
RETURNS INTEGER AS $$
DECLARE
    v_first_start   TIME;
    v_count         INTEGER;
BEGIN
    -- 첫 교대 시작 시간 (prod_date 계산용)
    SELECT sc.start_time INTO v_first_start
    FROM {schema}.shift_config sc
    WHERE sc.shift_order = 1;
    v_first_start := COALESCE(v_first_start, '08:00'::TIME);

    -- production_shift_history → production_daily 재집계
    WITH shift_dates AS (
        SELECT
            h.plc_id,
            CASE
                WHEN (h.shift_start AT TIME ZONE 'Asia/Seoul')::TIME < v_first_start
                THEN (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE - 1
                ELSE (h.shift_start AT TIME ZONE 'Asia/Seoul')::DATE
            END AS prod_date,
            h.shift_production,
            h.shift_ng_qty,
            h.target_qty,
            h.alm_downtime_min,
            h.action_downtime_min,
            h.total_downtime_min,
            h.target_time_min
        FROM {schema}.production_shift_history h
    ),
    daily_agg AS (
        SELECT
            plc_id,
            prod_date,
            SUM(shift_production)    AS production,
            SUM(shift_ng_qty)        AS ng_qty,
            SUM(target_qty)          AS target_qty,
            SUM(alm_downtime_min)    AS alm_down,
            SUM(action_downtime_min) AS action_down,
            SUM(total_downtime_min)  AS total_down,
            SUM(target_time_min)     AS target_time
        FROM shift_dates
        GROUP BY plc_id, prod_date
    )
    INSERT INTO {schema}.production_daily (
        plc_id, prod_date,
        daily_production, daily_ng_qty, daily_target_qty,
        daily_alm_downtime_min, daily_action_downtime_min, daily_total_downtime_min,
        daily_target_time_min,
        achievement_rate, first_pass_yield,
        alm_operating_rate, action_operating_rate, operating_rate,
        updated_at
    )
    SELECT
        d.plc_id, d.prod_date,
        d.production, d.ng_qty,
        COALESCE(pt.target_qty, d.target_qty),
        d.alm_down, d.action_down, d.total_down,
        d.target_time,
        -- 달성률
        CASE WHEN COALESCE(pt.target_qty, d.target_qty) > 0
            THEN ROUND(d.production / COALESCE(pt.target_qty, d.target_qty) * 100, 2)
            ELSE 0 END,
        -- 직행률: 생산 / (생산 + NG) × 100
        CASE WHEN (d.production + d.ng_qty) > 0
            THEN ROUND(d.production / (d.production + d.ng_qty) * 100, 2)
            ELSE 0 END,
        -- 알람 가동률
        CASE WHEN d.target_time > 0
            THEN ROUND((d.target_time - d.alm_down) / d.target_time * 100, 2)
            ELSE 0 END,
        -- 액션 가동률
        CASE WHEN d.target_time > 0
            THEN ROUND((d.target_time - d.action_down) / d.target_time * 100, 2)
            ELSE 0 END,
        -- 전체 가동률
        CASE WHEN d.target_time > 0
            THEN ROUND((d.target_time - d.total_down) / d.target_time * 100, 2)
            ELSE 0 END,
        NOW()
    FROM daily_agg d
    LEFT JOIN {schema}.plc_master pm ON pm.plc_id = d.plc_id
    LEFT JOIN {schema}.production_target pt
        ON pt.line_id = pm.line_id AND pt.target_type = 'daily'
    ON CONFLICT (plc_id, prod_date) DO UPDATE SET
        daily_production          = EXCLUDED.daily_production,
        daily_ng_qty              = EXCLUDED.daily_ng_qty,
        daily_target_qty          = EXCLUDED.daily_target_qty,
        daily_alm_downtime_min    = EXCLUDED.daily_alm_downtime_min,
        daily_action_downtime_min = EXCLUDED.daily_action_downtime_min,
        daily_total_downtime_min  = EXCLUDED.daily_total_downtime_min,
        daily_target_time_min     = EXCLUDED.daily_target_time_min,
        achievement_rate          = EXCLUDED.achievement_rate,
        first_pass_yield          = EXCLUDED.first_pass_yield,
        alm_operating_rate        = EXCLUDED.alm_operating_rate,
        action_operating_rate     = EXCLUDED.action_operating_rate,
        operating_rate            = EXCLUDED.operating_rate,
        updated_at                = NOW();

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$ LANGUAGE plpgsql;

-- 10-1. 백필 실행 (custom_init.sql 실행 시 자동)
DO $$
DECLARE
    v_result INTEGER;
BEGIN
    SELECT {schema}.fn_backfill_daily_statistics() INTO v_result;
    PERFORM {schema}.fn_log_init('backfill_daily', 'success',
        format('UPSERT %s rows into production_daily', v_result));
EXCEPTION WHEN OTHERS THEN
    PERFORM {schema}.fn_log_init('backfill_daily', 'error', SQLERRM);
END $$;


-- ============================================================================
-- 11. 권한 부여 (모든 테이블/뷰에 대해)
-- ============================================================================
GRANT USAGE ON SCHEMA {schema} TO api_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA {schema} TO api_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA {schema} GRANT SELECT ON TABLES TO api_reader;
