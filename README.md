# NeuroForge Collector

PLC, 센서 등의 산업용 데이터를 수집하여 TimescaleDB에 저장하는 데이터 수집 프레임워크입니다.

## 주요 기능

- **다중 프로토콜 지원**: Modbus TCP/RTU, FENET, MC Protocol 등 (모듈식 확장)
- **유연한 수집 주기**: 하나의 PLC에서 다른 주기로 데이터 수집 (1초, 1분 등)
- **이벤트 기반 아키텍처**: 느슨하게 결합된 컴포넌트 간 통신
- **다중 발행 대상**: TimescaleDB, MQTT, RabbitMQ, JSON File
- **분산 아키텍처**: RabbitMQ 기반 Collector/Publisher 분리 지원
- **Docker 지원**: Docker Compose로 간편한 배포
- **분리된 로깅**: 수집 로그와 송신 로그를 별도 레벨로 관리

## 아키텍처

### 단일 모드 (Standalone)

```
┌──────────────┐     Event     ┌──────────────┐     Buffer    ┌──────────────┐
│   Collector  │ ───────────▶  │  Processor   │ ───────────▶  │  Publisher   │
│  (Raw Conn)  │  DATA_READY   │  (Parsing)   │  BUFFER_PUT   │ (DB/MQTT/RMQ)│
└──────────────┘               └──────────────┘               └──────────────┘
       │                              │                              │
       ▼                              ▼                              ▼
    Protocol                      Scaling                     TimescaleDB
  Modbus/FENET                  Transform                    MQTT / RabbitMQ
  MC Protocol
```

### 분산 모드 (Distributed)

```
[Collector-PLC1] ──┐                         ┌── DB Batch Insert (asyncpg)
[Collector-PLC2] ──┼── RabbitMQ (Topic) ─────┤
[Collector-PLCn] ──┘  exchange: plc.data      └── MQTT Forward (aiomqtt)
                      queue.db / queue.mqtt    collector-publisher (1개)
```

분산 모드에서는 각 Collector가 RabbitMQ로 데이터를 전송하고,
별도의 [collector-publisher](../collector-publisher) 서비스가 DB/MQTT로 발행합니다.

자세한 아키텍처 설명은 [ARCHITECTURE.md](docs/ARCHITECTURE.md)를 참조하세요.

## 빠른 시작

### 1. 환경 설정

```bash
# 저장소 클론
git clone https://github.com/your-org/neuroforge-collector.git
cd neuroforge-collector

# 환경 변수 설정
cp .env.example .env
# .env 파일을 편집하여 환경에 맞게 설정

# 설정 파일 확인/수정
vi config/collector.yaml
vi config/tags.csv
```

### 2. Docker로 실행

```bash
# 서비스 시작
docker-compose up -d

# 로그 확인
docker-compose logs -f collector

# 서비스 중지
docker-compose down
```

### 3. 로컬 실행 (개발용)

```bash
# 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 실행
python -m src.main --config config/collector.yaml
```

## 디렉토리 구조

```
simpleCollector/
├── config/                 # 설정 파일
│   ├── collector.yaml      # 메인 설정 (접속정보, 로그, 버퍼)
│   └── tags.csv            # 태그 정의
├── docs/                   # 문서
│   └── ARCHITECTURE.md     # 아키텍처 상세 설명
├── scripts/                # 스크립트
│   └── init-db.sql         # DB 초기화 SQL
├── src/                    # 소스 코드
│   ├── core/               # 핵심 컴포넌트
│   │   ├── interfaces.py   # 추상 인터페이스
│   │   ├── events.py       # 이벤트 시스템
│   │   ├── buffer.py       # 데이터 버퍼
│   │   └── config.py       # 설정 로더
│   ├── collectors/         # 수집기 모듈
│   │   └── base.py         # 기본 수집기
│   ├── processors/         # 처리기 모듈
│   │   └── base.py         # 기본 처리기
│   ├── publishers/         # 발행기 모듈
│   │   └── base.py         # 기본 발행기
│   ├── pipeline/           # 파이프라인 관리
│   │   └── manager.py      # 파이프라인 매니저
│   ├── utils/              # 유틸리티
│   │   └── logging.py      # 로깅 설정
│   └── main.py             # 메인 엔트리포인트
├── tests/                  # 테스트
├── docker-compose.yml      # Docker Compose 설정
├── Dockerfile              # Docker 이미지 빌드
├── requirements.txt        # Python 의존성
└── README.md
```

## 설정

### collector.yaml 주요 설정

```yaml
collector:
  plc_id: 1                          # PLC 식별자
  name: "PLC_1_Collector"
  protocol:
    type: "modbus"                   # 프로토콜 유형
    host: "192.168.1.100"
    port: 502
  collection_groups:                 # 수집 그룹 (다른 주기)
    - name: "1sec"
      interval_ms: 1000
    - name: "1min"
      interval_ms: 60000

publisher:
  database:
    enabled: true
    host: "localhost"
    port: 5432
    database: "collector"
  mqtt:
    enabled: false                   # MQTT 비활성화
  rabbitmq:
    enabled: false                   # 분산 모드 시 활성화
    host: "rabbitmq"
    exchange_name: "plc.data"
    compression: "zlib"

buffer:
  max_size: 10000                    # 버퍼 최대 크기
  batch_size: 100                    # 배치 크기

logging:
  level: "INFO"
  collection_level: "DEBUG"          # 수집 로그만 DEBUG
  publish_level: "INFO"
```

### tags.csv 태그 정의

```csv
tag_id,tag_name,address,data_type,scale,offset,unit,description,collection_group
1,Temperature_1,D0,float32,0.1,0,°C,온도 센서 1,1sec
2,Pressure_1,D4,float32,0.01,0,bar,압력 센서 1,1sec
3,Tank_Level,D16,float32,0.1,0,%,탱크 수위,1min
```

### 환경 변수

환경 변수로 설정을 오버라이드할 수 있습니다:

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `PLC_ID` | PLC 식별자 | 1 |
| `PLC_HOST` | PLC 호스트 | 192.168.1.100 |
| `DB_HOST` | 데이터베이스 호스트 | localhost |
| `DB_PASSWORD` | 데이터베이스 비밀번호 | - |
| `RABBITMQ_HOST` | RabbitMQ 호스트 | localhost |
| `RABBITMQ_USER` | RabbitMQ 사용자 | guest |
| `RABBITMQ_PASSWORD` | RabbitMQ 비밀번호 | guest |
| `LOG_LEVEL` | 로그 레벨 | INFO |

## 모듈 확장

### 새 프로토콜 모듈 추가

```python
# src/collectors/my_protocol.py
from src.collectors.base import BaseCollector
from src.core.interfaces import CollectedData

class MyProtocolCollector(BaseCollector):
    async def _do_connect(self) -> bool:
        # 연결 로직 구현
        self._client = MyProtocolClient(self._host, self._port)
        return await self._client.connect()

    async def _do_disconnect(self) -> None:
        await self._client.disconnect()

    async def _do_collect(self, group: str) -> CollectedData:
        tags = self._tags.get(group, [])
        raw_data = await self._client.read(tags)
        return CollectedData(
            source_time=datetime.now(),
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=raw_data,
            collection_group=group,
        )
```

### 새 발행기 모듈 추가

```python
# src/publishers/my_target.py
from src.publishers.base import BasePublisher

class MyTargetPublisher(BasePublisher):
    async def _do_connect(self) -> bool:
        # 연결 로직 구현
        return True

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        # 발행 로직 구현
        return True
```

## 데이터베이스 스키마

```sql
CREATE TABLE plc_data_integrated (
    source_time     TIMESTAMPTZ       NOT NULL,
    server_time     TIMESTAMPTZ       NOT NULL,
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    value           DOUBLE PRECISION  NOT NULL
);

-- TimescaleDB 하이퍼테이블
SELECT create_hypertable('plc_data_integrated', 'source_time');

-- 인덱스
CREATE INDEX idx_plc_time ON plc_data_integrated (plc_id, source_time DESC);
```

## 명령행 옵션

```bash
python -m src.main --help

Options:
  -c, --config PATH    설정 파일 경로 (기본: config/collector.yaml)
  -l, --log-level LVL  로그 레벨 (DEBUG, INFO, WARNING, ERROR)
  --dry-run            설정 검증만 수행하고 종료
  --version            버전 정보 출력
```

## 트러블슈팅

### 연결 오류

```
[ERROR] Connection failed: Connection refused
```

- PLC 호스트/포트 확인
- 방화벽 설정 확인
- PLC 전원 및 네트워크 상태 확인

### 버퍼 오버플로우

```
[WARNING] Buffer threshold reached: 8000/10000
```

- 버퍼 크기 증가 (`buffer.max_size`)
- 발행 주기 단축 (`publisher.publish_interval_ms`)
- 데이터베이스 연결 상태 확인

### 데이터베이스 연결 실패

```
[ERROR] Failed to connect to database
```

- TimescaleDB 컨테이너 상태 확인
- 접속 정보 확인 (호스트, 포트, 사용자, 비밀번호)
- 네트워크 연결 확인

## 라이선스

MIT License

## 기여

버그 리포트, 기능 요청, PR 환영합니다.
