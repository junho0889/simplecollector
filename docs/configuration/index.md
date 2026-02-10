# 설정 가이드

Simple Collector의 설정 방법을 안내합니다.

---

## 설정 파일 구조

```
config/
├── collector.yaml      # 메인 설정 파일
├── tags_fast.csv       # 빠른 수집 태그 정의
├── tags_slow.csv       # 느린 수집 태그 정의
└── .env                # 환경 변수 (선택)
```

---

## 설정 유형

<div class="grid cards" markdown>

-   :material-file-cog:{ .lg .middle } **YAML 설정**

    ---

    수집기, 프로토콜, Publisher 설정

    [:octicons-arrow-right-24: YAML 설정](yaml-config.md)

-   :material-table:{ .lg .middle } **CSV 태그 정의**

    ---

    수집할 태그 목록 및 속성

    [:octicons-arrow-right-24: 태그 CSV](tags-csv.md)

-   :material-key:{ .lg .middle } **환경 변수**

    ---

    민감한 정보 및 런타임 설정

    [:octicons-arrow-right-24: 환경 변수](environment.md)

</div>

---

## 설정 우선순위

설정은 다음 순서로 적용됩니다 (높은 우선순위가 낮은 것을 덮어씁니다):

1. **환경 변수** (가장 높음)
2. **YAML 설정 파일**
3. **기본값** (가장 낮음)

```yaml
# YAML에서 환경 변수 참조
publisher:
  database:
    password: ${DB_PASSWORD}       # 환경 변수 사용
    host: ${DB_HOST:-localhost}    # 기본값 지정 가능
```

---

## 설정 검증

### 설정 파일 유효성 검사

```bash
python -m src.main --config config/collector.yaml --validate
```

### 예상 출력

```
[OK] YAML 문법 유효
[OK] 필수 필드 확인
[OK] 프로토콜 설정 유효
[OK] Publisher 설정 유효
[OK] 태그 파일 로드 성공 (fast: 10개, slow: 5개)

설정 검증 완료: 오류 없음
```

---

## 최소 설정 예시

### collector.yaml

```yaml
collector:
  plc_id: 1
  name: "MyCollector"
  protocol:
    type: modbus
    host: "192.168.1.100"
    port: 502
  collection:
    - group: default
      interval_ms: 1000
      tags_file: "config/tags.csv"

publisher:
  database:
    enabled: true
    host: "localhost"
    port: 5432
    database: "collector"
    user: "collector"
    password: "password"
```

### tags.csv

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset
1,Temperature,D,100,float32,default,1.0,0.0
2,Pressure,D,102,float32,default,1.0,0.0
```

---

## 고급 설정

### 다중 수집 그룹

```yaml
collector:
  collection:
    # 1초 주기 - 실시간 데이터
    - group: realtime
      interval_ms: 1000
      tags_file: "config/tags_realtime.csv"

    # 10초 주기 - 일반 데이터
    - group: normal
      interval_ms: 10000
      tags_file: "config/tags_normal.csv"

    # 1분 주기 - 통계 데이터
    - group: statistics
      interval_ms: 60000
      tags_file: "config/tags_statistics.csv"
```

### 이벤트 기반 수집 (on_change 모드)

알람, 상태 변경 등 값이 바뀔 때만 데이터를 전달하는 모드입니다.

```yaml
collector:
  collection:
    # 알람 그룹 - 값 변경 시에만 전달
    - group: alarms
      interval_ms: 500
      tags_file: "config/tags_alarms.csv"
      mode: on_change
      deadband: 0.0           # 모든 변화 감지

    # 온도 모니터링 - 0.5도 이상 변화 시
    - group: temperature
      interval_ms: 1000
      tags_file: "config/tags_temp.csv"
      mode: on_change
      deadband: 0.5
      deadband_type: absolute
```

| 옵션 | 설명 |
|------|------|
| `mode: on_change` | 값 변경 시에만 전달 |
| `deadband` | 변화 감지 임계값 |
| `deadband_type` | `absolute` (절대값) 또는 `percent` (백분율) |

[:octicons-arrow-right-24: 자세한 설정](yaml-config.md#on_change-모드)

### 마스터 테이블 동기화

```yaml
publisher:
  database:
    enabled: true
    master_sync_enabled: true      # 마스터 동기화 활성화
    master_sync_schema: test       # 스키마 지정
```

---

## 설정 변경 적용

!!! warning "주의"
    설정 변경 후에는 Collector를 재시작해야 합니다.

```bash
# Docker
docker compose restart collector

# 직접 실행
# Ctrl+C로 중지 후 재실행
python -m src.main --config config/collector.yaml
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
