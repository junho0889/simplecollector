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

## Git Workflow
- 개발 단계: 큰 변경 시 자동 commit + push
- Commit message: 한국어 OK, Co-Authored-By 포함
