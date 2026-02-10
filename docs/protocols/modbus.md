# Modbus TCP

Modbus TCP/IP 프로토콜을 사용한 데이터 수집 방법을 안내합니다.

---

## 개요

**Modbus TCP**는 1979년 Modicon(현 Schneider Electric)에서 개발한 Modbus 프로토콜의 이더넷 버전입니다.
TCP/IP 네트워크를 통해 포트 502에서 통신하며, 산업 자동화 분야에서 가장 널리 사용되는 개방형 표준 프로토콜입니다.

### 특징

| 특징 | 설명 |
|------|------|
| 개방형 표준 | 로열티 프리, 벤더 중립적 |
| 간단한 구조 | 마스터-슬레이브 아키텍처 |
| 폭넓은 호환성 | 대부분의 PLC, RTU, 센서 지원 |
| 신뢰성 | TCP의 체크섬 보호로 별도 CRC 불필요 |

### Modbus 변형

| 변형 | 물리 계층 | 특징 |
|------|----------|------|
| **Modbus TCP** | Ethernet/TCP | 포트 502, MBAP 헤더 |
| Modbus RTU | RS-232/485 | 바이너리, CRC16 |
| Modbus ASCII | RS-232/485 | ASCII, LRC |

!!! info "Simple Collector 지원"
    현재 Simple Collector는 **Modbus TCP**를 지원합니다.

---

## 프로토콜 구조

### MBAP 헤더 (Modbus Application Protocol)

Modbus TCP 프레임은 7바이트의 MBAP 헤더로 시작합니다:

```
┌────────────────────────────────────────────────────────────────┐
│                    Modbus TCP Frame (최대 260 bytes)           │
├──────────────────────────────────────┬─────────────────────────┤
│          MBAP Header (7 bytes)       │    PDU (최대 253 bytes) │
├──────┬──────┬────────┬───────────────┼──────────┬──────────────┤
│ TxID │ PID  │ Length │ Unit ID       │ FC       │ Data         │
│ 2B   │ 2B   │ 2B     │ 1B            │ 1B       │ N bytes      │
└──────┴──────┴────────┴───────────────┴──────────┴──────────────┘
```

| 필드 | 크기 | 설명 |
|------|------|------|
| Transaction ID | 2 bytes | 요청 식별자 (응답에서 동일) |
| Protocol ID | 2 bytes | Modbus = 0x0000 |
| Length | 2 bytes | 이후 바이트 수 (Unit ID + PDU) |
| Unit ID | 1 byte | 장치 식별자 (1~247) |
| Function Code | 1 byte | 기능 코드 (1~127) |
| Data | N bytes | 기능별 데이터 |

### Function Code (기능 코드)

| 코드 | 기능 | 설명 | 접근 |
|------|------|------|------|
| **01** | Read Coils | 코일(DO) 읽기 | R |
| **02** | Read Discrete Inputs | 이산 입력(DI) 읽기 | R |
| **03** | Read Holding Registers | 홀딩 레지스터(AO) 읽기 | R |
| **04** | Read Input Registers | 입력 레지스터(AI) 읽기 | R |
| 05 | Write Single Coil | 단일 코일 쓰기 | W |
| 06 | Write Single Register | 단일 레지스터 쓰기 | W |
| 15 | Write Multiple Coils | 다중 코일 쓰기 | W |
| 16 | Write Multiple Registers | 다중 레지스터 쓰기 | W |

!!! note "Simple Collector 지원 기능"
    데이터 수집기로서 읽기 기능 **FC01~FC04**를 지원합니다.

### 예외 응답 (Exception Response)

| 코드 | 이름 | 원인 |
|------|------|------|
| 0x01 | Illegal Function | 지원하지 않는 기능 코드 |
| 0x02 | Illegal Data Address | 유효하지 않은 주소 |
| 0x03 | Illegal Data Value | 유효하지 않은 데이터 값 |
| 0x04 | Slave Device Failure | 장치 내부 오류 |
| 0x06 | Slave Device Busy | 장치 처리 중 |

---

## 메모리 영역

### 레지스터 타입

Modbus는 4가지 메모리 영역을 정의합니다:

| 주소 접두사 | 영역 | 크기 | FC | 설명 |
|------------|------|------|-----|------|
| 0xxxx | Coil | 1 bit | 01 | 출력 비트 (R/W) |
| 1xxxx | Discrete Input | 1 bit | 02 | 입력 비트 (R) |
| 3xxxx | Input Register | 16 bit | 04 | 아날로그 입력 (R) |
| 4xxxx | Holding Register | 16 bit | 03 | 데이터/설정값 (R/W) |

### Simple Collector 주소 형식

| CSV 접두사 | 의미 | Function Code |
|-----------|------|---------------|
| `D`, `HR` | Holding Register | FC03 |
| `I`, `IR` | Input Register | FC04 |
| `M`, `C` | Coil | FC01 |
| `X`, `DI` | Discrete Input | FC02 |

```csv
# 예시: 다양한 메모리 영역
tag_id,tag_name,memory,address,data_type,collection_group
1,Temperature,D,100,float32,fast        # Holding Register D100
2,Sensor_Input,IR,0,uint16,fast          # Input Register I0
3,Valve_Status,M,0,bool,fast             # Coil M0
4,Limit_Switch,DI,10,bool,fast           # Discrete Input DI10
```

---

## 데이터 타입

### 16비트 타입 (1 레지스터)

| 타입 | 범위 | 설명 |
|------|------|------|
| `uint16` | 0 ~ 65,535 | 부호 없는 16비트 |
| `int16` | -32,768 ~ 32,767 | 부호 있는 16비트 |
| `bool` | 0/1 | 비트 (Coil/DI용) |

### 32비트 타입 (2 레지스터)

| 타입 | 범위 | 설명 |
|------|------|------|
| `uint32` | 0 ~ 4,294,967,295 | 부호 없는 32비트 |
| `int32` | -2,147,483,648 ~ 2,147,483,647 | 부호 있는 32비트 |
| `float32` | IEEE 754 | 단정밀도 부동소수점 |

### 64비트 타입 (4 레지스터)

| 타입 | 설명 |
|------|------|
| `uint64` | 부호 없는 64비트 |
| `float64` | 배정밀도 부동소수점 |

!!! warning "32비트 이상 데이터"
    32비트 데이터는 연속된 2개 레지스터를 사용합니다.
    예: D200의 float32 → D200 (High), D201 (Low)

---

## 바이트 순서

### Big Endian (기본)

Modbus 표준은 **Big Endian** (Most Significant Byte First)입니다.

```
16비트 값 0x1234:
  Register: [0x12][0x34]
            High   Low
```

### Little Endian

일부 장치(특히 Schneider Electric)는 Little Endian을 사용합니다.

### 워드 순서 (32비트 이상)

32비트 데이터의 워드 배치 순서:

```yaml
# Big Endian Word Order (기본)
# 값 0x12345678
# D100 = 0x1234 (High Word)
# D101 = 0x5678 (Low Word)
protocol:
  word_order: big

# Little Endian Word Order
# 값 0x12345678
# D100 = 0x5678 (Low Word)
# D101 = 0x1234 (High Word)
protocol:
  word_order: little
```

### 제조사별 바이트/워드 순서

| 제조사 | Byte Order | Word Order |
|--------|------------|------------|
| Siemens | Big | Big |
| Schneider Electric | Big | **Little** |
| Allen-Bradley | Big | Big |
| ABB | Big | Big |
| Omron | Big | Little |

---

## 설정

### 기본 설정

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "Modbus_Collector"

  protocol:
    type: modbus
    host: "192.168.1.100"
    port: 502
    unit_id: 1
```

### 전체 옵션

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "Modbus_TCP_Collector"

  protocol:
    type: modbus
    host: "192.168.1.100"      # PLC/Gateway IP 주소
    port: 502                   # TCP 포트 (기본: 502)
    unit_id: 1                  # Modbus Unit ID (1~247)
    timeout_ms: 3000            # 타임아웃 (밀리초)
    reconnect_interval_ms: 5000 # 재연결 간격

    # 바이트/워드 순서
    byte_order: big             # big / little
    word_order: big             # big / little

    # 최적화 설정
    extra:
      max_address_gap: 100      # 연속 주소 간주 최대 간격
```

### 설정 옵션 상세

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `host` | string | - | PLC/Gateway IP 주소 (필수) |
| `port` | int | 502 | TCP 포트 |
| `unit_id` | int | 1 | Modbus Unit ID (1~247) |
| `timeout_ms` | int | 3000 | 요청 타임아웃 (ms) |
| `reconnect_interval_ms` | int | 5000 | 자동 재연결 간격 (ms) |
| `byte_order` | string | big | 바이트 순서 (big/little) |
| `word_order` | string | big | 워드 순서 (big/little) |
| `extra.max_address_gap` | int | 100 | 연속 주소 병합 최대 간격 |

---

## 태그 설정

### CSV 형식

```csv title="config/tags_modbus.csv"
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,unit,description
1,Temperature,D,100,float32,fast,1.0,0.0,2,°C,온도
2,Pressure,D,102,float32,fast,0.01,0.0,2,bar,압력
3,Flow_Rate,D,104,uint32,fast,0.1,0.0,1,L/min,유량
4,Valve_Status,M,0,bool,fast,1.0,0.0,,,밸브 상태
5,Alarm_Word,D,200,uint16,fast,1.0,0.0,,,알람 워드
6,Setpoint,D,300,float32,slow,1.0,0.0,1,°C,설정값
7,Total_Count,D,400,uint32,slow,1.0,0.0,,,총 카운트
8,Sensor_Raw,IR,0,uint16,fast,1.0,0.0,,,센서 Raw 값
```

### 컬럼 설명

| 컬럼 | 필수 | 설명 |
|------|------|------|
| `tag_id` | O | 태그 고유 ID |
| `tag_name` | O | 태그 이름 |
| `memory` | O | 메모리 영역 (D, IR, M, DI) |
| `address` | O | 레지스터/비트 주소 |
| `data_type` | O | 데이터 타입 |
| `collection_group` | O | 수집 그룹 |
| `scale` | - | 스케일 팩터 (기본 1.0) |
| `offset` | - | 오프셋 (기본 0.0) |
| `decimals` | - | 소수점 자릿수 |
| `unit` | - | 단위 |
| `description` | - | 설명 |

---

## 성능 최적화

### 연속 주소 병합

Simple Collector는 연속된 주소의 태그를 자동으로 병합하여 네트워크 요청을 최소화합니다.

```
태그 정의:
  D100 (uint16), D101 (uint16), D102 (float32), D104 (uint16)

최적화 결과:
  1회 요청: D100 ~ D104 (5개 레지스터 일괄 읽기)

요청 횟수: 4회 → 1회
```

### 최적화 설정

```yaml
protocol:
  extra:
    max_address_gap: 100  # 간격이 100 이하면 병합
```

!!! tip "최적화 효과"
    간격이 큰 주소도 한 번에 읽으면 전체적인 요청 횟수가 줄어 성능이 향상됩니다.
    단, 간격이 너무 크면 불필요한 데이터를 읽게 되므로 적절한 값 설정이 중요합니다.

### 그룹별 분리

```yaml
collection:
  - group: realtime        # 중요 데이터
    interval_ms: 500
    tags_file: "config/tags_realtime.csv"

  - group: normal          # 일반 데이터
    interval_ms: 5000
    tags_file: "config/tags_normal.csv"

  - group: statistics      # 통계 데이터
    interval_ms: 60000
    tags_file: "config/tags_statistics.csv"
```

---

## 제조사별 설정 예시

### Siemens S7 (CP343 Ethernet)

```yaml
protocol:
  type: modbus
  host: "192.168.1.100"
  port: 502
  unit_id: 1
  byte_order: big
  word_order: big
```

### Schneider Electric (M340/M580)

```yaml
protocol:
  type: modbus
  host: "192.168.1.100"
  port: 502
  unit_id: 1
  byte_order: big
  word_order: little    # Schneider는 Little Endian Word
```

### Allen-Bradley (ProSoft MVI Gateway)

```yaml
protocol:
  type: modbus
  host: "192.168.1.100"
  port: 502
  unit_id: 0            # AB Gateway는 Unit ID 0 사용
  byte_order: big
  word_order: big
```

### Omron CJ2 (Ethernet Unit)

```yaml
protocol:
  type: modbus
  host: "192.168.1.100"
  port: 502
  unit_id: 1
  byte_order: big
  word_order: little
```

---

## 문제 해결

### 연결 실패

```
ERROR | Connection to 192.168.1.100:502 failed
```

**확인 사항:**

```bash
# 네트워크 연결 테스트
ping 192.168.1.100

# 포트 연결 테스트 (Linux/Mac)
nc -zv 192.168.1.100 502

# 포트 연결 테스트 (Windows)
Test-NetConnection -ComputerName 192.168.1.100 -Port 502
```

**점검:**

- PLC의 Modbus TCP 기능 활성화 여부
- 방화벽 설정 (포트 502)
- PLC의 IP 주소 및 서브넷 설정

### Unit ID 오류

```
ERROR | Illegal Function (Exception code: 0x01)
```

**확인:**

- Unit ID가 PLC 설정과 일치하는지 확인
- 일부 장치는 Unit ID 0 또는 255 사용
- Gateway 사용 시 각 슬레이브의 Unit ID 확인

### 주소 범위 오류

```
ERROR | Illegal Data Address (Exception code: 0x02)
```

**확인:**

- 요청한 주소가 PLC에 존재하는지 확인
- 0-based vs 1-based 주소 오프셋 확인
- Function Code와 메모리 영역 매칭 확인

### 데이터 깨짐

**확인:**

- `byte_order` 설정 확인
- `word_order` 설정 확인 (32비트 데이터)
- PLC 제조사 매뉴얼에서 엔디안 확인

---

## 참고 자료

### 공식 사양

- [Modbus Organization 공식 사양](https://modbus.org/specs.php)
- [Modbus Application Protocol V1.1b](https://modbus.org/docs/Modbus_Application_Protocol_V1_1b.pdf)

### 유용한 링크

- [pymodbus 문서](https://pymodbus.readthedocs.io/)
- [Modbus Protocol Overview - Fernhill Software](https://www.fernhillsoftware.com/help/drivers/modbus/modbus-protocol.html)
- [Modbus TCP 상세 설명 - IPC2U](https://ipc2u.com/articles/knowledge-base/detailed-description-of-the-modbus-tcp-protocol-with-command-examples/)

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
