"""
POSIOT LoRa 센서 프로파일
=========================

BLE POSIOT 센서와 동일한 데이터를 LoRa로 전송하는 end device용.
BLE advertisement의 manufacturer_data와 동일한 바이트 레이아웃을 사용합니다.

End Device Payload Layout (device_id 제외, data bytes만):
    [0:2]   temperature       (int16_le, /100, ℃)
    [2:4]   humidity          (int16_le, /100, %)
    [4:6]   pressure          (uint16_be, 특수공식, hPa)
    [6]     battery           (uint8, %)
    [7]     version/mode      (uint8, upper 4bit=version, lower 4bit=mode)
    [8:14]  accel_rms x/y/z   (3×uint16_be, /100, g)
    [14:16] velocity_rms      (uint16_le, /100, mm/s)
    [16:18] accel_rms_1k_5k   (uint16_be, /100, g)
    [18:20] vibration_peak    (uint16_be)
    [20]    harmony_cnt_low   (uint8)
    [21]    harmony_cnt_high  (uint8)
    [22:24] gravity_mag_xyz   (uint16_be)
    [24]    sound_db          (uint8, dB)
    [25:27] sound_peak        (uint16_be)
    [27:29] prob_temp         (int16_le, /100, ℃)

총 29 바이트. BLE와의 차이:
    - BLE: temperature가 company_id(uint16)에 인코딩
    - LoRa: temperature가 payload [0:2]에 포함 (int16_le)
    → 주소 오프셋이 2바이트씩 밀림

End device 구현 시 이 레이아웃을 참조하여 바이트 패킹하세요.
자세한 내용은 deploy/lora/ENDDEVICE_GUIDE.md 참고.
"""

import logging
import struct
from typing import Any, Dict, List

from .base import DeviceProfile

logger = logging.getLogger(__name__)


class PosiotLoRaProfile(DeviceProfile):
    """POSIOT 산업용 진동/환경 센서 (LoRa 전송)."""

    FIELDS = [
        'temperature', 'humidity', 'pressure', 'battery',
        'version', 'mode',
        'accel_rms_x', 'accel_rms_y', 'accel_rms_z',
        'velocity_rms_under_1k', 'accel_rms_1k_5k',
        'vibration_peak',
        'harmony_cnt_under_1k', 'harmony_cnt_1k_5k',
        'gravity_mag_xyz',
        'sound_db', 'sound_peak',
        'prob_temp',
    ]

    @property
    def name(self) -> str:
        return "posiot_lora"

    def get_field_names(self) -> List[str]:
        return list(self.FIELDS)

    def parse(self, data: bytes) -> Dict[str, Any]:
        """POSIOT LoRa payload 파싱."""
        result: Dict[str, Any] = {}

        if len(data) < 2:
            return result

        try:
            # temperature: [0:2] int16_le /100
            result['temperature'] = struct.unpack_from('<h', data, 0)[0] / 100.0

            if len(data) >= 4:
                # humidity: [2:4] int16_le /100
                result['humidity'] = struct.unpack_from('<h', data, 2)[0] / 100.0

            if len(data) >= 6:
                # pressure: [4:6] uint16_be, 특수 공식
                raw_pressure = struct.unpack_from('>H', data, 4)[0]
                result['pressure'] = round(
                    (raw_pressure * 255 + 50000) / 4096.0, 2
                )

            if len(data) >= 7:
                # battery: [6] uint8
                result['battery'] = struct.unpack_from('B', data, 6)[0]

            if len(data) >= 8:
                # version/mode: [7] uint8
                version_mode = struct.unpack_from('B', data, 7)[0]
                result['version'] = version_mode >> 4
                result['mode'] = version_mode & 0x0F

            if len(data) >= 14:
                # accel_rms x/y/z: [8:14] 3×uint16_be /100
                ax, ay, az = struct.unpack_from('>HHH', data, 8)
                result['accel_rms_x'] = ax / 100.0
                result['accel_rms_y'] = ay / 100.0
                result['accel_rms_z'] = az / 100.0

            if len(data) >= 16:
                # velocity_rms: [14:16] uint16_le /100
                result['velocity_rms_under_1k'] = (
                    struct.unpack_from('<H', data, 14)[0] / 100.0
                )

            if len(data) >= 18:
                # accel_rms_1k_5k: [16:18] uint16_be /100
                result['accel_rms_1k_5k'] = (
                    struct.unpack_from('>H', data, 16)[0] / 100.0
                )

            if len(data) >= 20:
                # vibration_peak: [18:20] uint16_be
                result['vibration_peak'] = struct.unpack_from('>H', data, 18)[0]

            if len(data) >= 21:
                result['harmony_cnt_under_1k'] = struct.unpack_from('B', data, 20)[0]

            if len(data) >= 22:
                result['harmony_cnt_1k_5k'] = struct.unpack_from('B', data, 21)[0]

            if len(data) >= 24:
                result['gravity_mag_xyz'] = struct.unpack_from('>H', data, 22)[0]

            if len(data) >= 25:
                result['sound_db'] = struct.unpack_from('B', data, 24)[0]

            if len(data) >= 27:
                result['sound_peak'] = struct.unpack_from('>H', data, 25)[0]

            if len(data) >= 29:
                # prob_temp: [27:29] int16_le /100
                result['prob_temp'] = struct.unpack_from('<h', data, 27)[0] / 100.0

        except struct.error as e:
            logger.warning(f"[PosiotLoRaProfile] Parsing error: {e}")

        return result
