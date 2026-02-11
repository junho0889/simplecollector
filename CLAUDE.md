# CLAUDE.md - Project Instructions

## Project Overview
Industrial PLC data collection distributed system (3 repositories).

```
[Collector-PLC1..N] → RabbitMQ (topic: plc.data) → Publisher Service → DB (TimescaleDB) + MQTT
CollectorHub Agent manages containers via Docker API + RabbitMQ Management HTTP API
```

## Repositories
| Project | Path | Remote | Branch |
|---------|------|--------|--------|
| simpleCollector | `d:\4.source\simpleCollector` | `https://git.neurosense.synology.me/NASDAQ/simpleCollector.git` | `dev/junho.noh` |
| collectorhub | `d:\4.source\collectorhub` | `https://git.neurosense.synology.me/NASDAQ/collectorhub.git` | `dev/junho.noh` |
| collector-publisher | `d:\4.source\collector-publisher` | - | - |

## Code Style & Language
- Python 3.11+ (asyncio)
- Type hints required
- Docstrings for public APIs
- Korean comments OK

## simpleCollector
- PLC data collector using asyncio
- **Config**: Dataclasses in `src/core/config.py`, parsed from YAML by `ConfigLoader._parse_config()`
- **Publishers**: Extend `BasePublisher` with `_do_connect/_do_disconnect/_do_publish/_do_health_check`
- **Registry**: `ProtocolRegistry` + `PublisherRegistry` in `src/core/registry.py`
- **Modbus**: Pure Python (no pymodbus), supports TCP/RTU/RTU-over-TCP
- **Log level**: Custom VERBOSE (between DEBUG and INFO)
- **MessageSerializer**: JSON → zlib compress → Fernet encrypt (optional)

## collector-publisher
- RabbitMQ consumer → DB (asyncpg COPY) + MQTT (aiomqtt) publisher
- Shares `MessageSerializer` format with simpleCollector

## collectorhub
### Agent (`agent/`)
- FastAPI + pydantic-settings, env prefix `AGENT_`, env_file `.env`
- Docker client: `python-on-whales` (NOT docker-py)
- YAML: `ruamel.yaml` (preserves comments)
- Routers: `agent/routers/__init__.py`

### Web UI (`web/`)
- Framework: Reflex (Pure Python fullstack)
- Theme: Dark mode, accent=cyan, gray=slate, font=Noto Sans KR
- State: Inherits `BaseState(rx.State)` with `api_get/api_post/api_put/api_delete`
- Pages: Functional components, `rx.foreach`, `rx.cond`, `on_mount`
- Routing: `app.add_page(func, route, title)` in `collectorhub.py`
- Docker build required (Python 3.11, incompatible with 3.14)

## Git Workflow
- 개발 단계: 큰 변경 시 자동 commit + push (사용자 요청)
- Commit message: 한국어 OK, Co-Authored-By 포함
