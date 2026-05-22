# Publisher v0.2.1 배포 가이드 (2026-03-10)

## 변경 내용

DB 장애 복원력 개선 — TimescaleDB 재시작 시 publisher가 자동 복구되지 않던 버그 수정

| 수정 | 설명 |
|------|------|
| DB 재연결 지수 백오프 | `asyncio.Lock` + 지수 백오프 (1→2→4→8→10초 cap), 동시 재연결 방지 |
| pool acquire timeout | 모든 `pool.acquire()`에 `timeout=10` 추가 |
| queue.monitor 무제한 재시도 | `max_retries=0` → TTL/max-length로만 바운딩 (메시지 유실 방지) |
| flush 백오프 cap 축소 | 30초 → 10초 (복구 속도 개선) |
| 내장 Scheduler | `scheduler:` YAML 설정으로 cron job 실행 (pg_cron 불필요) |

## 가져갈 파일

```
deploy/jem/
├── neuroforge_publisher.tar          ← Docker 이미지 (v0.2.1, ARM64, 69MB)
├── publisher_db1.yaml           ← 설정 (변경 없음, 기존과 동일)
└── publisher_db2.yaml           ← 설정 (변경 없음, 기존과 동일)
```

**neuroforge_publisher.tar만 가져가면 됩니다.**
- publisher_db1.yaml, publisher_db2.yaml은 이전 배포와 동일 (scheduler 설정 이미 포함)
- collector, tags CSV, custom_init.sql, docker-compose.yml 변경 없음

## 배포 절차

### 1. tar 파일 전송
```bash
# USB 또는 scp
scp deploy/jem/neuroforge_publisher.tar pi@192.168.0.236:/home/pi/deploy/
```

### 2. 이미지 로드
```bash
docker load -i /home/pi/deploy/neuroforge_publisher.tar
# → neuroforge_publisher:latest
```

### 3. publisher 컨테이너 재시작
```bash
cd /home/pi/deploy    # docker-compose.yml 위치

# publisher만 재시작 (collector 중단 없음)
docker compose restart publisher-db1 publisher-db2

# 또는 개별 재시작
docker restart jem-publisher-db1
docker restart jem-publisher-db2
```

### 4. 로그 확인
```bash
# 버전 확인
docker logs jem-publisher-db1 2>&1 | head -20
# → "NeuroForge Publisher v0.2.1 (build: 2026-03-10)" 확인

# 정상 동작 확인 (30초 관찰)
docker logs -f jem-publisher-db1 2>&1 | grep -E "DB|scheduler|reconnect"
```

## 확인 포인트

| 확인 | 예상 로그 |
|------|----------|
| 버전 | `v0.2.1 (build: 2026-03-10)` |
| Scheduler 시작 | `Scheduler started with 3 jobs` |
| Custom SQL | `Custom SQL completed: N executed, M already exist, 5 errors` (트리거 중복은 정상) |
| DB 연결 | `DB pool created` |
| 데이터 처리 | `Batch COPY: N rows` |

## 롤백

문제 발생 시 이전 이미지로 즉시 롤백:
```bash
# 이전 tar 백업해둔 경우
docker load -i neuroforge_publisher_backup.tar
docker compose restart publisher-db1 publisher-db2
```

## 주의사항

- collector(simpleCollector) 변경 없음 → collector 재시작 불필요
- custom_init.sql 변경 없음 → DB 스키마 수동 작업 불필요
- publisher 재시작 시 master_sync 자동 실행 → `_master`, `_latest` 테이블 TRUNCATE 후 재구축 (정상 동작)
- scheduler가 기존 pg_cron job과 중복 실행될 수 있으므로, pg_cron에 동일 job이 있다면 제거 권장
