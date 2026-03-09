# LoRa End Device 연동 가이드

## 개요

```
[End Device] ──LoRa P2P──→ [CM4 + RAK5146] ──SPI──→ [simpleCollector] ──→ [RabbitMQ]
```

End device는 센서 데이터를 LoRa 무선으로 전송하고, CM4 게이트웨이의 RAK5146 모듈이 수신합니다.
이 문서는 end device 펌웨어 개발자를 위한 **페이로드 규격** 및 **LoRa 파라미터 설정** 가이드입니다.

---

## 1. 패킷 포맷

### 1.1 전체 구조

```
┌──────────┬─────────────────────────────────┐
│ Byte 0   │ Byte 1 ~ N                      │
│ device_id│ sensor payload                  │
│ (uint8)  │ (프로파일별 바이트 레이아웃)        │
└──────────┴─────────────────────────────────┘
```

| 필드 | 오프셋 | 크기 | 타입 | 설명 |
|------|--------|------|------|------|
| `device_id` | 0 | 1 byte | uint8 | End device 고유 식별자 (1~255) |
| `payload` | 1 ~ N | 가변 | - | 센서 데이터 (프로파일에 따라 다름) |

- `device_id`는 게이트웨이에서 어떤 end device의 데이터인지 구분하는 용도입니다.
- 같은 게이트웨이에 연결되는 end device끼리 ID가 겹치면 안 됩니다.
- ID `0`은 예약됨 (사용 금지).

### 1.2 POSIOT 호환 페이로드 (posiot_lora 프로파일)

BLE POSIOT 센서와 동일한 데이터를 LoRa로 전송하는 경우의 바이트 레이아웃입니다.

| 오프셋 | 크기 | 타입 | 스케일 | 필드명 | 단위 | 설명 |
|--------|------|------|--------|--------|------|------|
| 0 | 2 | int16_le | ÷100 | temperature | ℃ | 온도 (예: 2534 → 25.34℃) |
| 2 | 2 | int16_le | ÷100 | humidity | % | 습도 |
| 4 | 2 | uint16_be | 특수공식 | pressure | hPa | 기압 |
| 6 | 1 | uint8 | ×1 | battery | % | 배터리 잔량 |
| 7 | 1 | uint8 | - | version_mode | - | 상위4bit=버전, 하위4bit=모드 |
| 8 | 2 | uint16_be | ÷100 | accel_rms_x | g | 가속도 RMS X축 |
| 10 | 2 | uint16_be | ÷100 | accel_rms_y | g | 가속도 RMS Y축 |
| 12 | 2 | uint16_be | ÷100 | accel_rms_z | g | 가속도 RMS Z축 |
| 14 | 2 | uint16_le | ÷100 | velocity_rms | mm/s | 속도 RMS (<1kHz) |
| 16 | 2 | uint16_be | ÷100 | accel_rms_1k_5k | g | 가속도 RMS 1-5kHz |
| 18 | 2 | uint16_be | ×1 | vibration_peak | - | 진동 피크 |
| 20 | 1 | uint8 | ×1 | harmony_cnt_low | - | 고조파 카운트 (<1kHz) |
| 21 | 1 | uint8 | ×1 | harmony_cnt_high | - | 고조파 카운트 (1-5kHz) |
| 22 | 2 | uint16_be | ×1 | gravity_mag_xyz | - | 중력 크기 |
| 24 | 1 | uint8 | ×1 | sound_db | dB | 소음 (데시벨) |
| 25 | 2 | uint16_be | ×1 | sound_peak | - | 소음 피크 |
| 27 | 2 | int16_le | ÷100 | prob_temp | ℃ | 프로브 온도 |

**총 페이로드 크기: 29 bytes** (device_id 제외)
**전체 패킷 크기: 30 bytes** (device_id 포함)

#### 기압 특수 공식
```
pressure_hPa = (raw_uint16 * 255 + 50000) / 4096.0
```

### 1.3 커스텀 프로파일

다른 센서를 사용하는 경우, 새 프로파일을 추가할 수 있습니다:

1. `src/collectors/lora_rak5146/profiles/` 에 새 파일 생성
2. `DeviceProfile` 상속, `parse()` 구현
3. `profiles/__init__.py`에서 `ProfileRegistry.register()` 호출
4. YAML의 `device_profile` 값을 새 프로파일 이름으로 변경

---

## 2. LoRa RF 파라미터

Gateway(RAK5146)와 end device의 LoRa 파라미터가 일치해야 통신 가능합니다.

### 2.1 권장 설정 (KR920 대역)

| 파라미터 | 값 | 비고 |
|----------|-----|------|
| **주파수** | 922.1 ~ 923.3 MHz | KR920 ISM 대역 |
| **Spreading Factor** | SF7 ~ SF12 | 거리/속도 트레이드오프 |
| **Bandwidth** | 125 kHz | 표준 |
| **Coding Rate** | 4/5 | 표준 |
| **Preamble** | 8 symbols | 표준 |
| **Sync Word** | 0x12 | Private network (LoRaWAN=0x34) |
| **CRC** | 활성화 | 필수 (CRC 실패 시 패킷 무시) |
| **TX Power** | ≤14 dBm | KR920 법적 한도 |

### 2.2 SF 선택 가이드

| SF | 도달 거리 | 데이터 속도 | Air Time (30B) | 추천 |
|----|-----------|-------------|----------------|------|
| SF7 | ~2 km | ~5.5 kbps | ~56 ms | 근거리, 빠른 주기 |
| SF9 | ~5 km | ~1.8 kbps | ~164 ms | 일반 |
| SF12 | ~10+ km | ~0.3 kbps | ~1.3 s | 원거리, 느린 주기 |

- **30 바이트 기준** air time입니다.
- 짧은 전송 주기가 필요하면 SF7~SF9 권장.
- 배터리 수명을 위해 가능한 낮은 SF 사용.

### 2.3 전송 주기

| 시나리오 | 권장 주기 | 비고 |
|----------|-----------|------|
| 실시간 모니터링 | 5~10초 | 배터리 소모 큼 |
| 일반 수집 | 30~60초 | 권장 |
| 저전력 | 5~10분 | 배터리 최적화 |

게이트웨이의 `cache_ttl` 설정보다 짧은 주기로 전송해야 합니다. (기본 30초)

---

## 3. End Device 펌웨어 예시 (Arduino/C++)

### 3.1 패킷 패킹 함수

```cpp
#include <stdint.h>
#include <string.h>

// POSIOT 호환 패킷 생성
// 반환값: 전체 패킷 길이 (device_id + payload)
uint8_t build_posiot_packet(
    uint8_t* buffer,        // 출력 버퍼 (최소 30바이트)
    uint8_t device_id,      // 디바이스 ID (1~255)
    int16_t temperature,    // 온도 × 100 (예: 2534 = 25.34℃)
    int16_t humidity,       // 습도 × 100
    uint16_t pressure_raw,  // 기압 raw (특수공식용)
    uint8_t battery,        // 배터리 %
    uint8_t version,        // 펌웨어 버전 (0~15)
    uint8_t mode,           // 동작 모드 (0~15)
    uint16_t accel_x,       // 가속도 X × 100
    uint16_t accel_y,       // 가속도 Y × 100
    uint16_t accel_z,       // 가속도 Z × 100
    uint16_t velocity_rms,  // 속도 RMS × 100
    uint16_t accel_1k5k,    // 가속도 1-5kHz × 100
    uint16_t vib_peak,      // 진동 피크
    uint8_t harm_low,       // 고조파 <1kHz
    uint8_t harm_high,      // 고조파 1-5kHz
    uint16_t gravity_mag,   // 중력 크기
    uint8_t sound_db,       // 소음 dB
    uint16_t sound_peak,    // 소음 피크
    int16_t prob_temp       // 프로브 온도 × 100
) {
    uint8_t idx = 0;

    // device_id
    buffer[idx++] = device_id;

    // temperature: int16_le
    buffer[idx++] = temperature & 0xFF;
    buffer[idx++] = (temperature >> 8) & 0xFF;

    // humidity: int16_le
    buffer[idx++] = humidity & 0xFF;
    buffer[idx++] = (humidity >> 8) & 0xFF;

    // pressure: uint16_be
    buffer[idx++] = (pressure_raw >> 8) & 0xFF;
    buffer[idx++] = pressure_raw & 0xFF;

    // battery: uint8
    buffer[idx++] = battery;

    // version_mode: uint8 (upper 4 = version, lower 4 = mode)
    buffer[idx++] = (version << 4) | (mode & 0x0F);

    // accel_rms x/y/z: uint16_be × 3
    buffer[idx++] = (accel_x >> 8) & 0xFF;
    buffer[idx++] = accel_x & 0xFF;
    buffer[idx++] = (accel_y >> 8) & 0xFF;
    buffer[idx++] = accel_y & 0xFF;
    buffer[idx++] = (accel_z >> 8) & 0xFF;
    buffer[idx++] = accel_z & 0xFF;

    // velocity_rms: uint16_le
    buffer[idx++] = velocity_rms & 0xFF;
    buffer[idx++] = (velocity_rms >> 8) & 0xFF;

    // accel_rms_1k_5k: uint16_be
    buffer[idx++] = (accel_1k5k >> 8) & 0xFF;
    buffer[idx++] = accel_1k5k & 0xFF;

    // vibration_peak: uint16_be
    buffer[idx++] = (vib_peak >> 8) & 0xFF;
    buffer[idx++] = vib_peak & 0xFF;

    // harmony counts: uint8 × 2
    buffer[idx++] = harm_low;
    buffer[idx++] = harm_high;

    // gravity_mag: uint16_be
    buffer[idx++] = (gravity_mag >> 8) & 0xFF;
    buffer[idx++] = gravity_mag & 0xFF;

    // sound_db: uint8
    buffer[idx++] = sound_db;

    // sound_peak: uint16_be
    buffer[idx++] = (sound_peak >> 8) & 0xFF;
    buffer[idx++] = sound_peak & 0xFF;

    // prob_temp: int16_le
    buffer[idx++] = prob_temp & 0xFF;
    buffer[idx++] = (prob_temp >> 8) & 0xFF;

    return idx;  // 30 bytes
}
```

### 3.2 전송 예시 (SX1276/SX1262 기반)

```cpp
#include <SPI.h>
#include <LoRa.h>   // arduino-LoRa 또는 RadioLib

#define DEVICE_ID     1
#define FREQ_HZ       923300000   // 923.3 MHz
#define TX_POWER      14          // dBm
#define SPREADING_F   7           // SF7
#define BANDWIDTH     125000      // 125 kHz
#define SYNC_WORD     0x12        // Private network

void setup() {
    LoRa.begin(FREQ_HZ);
    LoRa.setTxPower(TX_POWER);
    LoRa.setSpreadingFactor(SPREADING_F);
    LoRa.setSignalBandwidth(BANDWIDTH);
    LoRa.setSyncWord(SYNC_WORD);
    LoRa.enableCrc();
}

void loop() {
    // 센서 데이터 읽기
    int16_t temp = read_temperature() * 100;  // 25.34℃ → 2534
    int16_t humi = read_humidity() * 100;
    // ... 나머지 센서 데이터 ...

    // 패킷 생성
    uint8_t packet[30];
    uint8_t len = build_posiot_packet(
        packet, DEVICE_ID,
        temp, humi, pressure_raw, battery,
        1, 0,  // version=1, mode=0
        accel_x, accel_y, accel_z,
        vel_rms, accel_1k5k, vib_peak,
        harm_low, harm_high, grav_mag,
        sound, sound_pk, probe_temp
    );

    // LoRa 전송
    LoRa.beginPacket();
    LoRa.write(packet, len);
    LoRa.endPacket();

    delay(5000);  // 5초 주기
}
```

---

## 4. Gateway 설정

### 4.1 libloragw.so 빌드 (ARM64)

```bash
# RPi에서 직접 빌드
git clone https://github.com/Lora-net/sx1302_hal.git
cd sx1302_hal
make clean && make

# 빌드된 라이브러리 복사
cp libloragw/libloragw.so /path/to/deploy/lora/lib/
```

### 4.2 SPI 활성화 (Raspberry Pi)

```bash
sudo raspi-config
# → Interface Options → SPI → Enable

# 확인
ls -la /dev/spidev0.*
# /dev/spidev0.0  ← 이 디바이스 사용
```

### 4.3 RAK5146 핀 연결 확인

RAK5146 mPCIe 모듈은 CM4 IO Board의 mPCIe 슬롯에 장착합니다.
SPI 핀이 자동 매핑되므로 별도 배선 불필요.

| RAK5146 | CM4 | 설명 |
|---------|-----|------|
| SPI_CLK | GPIO 11 | SPI Clock |
| SPI_MISO | GPIO 9 | SPI Master In |
| SPI_MOSI | GPIO 10 | SPI Master Out |
| SPI_CS | GPIO 8 | Chip Select |
| RESET | GPIO 17 | 모듈 리셋 (옵션) |

### 4.4 Docker 실행

```bash
cd deploy/lora
docker-compose up -d
```

---

## 5. 디버깅

### 5.1 수신 확인 (로그)

```bash
docker logs -f collector_lora
```

정상 수신 시:
```
[LoRaScanner] Device 1: 29B, RSSI=-45.0, SNR=10.5
```

### 5.2 HAL 테스트 (라이브러리 단독)

```bash
# sx1302_hal 빌드 후 테스트 프로그램으로 확인
cd sx1302_hal
./libloragw/test_loragw_hal_rx
```

### 5.3 Troubleshooting

| 증상 | 원인 | 해결 |
|------|------|------|
| `HAL library not found` | libloragw.so 경로 오류 | docker-compose volume 확인 |
| `lgw_start failed` | SPI 접근 불가 | `--privileged` 또는 `--device` 확인 |
| 패킷 수신 안 됨 | 주파수/SF/Sync Word 불일치 | end device RF 파라미터 확인 |
| CRC error 다수 | 신호 약하거나 간섭 | 안테나 확인, SF 올리기 |
| 캐시 만료 (None) | 전송 주기 > cache_ttl | cache_ttl 늘리거나 전송 주기 줄이기 |
