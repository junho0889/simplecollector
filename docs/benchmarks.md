# 성능 벤치마크

Simple Collector의 성능 테스트 결과 및 최적화 가이드입니다.

---

## 테스트 환경

### 하드웨어

| 구성 요소 | 사양 |
|----------|------|
| **CPU** | Intel Core i7-10700 (8 Core / 16 Thread) |
| **메모리** | 32 GB DDR4 |
| **디스크** | NVMe SSD 512 GB |
| **네트워크** | 1 Gbps Ethernet |

### 소프트웨어

| 구성 요소 | 버전 |
|----------|------|
| **OS** | Windows 10 Pro / Ubuntu 22.04 |
| **Docker** | 24.0.7 |
| **Python** | 3.11.6 |
| **TimescaleDB** | 2.13.0 (PostgreSQL 15) |
| **Simple Collector** | 0.2.0-beta |

### 대상 PLC

| 항목 | 값 |
|------|-----|
| **제조사** | Modbus Simulator |
| **프로토콜** | Modbus TCP |
| **주소** | 192.168.0.10:502 |

---

## 벤치마크 결과

### 5,000 태그 스트레스 테스트

!!! success "테스트 통과"
    5,000개 태그 동시 수집 및 저장 성공

#### 테스트 구성

```yaml
collector:
  plc_id: 99
  name: "StressTest_Modbus"

  collection_groups:
    - name: fast
      interval_ms: 1000      # 3,001 태그
    - name: normal
      interval_ms: 5000      # 960 태그
    - name: slow
      interval_ms: 30000     # 1,039 태그

buffer:
  max_size: 100000
  batch_size: 500

publisher:
  publish_interval_ms: 500
  database:
    pool_size: 10
```

#### 결과 요약

| 지표 | 결과 |
|------|------|
| **총 태그 수** | 5,000 |
| **테스트 시간** | 139.4초 |
| **총 수집 레코드** | 378,150 |
| **초당 처리량** | **2,712 records/sec** |
| **태그 커버리지** | 100% (5,000/5,000) |
| **데이터 품질** | 100% (quality_code = 1) |
| **메모리 사용량** | ~58 MB |

#### 초당 처리량 분포

```
시간          레코드 수    태그 수
07:05:42      3,460       3,460
07:05:41      3,001       3,001
07:05:40      3,001       3,001
07:05:39      3,001       3,001
07:05:37      3,961       3,961
```

> fast 그룹(3,001 태그)이 매초 안정적으로 수집됨

---

### 수집 그룹별 성능

| 그룹 | 태그 수 | 주기 | 읽기 그룹 | 평균 응답시간 |
|------|--------|------|----------|--------------|
| **fast** | 3,001 | 1초 | 58 | 120ms |
| **normal** | 960 | 5초 | 55 | 85ms |
| **slow** | 1,039 | 30초 | 56 | 95ms |

#### 읽기 최적화 효과

```
최적화 전: 5,000 태그 × 1 읽기 = 5,000회 읽기
최적화 후: 169 읽기 그룹 (연속 주소 병합)

→ 읽기 횟수 96.6% 감소
```

---

### 데이터베이스 성능

#### INSERT 성능

| 방식 | 처리량 | 지연시간 |
|------|--------|---------|
| **단일 INSERT** | ~500 rec/sec | 2ms/rec |
| **Batch INSERT** | ~2,000 rec/sec | 0.5ms/rec |
| **COPY 프로토콜** | ~5,000 rec/sec | 0.2ms/rec |

> Simple Collector는 **COPY 프로토콜**을 사용하여 최고 성능 달성

#### 스토리지 효율

| 항목 | 값 |
|------|-----|
| **레코드당 크기** | ~85 bytes |
| **압축 후 크기** | ~32 bytes |
| **압축률** | 62% |
| **일일 데이터량** (5,000 태그, 1초 주기) | ~12 GB |
| **압축 후** | ~4.5 GB |

---

### 리소스 사용량

#### CPU 사용률

| 태그 수 | 수집 주기 | CPU 사용률 |
|--------|----------|-----------|
| 100 | 1초 | < 1% |
| 1,000 | 1초 | 2-3% |
| 5,000 | 1초 | 5-8% |
| 10,000 | 1초 | 10-15% |

#### 메모리 사용량

| 태그 수 | 메모리 (정상) | 메모리 (버퍼 풀) |
|--------|--------------|-----------------|
| 100 | 25 MB | 30 MB |
| 1,000 | 35 MB | 50 MB |
| 5,000 | 58 MB | 120 MB |
| 10,000 | 95 MB | 250 MB |

---

## 성능 최적화 가이드

### 1. 수집 최적화

#### 연속 주소 배치

```csv title="tags.csv - 최적화된 배치"
# 좋은 예: 연속 주소
tag_id,tag_name,memory,address,data_type
1,Tag_001,HR,0,uint16
2,Tag_002,HR,1,uint16
3,Tag_003,HR,2,uint16
4,Tag_004,HR,3,uint16
5,Tag_005,HR,4,uint16

# 나쁜 예: 분산 주소
tag_id,tag_name,memory,address,data_type
1,Tag_001,HR,0,uint16
2,Tag_002,HR,100,uint16    # 100 레지스터 건너뜀
3,Tag_003,HR,500,uint16    # 400 레지스터 건너뜀
```

#### 수집 그룹 분리

```yaml title="collector.yaml"
collection_groups:
  # 중요/빈번한 데이터
  - name: critical
    interval_ms: 100       # 100ms
    timeout_ms: 500
    tags: ["Temperature", "Pressure", "Alarm"]

  # 일반 데이터
  - name: normal
    interval_ms: 1000      # 1초
    timeout_ms: 3000

  # 설정/상태 데이터
  - name: slow
    interval_ms: 30000     # 30초
    timeout_ms: 10000
```

### 2. 발행 최적화

#### 배치 크기 조정

```yaml title="collector.yaml"
buffer:
  max_size: 100000        # 최대 버퍼
  batch_size: 500         # 배치 크기 (권장: 500-1000)
  threshold_ratio: 0.8    # 80%에서 경고

publisher:
  publish_interval_ms: 500  # 발행 주기
  database:
    pool_size: 10           # 커넥션 풀
```

#### 배치 크기별 성능

| 배치 크기 | 처리량 | 지연시간 | 권장 |
|----------|--------|---------|------|
| 100 | 1,500 rec/sec | 67ms | 저지연 필요 시 |
| 500 | 3,500 rec/sec | 143ms | **권장** |
| 1000 | 4,500 rec/sec | 222ms | 고처리량 |
| 2000 | 5,000 rec/sec | 400ms | 최대 처리량 |

### 3. 데이터베이스 최적화

#### TimescaleDB 설정

```sql
-- 청크 간격 설정 (데이터량에 따라 조정)
SELECT set_chunk_time_interval('modbus.plc_data_integrated', INTERVAL '1 day');

-- 압축 정책 (7일 이상 된 데이터)
SELECT add_compression_policy('modbus.plc_data_integrated', INTERVAL '7 days');

-- 리텐션 정책 (90일 보관)
SELECT add_retention_policy('modbus.plc_data_integrated', INTERVAL '90 days');

-- 인덱스 최적화
CREATE INDEX CONCURRENTLY idx_plc_data_plc_tag_time
ON modbus.plc_data_integrated (plc_id, tag_id, source_time DESC);
```

#### 연결 풀 최적화

```ini title="postgresql.conf"
# 연결 설정
max_connections = 200
shared_buffers = 4GB        # RAM의 25%
effective_cache_size = 12GB # RAM의 75%
work_mem = 64MB

# WAL 설정
wal_level = minimal         # 복제 불필요 시
max_wal_size = 4GB
min_wal_size = 1GB
```

---

## 확장성 테스트

### 수평 확장 (다중 Collector)

| 구성 | 총 태그 수 | 총 처리량 |
|------|-----------|----------|
| 1 Collector | 5,000 | 2,700 rec/sec |
| 2 Collector | 10,000 | 5,200 rec/sec |
| 4 Collector | 20,000 | 10,000 rec/sec |

### 제약 사항

| 구성 요소 | 권장 최대값 | 비고 |
|----------|-----------|------|
| 단일 Collector 태그 수 | 10,000 | CPU 의존 |
| DB 커넥션 풀 | 20 | DB max_connections 고려 |
| 버퍼 크기 | 500,000 | 메모리 의존 |
| 배치 크기 | 2,000 | 트랜잭션 크기 고려 |

---

## 모니터링 쿼리

### 실시간 처리량

```sql
SELECT
    date_trunc('minute', server_time) as minute,
    COUNT(*) as records,
    COUNT(DISTINCT tag_id) as unique_tags,
    COUNT(*) / 60.0 as records_per_second
FROM modbus.plc_data_integrated
WHERE server_time > NOW() - INTERVAL '10 minutes'
GROUP BY minute
ORDER BY minute DESC;
```

### 품질 모니터링

```sql
SELECT
    quality_code,
    q.description,
    COUNT(*) as count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) as percentage
FROM modbus.plc_data_integrated p
JOIN modbus.quality_code_master q USING (quality_code)
WHERE server_time > NOW() - INTERVAL '1 hour'
GROUP BY quality_code, q.description
ORDER BY count DESC;
```

### 지연 시간 분석

```sql
SELECT
    tag_id,
    AVG(EXTRACT(EPOCH FROM (server_time - source_time))) * 1000 as avg_latency_ms,
    MAX(EXTRACT(EPOCH FROM (server_time - source_time))) * 1000 as max_latency_ms
FROM modbus.plc_data_integrated
WHERE server_time > NOW() - INTERVAL '1 hour'
GROUP BY tag_id
ORDER BY avg_latency_ms DESC
LIMIT 10;
```

---

## 결론

| 지표 | 목표 | 달성 | 상태 |
|------|------|------|------|
| 초당 처리량 | 2,000+ | 2,712 | :material-check-circle:{ .success } |
| 데이터 품질 | 99%+ | 100% | :material-check-circle:{ .success } |
| 메모리 사용량 | < 100 MB | 58 MB | :material-check-circle:{ .success } |
| CPU 사용률 | < 20% | 5-8% | :material-check-circle:{ .success } |
| 지연 시간 | < 1초 | ~200ms | :material-check-circle:{ .success } |

**종합 평가**: 프로덕션 배포 준비 완료

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

*테스트 일자: 2026-02-05*

</div>
