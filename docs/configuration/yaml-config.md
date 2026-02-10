# YAML 설정

Simple Collector의 메인 설정 파일(collector.yaml) 작성법을 안내합니다.

---

## 전체 구조

```yaml
# =============================================================================
# Simple Collector Configuration
# =============================================================================

collector:          # 수집기 설정
  plc_id: ...
  name: ...
  protocol: ...
  collection: ...

publisher:          # 발행기 설정
  database: ...
  mqtt: ...

buffer:             # 버퍼 설정
  max_size: ...
  batch_size: ...

logging:            # 로깅 설정
  level: ...
  file_path: ...
```

---

## collector 섹션

### 기본 설정

```yaml
collector:
  plc_id: 1                      # PLC 고유 ID (필수, 정수)
  name: "Production_Line_1"      # 수집기 이름 (필수)
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `plc_id` | int | O | PLC 고유 식별자 (1~32767) |
| `name` | string | O | 수집기 이름 |

### protocol 설정

#### Modbus TCP

```yaml
collector:
  protocol:
    type: modbus              # 프로토콜 타입
    host: "192.168.1.100"     # PLC IP 주소
    port: 502                 # TCP 포트 (기본: 502)
    unit_id: 1                # Modbus Unit ID (기본: 1)
    timeout: 3.0              # 타임아웃 (초, 기본: 3.0)
    byte_order: big           # 바이트 순서 (big/little)
    word_order: big           # 워드 순서 (big/little)
```

#### MC Protocol

```yaml
collector:
  protocol:
    type: mcprotocol          # 프로토콜 타입
    host: "192.168.1.100"     # PLC IP 주소
    port: 5000                # TCP 포트 (기본: 5000)
    plc_type: "Q"             # PLC 시리즈 (Q/L/R)
    timeout: 3.0              # 타임아웃 (초)
    network_no: 0             # 네트워크 번호
    station_no: 0             # 국번
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `type` | string | - | `modbus` 또는 `mcprotocol` |
| `host` | string | - | PLC IP 주소 |
| `port` | int | 502/5000 | TCP 포트 |
| `timeout` | float | 3.0 | 연결/응답 타임아웃 (초) |

### collection 설정

```yaml
collector:
  collection:
    - group: fast             # 그룹 이름
      interval_ms: 1000       # 수집 주기 (밀리초)
      tags_file: "config/tags_fast.csv"
      enabled: true           # 활성화 여부 (기본: true)

    - group: slow
      interval_ms: 60000
      tags_file: "config/tags_slow.csv"

    - group: alarms           # 알람/이벤트 그룹
      interval_ms: 500
      tags_file: "config/tags_alarms.csv"
      mode: on_change         # 값 변경 시에만 전달
      deadband: 0.5           # 변화 임계값
      deadband_type: absolute # 비교 방식
```

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| `group` | string | O | 그룹 이름 (고유해야 함) |
| `interval_ms` | int | O | 수집 주기 (밀리초) |
| `tags_file` | string | O | 태그 CSV 파일 경로 |
| `enabled` | bool | - | 그룹 활성화 여부 |
| `mode` | string | - | 수집 모드: `polling` (기본) 또는 `on_change` |
| `deadband` | float | - | 변화 감지 임계값 (기본: 0.0) |
| `deadband_type` | string | - | 임계값 유형: `absolute` (기본) 또는 `percent` |

#### on_change 모드

알람, 이벤트, 상태 변경 등 값이 변경될 때만 데이터를 전달해야 하는 경우 사용합니다.

```yaml
collector:
  collection:
    # 알람 그룹: 값이 변경될 때만 전달
    - group: alarms
      interval_ms: 500
      tags_file: "config/tags_alarms.csv"
      mode: on_change
      deadband: 0.0           # 모든 변화 감지

    # 온도 모니터링: 0.5도 이상 변화 시에만 전달
    - group: temperature
      interval_ms: 1000
      tags_file: "config/tags_temp.csv"
      mode: on_change
      deadband: 0.5
      deadband_type: absolute

    # 진동 모니터링: 5% 이상 변화 시에만 전달
    - group: vibration
      interval_ms: 100
      tags_file: "config/tags_vibration.csv"
      mode: on_change
      deadband: 5.0
      deadband_type: percent
```

!!! info "deadband 동작"
    - **absolute**: 절대값 비교 `|new - old| > deadband`
    - **percent**: 백분율 비교 `|new - old| / |old| × 100 > deadband`
    - bool/string 타입은 deadband 무시, 값 변경 시 항상 전달

---

## publisher 섹션

### database 설정

```yaml
publisher:
  database:
    enabled: true             # 활성화 여부
    host: "localhost"         # DB 호스트
    port: 5432                # DB 포트
    database: "collector"     # 데이터베이스 이름
    user: "collector"         # 사용자
    password: "${DB_PASSWORD}" # 비밀번호 (환경변수 권장)
    schema: "test"            # 스키마 이름
    table: "plc_data"         # 테이블 이름
    pool_size: 5              # 커넥션 풀 크기
    master_sync_enabled: true # 마스터 동기화
    master_sync_schema: "test" # 마스터 스키마
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | Publisher 활성화 |
| `host` | string | localhost | DB 호스트 |
| `port` | int | 5432 | DB 포트 |
| `database` | string | - | 데이터베이스 이름 |
| `user` | string | - | 사용자 이름 |
| `password` | string | - | 비밀번호 |
| `schema` | string | public | 스키마 이름 |
| `pool_size` | int | 5 | 커넥션 풀 크기 |

### mqtt 설정

```yaml
publisher:
  mqtt:
    enabled: true             # 활성화 여부
    host: "localhost"         # 브로커 호스트
    port: 1883                # 브로커 포트
    username: "collector"     # 사용자 (선택)
    password: "${MQTT_PASSWORD}" # 비밀번호 (선택)
    topic_prefix: "factory/plc" # 토픽 접두사
    qos: 1                    # QoS 레벨 (0, 1, 2)
    retain: false             # 메시지 보존
    client_id: "collector_1"  # 클라이언트 ID
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | MQTT 활성화 |
| `host` | string | localhost | 브로커 호스트 |
| `port` | int | 1883 | 브로커 포트 |
| `qos` | int | 1 | QoS 레벨 |
| `topic_prefix` | string | - | 토픽 접두사 |

### json_file 설정

```yaml
publisher:
  json_file:
    enabled: true             # 활성화 여부
    file_path: "data/output.json"  # 출력 파일 경로
    mode: "append"            # 모드: append 또는 overwrite
    max_size_mb: 10           # 파일 최대 크기 (MB)
    backup_count: 5           # 백업 파일 수
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | JSON File Publisher 활성화 |
| `file_path` | string | data/output.json | 출력 파일 경로 |
| `mode` | string | append | `append` (추가) 또는 `overwrite` (덮어쓰기) |
| `max_size_mb` | int | 10 | 파일 최대 크기 (MB), 초과 시 로테이션 |
| `backup_count` | int | 5 | 백업 파일 수 |

!!! info "출력 형식"
    MQTT Publisher와 동일한 compact JSON 형식으로 출력됩니다.
    각 배치가 한 줄로 작성됩니다 (줄바꿈, 공백 없음).

---

## buffer 섹션

```yaml
buffer:
  max_size: 10000           # 최대 버퍼 크기
  batch_size: 100           # 배치 크기
  overflow_policy: drop_oldest  # 오버플로우 정책
  threshold_percent: 80     # 경고 임계값 (%)
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `max_size` | int | 10000 | 최대 버퍼 크기 |
| `batch_size` | int | 100 | 한 번에 발행할 레코드 수 |
| `overflow_policy` | string | drop_oldest | `drop_oldest` 또는 `drop_newest` |
| `threshold_percent` | int | 80 | 경고 임계값 (%) |

---

## logging 섹션

```yaml
logging:
  level: "INFO"              # 기본 로그 레벨
  collection_level: "DEBUG"  # 수집 로그 레벨
  publish_level: "INFO"      # 발행 로그 레벨
  file_path: "logs/collector.log"  # 로그 파일 경로
  max_size_mb: 10            # 파일 최대 크기 (MB)
  backup_count: 5            # 백업 파일 수
  format: "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
```

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `level` | string | INFO | 기본 로그 레벨 |
| `collection_level` | string | INFO | 수집 로그 레벨 |
| `publish_level` | string | INFO | 발행 로그 레벨 |
| `file_path` | string | - | 로그 파일 경로 |
| `max_size_mb` | int | 10 | 파일 최대 크기 |
| `backup_count` | int | 5 | 백업 파일 수 |

---

## 전체 예시

```yaml title="config/collector.yaml"
# =============================================================================
# Simple Collector Configuration
# Copyright (c) 2024-2026 NEUROSENSE Inc.
# =============================================================================

collector:
  plc_id: 1
  name: "Production_Line_1"

  protocol:
    type: modbus
    host: "192.168.1.100"
    port: 502
    unit_id: 1
    timeout: 3.0
    byte_order: big
    word_order: big

  collection:
    - group: realtime
      interval_ms: 1000
      tags_file: "config/tags_realtime.csv"

    - group: normal
      interval_ms: 10000
      tags_file: "config/tags_normal.csv"

    - group: alarms
      interval_ms: 500
      tags_file: "config/tags_alarms.csv"
      mode: on_change
      deadband: 0.0

publisher:
  database:
    enabled: true
    host: ${DB_HOST:-localhost}
    port: ${DB_PORT:-5432}
    database: ${DB_NAME:-collector}
    user: ${DB_USER:-collector}
    password: ${DB_PASSWORD}
    schema: "test"
    pool_size: 5
    master_sync_enabled: true
    master_sync_schema: "test"

  mqtt:
    enabled: false
    host: ${MQTT_HOST:-localhost}
    port: ${MQTT_PORT:-1883}
    topic_prefix: "factory/line1"
    qos: 1

  json_file:
    enabled: false
    file_path: "data/output.json"
    mode: "append"
    max_size_mb: 10
    backup_count: 5

buffer:
  max_size: 10000
  batch_size: 100
  overflow_policy: drop_oldest
  threshold_percent: 80

logging:
  level: "INFO"
  collection_level: "DEBUG"
  publish_level: "INFO"
  file_path: "logs/collector.log"
  max_size_mb: 10
  backup_count: 5
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
