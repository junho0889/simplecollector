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
- Image: `neuroforge_collector_mc:mc-latest` (~66MB)
- Base: `python:3.11-slim` + multi-stage build
- 프로토콜별 선택적 코드 복사 (ARG PROTOCOL)
- Dockerfile: `build/collector/Dockerfile`

## Running
```bash
# 로컬 실행
python -m src.main -c config/test/collector_debug.yaml

# Docker
docker run -v ./config:/app/config neuroforge_collector_mc:mc-latest
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
- 출력: `deploy/jem/neuroforge_collector_mc.tar`, `deploy/jem/neuroforge_publisher.tar`
- 플랫폼: `linux/arm64` (라즈베리파이)

### 소스 수정 후 필수 작업
**소스 코드(`src/` 또는 `collector-publisher/src/`)를 수정한 경우 반드시 아래 두 가지를 수행할 것:**

1. **커밋**: 변경 내용을 커밋한다
2. **빌드**: `python build_deploy.py` 를 실행하여 배포용 tar 파일을 갱신한다

> 설정 파일(`deploy/jem/*.yaml`, `*.csv`, `*.sql`)만 변경한 경우에는 빌드 불필요. 커밋만 수행.

### 이미지 레지스트리 업로드 (NCR)
**사용자가 빌드를 명시적으로 요청하면(예: "빌드해", "tar 만들어", "이미지 만들어"), 빌드 후 자동으로 NCR에 push한다.**

#### 기본 정보 (Cortex 최종 확정 — 2026-05-26)
- **레지스트리**: `neuroforge-max-registry.kr.ncr.ntruss.com`
- **경로 형식**: `<NCR>/neuroforge/{group}-{role}:v<semver>` + `:latest`
  - **namespace = `neuroforge/`** (단일, 통합)
  - **image = `{group}-{role}`** flat hyphen (group ∈ {edge, cortex})
  - **tag = `v<semver>` (v prefix 필수)** + `latest` 겹침
- **사전 인증**: `docker login neuroforge-max-registry.kr.ncr.ntruss.com` (Docker Desktop 자격증명 헬퍼에 저장됨)

#### ⚠️ 버전 관리 룰 (반드시 지킬 것)
1. **semver 태그는 immutable** — 같은 `:v<X.Y.Z>` 를 다른 binary 로 덮어쓰지 않는다.
   `push-to-ncr.sh` 가 `docker manifest inspect` 로 미리 검사해서 중복이면 ver 태그 push **자동 skip** (`:latest` 만 갱신). 코드 변경했는데 이 GUARD 가 뜨면 **APP_VERSION 범프부터** 다시.
2. **코드 변경 → APP_VERSION 범프 → 커밋 → 빌드 → push** 순서 엄수.
   - `src/version.py` 의 `APP_VERSION` 변경 + `APP_BUILD_DATE` 갱신 + `CHANGELOG` 항목 추가
   - `deploy/metadata/*.json` 의 `"version"` 도 동일하게
   - CLAUDE.md "현재 버전" 표 + Changelog 갱신
3. **커밋 메시지 필수 규약** — NCR push 전 마지막 커밋은 **반드시 한글로 변경점 명시** (`git log` / NCR 콘솔 / Cortex UI 에서 그대로 표시):
   - **subject (한 줄)**: `feat:`, `fix:`, `chore:` 접두어 + 한글 요약 (예: `feat: DB 로깅 (neuroforge_logs.collector_log) + LoggingConfig 통일`)
   - **body (여러 줄)**: 무엇이 바뀌었는지 구체적으로 — 추가/수정/제거된 파일·기능·환경변수·DDL 명시
   - **buildable change 인데 메시지가 모호하면 (`update`, `wip`, `tmp` 등) push 보류 후 메시지 보강** — Cortex / 다른 팀이 `git log` 만 보고 알 수 있어야 함
   - 예시 (좋음):
     ```
     feat: catalog auto-publish 컨벤션 — publisher 부팅 훅 추가

     - schema_meta + enum_meta + table_naming + queues 트랜잭션 UPSERT
     - catalog_publish_log 에 hash 변경 또는 30분 경과 시에만 INSERT
     - 실패 시 connect() 실패 → polling 시작 차단
     - PUBLISHER_INSTANCE_ID env / IMAGE_TAG / GIT_SHA 자동 주입
     ```
4. **커밋한 뒤에 빌드** — Dockerfile LABEL `org.opencontainers.image.revision` 가 `--build-arg GIT_SHA` 로 박힘. 커밋 전 빌드하면 옛 sha 라벨이 박혀 추적성 깨짐.
5. **range bump 가이드** (semver):
   - **patch** (z): 버그 수정, 메타/문서, 비호환 없는 내부 변경
   - **minor** (y): 새 기능, 호환 유지하는 인터페이스 추가 (env 신규 등)
   - **major** (x): 호환성 깨는 변경 (config 스키마/큐 contract 등)
6. **재빌드(같은 코드)로 재push 가능** — GUARD 가 manifest digest 같으면 skip 처리하므로 무해. 코드가 진짜 안 바뀌었다면 ver 태그 변경할 필요 없음.
7. **`:latest` 는 항상 mutable** — 매 push 마다 최신 sha 로 이동. 운영 게이트웨이는 가급적 `:v<ver>` 핀.

#### 우리 이미지 현재 버전
- `neuroforge/edge-collector-mc:v0.4.3` + `:latest`
- `neuroforge/edge-collector-ble:v0.4.3` + `:latest`
- `neuroforge/edge-publisher:v0.3.4` + `:latest`

#### 표준 흐름 (전체 빌드 + 푸시)
```bash
# 1. ARM64 빌드 — 로컬 docker daemon 에 직접 load (tar 출력 안 함, 2026-05-26 변경)
python deploy/jem_ble/build_deploy.py

# 2. NCR push (v<ver> + latest 양쪽 자동, GUARD: 같은 ver 중복 차단)
bash scripts/push-to-ncr.sh
```

#### push-to-ncr.sh 사용 패턴
```bash
bash scripts/push-to-ncr.sh                              # 3개 전부
bash scripts/push-to-ncr.sh edge-collector-mc            # 1개만
bash scripts/push-to-ncr.sh edge-collector-mc edge-publisher
NCR=test-registry.example.com bash scripts/push-to-ncr.sh   # 레지스트리 override
NS=neuroforge_staging         bash scripts/push-to-ncr.sh   # namespace override
```
- 로컬 docker 이미지에서 `<name>:<semver>` 태그를 찾아 자동 매핑 (없으면 skip + 메시지)
- `v` 접두사는 스크립트가 자동으로 붙임 (로컬 `0.4.2` → NCR `v0.4.2`)
- 빌드 안 한 채 "push만"도 가능 → 이미 load된 이미지가 있으면 그대로 푸시

#### tar 사본 — 더 이상 생성하지 않음 (deprecated 2026-05-26)
build_deploy.py 가 `--load` 로 로컬 daemon 에 직접 로드만 함. 더 이상:
- `deploy/jem/` `deploy/jem_ble/` `deploy/new_db_config/` 에 tar 안 생김
- `192.168.0.142:/.../collector_images/` 에 SCP 안 함

운영 배포는 **NCR pull 단독** (게이트웨이가 `docker pull <NCR>/neuroforge/edge-*:v<ver>`). 비상시 tar 가 필요하면 임시로 `docker save -o <file>.tar <image>:<tag>` 로 수동 추출.

#### 검증
```bash
# 푸시 직후 레지스트리에서 태그 확인 (인증 필요)
curl -u <user>:<pass> https://neuroforge-max-registry.kr.ncr.ntruss.com/v2/neuroforge/edge-collector-mc/tags/list

# 게이트웨이/다른 PC에서 pull
docker login neuroforge-max-registry.kr.ncr.ntruss.com   # 1회
docker pull neuroforge-max-registry.kr.ncr.ntruss.com/neuroforge/edge-collector-mc:v0.4.2
docker pull neuroforge-max-registry.kr.ncr.ntruss.com/neuroforge/edge-publisher:latest
```

#### 메타 자동 매핑 (이미 박힘)
- `src/version.py` 의 `APP_VERSION` → `build_deploy.py` 가 `--build-arg VERSION=...` 주입
- → Dockerfile `LABEL org.opencontainers.image.version` 박힘
- → push-to-ncr.sh 가 로컬 태그(`<name>:0.4.2`)에서 버전 파싱 → NCR `<name>:0.4.2` + `:latest` 자동
- → NCR 콘솔/Cortex 가 `org.opencontainers.image.{version,title,source,revision}` 자동 감지

#### 트러블슈팅
- `denied: access forbidden` / `unauthorized` → `docker login neuroforge-max-registry.kr.ncr.ntruss.com` 재실행
- `manifest invalid` → 로컬 이미지 미존재 — `docker load -i <tar>` 누락 확인
- `no local image: <name>:<version>` (push-to-ncr.sh) → build_deploy.py 안 돈 상태, 또는 docker load 누락

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
| simpleCollector | 0.4.8 | 2026-06-18 |
| collector-publisher | 0.3.11 | 2026-06-18 |

### simpleCollector Changelog
| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 0.4.8 | 2026-06-18 | feat: MQTT collector 추가 (`src/collectors/mqtt/`) — 브로커 구독(PUB/SUB) 수집기. BLE 스캐너 패턴: MqttSubscriber(aiomqtt)가 토픽별 최신 payload 캐시 → BaseCollector 폴링이 _do_collect로 읽고, MqttProcessor가 payload(JSON path/스칼라) 파싱 → BaseProcessor 스케일/라우팅. 태그 매핑 `tag.address="토픽#json_path"`(config/CSV 무수정), 구독은 protocol.extra.subscribe_topics/base_topic. registry에 'mqtt'(aiomqtt) 등록. cache_ttl stale 감지, 재연결은 _reconnect_loop. 샘플 config/test/collector_mqtt.yaml+tags_mqtt.csv |
| 0.4.7 | 2026-06-18 | feat: neuroforge_config enum_meta superset 통일 (worker팀 공존, CONFIG_SCHEMA_VERSION 1.0.0→1.1.0) — build_collector_capabilities enum_meta 키 table_name→scope, column_name→field. publish_catalog가 (scope,field,value)로 UPSERT + 자기 scope만 DELETE 후 재발행(라이브 worker/forwarder 행 보존). schema_meta는 schema_version 그대로(컬럼 무변경). DDL은 publisher 소유라 collector는 publish 경로만 정렬 |
| 0.4.6 | 2026-06-10 | fix: QA 감사(docs/QA_AUDIT_REPORT_2026-06-10.md) Critical 3건 + High 2건 — (C-1) RabbitMQ publisher 재연결 태스크를 start()에서 상시 기동: 운영 중 브로커 단절 1회로 영구 발행 중단 + 버퍼 drop_oldest 무음 손실되던 문제 해결, _do_connect 시 이전 연결 정리. (C-2) MC Protocol _receive_response 타임아웃/수신 오류 시 소켓 폐기 + state=ERROR — 지연 응답 프레임 오정렬로 엉뚱한 태그에 값 기록되던 무음 오염 차단. (C-3) Modbus TCP Transaction ID 검증(stale 응답 폐기) + TCP/RTU-over-TCP 타임아웃·RTU CRC 오류 시 소켓 폐기. (H-1) BaseProcessor가 metadata failed 검사 → 수집 실패 시 전 태그 quality_code=0 행 생성(통신이상↔무수집 구분). (H-2) MC _extract_bool 비트 디바이스 분기 — word_addr 충돌로 알람 오발생/미발생하던 문제 차단. 회귀 테스트 docs/regression_qa_*.py (36건 PASS) |
| 0.4.5 | 2026-05-27 | chore: multi-arch 이미지 — `build_deploy.py`가 `linux/amd64,linux/arm64` 동시 빌드 + buildx `--push`로 NCR에 manifest list 직접 push. ARM64 전용 이미지가 ubuntu(amd64)에서 pull fail 하던 이슈 해결. BLE collector도 통합 BUILDS. 옛 흐름은 `--legacy-load`로 유지. 런타임 무변경 |
| 0.4.4 | 2026-05-26 | chore: tar 출력 제거, NCR 단독 배포 — build_deploy.py가 `--load`로 로컬 daemon 직접 로드. 이후 흐름: build_deploy.py → scripts/push-to-ncr.sh. 런타임 무변경 |
| 0.4.3 | 2026-05-26 | feat: DB 로깅 — docker stdout=ERROR만, DEBUG/INFO/WARN/ERROR 전부 neuroforge_logs.collector_log 에 비동기 배치 INSERT(DbLogHandler). LoggingConfig 통일(stdout_min_level/db_*/subsystem_levels 신규). DSN: LOGS_DB_* → CONFIG_DB_* → DB_* 폴백 |
| 0.4.2 | 2026-05-25 | feat: catalog auto-publish — 부팅 훅 publish_catalog()가 schema_meta + collector enum_meta(device/protocol/data_type 16종/memory 13종 등)을 트랜잭션 UPSERT + catalog_publish_log에 hash 변경/30분 경과 시에만 INSERT. 실패 시 polling 블로킹 |
| 0.4.1 | 2026-05-25 | feat: Cortex 메타 연동 — 부팅 시 schema_meta에 collector 버전 UPSERT. config DB DDL에 enum_meta/table_naming/constraint_meta 추가(앱 enum·{group}_* 네이밍·운영 제약 노출) |
| 0.4.0 | 2026-05-22 | feat: config DB 로딩 경로 추가 (CONFIG_SOURCE=db) — YAML/CSV 대신 neuroforge_config 스키마(vw_collector/vw_device/vw_tag)에서 AppConfig+태그 구성. DB 접속 env(CONFIG_DB_*/DB_* 폴백), collector 식별 COLLECTOR_KEY, 미접속 시 영구 재시도. 기본 file 모드로 기존 동작 불변 |
| 0.3.6 | 2026-05-04 | fix: 운영 중 PLC 끊김 시 영구 재연결 보장 — _reconnect_loop 항상 실행, 백오프 30초 cap, 재연결 실패 ERROR throttle(60초), 복구 시 INFO. LOSS 로그 throttle(첫+30초+복구). MC Protocol writer NoneType race 가드 |
| 0.3.5 | 2026-04-22 | fix: POSIOT BLE profile 이중 스케일링 버그 — profile이 raw int만 반환하도록 변경 (CSV scale/offset/decimals 단일 경로), pressure 특수공식도 CSV 선형 스케일로 이전 |
| 0.3.4 | 2026-04-22 | BLE 예외 처리 보강 (scanner/byte_offset/orphan 디바이스 경고) |
| 0.3.3 | 2026-03-21 | BLE device_type 지원 + GitHub 미러 |
| 0.3.2 | 2026-03-05 | 비트 디바이스(M/X/Y) 워드 기반 읽기 전환 — 비트 읽기(0x0001) PLC 호환성 문제 해결 |
| 0.3.1 | 2026-03-05 | L 디바이스 워드 기반 비트 읽기 주소 버그 수정 |
| 0.3.0 | 2026-02-27 | MC/Modbus 부분 실패 허용, 지수 백오프, 재연결 캐시 초기화, Modbus coil/discrete/STRING |
| 0.2.3-beta | 2026-02-09 | MC Protocol VERBOSE 로깅, NaN/Inf 검증 |
| 0.2.0-beta | 2026-01-15 | 태그 설정 확장, 마스터 동기화, 성능 최적화 |
| 0.1.0 | 2025-12-01 | 초기 릴리스 (MC Protocol + RabbitMQ) |

### collector-publisher Changelog
| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 0.3.11 | 2026-06-18 | fix: schema_meta.version 타입 TEXT→INTEGER 정정 — worker가 INTEGER로 쓰는 계약버전을 0.3.10 superset DDL이 TEXT로 만들어 worker publish 타입에러 나던 것 해결(Cortex 252 실측). ALTER COLUMN version TYPE INTEGER USING version::integer 추가(기존 NULL 무손실). schema_version(TEXT) 무변경 |
| 0.3.10 | 2026-06-18 | feat: neuroforge_config enum_meta/schema_meta superset 통일 (worker/forwarder 공존, CONFIG_SCHEMA_VERSION 1.0.0→1.1.0) — enum_meta table_name→scope·column_name→field (PK scope,field,value), schema_meta에 worker 컬럼(version/binary_version/built_at/booted_at/updated_at) nullable 추가 + schema_version NOT NULL 완화. DDL 멱등 마이그레이션(rename DO블록 + ADD COLUMN IF NOT EXISTS). publish_catalog는 자기 scope만 DELETE 후 재발행. collector/publisher ↔ worker/forwarder 같은 config 스키마 동시 운영 가능 |
| 0.3.9 | 2026-06-10 | fix: QA 감사 Critical 3건 + High 1건 — (C-7) BufferedQueueConsumer deserialize 실패에 지수 백오프(최대 10초) + 로그 throttle(30초) + x-delivery-count 기준 5회 초과 시 nack(requeue=False): 포이즌 메시지 1건이 queue.db 전체를 무한 핫루프로 정지시키던 문제 해결. (C-8) connect() 풀 누수 차단 — create_pool 성공 후 후속 단계 실패 시 풀 close (5초 재시도마다 min_size개씩 누적 → Postgres max_connections 고갈 방지). (C-9) payload의 collection_group을 SQL 식별자로 쓰기 전 _safe_group_name() 검증 — 위반 레코드 skip+카운트 경고, 인젝션 표면/무음 데이터 증발 차단. (H-4) _upsert_group_latest 최종 실패 시 raise → nack 복원 — silent ack로 배치 유실/latest stale 되던 문제 해결. (M-1) compression_orderby 폴백 'source_time DESC'→'timestamp DESC' — yaml 키 생략 시 압축 정책 조용히 비활성되던 지뢰 제거 (스키마/저장 무변경) |
| 0.3.8 | 2026-06-04 | fix: alm_latest UPSERT 데드락 — `_upsert_group_latest`가 unnest 다중행 UPSERT를 행 순서 고정 없이 날려, publisher 2개(또는 큰 pool)가 같은 `{group}_latest`를 동시 갱신 시 서로 다른 락 순서로 ShareLock 교착. unnest 배열을 `(device_id, tag_id)` 정렬해 모든 트랜잭션이 동일 순서로 락 획득 → 데드락 원천 차단. 추가로 잔여 데드락 시 victim 배치를 버리지 않고 지터 백오프로 최대 3회 재시도(DeadlockDetectedError 한정) → alm_latest stale/history 누락 방지. 동시성/pool_size 무관 안전 |
| 0.3.7 | 2026-05-27 | chore: multi-arch 이미지 (linux/amd64 + linux/arm64) — simpleCollector 의 build_deploy.py 가 buildx multi-platform + --push 로 NCR 에 manifest list 직접 push. v0.3.6 amd64 미지원 이슈 해결. 런타임 무변경 |
| 0.3.6 | 2026-05-27 | feat: DB 부하 회복력 — (1) asyncpg `command_timeout` yaml 노출 (`database.command_timeout`, 기본 30s) (2) 그룹별 COPY를 `asyncio.gather` 병렬화 → pool_size 활용 (3) `_insert_copy_to_group` partial split-on-fail — COPY 실패 시 batch 절반으로 재귀 재시도 (4) BufferedQueueConsumer `flush_size/flush_interval/backoff_cap_sec` yaml 노출 (`rabbitmq.db_flush_*`). 정상 운영 동일, IO 사건(백필/외부 polling) 회복력 크게 향상 |
| 0.3.5 | 2026-05-26 | chore: build_deploy.py tar 출력 제거 알림 — 운영 흐름: build → push-to-ncr.sh → NCR. GIT_SHA 라벨 갱신 위해 patch 범프 |
| 0.3.4 | 2026-05-26 | feat: DB 로깅 + LoggingConfig 통일 — publisher LoggingConfig 를 collector 와 동일 구조로 확장(19+α 필드). DbLogHandler 가 neuroforge_logs.publisher_log 에 비동기 배치 INSERT. apply_logs_schema() 부팅 시 logs 스키마/hypertable/정책 IF NOT EXISTS 적용 |
| 0.3.3 | 2026-05-25 | feat: 메타 주도 폼(publisher 2차) — build_publisher_capabilities()에 contract_version + config_columns(22컬럼, 5그룹) 추가. publisher_group/snapshot_trigger/settings 컬럼 스키마를 Cortex 폼이 동적 렌더링 가능 |
| 0.3.2 | 2026-05-25 | feat: catalog auto-publish — DDL에 catalog_publish_log + queue_meta 추가, 메타 시드를 DDL에서 분리(컴포넌트 publish). publish_catalog()가 publisher 메타(enum/naming/constraint/queues) 트랜잭션 UPSERT + 변경 시 INSERT. 실패 시 부팅 블로킹 |
| 0.3.1 | 2026-05-25 | feat: Cortex 메타 연동 — DDL에 schema_meta/enum_meta/table_naming/constraint_meta 추가, 부팅 시 publisher 버전+ddl_sha UPSERT. worker source_table=`{group}_integrated` 규칙을 table_naming으로 노출 |
| 0.3.0 | 2026-05-22 | feat: config DB 로딩 경로 추가 (CONFIG_SOURCE=db) — ConfigDbReader가 부팅 시 neuroforge_config 스키마 DDL 보장, master_sync가 vw_device/vw_tag에서 *_master 투영(sync_from_reader), 그룹/정책/extensions는 vw_publisher_group+publisher_settings에서 로드. config 소스/데이터 타깃 연결 분리(CONFIG_DB_* 폴백). 기본 file 모드로 기존 동작 불변 |
| 0.2.3 | 2026-04-30 | fix: DB 다운 시 로그 폭주 / 즉시 재전달 루프 — QueueConsumer 연속 실패 지수 백오프(최대 10초) + 로그 throttle(30초), 'pool unavailable' 중복 ERROR 로그 제거 |
| 0.2.2 | 2026-04-22 | {group}_integrated 압축/보관 정책 remove→add 스왑 — YAML 변경값이 재시작만으로 반영 |
| 0.2.1 | 2026-03-10 | DB 재연결 지수 백오프 (60초 cap, asyncio.Lock), queue.monitor 무제한 재시도, pool acquire timeout 10초 |
| 0.2.0 | 2026-02-27 | Extensions (history/snapshot), DB pool SELECT 1 헬스체크, master sync 트랜잭션 |
| 0.1.0 | 2026-02-10 | 초기 릴리스 (3-Queue, COPY, 그룹별 동적 테이블, 마스터 동기화) |

## Protocol Development
새 프로토콜 추가 시 `docs/PROTOCOL_DEVELOPMENT_GUIDE.md` 참고

## Git Workflow
- 개발 단계: 큰 변경 시 자동 commit + push
- Commit message: 한국어 OK, Co-Authored-By 포함
