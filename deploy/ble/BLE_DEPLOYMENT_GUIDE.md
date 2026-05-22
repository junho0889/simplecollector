# BLE Collector 배포 가이드 — POSIOT 산업용 센서

## 1. 개요

BLE(Bluetooth Low Energy) 광고 패킷을 수신하여 POSIOT 산업용 진동/환경 센서 데이터를 수집하는 모듈.
기존 MC Protocol/Modbus 수집기와 동일한 simpleCollector 프레임워크 위에서 동작한다.

```
[POSIOT 센서] ──BLE 광고 브로드캐스트──> [BleScanner] → [BleCollector] → [BleProcessor] → [RabbitMQ]
                                                                                              ↓
                                                                            [collector-publisher] → MQTT / TimescaleDB
```

### PLC 수집과의 차이점

| 항목 | PLC (MC/Modbus) | BLE |
|------|-----------------|-----|
| 통신 방식 | Request/Response (폴링) | 센서가 광고 패킷을 브로드캐스트, 수신만 함 |
| 연결 | TCP 소켓 연결 유지 | 연결 없음 (connectionless) |
| 디바이스 식별 | IP:Port | MAC Address + Device Name |
| 데이터 소스 | PLC 메모리 레지스터 (D, M, X 등) | 광고 패킷의 manufacturer_data (27바이트) |
| 라이브러리 | 자체 구현 (socket) | Bleak (크로스플랫폼 BLE) |

## 2. 센서 디바이스 식별 방식

BLE 센서는 연결 없이 **광고 패킷을 주기적으로 브로드캐스트**한다.
우리는 요청하지 않고, **수동적으로 듣고(listen) 있다가 캐치**하는 구조.

### 식별 레이어

1. **Device Name 필터** — BLE 광고의 LocalName이 `"POSIOT"`을 포함하는지 확인
2. **MAC Address** — YAML에 등록된 특정 MAC 주소만 처리
3. **Device Profile** — `"posiot"` 프로파일이 manufacturer_data 바이트 구조를 파싱

```yaml
# collector_ble1.yaml
extra:
  mac_address: "D4:BB:36:61:27:AC"    # 센서 MAC (필수)
  device_name_filter: "POSIOT"         # 이름 필터 (선택)
  device_profile: "posiot"             # 파싱 프로파일 (기본값: posiot)
```

> 자동 탐색(auto-discovery) 아님. 새 센서 추가 시 MAC 주소를 알아서 YAML에 등록해야 함.

## 3. POSIOT 패킷 구조 (Manufacturer Data)

BLE 광고의 `manufacturer_data`는 `{company_id: bytes}` 형태이며, POSIOT 센서는 아래 레이아웃을 따른다.

### company_id → 온도

```
company_id (uint16) → int16_le로 재해석 → /100 → ℃
예: company_id=2500 → 25.00℃
```

### data bytes (27바이트)

| 오프셋 | 필드 | 타입 | 단위 | 변환 |
|--------|------|------|------|------|
| [0:2] | humidity | int16_le | % | /100 |
| [2:4] | pressure | uint16_be | hPa | (raw*255+50000)/4096 |
| [4] | battery | uint8 | % | 그대로 |
| [5] | version/mode | uint8 | - | upper 4bit=version, lower 4bit=mode |
| [6:8] | accel_rms_x | uint16_be | g | /100 |
| [8:10] | accel_rms_y | uint16_be | g | /100 |
| [10:12] | accel_rms_z | uint16_be | g | /100 |
| [12:14] | velocity_rms_under_1k | uint16_le | mm/s | /100 |
| [14:16] | accel_rms_1k_5k | uint16_be | g | /100 |
| [16:18] | vibration_peak | uint16_be | - | 그대로 |
| [18] | harmony_cnt_under_1k | uint8 | - | 그대로 |
| [19] | harmony_cnt_1k_5k | uint8 | - | 그대로 |
| [20:22] | gravity_mag_xyz | uint16_be | - | 그대로 |
| [22] | sound_db | uint8 | dB | 그대로 |
| [23:25] | sound_peak | uint16_be | - | 그대로 |
| [25:27] | prob_temp | int16_le | ℃ | /100 |

> 파싱 구현: `src/collectors/ble/profiles/posiot.py`

## 4. 태그 CSV 매핑

`tags_ble_posiot.csv`에서 `address` 컬럼이 프로파일 파싱 필드명과 1:1 매칭된다.
PLC의 메모리 주소(D200, M100) 대신 **필드명 문자열**을 사용.

| tag_id | tag_name | address (=필드명) | data_type | group | 설명 |
|--------|----------|-------------------|-----------|-------|------|
| 1 | temperature | temperature | float32 | ble_env | 온도 |
| 2 | humidity | humidity | float32 | ble_env | 습도 |
| 3 | pressure | pressure | float32 | ble_env | 기압 |
| 4 | battery | battery | uint16 | ble_env | 배터리잔량 |
| 5 | prob_temp | prob_temp | float32 | ble_env | 프로브온도 |
| 6 | accel_rms_x | accel_rms_x | float32 | ble_vib | 가속도RMS X |
| 7 | accel_rms_y | accel_rms_y | float32 | ble_vib | 가속도RMS Y |
| 8 | accel_rms_z | accel_rms_z | float32 | ble_vib | 가속도RMS Z |
| 9 | velocity_rms | velocity_rms_under_1k | float32 | ble_vib | 속도RMS |
| 10 | accel_rms_1k5k | accel_rms_1k_5k | float32 | ble_vib | 가속도RMS 1-5kHz |
| 11 | vibration_peak | vibration_peak | uint16 | ble_vib | 진동피크 |
| 12 | harmony_cnt_low | harmony_cnt_under_1k | uint16 | ble_vib | 고조파수 <1kHz |
| 13 | harmony_cnt_high | harmony_cnt_1k_5k | uint16 | ble_vib | 고조파수 1-5kHz |
| 14 | gravity_mag | gravity_mag_xyz | uint16 | ble_vib | 중력크기 |
| 15 | sound_db | sound_db | uint16 | ble_vib | 소음 |
| 16 | sound_peak | sound_peak | uint16 | ble_vib | 소음피크 |
| 17 | rssi | rssi | int16 | ble_env | 신호강도 (META) |
| 18 | version | version | uint16 | ble_env | 펌웨어버전 |
| 19 | mode | mode | uint16 | ble_env | 동작모드 |
| 20 | battery_low | battery | bool | ble_alm | 배터리부족알람 |

- **ADV 필드**: manufacturer_data에서 프로파일이 파싱한 값
- **META 필드**: BLE 메타데이터 (rssi, device_name, mac_address)

## 5. Collection Groups

| 그룹 | 주기 | 모드 | 태그 | 용도 |
|------|------|------|------|------|
| `ble_env` | 5초 | polling | 온도, 습도, 기압, 배터리, RSSI 등 | 환경 모니터링 |
| `ble_vib` | 5초 | polling | 가속도, 진동, 소음 등 | 진동 분석 |
| `ble_alm` | 5초 | on_change | battery_low | 알람 |

## 6. 소스 구조

```
src/collectors/ble/
├── __init__.py          # BleCollector, BleProcessor export
├── collector.py         # BleCollector — MAC 기반 광고 패킷 수신
├── processor.py         # BleProcessor — 프로파일 기반 파싱 → 태그 매핑
├── scanner.py           # BleScanner — Bleak 싱글톤 스캐너 (레퍼런스 카운팅)
└── profiles/
    ├── __init__.py      # ProfileRegistry (프로파일 자동 등록)
    ├── base.py          # DeviceProfile ABC (parse, get_field_names)
    └── posiot.py        # PosiotProfile — 27바이트 패킷 파싱
```

### 주요 클래스

| 클래스 | 역할 |
|--------|------|
| `BleScanner` | 싱글톤. Bleak으로 BLE 광고를 연속 수신, MAC별 캐시 관리 |
| `BleCollector` | BaseCollector 구현. 특정 MAC의 최신 광고 데이터 반환 |
| `BleProcessor` | BaseProcessor 구현. 프로파일로 파싱 후 태그에 매핑 |
| `PosiotProfile` | DeviceProfile 구현. struct.unpack으로 27바이트 디코딩 |
| `ProfileRegistry` | 프로파일 이름→인스턴스 딕셔너리. 새 센서 추가 시 여기 등록 |

### 데이터 흐름

```
BLE 광고 수신 (Bleak callback)
    ↓
BleScanner._adv_cache[MAC] 저장 (TTL 관리)
    ↓
BleCollector._do_collect() → scanner.get_latest(MAC)
    ↓ CollectedData(metadata={manufacturer_data, rssi, ...})
BleProcessor._parse_raw_data()
    ↓ ProfileRegistry.get("posiot").parse(company_id, bytes)
    ↓ → {temperature: 25.0, humidity: 65.3, ...}
태그 매핑 (address 컬럼 → 파싱 필드명)
    ↓
Publisher → RabbitMQ
```

## 7. 배포 환경

### Windows (개발/테스트)

Docker에서는 BLE 어댑터 접근 불가 (WSL2/Hyper-V VM 제약).
**collector는 반드시 네이티브(Windows)로 실행.**

```bash
# 1. 인프라만 Docker로
docker compose up -d

# 2. BLE collector는 로컬에서
python -m src.main -c deploy/ble/collector_ble1.yaml

# 3. MQTT 수신 확인
docker exec -it ble-mosquitto mosquitto_sub -t '#' -v
```

### Linux / Raspberry Pi (운영)

```bash
# BlueZ 필요 (보통 기본 설치됨)
sudo apt install bluez

# Bluetooth 상태 확인
sudo systemctl status bluetooth
sudo hciconfig hci0 up
```

#### Docker에서 BLE 하드웨어 접근

라즈베리파이(Linux)에서는 Docker 컨테이너 안에서도 BLE 사용이 가능하지만,
Bleak이 BlueZ의 D-Bus 인터페이스를 사용하므로 아래 설정이 **필수**:

```yaml
services:
  ble-collector:
    image: neuroforge_collector_ble:latest
    network_mode: host                          # BLE는 호스트 Bluetooth 스택 직접 사용
    privileged: true                            # BLE 어댑터 접근 권한
    volumes:
      - /var/run/dbus:/var/run/dbus:ro          # BlueZ D-Bus 소켓
      - ./collector_ble1.yaml:/app/config/collector.yaml:ro
      - ./tags_ble_posiot.csv:/app/config/tags.csv:ro
    command: ["--config", "/app/config/collector.yaml"]
    restart: unless-stopped
```

| 설정 | 이유 |
|------|------|
| `network_mode: host` | BLE는 TCP/IP가 아님. 호스트의 Bluetooth 스택을 직접 사용해야 함 |
| `/var/run/dbus` 마운트 | Bleak → BlueZ 간 D-Bus IPC 통신 경로 |
| `privileged: true` | BLE 어댑터(`/dev/hci0`) 접근 권한. 보안이 중요하면 아래 대체 가능 |

#### privileged 대신 최소 권한 설정 (선택)

```yaml
services:
  ble-collector:
    # privileged: true 대신
    devices:
      - /dev/hci0:/dev/hci0
    cap_add:
      - NET_ADMIN                # BLE 어댑터 제어
      - NET_RAW                  # raw 소켓 접근
    volumes:
      - /var/run/dbus:/var/run/dbus:ro
```

> **Windows Docker에서는 불가능** — WSL2/Hyper-V VM이 호스트 Bluetooth를 패스스루하지 못함.
> Windows에서는 반드시 collector를 네이티브로 실행해야 한다 (위 Windows 섹션 참고).

### 의존성

```
bleak          # BLE 스캐닝 (pip install bleak)
aio-pika       # RabbitMQ (기존 의존성)
```

## 8. 설정 파일 목록

| 파일 | 용도 |
|------|------|
| `collector_ble1.yaml` | BLE collector 설정 (MAC, 프로파일, 그룹) |
| `tags_ble_posiot.csv` | 태그 정의 (20개 태그) |
| `publisher_ble.yaml` | publisher 설정 (MQTT 전용, DB 비활성) |
| `docker-compose.yml` | 인프라 (RabbitMQ + Mosquitto + Node-RED) |
| `mosquitto.conf` | Mosquitto 설정 |
| `nodered_ble_debug.json` | Node-RED MQTT 디버그 플로우 |
| `test_mqtt_bridge.py` | MQTT 브릿지 테스트 스크립트 |

## 9. 새 센서 타입 추가 방법

1. `src/collectors/ble/profiles/my_sensor.py` 작성
   - `DeviceProfile` 상속, `parse()` / `get_field_names()` 구현
2. `profiles/__init__.py`에서 등록: `ProfileRegistry.register(MySensorProfile())`
3. YAML에서 지정: `device_profile: "my_sensor"`
4. CSV에 해당 필드명으로 태그 정의

## 10. 참고

- 원본 코드: `D:\2.Project\1.NASDAQ\SensorProcessServer` (data_collector/main.py)
- POSIOT 센서 사양서 확인 필요 시 패킷 레이아웃은 위 표 참조
- BLE 광고 주기는 센서 펌웨어 설정에 의존 (보통 1~10초)
- `duplicate_filter_s: 4.0` — 동일 MAC에서 4초 이내 중복 광고 무시
- `cache_ttl: 30.0` — 30초간 광고가 없으면 디바이스 오프라인 판정
