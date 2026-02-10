# MC Protocol

Mitsubishi Electric의 MC Protocol (MELSEC Communication Protocol)을 사용한 데이터 수집 방법을 안내합니다.

---

## 개요

**MC Protocol**은 Mitsubishi Electric의 MELSEC 시리즈 PLC를 위한 전용 통신 프로토콜입니다.
1990년대 A 시리즈 PLC와 함께 도입되어, 현재 Q, L, iQ-R, iQ-F 시리즈까지 지원합니다.

### 특징

| 특징 | 설명 |
|------|------|
| 고속 통신 | Binary 인코딩으로 최적화된 데이터 전송 |
| 대용량 처리 | 한 번에 최대 960 워드 읽기 가능 |
| 다양한 디바이스 | D, M, W, X, Y 등 직접 접근 |
| 이중화 지원 | 멀티드롭, CC-Link IE 연동 |

### 지원 PLC

| 시리즈 | 모델 예시 | 프레임 타입 |
|--------|----------|------------|
| **MELSEC Q** | Q02, Q06, Q12, Q25 | 3E, 4E |
| **MELSEC L** | L02, L06, L26 | 3E |
| **MELSEC iQ-R** | R04, R08, R16, R32 | 3E, 4E |
| **MELSEC iQ-F** | FX5U, FX5UC | 3E |
| **MELSEC-F** | FX3U, FX3G (Ethernet 옵션) | 1E |

---

## 프로토콜 구조

### 프레임 타입

MC Protocol은 3가지 프레임 타입을 지원합니다:

| 프레임 | 대상 | 특징 |
|--------|------|------|
| **1E** | FX 시리즈 | 호스트 스테이션 전용, 단순 구조 |
| **3E** | Q/L/iQ 시리즈 | 표준 프레임, 랜덤 읽기 지원 |
| **4E** | iQ-R 시리즈 | 확장 프레임, 시리얼 번호 포함 |

!!! info "Simple Collector 지원"
    현재 **3E 프레임 (Binary 인코딩)**을 사용합니다.

### 3E 프레임 구조 (Binary)

```
┌──────────────────────────────────────────────────────────────────────┐
│                    MC Protocol 3E Frame (Binary)                     │
├──────────┬───────┬───────┬──────────┬─────────┬─────────┬───────────┤
│ Subheader│Net No.│PC No. │Req. Dest │Req. Data│ Timer   │ Data      │
│ 2 bytes  │1 byte │1 byte │4 bytes   │Length 2B│ 2 bytes │ N bytes   │
└──────────┴───────┴───────┴──────────┴─────────┴─────────┴───────────┘
```

### 서브헤더 (Subheader)

| 인코딩 | 요청 | 응답 |
|--------|------|------|
| Binary | 0x5000 | 0xD000 |
| ASCII | "5000" | "D000" |

### 요청 목적지 (Request Destination)

| 필드 | 크기 | 설명 |
|------|------|------|
| 네트워크 번호 | 1 byte | 대상 네트워크 (0=자국) |
| PC 번호 | 1 byte | 대상 PC 번호 (0xFF=자국) |
| 요청 목적지 모듈 I/O | 2 bytes | 모듈 I/O 번호 (0x03FF=CPU) |
| 요청 목적지 국 번호 | 1 byte | 국 번호 (0=자국) |

### 명령어 코드 (Command)

| 명령 | Binary | ASCII | 설명 |
|------|--------|-------|------|
| 배치 읽기 (Bit) | 0x0401 | "0401" | 비트 디바이스 일괄 읽기 |
| 배치 읽기 (Word) | 0x0401 | "0401" | 워드 디바이스 일괄 읽기 |
| 배치 쓰기 (Bit) | 0x1401 | "1401" | 비트 디바이스 일괄 쓰기 |
| 배치 쓰기 (Word) | 0x1401 | "1401" | 워드 디바이스 일괄 쓰기 |
| 랜덤 읽기 | 0x0403 | "0403" | 불연속 주소 읽기 |
| 랜덤 쓰기 | 0x1402 | "1402" | 불연속 주소 쓰기 |

---

## Binary vs ASCII

### 인코딩 비교

| 항목 | Binary | ASCII |
|------|--------|-------|
| 데이터 효율 | 높음 (1:1) | 낮음 (1:2) |
| 전송 속도 | 빠름 | 느림 |
| 디버깅 | 어려움 | 쉬움 (패킷 모니터링) |
| 100 워드 읽기 응답 | 100 bytes | 200 bytes |
| 100 비트 읽기 응답 | 13 bytes | 26 bytes |

!!! tip "권장 설정"
    Simple Collector는 성능을 위해 **Binary 인코딩**을 기본으로 사용합니다.
    디버깅 시에는 ASCII 모드로 전환하여 패킷 분석이 가능합니다.

---

## 디바이스 영역

### 주요 디바이스

| 디바이스 | 코드 | 타입 | 설명 | Q 시리즈 범위 |
|---------|------|------|------|--------------|
| **D** | 0xA8 | 워드 | 데이터 레지스터 | 0~12287 |
| **M** | 0x90 | 비트 | 내부 릴레이 | 0~8191 |
| **X** | 0x9C | 비트 | 입력 접점 | 0~1FFF (8진) |
| **Y** | 0x9D | 비트 | 출력 접점 | 0~1FFF (8진) |
| **W** | 0xB4 | 워드 | 링크 레지스터 | 0~2047 |
| **R** | 0xAF | 워드 | 파일 레지스터 | 0~32767 |
| **ZR** | 0xB0 | 워드 | 확장 레지스터 | 0~65535 |
| **T** | 0xC1 | 워드 | 타이머 현재값 | 0~2047 |
| **C** | 0xC5 | 워드 | 카운터 현재값 | 0~1023 |
| **ST** | 0xC8 | 워드 | 적산 타이머 | 0~2047 |
| **SM** | 0x91 | 비트 | 특수 릴레이 | 0~9999 |
| **SD** | 0xA9 | 워드 | 특수 레지스터 | 0~9999 |

### 시리즈별 주소 범위

| 디바이스 | Q 시리즈 | iQ-R 시리즈 | iQ-F 시리즈 |
|---------|---------|------------|-------------|
| D | 0~12287 | 0~65535 | 0~32767 |
| M | 0~8191 | 0~65535 | 0~32767 |
| W | 0~2047 | 0~8191 | 0~511 |
| R | 0~32767 | 0~65535 | 0~32767 |

### 주소 표기법

```
비트 디바이스 (X, Y):
  - 8진수 표기: X0, X7, X10 (= 8), X17 (= 15)
  - 예: Y20 = 16번째 출력 (8진수 20 = 10진수 16)

워드 디바이스 (D, W):
  - 10진수 표기: D0, D100, D12287
  - 예: D100 = 100번 데이터 레지스터
```

---

## 데이터 타입

### 16비트 타입 (1 워드)

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset
1,Register_Value,D,100,uint16,fast,1.0,0.0
2,Signed_Word,D,101,int16,fast,1.0,0.0
3,Relay_Status,M,0,bool,fast,1.0,0.0
```

### 32비트 타입 (2 워드)

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset
4,Counter,D,200,uint32,fast,1.0,0.0
5,Temperature,D,202,float32,fast,1.0,0.0
6,Total_Time,D,204,int32,fast,1.0,0.0
```

### 문자열 타입

```csv
tag_id,tag_name,memory,address,data_type,collection_group,word_length,description
7,Model_Name,D,900,string,fast,10,현재 모델명 (10워드=20자)
8,Lot_Number,D,920,string,fast,8,로트 번호 (8워드=16자)
```

!!! note "문자열 길이"
    `word_length=10`은 10워드(20바이트)를 읽습니다.
    D900~D909의 값을 ASCII 문자열로 변환합니다.

### Word → Float 변환

정수 워드 값을 실수로 변환하는 경우:

```csv
# D216의 정수값 12340을 scale 적용하여 12.34로 변환
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,format
2,Cycle_Time,D,216,word,fast,0.001,0.0,3,float32
```

| 설정 | 값 | 설명 |
|------|-----|------|
| D216 Raw 값 | 12340 | PLC에서 읽은 정수 |
| scale | 0.001 | 스케일 팩터 |
| 결과 | 12.340 | (12340 × 0.001) = 12.34 |
| decimals | 3 | 소수점 3자리 표시 |

---

## 설정

### 기본 설정

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "MC_Collector"

  protocol:
    type: mcprotocol
    host: "192.168.1.100"
    port: 5000
    plc_type: "Q"
```

### 전체 옵션

```yaml title="config/collector.yaml"
collector:
  plc_id: 1
  name: "MC_Protocol_Collector"

  protocol:
    type: mcprotocol
    host: "192.168.1.100"      # PLC IP 주소
    port: 5000                  # TCP 포트 (기본: 5000)
    plc_type: "Q"               # PLC 시리즈 (Q/L/R/iQ-F)
    timeout_ms: 3000            # 타임아웃 (밀리초)
    reconnect_interval_ms: 5000 # 재연결 간격

    # 멀티드롭/네트워크 설정
    network_no: 0               # 네트워크 번호 (0=자국)
    station_no: 0               # 국번 (0=자국)
    module_io: 0x03FF           # 모듈 I/O 번호 (0x03FF=CPU)
    multidrop_station: 0        # 멀티드롭 국번

    # 통신 모드
    extra:
      encoding: binary          # binary / ascii
      frame_type: 3E            # 1E / 3E / 4E
```

### 설정 옵션 상세

| 옵션 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `host` | string | - | PLC IP 주소 (필수) |
| `port` | int | 5000 | TCP 포트 |
| `plc_type` | string | Q | PLC 시리즈 (Q/L/R/iQ-F) |
| `timeout_ms` | int | 3000 | 요청 타임아웃 (ms) |
| `network_no` | int | 0 | 네트워크 번호 |
| `station_no` | int | 0 | 국번 |
| `module_io` | hex | 0x03FF | 모듈 I/O 번호 |

### PLC 시리즈별 기본 포트

| 시리즈 | 기본 포트 | 비고 |
|--------|----------|------|
| Q 시리즈 | 5000 | 내장 Ethernet |
| L 시리즈 | 5000 | 내장 Ethernet |
| iQ-R 시리즈 | 5000 | 내장 Ethernet |
| iQ-F (FX5U) | 5000 | 내장 Ethernet |
| QJ71E71 | 5001~5004 | 모듈 연결 번호별 |

---

## PLC 설정 (GX Works2/3)

### 내장 이더넷 설정 (Q 시리즈)

1. **파라미터** → **PLC 파라미터** → **내장 이더넷 포트 설정**
2. **열기 설정** 추가:
   - 프로토콜: **TCP**
   - 열기 방식: **MC 프로토콜**
   - 로컬 포트: **5000**
   - 통신 데이터 코드: **Binary**

### 이더넷 모듈 설정 (QJ71E71)

1. **네트워크 파라미터** → **이더넷/CC IE/MELSECNET**
2. **열기 설정**에서 MC 프로토콜 포트 추가
3. 각 연결별로 포트 번호 설정 (5001, 5002, ...)

### iQ-R 시리즈 설정 (GX Works3)

1. **모듈 파라미터** → **CPU 파라미터** → **내장 Ethernet 포트**
2. **MELSOFT 연결** 설정:
   - 프로토콜: TCP
   - 포트: 5000
   - 통신 데이터 코드: Binary

---

## 태그 설정 예제

### 생산 데이터 수집

```csv title="config/tags_mc_production.csv"
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
1,Current_Model,D,900,string,fast,1.0,0.0,,10,,,현재 생산 모델
2,Cycle_Time,D,216,word,fast,0.001,0.0,3,,float32,sec,사이클 타임
3,Yield_Rate,D,820,float32,fast,1.0,0.0,2,,,%,직행률
4,NG_Total,D,816,uint32,fast,1.0,0.0,,,,,NG 총수량
5,Work_Count,D,796,uint32,fast,1.0,0.0,,,,,작업 수
6,Good_Count,D,798,uint32,fast,1.0,0.0,,,,,양품 수
7,Run_Flag,M,0,bool,fast,1.0,0.0,,,,,운전중
8,Alarm_Flag,M,1,bool,fast,1.0,0.0,,,,,알람
9,Emergency_Stop,X,0,bool,fast,1.0,0.0,,,,,비상정지
10,Motor_Output,Y,10,bool,fast,1.0,0.0,,,,,모터 출력
```

### 설비 상태 모니터링

```csv title="config/tags_mc_status.csv"
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,unit,description
20,Motor_Speed,D,100,uint16,fast,1.0,0.0,0,rpm,모터 회전수
21,Temperature_1,D,102,float32,fast,1.0,0.0,1,°C,온도1
22,Temperature_2,D,104,float32,fast,1.0,0.0,1,°C,온도2
23,Pressure,D,106,float32,fast,0.01,0.0,2,bar,압력
24,Flow_Rate,D,108,float32,fast,1.0,0.0,1,L/min,유량
25,Error_Code,D,200,uint16,fast,1.0,0.0,,,에러 코드
```

---

## 멀티드롭 설정

CC-Link IE 또는 MELSECNET으로 연결된 리모트 스테이션 접근:

```yaml
protocol:
  type: mcprotocol
  host: "192.168.1.100"      # 마스터 PLC IP
  port: 5000
  plc_type: "Q"
  network_no: 1              # 대상 네트워크 번호
  station_no: 2              # 대상 국번
```

```
┌────────────────┐     CC-Link IE      ┌────────────────┐
│  Simple        │────────────────────▶│  Master PLC    │
│  Collector     │   MC Protocol       │  Network: 0    │
│                │                     │  Station: 0    │
└────────────────┘                     └────────┬───────┘
                                                │
                                     ┌──────────┴──────────┐
                                     ▼                     ▼
                              ┌────────────┐        ┌────────────┐
                              │ Station 1  │        │ Station 2  │
                              │ Network: 1 │        │ Network: 1 │
                              │ Station: 1 │        │ Station: 2 │◀─── Target
                              └────────────┘        └────────────┘
```

---

## 성능 최적화

### 대용량 읽기

MC Protocol은 한 번에 최대 **960 워드**까지 읽을 수 있습니다.

```csv
# 연속 주소는 자동으로 병합됩니다
tag_id,tag_name,memory,address,data_type,collection_group
1,Data1,D,0,uint16,fast
2,Data2,D,1,uint16,fast
...
100,Data100,D,99,uint16,fast

# 결과: 1회 요청으로 D0~D99 일괄 읽기 (100 워드)
```

### 그룹별 분리

```yaml
collection:
  # 고속 수집 (중요 데이터)
  - group: realtime
    interval_ms: 200
    tags_file: "config/tags_realtime.csv"

  # 일반 수집
  - group: normal
    interval_ms: 1000
    tags_file: "config/tags_normal.csv"

  # 저속 수집 (통계 데이터)
  - group: statistics
    interval_ms: 60000
    tags_file: "config/tags_statistics.csv"
```

### 성능 벤치마크

| 조건 | 처리량 | 비고 |
|------|--------|------|
| 100 태그, 1초 주기 | ~2,000 rec/sec | Binary 인코딩 |
| 500 태그, 1초 주기 | ~5,000 rec/sec | 주소 병합 최적화 |
| 1,000 태그, 500ms 주기 | ~8,000 rec/sec | 고성능 모드 |

---

## 문제 해결

### 연결 실패

```
ERROR | Connection to 192.168.1.100:5000 failed
```

**확인:**

```bash
# 네트워크 연결
ping 192.168.1.100

# 포트 연결
telnet 192.168.1.100 5000
```

**PLC 설정 확인:**

- 내장 이더넷 또는 이더넷 모듈 열기 설정
- MC 프로토콜 포트 활성화
- 방화벽/접근 제어 설정

### 타임아웃 오류

```
ERROR | Read timeout at D100
```

**확인:**

- `timeout_ms` 값 증가 (3000 → 5000)
- PLC CPU 부하 상태 확인
- 네트워크 지연 시간 확인

### 디바이스 오류

```
ERROR | Invalid device: D99999
```

**확인:**

- 디바이스 주소가 PLC 범위 내인지 확인
- `plc_type` 설정이 올바른지 확인

### 문자열 깨짐

```
읽은 문자열: "?�?ABC"
```

**확인:**

- `word_length`가 충분한지 확인
- PLC에서 ASCII로 저장했는지 확인
- 인코딩 설정 (Binary vs ASCII)

---

## 참고 자료

### 공식 문서

- [MELSEC Communication Protocol Reference Manual (RJ71C24)](https://dl.mitsubishielectric.com/dl/fa/document/manual/plc/sh080008/sh080008ab.pdf)
- [MELSEC iQ-F FX5 User's Manual (MELSEC Communication Protocol)](https://dl.mitsubishielectric.com/dl/fa/document/manual/plcf/jy997d60801/jy997d60801g.pdf)

### 유용한 링크

- [pymcprotocol 라이브러리](https://github.com/plcpeople/mcprotocol)
- [Mitsubishi Electric FA 다운로드](https://www.mitsubishielectric.com/fa/)
- [MC Protocol 분석 - Oreate AI](https://www.oreateai.com/blog/analysis-of-mc-communication-protocol-technology-for-mitsubishi-plc-in-scada-systems/)

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
