# Database Publisher

TimescaleDB/PostgreSQL로 데이터를 저장하는 방법을 안내합니다.

---

## 개요

**Database Publisher**는 수집된 데이터를 TimescaleDB에 저장합니다.
시계열 데이터에 최적화된 구조로 장기 저장 및 분석에 적합합니다.

### 특징

- 배치 INSERT로 고성능 저장
- TimescaleDB 하이퍼테이블 지원
- 마스터 테이블 자동 동기화
- 연결 풀링

---

## 설정

### 기본 설정

```yaml title="config/collector.yaml"
publisher:
  database:
    enabled: true
    host: "localhost"
    port: 5432
    database: "collector"
    user: "collector"
    password: "${DB_PASSWORD}"
```

### 전체 옵션

```yaml
publisher:
  database:
    enabled: true                  # 활성화 여부
    host: "localhost"              # DB 호스트
    port: 5432                     # DB 포트
    database: "collector"          # 데이터베이스 이름
    user: "collector"              # 사용자
    password: "${DB_PASSWORD}"     # 비밀번호
    schema: "test"                 # 스키마 (기본: public)
    table: "plc_data"              # 테이블 이름
    pool_size: 5                   # 커넥션 풀 크기
    pool_timeout: 30               # 커넥션 타임아웃 (초)
    master_sync_enabled: true      # 마스터 동기화 활성화
    master_sync_schema: "test"     # 마스터 테이블 스키마
```

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | Publisher 활성화 |
| `host` | string | localhost | DB 호스트 |
| `port` | int | 5432 | DB 포트 |
| `database` | string | - | 데이터베이스 이름 |
| `user` | string | - | 사용자 이름 |
| `password` | string | - | 비밀번호 |
| `schema` | string | public | 데이터 스키마 |
| `table` | string | plc_data | 데이터 테이블 |
| `pool_size` | int | 5 | 커넥션 풀 크기 |
| `master_sync_enabled` | bool | false | 마스터 동기화 |

---

## 데이터베이스 스키마

### 데이터 테이블

```sql
CREATE TABLE IF NOT EXISTS test.plc_data (
    time            TIMESTAMPTZ     NOT NULL,
    plc_id          SMALLINT        NOT NULL,
    tag_id          INTEGER         NOT NULL,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    v_byte          SMALLINT,
    v_bool          BOOLEAN,
    quality_code    SMALLINT        DEFAULT 1
);

-- TimescaleDB 하이퍼테이블 변환
SELECT create_hypertable('test.plc_data', 'time',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_plc_data_plc_tag
    ON test.plc_data (plc_id, tag_id, time DESC);
```

### 컬럼 설명

| 컬럼 | 타입 | 설명 |
|------|------|------|
| `time` | TIMESTAMPTZ | 데이터 수집 시간 |
| `plc_id` | SMALLINT | PLC 식별자 |
| `tag_id` | INTEGER | 태그 식별자 |
| `v_int` | INTEGER | 정수 값 |
| `v_bigint` | BIGINT | 큰 정수 값 |
| `v_float` | DOUBLE PRECISION | 실수 값 |
| `v_text` | TEXT | 문자열 값 |
| `v_byte` | SMALLINT | 바이트 값 |
| `v_bool` | BOOLEAN | 불리언 값 |
| `quality_code` | SMALLINT | 품질 코드 (1=정상) |

---

## 마스터 테이블

### PLC 마스터

```sql
CREATE TABLE IF NOT EXISTS test.plc_master (
    plc_id          SMALLINT        PRIMARY KEY,
    plc_name        VARCHAR(100)    NOT NULL,
    protocol_type   VARCHAR(50)     NOT NULL,
    host            VARCHAR(255),
    port            INTEGER,
    description     TEXT,
    collect_yn      CHAR(1)         DEFAULT 'Y',
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);
```

### 태그 마스터

```sql
CREATE TABLE IF NOT EXISTS test.tag_master (
    plc_id          SMALLINT        NOT NULL,
    tag_id          INTEGER         NOT NULL,
    tag_name        VARCHAR(100)    NOT NULL,
    memory          VARCHAR(10),
    address         VARCHAR(50)     NOT NULL,
    data_type       VARCHAR(30)     NOT NULL,
    scale           DOUBLE PRECISION DEFAULT 1.0,
    offset_value    DOUBLE PRECISION DEFAULT 0.0,
    decimals        SMALLINT,
    word_length     SMALLINT,
    format          VARCHAR(30),
    unit            VARCHAR(30),
    description     TEXT,
    collection_group VARCHAR(50)    DEFAULT 'default',
    collect_yn      CHAR(1)         DEFAULT 'Y',
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW(),
    PRIMARY KEY (plc_id, tag_id)
);
```

### 동기화 설정

```yaml
publisher:
  database:
    master_sync_enabled: true      # 활성화
    master_sync_schema: "test"     # 스키마 지정
```

시작 시 자동으로 현재 설정을 마스터 테이블에 동기화합니다.

---

## TimescaleDB 설정

### Docker로 실행

```bash
docker run -d \
  --name timescaledb \
  -p 5432:5432 \
  -e POSTGRES_USER=collector \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=collector \
  -v timescaledb-data:/var/lib/postgresql/data \
  timescale/timescaledb:latest-pg15
```

### 초기화 스크립트

```bash
# 테이블 생성
docker exec -i timescaledb psql -U collector \
  -d collector < scripts/init-db.sql
```

### 청크 관리

```sql
-- 청크 크기 설정 (1일)
SELECT set_chunk_time_interval('test.plc_data', INTERVAL '1 day');

-- 오래된 데이터 삭제 (90일 이상)
SELECT drop_chunks('test.plc_data', INTERVAL '90 days');

-- 압축 활성화 (7일 이상)
ALTER TABLE test.plc_data SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'plc_id, tag_id'
);

SELECT add_compression_policy('test.plc_data', INTERVAL '7 days');
```

---

## 성능 최적화

### 배치 INSERT

데이터는 배치로 INSERT됩니다:

```sql
INSERT INTO test.plc_data (time, plc_id, tag_id, v_float, quality_code)
VALUES
    ('2026-02-05 10:00:00', 1, 1, 25.5, 1),
    ('2026-02-05 10:00:00', 1, 2, 1.23, 1),
    ('2026-02-05 10:00:00', 1, 3, 100.0, 1),
    -- ... 최대 batch_size개
;
```

### 권장 설정

```yaml
buffer:
  batch_size: 100       # 100~500 권장

publisher:
  database:
    pool_size: 5        # 동시 연결 수
    pool_timeout: 30    # 연결 대기 시간
```

### 인덱스 최적화

```sql
-- 자주 사용하는 쿼리에 맞춰 인덱스 생성
CREATE INDEX idx_plc_data_time ON test.plc_data (time DESC);
CREATE INDEX idx_plc_data_tag ON test.plc_data (plc_id, tag_id);
```

---

## 쿼리 예시

### 최근 데이터 조회

```sql
SELECT time, tag_id, v_float, quality_code
FROM test.plc_data
WHERE plc_id = 1
ORDER BY time DESC
LIMIT 100;
```

### 태그별 최신값

```sql
SELECT DISTINCT ON (tag_id)
    tag_id, v_float, time
FROM test.plc_data
WHERE plc_id = 1
ORDER BY tag_id, time DESC;
```

### 시간대별 평균

```sql
SELECT
    time_bucket('1 hour', time) AS hour,
    tag_id,
    AVG(v_float) AS avg_value,
    MIN(v_float) AS min_value,
    MAX(v_float) AS max_value
FROM test.plc_data
WHERE plc_id = 1
  AND time > NOW() - INTERVAL '24 hours'
GROUP BY hour, tag_id
ORDER BY hour, tag_id;
```

### 품질 통계

```sql
SELECT
    tag_id,
    COUNT(*) AS total,
    SUM(CASE WHEN quality_code = 1 THEN 1 ELSE 0 END) AS good,
    SUM(CASE WHEN quality_code = 0 THEN 1 ELSE 0 END) AS bad
FROM test.plc_data
WHERE time > NOW() - INTERVAL '1 day'
GROUP BY tag_id;
```

---

## 문제 해결

### 연결 실패

```
ERROR | Database connection failed: Connection refused
```

**확인:**

```bash
# DB 실행 상태
docker ps | grep timescaledb

# 연결 테스트
psql -h localhost -U collector -d collector -c "SELECT 1"
```

### 권한 오류

```
ERROR | Permission denied for table plc_data
```

**해결:**

```sql
GRANT ALL ON SCHEMA test TO collector;
GRANT ALL ON ALL TABLES IN SCHEMA test TO collector;
```

### 디스크 공간

```
ERROR | Could not extend file: No space left on device
```

**해결:**

```sql
-- 오래된 데이터 삭제
SELECT drop_chunks('test.plc_data', INTERVAL '30 days');

-- 압축 실행
SELECT compress_chunk(i) FROM show_chunks('test.plc_data') i;
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
