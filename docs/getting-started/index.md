# 시작하기

Simple Collector를 시작하기 위한 가이드입니다.

---

## 개요

Simple Collector는 산업용 데이터 수집을 위한 고성능 파이썬 프레임워크입니다.
PLC, 센서 등 다양한 산업 장비에서 데이터를 수집하여 TimescaleDB, MQTT 등으로 전송합니다.

### 주요 특징

| 특징 | 설명 |
|------|------|
| 고성능 | 비동기 기반 설계로 초당 10,000+ 태그 처리 |
| 다양한 프로토콜 | Modbus TCP/RTU, MC Protocol 지원 |
| 유연한 저장소 | TimescaleDB, MQTT 등 다양한 목적지 |
| 신뢰성 | 자동 재연결, 버퍼링, 데이터 손실 추적 |

---

## 시스템 요구사항

### 하드웨어

| 구성요소 | 최소 | 권장 |
|---------|------|------|
| CPU | 1 Core | 2+ Cores |
| RAM | 256 MB | 512 MB |
| Storage | 100 MB | 1 GB |
| Network | 100 Mbps | 1 Gbps |

### 소프트웨어

| 구성요소 | 버전 |
|---------|------|
| Python | 3.10+ |
| Docker | 20.10+ (선택) |
| Docker Compose | 2.0+ (선택) |
| TimescaleDB | 2.0+ (선택) |
| MQTT Broker | Mosquitto 2.0+ (선택) |

---

## 빠른 시작

### 1. Docker 사용 (권장)

```bash
# 설정 파일 복사 및 수정
cp config/collector.example.yaml config/collector.yaml
vi config/collector.yaml

# 실행
docker-compose up -d
```

### 2. 직접 설치

```bash
# 의존성 설치
pip install -r requirements.txt

# 실행
python -m src.main --config config/collector.yaml
```

---

## 다음 단계

<div class="grid cards" markdown>

-   :material-download:{ .lg .middle } 설치

    ---

    상세한 설치 가이드

    [:octicons-arrow-right-24: 설치 가이드](installation.md)

-   :material-rocket-launch:{ .lg .middle } 빠른 시작

    ---

    첫 번째 데이터 수집

    [:octicons-arrow-right-24: 빠른 시작](quickstart.md)

-   :material-docker:{ .lg .middle } Docker

    ---

    Docker 환경에서 실행

    [:octicons-arrow-right-24: Docker 가이드](docker.md)

-   :material-cog:{ .lg .middle } 설정

    ---

    상세 설정 방법

    [:octicons-arrow-right-24: 설정 가이드](../configuration/index.md)

</div>

---

<div align="center">

NEUROSENSE Inc. | Intelligent Industrial Solutions

</div>
