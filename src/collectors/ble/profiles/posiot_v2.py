"""
POSIOT V2 환경/진동 센서 프로파일
==================================

V1(posiot)과 다른 AD 패킷 구조를 가진 POSIOT 센서.
GAP + UUID 헤더 후 manufacturer_data 시작.

Manufacturer Data Layout (company_id + data bytes):
    company_id 2바이트를 데이터 앞에 복원하여 full payload 구성 (22 bytes):

    [0]     mode/version      (uint8, upper 4bit=mode, lower 4bit=version)
    [1:3]   sound_db          (uint16_be, dB)
    [3:5]   velocity_rms      (uint16_be, mm/s)
    [5:7]   accel_x           (int16_be, mg)
    [7:9]   accel_y           (int16_be, mg)
    [9:11]  accel_z           (int16_be, mg)
    [11:13] temperature       (int16_le, /100, ℃)
    [13:15] humidity          (uint16_le, /100, %)
    [15:17] vib_fft_freq      (uint16_be, Hz)
    [17:19] sound_fft_freq    (uint16_be, Hz)
    [19:21] probe             (int16_le, /100, ℃ or A)
    [21]    battery           (uint8, %)
"""

import logging
import struct
from typing import Any, Dict, List

from .base import DeviceProfile

logger = logging.getLogger(__name__)


class PosiotV2Profile(DeviceProfile):
    """POSIOT V2 환경/진동 센서 (다른 AD 패킷 구조)."""

    FIELDS = [
        'mode', 'version',
        'sound_db',
        'velocity_rms',
        'accel_x', 'accel_y', 'accel_z',
        'temperature', 'humidity',
        'vib_fft_freq', 'sound_fft_freq',
        'probe',
        'battery',
    ]

    @property
    def name(self) -> str:
        return "posiot_v2"

    def get_field_names(self) -> List[str]:
        return list(self.FIELDS)

    def parse(self, company_id: int, data: bytes) -> Dict[str, Any]:
        """POSIOT V2 manufacturer_data 파싱.

        company_id 2바이트 + data를 합쳐 full payload로 복원 후 파싱.
        """
        # company_id(LE 2bytes) + data → full manufacturer payload
        full = struct.pack('<H', company_id & 0xFFFF) + data
        result: Dict[str, Any] = {}

        try:
            if len(full) < 1:
                return result

            # [0] mode/version: upper 4bit=mode, lower 4bit=version
            mv = full[0]
            result['mode'] = (mv >> 4) & 0x0F
            result['version'] = mv & 0x0F

            if len(full) >= 3:
                # [1:3] sound_db (uint16_be, dB)
                result['sound_db'] = struct.unpack_from('>H', full, 1)[0]

            if len(full) >= 5:
                # [3:5] velocity_rms (uint16_be, mm/s)
                result['velocity_rms'] = struct.unpack_from('>H', full, 3)[0]

            if len(full) >= 7:
                # [5:7] accel_x (int16_be, mg)
                result['accel_x'] = struct.unpack_from('>h', full, 5)[0]

            if len(full) >= 9:
                # [7:9] accel_y (int16_be, mg)
                result['accel_y'] = struct.unpack_from('>h', full, 7)[0]

            if len(full) >= 11:
                # [9:11] accel_z (int16_be, mg)
                result['accel_z'] = struct.unpack_from('>h', full, 9)[0]

            if len(full) >= 13:
                # [11:13] temperature (int16_le, /100 ℃)
                result['temperature'] = struct.unpack_from('<h', full, 11)[0] / 100.0

            if len(full) >= 15:
                # [13:15] humidity (uint16_le, /100 %)
                result['humidity'] = struct.unpack_from('<H', full, 13)[0] / 100.0

            if len(full) >= 17:
                # [15:17] vib_fft_freq (uint16_be, Hz)
                result['vib_fft_freq'] = struct.unpack_from('>H', full, 15)[0]

            if len(full) >= 19:
                # [17:19] sound_fft_freq (uint16_be, Hz)
                result['sound_fft_freq'] = struct.unpack_from('>H', full, 17)[0]

            if len(full) >= 21:
                # [19:21] probe (int16_le, /100)
                result['probe'] = struct.unpack_from('<h', full, 19)[0] / 100.0

            if len(full) >= 22:
                # [21] battery (uint8, %)
                result['battery'] = full[21]

        except struct.error as e:
            logger.warning(f"[PosiotV2Profile] Parsing error: {e}")

        return result
