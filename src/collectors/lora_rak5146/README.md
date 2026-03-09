# LoRa RAK5146 Collector Module

## 현재 상태: 초기 개발 완료 (미테스트)

코드 작성 완료, 실제 하드웨어 테스트 전 단계.

---

## 아키텍처

```
[End Device] ──LoRa P2P──→ [RAK5146 (mPCIe)] ──SPI──→ [HAL libloragw.so]
                                                              ↓
                                                    LoRaScanner (싱글톤)
                                                    lgw_receive() 폴링
                                                    device_id별 캐시
                                                              ↓
                                                    LoRaRak5146Collector
                                                    캐시에서 최신 패킷 조회
                                                              ↓
                                                    LoRaRak5146Processor
                                                    DeviceProfile로 파싱
                                                              ↓
                                                    RabbitMQ Publisher
```

BLE 모듈과 동일한 패턴:
| 역할 | BLE | LoRa |
|------|-----|------|
| 하드웨어 추상화 | Bleak (pip) | libloragw.so (ctypes) |
| 수신기 | BleScanner | LoRaScanner |
| 수신 방식 | callback (advertisement) | polling (lgw_receive, non-blocking) |
| 캐시 키 | MAC address | device_id (uint8) |
| 프로파일 | PosiotProfile | PosiotLoRaProfile |

## 파일 구조

```
src/collectors/lora_rak5146/
├── __init__.py              # 모듈 export
├── hal_wrapper.py           # libloragw.so ctypes 바인딩
│                              - LgwPktRx, LgwConfBoard 등 C struct 미러링
│                              - HalWrapper: configure → start → receive → stop
├── scanner.py               # LoRaScanner 싱글톤
│                              - HAL 래핑, 수신 루프, device_id별 캐시
│                              - 참조 카운팅 (acquire/release)
├── collector.py             # LoRaRak5146Collector (BaseCollector 상속)
│                              - _do_connect: scanner acquire
│                              - _do_collect: scanner.get_latest(device_id)
├── processor.py             # LoRaRak5146Processor (BaseProcessor 상속)
│                              - _parse_raw_data: ProfileRegistry로 파싱
│                              - tag.address → 프로파일 필드명 매핑
└── profiles/
    ├── __init__.py          # ProfileRegistry (자동 등록)
    ├── base.py              # DeviceProfile 추상 클래스
    │                          - BLE와 차이: parse(data) — company_id 없음
    └── posiot_lora.py       # POSIOT 호환 29바이트 레이아웃
```

## 패킷 포맷

```
[device_id (1B)] [sensor payload (29B for posiot_lora)]
```

- device_id: uint8 (1~255), end device 식별자
- Scanner가 첫 바이트를 device_id로 분리 → 나머지를 payload로 캐시
- Processor는 payload만 프로파일에 전달

## 설정 (YAML protocol.extra)

```yaml
extra:
  device_id: 1                        # 대상 end device ID (필수)
  device_profile: "posiot_lora"       # 프로파일 이름
  lib_path: "/app/lib/libloragw.so"   # HAL .so 경로
  spi_path: "/dev/spidev0.0"          # SPI 디바이스
  freq_hz: 923300000                  # 중심 주파수 (KR920)
  cache_ttl: 30.0                     # 캐시 만료 (초)
  poll_interval: 0.01                 # lgw_receive 폴링 주기 (초)
```

## Registry 등록

`src/core/registry.py`:
```python
'lora_rak5146': ('src.collectors.lora_rak5146', []),  # 의존성 없음 (ctypes)
'lora': ('src.collectors.lora_rak5146', []),           # alias
```

## 배포 파일

```
deploy/lora/
├── collector_lora.yaml      # YAML 설정 예시
├── tags_lora.csv            # 태그 정의 21개 (LORA + META)
├── docker-compose.yml       # Docker 배포 (--privileged for SPI)
└── ENDDEVICE_GUIDE.md       # End Device 펌웨어 가이드
                               - 패킷 포맷 상세
                               - LoRa RF 파라미터 (KR920)
                               - Arduino C++ 예시 코드
                               - libloragw.so 빌드 방법
                               - 디버깅/트러블슈팅
```

## 남은 작업

### 필수 (하드웨어 확보 후)
1. **libloragw.so 빌드 테스트** — RPi CM4/CM5에서 sx1302_hal 크로스 빌드
2. **HAL 통합 테스트** — 실제 RAK5146으로 lgw_start/receive 동작 확인
3. **ctypes struct 정렬 검증** — C struct ↔ Python ctypes 필드 순서/패딩 일치 확인
   - 특히 `LgwPktRx`의 필드 오프셋이 실제 HAL과 다를 수 있음
   - 방법: C 테스트 프로그램에서 sizeof/offsetof 출력 → Python과 비교
4. **End device 연동 테스트** — 실제 LoRa 송수신 확인

### 선택 (기능 확장)
- **다중 프로파일**: 다른 센서 타입용 프로파일 추가
- **device_id 2바이트 확장**: 255대 초과 시 uint16으로 변경
- **downlink 지원**: end device에 설정 명령 전송 (lgw_send)
- **Dockerfile**: LoRa 전용 Docker 이미지 빌드 스크립트

### 주의사항
- `hal_wrapper.py`의 ctypes struct 필드 순서는 sx1302_hal v2.1.0 (loragw_hal.h) 기준
  - HAL 버전 업데이트 시 struct 재검증 필요
- `lorawan_public = False` — raw LoRa P2P 모드 (LoRaWAN 아님)
- Docker 실행 시 `--privileged` 또는 `--device /dev/spidev0.0` 필수
