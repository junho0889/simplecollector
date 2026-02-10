# 트러블슈팅 가이드

Simple Collector 운영 중 발생할 수 있는 문제와 해결 방법을 안내합니다.

---

## 연결 문제

### PLC 연결 실패

#### 증상

```
[ERROR] [ModbusCollector] Connection failed: [Errno 111] Connection refused
[ERROR] [ModbusCollector] Failed to connect to 192.168.1.100:502
```

#### 원인 및 해결

=== "네트워크 문제"

    ```bash
    # 1. 네트워크 연결 확인
    ping 192.168.1.100

    # 2. 포트 연결 확인
    nc -zv 192.168.1.100 502
    # 또는
    telnet 192.168.1.100 502

    # 3. 방화벽 확인
    sudo iptables -L -n | grep 502
    ```

=== "PLC 설정 문제"

    ```yaml
    # collector.yaml 확인
    collector:
      protocol:
        type: modbus
        host: "192.168.1.100"  # IP 확인
        port: 502               # 포트 확인
        unit_id: 1              # Unit ID 확인 (1-247)
        timeout_ms: 5000        # 타임아웃 늘리기
    ```

=== "Docker 네트워크"

    ```bash
    # Docker 네트워크 확인
    docker network ls

    # 컨테이너에서 연결 테스트
    docker exec simple-collector ping 192.168.1.100

    # host 네트워크 모드 사용 (필요 시)
    # docker-compose.yml
    services:
      collector:
        network_mode: "host"
    ```

---

### 데이터베이스 연결 실패

#### 증상

```
[ERROR] Connection refused: connect to timescaledb:5432
[ERROR] asyncpg.exceptions.InvalidPasswordError: password authentication failed
```

#### 원인 및 해결

=== "연결 정보 오류"

    ```bash
    # 환경 변수 확인
    echo $DB_HOST $DB_PORT $DB_USER $DB_NAME

    # 직접 연결 테스트
    psql -h localhost -p 5432 -U user -d neurosense

    # Docker 환경에서
    docker exec timescaledb psql -U user -d neurosense -c "SELECT 1"
    ```

=== "네트워크 설정"

    ```yaml title="docker-compose.yml"
    services:
      collector:
        networks:
          - db_network
        environment:
          DB_HOST: timescaledb  # 컨테이너 이름 사용

    networks:
      db_network:
        external: true
        name: create_db_default  # 기존 네트워크 이름
    ```

=== "인증 오류"

    ```bash
    # pg_hba.conf 확인
    docker exec timescaledb cat /var/lib/postgresql/data/pg_hba.conf

    # 권한 부여
    docker exec timescaledb psql -U postgres -c "
      GRANT ALL PRIVILEGES ON DATABASE neurosense TO user;
      GRANT ALL ON SCHEMA modbus TO user;
    "
    ```

---

### MQTT 연결 실패

#### 증상

```
[ERROR] MQTT connection failed: [Errno 111] Connection refused
[ERROR] MQTT: Connection lost, reconnecting...
```

#### 원인 및 해결

```bash
# 1. 브로커 상태 확인
docker exec simple-collector-mqtt mosquitto_sub -t '$SYS/#' -C 1

# 2. 연결 테스트
mosquitto_pub -h localhost -p 1883 -t test -m "hello"

# 3. 인증 확인 (인증 사용 시)
mosquitto_pub -h localhost -p 1883 -u user -P password -t test -m "hello"
```

```yaml title="collector.yaml"
publisher:
  mqtt:
    enabled: true
    host: "mqtt"          # Docker 서비스 이름
    port: 1883
    client_id: "collector_${HOSTNAME}"  # 고유한 클라이언트 ID
    keepalive: 60
    reconnect_delay_ms: 5000
```

---

## 데이터 수집 문제

### 태그 읽기 실패

#### 증상

```
[WARNING] Failed to read tags: Modbus Error: [Input/Output] No Response
[WARNING] Tag Tag_00001 quality_code changed to 0 (bad)
```

#### 원인 및 해결

=== "주소 오류"

    ```csv title="tags.csv 확인"
    # Modbus 주소 체계
    # HR (Holding Register): 40001-49999 → 주소 0-9998
    # IR (Input Register): 30001-39999 → 주소 0-9998

    tag_id,tag_name,memory,address,data_type
    1,Temperature,HR,0,float32     # HR0 = 40001
    2,Pressure,HR,100,uint32       # HR100 = 40101
    ```

=== "데이터 타입 불일치"

    ```csv title="tags.csv"
    # 올바른 데이터 타입 지정
    tag_id,tag_name,memory,address,data_type,word_length
    1,Model_Name,HR,900,string,,10       # 10 워드 = 20바이트
    2,Cycle_Time,HR,216,uint16,          # 단일 워드
    3,Temperature,HR,300,float32,        # 2 워드
    4,Counter,HR,400,uint32,             # 2 워드
    ```

=== "타임아웃 조정"

    ```yaml title="collector.yaml"
    collector:
      protocol:
        timeout_ms: 10000     # 10초로 증가
        reconnect_interval_ms: 5000

      collection_groups:
        - name: fast
          interval_ms: 1000
          timeout_ms: 5000    # 그룹별 타임아웃
          retry_count: 3      # 재시도 횟수
    ```

---

### 데이터 품질 저하

#### 증상

```
[WARNING] High bad quality ratio: 15.3% (153/1000 tags)
[WARNING] Buffer overflow, dropping oldest 1000 records
```

#### 원인 및 해결

```bash
# 1. 품질 상태 확인
docker exec timescaledb psql -U user -d neurosense -c "
  SELECT quality_code, COUNT(*)
  FROM modbus.plc_data_integrated
  WHERE server_time > NOW() - INTERVAL '1 hour'
  GROUP BY quality_code;
"

# 2. 태그별 품질 확인
docker exec timescaledb psql -U user -d neurosense -c "
  SELECT tag_id, tag_name, quality_code
  FROM modbus.v_tag_latest
  WHERE quality_code != 1
  ORDER BY tag_id;
"
```

```yaml title="collector.yaml - 버퍼 조정"
buffer:
  max_size: 100000      # 버퍼 크기 증가
  batch_size: 500       # 배치 크기
  threshold_ratio: 0.8  # 80%에서 경고
  drop_oldest: true     # 오래된 데이터 삭제
```

---

## 성능 문제

### 높은 CPU 사용률

#### 증상

```
[WARNING] Collection cycle exceeded interval: 1500ms > 1000ms
[WARNING] Processing backlog: 5000 records pending
```

#### 원인 및 해결

=== "수집 최적화"

    ```yaml title="collector.yaml"
    collector:
      collection_groups:
        # 연속 주소는 하나의 그룹으로
        - name: fast
          interval_ms: 1000
          # 연속 주소 자동 병합으로 읽기 횟수 감소

    # 태그 CSV에서 주소를 연속으로 배치
    # HR0, HR1, HR2... (좋음)
    # HR0, HR100, HR5... (나쁨)
    ```

=== "발행 최적화"

    ```yaml title="collector.yaml"
    publisher:
      publish_interval_ms: 500   # 발행 주기 조정

      database:
        pool_size: 10            # 커넥션 풀 증가
        batch_size: 500          # 배치 크기 증가
    ```

=== "리소스 확인"

    ```bash
    # CPU 사용률 확인
    docker stats simple-collector

    # 프로세스 상세
    docker exec simple-collector top -bn1 | head -20

    # Python 프로파일링
    python -m cProfile -o profile.out -m src.main
    ```

---

### 메모리 누수

#### 증상

```
[WARNING] Memory usage: 1.5 GB (threshold: 1 GB)
Container killed: OOM (Out of Memory)
```

#### 원인 및 해결

```bash
# 1. 메모리 사용량 모니터링
docker stats simple-collector --format "{{.MemUsage}}"

# 2. Python 메모리 프로파일링
pip install memory_profiler
python -m memory_profiler -m src.main

# 3. Docker 메모리 제한 설정
```

```yaml title="docker-compose.yml"
services:
  collector:
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 512M
```

---

### 데이터베이스 성능 저하

#### 증상

```
[WARNING] Database insert slow: 2500ms for 500 records
[ERROR] asyncpg.exceptions.TooManyConnectionsError
```

#### 원인 및 해결

```sql
-- 1. 테이블 크기 확인
SELECT hypertable_size('modbus.plc_data_integrated');

-- 2. 청크 상태 확인
SELECT * FROM timescaledb_information.chunks
WHERE hypertable_name = 'plc_data_integrated';

-- 3. 인덱스 상태 확인
SELECT indexrelname, idx_scan, idx_tup_read
FROM pg_stat_user_indexes
WHERE schemaname = 'modbus';

-- 4. 압축 적용 (오래된 데이터)
SELECT compress_chunk(c.chunk_name)
FROM timescaledb_information.chunks c
WHERE c.hypertable_name = 'plc_data_integrated'
  AND c.range_end < NOW() - INTERVAL '7 days';

-- 5. 리텐션 정책 설정
SELECT add_retention_policy('modbus.plc_data_integrated', INTERVAL '90 days');
```

---

## Docker 문제

### 컨테이너 시작 실패

#### 증상

```
simple-collector exited with code 1
Error: Cannot connect to database
```

#### 원인 및 해결

```bash
# 1. 로그 확인
docker logs simple-collector --tail 100

# 2. 설정 파일 검증
docker run --rm -v $(pwd)/config:/app/config simple-collector:0.2.0-beta \
  python -m src.main --config /app/config/collector.yaml --validate

# 3. 의존성 순서 확인
docker compose up -d timescaledb mqtt
sleep 10  # 의존 서비스 준비 대기
docker compose up -d collector

# 4. 헬스체크 확인
docker compose ps
```

```yaml title="docker-compose.yml"
services:
  collector:
    depends_on:
      mqtt:
        condition: service_healthy
      # timescaledb는 외부 네트워크이므로 별도 확인 필요

    healthcheck:
      test: ["CMD", "python", "-c", "import src; print('OK')"]
      interval: 30s
      timeout: 10s
      retries: 3
```

---

### 볼륨 권한 문제

#### 증상

```
PermissionError: [Errno 13] Permission denied: '/app/logs/collector.log'
```

#### 원인 및 해결

```bash
# 1. 권한 확인
ls -la logs/
ls -la config/

# 2. 권한 수정
chmod -R 755 logs/
chmod -R 644 config/

# 3. 소유자 변경 (필요 시)
sudo chown -R 1000:1000 logs/
```

```yaml title="docker-compose.yml"
services:
  collector:
    user: "${UID:-1000}:${GID:-1000}"
    volumes:
      - ./logs:/app/logs
      - ./config:/app/config:ro  # 설정은 읽기 전용
```

---

## 로그 분석

### 로그 레벨 설정

```yaml title="collector.yaml"
logging:
  level: DEBUG              # 전체 로그 레벨
  collection_level: DEBUG   # 수집 로그
  publish_level: INFO       # 발행 로그

  file_path: "logs/collector.log"
  max_size_mb: 100
  backup_count: 10
```

### 주요 로그 패턴

| 패턴 | 의미 | 조치 |
|------|------|------|
| `Connection failed` | 연결 실패 | 네트워크/설정 확인 |
| `quality_code changed to 0` | 데이터 품질 저하 | 태그 주소/타입 확인 |
| `Buffer overflow` | 버퍼 초과 | 버퍼 크기 증가 또는 발행 속도 개선 |
| `Collection cycle exceeded` | 수집 지연 | 태그 수/주기 조정 |
| `Reconnecting` | 재연결 시도 | 연결 안정성 확인 |

### 로그 검색

```bash
# 에러 로그만 확인
docker logs simple-collector 2>&1 | grep -i error

# 특정 태그 로그
docker logs simple-collector 2>&1 | grep "Tag_00001"

# 시간대별 로그
docker logs simple-collector --since "2026-02-05T07:00:00" --until "2026-02-05T08:00:00"

# 실시간 모니터링
docker logs -f simple-collector | grep -E "(ERROR|WARNING)"
```

---

## 진단 명령어

### 시스템 상태 확인

```bash
# 전체 서비스 상태
docker compose ps

# 리소스 사용량
docker stats --no-stream

# 네트워크 상태
docker network inspect simplecollector_collector_network

# 볼륨 상태
docker volume ls
```

### 데이터베이스 진단

```bash
# 최근 데이터 확인
docker exec timescaledb psql -U user -d neurosense -c "
  SELECT COUNT(*), MIN(server_time), MAX(server_time)
  FROM modbus.plc_data_integrated
  WHERE server_time > NOW() - INTERVAL '1 hour';
"

# 초당 INSERT 속도
docker exec timescaledb psql -U user -d neurosense -c "
  SELECT
    date_trunc('second', server_time) as ts,
    COUNT(*) as records
  FROM modbus.plc_data_integrated
  WHERE server_time > NOW() - INTERVAL '1 minute'
  GROUP BY ts
  ORDER BY ts DESC
  LIMIT 10;
"
```

---

## 긴급 복구

### 서비스 재시작

```bash
# 단일 서비스 재시작
docker compose restart collector

# 전체 재시작
docker compose down && docker compose up -d

# 강제 재빌드
docker compose build --no-cache collector
docker compose up -d collector
```

### 데이터 백업

```bash
# 마스터 테이블 백업
docker exec timescaledb pg_dump -U user -d neurosense \
  -t modbus.plc_master -t modbus.tag_master > master_backup.sql

# 최근 데이터 백업
docker exec timescaledb psql -U user -d neurosense -c "
  COPY (
    SELECT * FROM modbus.plc_data_integrated
    WHERE server_time > NOW() - INTERVAL '1 day'
  ) TO STDOUT WITH CSV HEADER;
" > data_backup.csv
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
