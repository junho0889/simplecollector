# simpleCollector 프로젝트 개요

## 1. 왜 필요한가

### 제조 현장의 데이터 수집 문제

제조 현장의 PLC(Programmable Logic Controller)에는 생산수량, 불량률, 설비 온도, 알람 상태 등 수많은 데이터가 실시간으로 쌓인다. 하지만 이 데이터를 활용하려면 몇 가지 근본적인 문제가 있다.

**1) PLC는 데이터를 밖으로 보내지 않는다**

PLC는 설비를 제어하는 장치이지, 데이터를 저장하거나 외부에 전송하는 장치가 아니다. PLC 내부 메모리(D 레지스터, M 릴레이 등)에 값이 있지만, 누군가 능동적으로 읽어가지 않으면 그 데이터는 사라진다.

**2) 프로토콜이 제각각이다**

미쓰비시 PLC는 MC Protocol, 지멘스는 S7, LS산전은 XGT 등 제조사마다 통신 프로토콜이 다르다. 같은 "D200 레지스터의 값을 읽어라"를 요청하더라도 프로토콜마다 TCP 프레임 구조가 완전히 다르다.

**3) 수집 주기와 방식이 태그마다 다르다**

- 생산수량: 1~2초마다 항상 수집 (polling)
- 알람: 값이 변했을 때만 기록 (on_change)
- 설비 로그: 5초마다 수집 (polling)

하나의 PLC에서도 태그의 성격에 따라 수집 전략이 달라야 한다.

**4) 데이터를 DB에 바로 넣으면 병목이 생긴다**

PLC에서 1초에 수백 개 태그를 읽어서 직접 DB에 INSERT하면, DB 연결 지연이나 장애 시 수집 자체가 멈춘다. 수집과 저장은 분리되어야 한다.

### simpleCollector가 해결하는 것

| 문제 | 해결 방법 |
|------|-----------|
| PLC가 데이터를 안 보냄 | 능동적으로 PLC 메모리를 주기적으로 읽어감 |
| 프로토콜이 제각각 | 프로토콜 추상화 계층 (MC Protocol, Modbus 등 플러그인 구조) |
| 태그별 수집 전략이 다름 | Collection Group으로 그룹별 주기/모드 분리 |
| DB 직접 연결 시 병목 | RabbitMQ 메시지 큐로 수집과 저장을 완전 분리 |

---

## 2. 개발 배경

### 기존 방식의 한계

초기에는 HMI(Human Machine Interface) 소프트웨어나 상용 SCADA 시스템을 통해 PLC 데이터를 수집했다. 하지만 다음과 같은 한계가 있었다.

- **상용 SCADA 라이선스 비용**: 태그 수에 비례해서 라이선스 비용이 증가한다. 수백~수천 태그를 수집하면 비용이 급격히 올라간다.
- **유연성 부족**: 수집 주기, 데이터 변환, 발행 대상을 자유롭게 바꾸기 어렵다. 설정 하나 바꾸려면 SCADA 화면을 열어서 수작업해야 한다.
- **확장성**: PLC가 10대, 20대로 늘어나면 SCADA 하나로 감당하기 힘들다. PLC별로 독립적인 수집기를 배포하기 어렵다.
- **데이터 파이프라인 통합**: 수집한 데이터를 TimescaleDB, MQTT, 다른 시스템에 동시에 보내는 파이프라인을 구축하기 어렵다.

### 설계 목표

이러한 한계를 해결하기 위해 simpleCollector를 직접 개발했다. 핵심 설계 목표는 다음과 같다.

1. **설정만으로 배포**: 코드 수정 없이 YAML + CSV 파일만 바꾸면 다른 PLC/사이트에 배포 가능
2. **경량 컨테이너**: Docker 이미지 66MB, 메모리 35MB로 저사양 PC에서도 동작
3. **프로토콜 확장**: 새로운 PLC 프로토콜은 Collector/Processor만 추가하면 나머지는 재사용
4. **읽기 전용 안전성**: PLC에 쓰기 명령을 절대 보내지 않음 (BATCH_READ만 구현)
5. **수집-저장 분리**: RabbitMQ를 사이에 두고, 수집기와 저장기가 독립적으로 동작

### 전체 시스템에서의 위치

```
                    simpleCollector (이 프로젝트)
                    ┌─────────────────────────┐
[PLC] ←TCP/IP→     │ Collector → Processor   │
                    │           → Buffer      │
                    │           → Publisher    │ →AMQP→ [RabbitMQ]
                    └─────────────────────────┘
                                                         │
                                                         ▼
                                                  [collector-publisher]
                                                         │
                                              ┌──────────┴──────────┐
                                              ▼                     ▼
                                        [TimescaleDB]           [MQTT Broker]

                    [CollectorHub Agent] ← Docker API로 위 컨테이너들을 관리
```

| 프로젝트 | 역할 |
|----------|------|
| **simpleCollector** (이 프로젝트) | PLC에서 데이터를 읽어 RabbitMQ로 발행 |
| collector-publisher | RabbitMQ에서 데이터를 받아 TimescaleDB에 저장 + MQTT 전달 |
| CollectorHub | 관리 플랫폼 (Agent가 Docker 컨테이너를 원격 관리) |

---

## 3. 어떻게 동작하는가

### 3.1 전체 데이터 흐름

```
PLC 메모리          simpleCollector 내부                          외부
─────────────       ──────────────────────────────────           ──────────
D200 = 1500         ┌─────────┐    CollectedData (raw bytes)
D216 = 30611        │Collector│──────────────────────┐
M100 = 1            └─────────┘                      ▼
                                                ┌─────────┐    ProcessedData
                                                │Processor│──────────┐
                                                └─────────┘          ▼
                                                                ┌─────────┐
                                                                │ Buffer  │
                                                                └────┬────┘
                                                                     ▼
                                                                ┌─────────┐   JSON→zlib→AMQP
                                                                │Publisher│──────────────→ [RabbitMQ]
                                                                └─────────┘
```

**단계별 설명:**

| 단계 | 컴포넌트 | 하는 일 |
|------|----------|---------|
| 1 | **Collector** | PLC에 TCP 연결 → 바이너리 프레임으로 레지스터 읽기 요청 → 응답 바이트를 `CollectedData`로 포장 |
| 2 | **Processor** | raw 바이트를 태그 정의에 따라 파싱 (uint16, float32, bool 등) → 스케일링 적용 → `ProcessedData`로 변환 |
| 3 | **Buffer** | ProcessedData를 비동기 큐에 임시 저장 (최대 10,000건). Publisher가 가져갈 때까지 보관 |
| 4 | **Publisher** | Buffer에서 배치 단위로 꺼내 → JSON 직렬화 → zlib 압축 → (선택) Fernet 암호화 → RabbitMQ에 발행 |

### 3.2 Collector: PLC 데이터 읽기

Collector는 PLC에 TCP 소켓으로 연결하여, 설정된 주기마다 메모리 영역을 읽는다.

**읽기 최적화 (Read Group)**

태그가 262개라고 해서 TCP 요청을 262번 보내지 않는다. 연속된 메모리 주소를 하나의 배치 읽기로 묶는다.

```
태그 목록: D200, D202, D210, D800, D810

주소 정렬 후 그룹핑 (max_address_gap=200):
  Group 1: D200~D212 → 1번의 TCP 요청으로 13워드 읽기
  Group 2: D800~D812 → 1번의 TCP 요청으로 13워드 읽기

결과: 5개 태그를 2번의 TCP 통신으로 완료
```

실제 운영에서 262개 태그는 보통 5~10번의 TCP 요청으로 처리된다.

**지원 프로토콜**

| 프로토콜 | 대상 PLC | 프레임 | 파일 위치 |
|----------|----------|--------|-----------|
| MC Protocol | 미쓰비시 iQ-R, Q, L, iQ-F | Binary 3E/4E | `src/collectors/mc_protocol/` |
| Modbus | 범용 (TCP/RTU/RTU-over-TCP) | Modbus TCP | `src/collectors/modbus/` |

두 프로토콜 모두 외부 라이브러리 없이 순수 Python(`asyncio.open_connection` + `struct`)으로 구현되어 있다.

### 3.3 Processor: 데이터 변환

Collector가 읽어온 raw 바이트를 실제 의미 있는 값으로 변환한다.

**데이터 타입 변환**

| 타입 | 크기 | 예시 |
|------|------|------|
| BOOL | 1bit | M100 → `True`/`False` |
| UINT16 | 2bytes | D200 = 0x05DC → `1500` |
| INT16 | 2bytes | 부호 있는 정수 |
| UINT32 | 4bytes (2워드) | D216~D217 → `30611` |
| INT32 | 4bytes (2워드) | 부호 있는 32비트 정수 |
| FLOAT32 | 4bytes (2워드) | IEEE 754 부동소수점 |
| FLOAT64 | 8bytes (4워드) | 64비트 부동소수점 |
| STRING | N워드 | D900~D909 → `"MODEL_A"` |

**스케일링 (고정소수점 변환)**

PLC에서 소수점을 표현할 수 없는 경우, 정수에 10을 곱해서 저장하는 관례가 있다. 예를 들어 온도 30.61도를 PLC에는 `3061`로 저장한다.

```python
# 태그 설정: scale=1.0, offset=0.0, decimals=2
# PLC raw 값: 3061

scaled = (3061 * 1.0) + 0.0     # = 3061
scaled = 3061 / (10 ** 2)        # = 30.61   ← decimals=2이므로 100으로 나눔
scaled = round(30.61, 2)         # = 30.61
```

> **주의**: `decimals`는 단순 반올림 자릿수가 아니라, `÷10^decimals` 변환이다. PLC/HMI 업계 표준 관례.

**출력 타입 라우팅**

변환된 값은 DB 컬럼에 맞게 분류된다:

| 원본 타입 | 출력 필드 | 이유 |
|-----------|-----------|------|
| BOOL | `v_bool` | boolean |
| INT16, UINT16, INT32 | `v_int` | PostgreSQL INTEGER 범위 내 |
| UINT32 | `v_bigint` | UINT32 최대값(42억)이 INTEGER 범위 초과 |
| FLOAT32, FLOAT64 | `v_float` | 부동소수점 |
| STRING | `v_text` | 문자열 |
| scale이나 decimals 적용된 정수 | `v_float` | 스케일링 결과는 항상 float |

### 3.4 Collection Group: 태그별 수집 전략

태그를 그룹으로 나눠서 각 그룹마다 다른 주기와 방식으로 수집한다. 각 그룹은 독립된 asyncio Task로 동작한다.

```yaml
collection_groups:
  - name: "fast"          # 실시간 데이터
    interval_ms: 2000     # 2초마다
    mode: "polling"       # 항상 전체 값 발행

  - name: "alm"           # 알람
    interval_ms: 1000     # 1초마다 체크
    mode: "on_change"     # 값이 변한 태그만 발행
    deadband: 0

  - name: "log"           # 설비 로그
    interval_ms: 5000     # 5초마다
    mode: "polling"
```

- **polling**: 매 주기마다 모든 태그 값을 발행
- **on_change**: 이전 값과 비교해서 변한 태그만 발행 (deadband로 미세 변동 무시 가능)

### 3.5 Publisher: RabbitMQ 발행

처리된 데이터를 RabbitMQ 메시지로 발행한다.

**직렬화 파이프라인**

```
List[ProcessedData.to_dict()]
        │
        ▼
   json.dumps()              ← JSON 문자열로 변환
        │
        ▼
   zlib.compress(level=6)    ← 압축 (기본값)
        │
        ▼
   Fernet.encrypt()          ← 암호화 (선택, 기본 비활성)
        │
        ▼
   AMQP Message body         → RabbitMQ Topic Exchange "plc.data"
                                routing_key: "plc.{plc_id}.data"
```

**메시지 속성:**
- `delivery_mode=2` (persistent): RabbitMQ 재시작해도 메시지 유지
- `content_type: application/json`
- 헤더에 `compression`, `encrypted`, `plc_id`, `batch_count` 포함

**장애 대응:**
- Publisher 실패 시 → 데이터를 Buffer 앞쪽에 되돌림 → 재시도
- RabbitMQ 연결 끊김 → `aio_pika.connect_robust()`가 자동 재연결
- 프로그램 종료 시 → Buffer의 미발행 데이터를 pickle로 디스크에 저장 → 재시작 시 복원

### 3.6 Pipeline: 전체 조립

하나의 PLC당 하나의 Pipeline이 생성된다.

```
Pipeline (PLC 1대에 대응)
├── Collector       PLC 연결 + 데이터 읽기
├── Processor       바이트 → 타입 변환 + 스케일링
├── Buffer          asyncio 큐 (최대 10,000건)
└── Publisher       Buffer → 직렬화 → RabbitMQ
```

**시작 순서**: Processor → Publisher → Collector (Processor가 준비된 후 데이터를 받아야 함)
**종료 순서**: Collector → Processor → Publisher → Buffer 저장

PipelineManager가 여러 Pipeline을 관리하며, SIGTERM/SIGINT 시그널을 받으면 전체를 안전하게 종료한다.

---

## 4. 설정 파일 가이드

### 4.1 필요한 파일

| 파일 | 역할 | 예시 |
|------|------|------|
| collector YAML | PLC 접속 정보, 수집 그룹, RabbitMQ 설정 | `config/collector_mc_docker.yaml` |
| tags CSV | 수집할 태그 목록 (메모리 주소, 타입, 스케일링) | `config/tags_mc_docker.csv` |

### 4.2 YAML 구조

```yaml
collector:
  plc_id: 1                           # PLC 고유 ID (DB에서 식별용)
  name: "MC_Docker"                    # 수집기 이름 (로그에 표시)
  tags_file: "config/tags.csv"         # 태그 CSV 경로

  protocol:
    type: "mc_protocol"                # 프로토콜 종류
    host: "192.168.0.1"                # PLC IP
    port: 5007                         # PLC 포트
    timeout_ms: 5000                   # TCP 타임아웃
    reconnect_interval_ms: 3000        # 재연결 간격

    extra:                             # 프로토콜별 추가 설정
      plc_series: "iq-r"               # iQ-R, Q, L, iQ-F
      frame_type: "binary_3e"          # binary_3e 또는 binary_4e
      max_address_gap: 200             # 읽기 그룹 병합 간격

  collection_groups:                   # 수집 그룹 정의
    - name: "fast"
      interval_ms: 2000
    - name: "alm"
      interval_ms: 1000
      mode: "on_change"
    - name: "log"
      interval_ms: 5000

buffer:
  max_size: 10000                      # 버퍼 최대 크기

publisher:
  batch_size: 1100                     # 한 번에 발행할 데이터 수
  rabbitmq:
    enabled: true
    host: "rabbitmq"                   # RabbitMQ 호스트
    port: 5672
    username: "admin"
    password: "admin"
    exchange_name: "plc.data"          # Topic Exchange 이름
    compression: "zlib"                # 압축 방식

logging:
  level: "INFO"                        # 로그 레벨
```

환경변수 치환을 지원한다: `host: "${RABBITMQ_HOST:localhost}"`

### 4.3 Tags CSV 형식

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
1,Current_Model,D,900,string,fast,1.0,0.0,,10,,,현재 모델
2,Cycle_Time,D,216,uint32,fast,0.1,0.0,1,,,,CYCLE TIME
100,ALM_EMG_Stop,M,100,bool,alm,1,0,,,,,비상정지 알람
200,LOG_Temperature,D,500,float32,log,0.1,0.0,1,,,℃,설비 온도
```

| 필드 | 필수 | 설명 |
|------|------|------|
| tag_id | O | 고유 숫자 ID |
| tag_name | O | 태그 이름 |
| memory | O | 메모리 영역 (D, M, W, X, Y 등) |
| address | O | 메모리 주소 (숫자) |
| data_type | O | 데이터 타입 (bool, uint16, int16, uint32, int32, float32, float64, string) |
| collection_group | O | 수집 그룹 이름 (YAML의 collection_groups.name과 일치) |
| scale | - | 스케일 계수 (기본 1.0) |
| offset | - | 오프셋 (기본 0.0) |
| decimals | - | 고정소수점 자릿수 (÷10^decimals 변환) |
| word_length | - | 문자열 읽기 워드 수 (string 타입 필수) |
| format | - | 출력 변환 포맷 (예: "float32" → 정수를 float로 변환) |
| unit | - | 단위 (℃, MPa 등) |
| description | - | 설명 |

---

## 5. 실행 방법

```bash
# 로컬 개발
python -m src.main -c config/test/collector_debug.yaml

# 설정 검증만 (실제 연결 안 함)
python -m src.main -c config/test/collector_debug.yaml --dry-run

# 데모 모드 (PLC 없이 랜덤 데이터)
python -m src.main --demo

# Docker
docker build --build-arg PROTOCOL=mc_protocol -f build/collector/Dockerfile -t neuro_collector_mc:mc-latest .
docker run -d -v ./config:/app/config neuro_collector_mc:mc-latest
```

---

## 6. 디렉토리 구조

```
simpleCollector/
├── src/
│   ├── main.py                      # 엔트리포인트
│   ├── version.py                   # 버전 정보
│   ├── core/
│   │   ├── config.py                # YAML+CSV 파싱 → dataclass
│   │   ├── interfaces.py            # TagDefinition, CollectedData, ProcessedData
│   │   ├── buffer.py                # 비동기 데이터 버퍼
│   │   ├── events.py                # EventBus (컴포넌트 간 이벤트 전달)
│   │   └── registry.py              # ProtocolRegistry + PublisherRegistry
│   ├── collectors/
│   │   ├── base.py                  # BaseCollector (공통 로직)
│   │   ├── mc_protocol/             # 미쓰비시 MC Protocol
│   │   │   ├── collector.py         # TCP 연결, 프레임 빌드, 배치 읽기
│   │   │   └── processor.py         # MC 바이트 → 타입 변환
│   │   └── modbus/                  # Modbus TCP/RTU
│   │       ├── collector.py
│   │       └── processor.py
│   ├── processors/
│   │   └── base.py                  # BaseProcessor (스케일링, 타입 라우팅, on_change)
│   ├── publishers/
│   │   ├── base.py                  # BasePublisher (배치 발행, 재시도)
│   │   └── rabbitmq/
│   │       ├── publisher.py         # RabbitMQ AMQP 발행
│   │       └── serializer.py        # JSON → zlib → Fernet
│   ├── pipeline/
│   │   └── manager.py               # Pipeline + PipelineManager
│   └── utils/
│       ├── logging.py               # 로깅 설정 (VERBOSE 커스텀 레벨)
│       └── serializer.py            # 공용 직렬화 유틸
├── config/
│   ├── collector_mc_docker.yaml     # 운영 MC 설정
│   ├── collector_modbus_docker.yaml # 운영 Modbus 설정
│   ├── tags_mc_docker.csv           # 운영 MC 태그
│   ├── tags_modbus_docker.csv       # 운영 Modbus 태그
│   └── test/
│       ├── collector_debug.yaml     # 디버그 설정
│       └── tags_debug.csv           # 디버그 태그
├── build/
│   └── collector/
│       └── Dockerfile               # 멀티스테이지 빌드 (프로토콜 선택적 복사)
├── tests/
│   └── mc_protocol_test/            # MC Protocol 통합 테스트
├── docs/                            # 문서
├── requirements.txt                 # Python 의존성
└── CLAUDE.md                        # AI 어시스턴트 컨텍스트
```

---

## 7. 성능 참고

1대 PLC, 1초 주기, 262개 태그 기준:

| 항목 | 수치 |
|------|------|
| 처리량 | ~262 태그/초 |
| 메모리 | ~35 MB |
| 메시지 크기 | 평균 ~168 bytes/태그 |
| Docker 이미지 | ~66 MB |
| MC 배치 읽기 | 최대 960워드/요청 (3E), 1920워드/요청 (4E) |

10대 PLC 추정: ~630 MB RAM, ~107 GB/월 데이터 (8core/8GB/200GB SSD 권장)
