"""
PTS-2305BP 산업용 진동/환경 센서 프로파일
==========================================

POSIOT PTS-2305BP (구 pulley) 센서.
Manufacturer Data만 사용하는 단순 AD 구조.

Manufacturer Data Layout (company_id + data bytes):
    company_id: temperature (int16, /100, ℃)

    data bytes:
    [0:2]   humidity          (int16_le, /100, %)
    [2:4]   pressure          (uint16_be, 특수공식, hPa)
    [4]     battery           (uint8, %)
    [5]     version/mode      (uint8, upper 4bit=version, lower 4bit=mode)
    [6:12]  accel_rms x/y/z   (3×uint16_be, /100, g)
    [12:14] velocity_rms      (uint16_le, /100, mm/s)
    [14:16] accel_rms_1k_5k   (uint16_be, /100, g)
    [16:18] vibration_peak    (uint16_be)
    [18]    harmony_cnt_low   (uint8)
    [19]    harmony_cnt_high  (uint8)
    [20:22] gravity_mag_xyz   (uint16_be)
    [22]    sound_db          (uint8, dB)
    [23:25] sound_peak        (uint16_be)
    [25:27] prob_temp         (int16_le, /100, ℃)
"""

import logging
import struct
from typing import Any, Dict, List

from .base import DeviceProfile

logger = logging.getLogger(__name__)


class PosiotProfile(DeviceProfile):
    """PTS-2305BP 산업용 진동/환경 센서."""

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
        return "pts-2305bp"

    def get_field_names(self) -> List[str]:
        return list(self.FIELDS)

    def parse(self, company_id: int, data: bytes) -> Dict[str, Any]:
        """POSIOT manufacturer_data 파싱."""
        result: Dict[str, Any] = {}

        try:
            # temperature: company_id에서 추출 (int16 → /100)
            temp_bytes = struct.pack('<H', company_id & 0xFFFF)
            result['temperature'] = struct.unpack('<h', temp_bytes)[0] / 100.0
        except struct.error:
            pass

        if len(data) < 2:
            return result

        try:
            # humidity: [0:2] int16_le /100
            result['humidity'] = struct.unpack_from('<h', data, 0)[0] / 100.0

            if len(data) >= 4:
                # pressure: [2:4] uint16_be, 특수 공식
                raw_pressure = struct.unpack_from('>H', data, 2)[0]
                result['pressure'] = round(
                    (raw_pressure * 255 + 50000) / 4096.0, 2
                )

            if len(data) >= 5:
                # battery: [4] uint8
                result['battery'] = struct.unpack_from('B', data, 4)[0]

            if len(data) >= 6:
                # version/mode: [5] uint8
                version_mode = struct.unpack_from('B', data, 5)[0]
                result['version'] = version_mode >> 4
                result['mode'] = version_mode & 0x0F

            if len(data) >= 12:
                # accel_rms x/y/z: [6:12] 3×uint16_be /100
                ax, ay, az = struct.unpack_from('>HHH', data, 6)
                result['accel_rms_x'] = ax / 100.0
                result['accel_rms_y'] = ay / 100.0
                result['accel_rms_z'] = az / 100.0

            if len(data) >= 14:
                # velocity_rms: [12:14] uint16_le /100
                result['velocity_rms_under_1k'] = (
                    struct.unpack_from('<H', data, 12)[0] / 100.0
                )

            if len(data) >= 16:
                # accel_rms_1k_5k: [14:16] uint16_be /100
                result['accel_rms_1k_5k'] = (
                    struct.unpack_from('>H', data, 14)[0] / 100.0
                )

            if len(data) >= 18:
                # vibration_peak: [16:18] uint16_be
                result['vibration_peak'] = struct.unpack_from('>H', data, 16)[0]

            if len(data) >= 19:
                result['harmony_cnt_under_1k'] = struct.unpack_from('B', data, 18)[0]

            if len(data) >= 20:
                result['harmony_cnt_1k_5k'] = struct.unpack_from('B', data, 19)[0]

            if len(data) >= 22:
                result['gravity_mag_xyz'] = struct.unpack_from('>H', data, 20)[0]

            if len(data) >= 23:
                result['sound_db'] = struct.unpack_from('B', data, 22)[0]

            if len(data) >= 25:
                result['sound_peak'] = struct.unpack_from('>H', data, 23)[0]

            if len(data) >= 27:
                # prob_temp: [25:27] int16_le /100
                result['prob_temp'] = struct.unpack_from('<h', data, 25)[0] / 100.0

        except struct.error as e:
            logger.warning(f"[PosiotProfile] Parsing error: {e}")

        return result
