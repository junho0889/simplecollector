# 설치 가이드

Simple Collector의 상세 설치 방법을 안내합니다.

---

## 설치 방법 선택

| 방법 | 용도 | 난이도 |
|------|------|--------|
| Docker (권장) | 프로덕션, 빠른 시작 | 쉬움 |
| 직접 설치 | 개발, 커스터마이징 | 보통 |
| 가상환경 | 격리된 개발 환경 | 보통 |

---

## Docker 설치 (권장)

### 사전 요구사항

```bash
# Docker 버전 확인
docker --version  # 20.10+

# Docker Compose 버전 확인
docker compose version  # 2.0+
```

### 설치 단계

#### 1. 설정 파일 준비

```bash
# 기본 설정 복사
cp config/collector.example.yaml config/collector.yaml

# 환경 변수 파일 생성
cat > .env << 'EOF'
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=collector
DB_USER=collector
DB_PASSWORD=your_secure_password

# MQTT (선택)
MQTT_HOST=localhost
MQTT_PORT=1883
EOF
```

#### 2. 설정 수정

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "PLC1_Collector"
  protocol:
    type: modbus
    host: "192.168.1.100"
    port: 502

publisher:
  database:
    enabled: true
    host: ${DB_HOST}
    port: ${DB_PORT}
    database: ${DB_NAME}
    user: ${DB_USER}
    password: ${DB_PASSWORD}
```

#### 3. 실행

```bash
# 빌드 및 실행
docker compose up -d

# 로그 확인
docker compose logs -f collector
```

---

## 직접 설치

### 사전 요구사항

```bash
# Python 버전 확인
python --version  # 3.10+

# pip 업그레이드
pip install --upgrade pip
```

### 설치 단계

#### 1. 가상환경 생성 (권장)

=== "Linux/macOS"

    ```bash
    python -m venv venv
    source venv/bin/activate
    ```

=== "Windows"

    ```powershell
    python -m venv venv
    .\venv\Scripts\activate
    ```

#### 2. 의존성 설치

```bash
pip install -r requirements.txt
```

#### 3. 설정 파일 준비

```bash
cp config/collector.example.yaml config/collector.yaml
vi config/collector.yaml
```

#### 4. 실행

```bash
python -m src.main --config config/collector.yaml
```

---

## 의존성 목록

### 핵심 의존성

| 패키지 | 버전 | 용도 |
|--------|------|------|
| asyncio | built-in | 비동기 처리 |
| pyyaml | 6.0+ | YAML 설정 파싱 |
| pydantic | 2.0+ | 데이터 검증 |

### 프로토콜 의존성

| 패키지 | 버전 | 용도 |
|--------|------|------|
| pymodbus | 3.0+ | Modbus TCP/RTU |
| pymcprotocol | 0.4+ | MC Protocol |

### Publisher 의존성

| 패키지 | 버전 | 용도 |
|--------|------|------|
| asyncpg | 0.28+ | PostgreSQL/TimescaleDB |
| paho-mqtt | 1.6+ | MQTT |

---

## 설치 확인

### 버전 확인

```bash
python -c "from src import __version__; print(__version__)"
# 출력: 0.2.0-beta
```

### 모듈 확인

```bash
python -c "from src.collectors.modbus import ModbusCollector; print('OK')"
python -c "from src.publishers.database import DatabasePublisher; print('OK')"
```

### 설정 검증

```bash
python -m src.main --config config/collector.yaml --validate
```

---

## 문제 해결

### ModuleNotFoundError

```bash
# 가상환경이 활성화되어 있는지 확인
which python
# /path/to/venv/bin/python 이어야 함

# 의존성 재설치
pip install -r requirements.txt
```

### Docker 권한 오류

```bash
# Docker 그룹에 사용자 추가
sudo usermod -aG docker $USER

# 재로그인 후 확인
docker ps
```

### 포트 충돌

```bash
# 사용 중인 포트 확인
netstat -tlnp | grep 5432
lsof -i :5432

# 다른 포트 사용
docker compose down
# docker-compose.yml에서 포트 변경
docker compose up -d
```

---

## 다음 단계

설치가 완료되었다면 [빠른 시작 가이드](quickstart.md)로 진행하세요.

---

<div align="center">

NEUROSENSE Inc. | Intelligent Industrial Solutions

</div>
