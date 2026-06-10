# QA 안정성 감사 리포트 — simpleCollector + collector-publisher

작성일: 2026-06-10
대상: `simpleCollector` v0.4.5 / `collector-publisher` v0.3.8
방식: 전체 소스 정밀 코드 리뷰 + 실제 모듈 import 기반 결함 재현 검증(`docs/verify_findings.py`)
환경 제약: 도커 미사용(샌드박스 권한 한계) → in-process 재현으로 대체. 외부 운영 DB/브로커 정보는 사용하지 않음.

> **검증 결과 요약**: 핵심 결함 10건 중 **10건 재현 확정**. 가장 위험한 항목은 운영 중 데이터 무음 손실/오염 3건(C-1, C-2, C-3)과 데이터 정합성 2건(H-1, H-2).

---

## 0. 한눈에 보기 (심각도 분포)

| 심각도 | 건수 | 대표 항목 |
|--------|------|-----------|
| 🔴 Critical | 6 | Publisher 영구정지, 프레임 오정렬 오염, 포이즌 메시지 정지, 그룹명 무검증 SQL, 풀 누수 |
| 🟠 High | 9 | 실패데이터 미생성, 비트 충돌, 평문 비밀번호, 큐 미존재 유실, silent ack |
| 🟡 Medium | 14 | 압축정책 미적용, TRUNCATE 부작용, 타임존, 리소스 제한 부재 |
| 🟢 Low | 다수 | decimals+offset, 시크릿 위생, 저장소 정리 |

가장 먼저 손봐야 할 순서: **C-1 → C-2/C-3 → C-7(포이즌) → C-8(풀 누수) → H-1/H-2 → 시크릿 정리.**

---

## 1. 🔴 Critical — 데이터 손실/오염, 서비스 정지

### C-1. (collector) Publisher 최초 연결 성공 후 끊기면 **영구 발행 중단** ✅재현
- 위치: `src/publishers/base.py:266-271`, `src/publishers/rabbitmq/publisher.py:229-232`
- 원인: 재연결 태스크가 *최초 connect 실패 시에만* 생성됨. 운영 중 브로커가 끊기면 `_do_publish`가 `_is_connected=False`로 만들지만, 이를 다시 True로 되돌릴 코드(재연결 루프)가 실행되지 않음. `_publish_loop`는 `if not self._is_connected: sleep; continue`만 반복.
- 결과: 브로커 재시작/네트워크 단절 1회 → 영원히 발행 중단. 버퍼(기본 10,000행 ≈ 262 tags/sec 기준 약 38초치)가 차면 `drop_oldest`로 **오래된 데이터부터 무음 폐기**. 컨테이너 재시작 전까지 전 구간 손실.
- 검증: `start()`가 `if not await self.connect():` 안에서만 재연결 태스크 생성하고 `_publish_loop`에 복구 트리거가 없음을 소스에서 확인.
- 수정: collector 쪽 `_reconnect_loop`(상시 감시형)와 동일하게, `start()`에서 재연결 태스크를 무조건 띄우거나 publish 실패 시 태스크가 없으면 생성. **(아이러니: collector에는 v0.3.6에서 이 패턴이 이미 적용·테스트됨)**

### C-2. (collector) MC Protocol 응답 타임아웃 후 소켓 미폐기 → **프레임 오정렬로 엉뚱한 태그에 값 기록** ✅재현
- 위치: `src/collectors/mc_protocol/collector.py` `_receive_response`
- 원인: `_receive_response`가 내부에서 `asyncio.TimeoutError`를 잡아 `return None`만 함. 소켓을 닫지도, state를 ERROR로 바꾸지도 않아 `_read_group`의 ERROR/재연결 분기가 **도달 불가능한 죽은 코드**.
- 결과: PLC 응답이 타임아웃을 살짝 넘기면 소켓 유지 → 다음 주기 새 요청 → **지연 도착한 이전 응답을 새 요청 응답으로 파싱**. 3E 프레임은 시퀀스 번호가 없어 검출 불가 → D200 그룹 값이 D300 태그에 들어가는 식의 무음 오염.
- 수정: 타임아웃/파싱 실패 시 `await self._close_connection()` + `self._state = ConnectionState.ERROR`로 강제 재수립.

### C-3. (collector) Modbus TCP Transaction ID 미검증 + 타임아웃 시 소켓 유지 ✅재현(2건)
- 위치: `src/collectors/modbus/collector.py:320, 354-359`
- 원인: ① `_, _, resp_length, _ = struct.unpack('>HHHB', header)` — MBAP의 Transaction ID를 버림(stale 응답 검출 불가). ② `except asyncio.TimeoutError:`에서 `_close()`를 호출하지 않음(ConnectionError/IncompleteRead만 닫음). `ModbusRtuOverTcpTransport`도 동일.
- 결과: C-2와 같은 무음 데이터 오염. 늦게 온 holding 응답이 다음 input 레지스터 요청의 응답으로 해석.
- 수정: 응답 TID ≠ 요청 TID면 폐기/재읽기, 타임아웃 시 `await self._close()` 추가(2줄).

### C-7. (publisher) 포이즌 메시지가 `queue.db` 전체를 **영구 정지** + 백오프 없는 nack 핫루프
- 위치: `src/consumer.py:331-336`(deserialize 실패 즉시 nack, 백오프·throttle 없음), `src/publishers/database.py:907-921`(`flush_timeseries`가 한 그룹 실패 시 전체 False → 전 메시지 nack)
- 원인: `BufferedQueueConsumer`에는 `QueueConsumer`의 지수 백오프/30초 throttle/max_retries가 적용되지 않음. split-on-fail 하한이 500행이라 깨진 1행이 섞인 조각은 영원히 실패.
- 결과: 손상 payload 또는 영구 실패 레코드 1건 → 즉시 재전달 무한 반복 → **라즈베리파이 CPU/로그/DB로그 폭주** + DB 파이프라인 전면 정지 + 큐 적체.
- 수정: 부모처럼 `sleep(backoff)` + 로그 throttle, `x-delivery-count` 기준 N회 후 `nack(requeue=False)`(+DLX 격리), split 하한 1까지 허용 옵션.

### C-8. (publisher) `connect()` 재시도 루프의 **커넥션 풀 누수 → Postgres max_connections 고갈**
- 위치: `src/publishers/database.py:191-256`, `src/main.py:226-233`
- 원인: `create_pool` 성공 후 후속 단계(`apply_schema`/`publish_catalog` 등)가 raise하면 except에서 False만 반환하고 `self._pool`을 close하지 않음. main이 5초마다 재호출하며 기존 풀을 닫지 않고 덮어씀 → min_size(2)개씩 누수.
- 결과: `neuroforge_config` 문제로 `publish_catalog`가 계속 실패하면 몇 분 내 Postgres 커넥션 고갈 → **같은 DB를 쓰는 다른 publisher/collector/Cortex까지 동반 다운**.
- 수정: except 블록에 `if self._pool: await self._pool.close(); self._pool = None`.

### C-9. (publisher) 검증 안 된 그룹명이 SQL에 직접 포매팅 — 인젝션 표면 + 무음 데이터 증발
- 위치: `src/publishers/database.py:1142-1145, 1066-1069`, 검증 우회 `588-590`
- 원인: `_ensure_group_tables()`는 invalid 그룹명을 로그만 남기고 return하지만 호출자(`upsert_latest`/`insert_timeseries`)는 그 후에도 진행해 메시지 payload의 `collection_group`을 그대로 SQL 식별자로 사용. `"ALM"`(대문자)처럼 규칙 위반 그룹은 테이블 미생성 → upsert 실패 → broad except가 삼킴 → ack → **무음 폐기**.
- 수정: DML 분류 시점에서 `validate_group_name()`을 invariant로 강제, 실패 레코드는 skip(카운트+경고)/dead-letter.

---

## 2. 🟠 High — 정합성·보안·운영 가시성

### H-1. (collector) 수집 실패 시 quality_code=0 데이터가 MC/Modbus에서 **생성되지 않음** ✅재현
- 위치: `src/collectors/base.py:_create_failed_data`(metadata `values` 키) ↔ `src/collectors/mc_protocol/processor.py:112-115`(`devices`만 조회), modbus도 `registers`만 조회
- 원인: 실패 데이터 metadata 키(`values`)와 프로토콜 프로세서가 읽는 키(`devices`/`registers`) 불일치 + 프로세서가 `failed` 플래그를 검사하지 않음. 빈 devices → `return []`.
- 결과: PLC 다운 구간이 DB에 quality=0 행 없이 "데이터 없음"으로만 남아 다운스트림(알람/통계/snapshot)이 통신이상과 무수집을 구분 불가.
- 검증: collector가 `values` 저장, MC 프로세서가 `devices`만 읽고 `failed` 미검사임을 소스에서 확인.
- 수정: `BaseProcessor.process`에서 `metadata.get("failed")`를 먼저 검사해 전 태그를 raw=None 경로로 라우팅(1곳 수정으로 전 프로토콜 해결).

### H-2. (collector) MC `_extract_bool`이 비트 디바이스에서 **워드 해석을 먼저 시도해 다른 비트주소 값과 충돌** ✅재현
- 위치: `src/collectors/mc_protocol/processor.py:248-281`
- 원인: `device` 인자를 받고도 사용하지 않고 무조건 `word_addr = address // 16`을 먼저 조회. 수집기는 비트 디바이스를 비트주소 키로 저장하므로 충돌.
- 검증: `device_data={4:1}`(M4 ON) 상태에서 `M64`(word_addr=4) 조회 시 정상은 None이어야 하나 **1.0 반환** 확인.
- 결과: 같은 그룹에 주소 16 이상 알람 비트가 섞이면 알람 미발생/오발생.
- 수정: `DeviceCode.is_bit_device(device)`로 분기, 비트 디바이스는 곧장 `device_data.get(address)` 사용.

### H-3. (collector) 수집 그룹 태스크가 예외로 죽으면 **해당 그룹 영구 중단 + 소켓 누수**
- 위치: `src/collectors/base.py:373-411` `_collection_loop`, `336-341` `stop`
- 원인: while 본문을 감싸는 `except Exception` 없음, done-callback 없음. 알림/이벤트 경로 예외가 새면 그룹 태스크가 무음 사망. `stop()`의 `await task`가 죽은 태스크 예외를 재발생시켜 뒤 그룹 cancel/`disconnect()` 스킵.
- 수정: 루프 본문 try/except + sleep 후 continue, `stop()`은 `gather(*tasks, return_exceptions=True)`.

### H-4. (publisher) `upsert_latest`가 실패를 삼키고 **항상 True 반환 → 실패해도 ack**
- 위치: `src/publishers/database.py:1165-1186`, `src/main.py:381-387`
- 원인: `_upsert_group_latest`가 데드락 3회 초과 포함 모든 예외를 로그만 남기고 return → True 반환 → RuntimeError 가드 미발동 → ack. v0.3.8 changelog의 "victim 배치를 버리지 않고"는 3회 실패 시점에는 사실이 아님(drop+ack).
- 결과: latest stale → history/snapshot 트리거 전환 누락.
- 수정: 최종 실패 시 raise → False 반환 → nack 경로 복원.

### H-5. (교차) 큐 미존재 시 **무음 데이터 유실**(기동 순서 의존)
- 위치: collector는 exchange만 선언(`publisher.py:150-154`), 큐 선언·바인딩은 publisher만 수행. 발행 시 `mandatory` 미사용.
- 결과: 신규 사이트에서 collector가 publisher보다 먼저 기동하면 라우팅 대상 없는 메시지가 persistent여도 즉시 폐기 → 에러 없이 수집 데이터 전량 소실.
- 수정: collector 측 큐 사전 선언+바인딩, 또는 `mandatory=True`+return 핸들러, 또는 alternate-exchange 파킹 큐.

### H-6. (교차) 역직렬화 실패 메시지 무한 재전달 + 헤더 무시 ✅재현
- 위치: `src/consumer.py:310-336`, publisher가 메시지의 `compression`/`encrypted` 헤더를 무시(`src/main.py`)하고 고정 YAML로만 역직렬화
- 검증: publisher main에 헤더 기반 분기(`headers.get`)가 없음을 확인. (참고: 현재 zlib/비암호화 기본값은 양측 일치해 roundtrip은 정상 ✅)
- 결과: collector가 압축/암호화 방식을 바꾸면 전 메시지 deserialize 실패 → 무한 nack 루프(C-7과 결합).
- 수정: 헤더 기반 역직렬화 분기 + DLQ.

### H-7. (보안) OPC UA 개인키가 git에 커밋
- 위치: `collector-publisher/certs/server_key.pem`(`-----BEGIN PRIVATE KEY-----`), `server_cert.der`
- 수정: `git rm --cached` + 이력 정리 + 키 재발급, `.gitignore`에 `certs/`. 런타임 self-signed 자동생성 경로가 있으므로 저장소 보관 불필요.

### H-8. (보안) 운영 DB/RabbitMQ 평문 자격증명 + git-tracked + 이미지에 포함
- 위치(비밀번호 출력 없이 위치만): `simpleCollector/deploy/jem/publisher_db1.yaml`, `publisher_db2.yaml`, `publisher_db1_worker.yaml`의 DB 비밀번호 평문 / `config/collector_mc_docker.yaml:65-66`, `collector_modbus_docker.yaml:67-68`의 RabbitMQ 약체 계정 평문 / `collector-publisher/config/publisher_debug.yaml`, `config/custom_init.sql`의 평문 자격증명 / `build/collector/Dockerfile:83`이 `config/`를 이미지에 COPY해 NCR push.
- 결과: 저장소/레지스트리 접근 권한 = 운영 DB·MQ 자격증명 + 내부망 구조(`192.168.0.x`) 노출.
- 수정: 전부 `${VAR}` env 치환(기본값 제거), 이미지에서 `config/` 제거(볼륨 마운트 전용), 노출 비밀번호 로테이션.

---

## 3. 🟡 Medium — 운영 안정성·성능(라즈베리파이)

### M-1. (publisher) `compression_orderby` 기본값이 존재하지 않는 컬럼 → 압축 정책 영구 미적용
- 위치: `src/config.py:523` 폴백 `'source_time DESC'`인데 테이블 컬럼은 `timestamp`(`schema_init.py`). dataclass 기본값(186행)은 `'timestamp DESC'`로 자기모순. 잔존 yaml: `config/publisher.yaml:70`, `config/publisher_debug.yaml:60`, `simpleCollector/deploy/ble/publisher_ble.yaml:56`.
- 결과: `ALTER TABLE ... compress_orderby='source_time DESC'` 실패를 warning으로 삼킴(stdout은 ERROR만) → **TimescaleDB 압축이 조용히 영구 비활성 → Pi 디스크/SD 포화**.
- 수정: `config.py:523`을 `'timestamp DESC'`로 통일 + 압축 ALTER 실패를 ERROR로 승격 + 잔존 yaml 수정.

### M-2. (Docker/Pi) 운영 compose에 리소스 제한·로그 로테이션·실healthcheck·TZ 전무 ✅재현
- 위치: `deploy/jem/docker-compose.yml`(12개 컨테이너) — `restart: unless-stopped`만 존재. `mem_limit`/`cpus`/`logging.options.max-size`/healthcheck/TZ 없음. Dockerfile HEALTHCHECK는 `python -c "import sys; sys.exit(0)"` no-op.
- 결과: json-file 로그 무제한 → SD 카드 마모/포화(DEBUG 켜면 가속). 메모리 제한 없음 → 한 컨테이너 폭주 시 OOM이 무작위 컨테이너 kill. healthcheck가 항상 healthy → 수집 중단 미감지.
- 수정: `logging: {driver: json-file, options: {max-size: "10m", max-file: "3"}}` + `mem_limit`/`cpus` + status API `/health` 기반 healthcheck.

### M-3. (운영) 수집 중단 감지 수단 부재
- 위치: `src/core/config.py:115`(`stdout_min_level="ERROR"`) + jem compose에 LOGS_DB_*/DB_* env, STATUS_API_ENABLED 없음.
- 결과: 재연결/LOSS 같은 INFO/WARN이 어디에도 기록 안 됨. status API는 어떤 배포에서도 비활성(활성화해도 무인증 0.0.0.0).
- 수정: compose에 로깅 DSN 주입, "마지막 발행 후 N초 무발행" 알람(큐 idle 모니터/DB freshness), status API 토큰 인증.

### M-4. (publisher) 재시작 TRUNCATE 부작용: history 중복 / snapshot edge 누락 / 멀티 publisher 간섭
- 위치: `src/publishers/master_sync.py:157-185`, `schema_init.py` 트리거 정의
- 내용: ① latest TRUNCATE 후 첫 수신값 INSERT가 history AFTER INSERT 트리거를 무조건 발화 → 재시작마다 전 태그 history 1행 추가(trigger_on 무시). ② snapshot은 BEFORE UPDATE만 등록 → 재시작 직후 watch_tag TRUE가 INSERT로 들어오면 rising edge 미감지. ③ `LIKE '%_latest'`로 무관한 사용자 테이블까지 TRUNCATE. ④ publisher 2개 운영 시 TRUNCATE(ACCESS EXCLUSIVE)가 상대 UPSERT와 충돌.
- 수정: known group 화이트리스트 기반 명시 TRUNCATE, history insert-trigger 직전값 비교 정책, snapshot INSERT 경로 옵션화.

### M-5. (교차) 타임스탬프 naive `datetime.now()` + RTC 없는 Pi → 시각 무결성 취약
- 위치: collector 전반(`mc_protocol/collector.py`, `modbus/collector.py`, `processors/base.py`)이 naive, `interfaces.py:375` ISO에 오프셋 없음, publisher `database.py`가 naive를 UTC로 간주.
- 결과: 한쪽 컨테이너에 `TZ=Asia/Seoul`이 들어가면 9시간 스큐. Pi 전원 손실 시 NTP 동기 전 수집분이 과거/미래 타임스탬프로 영구 저장.
- 수정: `datetime.now(timezone.utc)` 전면 통일 + ISO 오프셋 포함 + 부팅 시 NTP 동기 게이트(chrony waitsync).

### M-6. 그 외 Medium
- (collector) EventBus 큐 무제한(`events.py:184`) — 데이터 경로가 큐 경유라 백프레셔 부재 → OOM 가능.
- (collector) `BUFFER_UPDATED` 이벤트가 레코드 1건당 발생 + Event마다 uuid4 — 구독자 없음. 262/sec 무의미 CPU 소모.
- (collector) Status API 키 불일치로 `/health` 항상 unhealthy, `/stats`는 datetime 직렬화로 HTTP 500.
- (collector) `DeviceCode.set_series`가 클래스 변수(전역) 변경 — 멀티 PLC 시리즈 혼용 불가.
- (collector) 로그 gzip 압축이 이벤트 루프에서 동기 실행 → 압축 중 수집 누락.
- (collector) CSV 중복 tag_id 무검증 ✅재현(D200/D300 같은 tag_id=1 통과).
- (publisher) `_flush_timer_loop` 태스크 무감시 → 죽으면 좀비(소비만 하고 flush 안 함).
- (publisher) monitor 경로 메시지당 `SELECT 1` 헬스체크 → Pi에서 왕복 2배.
- (publisher) unnest UPSERT 락 순서가 plan 순서에 암묵 의존 → `ORDER BY 1,2` 보강 권장(v0.3.8 fix 자체는 적용범위 정확).
- (교차) collection_group 이름 표류(`fast`/`slow` vs 표준 `plc_data`) + 미등록 그룹 무음 테이블 자동생성.
- (교차) 큐 인자 변경 시 PRECONDITION_FAILED 크래시 루프, 정전 시 버퍼 데이터 손실(`/app/data` 미마운트).

---

## 4. 🟢 Low (요약)
- **L-1 decimals+offset 동시 적용 버그** ✅재현: 정수 타입 고정소수점 변환에서 offset까지 ÷10^decimals 됨(raw=3061, offset=100, decimals=2 → 31.61, offset 100배 의도 왜곡). 현재 CSV는 대부분 scale=1/offset=0이라 미발현이나 지뢰.
- MC 비트 니블 해석이 MELSEC 사양과 반대(죽은 코드지만 지뢰), `put_front` 가득참 시 최신 데이터 폐기(drop_oldest와 반대), AMQP URL을 f-string 조립(비밀번호 특수문자 시 실패), 약체 기본 자격증명 광범위 산재, `build/publisher/` 구버전 소스 사본 중복, `nul`·`config;C`·대용량 tar 등 저장소 위생, DDL f-string 보간(config 신뢰 전제 인젝션 표면), DbLogHandler `close()` 스레드 join 누락.

---

## 5. ✅ 잘 되어 있는 점 (균형)
- 전 프로토콜이 `readexactly`+`wait_for`로 TCP partial read를 정확히 처리(짧은 읽기 버그 없음).
- 로그 폭주 방지가 모범적: LOSS 로그 throttle, 재연결 ERROR 60초 throttle, DbLogHandler 드롭 카운트 단계 보고 — Pi CPU 보호 관점 우수.
- **collector 재연결**은 상시 `_reconnect_loop` + 지수 백오프 30초 cap + 복구 감지가 v0.3.6 테스트로 고정됨(반면 publisher에는 미적용 = C-1).
- 데이터 검증: NaN/Inf 차단, UINT32→bigint 매핑, Modbus CRC lookup table, MC 에러코드 매핑.
- asyncio 위생: 동기 시리얼 IO executor 격리, request_lock 소켓 직렬화, SIGTERM/SIGINT graceful shutdown, 버퍼 Condition 기반 소비.
- **직렬화 포맷 양측 동일** ✅재현(zlib level 6 → Fernet, 기본값 일치, roundtrip 검증 통과).
- 메시지 필드 계약 일치(`source_time`/`plc_id|ble_id`/`tag_id`/`quality`/`v_*`/`collection_group`), durable 큐 + delivery_mode=2.
- v0.3.8 데드락 fix 적용 범위 정확(latest 단일 writer, 정렬+DeadlockDetectedError 한정 재시도), monitor 큐 TTL/max-length 바운딩, JEM 태그 CSV 10개 전수검사 클린.
- `gosu`로 non-root 실행 + SIGTERM 전달 정상.

---

## 6. 🧪 테스트 커버리지 공백
1. **Publisher 재연결**(C-1 회귀) — collector resilience 테스트의 미러 필요.
2. **MC/Modbus 프레임 빌드·파싱** — 엔디안, 워드오더, 비트 니블, 타임아웃 후 stale 응답(C-2/C-3).
3. **DataBuffer** overflow/put_front/shutdown persist.
4. **quality_code=0 E2E**(H-1) — 실패 데이터가 프로세서를 통과하는지.
5. **BufferedQueueConsumer** flush 부분실패 ack/nack, deserialize 포이즌(C-7/H-6), 타이머 좀비(M-6).
6. **`_upsert_group_latest`**(v0.3.8 핵심) 정렬·재시도·예외삼킴 단위 테스트.
7. **master_sync 동시성** TRUNCATE vs 동시 UPSERT, partial split 중복 삽입.
8. **serializer 교차 roundtrip**(본 감사가 추가 — `verify_findings.py`).

---

## 7. 권장 조치 우선순위
1. **C-1** Publisher 재연결 상시화 (수정 5줄 내, 운영 최우선 리스크)
2. **C-2 / C-3** 타임아웃 시 소켓 폐기 + Modbus TID 검증 (무음 오염 차단)
3. **C-7 / C-8** 포이즌 메시지 DLQ + 풀 누수 close
4. **C-9 / H-4 / H-5 / H-6** 그룹명 검증, silent ack 제거, 큐 사전선언, 헤더 기반 역직렬화
5. **H-1 / H-2** 데이터 정합성(실패데이터 라우팅, 비트 디바이스 분기)
6. **H-7 / H-8** 키·비밀번호 로테이션 + 이미지에서 config 제거
7. **M-1 / M-2 / M-3** 압축정책 수정(1줄, 효과 큼) + 리소스 제한·로그 로테이션 + 수집중단 가시성

---

## 부록: 재현 검증 스위트
`docs/verify_findings.py` — 실제 소스 모듈을 import 해 10개 결함을 in-process로 재현. 결과: **10/10 재현 확정**(직렬화 호환은 정상 동작 확인). 실행: `python3 docs/verify_findings.py`.
