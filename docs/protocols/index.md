# 프로토콜 가이드

Simple Collector에서 지원하는 산업용 프로토콜을 안내합니다.

---

## 지원 프로토콜

<div class="grid cards" markdown>

-   :material-connection:{ .lg .middle } **Modbus TCP**

    ---

    산업 표준 프로토콜

    - Modbus TCP
    - 대부분의 PLC/센서 호환
    - 포트: 502

    [:octicons-arrow-right-24: Modbus 가이드](modbus.md)

-   :material-chip:{ .lg .middle } **MC Protocol**

    ---

    Mitsubishi Electric 전용

    - MELSEC Q/L/iQ-R 시리즈
    - 3E Frame (Binary/ASCII)
    - 포트: 5000/5001

    [:octicons-arrow-right-24: MC Protocol 가이드](mc-protocol.md)

-   :material-factory:{ .lg .middle } **FENET**

    ---

    LS Electric XGT 시리즈

    - XGK, XGB, XGI, XGR
    - 독자 프로토콜
    - 포트: 2004

    [:octicons-arrow-right-24: FENET 가이드](fenet.md)

-   :material-cog:{ .lg .middle } **S7 Protocol**

    ---

    Siemens S7 시리즈

    - S7-300/400/1200/1500
    - ISO-on-TCP (snap7)
    - 포트: 102

    [:octicons-arrow-right-24: S7 Protocol 가이드](s7-protocol.md)

-   :material-shield-lock:{ .lg .middle } **OPC UA**

    ---

    산업 표준 IEC 62541

    - 플랫폼 독립적
    - 강력한 보안 (X.509)
    - 포트: 4840

    [:octicons-arrow-right-24: OPC UA 가이드](opcua.md)

</div>

---

## 프로토콜 비교

| 특성 | Modbus TCP | MC Protocol | FENET | S7 Protocol | OPC UA |
|------|-----------|-------------|-------|-------------|--------|
| **제조사** | 개방형 | Mitsubishi | LS Electric | Siemens | IEC 표준 |
| **기본 포트** | 502 | 5000 | 2004 | 102 | 4840 |
| **최대 데이터** | 125 워드 | 960 워드 | 60 워드 | 200 바이트 | 제한 없음 |
| **보안** | 없음 | 없음 | 없음 | 없음 | X.509 인증서 |
| **지원 PLC** | 범용 | MELSEC | XGT | S7 시리즈 | 범용 |
| **라이브러리** | pymodbus | 자체 구현 | 자체 구현 | snap7 | asyncua |

---

## 프로토콜 선택

### Modbus TCP 사용

- 다양한 제조사의 PLC/RTU
- 범용 센서, 인버터, 미터
- 표준 프로토콜이 필요한 경우

### MC Protocol 사용

- Mitsubishi MELSEC 시리즈 PLC
- 대용량 데이터 읽기 필요
- 직접 디바이스 접근 필요

### FENET 사용

- LS Electric XGT 시리즈 PLC
- XGK, XGB, XGI, XGR CPU
- 국내 공장 자동화 시스템

### S7 Protocol 사용

- Siemens S7 시리즈 PLC
- S7-300/400/1200/1500
- TIA Portal 연동 시스템

### OPC UA 사용

- 보안이 중요한 환경
- 다양한 장비 통합
- IEC 표준 준수 필요

---

## 공통 설정

### protocol 섹션 구조

```yaml
collector:
  protocol:
    type: modbus        # modbus 또는 mcprotocol
    host: "192.168.1.100"
    port: 502
    timeout: 3.0
    # 프로토콜별 추가 옵션
```

### 필수 옵션

| 옵션 | 타입 | 설명 |
|------|------|------|
| `type` | string | 프로토콜 유형 |
| `host` | string | PLC IP 주소 |
| `port` | int | TCP 포트 |

### 공통 옵션

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `timeout` | float | 3.0 | 연결/응답 타임아웃 (초) |
| `retry_count` | int | 3 | 재시도 횟수 |
| `retry_delay` | float | 1.0 | 재시도 간격 (초) |

---

## 연결 최적화

### 연속 주소 병합

Simple Collector는 연속된 주소의 태그를 자동으로 병합하여 읽습니다.

```
# 개별 읽기 (비효율적)
Read D100 (1 word)
Read D101 (1 word)
Read D102 (1 word)
Read D103 (1 word)

# 병합 읽기 (최적화)
Read D100-D103 (4 words) → 1회 요청
```

### 성능 고려사항

| 항목 | 권장값 | 설명 |
|------|--------|------|
| 수집 주기 | ≥100ms | 너무 빠르면 PLC 부하 |
| 배치 크기 | 50~100 | 한 번에 읽을 레지스터 수 |
| 타임아웃 | 3.0초 | 네트워크 상태에 따라 조정 |

---

## 이벤트 기반 수집 (on_change 모드)

알람, 상태 변경 등 값이 변경될 때만 데이터를 전달해야 하는 경우 사용합니다.

### 동작 원리

```
┌──────────────┐   수집    ┌──────────────┐   비교    ┌──────────────┐
│  Collector   │ ────────▶ │  Processor   │ ────────▶ │   Buffer     │
└──────────────┘           │              │           └──────────────┘
                           │  이전 값과   │  변경 시에만
                           │  비교        │  전달
                           └──────────────┘
```

1. Collector가 주기적으로 PLC 데이터 수집
2. Processor가 이전 값과 비교 (deadband 적용)
3. 변경된 데이터만 Buffer/Publisher로 전달

### 설정 예시

```yaml
collector:
  collection:
    # 알람 그룹: 모든 변화 감지
    - group: alarms
      interval_ms: 500
      tags_file: "config/tags_alarms.csv"
      mode: on_change
      deadband: 0.0

    # 온도 그룹: 0.5도 이상 변화 시에만
    - group: temperature
      interval_ms: 1000
      tags_file: "config/tags_temp.csv"
      mode: on_change
      deadband: 0.5
      deadband_type: absolute

    # 진동 그룹: 5% 이상 변화 시에만
    - group: vibration
      interval_ms: 100
      tags_file: "config/tags_vibration.csv"
      mode: on_change
      deadband: 5.0
      deadband_type: percent
```

### deadband 설정

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `mode` | string | polling | `polling` 또는 `on_change` |
| `deadband` | float | 0.0 | 변화 감지 임계값 |
| `deadband_type` | string | absolute | `absolute` 또는 `percent` |

**deadband_type 비교:**

- **absolute**: 절대값 비교 → `|new - old| > deadband`
- **percent**: 백분율 비교 → `|new - old| / |old| × 100 > deadband`

!!! tip "사용 시나리오"
    - **알람/이벤트**: `deadband: 0.0` (모든 변화 감지)
    - **온도 모니터링**: `deadband: 0.5, deadband_type: absolute`
    - **아날로그 센서**: `deadband: 1.0, deadband_type: percent`

!!! note "데이터 타입별 동작"
    - **bool/string**: deadband 무시, 값 변경 시 항상 전달
    - **숫자 타입**: deadband 적용하여 비교

---

## 오류 처리

### 연결 실패

```
ERROR | Connection to 192.168.1.100:502 failed
```

**확인 사항:**

1. PLC 전원 및 네트워크 연결
2. IP 주소 및 포트 확인
3. 방화벽 설정
4. PLC의 통신 설정 활성화

### 읽기 실패

```
ERROR | Read error at D100: Timeout
```

**확인 사항:**

1. 주소 범위가 PLC에 존재하는지
2. 데이터 타입이 올바른지
3. 타임아웃 값 증가 필요

### 자동 재연결

Simple Collector는 연결 끊김 시 자동으로 재연결을 시도합니다.

```yaml
collector:
  protocol:
    retry_count: 3        # 최대 재시도 횟수
    retry_delay: 1.0      # 재시도 간격 (초)
```

---

## 데이터 품질

### quality_code

| 코드 | 의미 | 설명 |
|------|------|------|
| 1 | Good | 정상 수집 |
| 0 | Bad | 수집 실패 |

### 실패 시 동작

수집 실패 시에도 `quality_code=0`인 레코드가 생성됩니다:

```json
{
  "time": "2026-02-05T10:00:01Z",
  "plc_id": 1,
  "tag_id": 1,
  "v_float": null,
  "quality_code": 0
}
```

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
