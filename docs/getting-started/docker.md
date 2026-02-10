# Docker 가이드

Docker 환경에서 Simple Collector를 실행하는 방법을 안내합니다.

---

## Docker Compose 구성

### 기본 구성

```yaml title="docker-compose.yml"
services:
  # Simple Collector
  collector:
    image: simple-collector:0.2.0-beta
    container_name: simple-collector
    restart: unless-stopped
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
    environment:
      - DB_PASSWORD=${DB_PASSWORD}
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  # 문서 서버 (선택)
  docs:
    image: squidfunk/mkdocs-material:latest
    container_name: simple-collector-docs
    ports:
      - "8000:8000"
    volumes:
      - ./:/docs:ro
    command: serve --dev-addr=0.0.0.0:8000
    profiles:
      - docs
```

---

## 실행 방법

### Collector 실행

```bash
# 기본 실행
docker compose up -d collector

# 로그 확인
docker compose logs -f collector

# 중지
docker compose down
```

### 문서 서버 실행

```bash
# 문서 서버 실행
docker compose --profile docs up -d

# 접속: http://localhost:8000
```

### 문서 빌드

```bash
# 정적 사이트 빌드
docker compose --profile build run docs-build

# 빌드 결과: site/ 디렉토리
```

---

## 이미지 빌드

### Dockerfile

```dockerfile title="Dockerfile"
FROM python:3.11-slim

WORKDIR /app

# 의존성 설치
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 소스 복사
COPY src/ src/
COPY config/ config/

# 실행
CMD ["python", "-m", "src.main", "--config", "config/collector.yaml"]
```

### 빌드 명령

```bash
# AMD64 (일반 서버)
docker build -t simple-collector:0.2.0-beta .

# ARM64 (Raspberry Pi, Apple Silicon)
docker build --platform linux/arm64 -t simple-collector:0.2.0-beta-arm64 .

# 멀티 플랫폼
docker buildx build --platform linux/amd64,linux/arm64 \
  -t simple-collector:0.2.0-beta --push .
```

---

## 환경 변수

### .env 파일

```bash title=".env"
# Database
DB_HOST=timescaledb
DB_PORT=5432
DB_NAME=collector
DB_USER=collector
DB_PASSWORD=your_secure_password

# MQTT
MQTT_HOST=mosquitto
MQTT_PORT=1883
MQTT_USER=collector
MQTT_PASSWORD=mqtt_password

# Collector
LOG_LEVEL=INFO
COLLECTION_LOG_LEVEL=DEBUG
```

### 환경 변수 사용

```yaml title="config/collector.yaml"
publisher:
  database:
    host: ${DB_HOST}
    port: ${DB_PORT}
    password: ${DB_PASSWORD}
```

---

## 전체 스택 예시

### Full Stack Compose

```yaml title="docker-compose.full.yml"
services:
  # ==========================================================================
  # Simple Collector
  # ==========================================================================
  collector:
    image: simple-collector:0.2.0-beta
    container_name: simple-collector
    restart: unless-stopped
    depends_on:
      timescaledb:
        condition: service_healthy
    volumes:
      - ./config:/app/config:ro
      - ./logs:/app/logs
    environment:
      - DB_HOST=timescaledb
      - DB_PASSWORD=${DB_PASSWORD:-password}
    networks:
      - collector-net

  # ==========================================================================
  # TimescaleDB
  # ==========================================================================
  timescaledb:
    image: timescale/timescaledb:latest-pg15
    container_name: timescaledb
    restart: unless-stopped
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_USER=collector
      - POSTGRES_PASSWORD=${DB_PASSWORD:-password}
      - POSTGRES_DB=collector
    volumes:
      - timescaledb-data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U collector"]
      interval: 10s
      timeout: 5s
      retries: 5
    networks:
      - collector-net

  # ==========================================================================
  # MQTT Broker (선택)
  # ==========================================================================
  mosquitto:
    image: eclipse-mosquitto:2
    container_name: mosquitto
    restart: unless-stopped
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto/config:/mosquitto/config:ro
      - mosquitto-data:/mosquitto/data
    networks:
      - collector-net
    profiles:
      - mqtt

  # ==========================================================================
  # Documentation
  # ==========================================================================
  docs:
    image: squidfunk/mkdocs-material:latest
    container_name: simple-collector-docs
    ports:
      - "8000:8000"
    volumes:
      - ./:/docs:ro
    command: serve --dev-addr=0.0.0.0:8000
    profiles:
      - docs

volumes:
  timescaledb-data:
  mosquitto-data:

networks:
  collector-net:
    driver: bridge
```

### 실행

```bash
# 전체 스택 실행
docker compose -f docker-compose.full.yml up -d

# MQTT 포함
docker compose -f docker-compose.full.yml --profile mqtt up -d

# 문서 포함
docker compose -f docker-compose.full.yml --profile docs up -d

# 모든 서비스
docker compose -f docker-compose.full.yml --profile mqtt --profile docs up -d
```

---

## 모니터링

### 컨테이너 상태

```bash
# 상태 확인
docker compose ps

# 리소스 사용량
docker stats simple-collector

# 상세 정보
docker inspect simple-collector
```

### 로그 관리

```bash
# 실시간 로그
docker compose logs -f collector

# 최근 100줄
docker compose logs --tail 100 collector

# 타임스탬프 포함
docker compose logs -t collector
```

### 헬스체크

```bash
# 헬스체크 상태
docker inspect --format='{{json .State.Health}}' simple-collector

# 컨테이너 상태
docker inspect --format='{{.State.Status}}' simple-collector
```

---

## 문제 해결

### 컨테이너가 시작되지 않음

```bash
# 로그 확인
docker compose logs collector

# 설정 파일 확인
docker compose config
```

### 데이터베이스 연결 실패

```bash
# 네트워크 확인
docker network ls
docker network inspect simple-collector_collector-net

# DB 연결 테스트
docker exec -it timescaledb psql -U collector -c "SELECT 1"
```

### 볼륨 권한 문제

```bash
# 볼륨 권한 확인
ls -la config/ logs/

# 권한 수정
chmod 755 config/ logs/
chmod 644 config/*.yaml config/*.csv
```

---

## 프로덕션 권장사항

### 리소스 제한

```yaml
services:
  collector:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

### 재시작 정책

```yaml
services:
  collector:
    restart: unless-stopped
    # 또는
    restart: always
```

### 로그 관리

```yaml
services:
  collector:
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "5"
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
