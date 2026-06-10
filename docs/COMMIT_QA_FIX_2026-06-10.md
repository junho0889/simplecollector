# QA 감사 Critical 수정 — 커밋/빌드 절차 (2026-06-10)

Cowork 샌드박스 mount 동기화 버그(수정 파일 꼬리 잘림) 때문에 커밋은 Windows에서 직접 실행할 것.
**주의**: 양쪽 repo에 이번 수정과 무관한 변경 파일들이 이미 있으므로 `git add -A` 금지, 아래 파일만 add.

## 1. simpleCollector (0.4.5 → 0.4.6)

```bat
cd /d D:\4.source\simpleCollector
git add src/publishers/base.py src/publishers/rabbitmq/publisher.py src/collectors/mc_protocol/collector.py src/collectors/modbus/collector.py src/collectors/mc_protocol/processor.py src/processors/base.py src/version.py deploy/metadata/collector.json CLAUDE.md docs/QA_AUDIT_REPORT_2026-06-10.md docs/COMMIT_QA_FIX_2026-06-10.md docs/verify_findings.py docs/regression_qa_critical_collector.py docs/regression_qa_high.py
git commit -m "fix: QA 감사 Critical 3건 + High 2건 — 상시 재연결, 타임아웃 소켓 폐기, TID 검증, 실패데이터 라우팅, 비트 충돌" -m "- (C-1) src/publishers/base.py: start()에서 _reconnect_loop 태스크를 무조건 기동 — 운영 중 브로커 단절 1회로 영구 발행 중단 + 버퍼 drop_oldest 무음 손실되던 문제 해결. rabbitmq/publisher.py _do_connect 시 이전 연결 잔존 리소스 정리
- (C-2) src/collectors/mc_protocol/collector.py: _receive_response 타임아웃/수신 오류 시 _close_connection() + state=ERROR — 지연 도착 응답이 다음 요청 응답으로 파싱되어(3E 프레임 시퀀스 없음) 엉뚱한 태그에 값 기록되던 프레임 오정렬 오염 차단
- (C-3) src/collectors/modbus/collector.py: MBAP Transaction ID 검증(stale 응답 시 소켓 폐기), TCP/RTU-over-TCP 타임아웃 시 _close(), RTU CRC 오류 시 소켓 폐기
- (H-1) src/processors/base.py: BaseProcessor.process가 metadata['failed'] 검사 → 수집 실패 시 전 태그 quality_code=0 행 생성 (전 프로토콜 적용, 통신이상과 무수집 구분 가능)
- (H-2) src/collectors/mc_protocol/processor.py: _extract_bool 비트 디바이스 분기 — 비트주소 직접 조회로 word_addr 충돌(알람 오발생/미발생) 차단
- 회귀 테스트: docs/regression_qa_critical_collector.py + docs/regression_qa_high.py (외부 인프라 불필요), verify_findings.py 탐지기 정밀화
- 버전 0.4.6 (version.py + deploy/metadata/collector.json + CLAUDE.md), 근거: docs/QA_AUDIT_REPORT_2026-06-10.md"
```

## 2. collector-publisher (0.3.8 → 0.3.9)

```bat
cd /d D:\4.source\collector-publisher
git add src/consumer.py src/publishers/database.py src/config.py src/version.py metadata.json docs/regression_qa_critical_publisher.py
git commit -m "fix: QA 감사 Critical 3건 + High 1건 — 포이즌 메시지 가드, DB 풀 누수 차단, 그룹명 검증, silent ack 제거" -m "- (C-7) src/consumer.py: BufferedQueueConsumer deserialize 실패에 지수 백오프(최대 10초) + 로그 throttle(30초) + x-delivery-count >= max_retries(기본 5) 시 nack(requeue=False) — 손상 payload 1건이 queue.db를 즉시 재전달 무한 핫루프로 정지시키던 문제 해결 (DLX 설정 시 격리)
- (C-8) src/publishers/database.py: connect()에서 create_pool 성공 후 후속 단계 실패 시 풀 close — main 5초 재시도마다 min_size개씩 누수되어 Postgres max_connections 고갈로 이어지던 문제 차단
- (C-9) src/publishers/database.py: insert_timeseries/upsert_latest 분류 시점에 _safe_group_name() 검증 — 규칙 위반 collection_group 레코드는 skip+카운트 경고(그룹당 1회), SQL 식별자 인젝션 표면 및 broad except에 삼켜지는 무음 데이터 증발 차단
- (H-4) src/publishers/database.py: _upsert_group_latest 최종 실패(데드락 3회 초과/일반 예외) 시 raise → upsert_latest False → nack 경로 복원 — silent ack로 배치 유실 + latest stale/트리거 전환 누락되던 문제 해결 (0.3.8 changelog의 'victim 배치 보존' 완결)
- (M-1) src/config.py: compression_orderby 폴백 'source_time DESC' → 'timestamp DESC' — 실제 컬럼명/dataclass 기본값과 통일. yaml 키 생략 시 압축 ALTER가 존재하지 않는 컬럼으로 실패해 TimescaleDB 압축이 조용히 비활성되던 지뢰 제거. 스키마/저장 경로 무변경, 운영 yaml(명시값)은 동작 동일
- 버전 0.3.9 (version.py + metadata.json)"
```

## 3. 빌드 + NCR push (커밋 후)

```bat
cd /d D:\4.source\simpleCollector
python deploy/jem_ble/build_deploy.py
bash scripts/push-to-ncr.sh
```

semver immutable GUARD가 v0.4.6 / v0.3.9 신규 태그로 정상 push할 것.

## 테스트 결과 (2026-06-10, 샌드박스 — 외부 인프라 불필요)

- **회귀 테스트 36/36 PASS** (Critical 20 + High 8 + 재실행 8):
  - `simpleCollector/docs/regression_qa_high.py` — H-1(실패데이터 quality=0 생성/정상경로 무영향),
    H-2(M64-M4 충돌 해소/비트 직접조회/워드 디바이스 유지), H-4(일반 예외·데드락 최종 실패 → False/nack) 8건
  - `simpleCollector/docs/regression_qa_critical_collector.py` — 실제 TCP 목 PLC 서버 기반 8건
    (Modbus TID 불일치/타임아웃/stale 오염 회귀, MC 타임아웃 소켓 폐기+ERROR, publisher 상시 재연결)
  - `collector-publisher/docs/regression_qa_critical_publisher.py` — 8건
    (포이즌 nack(requeue=False)/백오프 증가/정상 메시지 무영향, 풀 3회 누수 없음, 그룹명 판정+skip)
  - 추가 4건: RTU-over-TCP 타임아웃 폐기, 10회 반복 단절 soak 전부 복구 + 태스크 누수 0
- **verify_findings.py 재실행**: C-1/C-2/C-3 전부 NOT-REPRO 전환 (탐지기 2개 정밀화 포함)
- 실행법: `python3 docs/regression_qa_critical_collector.py` (각 repo에서)

### 알려둘 점
1. **C-7의 x-delivery-count는 quorum queue 전용** (RabbitMQ 3.12+). 현재 운영 토폴로지(classic durable)에서는
   임계 폐기 경로가 발동하지 않음 — 백오프+throttle로 핫루프는 차단되지만 포이즌 메시지는 계속 재시도됨.
   후속: queue.db를 quorum으로 전환하거나 body-hash 기반 카운팅 추가 검토.
2. 미수정 잔존(이번 범위 밖): H-3(수집 그룹 태스크 예외 가드), H-5(큐 미존재 무음 유실),
   H-6(헤더 무시 역직렬화), H-7/H-8(보안 — 키/평문 자격증명, 로테이션 필요), Medium 14건,
   L-1(decimals+offset), CSV 중복 tag_id — verify_findings.py 재실행 결과 REPRODUCED 4/10
   (serializer COMPAT 1건은 정상 의미, 나머지 3건: H-6 헤더/L-1/CSV 중복).

## 수정 요약 (검증: py_compile 6/6 통과, 수정 위치 전수 확인)

| 항목 | 파일 | 내용 |
|------|------|------|
| C-1 | publishers/base.py, rabbitmq/publisher.py | 재연결 태스크 상시 기동 + 재연결 시 잔존 연결 정리 |
| C-2 | mc_protocol/collector.py | 타임아웃/수신 오류 시 소켓 폐기 + ERROR 상태 |
| C-3 | modbus/collector.py | TID 검증 + 타임아웃/CRC 오류 시 소켓 폐기 |
| C-7 | consumer.py | 포이즌 메시지 백오프/throttle/재전달 제한 |
| C-8 | database.py connect() | 실패 시 풀 close (누수 차단) |
| C-9 | database.py | _safe_group_name() invariant (분류 시점 검증) |
