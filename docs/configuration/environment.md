# 환경 변수

Simple Collector에서 사용하는 환경 변수를 안내합니다.

---

## 환경 변수 사용

### YAML에서 참조

```yaml
publisher:
  database:
    host: ${DB_HOST}           # 필수
    password: ${DB_PASSWORD}    # 필수, 환경 변수 없으면 에러
    port: ${DB_PORT:-5432}      # 기본값 지정
```

### 문법

| 문법 | 설명 |
|------|------|
| `${VAR}` | 필수 환경 변수 |
| `${VAR:-default}` | 기본값이 있는 환경 변수 |
| `${VAR:?error message}` | 없으면 에러 메시지 출력 |

---

## 환경 변수 목록

### 데이터베이스

| 변수 | 설명 | 기본값 | 예시 |
|------|------|--------|------|
| `DB_HOST` | DB 호스트 | localhost | `192.168.1.10` |
| `DB_PORT` | DB 포트 | 5432 | `5432` |
| `DB_NAME` | 데이터베이스 이름 | - | `collector` |
| `DB_USER` | 사용자 이름 | - | `collector` |
| `DB_PASSWORD` | 비밀번호 | - | `secure_password` |
| `DB_SCHEMA` | 스키마 이름 | public | `test` |

### MQTT

| 변수 | 설명 | 기본값 | 예시 |
|------|------|--------|------|
| `MQTT_HOST` | 브로커 호스트 | localhost | `192.168.1.10` |
| `MQTT_PORT` | 브로커 포트 | 1883 | `1883` |
| `MQTT_USER` | 사용자 이름 | - | `collector` |
| `MQTT_PASSWORD` | 비밀번호 | - | `mqtt_password` |

### 로깅

| 변수 | 설명 | 기본값 | 예시 |
|------|------|--------|------|
| `LOG_LEVEL` | 기본 로그 레벨 | INFO | `DEBUG` |
| `COLLECTION_LOG_LEVEL` | 수집 로그 레벨 | INFO | `DEBUG` |
| `PUBLISH_LOG_LEVEL` | 발행 로그 레벨 | INFO | `INFO` |

### 기타

| 변수 | 설명 | 기본값 | 예시 |
|------|------|--------|------|
| `CONFIG_PATH` | 설정 파일 경로 | config/collector.yaml | `/app/config/collector.yaml` |

---

## .env 파일

### 생성

```bash title=".env"
# =============================================================================
# Simple Collector Environment Variables
# =============================================================================

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=collector
DB_USER=collector
DB_PASSWORD=your_secure_password
DB_SCHEMA=test

# MQTT (선택)
MQTT_HOST=localhost
MQTT_PORT=1883
MQTT_USER=collector
MQTT_PASSWORD=mqtt_password

# Logging
LOG_LEVEL=INFO
COLLECTION_LOG_LEVEL=DEBUG
PUBLISH_LOG_LEVEL=INFO
```

### 로드

#### Docker Compose

```yaml title="docker-compose.yml"
services:
  collector:
    env_file:
      - .env
```

#### 직접 실행

=== "Linux/macOS"

    ```bash
    export $(cat .env | xargs)
    python -m src.main
    ```

=== "Windows PowerShell"

    ```powershell
    Get-Content .env | ForEach-Object {
      if ($_ -match '^([^#][^=]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($matches[1], $matches[2])
      }
    }
    python -m src.main
    ```

---

## 보안 권장사항

### .env 파일 보호

```bash
# 권한 설정 (Linux/macOS)
chmod 600 .env

# .gitignore에 추가
echo ".env" >> .gitignore
```

### Docker Secrets (프로덕션)

```yaml title="docker-compose.yml"
services:
  collector:
    secrets:
      - db_password
    environment:
      - DB_PASSWORD_FILE=/run/secrets/db_password

secrets:
  db_password:
    file: ./secrets/db_password.txt
```

### Kubernetes Secrets

```yaml title="k8s-secret.yaml"
apiVersion: v1
kind: Secret
metadata:
  name: collector-secrets
type: Opaque
stringData:
  DB_PASSWORD: your_secure_password
  MQTT_PASSWORD: mqtt_password
```

---

## 환경별 설정

### 개발 환경

```bash title=".env.development"
LOG_LEVEL=DEBUG
COLLECTION_LOG_LEVEL=DEBUG
DB_HOST=localhost
```

### 프로덕션 환경

```bash title=".env.production"
LOG_LEVEL=INFO
COLLECTION_LOG_LEVEL=WARNING
DB_HOST=production-db.internal
```

### 사용

```bash
# 개발
cp .env.development .env

# 프로덕션
cp .env.production .env
```

---

## 문제 해결

### 환경 변수가 적용되지 않음

```bash
# 현재 값 확인
echo $DB_HOST
printenv | grep DB_

# .env 파일 문법 확인 (공백, 따옴표 주의)
cat .env
```

### Docker에서 환경 변수 확인

```bash
# 컨테이너 내부 확인
docker exec simple-collector printenv | grep DB_

# Docker Compose 설정 확인
docker compose config
```

### 비밀번호 특수문자

```bash
# 특수문자가 포함된 경우 따옴표 사용
DB_PASSWORD='p@ssw0rd!#$'

# 또는 이스케이프
DB_PASSWORD=p\@ssw0rd\!\#\$
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
