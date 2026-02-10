# 시스템 요구사항

Simple Collector를 설치하고 운영하기 위한 시스템 요구사항입니다.

---

## 최소 요구사항

### 하드웨어

| 구성 요소 | 최소 | 권장 | 고성능 |
|----------|------|------|--------|
| **CPU** | 2 Core | 4 Core | 8+ Core |
| **메모리** | 512 MB | 2 GB | 4+ GB |
| **디스크** | 1 GB | 10 GB | SSD 50+ GB |
| **네트워크** | 100 Mbps | 1 Gbps | 1 Gbps |

### 소프트웨어

| 구성 요소 | 최소 버전 | 권장 버전 | 비고 |
|----------|----------|----------|------|
| **Python** | 3.10 | 3.11+ | 3.12 지원 |
| **Docker** | 20.10 | 24.0+ | Docker Desktop 포함 |
| **Docker Compose** | 2.0 | 2.20+ | V2 문법 사용 |

---

## 운영체제 지원

### 공식 지원

| OS | 버전 | 상태 | 비고 |
|----|------|------|------|
| **Ubuntu** | 20.04 LTS | :material-check-circle:{ .success } 완전 지원 | 권장 |
| **Ubuntu** | 22.04 LTS | :material-check-circle:{ .success } 완전 지원 | 권장 |
| **Debian** | 11 (Bullseye) | :material-check-circle:{ .success } 완전 지원 | |
| **CentOS** | 8 Stream | :material-check-circle:{ .success } 완전 지원 | |
| **Rocky Linux** | 8/9 | :material-check-circle:{ .success } 완전 지원 | |
| **Windows** | 10/11 | :material-check-circle:{ .success } 완전 지원 | Docker Desktop 필요 |
| **Windows Server** | 2019/2022 | :material-check-circle:{ .success } 완전 지원 | |
| **macOS** | 12+ (Monterey) | :material-alert-circle:{ .warning } 개발용 | ARM/Intel |

### 컨테이너 환경

| 환경 | 상태 | 비고 |
|------|------|------|
| **Docker** | :material-check-circle:{ .success } 완전 지원 | 권장 배포 방식 |
| **Kubernetes** | :material-check-circle:{ .success } 완전 지원 | Helm Chart 제공 |
| **Docker Swarm** | :material-alert-circle:{ .warning } 테스트됨 | |
| **Podman** | :material-alert-circle:{ .warning } 테스트됨 | |

---

## 데이터베이스 요구사항

### TimescaleDB (권장)

| 항목 | 최소 | 권장 | 비고 |
|------|------|------|------|
| **버전** | 2.10 | 2.13+ | PostgreSQL 15 기반 |
| **메모리** | 1 GB | 4 GB | shared_buffers 설정 |
| **디스크** | 10 GB | 100 GB+ | SSD 권장 |
| **연결 수** | 10 | 100+ | max_connections |

### PostgreSQL

| 항목 | 최소 | 권장 |
|------|------|------|
| **버전** | 13 | 15+ |
| **확장** | - | TimescaleDB |

### 권장 설정

```ini title="postgresql.conf"
# 메모리 (4GB RAM 기준)
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 64MB
maintenance_work_mem = 256MB

# 연결
max_connections = 100

# WAL
wal_level = replica
max_wal_size = 2GB

# TimescaleDB
timescaledb.max_background_workers = 8
```

---

## 네트워크 요구사항

### 포트

| 서비스 | 포트 | 프로토콜 | 방향 | 용도 |
|--------|------|----------|------|------|
| **Modbus TCP** | 502 | TCP | Outbound | PLC 통신 |
| **MC Protocol** | 5000-5010 | TCP | Outbound | 미쓰비시 PLC |
| **PostgreSQL** | 5432 | TCP | Outbound | 데이터베이스 |
| **MQTT** | 1883 | TCP | Outbound | 메시지 브로커 |
| **MQTT TLS** | 8883 | TCP | Outbound | 암호화된 MQTT |

### 방화벽 설정 예시

```bash title="firewall-cmd (RHEL/CentOS)"
# PLC 통신 허용
firewall-cmd --permanent --add-port=502/tcp

# 데이터베이스 (내부망)
firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/8" port port="5432" protocol="tcp" accept'

# 적용
firewall-cmd --reload
```

```bash title="ufw (Ubuntu)"
# PLC 통신
ufw allow out 502/tcp

# 데이터베이스
ufw allow out 5432/tcp
```

---

## 성능 기준

### 태그 수에 따른 권장 사양

| 태그 수 | CPU | 메모리 | 디스크 I/O | 네트워크 |
|--------|-----|--------|-----------|---------|
| **100 이하** | 1 Core | 256 MB | HDD OK | 10 Mbps |
| **500 이하** | 2 Core | 512 MB | HDD OK | 100 Mbps |
| **1,000 이하** | 2 Core | 1 GB | SSD 권장 | 100 Mbps |
| **5,000 이하** | 4 Core | 2 GB | SSD 필수 | 1 Gbps |
| **10,000 이상** | 8+ Core | 4+ GB | NVMe 권장 | 1 Gbps |

### 수집 주기별 리소스

| 수집 주기 | CPU 사용률 | 메모리 | DB 쓰기량 |
|----------|-----------|--------|----------|
| **1초** | 중간 | 보통 | 높음 |
| **5초** | 낮음 | 낮음 | 중간 |
| **30초** | 매우 낮음 | 낮음 | 낮음 |
| **100ms** | 높음 | 높음 | 매우 높음 |

---

## 의존성 버전

### Python 패키지

```txt title="requirements.txt"
# Core
pyyaml>=6.0
pydantic>=2.0

# Protocols
pymodbus>=3.5.0
pymcprotocol>=0.4.0

# Publishers
asyncpg>=0.28.0
paho-mqtt>=1.6.0

# Utilities
python-dotenv>=1.0.0
```

### 버전 호환성 매트릭스

| Simple Collector | Python | pymodbus | asyncpg | TimescaleDB |
|-----------------|--------|----------|---------|-------------|
| 0.2.x | 3.10-3.12 | 3.5+ | 0.28+ | 2.10+ |
| 0.1.x | 3.9-3.11 | 3.0+ | 0.27+ | 2.8+ |

---

## 클라우드 환경

### AWS

| 인스턴스 | vCPU | 메모리 | 용도 |
|---------|------|--------|------|
| **t3.micro** | 2 | 1 GB | 개발/테스트 |
| **t3.small** | 2 | 2 GB | 소규모 (500 태그) |
| **t3.medium** | 2 | 4 GB | 중규모 (2,000 태그) |
| **c6i.large** | 2 | 4 GB | 프로덕션 권장 |
| **c6i.xlarge** | 4 | 8 GB | 대규모 (10,000+ 태그) |

### Azure

| VM 크기 | vCPU | 메모리 | 용도 |
|--------|------|--------|------|
| **B1s** | 1 | 1 GB | 개발/테스트 |
| **B2s** | 2 | 4 GB | 소규모 |
| **D2s_v5** | 2 | 8 GB | 프로덕션 권장 |
| **D4s_v5** | 4 | 16 GB | 대규모 |

### GCP

| 머신 타입 | vCPU | 메모리 | 용도 |
|----------|------|--------|------|
| **e2-micro** | 2 | 1 GB | 개발/테스트 |
| **e2-small** | 2 | 2 GB | 소규모 |
| **n2-standard-2** | 2 | 8 GB | 프로덕션 권장 |
| **n2-standard-4** | 4 | 16 GB | 대규모 |

---

## 체크리스트

### 설치 전 확인사항

- [ ] Python 3.10 이상 설치 확인
- [ ] Docker 및 Docker Compose 설치 확인
- [ ] 네트워크 연결 (PLC, DB) 확인
- [ ] 방화벽 포트 개방 확인
- [ ] 디스크 여유 공간 확인
- [ ] 시스템 시간 동기화 (NTP) 확인

### 운영 전 확인사항

- [ ] 데이터베이스 연결 테스트
- [ ] PLC 통신 테스트
- [ ] 로그 저장 경로 쓰기 권한
- [ ] 환경 변수 설정 완료
- [ ] 백업 정책 수립

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
