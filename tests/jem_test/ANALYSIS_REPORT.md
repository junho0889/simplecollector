# JEM 데이터 수집 파이프라인 분석 보고서

> 분석일: 2026-02-27
> 대상: simpleCollector + collector-publisher (JEM 진영전기 배포 설정)
> 분석 범위: 소스 코드 + deploy/jem 설정 + test_data.csv (실제 수집 데이터)

---

## 1. test_data.csv 데이터 정합성 분석

### 1.1 데이터 개요
- **파일**: `C:\Users\admin\Desktop\test_data.csv` (1198행, ~13초)
- **컬럼**: `timestamp, plc_id, tag_id, v_bool, v_int, v_bigint, v_float, v_text, quality_code`
- **PLC**: 1, 2, 3, 4, 6, 7, 8, 9, 10 (9개)
- **수집 그룹**: plc_data만 (alm 데이터 없음)
- **주기**: PLC당 ~1초 간격

### 1.2 PLC → 태그 CSV 매핑 (collector_plc*.yaml 기준)

| PLC ID | YAML | Tags CSV | 설명 | plc_data 태그수 | test_data 존재 |
|--------|------|----------|------|-----------------|---------------|
| 1 | collector_plc1.yaml | tags_04_PLC-A.csv | PS 접점 조립 | 9 | **OK** (9/9) |
| 2 | collector_plc2.yaml | tags_02_PLC-B.csv | YOKE 조립 | 9 | **OK** (9/9) |
| 3 | collector_plc3.yaml | tags_05_PLC-C.csv | TER 조립 | 9 | **OK** (9/9) |
| 4 | collector_plc4.yaml | tags_10_PLC-D.csv | 중간 검사 | 11 | **OK** (13 tag_ids) |
| 5 | collector_plc5.yaml | tags_01_PLC-EFG.csv | AIR 청소 | 6 | test_data 미포함 (운영 정상) |
| 6 | collector_plc6.yaml | tags_06_PLC-H.csv | 1차 SEAL | 9 | **OK** (9/9) |
| 7 | collector_plc7.yaml | tags_07_PLC-I.csv | 예비납땜 | 9 | **OK** (9/9) |
| 8 | collector_plc8.yaml | tags_03_PLC-J.csv | 최종검사 | 10 | **OK** (10/10) |
| 9 | collector_plc9.yaml | tags_08_PLC-K.csv | LASER 날인 | 9 | **OK** (10/10) |
| 10 | collector_plc10.yaml | tags_09_PLC-L.csv | 동작검사 | 6 | **OK** (9 tag_ids) |

### 1.3 값-타입 정합성 검증 결과

| 분류 | 태그 예시 | 데이터 타입 | CSV 컬럼 | 결과 |
|------|-----------|------------|----------|------|
| 생산수량 | D200 uint32 | v_bigint | 6번째 | **정상** |
| 합격률 | D206 float32 | v_float | 7번째 | **정상** |
| 불량률 | D208 float32 | v_float | 7번째 | **정상** |
| 실시간효율 | D212 int16 | v_int | 5번째 | **정상** |
| 사이클시간 | D216 uint32 scale=0.1 | v_float | 7번째 | **정상** (3.3 = raw 33 × 0.1) |
| 설비명 | D900 string | v_text | 8번째 | **정상** ("JH-2HO") |

### 1.4 PLC 5 (EFG) — test_data.csv 미포함 (운영 정상)
tags_01_PLC-EFG.csv에 plc_data 태그 6개 존재 (D230~D246).
test_data.csv에 PLC 5 데이터가 0건이지만, **실제 운영 환경에서는 정상 수집 중**.
test_data.csv 추출 시점 또는 추출 조건에 의한 누락으로 판단됨.

---

## 2. simpleCollector 소스 코드 이슈

### [C-01] CRITICAL: MC Protocol 비트 디바이스(M/X/Y) 패킹 형식 불일치
- **파일**: `src/collectors/mc_protocol/collector.py:890-899`
- **현상**: MC Protocol Binary 3E의 비트 응답은 **니블 패킹** (1바이트 = 2비트 값)
  - 하위 니블(0x0F) = 짝수 비트, 상위 니블(0xF0) = 홀수 비트
  - 현재 코드는 이 형식을 따르나, `& 0x01`로 마지막에 1비트만 추출
- **영향**: 비트 디바이스 값이 0 or 1로 올바르게 추출됨 (니블 값이 0x00=OFF, 0x01=ON)
- **실제 위험도**: **낮음** — MC Protocol Binary 3E에서 비트는 0x00 또는 0x01 니블 값으로 오므로 `& 0x01` 적용은 안전. 다만 ASCII 모드 사용 시 문제 가능.

### [C-02] HIGH: NaN/Inf 부동소수점 값 검증 누락
- **파일**: `src/core/interfaces.py:189-234` (`apply_scaling()`)
- **현상**: PLC에서 IEEE 754 NaN (센서 오류) 또는 Inf (0 나누기) 반환 시 검증 없이 그대로 전달
- **영향**: `round(NaN, 2)` → NaN → DB 저장 시 NULL 변환 또는 오류
- **권장**: float 타입 추출 후 `math.isfinite()` 검증 추가

### [C-03] HIGH: _last_request/_last_response_raw 디버그 버퍼 미정리
- **파일**: `src/collectors/mc_protocol/collector.py:852-863`
- **현상**: 매 요청마다 `self._last_request`, `self._last_response_raw` 덮어쓰기
- **영향**: GC가 이전 참조를 정리하므로 심각한 메모리 누수는 아님. 단, 대용량 응답(960워드 = 1920바이트) 참조가 항상 유지됨
- **실제 위험도**: **낮음** — 단일 버퍼 교체이므로 최대 ~2KB 유지

### [C-04] MEDIUM: ReadGroup 경계 검증 (960 워드 초과 가능성)
- **파일**: `src/collectors/mc_protocol/collector.py:514-516`
- **현상**: `total_size` 계산 시 마지막 태그의 `size`를 포함하지만, 검증 조건은 `total_size <= max_points`
- **영향**: 경계값에서 961워드 그룹 생성 가능 → PLC 에러 응답 0xC05B
- **발생 확률**: 태그 배치에 따라 드물게 발생

### [C-05] MEDIUM: 재연결 루프와 수집 루프 간 락 분리
- **파일**: `src/collectors/base.py:532-577` vs `src/collectors/mc_protocol/collector.py:822-863`
- **현상**: `_reconnect_loop()`는 `connect()` 호출, 수집 루프는 `_request_lock` 사용 → 서로 다른 락
- **영향**: 네트워크 불안정 시 재연결 직후 수집 요청이 죽은 소켓으로 전송될 수 있음
- **발생 확률**: 네트워크 장애 복구 시점에 간헐적 발생

### [C-06] LOW: on_change 값 캐시 크기 제한 없음
- **파일**: `src/processors/base.py:138-139`
- **현상**: `self._value_cache: Dict[int, Any]` 무제한 성장
- **영향**: JEM 환경(~2600 alm 태그 × 10 PLC) → 최대 ~200KB 수준으로 실질적 문제 없음

### [C-07] LOW: 재연결 백오프 코드 중복
- **파일**: `src/collectors/base.py:532-577` + `src/publishers/base.py:381-422`
- **현상**: 동일한 지수 백오프 로직이 Collector와 Publisher에 각각 구현
- **권장**: 공통 유틸리티로 추출

---

## 3. collector-publisher 소스 코드 이슈

### [P-01] CRITICAL: _parse_datetime() 파싱 실패 시 현재 시간으로 대체
- **파일**: `src/publishers/database.py:820-830`
- **코드**:
  ```python
  except (ValueError, TypeError):
      return datetime.now()  # 조용히 현재 시간 사용
  ```
- **영향**: 잘못된 timestamp 문자열이 들어오면 데이터가 실제 수집 시간이 아닌 현재 시간으로 저장
- **위험도**: 정상 운영 시 발생 확률 낮지만, 발생 시 시계열 데이터 무결성 파괴
- **권장**: WARNING 로그 + None 반환 (해당 레코드 스킵)

### [P-02] ~~HIGH~~ → LOW: BufferedQueueConsumer 버퍼 크기 제한
- **파일**: `src/consumer.py:254-292`
- **현상**: DB 장애 시 flush 실패 → nack(requeue=True) → 메시지가 RabbitMQ로 복귀
- **재검토**: prefetch_count=50으로 미처리 메시지 수가 제한되며, flush 실패 시 nack으로 버퍼를 비움.
  버퍼 크기 3배 초과 경고 로그도 존재. **실질적 메모리 폭발 위험 낮음**
- **수정 불필요**

### [P-03] HIGH: _ensure_group_tables() 동시성 보호 없음
- **파일**: `src/publishers/database.py:447-480`
- **현상**: queue.db와 queue.monitor 소비자가 동시에 새 그룹 감지 시 테이블 중복 생성 시도
- **영향**: `DuplicateTableError`는 catch하지만, hypertable 생성 레이스 가능
- **권장**: `asyncio.Lock()` 추가

### [P-04] MEDIUM: compression_orderby 기본값 불일치
- **파일**: `src/config.py:118` → 기본값 `"source_time DESC"`
- **실제 컬럼명**: `timestamp` (`schema_init.py:219`)
- **JEM 설정**: `publisher_db1.yaml:54` → `"timestamp DESC"` (올바름)
- **영향**: JEM에는 영향 없음. 하지만 기본값 사용하는 다른 배포에서 압축 정책 실패
- **권장**: `config.py` 기본값을 `"timestamp DESC"`로 수정

### [P-05] MEDIUM: COPY 실패 시 폴백 없음
- **파일**: `src/publishers/database.py:691-708`
- **현상**: COPY 중 단일 레코드 제약 위반 → 전체 배치 실패 → 메시지 nack → 재전달 → 무한 루프
- **권장**: COPY 실패 시 batch INSERT 폴백 또는 문제 레코드 격리

### [P-06] ~~MEDIUM~~ → LOW: quality_code 누락 시 기본값 1 (GOOD)
- **파일**: `src/publishers/database.py:817`
- **코드**: `record.get('quality', 1)`
- **재검토**: collector가 quality_code를 항상 포함하여 보내므로 누락 사례 없음.
  만약 누락되면 GOOD(1)으로 처리하는 것이 합리적 기본값.
- **수정 불필요**

### [P-07] LOW: 커스텀 SQL schema_name 검증 미흡
- **파일**: `src/publishers/database.py:270-346`
- **현상**: `{schema}` 플레이스홀더가 단순 문자열 치환으로 처리
- **영향**: schema_name에 SQL 특수문자 포함 시 위험하지만, 설정 파일에서만 입력되므로 실질적 위험 낮음

### [P-08] LOW: 셧다운 시 QueueConsumer 미처리 메시지 유실 가능
- **파일**: `src/main.py:301-307`
- **영향**: 정상 종료 시 BufferedQueueConsumer는 잔여 버퍼 flush하지만, 처리 중인 메시지는 유실 가능

---

## 4. 설정 파일 이슈

### [CF-01] ~~PLC 5 (EFG) plc_data 태그 정의 확인 필요~~ → 정상 확인
- `tags_01_PLC-EFG.csv`에 plc_data 태그 6개 존재 (D230~D246)
- test_data.csv에 PLC 5 데이터 0건이지만, **운영 환경에서 정상 수집 확인됨**
- test_data.csv 추출 조건에 의한 미포함으로 결론

### [CF-02] PLC 4, PLC 10 비표준 주소 사용
- 대부분 PLC: D200~D216 (표준)
- PLC 4 (PLC-D): D796, D798, D816, D820, D900 추가
- PLC 10 (PLC-L): D230~D246 (오프셋 +30)
- **영향 없음**: 태그 CSV에 정확히 정의되어 있어 수집은 정상

### [CF-03] alm 데이터 test_data.csv에 없음
- test_data.csv는 plc_data만 포함 (alm은 on_change → 변경 시에만 발행)
- 13초 샘플 기간 동안 알람 변경이 없었을 수 있음
- **정상적 동작**일 가능성 높음

---

## 5. 요약 — 우선순위별 조치 항목

### 즉시 수정 권장 (운영 영향)
| ID | 이슈 | 컴포넌트 | 우선순위 |
|----|------|----------|----------|
| P-01 | _parse_datetime() 현재시간 폴백 | publisher | **CRITICAL** |
| P-03 | 테이블 생성 레이스 컨디션 | publisher | **HIGH** |
| C-02 | NaN/Inf 검증 누락 | collector | **HIGH** |
| P-04 | compression_orderby 기본값 | publisher config | **MEDIUM** |

### 안정성 개선 (간헐적 장애 방지)
| ID | 이슈 | 컴포넌트 | 우선순위 |
|----|------|----------|----------|
| C-04 | ReadGroup 960워드 경계 | collector | MEDIUM |
| C-05 | 재연결/수집 락 분리 | collector | MEDIUM |
| P-05 | COPY 실패 폴백 없음 | publisher | MEDIUM |

### 코드 품질 (장기 유지보수)
| ID | 이슈 | 컴포넌트 | 우선순위 |
|----|------|----------|----------|
| C-06 | on_change 캐시 제한 | collector | LOW |
| C-07 | 백오프 코드 중복 | collector/publisher | LOW |
| P-07 | schema 검증 | publisher | LOW |
| P-08 | 셧다운 메시지 유실 | publisher | LOW |

---

## 6. 운영 데이터 관찰 요약

### 정상 확인 항목
- 9개 PLC plc_data 수집 정상 (타입, 스케일링, 값 범위 모두 합격)
- 데이터 주기 ~1초 유지
- quality_code = 1 (양품) 전체
- 합격률/불량률 합계 100% 근사 (예: 99.49 + 0.51 = 100.00)
- 생산수량 = OK + NG 정합 (예: PLC 2: 7410 = 7372 + 38)

### 확인 필요 항목
- alm 그룹 on_change 동작 (장시간 모니터링 필요)
- 압축 정책 정상 작동 여부 (1일 이상 운영 후 확인)
