# 태그 CSV 설정

수집할 태그를 정의하는 CSV 파일 작성법을 안내합니다.

---

## CSV 파일 형식

### 기본 구조

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
```

### 컬럼 설명

| 컬럼 | 타입 | 필수 | 설명 | 예시 |
|------|------|------|------|------|
| `tag_id` | int | O | 태그 고유 ID | `1` |
| `tag_name` | string | O | 태그 이름 | `Temperature` |
| `memory` | string | O | 메모리 영역 | `D`, `M`, `W` |
| `address` | string | O | 주소 번호 | `100`, `0` |
| `data_type` | string | O | 데이터 타입 | `float32`, `uint32` |
| `collection_group` | string | O | 수집 그룹 | `fast`, `slow` |
| `scale` | float | - | 스케일 계수 | `1.0`, `0.001` |
| `offset` | float | - | 오프셋 | `0.0`, `-273.15` |
| `decimals` | int | - | 소수점 자릿수 | `2`, `3` |
| `word_length` | int | - | 워드 수 (문자열) | `10`, `20` |
| `format` | string | - | 변환 포맷 | `float32` |
| `unit` | string | - | 단위 | `°C`, `bar` |
| `description` | string | - | 설명 | `온도 센서` |

---

## 메모리 영역

### Modbus

| 영역 | 코드 | 레지스터 타입 | 주소 범위 |
|------|------|--------------|----------|
| Holding Register | `D` 또는 `HR` | 읽기/쓰기 | 0~65535 |
| Input Register | `I` 또는 `IR` | 읽기 전용 | 0~65535 |
| Coil | `M` 또는 `C` | 읽기/쓰기 비트 | 0~65535 |
| Discrete Input | `X` 또는 `DI` | 읽기 전용 비트 | 0~65535 |

### MC Protocol

| 영역 | 코드 | 설명 |
|------|------|------|
| Data Register | `D` | 데이터 레지스터 |
| Internal Relay | `M` | 내부 릴레이 |
| Link Register | `W` | 링크 레지스터 |
| Input | `X` | 입력 |
| Output | `Y` | 출력 |
| Timer | `T` | 타이머 (현재값) |
| Counter | `C` | 카운터 (현재값) |
| File Register | `R` | 파일 레지스터 |

---

## 데이터 타입

### 숫자 타입

| 타입 | 크기 | 범위 | 워드 수 |
|------|------|------|--------|
| `bool` | 1 bit | 0/1 | 1 |
| `int16` | 16 bit | -32768 ~ 32767 | 1 |
| `uint16` | 16 bit | 0 ~ 65535 | 1 |
| `int32` | 32 bit | -2^31 ~ 2^31-1 | 2 |
| `uint32` | 32 bit | 0 ~ 2^32-1 | 2 |
| `float32` | 32 bit | IEEE 754 | 2 |
| `float64` | 64 bit | IEEE 754 | 4 |
| `word` | 16 bit | 0 ~ 65535 | 1 |

### 특수 타입

| 타입 | 설명 | 필수 옵션 |
|------|------|----------|
| `string` | 문자열 | `word_length` |
| `bcd` | BCD 형식 | - |

---

## 스케일링

### 계산 공식

```
최종값 = (원시값 × scale) + offset
```

### 예시

```csv
# 온도: 원시값 2500 → 25.00°C
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset
1,Temperature,D,100,uint16,fast,0.01,0.0

# 온도 (섭씨→화씨): 원시값 25.0 → 77.0°F
2,TempF,D,100,float32,fast,1.8,32.0

# 압력: 원시값 4000 → 4.000 bar
3,Pressure,D,102,uint16,fast,0.001,0.0
```

### decimals 적용

```csv
# 소수점 2자리로 반올림
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals
1,Yield_Rate,D,820,float32,fast,1.0,0.0,2

# 결과: 85.456789 → 85.46
```

---

## format 변환

### word → float 변환

`word` 타입을 `float`로 변환할 때 `format` 필드를 사용합니다.

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format
1,Cycle_Time,D,216,word,fast,0.001,0.0,3,,float32
```

**동작 원리:**

1. `D216`에서 `word` (정수) 값 읽기: `12340`
2. 스케일 적용: `12340 × 0.001 = 12.34`
3. decimals 적용: `round(12.34, 3) = 12.340`
4. `format=float32` → 출력 컬럼: `v_float`

### 출력 타입 매핑

| format 값 | 출력 컬럼 |
|----------|----------|
| `float32`, `float64`, `float` | `v_float` |
| `int32`, `int16`, `int` | `v_int` |
| `int64`, `bigint` | `v_bigint` |
| `string`, `text` | `v_text` |
| `bool` | `v_bool` |

---

## 문자열 처리

### 기본 문자열

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length
1,Model_Name,D,900,string,fast,1.0,0.0,,10
```

- `word_length=10`: D900~D909 (10개 워드 = 20바이트)

### 문자열 인코딩

- **Modbus**: ASCII (1워드 = 2문자)
- **MC Protocol**: ASCII (1워드 = 2문자)

---

## Bool 처리

### 기본 Bool

```csv
tag_id,tag_name,memory,address,data_type,collection_group
1,Run_Status,M,0,bool,fast
```

### 커스텀 임계값 (향후 지원)

```csv
tag_id,tag_name,memory,address,data_type,collection_group,bool_true_value,bool_false_value,bool_invert
1,Status,D,100,bool,fast,1,0,false
2,Alarm,D,101,bool,fast,0,1,true
```

---

## 완전한 예시

### tags_fast.csv (1초 주기)

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
1,Temperature,D,100,float32,fast,1.0,0.0,2,,,°C,온도 센서
2,Pressure,D,102,float32,fast,1.0,0.0,2,,,bar,압력 센서
3,Motor_Speed,D,104,uint32,fast,0.1,0.0,1,,,rpm,모터 속도
4,Cycle_Time,D,216,word,fast,0.001,0.0,3,,float32,sec,사이클 타임
5,Run_Flag,M,0,bool,fast,1.0,0.0,,,,,운전 상태
6,Alarm_Flag,M,1,bool,fast,1.0,0.0,,,,,알람 발생
7,Model_Name,D,900,string,fast,1.0,0.0,,10,,,현재 모델
```

### tags_slow.csv (1분 주기)

```csv
tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description
101,Total_Count,D,200,uint32,slow,1.0,0.0,,,,,총 생산량
102,Good_Count,D,202,uint32,slow,1.0,0.0,,,,,양품 수량
103,NG_Count,D,204,uint32,slow,1.0,0.0,,,,,불량 수량
104,Yield_Rate,D,820,float32,slow,1.0,0.0,2,,,%,수율
105,Operating_Time,D,300,uint32,slow,1.0,0.0,0,,,min,가동 시간
```

---

## 주의사항

!!! warning "중요"
    - `tag_id`는 전체 설정에서 고유해야 합니다
    - `collection_group`은 YAML의 `collection.group`과 일치해야 합니다
    - CSV 파일은 UTF-8 인코딩으로 저장하세요

!!! tip "팁"
    - 빈 값은 비워두거나 생략 가능합니다
    - 숫자 필드에 빈 값은 기본값이 적용됩니다
    - Excel에서 편집 시 CSV로 저장할 때 인코딩 확인

---

<div align="center">

**NEUROSENSE Inc.** | *Intelligent Industrial Solutions*

</div>
