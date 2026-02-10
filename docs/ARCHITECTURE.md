# Simple Collector 아키텍처

이 문서는 Simple Collector의 전체 아키텍처와 설계 결정 사항을 설명합니다.

## 목차

1. [개요](#개요)
2. [시스템 아키텍처](#시스템-아키텍처)
3. [핵심 컴포넌트](#핵심-컴포넌트)
4. [데이터 흐름](#데이터-흐름)
5. [스레딩 모델](#스레딩-모델)
6. [이벤트 시스템](#이벤트-시스템)
7. [모듈 확장](#모듈-확장)
8. [설계 결정](#설계-결정)

## 개요

Simple Collector는 산업용 데이터 수집을 위한 파이썬 기반 프레임워크입니다.
PLC, 센서 등에서 데이터를 수집하고 TimescaleDB나 MQTT로 전송합니다.

### 핵심 설계 원칙

1. **모듈화**: 프로토콜과 발행 대상을 플러그인처럼 추가/제거
2. **이벤트 기반**: 컴포넌트 간 느슨한 결합
3. **비동기**: asyncio 기반 고성능 처리
4. **유연한 스케줄링**: 하나의 연결에서 다양한 수집 주기 지원
5. **안정성**: 자동 재연결, 버퍼링, 재시도

## 시스템 아키텍처

### 전체 구조

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Docker Container                                │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                        PipelineManager                                │   │
│  │                                                                       │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │   │
│  │  │                     Pipeline (per PLC)                          │ │   │
│  │  │                                                                  │ │   │
│  │  │  ┌───────────┐    ┌───────────┐    ┌───────────┐    ┌────────┐│ │   │
│  │  │  │ Collector │───▶│ Processor │───▶│  Buffer   │───▶│Publisher││ │   │
│  │  │  │           │    │           │    │           │    │        ││ │   │
│  │  │  │ ┌───────┐ │    │ ┌───────┐ │    │           │    │┌──────┐││ │   │
│  │  │  │ │1sec   │ │    │ │ Parse │ │    │ ThreadSafe│    ││  DB  │││ │   │
│  │  │  │ │Task   │ │    │ │ Scale │ │    │   Queue   │    │└──────┘││ │   │
│  │  │  │ ├───────┤ │    │ └───────┘ │    │           │    │┌──────┐││ │   │
│  │  │  │ │1min   │ │    │           │    │           │    ││ MQTT │││ │   │
│  │  │  │ │Task   │ │    │           │    │           │    │└──────┘││ │   │
│  │  │  │ └───────┘ │    │           │    │           │    │        ││ │   │
│  │  │  └───────────┘    └───────────┘    └───────────┘    └────────┘│ │   │
│  │  └─────────────────────────────────────────────────────────────────┘ │   │
│  │                                                                       │   │
│  │  ┌─────────────────────────────────────────────────────────────────┐ │   │
│  │  │                       Shared EventBus                            │ │   │
│  │  └─────────────────────────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                │                                       │
                ▼                                       ▼
         ┌──────────┐                           ┌──────────────┐
         │   PLC    │                           │  TimescaleDB │
         │ (Modbus) │                           │    / MQTT    │
         └──────────┘                           └──────────────┘
```

### 1:1 파이프라인 구조

각 PLC는 독립적인 파이프라인(컨테이너)으로 관리됩니다:

```
PLC 1 ──▶ [Container 1: Collector → Processor → Publisher] ──▶ DB
PLC 2 ──▶ [Container 2: Collector → Processor → Publisher] ──▶ DB
PLC 3 ──▶ [Container 3: Collector → Processor → Publisher] ──▶ DB
```

## 핵심 컴포넌트

### 1. Collector (수집기)

**역할**: 프로토콜별 데이터 수집

```
┌─────────────────────────────────────────┐
│               Collector                  │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │     Collection Groups            │   │
│  │  ┌─────────┐  ┌─────────┐       │   │
│  │  │  1sec   │  │  1min   │  ...  │   │
│  │  │  Task   │  │  Task   │       │   │
│  │  └────┬────┘  └────┬────┘       │   │
│  │       │            │            │   │
│  │       ▼            ▼            │   │
│  │    collect()    collect()       │   │
│  └─────────────────────────────────┘   │
│                    │                    │
│                    ▼                    │
│  ┌─────────────────────────────────┐   │
│  │     Protocol Implementation      │   │
│  │  (Modbus/FENET/MC Protocol)     │   │
│  └─────────────────────────────────┘   │
└─────────────────────────────────────────┘
```

**주요 기능**:
- 그룹별 독립 수집 태스크 (다른 주기)
- 자동 재연결
- 수집 완료 시 이벤트 발생

**인터페이스**:
```python
class ICollector(ABC):
    @abstractmethod
    async def connect(self) -> bool: ...
    @abstractmethod
    async def disconnect(self) -> None: ...
    @abstractmethod
    async def collect(self, group: str) -> CollectedData: ...
```

### 2. Processor (처리기)

**역할**: Raw 데이터 파싱 및 스케일링

```
┌────────────────────────────────────────┐
│              Processor                  │
│                                        │
│  EVENT: DATA_COLLECTED                 │
│          │                             │
│          ▼                             │
│  ┌────────────────────────────────┐   │
│  │     Parse Raw Data              │   │
│  │  (Protocol-specific decoding)   │   │
│  └────────────────────────────────┘   │
│          │                             │
│          ▼                             │
│  ┌────────────────────────────────┐   │
│  │     Apply Scaling               │   │
│  │  value = (raw * scale) + offset │   │
│  └────────────────────────────────┘   │
│          │                             │
│          ▼                             │
│  ┌────────────────────────────────┐   │
│  │     Buffer.put(ProcessedData)   │   │
│  └────────────────────────────────┘   │
└────────────────────────────────────────┘
```

**주요 기능**:
- 이벤트 기반 동작 (DATA_COLLECTED 구독)
- 프로토콜별 파싱 로직
- 스케일링 자동 적용
- 버퍼에 데이터 저장

### 3. Buffer (버퍼)

**역할**: 스레드 안전한 데이터 임시 저장

```
┌─────────────────────────────────────────┐
│                Buffer                    │
│                                         │
│  ┌─────────────────────────────────┐   │
│  │          FIFO Queue              │   │
│  │  [Data1][Data2][Data3]...[DataN] │   │
│  └─────────────────────────────────┘   │
│                                         │
│  Features:                              │
│  • Thread-safe (asyncio.Lock)          │
│  • Overflow handling (drop_oldest)     │
│  • Batch extraction                    │
│  • Persistence on shutdown             │
│  • Threshold events                    │
└─────────────────────────────────────────┘
```

**주요 기능**:
- FIFO 순서 보장
- 오버플로우 정책 (오래된 데이터 삭제/새 데이터 거부)
- 배치 추출
- 실패 데이터 되돌리기 (put_front)

### 4. Publisher (발행기)

**역할**: 외부 시스템으로 데이터 전송

```
┌────────────────────────────────────────┐
│              Publisher                  │
│                                        │
│  ┌────────────────────────────────┐   │
│  │       Publish Loop              │   │
│  │                                 │   │
│  │  while running:                 │   │
│  │    batch = buffer.get_batch()   │   │
│  │    success = publish(batch)     │   │
│  │    if not success:              │   │
│  │      buffer.put_front(batch)    │   │
│  └────────────────────────────────┘   │
│          │                             │
│          ▼                             │
│  ┌────────────────────────────────┐   │
│  │    Target Implementation        │   │
│  │  • DatabasePublisher            │   │
│  │  • MqttPublisher                │   │
│  └────────────────────────────────┘   │
└────────────────────────────────────────┘
```

**주요 기능**:
- 버퍼 모니터링
- 배치 전송
- 재시도 로직
- 실패 데이터 보존

## 데이터 흐름

### 정상 흐름

```
1. PLC Data
   │
   ▼
2. Collector.collect(group="1sec")
   │
   ├─▶ CollectedData {
   │      source_time: 2024-01-01T12:00:00
   │      raw_data: bytes[...]
   │      plc_id: 1
   │      collection_group: "1sec"
   │   }
   │
   ▼
3. EventBus.emit(DATA_COLLECTED)
   │
   ▼
4. Processor._on_data_collected(event)
   │
   ├─▶ Parse raw_data
   ├─▶ Apply scaling
   │
   ├─▶ ProcessedData {
   │      source_time: 2024-01-01T12:00:00
   │      server_time: 2024-01-01T12:00:01
   │      plc_id: 1
   │      tag_id: 1
   │      value: 25.5
   │   }
   │
   ▼
5. Buffer.put(ProcessedData)
   │
   ▼
6. Publisher.get_batch() → [ProcessedData, ...]
   │
   ▼
7. Publisher.publish(batch)
   │
   ├─▶ Database INSERT
   └─▶ MQTT Publish
```

### 실패 처리 흐름

```
Publisher.publish(batch) FAILED
         │
         ▼
   retry (max 3 times)
         │
         ▼
   still FAILED?
         │
    ┌────┴────┐
    │         │
    NO       YES
    │         │
    ▼         ▼
  Success   Buffer.put_front(batch)
              │
              ▼
         Next cycle retry
```

## 스레딩 모델

### Asyncio 기반 태스크

```
┌─────────────────────────────────────────────────────────────┐
│                    asyncio Event Loop                        │
│                                                              │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │
│  │ Collector   │  │ Processor   │  │ Publisher   │         │
│  │ Tasks       │  │ Task        │  │ Task        │         │
│  │             │  │             │  │             │         │
│  │ • 1sec loop │  │ • Event     │  │ • Publish   │         │
│  │ • 5sec loop │  │   handler   │  │   loop      │         │
│  │ • 1min loop │  │             │  │ • Reconnect │         │
│  │ • Reconnect │  │             │  │   loop      │         │
│  └─────────────┘  └─────────────┘  └─────────────┘         │
│         │                │                │                  │
│         └────────────────┼────────────────┘                  │
│                          │                                   │
│                          ▼                                   │
│                   ┌─────────────┐                           │
│                   │  EventBus   │                           │
│                   │  Processor  │                           │
│                   │  Task       │                           │
│                   └─────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

### 동시성 처리

- **Collector**: 그룹별 독립 태스크 (병렬 수집)
- **Processor**: 이벤트 핸들러 (순차 처리)
- **Buffer**: asyncio.Lock (동시 접근 보호)
- **Publisher**: 단일 발행 루프

## 이벤트 시스템

### EventBus 구조

```
┌─────────────────────────────────────────────────────────────┐
│                         EventBus                             │
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │                 Event Queue                          │   │
│  │  [Event1] → [Event2] → [Event3] → ...               │   │
│  └─────────────────────────────────────────────────────┘   │
│                          │                                   │
│                          ▼                                   │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Event Processor Task                    │   │
│  │                                                      │   │
│  │  for event in queue:                                │   │
│  │    handlers = get_handlers(event.type)              │   │
│  │    for handler in handlers:                         │   │
│  │      await handler(event)                           │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
│  Handlers Registry:                                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  DATA_COLLECTED    → [processor.on_data_collected]  │   │
│  │  BUFFER_THRESHOLD  → [alert_handler]                │   │
│  │  PUBLISHER_ERROR   → [error_handler]                │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### 이벤트 타입

| 이벤트 | 발생 시점 | 데이터 |
|--------|-----------|--------|
| DATA_COLLECTED | 수집 완료 | CollectedData |
| DATA_PROCESSED | 처리 완료 | count, plc_id, group |
| BUFFER_UPDATED | 버퍼 추가 | current_size |
| BUFFER_THRESHOLD | 임계값 도달 | current_size, threshold |
| DATA_PUBLISHED | 발행 완료 | count |
| PUBLISH_FAILED | 발행 실패 | count, error |

## 모듈 확장

### 프로토콜 모듈 구조

```
src/collectors/
├── __init__.py
├── base.py           # BaseCollector
├── modbus/           # Modbus 모듈
│   ├── __init__.py
│   ├── collector.py  # ModbusCollector
│   └── processor.py  # ModbusProcessor
├── fenet/            # FENET 모듈
│   ├── __init__.py
│   ├── collector.py
│   └── processor.py
└── mcprotocol/       # MC Protocol 모듈
    ├── __init__.py
    ├── collector.py
    └── processor.py
```

### 모듈 구현 예시

```python
# src/collectors/modbus/collector.py

from src.collectors.base import BaseCollector
from src.core.interfaces import CollectedData
from pymodbus.client import AsyncModbusTcpClient

class ModbusCollector(BaseCollector):
    def __init__(self, plc_id, name, config, event_bus=None):
        super().__init__(plc_id, name, config, event_bus)
        self._client = None

    async def _do_connect(self) -> bool:
        self._client = AsyncModbusTcpClient(
            host=self._protocol_config.host,
            port=self._protocol_config.port,
        )
        return await self._client.connect()

    async def _do_disconnect(self) -> None:
        if self._client:
            self._client.close()

    async def _do_collect(self, group: str) -> CollectedData:
        tags = self._tags.get(group, [])

        # 레지스터 읽기
        result = await self._client.read_holding_registers(
            address=start_address,
            count=register_count,
            slave=self._protocol_config.unit_id,
        )

        return CollectedData(
            source_time=datetime.now(),
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=result.registers,
            collection_group=group,
        )
```

## 설계 결정

### 1. 왜 이벤트 기반인가?

**장점**:
- 컴포넌트 간 느슨한 결합
- 쉬운 모듈 교체
- 디버깅 용이 (이벤트 히스토리)

**대안 (직접 호출)**:
```python
# 직접 호출 방식 (사용 안 함)
data = collector.collect()
processed = processor.process(data)
publisher.publish(processed)
```

이 방식은 강한 결합을 만들어 유연성이 떨어집니다.

### 2. 왜 1:1 파이프라인인가?

**장점**:
- 장애 격리 (한 PLC 문제가 다른 PLC에 영향 안 줌)
- 독립적인 스케일링
- 단순한 설정 관리

**대안 (N:1 방식)**:
여러 PLC를 하나의 컨테이너에서 처리하면 리소스 효율적이지만,
장애 전파 위험과 설정 복잡도가 증가합니다.

### 3. 왜 버퍼를 사용하는가?

**장점**:
- 네트워크 지연/장애 대응
- 배치 처리로 DB 부하 감소
- 데이터 손실 방지

**위험 완화**:
- 오버플로우 시 오래된 데이터 삭제 (최신 우선)
- 종료 시 디스크 저장
- 임계값 알림

### 4. 왜 asyncio인가?

**장점**:
- I/O 바운드 작업에 효율적
- 단일 스레드로 동시성 처리
- 코루틴 기반 가독성

**고려사항**:
- CPU 바운드 작업 시 multiprocessing 필요
- 일부 프로토콜 라이브러리가 동기 전용일 수 있음

## 성능 고려사항

### 버퍼 크기

```
권장 크기 = (초당 수집량) × (예상 장애 시간) × 안전계수

예: 100 tags/sec × 60sec × 2 = 12,000
```

### 배치 크기

```
큰 배치: DB 효율 ↑, 지연 ↑
작은 배치: 실시간성 ↑, DB 부하 ↑

권장: 100~500 (상황에 따라 조정)
```

### 메모리 사용량

```
ProcessedData 1개 ≈ 100 bytes
버퍼 10,000개 ≈ 1 MB

총 메모리 ≈ 버퍼 + 코드 + 프로토콜 클라이언트
권장 컨테이너 메모리: 256~512 MB
```

## 분산 아키텍처 (Distributed Mode)

v0.3.0부터 RabbitMQ를 통한 Collector/Publisher 분리를 지원합니다.

### 분산 모드 개요

단일 모드에서는 각 Collector 컨테이너가 직접 DB/MQTT에 발행하므로
N개 PLC = N개 DB 커넥션이 필요합니다.
분산 모드에서는 모든 Collector가 RabbitMQ로 데이터를 전송하고,
단일 Publisher Service가 DB/MQTT로 발행합니다.

```
┌─────────────────┐
│ Collector PLC 1 │──┐
└─────────────────┘  │
┌─────────────────┐  │     ┌──────────────────────┐     ┌─────────────────┐
│ Collector PLC 2 │──┼────▶│      RabbitMQ        │────▶│ Publisher Svc   │
└─────────────────┘  │     │                      │     │                 │
┌─────────────────┐  │     │  Exchange: plc.data  │     │  ┌───────────┐ │
│ Collector PLC N │──┘     │  (topic, durable)    │     │  │ DB Insert │ │
└─────────────────┘        │                      │     │  ├───────────┤ │
                           │  queue.db ───────────┼────▶│  │MQTT Fwd   │ │
                           │  queue.mqtt ─────────┼────▶│  └───────────┘ │
                           └──────────────────────┘     └─────────────────┘
```

### 메시지 흐름

1. Collector의 `RabbitMQPublisher`가 ProcessedData 배치를 직렬화
2. 직렬화: `JSON → zlib 압축 → Fernet 암호화(선택)`
3. Routing key: `plc.{plc_id}.data` (Topic Exchange)
4. Publisher Service의 `QueueConsumer`가 큐에서 소비
5. `queue.db` → asyncpg COPY batch insert
6. `queue.mqtt` → aiomqtt forward

### 메시지 포맷

```
AMQP Message:
  body: compressed+encrypted JSON array of ProcessedData
  routing_key: "plc.{plc_id}.data"
  headers:
    compression: "zlib" | "gzip" | "none"
    encrypted: "true" | "false"
    plc_id: int
    batch_count: int
  delivery_mode: PERSISTENT (2)
```

### 사이트 구성

```
┌─────────────────────────────────────────────────────────┐
│                    Site (Factory)                         │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐ │
│  │RabbitMQ  │  │TimescaleDB│  │  CollectorHub Agent  │ │
│  │:5672     │  │:5432      │  │  :8080               │ │
│  └──────────┘  └──────────┘  └──────────────────────┘ │
│                                                          │
│  ┌──────────────┐  ┌──────────────────────────────┐    │
│  │Publisher Svc │  │ Collector Containers (동적)   │    │
│  │(1 instance)  │  │ collector-plc1, plc2, ...     │    │
│  └──────────────┘  └──────────────────────────────┘    │
└─────────────────────────────────────────────────────────┘
```

자세한 분산 배포 가이드는 `collectorhub/deploy/` 디렉토리를 참조하세요.

## 확장 로드맵

1. **Phase 1** (완료): 핵심 인터페이스 및 프레임워크
2. **Phase 2** (완료): Modbus TCP/RTU, FENET, MC Protocol
3. **Phase 3** (완료): RabbitMQ 분산 아키텍처, Publisher Service 분리
4. **Phase 4**: OPC UA, S7 Protocol
5. **Phase 5**: CollectorHub 고도화, 알람 시스템
