# 변경 이력 (Changelog)

모든 주요 변경 사항이 이 파일에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.0.0/)를 기반으로 하며,
[Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

---

## [0.3.0-beta] - 2026-02-10

### 추가됨 (Added)

- **RabbitMQ Publisher** (`src/publishers/rabbitmq/`)
    - `aio-pika` 기반 비동기 AMQP 클라이언트
    - Topic Exchange (`plc.data`) 기반 라우팅
    - `plc.{plc_id}.data` routing key 패턴
    - Persistent 메시지 (delivery_mode=2)
    - Robust 연결 (자동 재연결)

- **MessageSerializer** (`src/publishers/rabbitmq/serializer.py`)
    - JSON → zlib/gzip 압축 → Fernet 암호화 파이프라인
    - 압축률 ~90% (100 records 기준)
    - 선택적 암호화 (`encryption_enabled`)

- **RabbitMQConfig 데이터클래스**
    - exchange_name, exchange_type, routing_key_prefix
    - compression, encryption, heartbeat, delivery_mode

- **PublisherRegistry 확장**
    - `rabbitmq` 타입 등록
    - `check_environment()`에 rabbitmq 상태 포함

### 변경됨 (Changed)

- `PublisherConfig`에 `rabbitmq` 필드 추가
- `ConfigLoader._parse_config()`에 rabbitmq 파싱 블록 추가
- `ComponentFactory.create_publishers()`에 RabbitMQ publisher 생성 로직 추가

### 모듈 버전

| 모듈 | 버전 | 변경 |
|------|------|------|
| publishers | 0.3.0 | RabbitMQ Publisher, MessageSerializer |
| core/config | 0.3.0 | RabbitMQConfig 추가 |
| core/registry | 0.3.0 | rabbitmq 등록 |

---

## [0.2.3-beta] - 2026-02-09

### 추가됨 (Added)

- **VERBOSE 로그 레벨**
    - DEBUG(10)보다 낮은 레벨 5 추가
    - 개별 요청/응답 상세 로그용
    - `logger.verbose()` 메서드 사용
    - 콘솔/파일 모두 지원

- **JSON File Publisher**
    - MQTT와 동일한 JSON 포맷으로 파일 출력
    - Compact JSON (줄바꿈/공백 없음)
    - 파일 로테이션 지원
    - append/overwrite 모드

- **에러 로깅 강화**
    - 모든 모듈의 silent exception 패턴 제거
    - 영어 기반 에러 메시지 통일
    - 컨텍스트 정보 포함 (device, address 등)

### 변경됨 (Changed)

- MC Protocol 개별 읽기 로그: DEBUG → VERBOSE
- Publisher 개별 발행 로그: DEBUG → VERBOSE
- 그룹 완료 로그는 DEBUG 유지

### 모듈 버전

| 모듈 | 버전 | 변경 |
|------|------|------|
| collectors | 0.2.3 | VERBOSE 로그 지원 |
| publishers | 0.2.1 | JSON File Publisher, VERBOSE 로그 |
| utils | 0.2.1 | VERBOSE 레벨 추가 |

---

## [0.2.0-beta] - 2026-02-05

### 추가됨 (Added)

- **태그 설정 확장**
    - `decimals`: 소수점 자릿수 지정
    - `word_length`: 문자열 워드 수
    - `format`: 변환 포맷 (word → float32 등)
    - `bool_true_value`, `bool_false_value`: 커스텀 bool 임계값
    - `bool_invert`: bool 값 반전

- **마스터 테이블 동기화**
    - 시작 시 `plc_master`, `tag_master` 테이블 자동 동기화
    - DB 스키마 설정 지원 (`master_sync_schema`)

- **프로세서 최적화**
    - 출력 타입 룩업 테이블
    - 태그별 캐싱으로 처리 속도 향상
    - 지역 변수 최적화

- **수집 실패 처리**
    - `quality_code=0` 데이터 생성
    - 손실 추적 (연속/누적)
    - 별도 손실 로그 파일 (`loss.log`)

- **MkDocs 문서화 시스템**
    - Material 테마
    - 한국어/영어 검색
    - Docker Compose 통합

### 변경됨 (Changed)

- CSV 태그 파일 포맷 변경 (`memory`/`address` 분리)
- 로거 계층 구조 개선 (loss 로거 추가)

### 수정됨 (Fixed)

- MC Protocol 문자열 파싱 오류
- 버퍼 오버플로우 시 메모리 누수

---

## [0.1.0] - 2025-01-15

### 추가됨 (Added)

- **핵심 프레임워크**
    - `BaseCollector`: 프로토콜 독립적 수집기 기반 클래스
    - `BaseProcessor`: 데이터 처리기 기반 클래스
    - `BasePublisher`: 발행기 기반 클래스
    - `ThreadSafeBuffer`: 스레드 안전 버퍼

- **프로토콜 지원**
    - Modbus TCP/RTU
    - MC Protocol (Melsec Q/L/R 시리즈)

- **Publisher 지원**
    - TimescaleDB Publisher
    - MQTT Publisher (베타)

- **이벤트 시스템**
    - `EventBus`: 컴포넌트 간 느슨한 결합
    - 비동기 이벤트 처리

- **설정 관리**
    - YAML 기반 설정
    - CSV 태그 정의
    - 환경 변수 지원

- **로깅 시스템**
    - 컬러 콘솔 출력
    - 파일 로테이션
    - JSON 형식 지원

---

## 버전 체계

```
MAJOR.MINOR.PATCH[-PRERELEASE]

예: 0.2.0-beta
    │ │ │  └── 프리릴리스 (alpha, beta, rc)
    │ │ └───── 패치: 버그 수정
    │ └─────── 마이너: 기능 추가 (하위 호환)
    └───────── 메이저: 주요 변경 (호환성 변경)
```

### 릴리스 상태

| 태그 | 의미 |
|------|------|
| `alpha` | 개발 중, 불안정 |
| `beta` | 기능 완료, 테스트 중 |
| `rc` | 릴리스 후보 |
| (없음) | 정식 릴리스 |

---

## 모듈별 버전

| 모듈 | 버전 | 상태 | 비고 |
|------|------|------|------|
| **Core** | 0.3.0 | 안정 | RabbitMQConfig 추가 |
| **Modbus Collector** | 0.2.0 | 안정 | TCP/RTU 지원 |
| **MC Protocol Collector** | 0.2.3 | 안정 | Q/L/R 시리즈, VERBOSE 로그 |
| **Database Publisher** | 0.2.0 | 안정 | TimescaleDB |
| **MQTT Publisher** | 0.1.0 | 베타 | 기본 기능 |
| **JSON File Publisher** | 0.1.0 | 안정 | Compact JSON 출력 |
| **RabbitMQ Publisher** | 0.3.0 | 신규 | 분산 아키텍처 지원 |
| **MasterSync Service** | 0.1.0 | 안정 | 마스터 동기화 |
| **Utils** | 0.2.1 | 안정 | VERBOSE 로그 레벨 |

---

## 로드맵

### v0.3.0 (완료)

- [x] RabbitMQ Publisher (분산 아키텍처)
- [x] MessageSerializer (압축/암호화)
- [x] collector-publisher 서비스 분리

### v0.4.0 (예정)

- [ ] OPC UA 프로토콜 지원
- [ ] S7 Protocol 지원
- [ ] 알람 시스템

### v1.0.0 (예정)

- [ ] 프로덕션 안정화
- [ ] 성능 벤치마크
- [ ] 완전한 API 문서

---

<div align="center">

**NEUROSENSE Inc.**

*Intelligent Industrial Solutions*

</div>
