# 로깅 설정

Simple Collector의 로깅 시스템 설정 가이드입니다.

---

## 개요

Simple Collector는 수집, 송신, 시스템 로그를 분리하여 관리하는 고급 로깅 시스템을 제공합니다.

### 주요 기능

| 기능 | 설명 |
|------|------|
| **로거 분리** | 수집/송신/시스템/손실/에러 로거 분리 |
| **파일 로테이션** | 크기 기반 자동 로테이션 |
| **gzip 압축** | 로그 파일 자동 압축 |
| **JSON 포맷** | Kibana/ELK 연동용 JSON 출력 |
| **ECS 호환** | Elastic Common Schema 지원 |
| **월별 아카이브** | 오래된 로그 자동 정리 |
| **컬러 출력** | 터미널 컬러 지원 |

### 로거 계층 구조

```
collector                     # 루트 로거
├── collector.collection      # 수집 로그
├── collector.publish         # 송신 로그
├── collector.system          # 시스템 로그
├── collector.loss            # 데이터 손실 로그
└── collector.error           # 상세 에러 로그
```

---

## 기본 설정

### YAML 설정

```yaml title="config/collector.yaml"
logging:
  # 기본 로그 레벨
  level: INFO                              # DEBUG, INFO, WARNING, ERROR, CRITICAL

  # 로거별 레벨
  collection_level: INFO                   # 수집 로그 레벨
  publish_level: INFO                      # 송신 로그 레벨

  # 로그 포맷
  format: "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

  # 파일 출력
  file_path: "logs/collector.log"          # 로그 파일 경로
  max_size_mb: 10                          # 파일 최대 크기 (MB)
  backup_count: 5                          # 백업 파일 수

  # 압축 설정
  compress_enabled: true                   # 로테이션 시 gzip 압축
  compress_after_days: 7                   # N일 후 압축

  # 아카이브 설정
  archive_path: "logs/archive"             # 아카이브 디렉토리
  max_archive_count: 12                    # 최대 아카이브 수 (월 단위)

  # JSON 로깅 (ELK 연동)
  json_enabled: false                      # JSON 로깅 활성화
  json_file_path: "logs/collector.json"    # JSON 로그 파일
  ecs_enabled: true                        # ECS 포맷 사용

  # 에러 상세 로깅
  error_detail_enabled: true               # 에러 상세 로깅
```

### 환경변수

```bash
# 로그 레벨 오버라이드
LOG_LEVEL=DEBUG

# 환경 이름 (ECS 로깅용)
ENVIRONMENT=production
```

---

## 로그 레벨

### 레벨 설명

| 레벨 | 숫자 | 설명 | 사용 예 |
|------|------|------|---------|
| **VERBOSE** | 5 | 초상세 정보 | 개별 요청/응답, 태그별 값 |
| **DEBUG** | 10 | 디버깅 정보 | 그룹 완료, 배치 처리 |
| **INFO** | 20 | 일반 정보 | 연결 성공, 수집 완료 |
| **WARNING** | 30 | 경고 | 재시도, 품질 저하 |
| **ERROR** | 40 | 에러 | 연결 실패, 예외 발생 |
| **CRITICAL** | 50 | 심각한 에러 | 시스템 중단 |

!!! tip "VERBOSE 레벨 (v0.2.3+)"
    VERBOSE는 DEBUG보다 낮은 레벨로, 개별 PLC 요청/응답 로그에 사용됩니다.
    운영 환경에서는 INFO 이상, 문제 분석 시에만 VERBOSE를 사용하세요.

### 레벨별 로그 예시

```log
# VERBOSE (개별 요청)
2026-02-06 10:00:00 | VERBOSE  | collector.collection | Reading D[0:100] (word)
2026-02-06 10:00:00 | VERBOSE  | collector.publish | Published to factory/plc1: 256 bytes

# DEBUG (그룹 완료)
2026-02-06 10:00:00 | DEBUG    | collector.collection | Group 'fast' collected: 100 tags in 45ms

# INFO
2026-02-06 10:00:00 | INFO     | collector.collection | Collected 100 tags in 45ms

# WARNING
2026-02-06 10:00:01 | WARNING  | collector.collection | Retry 1/3: Connection timeout

# ERROR
2026-02-06 10:00:02 | ERROR    | collector.collection | Connection failed: Connection refused

# CRITICAL
2026-02-06 10:00:03 | CRITICAL | collector.system | System shutdown: Database connection lost
```

---

## 로거 분리

### 수집 로거 (collection)

데이터 수집 관련 로그:

```python
from src.utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()

logger.verbose(f"Reading D[0:100] (word)")          # 개별 요청 (VERBOSE)
logger.debug(f"Group 'fast' collected in 45ms")    # 그룹 완료 (DEBUG)
logger.info(f"Collected {count} tags from PLC {plc_id}")
logger.warning(f"Quality degraded: tag={tag_name}, quality={quality}")
logger.error(f"Read failed: {e}")
```

### 송신 로거 (publish)

데이터 송신 관련 로그:

```python
logger = LoggerFactory.get_publish_logger()

logger.verbose(f"Published to topic: 256 bytes")   # 개별 발행 (VERBOSE)
logger.debug(f"Batch insert: {duration_ms}ms")     # 배치 완료 (DEBUG)
logger.info(f"Published {count} records to database")
logger.warning(f"Retry publishing: attempt {retry}")
logger.error(f"Publish failed: {e}")
```

### 시스템 로거 (system)

시스템 이벤트 로그:

```python
logger = LoggerFactory.get_system_logger()

logger.info("Application started")
logger.warning("Configuration changed")
logger.error("Service unhealthy")
```

### 손실 로거 (loss)

데이터 손실 전용 로그:

```python
logger = LoggerFactory.get_loss_logger()

# 또는 헬퍼 함수 사용
from src.utils.logging import log_data_loss

log_data_loss(
    reason="connection_lost",
    lost_count=10,
    context={"plc_id": 1, "group": "fast"}
)
```

### 에러 로거 (error)

상세 에러 로그:

```python
from src.utils.logging import log_error

try:
    data = await collector.read()
except Exception as e:
    log_error(e, "Failed to collect data", {
        "plc_id": 1,
        "tag_count": 100,
        "group": "fast"
    })
```

---

## 로그 파일 관리

### 파일 구조

```
logs/
├── collector.log              # 메인 로그
├── collector.log.1.gz         # 로테이션된 로그 (압축)
├── collector.log.2.gz
├── loss.log                   # 손실 로그
├── error.log                  # 에러 상세 로그
├── collector.json             # JSON 로그 (ELK 연동용)
└── archive/                   # 월별 아카이브
    ├── logs_202601.tar.gz
    └── logs_202602.tar.gz
```

### 로테이션 설정

```yaml
logging:
  file_path: "logs/collector.log"
  max_size_mb: 10        # 10MB 초과 시 로테이션
  backup_count: 5        # 최대 5개 백업 유지
  compress_enabled: true # 로테이션 시 gzip 압축
```

로테이션 흐름:

```
collector.log (현재)
   ↓ 10MB 초과
collector.log.1.gz (압축)
   ↓ 다음 로테이션
collector.log.2.gz
   ...
collector.log.5.gz (삭제됨)
```

### 월별 아카이브

```yaml
logging:
  archive_path: "logs/archive"
  compress_after_days: 7     # 7일 후 압축
  max_archive_count: 12      # 12개월분 보관
```

아카이브 명명 규칙:

```
logs_YYYYMM.tar.gz
예: logs_202601.tar.gz
```

---

## JSON 로깅 (ELK 연동)

### 기본 JSON 포맷

```yaml
logging:
  json_enabled: true
  json_file_path: "logs/collector.json"
  ecs_enabled: false  # 기본 JSON
```

출력 예시:

```json
{
  "timestamp": "2026-02-06T10:00:00.123456",
  "level": "INFO",
  "logger": "collector.collection",
  "message": "Collected 100 tags in 45ms",
  "module": "collector",
  "function": "_do_collect",
  "line": 156,
  "process_id": 12345,
  "thread_id": 67890,
  "plc_id": 1,
  "tag_count": 100,
  "duration_ms": 45
}
```

### ECS (Elastic Common Schema) 포맷

```yaml
logging:
  json_enabled: true
  json_file_path: "logs/collector.json"
  ecs_enabled: true  # ECS 포맷 활성화
```

ECS 출력 예시:

```json
{
  "@timestamp": "2026-02-06T10:00:00.123456Z",
  "log": {
    "level": "info",
    "logger": "collector.collection",
    "origin": {
      "file": {"name": "collector.py", "line": 156},
      "function": "_do_collect"
    }
  },
  "message": "Collected 100 tags in 45ms",
  "host": {"hostname": "collector-01"},
  "service": {
    "name": "simple-collector",
    "version": "0.2.0",
    "environment": "production"
  },
  "process": {
    "pid": 12345,
    "thread": {"id": 67890, "name": "MainThread"}
  },
  "labels": {
    "plc_id": 1,
    "tag_count": 100
  },
  "event": {
    "duration": 45000000
  }
}
```

### Kibana 연동

Filebeat 설정 예시:

```yaml title="filebeat.yml"
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /var/log/simple-collector/*.json
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["localhost:9200"]
  index: "simple-collector-%{+yyyy.MM.dd}"
```

---

## 손실 로그

### 손실 추적

수집 또는 송신 실패 시 자동으로 손실이 기록됩니다:

```log
2026-02-06 10:00:01 | WARNING | collector.loss | LOSS group='fast' reason=collection_failed consecutive=3 total=15
2026-02-06 10:00:02 | WARNING | collector.loss | LOSS group='fast' reason=not_connected consecutive=4 total=16
```

### JSON 손실 로그

```json
{
  "timestamp": "2026-02-06T10:00:01.000000",
  "level": "WARNING",
  "logger": "collector.loss",
  "message": "Data loss: connection_lost - 10 records lost",
  "loss_reason": "connection_lost",
  "lost_count": 10,
  "plc_id": 1,
  "group": "fast"
}
```

### 손실 분석

손실 로그를 분석하여 문제를 파악합니다:

```bash
# 손실 원인별 집계
grep "LOSS" logs/loss.log | awk -F'reason=' '{print $2}' | awk '{print $1}' | sort | uniq -c

# 시간대별 손실
grep "LOSS" logs/loss.log | cut -d' ' -f1,2 | cut -c1-13 | sort | uniq -c
```

---

## 에러 상세 로깅

### 에러 컨텍스트

```python
from src.utils.logging import log_error

try:
    result = await process_data(data)
except ValueError as e:
    log_error(e, "Data validation failed", {
        "plc_id": plc_id,
        "tag_name": tag.tag_name,
        "raw_value": raw_value,
        "expected_type": tag.data_type.name,
    })
```

### 에러 로그 출력

```log
================================================================================
2026-02-06 10:00:01 | ERROR | collector.error
Message: Data validation failed: ValueError: Invalid float value
Location: processor._parse_value:156
================================================================================
Traceback (most recent call last):
  File "processor.py", line 150, in _parse_value
    value = struct.unpack('>f', data)
ValueError: Invalid float value
================================================================================
```

### JSON 에러 로그

```json
{
  "@timestamp": "2026-02-06T10:00:01.000Z",
  "log": {"level": "error", "logger": "collector.error"},
  "message": "Data validation failed",
  "error": {
    "type": "ValueError",
    "message": "Invalid float value",
    "stack_trace": "Traceback (most recent call last):\n  ..."
  },
  "labels": {
    "plc_id": 1,
    "tag_name": "Temperature",
    "raw_value": "0xFFFFFFFF"
  }
}
```

---

## 로그 컨텍스트

### 컨텍스트 매니저

특정 작업에 대한 로그 컨텍스트를 추가합니다:

```python
from src.utils.logging import LogContext, LoggerFactory

logger = LoggerFactory.get_collection_logger()

with LogContext(plc_id=1, group="fast"):
    logger.info("Starting collection")
    # → JSON에 {"labels": {"plc_id": 1, "group": "fast"}} 추가

    await collector.collect()
    logger.info("Collection completed")
```

---

## 성능 최적화

### 권장 설정

| 환경 | 레벨 | JSON | 압축 | 비고 |
|------|------|------|------|------|
| 개발 | DEBUG | 선택 | 아니오 | 상세 로그 필요 |
| 테스트 | INFO | 예 | 예 | ELK 테스트 |
| 운영 | WARNING | 예 | 예 | 최소 로그 |

### 운영 환경 설정

```yaml
logging:
  level: WARNING
  collection_level: INFO
  publish_level: INFO

  file_path: "logs/collector.log"
  max_size_mb: 50
  backup_count: 10
  compress_enabled: true

  json_enabled: true
  json_file_path: "logs/collector.json"
  ecs_enabled: true

  archive_path: "logs/archive"
  max_archive_count: 12

  error_detail_enabled: true
```

### 디스크 사용량 예측

```
일일 로그량 ≈ 수집 주기 × 태그 수 × 레코드 크기

예: 1초 주기 × 100태그 × 100바이트 = ~8.64GB/일 (DEBUG)
    1초 주기 × 100태그 × 50바이트 = ~4.32GB/일 (INFO)
    압축 시: ~0.5GB/일
```

---

## 문제 해결

### 로그가 출력되지 않음

1. 로그 레벨 확인: `level: DEBUG`로 설정
2. 파일 권한 확인: `logs/` 디렉토리 쓰기 권한
3. 핸들러 확인: `setup_logging()` 호출 확인

### 디스크 공간 부족

1. `max_size_mb` 감소
2. `backup_count` 감소
3. `compress_enabled: true` 확인
4. 로그 레벨 상향: `WARNING` 또는 `ERROR`

### JSON 파싱 오류

1. UTF-8 인코딩 확인
2. 멀티라인 메시지 확인
3. 특수 문자 이스케이프 확인

---

## 코드 예제

### 커스텀 로거 생성

```python
from src.utils.logging import LoggerFactory

# 커스텀 로거
my_logger = LoggerFactory.get_logger("custom")
my_logger.info("Custom log message")
```

### 로그 레벨 동적 변경

```python
from src.utils.logging import LoggerFactory

# 런타임 레벨 변경
LoggerFactory.set_level("collection", "DEBUG")
LoggerFactory.set_level("publish", "WARNING")
```

### 아카이브 수동 실행

```python
from src.utils.logging import run_archive_task

result = run_archive_task()
print(f"Archived: {len(result['archived_files'])} files")
print(f"Monthly: {result['monthly_archive']}")
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
