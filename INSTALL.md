# Simple Collector 설치 가이드

## 빠른 시작 (5분)

### 1. 설치
```batch
install.bat
```

### 2. 설정 수정
`config/collector_mc_r.yaml` 파일 수정:

```yaml
collector:
  protocol:
    host: "192.168.1.100"   # ← PLC IP 주소
    port: 5007              # ← PLC 포트

  mqtt:
    host: "192.168.0.163"   # ← MQTT 브로커 IP
    port: 11883             # ← MQTT 포트
    topic: "plc/data"       # ← 발행 토픽
```

### 3. 태그 설정
`config/tags_mc_r.csv` 파일에서 수집할 태그 정의:

```csv
tag_id,tag_name,address,data_type,collection_group,scale,offset,description
1,Motor_Speed,D100,uint16,fast,1.0,0.0,모터속도
2,Temperature,D102,float32,fast,0.1,0.0,온도
3,Run_Flag,M0,bool,fast,1.0,0.0,운전중
```

### 4. 실행
```batch
run.bat
```

---

## 설정 상세

### PLC 시리즈별 설정

| 시리즈 | plc_series | 기본 포트 | X/Y 주소 |
|--------|------------|-----------|----------|
| iQ-R   | iq-r       | 5007      | 16진수   |
| iQ-F   | iq-f       | 5000      | 16진수   |
| Q      | q          | 5000      | 8진수    |
| L      | l          | 5000      | 8진수    |

### 데이터 타입

| CSV data_type | 크기 | 설명 |
|---------------|------|------|
| bool          | 1bit | M, X, Y 비트 |
| uint16        | 16bit | D 워드 |
| uint32        | 32bit | D 더블워드 (2워드) |
| float32       | 32bit | 실수 (2워드) |

### 수집 그룹

| 그룹명 | 주기 | 용도 |
|--------|------|------|
| fast   | 1초  | 실시간 모니터링 |
| normal | 5초  | 설정값 |
| slow   | 60초 | 누적값, 통계 |

---

## 문제 해결

### Python을 찾을 수 없음
1. https://www.python.org/downloads/ 에서 Python 3.10+ 설치
2. 설치 시 "Add Python to PATH" 체크

### PLC 연결 오류
1. PLC IP/포트 확인
2. 방화벽 설정 확인
3. MC Protocol 활성화 확인 (GX Works3)

### MQTT 연결 오류
1. MQTT 브로커 IP/포트 확인
2. 브로커 실행 상태 확인
