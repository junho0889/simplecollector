# JSON File Publisher

JSON 파일로 데이터를 출력하는 방법을 안내합니다.

---

## 개요

**JSON File Publisher**는 수집된 데이터를 로컬 JSON 파일로 저장합니다.
MQTT Publisher와 동일한 JSON 형식을 사용하며, 오프라인 분석이나 백업 용도에 적합합니다.

!!! info "v0.2.3+ 신규 기능"
    JSON File Publisher는 v0.2.3-beta에서 추가되었습니다.

### 특징

- MQTT와 동일한 JSON 포맷
- Compact JSON (줄바꿈/공백 없음)
- 파일 로테이션 지원
- append/overwrite 모드

---

## 설정

### 기본 설정

```yaml title="config/collector.yaml"
publisher:
  json_file:
    enabled: true
    file_path: "data/output.json"
```

### 전체 옵션

```yaml
publisher:
  json_file:
    enabled: true              # 활성화 여부
    file_path: "data/output.json"  # 출력 파일 경로
    mode: "append"             # 모드: append 또는 overwrite
    max_size_mb: 10            # 파일 최대 크기 (MB)
    backup_count: 5            # 백업 파일 수
```

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | false | Publisher 활성화 |
| `file_path` | string | data/output.json | 출력 파일 경로 |
| `mode` | string | append | `append` (추가) 또는 `overwrite` (덮어쓰기) |
| `max_size_mb` | int | 10 | 파일 최대 크기 (MB) |
| `backup_count` | int | 5 | 백업 파일 수 |

---

## 출력 형식

### JSON 구조 (배치)

각 발행 배치가 한 줄로 출력됩니다:

```json
{"plc_id":1,"timestamp":"2026-02-09T10:00:01.123","count":10,"data":[...]}
```

### 전체 구조

```json
{
  "plc_id": 1,
  "timestamp": "2026-02-09T10:00:01.123",
  "count": 10,
  "data": [
    {
      "source_time": "2026-02-09T10:00:01.100",
      "server_time": "2026-02-09T10:00:01.123",
      "tag_id": 1,
      "v_float": 25.5,
      "quality_code": 1
    },
    {
      "source_time": "2026-02-09T10:00:01.100",
      "server_time": "2026-02-09T10:00:01.123",
      "tag_id": 2,
      "v_int": 1234,
      "quality_code": 1
    }
  ]
}
```

### 필드 설명

| 필드 | 타입 | 설명 |
|------|------|------|
| `plc_id` | int | PLC 식별자 |
| `timestamp` | string | 발행 시간 (ISO 8601) |
| `count` | int | 데이터 개수 |
| `data` | array | ProcessedData 배열 |

### data 배열 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `source_time` | string | PLC 수집 시간 |
| `server_time` | string | 서버 처리 시간 |
| `tag_id` | int | 태그 ID |
| `v_float` | float | float 값 (해당 시) |
| `v_int` | int | int 값 (해당 시) |
| `v_bigint` | int | bigint 값 (해당 시) |
| `v_text` | string | 문자열 값 (해당 시) |
| `v_bool` | bool | bool 값 (해당 시) |
| `quality_code` | int | 품질 코드 (1=정상, 0=실패) |

---

## 파일 로테이션

### 동작 방식

파일 크기가 `max_size_mb`를 초과하면 자동으로 로테이션됩니다:

```
data/output.json        (현재 파일)
data/output.json.1      (이전 파일)
data/output.json.2
data/output.json.3
data/output.json.4
data/output.json.5      (가장 오래된 파일, 삭제됨)
```

### 설정 예시

```yaml
publisher:
  json_file:
    enabled: true
    file_path: "data/output.json"
    mode: "append"
    max_size_mb: 50        # 50MB마다 로테이션
    backup_count: 10       # 최대 10개 백업 유지
```

---

## 모드

### append 모드 (기본)

기존 파일에 데이터를 추가합니다:

```yaml
publisher:
  json_file:
    mode: "append"
```

- 파일이 존재하면 끝에 추가
- 파일이 없으면 새로 생성
- 로테이션 지원

### overwrite 모드

매번 파일을 새로 작성합니다:

```yaml
publisher:
  json_file:
    mode: "overwrite"
```

- 시작 시 파일 초기화
- 마지막 배치만 유지
- 로테이션 미적용

---

## 예제

### 기본 설정 (데이터 백업용)

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "Data_Backup"

publisher:
  database:
    enabled: true         # DB도 활성화

  json_file:
    enabled: true
    file_path: "data/backup.json"
    mode: "append"
    max_size_mb: 100
    backup_count: 10
```

### 디버깅용 (최신 데이터만)

```yaml
publisher:
  json_file:
    enabled: true
    file_path: "data/latest.json"
    mode: "overwrite"     # 항상 최신 데이터만
```

### Docker 볼륨 마운트

```yaml title="docker-compose.yml"
services:
  collector:
    volumes:
      - ./data:/app/data       # JSON 출력 디렉토리
      - ./config:/app/config:ro
```

---

## 파일 분석

### jq를 사용한 분석

```bash
# 전체 레코드 수 확인
cat data/output.json | wc -l

# 특정 PLC 필터링
cat data/output.json | jq 'select(.plc_id == 1)'

# 특정 태그 값 추출
cat data/output.json | jq '.data[] | select(.tag_id == 1) | .v_float'

# 시간대별 카운트
cat data/output.json | jq -r '.timestamp' | cut -c1-13 | sort | uniq -c
```

### Python으로 분석

```python
import json

with open('data/output.json', 'r') as f:
    for line in f:
        batch = json.loads(line)
        print(f"PLC {batch['plc_id']}: {batch['count']} records")

        for record in batch['data']:
            if record.get('v_float') is not None:
                print(f"  Tag {record['tag_id']}: {record['v_float']}")
```

---

## 통계 확인

Publisher 통계에서 파일 정보를 확인할 수 있습니다:

```python
stats = publisher.get_stats()
print(f"File: {stats['file_path']}")
print(f"Mode: {stats['mode']}")
print(f"Total written: {stats['total_written']}")
print(f"File size: {stats.get('file_size_mb', 0):.2f} MB")
```

---

## 문제 해결

### 파일 쓰기 실패

```
ERROR | Failed to open file: Permission denied
```

**확인:**

```bash
# 디렉토리 권한 확인
ls -la data/

# 디렉토리 생성
mkdir -p data

# 권한 부여
chmod 755 data
```

### 디스크 공간 부족

```
ERROR | Write error: No space left on device
```

**확인:**

```bash
# 디스크 사용량
df -h

# JSON 파일 크기
du -sh data/*.json*
```

**해결:**

- `max_size_mb` 감소
- `backup_count` 감소
- 오래된 백업 수동 삭제

### 로테이션이 동작하지 않음

**확인:**

- `mode: "append"` 설정 확인
- 파일 크기가 `max_size_mb` 초과했는지 확인
- 로그에서 rotation 메시지 확인

---

## MQTT vs JSON File

| 특성 | MQTT | JSON File |
|------|------|-----------|
| 실시간성 | 즉시 (네트워크) | 즉시 (로컬 I/O) |
| 네트워크 | 필요 | 불필요 |
| 구독자 | 다수 가능 | 파일 접근자 |
| 데이터 형식 | 동일 | 동일 |
| 사용 사례 | 실시간 모니터링 | 로컬 백업, 오프라인 분석 |

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
