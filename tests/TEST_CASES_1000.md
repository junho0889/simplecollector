# JEM-BLE 테스트 케이스 1000개

> 생성일: 2026-03-21
> 대상: simpleCollector v0.3.3 + collector-publisher v0.2.1

---

## 카테고리 요약

| # | 카테고리 | 범위 | 건수 |
|---|---------|------|------|
| A | POSIOT V1 프로파일 파싱 | A001~A120 | 120 |
| B | POSIOT V2 프로파일 파싱 | B001~B100 | 100 |
| C | BLE Scanner | C001~C080 | 80 |
| D | BLE Collector (Single) | D001~D050 | 50 |
| E | BLE MultiCollector | E001~E070 | 70 |
| F | BLE Processor | F001~F060 | 60 |
| G | 태그 스케일링 (apply_scaling) | G001~G100 | 100 |
| H | 데이터 타입 해석 | H001~H060 | 60 |
| I | Config 파싱 (YAML+CSV) | I001~I080 | 80 |
| J | on_change / deadband | J001~J060 | 60 |
| K | 직렬화 (JSON→zlib→Fernet) | K001~K040 | 40 |
| L | RabbitMQ Publisher | L001~L050 | 50 |
| M | MC Protocol | M001~M080 | 80 |
| N | Modbus Protocol | N001~N070 | 70 |
| O | Pipeline / Lifecycle | O001~O030 | 30 |
| P | Docker / 배포 | P001~P030 | 30 |
| Q | 통합 테스트 (E2E) | Q001~Q050 | 50 |
| R | 에러 복구 / 재연결 | R001~R040 | 40 |
| S | 성능 / 부하 | S001~S030 | 30 |
| | **합계** | | **1200** |

---

## A. POSIOT V1 프로파일 파싱 (A001~A120)

### A-1. temperature (company_id → int16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A001 | 정상 양수 온도 | company_id=0x0A8C (2700) | 27.00 |
| A002 | 정상 음수 온도 | company_id=0xF448 (-3000 signed) | -30.00 |
| A003 | 0도 | company_id=0x0000 | 0.00 |
| A004 | 최대값 | company_id=0x7FFF (32767) | 327.67 |
| A005 | 최소값 | company_id=0x8000 (-32768) | -327.68 |
| A006 | 소수점 경계 | company_id=0x0001 (1) | 0.01 |
| A007 | 음수 소수점 | company_id=0xFFFF (-1) | -0.01 |
| A008 | 25.5도 정확도 | company_id=0x09F6 (2550) | 25.50 |
| A009 | -40도 (센서 하한) | company_id=0xF060 (-4000) | -40.00 |
| A010 | 85도 (센서 상한) | company_id=0x2134 (8500) | 85.00 |

### A-2. humidity (data[0:2] → int16_le / 100.0)
| ID | 테스트 | 입력 (bytes) | 기대값 |
|----|--------|-------------|--------|
| A011 | 정상 습도 50% | b'\x88\x13' (5000) | 50.00 |
| A012 | 0% | b'\x00\x00' | 0.00 |
| A013 | 100% | b'\x10\x27' (10000) | 100.00 |
| A014 | 99.99% | b'\x0F\x27' (9999) | 99.99 |
| A015 | 음수값 (이상) | b'\xFF\xFF' (-1) | -0.01 |
| A016 | 1바이트만 제공 | b'\x88' | struct.error → None |

### A-3. pressure (data[2:4] → uint16_be, 특수공식)
| ID | 테스트 | 입력 (bytes) | 기대값 |
|----|--------|-------------|--------|
| A017 | 표준 기압 | raw=40099 → (40099*255+50000)/4096 | 2503.58 |
| A018 | raw=0 | (0*255+50000)/4096 | 12.21 |
| A019 | raw=65535 | (65535*255+50000)/4096 | 4092.42 |
| A020 | raw=39216 (1013hPa) | 계산 | ~2448.75 |
| A021 | 2바이트 미만 | 1바이트만 | struct.error |

### A-4. battery (data[4] → uint8)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A022 | 정상 100% | b'\x64' | 100 |
| A023 | 0% | b'\x00' | 0 |
| A024 | 255 (최대) | b'\xFF' | 255 |
| A025 | 5바이트 미만 | 4바이트만 | IndexError → None |

### A-5. version/mode (data[5] 상위4비트/하위4비트)
| ID | 테스트 | 입력 | version | mode |
|----|--------|------|---------|------|
| A026 | v1 mode0 | b'\x10' | 1 | 0 |
| A027 | v2 mode3 | b'\x23' | 2 | 3 |
| A028 | v15 mode15 | b'\xFF' | 15 | 15 |
| A029 | v0 mode0 | b'\x00' | 0 | 0 |

### A-6. accel_rms_x/y/z (data[6:12] → 3×uint16_be / 100.0)
| ID | 테스트 | 입력 | x | y | z |
|----|--------|------|---|---|---|
| A030 | 정상값 | 0x0064,0x00C8,0x012C | 1.00 | 2.00 | 3.00 |
| A031 | 0값 | 0x0000×3 | 0.00 | 0.00 | 0.00 |
| A032 | 최대값 | 0xFFFF×3 | 655.35 | 655.35 | 655.35 |
| A033 | 비대칭 | 0x0001,0xFFFF,0x0000 | 0.01 | 655.35 | 0.00 |
| A034 | 12바이트 미만 | 10바이트만 | struct.error |

### A-7. velocity_rms (data[12:14] → uint16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A035 | 정상 | b'\xE8\x03' (1000) | 10.00 |
| A036 | 0 | b'\x00\x00' | 0.00 |
| A037 | 최대 | b'\xFF\xFF' (65535) | 655.35 |

### A-8. accel_rms_1k_5k (data[14:16] → uint16_be / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A038 | 정상 | b'\x03\xE8' (1000) | 10.00 |
| A039 | 0 | b'\x00\x00' | 0.00 |

### A-9. vibration_peak (data[16:18] → uint16_be)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A040 | 정상 | b'\x01\xF4' (500) | 500 |
| A041 | 0 | b'\x00\x00' | 0 |

### A-10. harmony_cnt (data[18], data[19] → uint8)
| ID | 테스트 | 입력 | under_1k | 1k_5k |
|----|--------|------|----------|-------|
| A042 | 정상 | b'\x05\x0A' | 5 | 10 |
| A043 | 최대 | b'\xFF\xFF' | 255 | 255 |
| A044 | 0 | b'\x00\x00' | 0 | 0 |

### A-11. gravity_mag (data[20:22] → uint16_be)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A045 | 정상 | b'\x03\xE8' | 1000 |

### A-12. sound_db/sound_peak (data[22], data[23:25])
| ID | 테스트 | 입력 | db | peak |
|----|--------|------|----|----|
| A046 | 정상 | b'\x3C\x01\xF4' | 60 | 500 |
| A047 | 무음 | b'\x00\x00\x00' | 0 | 0 |

### A-13. prob_temp (data[25:27] → int16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| A048 | 정상 25도 | b'\xC4\x09' (2500) | 25.00 |
| A049 | 음수 -10도 | b'\x18\xFC' (-1000) | -10.00 |
| A050 | 0도 | b'\x00\x00' | 0.00 |

### A-14. 데이터 길이 경계 테스트
| ID | 테스트 | 데이터 길이 | 기대 동작 |
|----|--------|------------|----------|
| A051 | 0바이트 | 0 | 빈 dict 또는 에러 |
| A052 | 1바이트 | 1 | humidity만 실패 |
| A053 | 2바이트 | 2 | humidity OK, pressure 실패 |
| A054 | 5바이트 | 5 | battery까지 OK |
| A055 | 6바이트 | 6 | version/mode까지 OK |
| A056 | 11바이트 | 11 | accel 부족 |
| A057 | 12바이트 | 12 | accel OK, velocity 실패 |
| A058 | 14바이트 | 14 | velocity OK |
| A059 | 22바이트 | 22 | sound_db까지 OK |
| A060 | 25바이트 | 25 | sound_peak까지 OK |
| A061 | 27바이트 (정상) | 27 | 전체 파싱 OK |
| A062 | 30바이트 (초과) | 30 | 정상 (초과분 무시) |

### A-15. 바이트 오더 혼합 검증
| ID | 테스트 | 필드 | endian |
|----|--------|------|--------|
| A063 | temperature | company_id | LE |
| A064 | humidity | data[0:2] | LE |
| A065 | pressure | data[2:4] | BE |
| A066 | accel_rms_x | data[6:8] | BE |
| A067 | velocity_rms | data[12:14] | LE |
| A068 | prob_temp | data[25:27] | LE |

### A-16. 실제 센서 패킷 재현
| ID | 테스트 | 설명 |
|----|--------|------|
| A069 | 실제 POSIOT v1 패킷 #1 | 실측 온도 23.45, 습도 65.2 |
| A070 | 실제 POSIOT v1 패킷 #2 | 고온 80도 환경 |
| A071 | 실제 POSIOT v1 패킷 #3 | 저온 -20도 환경 |
| A072 | 실제 POSIOT v1 패킷 #4 | 고진동 환경 |
| A073 | 실제 POSIOT v1 패킷 #5 | 배터리 부족 (5%) |
| A074 | 센서 리셋 직후 패킷 | 모든 필드 0 |
| A075 | 비정상 패킷 (깨진 데이터) | 랜덤 바이트 |

### A-17. 프로파일 자동 감지
| ID | 테스트 | 입력 | 기대 |
|----|--------|------|------|
| A076 | V1 27바이트 → posiot 선택 | 27바이트 | posiot |
| A077 | V2 22바이트 → posiot_v2 선택 | 22바이트 | posiot_v2 |
| A078 | 양쪽 다 파싱 가능 → 필드 수 비교 | 27바이트 | 더 많은 쪽 |
| A079 | 파싱 불가 → 빈 dict | 3바이트 | {} |
| A080 | 명시적 device_profile 지정 | "posiot" 지정 | posiot 강제 |

### A-18. company_id 범위
| ID | 테스트 | company_id | 기대 |
|----|--------|-----------|------|
| A081 | 0x0000 | 0 | temperature=0.0 |
| A082 | 0xFFFF | 65535 (uint) / -1 (int16) | -0.01 |
| A083 | 0x7FFF | 32767 | 327.67 |
| A084 | 0x8000 | -32768 (int16) | -327.68 |

### A-19. float 정밀도
| ID | 테스트 | 계산 | 검증 |
|----|--------|------|------|
| A085 | 온도 소수점 2자리 | 2345/100 | 23.45 (정확) |
| A086 | 습도 반올림 | 6667/100 | 66.67 |
| A087 | 기압 공식 round 2dp | (40000*255+50000)/4096 | 정확한 2dp |
| A088 | 가속도 소수점 3자리 | 1234/100 | 12.34 |
| A089 | velocity 소수점 2자리 | 999/100 | 9.99 |

### A-20. 에러 처리
| ID | 테스트 | 상황 | 기대 |
|----|--------|------|------|
| A090 | struct.unpack 실패 | 짧은 데이터 | warning 로그 + partial |
| A091 | None company_id | None | TypeError 또는 빈 dict |
| A092 | bytes가 아닌 입력 | string "abc" | TypeError |
| A093 | 빈 bytes | b'' | 빈 dict |
| A094 | bytearray 입력 | bytearray(27) | 정상 처리 |

### A-21. 필드 이름 일치
| ID | 테스트 | 검증 |
|----|--------|------|
| A095 | get_field_names() 반환값 | 19개 필드 이름 |
| A096 | parse 결과 키 == get_field_names() | 완전 일치 |
| A097 | CSV address 컬럼과 필드명 매칭 | temperature, humidity 등 |
| A098 | 대소문자 구분 | address="Temperature" vs "temperature" |
| A099 | 존재하지 않는 필드명 | address="unknown_field" → None |

### A-22. 멀티 센서 동시 파싱
| ID | 테스트 | 검증 |
|----|--------|------|
| A100 | 센서1 파싱 후 센서2 파싱 | 상태 간섭 없음 |
| A101 | 동시 비동기 파싱 | race condition 없음 |
| A102 | 같은 MAC 연속 파싱 | 캐시 활용 |
| A103 | 다른 MAC 교차 파싱 | 프로파일 분리 |

### A-23. 특수 센서 상태
| ID | 테스트 | 상태 |
|----|--------|------|
| A104 | 센서 슬립 모드 | mode=0x0F |
| A105 | 센서 충전 중 | battery 증가 패턴 |
| A106 | 센서 과열 | temperature > 80 |
| A107 | 센서 결빙 | temperature < -30 |
| A108 | 진동 경고 수준 | vibration_peak > 1000 |
| A109 | 소음 경고 수준 | sound_db > 85 |
| A110 | 배터리 임계치 | battery <= 10 |
| A111 | RSSI 약함 | rssi < -80 |
| A112 | RSSI 강함 | rssi > -30 |

### A-24. 연속 데이터 검증
| ID | 테스트 | 검증 |
|----|--------|------|
| A113 | 1000회 연속 파싱 안정성 | 메모리 누수 없음 |
| A114 | 동일 데이터 반복 파싱 | 결과 동일 |
| A115 | 점진적 온도 변화 | 연속성 확인 |
| A116 | 급격한 값 변화 | 정상 처리 |
| A117 | 패킷 손실 후 복구 | 마지막 유효값 |
| A118 | 타임스탬프 순서 | 단조 증가 |
| A119 | 패킷 간 간격 | 4초 필터 동작 |
| A120 | 캐시 TTL 만료 후 재수신 | 새 값으로 갱신 |

---

## B. POSIOT V2 프로파일 파싱 (B001~B100)

### B-1. 페이로드 재구성
| ID | 테스트 | 입력 | 기대 |
|----|--------|------|------|
| B001 | company_id + data → full 22바이트 | pack('<H', id) + data | 정상 재구성 |
| B002 | company_id 비트마스크 | id & 0xFFFF | 하위 16비트만 |
| B003 | company_id=0 | 0 | full[0:2] = b'\x00\x00' |
| B004 | company_id=0xFFFF | 65535 | full[0:2] = b'\xFF\xFF' |

### B-2. mode/version (full[0] 상위/하위 4비트)
| ID | 테스트 | 입력 | mode | version |
|----|--------|------|------|---------|
| B005 | 일반 | 0x21 | 2 | 1 |
| B006 | 최대 | 0xFF | 15 | 15 |
| B007 | 최소 | 0x00 | 0 | 0 |
| B008 | mode만 | 0xF0 | 15 | 0 |
| B009 | version만 | 0x0F | 0 | 15 |

### B-3. sound_db (full[1:3] → uint16_be)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B010 | 60dB | b'\x00\x3C' | 60 |
| B011 | 0dB | b'\x00\x00' | 0 |
| B012 | 최대 | b'\xFF\xFF' | 65535 |

### B-4. velocity_rms (full[3:5] → uint16_be)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B013 | 정상 | b'\x03\xE8' | 1000 |
| B014 | 0 | b'\x00\x00' | 0 |

### B-5. accel_x/y/z (full[5:11] → 3×int16_be, mg)
| ID | 테스트 | 입력 | x | y | z |
|----|--------|------|---|---|---|
| B015 | 양수 | 0x0064,0x00C8,0x012C | 100 | 200 | 300 |
| B016 | 음수 | 0xFF9C,0xFF38,0xFED4 | -100 | -200 | -300 |
| B017 | 0 | 0x0000×3 | 0 | 0 | 0 |
| B018 | 최대 int16 | 0x7FFF | 32767mg |
| B019 | 최소 int16 | 0x8000 | -32768mg |

### B-6. temperature (full[11:13] → int16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B020 | 25.00도 | b'\xC4\x09' | 25.00 |
| B021 | -10.00도 | b'\x18\xFC' | -10.00 |
| B022 | 0도 | b'\x00\x00' | 0.00 |
| B023 | 센서 상한 | b'\x34\x21' (8500) | 85.00 |
| B024 | 센서 하한 | b'\x60\xF0' (-4000) | -40.00 |

### B-7. humidity (full[13:15] → uint16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B025 | 65.20% | b'\x78\x19' (6520) | 65.20 |
| B026 | 0% | b'\x00\x00' | 0.00 |
| B027 | 100% | b'\x10\x27' | 100.00 |

### B-8. FFT 주파수 (vib_fft_freq, sound_fft_freq)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B028 | vib 100Hz | b'\x00\x64' | 100 |
| B029 | sound 1000Hz | b'\x03\xE8' | 1000 |
| B030 | 0Hz | b'\x00\x00' | 0 |

### B-9. probe (full[19:21] → int16_le / 100.0)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B031 | 30.50도 | b'\xEA\x0B' (3050) | 30.50 |
| B032 | 음수 | b'\x9C\xF8' (-1892) | -18.92 |

### B-10. battery (full[21] → uint8, %)
| ID | 테스트 | 입력 | 기대값 |
|----|--------|------|--------|
| B033 | 95% | b'\x5F' | 95 |
| B034 | 0% | b'\x00' | 0 |
| B035 | 100% | b'\x64' | 100 |

### B-11. 데이터 길이 경계 (V2)
| ID | 테스트 | 길이 | 기대 |
|----|--------|------|------|
| B036 | 0바이트 data | full=2 | mode/version만 |
| B037 | 10바이트 data | full=12 | accel_z까지 |
| B038 | 20바이트 data (정상) | full=22 | 전체 파싱 |
| B039 | 25바이트 (초과) | full=27 | 정상 (초과 무시) |

### B-12. V1 vs V2 구분
| ID | 테스트 | 검증 |
|----|--------|------|
| B040 | V2 필드명 목록 | 13개 필드 |
| B041 | V1에는 있고 V2에는 없는 필드 | pressure, harmony 등 |
| B042 | V2에는 있고 V1에는 없는 필드 | vib_fft_freq 등 |
| B043 | name 프로퍼티 | "posiot_v2" |
| B044 | V1 데이터를 V2로 파싱 시 | 값 불일치 |
| B045 | V2 데이터를 V1로 파싱 시 | 값 불일치 |

### B-13. 특수값/경계값 (V2 전체)
| ID | 테스트 | 검증 |
|----|--------|------|
| B046~B060 | 각 필드별 min/max/zero | 15개 필드 × 3 |
| B061~B070 | 바이트 오더 교차 검증 | LE vs BE 필드 혼합 |
| B071~B080 | struct.error 복구 | 각 오프셋별 |
| B081~B090 | 실제 V2 센서 패킷 재현 | 10개 시나리오 |
| B091~B100 | 연속 파싱 안정성 | 반복/병렬/캐시 |

---

## C. BLE Scanner (C001~C080)

### C-1. 싱글톤 패턴
| ID | 테스트 | 검증 |
|----|--------|------|
| C001 | get_instance() 첫 호출 | 새 인스턴스 생성 |
| C002 | get_instance() 두 번째 호출 | 동일 인스턴스 |
| C003 | 10회 호출 | 모두 동일 객체 |

### C-2. 레퍼런스 카운팅
| ID | 테스트 | 동작 | ref_count |
|----|--------|------|-----------|
| C004 | acquire 1회 | 스캔 시작 | 1 |
| C005 | acquire 2회 | 스캔 유지 | 2 |
| C006 | release 1회 (count=2) | 스캔 유지 | 1 |
| C007 | release 마지막 (count=1) | 스캔 중지 | 0 |
| C008 | release 과다 (count=0) | max(0, -1)=0 | 0 |
| C009 | acquire→release→acquire | 재시작 | 1 |
| C010 | 100회 acquire → 100회 release | 정상 종료 | 0 |

### C-3. TTL 설정 충돌
| ID | 테스트 | 검증 |
|----|--------|------|
| C011 | 첫 acquire ttl=30 | 30으로 설정 |
| C012 | 두 번째 acquire ttl=60 (다름) | warning 로그, 30 유지 |
| C013 | 첫 acquire filter=4, 두 번째 filter=2 | warning |

### C-4. Advertisement 캐시
| ID | 테스트 | 검증 |
|----|--------|------|
| C014 | 새 MAC 수신 | 캐시에 추가 |
| C015 | 기존 MAC 업데이트 | 캐시 갱신 |
| C016 | get_latest(존재하는 MAC) | AdvertisementEntry 반환 |
| C017 | get_latest(없는 MAC) | None |
| C018 | get_latest(대소문자 혼합) | upper 변환 후 조회 |
| C019 | TTL 내 조회 | 유효값 반환 |
| C020 | TTL 초과 조회 | None 반환 |
| C021 | TTL 정확히 경계 | 30.0초 → 경계값 |
| C022 | 캐시 100개 MAC | 메모리 정상 |
| C023 | 캐시 1000개 MAC | 성능 저하 없음 |

### C-5. 중복 필터
| ID | 테스트 | 검증 |
|----|--------|------|
| C024 | 같은 MAC 0.5초 간격 (filter=4) | 두 번째 무시 |
| C025 | 같은 MAC 4.1초 간격 (filter=4) | 두 번째 허용 |
| C026 | 같은 MAC 정확히 4.0초 | 경계값 |
| C027 | 다른 MAC 0.5초 간격 | 둘 다 허용 |
| C028 | filter=0 | 모든 패킷 허용 |
| C029 | filter=100 | 100초간 중복 차단 |

### C-6. 캐시 정리
| ID | 테스트 | 검증 |
|----|--------|------|
| C030 | TTL*2 초과 엔트리 | 정리됨 |
| C031 | TTL*2 이내 엔트리 | 유지 |
| C032 | _last_seen도 함께 정리 | pop 확인 |
| C033 | 정리 후 get_latest | None |

### C-7. 백오프 재연결
| ID | 테스트 | 검증 |
|----|--------|------|
| C034 | 첫 실패 → 1초 대기 | backoff=1.0 |
| C035 | 두 번째 실패 → 2초 | backoff=2.0 |
| C036 | 세 번째 실패 → 4초 | backoff=4.0 |
| C037 | 네 번째 실패 → 8초 | backoff=8.0 |
| C038 | 다섯 번째 실패 → 10초 (cap) | backoff=10.0 |
| C039 | 성공 후 리셋 | backoff=1.0 |
| C040 | 10회 연속 실패 후 성공 | 정상 복구 |

### C-8. AdvertisementEntry 구조
| ID | 테스트 | 검증 |
|----|--------|------|
| C041 | mac_address 저장 | upper case |
| C042 | device_name | 문자열 또는 None |
| C043 | rssi 범위 | -120 ~ 0 dBm |
| C044 | manufacturer_data | Dict[int, bytes] |
| C045 | timestamp | datetime 객체 |
| C046 | manufacturer_data 빈 dict | {} |
| C047 | manufacturer_data 여러 company_id | 다중 키 |

### C-9. 스캐너 시작/중지
| ID | 테스트 | 검증 |
|----|--------|------|
| C048 | start 정상 | _is_running=True |
| C049 | stop 정상 | _is_running=False |
| C050 | start → stop → start | 재시작 OK |
| C051 | 이미 시작된 상태에서 start | 무시 또는 경고 |
| C052 | 이미 중지된 상태에서 stop | 무시 |

### C-10. is_device_available
| ID | 테스트 | 검증 |
|----|--------|------|
| C053 | 캐시에 있고 TTL 내 | True |
| C054 | 캐시에 있고 TTL 초과 | False |
| C055 | 캐시에 없음 | False |
| C056 | MAC 대소문자 | 정규화 |

### C-11. 에러 처리
| ID | 테스트 | 검증 |
|----|--------|------|
| C057 | BleakError (Bluetooth 꺼짐) | 재시도 + 로그 |
| C058 | OSError (D-Bus 실패) | 재시도 |
| C059 | asyncio.CancelledError | 정상 종료 |
| C060 | 예상치 못한 Exception | 로그 + 재시도 |

### C-12. 동시성
| ID | 테스트 | 검증 |
|----|--------|------|
| C061 | 2개 collector 동시 acquire | ref_count=2 |
| C062 | 동시 get_latest | 동일 결과 |
| C063 | acquire 중 release | 정상 처리 |
| C064 | callback 중 get_latest | 안전 |
| C065~C080 | 다양한 동시성 시나리오 | 16개 추가 |

---

## D. BLE Collector - Single (D001~D050)

| ID | 테스트 | 검증 |
|----|--------|------|
| D001 | mac_address 필수 확인 | 없으면 에러 |
| D002 | mac_address 대문자 변환 | "aa:bb:cc" → "AA:BB:CC" |
| D003 | device_profile 기본값 | "posiot" |
| D004 | cache_ttl 기본값 | 30.0 |
| D005 | duplicate_filter_s 기본값 | 4.0 |
| D006 | _do_connect 성공 | scanner acquire |
| D007 | _do_connect 실패 | False 반환 |
| D008 | _do_collect 정상 | CollectedData 반환 |
| D009 | _do_collect 캐시 없음 | None |
| D010 | _do_collect TTL 초과 | None |
| D011 | device_name_filter 매칭 | 통과 |
| D012 | device_name_filter 불일치 | None |
| D013 | device_name_filter 빈 문자열 | 필터 안 함 |
| D014 | metadata에 manufacturer_data | dict 포함 |
| D015 | metadata에 rssi | int 값 |
| D016 | metadata에 mac_address | MAC 문자열 |
| D017 | metadata에 device_profile | 프로파일명 |
| D018 | _do_disconnect | scanner release |
| D019 | 연속 connect/disconnect | 정상 |
| D020 | plc_id vs ble_id 설정 | config 기반 |
| D021~D050 | 다양한 설정 조합 | 30개 추가 |

---

## E. BLE MultiCollector (E001~E070)

| ID | 테스트 | 검증 |
|----|--------|------|
| E001 | CSV에서 MAC 자동 추출 | 고유 MAC 목록 |
| E002 | MAC 2개 CSV | _devices에 2개 |
| E003 | MAC 4개 CSV | _devices에 4개 |
| E004 | MAC 중복 CSV (같은 MAC 20태그) | _devices에 1개, tag_count=20 |
| E005 | mac_address 빈 태그 | 무시 |
| E006 | mac_address 대소문자 혼합 | upper 통일 |
| E007 | device_name_filter 태그별 다름 | 첫 번째 사용 |
| E008 | collection_loop: 전 디바이스 순회 | _devices 전체 |
| E009 | collection_loop: 1개 디바이스 데이터 있음 | collected_count=1 |
| E010 | collection_loop: 모든 디바이스 데이터 없음 | failure 처리 |
| E011 | collection_loop: interval 준수 | sleep 계산 |
| E012 | _collect_device 정상 | CollectedData |
| E013 | _collect_device 캐시 없음 | None |
| E014 | _device_last_seen 갱신 | 타임스탬프 업데이트 |
| E015 | health_check: 1개 이상 가용 | True |
| E016 | health_check: 전부 불가 | False |
| E017 | health_check: scanner None | False |
| E018 | 디바이스 추가 (런타임) | CSV 리로드 |
| E019 | 디바이스 제거 | 정리 |
| E020 | consecutive_loss 카운트 | 실패 시 증가 |
| E021 | consecutive_loss 리셋 | 성공 시 0 |
| E022 | total_collected 누적 | 정상 |
| E023 | elapsed > interval | sleep_time=0 |
| E024 | elapsed < interval | 남은 시간 sleep |
| E025 | _is_running=False 시 루프 종료 | 정상 |
| E026 | 50개 디바이스 동시 | 성능 |
| E027~E070 | 다양한 시나리오 | 44개 추가 |

---

## F. BLE Processor (F001~F060)

| ID | 테스트 | 검증 |
|----|--------|------|
| F001 | manufacturer_data 파싱 → parsed_values | dict |
| F002 | manufacturer_data 빈 dict | parsed_values={} |
| F003 | 여러 company_id 중 첫 번째 사용 | next(iter()) |
| F004 | rssi meta 필드 추가 | parsed_values['rssi'] |
| F005 | device_name meta 필드 | parsed_values['device_name'] |
| F006 | mac_address meta 필드 | parsed_values['mac_address'] |
| F007 | MAC 기반 태그 필터링 | mac 일치만 |
| F008 | MAC 불일치 태그 스킵 | continue |
| F009 | MAC 없는 태그 (전체 적용) | mac_address="" |
| F010 | address → field_name 매핑 | tag.address.lower() |
| F011 | "temperature" → parsed_values["temperature"] | 값 반환 |
| F012 | "unknown_field" → None | 스킵 |
| F013 | 대소문자 | address="Temperature" → lower |
| F014 | 빈 address | field_name="" → 매칭 안 됨 |
| F015 | 프로파일 캐시 히트 | _mac_profile_cache |
| F016 | 프로파일 캐시 미스 → 자동감지 | 전체 시도 |
| F017 | 명시적 device_profile | 캐시 무시 |
| F018 | 2개 프로파일 모두 실패 | 빈 dict |
| F019 | 결과 (tag, value) 튜플 리스트 | 정상 |
| F020 | 4개 디바이스 20태그씩 | 올바른 필터링 |
| F021~F060 | 다양한 매핑/필터 조합 | 40개 추가 |

---

## G. 태그 스케일링 — apply_scaling (G001~G100)

### G-1. 기본 스케일링 (raw * scale + offset)
| ID | 입력 | scale | offset | 기대값 |
|----|------|-------|--------|--------|
| G001 | raw=100 | 1.0 | 0.0 | 100 |
| G002 | raw=100 | 2.0 | 0.0 | 200 |
| G003 | raw=100 | 1.0 | 50.0 | 150 |
| G004 | raw=100 | 0.5 | -10.0 | 40.0 |
| G005 | raw=0 | 5.0 | 10.0 | 10.0 |
| G006 | raw=-100 | 1.0 | 0.0 | -100 |
| G007 | raw=65535 | 0.001 | 0 | 65.535 |

### G-2. decimals — 정수 타입 (÷10^decimals)
| ID | raw | data_type | decimals | 기대값 |
|----|-----|-----------|----------|--------|
| G008 | 3061 | uint16 | 2 | 30.61 |
| G009 | 3061 | uint16 | 1 | 306.1 |
| G010 | 3061 | uint16 | 3 | 3.061 |
| G011 | 3061 | uint16 | 0 | 3061 (round) |
| G012 | 100 | int32 | 2 | 1.00 |
| G013 | 1 | uint16 | 2 | 0.01 |
| G014 | 0 | uint16 | 2 | 0.00 |
| G015 | 65535 | uint16 | 2 | 655.35 |

### G-3. decimals — float 타입 (round만)
| ID | raw | data_type | decimals | 기대값 |
|----|-----|-----------|----------|--------|
| G016 | 69.123 | float32 | 1 | 69.1 |
| G017 | 69.156 | float32 | 1 | 69.2 |
| G018 | 69.150 | float32 | 1 | 69.2 (반올림) |
| G019 | 3.14159 | float64 | 2 | 3.14 |
| G020 | 3.14159 | float64 | 4 | 3.1416 |

### G-4. NaN/Inf 검증
| ID | raw | 기대 |
|----|-----|------|
| G021 | float('nan') | None |
| G022 | float('inf') | None |
| G023 | float('-inf') | None |
| G024 | 정상 float | 정상값 |

### G-5. None 처리
| ID | raw | 기대 |
|----|-----|------|
| G025 | None | None |
| G026 | None (scale=2) | None |

### G-6. STRING 타입
| ID | raw | 기대 |
|----|-----|------|
| G027 | "hello" | "hello" (스케일링 없음) |
| G028 | "" | "" |
| G029 | "123" (STRING 타입) | "123" |

### G-7. BOOL 타입
| ID | raw | bool_invert | bool_true_value | 기대 |
|----|-----|-------------|-----------------|------|
| G030 | True | False | None | True |
| G031 | False | False | None | False |
| G032 | True | True | None | False |
| G033 | False | True | None | True |
| G034 | 1 | False | None | True |
| G035 | 0 | False | None | False |
| G036 | 5 | False | 3 | True (5>=3) |
| G037 | 2 | False | 3 | False (2<3) |
| G038 | 5 | False | None, false=3 | True (5>3) |
| G039 | 3 | False | None, false=3 | False (3==3, not >) |
| G040 | "invalid" | False | None | False (ValueError) |

### G-8. scale + decimals 조합
| ID | raw | scale | offset | decimals | type | 기대 |
|----|-----|-------|--------|----------|------|------|
| G041 | 100 | 2.0 | 10 | 2 | uint16 | (200+10)/100=2.10 |
| G042 | 100 | 2.0 | 10 | 2 | float32 | round(210, 2)=210.0 |
| G043 | 3061 | 1.0 | 0 | 2 | uint16 | 30.61 |
| G044 | 3061 | 1.0 | 0 | 2 | float32 | round(3061, 2)=3061.0 |
| G045 | 500 | 0.1 | -5 | 1 | int16 | (50-5)/10=4.5 |
| G046~G100 | 다양한 조합 | | | | | 55개 추가 |

---

## H. 데이터 타입 해석 (H001~H060)

### H-1. _resolve_data_type
| ID | raw_type | data_size | 기대 DataType |
|----|----------|-----------|---------------|
| H001 | "BOOL" | 16 | BOOL |
| H002 | "BIT" | 16 | BOOL |
| H003 | "UINT16" | 16 | UINT16 |
| H004 | "UINT32" | 32 | UINT32 |
| H005 | "INT16" | 16 | INT16 |
| H006 | "INT32" | 32 | INT32 |
| H007 | "FLOAT32" | 32 | FLOAT32 |
| H008 | "FLOAT64" | 64 | FLOAT64 |
| H009 | "REAL" | 32 | FLOAT32 |
| H010 | "LREAL" | 64 | FLOAT64 |
| H011 | "STRING" | 16 | STRING |
| H012 | "BYTE" | 8 | BYTE |
| H013 | "WORD" | 16 | WORD |
| H014 | "DWORD" | 32 | DWORD |
| H015 | "LWORD" | 64 | LWORD |
| H016 | "UDEC" | 16 | UINT16 |
| H017 | "UDEC" | 32 | UINT32 |
| H018 | "UDEC" | 64 | UINT64 |
| H019 | "DEC" | 16 | INT16 |
| H020 | "DEC" | 32 | INT32 |
| H021 | "DEC" | 64 | INT64 |
| H022 | "FLOAT" | 32 | FLOAT32 |
| H023 | "FLOAT" | 64 | FLOAT64 |
| H024 | "UNKNOWN" | 16 | FLOAT32 (기본) |
| H025 | "" (빈) | 16 | FLOAT32 |

### H-2. _compute_output_type
| ID | data_type | format | scale | decimals | 기대 output |
|----|-----------|--------|-------|----------|-------------|
| H026 | UINT16 | None | 1.0 | None | "int" |
| H027 | UINT16 | None | 2.0 | None | "float" |
| H028 | UINT16 | None | 1.0 | 2 | "float" |
| H029 | UINT32 | None | 1.0 | None | "bigint" |
| H030 | FLOAT32 | None | 1.0 | None | "float" |
| H031 | BOOL | None | 1.0 | None | "bool" |
| H032 | STRING | None | 1.0 | None | "text" |
| H033 | INT16 | "float32" | 1.0 | None | "float" |
| H034 | UINT16 | "int" | 1.0 | None | "int" |
| H035 | INT64 | None | 1.0 | None | "bigint" |

### H-3. ProcessedData 값 할당
| ID | output_type | scaled_value | v_bool | v_int | v_float | v_text |
|----|-------------|-------------|--------|-------|---------|--------|
| H036 | "float" | 23.45 | None | None | 23.45 | None |
| H037 | "int" | 100 | None | 100 | None | None |
| H038 | "bigint" | 4294967295 | None | None※bigint | None | None |
| H039 | "bool" | True | True | None | None | None |
| H040 | "text" | "hello" | None | None | None | "hello" |
| H041 | "float" | None (질량 0) | None | None | None | None |

### H-4. to_dict() 출력
| ID | 테스트 | 검증 |
|----|--------|------|
| H042 | source_time ISO format | milliseconds |
| H043 | plc_id 키 (PLC) | "plc_id" |
| H044 | ble_id 키 (BLE) | "ble_id" |
| H045 | v_float round(6) | 소수점 6자리 |
| H046 | collection_group="default" | 키 생략 |
| H047 | collection_group="plc_data" | 키 포함 |
| H048 | tag_name 빈 문자열 | 키 생략 |
| H049 | tag_name "온도" | 키 포함 |
| H050 | None 값 필드 | 키 생략 |

### H-5. byte_size
| ID | DataType | 기대 size |
|----|----------|----------|
| H051 | BOOL | 1 |
| H052 | UINT16 | 2 |
| H053 | UINT32 | 4 |
| H054 | FLOAT32 | 4 |
| H055 | FLOAT64 | 8 |
| H056 | INT64 | 8 |
| H057 | STRING | 1 |
| H058 | BYTE | 1 |
| H059 | WORD | 2 |
| H060 | LWORD | 8 |

---

## I. Config 파싱 (I001~I080)

### I-1. 환경변수 치환
| ID | 입력 | 환경변수 | 기대 |
|----|------|---------|------|
| I001 | "${RMQ_HOST:rabbitmq}" | RMQ_HOST 미설정 | "rabbitmq" |
| I002 | "${RMQ_HOST:rabbitmq}" | RMQ_HOST=myhost | "myhost" |
| I003 | "${RMQ_PORT:5672}" | RMQ_PORT 미설정 | "5672" |
| I004 | "${VAR}" (기본값 없음) | VAR 미설정 | "${VAR}" (원본) |
| I005 | "${VAR}" | VAR=hello | "hello" |
| I006 | "prefix_${VAR}_suffix" | VAR=mid | "prefix_mid_suffix" |
| I007 | "${A:1} ${B:2}" | 둘 다 미설정 | "1 2" |
| I008 | 치환 없는 문자열 | - | 그대로 |
| I009 | "${:}" (빈 변수명) | - | 그대로 |
| I010 | 숫자로 시작하는 변수 | ${1VAR:x} | 처리 |

### I-2. CSV 태그 파싱
| ID | 테스트 | 검증 |
|----|--------|------|
| I011 | 정상 CSV 행 | TagDefinition 생성 |
| I012 | tag_id 정수 변환 | int("1") = 1 |
| I013 | tag_id 비숫자 | ValueError |
| I014 | data_type 대소문자 | "float32" → FLOAT32 |
| I015 | scale 빈 값 → 1.0 | 기본값 |
| I016 | offset 빈 값 → 0.0 | 기본값 |
| I017 | decimals 빈 값 → None | 기본값 |
| I018 | memory + address 분리 | "D" + "200" → "D200" |
| I019 | memory 없음 (구형) | address 컬럼 그대로 |
| I020 | STRING + word_length | "D100:10" |
| I021 | mac_address 대문자 변환 | "aa:bb" → "AA:BB" |
| I022 | mac_address 빈 값 | "" |
| I023 | device_name 트림 | " POSIOT " → "POSIOT" |
| I024 | byte_offset 빈 → None | None |
| I025 | unit 컬럼 | "℃", "%", "dBm" 등 |
| I026 | description 한글 | "1호기 온도" |
| I027 | CSV 헤더 누락 컬럼 | KeyError 또는 기본값 |
| I028 | CSV 빈 행 | 스킵 |
| I029 | CSV 주석 행 (#) | 스킵 |
| I030 | CSV BOM 마커 | 처리 |

### I-3. YAML 파싱
| ID | 테스트 | 검증 |
|----|--------|------|
| I031 | collector.ble_id 파싱 | 정수 |
| I032 | collector.ble_id 없음 → None | None |
| I033 | collector.plc_id 파싱 | 정수 |
| I034 | protocol.type="ble" | BLE 모드 |
| I035 | protocol.type="mc_protocol" | MC 모드 |
| I036 | protocol.extra 파싱 | dict |
| I037 | collection_groups 리스트 | 복수 그룹 |
| I038 | publisher.rabbitmq.enabled | bool |
| I039 | buffer 설정 | max_size, batch_size 등 |
| I040 | logging 설정 | level, collection_level |

### I-4. device_type 자동 감지
| ID | 테스트 | 검증 |
|----|--------|------|
| I041 | ble_id 설정됨 → "ble" | device_type="ble" |
| I042 | ble_id 없음 → "plc" | device_type="plc" |
| I043 | ble_id=0 → "ble" | 0도 유효 |
| I044 | plc_id=1, ble_id=None → "plc" | plc_id 사용 |

### I-5. Config 검증
| ID | 테스트 | 검증 |
|----|--------|------|
| I045 | plc_id=0 (범위 외) | 에러 |
| I046 | plc_id=101 (범위 외) | 에러 |
| I047 | plc_id=1 (정상) | OK |
| I048 | ble_id=-1 (범위 외) | 에러 |
| I049 | ble_id=101 (범위 외) | 에러 |
| I050 | ble_id=0 (정상) | OK |
| I051 | collection_groups 비어있음 | 에러 |
| I052 | interval_ms=9 (최소 미만) | 에러 |
| I053 | interval_ms=10 (최소) | OK |
| I054 | batch_size > max_size | 에러 |
| I055 | threshold_ratio=-0.1 | 에러 |
| I056 | threshold_ratio=1.1 | 에러 |
| I057 | threshold_ratio=0.8 (정상) | OK |

### I-6. 파일 경로
| ID | 테스트 | 검증 |
|----|--------|------|
| I058 | tags_file 상대 경로 | YAML 기준 해석 |
| I059 | tags_file 절대 경로 | 그대로 사용 |
| I060 | tags_file 존재하지 않음 | FileNotFoundError |
| I061 | YAML 파일 존재하지 않음 | FileNotFoundError |
| I062 | YAML 문법 오류 | yaml.YAMLError |
| I063 | CSV 인코딩 UTF-8 | 정상 |
| I064 | CSV 인코딩 EUC-KR | 에러 또는 fallback |

### I-7. 다양한 그룹 설정
| ID | 테스트 | 검증 |
|----|--------|------|
| I065 | mode="polling" | 기본 |
| I066 | mode="on_change" | deadband 활성 |
| I067 | deadband=0.5 | 절대값 |
| I068 | deadband_type="percent" | 퍼센트 |
| I069 | timeout_ms=5000 | 기본값 |
| I070 | retry_count=3 | 기본값 |
| I071 | 그룹 3개 (plc_data, alm, ble_data) | 복수 |
| I072~I080 | 추가 설정 조합 | 9개 |

---

## J. on_change / deadband (J001~J060)

### J-1. 첫 관측
| ID | old | new | 기대 |
|----|-----|-----|------|
| J001 | (없음) | 100 | changed=True |
| J002 | (없음) | None | changed=True |
| J003 | (없음) | True | changed=True |

### J-2. None 처리
| ID | old | new | 기대 |
|----|-----|-----|------|
| J004 | None | None | False |
| J005 | None | 100 | True |
| J006 | 100 | None | True |

### J-3. Bool 비교
| ID | old | new | 기대 |
|----|-----|-----|------|
| J007 | True | True | False |
| J008 | True | False | True |
| J009 | False | True | True |
| J010 | False | False | False |
| J011 | 1 (int) | True | ? (bool vs int) |

### J-4. String 비교
| ID | old | new | 기대 |
|----|-----|-----|------|
| J012 | "abc" | "abc" | False |
| J013 | "abc" | "def" | True |
| J014 | "" | "" | False |
| J015 | "" | "x" | True |

### J-5. 절대 deadband
| ID | old | new | deadband | 기대 |
|----|-----|-----|---------|------|
| J016 | 100.0 | 100.0 | 0.5 | False |
| J017 | 100.0 | 100.4 | 0.5 | False |
| J018 | 100.0 | 100.6 | 0.5 | True |
| J019 | 100.0 | 99.4 | 0.5 | True |
| J020 | 100.0 | 100.5 | 0.5 | False (<=, not >) |
| J021 | 0.0 | 0.0 | 0.0 | False |
| J022 | 0.0 | 0.001 | 0.0 | True |
| J023 | -100 | -99 | 0.5 | True |
| J024 | -100 | -100.3 | 0.5 | False |

### J-6. 퍼센트 deadband
| ID | old | new | deadband | type | 기대 |
|----|-----|-----|---------|------|------|
| J025 | 100.0 | 105.0 | 10 | percent | False (5%) |
| J026 | 100.0 | 111.0 | 10 | percent | True (11%) |
| J027 | 100.0 | 110.0 | 10 | percent | False (10%, not >) |
| J028 | 0.0 | 5.0 | 10 | percent | True (old=0 특수) |
| J029 | 0.0 | 0.0 | 10 | percent | False |
| J030 | -100 | -90 | 10 | percent | False (10%) |
| J031 | -100 | -89 | 10 | percent | True (11%) |

### J-7. deadband=0 (기본)
| ID | old | new | 기대 |
|----|-----|-----|------|
| J032 | 100.0 | 100.0 | False |
| J033 | 100.0 | 100.001 | True |
| J034 | 100 | 100 | False (int) |
| J035 | 100 | 101 | True |

### J-8. 캐시 동작
| ID | 테스트 | 검증 |
|----|--------|------|
| J036 | 변경 시 캐시 업데이트 | 새 값 저장 |
| J037 | 미변경 시 캐시 유지 | 이전 값 유지 |
| J038 | plc_id별 캐시 분리 | (1,tag1) ≠ (2,tag1) |
| J039 | tag_id별 캐시 분리 | (1,tag1) ≠ (1,tag2) |

### J-9. 타입 fallback
| ID | old | new | 기대 |
|----|-----|-----|------|
| J040 | "abc" | 123 | str 비교 fallback |
| J041 | [1,2] | [1,2] | str 비교 |
| J042 | 복합 객체 | 복합 객체 | str(old)≠str(new) |

### J-10. 연속 시나리오
| ID | 시퀀스 | 기대 changed 수열 |
|----|--------|-------------------|
| J043 | [100, 100, 101, 101, 100] | [T, F, T, F, T] |
| J044 | [T, F, T, F, T] (bool) | [T, T, T, T, T] |
| J045 | [0, 0, 0, 1, 0] (db=0) | [T, F, F, T, T] |
| J046~J060 | 다양한 시퀀스 | 15개 추가 |

---

## K. 직렬화 (K001~K040)

### K-1. JSON
| ID | 테스트 | 검증 |
|----|--------|------|
| K001 | ensure_ascii=False | 한글 그대로 |
| K002 | separators=(',', ':') | 공백 없는 compact |
| K003 | 빈 리스트 | b'[]' |
| K004 | 단일 레코드 | 정상 JSON |
| K005 | 1000개 레코드 | 정상 |
| K006 | 특수문자 포함 (", \, /) | 이스케이프 |
| K007 | 유니코드 (이모지) | 정상 |
| K008 | None 값 | null |
| K009 | float NaN | JSON 미지원 → 에러 |
| K010 | datetime 객체 | serialize 전 ISO 변환 필요 |

### K-2. 압축
| ID | 테스트 | 검증 |
|----|--------|------|
| K011 | zlib 압축 level=6 | 정상 |
| K012 | gzip 압축 level=6 | 정상 |
| K013 | none (비압축) | 원본 그대로 |
| K014 | zlib 해제 | 원본 복원 |
| K015 | gzip 해제 | 원본 복원 |
| K016 | 잘못된 압축 데이터 해제 | zlib.error |
| K017 | 압축률 확인 (100건) | < 원본 |
| K018 | 1바이트 데이터 압축 | 오버헤드 |

### K-3. 암호화
| ID | 테스트 | 검증 |
|----|--------|------|
| K019 | Fernet 암호화 | 정상 |
| K020 | Fernet 복호화 | 원본 복원 |
| K021 | 잘못된 키로 복호화 | InvalidToken |
| K022 | 암호화 비활성 | 암호화 안 함 |
| K023 | 키 생성 | Fernet.generate_key() |
| K024 | 빈 데이터 암호화 | 정상 |
| K025 | 대용량 데이터 암호화 | 정상 |

### K-4. 전체 파이프라인
| ID | 테스트 | 검증 |
|----|--------|------|
| K026 | serialize → deserialize 왕복 | 원본 일치 |
| K027 | JSON → zlib → Fernet → 역순 | 일치 |
| K028 | JSON → zlib (no encrypt) → 역순 | 일치 |
| K029 | JSON → none → none → 역순 | 일치 |
| K030 | 한글 데이터 왕복 | 일치 |
| K031 | 빈 리스트 왕복 | [] |
| K032 | 1만 건 왕복 | 일치 |
| K033~K040 | 에지 케이스 | 8개 추가 |

---

## L. RabbitMQ Publisher (L001~L050)

| ID | 테스트 | 검증 |
|----|--------|------|
| L001 | 연결 성공 | _is_connected=True |
| L002 | 연결 실패 (호스트 없음) | 에러 로그 |
| L003 | 잘못된 인증 정보 | AuthenticationError |
| L004 | exchange 선언 | topic durable |
| L005 | exchange_type=direct | DIRECT |
| L006 | exchange_type=fanout | FANOUT |
| L007 | exchange_type=topic (기본) | TOPIC |
| L008 | virtual_host="/" | 정상 |
| L009 | virtual_host="/test" | 정상 |
| L010 | virtual_host 앞 / 자동 추가 | "test" → "/test" |
| L011 | 단일 레코드 발행 | 1건 |
| L012 | 배치 발행 (100건) | 정상 |
| L013 | 배치 발행 (1000건) | 정상 |
| L014 | plc_id별 그룹핑 | routing_key 분리 |
| L015 | ble_id별 그룹핑 | "ble.1.data" |
| L016 | routing_key 형식 | "{prefix}.{id}.data" |
| L017 | PERSISTENT 모드 | delivery_mode=2 |
| L018 | NON_PERSISTENT 모드 | delivery_mode=1 |
| L019 | 메시지 헤더 compression | "zlib" |
| L020 | 메시지 헤더 encrypted | "false" |
| L021 | 메시지 헤더 batch_count | 정수 |
| L022 | 메시지 헤더 collector_name | 선택적 |
| L023 | 메시지 timestamp | datetime |
| L024 | 연결 끊김 후 재연결 | robust connection |
| L025 | exchange 미선언 상태 publish | False |
| L026 | 빈 데이터 리스트 publish | 아무것도 안 함 |
| L027 | device_id_key 설정 | plc_id 또는 ble_id |
| L028~L050 | QoS, heartbeat, timeout 등 | 23개 추가 |

---

## M. MC Protocol (M001~M080)

### M-1. 디바이스 코드
| ID | 테스트 | 검증 |
|----|--------|------|
| M001 | D 디바이스 (워드) | 코드=0xA8, 10진수 주소 |
| M002 | W 디바이스 (워드) | 코드=0xB4, 16진수 주소 |
| M003 | M 디바이스 (비트→워드) | 코드=0x90, 워드 기반 읽기 |
| M004 | X 디바이스 (비트) | 코드=0x9C |
| M005 | Y 디바이스 (비트) | 코드=0x9D |
| M006 | L 디바이스 (비트) | 코드=0x92 |
| M007 | iQ-R 시리즈 | DEVICE_INFO_R |
| M008 | Q/L 시리즈 | DEVICE_INFO_Q |

### M-2. 주소 파싱
| ID | 입력 | 디바이스 | 주소 |
|----|------|---------|------|
| M009 | "D100" | D | 100 |
| M010 | "D0" | D | 0 |
| M011 | "D65535" | D | 65535 |
| M012 | "W1FF" | W | 0x1FF (16진수) |
| M013 | "M0" | M | 0 |
| M014 | "X0" | X | 0 |
| M015 | "Y100" | Y | 100 (또는 0x100) |

### M-3. 비트→워드 변환
| ID | 테스트 | 검증 |
|----|--------|------|
| M016 | M0~M15 → 1워드 | word_address=0 |
| M017 | M16~M31 → 1워드 | word_address=1 |
| M018 | M100 → word_address=6, bit=4 | 정확 |
| M019 | X (8진수 시리즈) | 8진수 변환 |
| M020 | X (16진수 시리즈) | 16진수 변환 |

### M-4. 프레임 빌드 & 파싱
| ID | 테스트 | 검증 |
|----|--------|------|
| M021 | 읽기 요청 프레임 | 바이너리 매칭 |
| M022 | 응답 프레임 파싱 | 데이터 추출 |
| M023 | 에러 응답 (0xC050) | "Wrong command" |
| M024 | 에러 응답 (0xC05B) | "Request point exceeded" |
| M025 | 에러 응답 (0xC060) | "CPU busy" |
| M026 | 에러 응답 (0xC070) | "Address out of range" |

### M-5. 데이터 읽기
| ID | 테스트 | 검증 |
|----|--------|------|
| M027 | 1워드 읽기 (uint16) | 2바이트 |
| M028 | 2워드 읽기 (uint32) | 4바이트 |
| M029 | 2워드 읽기 (float32) | IEEE 754 |
| M030 | 4워드 읽기 (float64) | IEEE 754 |
| M031 | 연속 100워드 읽기 | 200바이트 |
| M032 | 최대 960워드 읽기 | 1920바이트 |
| M033 | 961워드 (초과) | 분할 요청 |
| M034 | STRING 읽기 | 워드→문자열 |
| M035 | BOOL 읽기 (워드 기반) | 비트 추출 |

### M-6. 연결 관리
| ID | 테스트 | 검증 |
|----|--------|------|
| M036 | TCP 연결 성공 | socket open |
| M037 | 연결 타임아웃 | TimeoutError |
| M038 | 연결 거부 | ConnectionRefusedError |
| M039 | 재연결 (지수 백오프) | 1→2→4→8→10초 |
| M040 | 재연결 성공 후 캐시 초기화 | 정상 |

### M-7. 바이트 오더 & 타입 변환
| ID | 입력 (bytes) | data_type | 기대값 |
|----|-------------|-----------|--------|
| M041 | b'\x00\x64' | uint16 | 100 |
| M042 | b'\xFF\x9C' | int16 | -100 |
| M043 | b'\x00\x00\x00\x64' | uint32 | 100 |
| M044 | b'\x42\x28\x00\x00' | float32 | 42.0 |
| M045 | b'\x40\x45\x00\x00...' | float64 | 42.0 |
| M046 | b'\x01\x00' | bool (word) | True (bit 0) |
| M047 | b'\x00\x00' | bool (word) | False |
| M048~M080 | 다양한 조합 | 33개 추가 |

---

## N. Modbus Protocol (N001~N070)

### N-1. CRC-16 계산
| ID | 테스트 | 검증 |
|----|--------|------|
| N001 | 알려진 프레임 CRC | 정확한 CRC |
| N002 | 빈 데이터 | CRC=0xFFFF |
| N003 | 1바이트 | 정확 |
| N004 | CRC 테이블 초기화 | 256 엔트리 |
| N005 | verify(정상 프레임) | True |
| N006 | verify(손상 프레임) | False |
| N007 | verify(2바이트 미만) | False |

### N-2. Function Code
| ID | func_code | 설명 | 검증 |
|----|-----------|------|------|
| N008 | 0x01 | Read Coils | 정상 |
| N009 | 0x02 | Read Discrete Inputs | 정상 |
| N010 | 0x03 | Read Holding Registers | 정상 |
| N011 | 0x04 | Read Input Registers | 정상 |
| N012 | 0x81 | Error (0x01 + 0x80) | exception |

### N-3. Modbus 모드
| ID | 모드 | 검증 |
|----|------|------|
| N013 | TCP | MBAP 헤더 포함 |
| N014 | RTU | CRC 포함 |
| N015 | RTU_OVER_TCP | TCP + CRC |

### N-4. 예외 코드
| ID | code | 메시지 |
|----|------|--------|
| N016 | 0x01 | "Illegal Function" |
| N017 | 0x02 | "Illegal Data Address" |
| N018 | 0x03 | "Illegal Data Value" |
| N019 | 0x04 | "Slave Device Failure" |
| N020 | 0x06 | "Slave Device Busy" |

### N-5. 레지스터 읽기 & 타입 변환
| ID | 테스트 | 검증 |
|----|--------|------|
| N021 | Holding Register 1개 (uint16) | 2바이트 |
| N022 | Holding Register 2개 (uint32) | 4바이트 |
| N023 | Holding Register 2개 (float32) | IEEE 754 |
| N024 | Coil 1개 (bool) | 비트 |
| N025 | Discrete Input 1개 | 비트 |
| N026 | Input Register 1개 | 2바이트 |
| N027 | STRING (연속 레지스터) | 워드→ASCII |
| N028 | 바이트 스왑 (AB CD → CD AB) | word swap |
| N029 | 최대 125 레지스터 | 250바이트 |
| N030 | 126 레지스터 (초과) | 분할 |

### N-6. 연결/에러
| ID | 테스트 | 검증 |
|----|--------|------|
| N031 | TCP 연결 성공 | 정상 |
| N032 | 연결 타임아웃 | 재시도 |
| N033 | 응답 없음 | 타임아웃 |
| N034 | 부분 응답 | 재시도 |
| N035 | slave_id 불일치 | 무시 |
| N036~N070 | 다양한 시나리오 | 35개 추가 |

---

## O. Pipeline / Lifecycle (O001~O030)

| ID | 테스트 | 검증 |
|----|--------|------|
| O001 | start 순서: processor→publisher→collector | 정상 |
| O002 | stop 순서: collector→processor→publisher | 역순 |
| O003 | event_bus 시작 | 첫 소유자만 |
| O004 | 정상 종료 (SIGTERM) | graceful |
| O005 | 강제 종료 (SIGKILL) | 데이터 손실 가능 |
| O006 | collector 에러 → pipeline 유지 | 격리 |
| O007 | publisher 에러 → 버퍼 누적 | 정상 |
| O008 | processor 에러 → 데이터 드롭 | 로그 |
| O009 | 전체 파이프라인 1시간 연속 | 안정성 |
| O010 | 메모리 누수 검사 (1시간) | 증가 없음 |
| O011~O030 | 다양한 lifecycle 시나리오 | 20개 추가 |

---

## P. Docker / 배포 (P001~P030)

| ID | 테스트 | 검증 |
|----|--------|------|
| P001 | Dockerfile.ble 빌드 성공 | exit 0 |
| P002 | 멀티스테이지: builder → runtime | 크기 최소 |
| P003 | bleak 패키지 설치 | pip install |
| P004 | libdbus-1-3 설치 | apt-get |
| P005 | bluez 설치 | apt-get |
| P006 | 비root 사용자 생성 (collector) | gosu |
| P007 | D-Bus 볼륨 마운트 | /var/run/dbus |
| P008 | network_mode: host | BLE 필수 |
| P009 | privileged: true | BLE 필수 |
| P010 | 이미지 크기 < 200MB | 최적화 |
| P011 | ARM64 크로스빌드 | --platform linux/arm64 |
| P012 | docker-compose up (전체) | 정상 |
| P013 | docker-compose down -v | 정리 |
| P014 | 컨테이너 재시작 | unless-stopped |
| P015 | 환경변수 주입 | -e CONFIG_PATH |
| P016 | 볼륨 마운트 (:ro) | 읽기전용 |
| P017 | 로그 볼륨 (:rw) | 쓰기 |
| P018 | healthcheck 통과 | 정상 |
| P019 | 네트워크 collector-net | 컨테이너 간 통신 |
| P020 | RabbitMQ 포트 충돌 없음 | 56730/56731 |
| P021 | TimescaleDB 포트 충돌 없음 | 55433 |
| P022 | tar 이미지 export/load | docker save/load |
| P023 | build_deploy_ble.py 실행 | tar 생성 |
| P024~P030 | 추가 배포 시나리오 | 7개 |

---

## Q. 통합 테스트 E2E (Q001~Q050)

### Q-1. PLC 데이터 파이프라인
| ID | 테스트 | 검증 |
|----|--------|------|
| Q001 | PLC1 → collector → RabbitMQ → publisher → DB | plc_data_integrated |
| Q002 | PLC2 → collector → ... → DB | plc_id=2 분리 |
| Q003 | plc_data_latest UPSERT | PK별 1건 |
| Q004 | plc_data_master sync | 태그 정보 |
| Q005 | alm on_change 저장 | 변경분만 |

### Q-2. BLE 데이터 파이프라인
| ID | 테스트 | 검증 |
|----|--------|------|
| Q006 | BLE 센서 → scanner → collector → processor → publisher → RabbitMQ → publisher → DB | ble_data_integrated |
| Q007 | ble_data_latest UPSERT | PK별 1건 |
| Q008 | ble_data_master sync | 태그 정보 |
| Q009 | ble_master sync | BLE 디바이스 정보 |
| Q010 | BLE float 값 (온도/습도) | v_float 컬럼 |
| Q011 | BLE int 값 (배터리/RSSI) | v_int 컬럼 |
| Q012 | 멀티 센서 (MAC별) | tag_id 구분 |

### Q-3. 디바이스 타입 격리
| ID | 테스트 | 검증 |
|----|--------|------|
| Q013 | plc_data 테이블에 ble 데이터 없음 | WHERE ble_id → 컬럼 없음 |
| Q014 | ble_data 테이블에 plc 데이터 없음 | plc_id 컬럼 없음 |
| Q015 | plc_master에 BLE 장비 없음 | ble_id 없음 |
| Q016 | ble_master에 PLC 장비 없음 | plc_id 없음 |

### Q-4. 혼합 시나리오
| ID | 테스트 | 검증 |
|----|--------|------|
| Q017 | PLC+BLE 동시 발행 (같은 exchange) | 라우팅 분리 |
| Q018 | 순서 보장 | timestamp 순 |
| Q019 | 대량 혼합 (PLC 1000 + BLE 500) | 전수 저장 |
| Q020 | publisher 재시작 후 master sync | TRUNCATE + 재동기화 |

### Q-5. 스키마 초기화
| ID | 테스트 | 검증 |
|----|--------|------|
| Q021 | auto_init_schema=true (빈 DB) | 테이블 자동 생성 |
| Q022 | auto_init_schema=true (기존 DB) | IF NOT EXISTS |
| Q023 | hypertable 변환 | TimescaleDB |
| Q024 | 압축 정책 설정 | compress_after |
| Q025 | 보관 정책 | retention |

### Q-6. 데이터 무결성
| ID | 테스트 | 검증 |
|----|--------|------|
| Q026 | NULL timestamp 없음 | COUNT=0 |
| Q027 | NULL tag_id 없음 | COUNT=0 |
| Q028 | 값 컬럼 중 최소 1개 NOT NULL | 항상 |
| Q029 | quality_code 정상 | 0 또는 1 |
| Q030 | source_time 유효 | ISO 형식 |

### Q-7. 성능 측정
| ID | 테스트 | 검증 |
|----|--------|------|
| Q031 | 100건 처리 시간 | < 1초 |
| Q032 | 1000건 처리 시간 | < 5초 |
| Q033 | 10000건 처리 시간 | < 30초 |
| Q034 | COPY vs batch INSERT 비교 | COPY 더 빠름 |

### Q-8. 에러 시나리오
| ID | 테스트 | 검증 |
|----|--------|------|
| Q035 | RabbitMQ 다운 시 publisher | 재연결 대기 |
| Q036 | DB 다운 시 publisher | 버퍼 누적 |
| Q037 | 잘못된 메시지 포맷 | 스킵 + 로그 |
| Q038 | 빈 배치 메시지 | 무시 |
| Q039 | 압축 불일치 | decompress 에러 |
| Q040 | 암호화 키 불일치 | decrypt 에러 |

### Q-9. mock producer 검증
| ID | 테스트 | 검증 |
|----|--------|------|
| Q041 | scenario_plc1_data 30건 | routing plc.1.data |
| Q042 | scenario_plc2_data 10건 | routing plc.2.data |
| Q043 | scenario_plc1_alm 5건 | routing plc.1.data |
| Q044 | scenario_ble_data 60건 | routing ble.1.data |
| Q045 | 전체 105건 | 합산 |

### Q-10. 재시작 시나리오
| ID | 테스트 | 검증 |
|----|--------|------|
| Q046 | publisher 재시작 → latest TRUNCATE | 정상 |
| Q047 | publisher 재시작 → integrated 보존 | 데이터 유지 |
| Q048 | publisher 재시작 → master 재동기화 | UPSERT |
| Q049 | collector 재시작 → 연결 복구 | 정상 |
| Q050 | 전체 스택 재시작 | 데이터 무결성 |

---

## R. 에러 복구 / 재연결 (R001~R040)

| ID | 테스트 | 검증 |
|----|--------|------|
| R001 | PLC 연결 끊김 → 재연결 | 지수 백오프 |
| R002 | 백오프 초기값 | 1초 |
| R003 | 백오프 최대 | 10초 |
| R004 | 백오프 성공 리셋 | 1초로 |
| R005 | RabbitMQ 연결 끊김 | robust 재연결 |
| R006 | DB 연결 끊김 | pool 재생성 |
| R007 | BLE 어댑터 꺼짐 | scanner 재시도 |
| R008 | BLE 어댑터 켜짐 (복구) | 스캔 재개 |
| R009 | 네트워크 일시 단절 | 재연결 |
| R010 | 네트워크 장시간 단절 (1시간) | 재연결 시도 유지 |
| R011 | 부분 수집 실패 (일부 태그) | 성공분만 발행 |
| R012 | 전체 수집 실패 | quality=0 |
| R013 | 버퍼 가득 참 | threshold 경고 |
| R014 | 버퍼 오버플로우 | 오래된 데이터 드롭 |
| R015 | consecutive_loss 카운팅 | 정확한 카운트 |
| R016 | 캐시 초기화 (재연결 시) | stale 방지 |
| R017 | DB SELECT 1 헬스체크 | 주기적 |
| R018 | RabbitMQ heartbeat 실패 | 재연결 |
| R019 | 동시 다중 에러 | 우선순위 처리 |
| R020 | graceful shutdown 중 재연결 시도 | 중지 |
| R021~R040 | 다양한 복구 시나리오 | 20개 추가 |

---

## S. 성능 / 부하 (S001~S030)

| ID | 테스트 | 검증 |
|----|--------|------|
| S001 | 262 태그/초 (1PLC, 1초 주기) | 처리량 |
| S002 | 메모리 사용량 < 35MB | 정상 |
| S003 | 평균 레코드 크기 ~168바이트 | 측정 |
| S004 | 50개 BLE 센서 동시 수집 | 안정성 |
| S005 | 100개 BLE 센서 | 한계 테스트 |
| S006 | POSIOT 파싱 1000회/초 | CPU 부하 |
| S007 | zlib 압축 1000회/초 | CPU 부하 |
| S008 | JSON 직렬화 1000배치/초 | CPU 부하 |
| S009 | DB COPY 10000건/초 | 처리량 |
| S010 | RabbitMQ 발행 1000메시지/초 | 처리량 |
| S011 | 24시간 연속 운영 | 안정성 |
| S012 | 메모리 누수 (24시간) | 증가율 |
| S013 | 캐시 크기 (1000 MAC) | 메모리 |
| S014 | 이벤트 버스 처리량 | 병목 |
| S015 | 동시 10 PLC + 1 BLE | 리소스 |
| S016 | RPi 4 (ARM64) 성능 | 실환경 |
| S017 | RPi Zero (저사양) | 한계 |
| S018~S030 | 추가 성능 시나리오 | 13개 |

---

## 총계: 1,200개 테스트 케이스

| 레이어 | 단위 테스트 | 통합 테스트 | 성능 테스트 | 합계 |
|--------|-----------|-----------|-----------|------|
| BLE Profile (A+B) | 220 | - | - | 220 |
| BLE Scanner (C) | 80 | - | - | 80 |
| BLE Collector (D+E) | 120 | - | - | 120 |
| BLE Processor (F) | 60 | - | - | 60 |
| Scaling/Type (G+H) | 160 | - | - | 160 |
| Config (I) | 80 | - | - | 80 |
| on_change (J) | 60 | - | - | 60 |
| Serializer (K) | 40 | - | - | 40 |
| RabbitMQ (L) | 50 | - | - | 50 |
| MC Protocol (M) | 80 | - | - | 80 |
| Modbus (N) | 70 | - | - | 70 |
| Pipeline (O) | - | 30 | - | 30 |
| Docker (P) | - | 30 | - | 30 |
| E2E (Q) | - | 50 | - | 50 |
| Recovery (R) | - | 40 | - | 40 |
| Performance (S) | - | - | 30 | 30 |
| **합계** | **1,020** | **150** | **30** | **1,200** |
