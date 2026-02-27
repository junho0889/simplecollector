# CLAUDE.md - simpleCollector

## Project Overview
PLC 데이터 수집기. PLC에서 데이터를 읽어 RabbitMQ로 발행하는 asyncio 기반 서비스.

```
[PLC] ←MC/Modbus→ [simpleCollector] →AMQP→ [RabbitMQ] →→ [collector-publisher] →→ [TimescaleDB]
```

## Related Projects
| Project | Path | Role |
|---------|------|------|
| **simpleCollector** (this) | `d:\4.source\simpleCollector` | PLC → RabbitMQ 수집기 |
| collector-publisher | `d:\4.source\collector-publisher` | RabbitMQ → DB/MQTT 저장 |
| collectorhub | `d:\4.source\collectorhub` | 관리 플랫폼 (Agent + Web UI) |

- Git: `https://git.neurosense.synology.me/NASDAQ/simpleCollector.git` branch `dev/junho.noh`

## Code Style
- Python 3.11+ (asyncio)
- Type hints required
- Korean comments OK
- Custom log level: VERBOSE (between DEBUG and INFO)

## Architecture

### Data Flow
```
PLC ← Protocol (MC/Modbus) → Collector → Buffer → Processor → Publisher → RabbitMQ
```

### Key Modules
| Module | Path | Role |
|--------|------|------|
| Config | `src/core/config.py` | YAML → dataclass 파싱 (`ConfigLoader._parse_config()`) |
| Interfaces | `src/core/interfaces.py` | `TagDefinition`, `DataType`, `CollectedData` 등 |
| Registry | `src/core/registry.py` | `ProtocolRegistry` + `PublisherRegistry` |
| MC Protocol | `src/collectors/mc_protocol/` | Mitsubishi MC Protocol (iQ-R/Q/L/FX) |
| Modbus | `src/collectors/modbus/` | Pure Python (no pymodbus), TCP/RTU/RTU-over-TCP |
| Processor | `src/processors/base.py` | raw 값 → 스케일링 → 타입 라우팅 |
| Publisher | `src/publishers/rabbitmq/` | RabbitMQ 발행 (aio-pika) |
| Pipeline | `src/pipeline/` | Collector+Processor+Publisher 조합 |
| Serializer | `src/utils/serializer.py` | JSON → zlib → Fernet encrypt (optional) |

### Tag Scaling (`TagDefinition.apply_scaling()`)
```python
scaled = (raw_value * scale) + offset
# decimals 적용 (타입별 분기):
# - 정수 타입(uint16, int32 등): 고정소수점 변환 (÷10^decimals)
#   예) uint16, decimals=2: raw 3061 → 30.61
# - float 타입(float32, float64): 단순 반올림
#   예) float32, decimals=1: raw 69.1 → 69.1 (그대로)
if decimals > 0:
    if data_type in (FLOAT32, FLOAT64):
        scaled = round(scaled, decimals)
    else:
        scaled = round(scaled / (10 ** decimals), decimals)
```
**주의**: 정수 타입의 `decimals`는 `÷10^decimals` 변환 (PLC/HMI 업계 표준), float는 round만

### Collection Groups
태그를 그룹별로 나눠 다른 주기/방식으로 수집:
- `plc_data`: 실시간 데이터 (polling, 1~2초)
- `alm`: 알람 (on_change, 값 변경 시에만 발행)
- `log`: 설비 로그 (polling, 5초)

### Extensions (collector-publisher YAML 설정)
그룹별 부가 기능. publisher YAML의 `collection_groups[].extensions`에서 설정.

```yaml
collection_groups:
  - name: "alm"
    mode: "latest"
    extensions:
      history:                    # 값 변경 이력
        trigger_on: "v_bool"      # v_bool, v_int, v_float, v_text, all
      snapshot:                   # 신호 캡처 (rising edge)
        triggers:
          - watch_tag: 1
            capture_tags: [1, 2, 3]
```

- **history**: `{group}_latest` UPDATE 시 trigger_on 컬럼 변경 감지 → `{group}_history`에 INSERT
- **snapshot**: watch_tag의 v_bool rising edge(FALSE→TRUE) → capture_tags의 latest 값을 `{group}_snapshot`에 INSERT
- 테이블은 hypertable로 자동 변환, 압축/보관 정책은 그룹 설정을 따름
- DDL 자동 생성: `schema_init.py`의 `_build_history_ddl()`, `_build_snapshot_ddl()`

### Master Sync (재시작 동작)
publisher 재시작 시 `master_sync.py`가 실행:
1. `{group}_master` + `{group}_latest` 테이블 TRUNCATE (stale 데이터 방지)
2. collector YAML + tag CSV 스캔 → `plc_master` UPSERT + `{group}_master` INSERT
- `_integrated`, `_history`, `_snapshot` 데이터는 보존됨

### MessageSerializer Format
`JSON → zlib compress → Fernet encrypt (optional)`
- collector-publisher와 동일한 포맷 공유
- `compression: "zlib"`, `encryption_enabled: false` 기본값

## Config Files
| File | Purpose |
|------|---------|
| `config/collector_mc_docker.yaml` | 운영용 MC Protocol (Docker) |
| `config/collector_modbus_docker.yaml` | 운영용 Modbus (Docker) |
| `config/tags_mc_docker.csv` | MC Protocol 태그 정의 |
| `config/tags_modbus_docker.csv` | Modbus 태그 정의 |
| `config/test/collector_debug.yaml` | 디버그/개발용 |
| `config/test/tags_debug.csv` | 디버그 태그 정의 (586 tags) |

### Tags CSV Format
```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,unit,string_length,word_length,format,description
1,D200,D,200,uint16,plc_data,1,0,,,,,,생산수량
6,D208,D,208,float32,plc_data,1,0,2,,,,, 불량률
```

## Docker
- Image: `neuro_collector_mc:mc-latest` (~66MB)
- Base: `python:3.11-slim` + multi-stage build
- 프로토콜별 선택적 코드 복사 (ARG PROTOCOL)
- Dockerfile: `build/collector/Dockerfile`

## Running
```bash
# 로컬 실행
python -m src.main -c config/test/collector_debug.yaml

# Docker
docker run -v ./config:/app/config neuro_collector_mc:mc-latest
```

## Dependencies
- `aio-pika`: RabbitMQ async client
- `pyyaml`: YAML parsing
- `cryptography`: Fernet encryption (optional)
- `python-dotenv`: env var management

## Performance (1 PLC, 1초 주기)
- ~262 tags/sec (262 active tags × 1초)
- ~35MB RAM
- ~168 bytes/row average

## Build & Deploy (JEM)

### 빌드 스크립트
```bash
# 전체 빌드 (collector + publisher → ARM64 tar)
python build_deploy.py

# 개별 빌드
python build_deploy.py --collector
python build_deploy.py --publisher
```
- 출력: `deploy/jem/neuro_collector_mc.tar`, `deploy/jem/neuro_publisher.tar`
- 플랫폼: `linux/arm64` (라즈베리파이)

### 소스 수정 후 필수 작업
**소스 코드(`src/` 또는 `collector-publisher/src/`)를 수정한 경우 반드시 아래 두 가지를 수행할 것:**

1. **커밋**: 변경 내용을 커밋한다
2. **빌드**: `python build_deploy.py` 를 실행하여 배포용 tar 파일을 갱신한다

> 설정 파일(`deploy/jem/*.yaml`, `*.csv`, `*.sql`)만 변경한 경우에는 빌드 불필요. 커밋만 수행.

## Version Management

### 버전 규칙
- **Single Source of Truth**: `src/version.py`의 `APP_VERSION`이 유일한 버전 소스
- `src/__init__.py`, `pyproject.toml`, `deploy/metadata/collector.json`은 `version.py`를 참조하거나 동일한 값을 유지
- collector-publisher도 동일: `src/version.py` → `metadata.json`

### 코드 수정 시 버전 업데이트 절차
**코드(`src/`)를 수정한 경우 반드시:**
1. 해당 프로젝트의 `src/version.py`에서:
   - `APP_VERSION` 범프 (patch: 버그 수정, minor: 기능 추가, major: 호환성 변경)
   - `APP_BUILD_DATE` 오늘 날짜로 변경
   - `CHANGELOG`에 변경 내용 추가 (최신이 맨 위)
   - 변경된 모듈의 `MODULE_VERSIONS` 업데이트
2. `deploy/metadata/*.json`의 `version`도 동일하게 변경
3. 커밋 → 빌드 (`python build_deploy.py`)

### 현재 버전
| 프로젝트 | 버전 | 최종 빌드 |
|----------|------|-----------|
| simpleCollector | 0.3.0 | 2026-02-27 |
| collector-publisher | 0.2.0 | 2026-02-27 |

### simpleCollector Changelog
| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 0.3.0 | 2026-02-27 | MC/Modbus 부분 실패 허용, 지수 백오프, 재연결 캐시 초기화, Modbus coil/discrete/STRING |
| 0.2.3-beta | 2026-02-09 | MC Protocol VERBOSE 로깅, NaN/Inf 검증 |
| 0.2.0-beta | 2026-01-15 | 태그 설정 확장, 마스터 동기화, 성능 최적화 |
| 0.1.0 | 2025-12-01 | 초기 릴리스 (MC Protocol + RabbitMQ) |

### collector-publisher Changelog
| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 0.2.0 | 2026-02-27 | Extensions (history/snapshot), DB pool SELECT 1 헬스체크, master sync 트랜잭션 |
| 0.1.0 | 2026-02-10 | 초기 릴리스 (3-Queue, COPY, 그룹별 동적 테이블, 마스터 동기화) |

## Protocol Development
새 프로토콜 추가 시 `docs/PROTOCOL_DEVELOPMENT_GUIDE.md` 참고

## Git Workflow
- 개발 단계: 큰 변경 시 자동 commit + push
- Commit message: 한국어 OK, Co-Authored-By 포함
