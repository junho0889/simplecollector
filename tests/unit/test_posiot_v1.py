"""A. POSIOT V1 프로파일 파싱 테스트 (A001~A120)"""
import struct
import pytest
from src.collectors.ble.profiles.posiot import PosiotProfile


@pytest.fixture
def profile():
    return PosiotProfile()


def _build_full_data(
    humidity=5000, pressure=40099, battery=100,
    version=1, mode=0,
    ax=100, ay=200, az=300,
    velocity=1000, accel_1k5k=500,
    vib_peak=500,
    harm_low=5, harm_high=10,
    gravity=1000,
    sound_db=60, sound_peak=500,
    prob_temp=2500,
):
    """27바이트 POSIOT V1 데이터 생성 헬퍼."""
    data = b''
    data += struct.pack('<h', humidity)       # [0:2]
    data += struct.pack('>H', pressure)       # [2:4]
    data += struct.pack('B', battery)         # [4]
    data += struct.pack('B', (version << 4) | (mode & 0x0F))  # [5]
    data += struct.pack('>HHH', ax, ay, az)  # [6:12]
    data += struct.pack('<H', velocity)       # [12:14]
    data += struct.pack('>H', accel_1k5k)    # [14:16]
    data += struct.pack('>H', vib_peak)      # [16:18]
    data += struct.pack('B', harm_low)        # [18]
    data += struct.pack('B', harm_high)       # [19]
    data += struct.pack('>H', gravity)        # [20:22]
    data += struct.pack('B', sound_db)        # [22]
    data += struct.pack('>H', sound_peak)     # [23:25]
    data += struct.pack('<h', prob_temp)      # [25:27]
    return data


# ============================================================================
# A-1. temperature (company_id → int16_le / 100.0)
# ============================================================================

class TestTemperature:
    """A001~A010"""

    def test_A001_positive_temp(self, profile):
        """정상 양수 온도 27.00"""
        result = profile.parse(0x0A8C, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(27.0, abs=0.01)

    def test_A002_negative_temp(self, profile):
        """정상 음수 온도 -30.00"""
        raw = struct.unpack('<H', struct.pack('<h', -3000))[0]
        result = profile.parse(raw, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(-30.0, abs=0.01)

    def test_A003_zero_temp(self, profile):
        result = profile.parse(0x0000, b'\x00' * 27)
        assert result['temperature'] == 0.0

    def test_A004_max_int16(self, profile):
        result = profile.parse(0x7FFF, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(327.67, abs=0.01)

    def test_A005_min_int16(self, profile):
        result = profile.parse(0x8000, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(-327.68, abs=0.01)

    def test_A006_small_positive(self, profile):
        result = profile.parse(0x0001, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(0.01, abs=0.001)

    def test_A007_small_negative(self, profile):
        result = profile.parse(0xFFFF, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(-0.01, abs=0.001)

    def test_A008_25_5(self, profile):
        result = profile.parse(0x09F6, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(25.50, abs=0.01)

    def test_A009_sensor_lower(self, profile):
        """센서 하한 -40도"""
        raw = struct.unpack('<H', struct.pack('<h', -4000))[0]
        result = profile.parse(raw, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(-40.0, abs=0.01)

    def test_A010_sensor_upper(self, profile):
        """센서 상한 85도"""
        result = profile.parse(0x2134, b'\x00' * 27)
        assert result['temperature'] == pytest.approx(85.0, abs=0.01)


# ============================================================================
# A-2. humidity (data[0:2] → int16_le / 100.0)
# ============================================================================

class TestHumidity:
    """A011~A016"""

    def test_A011_normal_50(self, profile):
        data = struct.pack('<h', 5000) + b'\x00' * 25
        result = profile.parse(0, data)
        assert result['humidity'] == pytest.approx(50.0, abs=0.01)

    def test_A012_zero(self, profile):
        data = struct.pack('<h', 0) + b'\x00' * 25
        result = profile.parse(0, data)
        assert result['humidity'] == 0.0

    def test_A013_100_percent(self, profile):
        data = struct.pack('<h', 10000) + b'\x00' * 25
        result = profile.parse(0, data)
        assert result['humidity'] == pytest.approx(100.0, abs=0.01)

    def test_A014_99_99(self, profile):
        data = struct.pack('<h', 9999) + b'\x00' * 25
        result = profile.parse(0, data)
        assert result['humidity'] == pytest.approx(99.99, abs=0.01)

    def test_A015_negative(self, profile):
        data = struct.pack('<h', -1) + b'\x00' * 25
        result = profile.parse(0, data)
        assert result['humidity'] == pytest.approx(-0.01, abs=0.001)

    def test_A016_too_short(self, profile):
        result = profile.parse(0, b'\x88')
        assert 'humidity' not in result


# ============================================================================
# A-3. pressure (data[2:4] → uint16_be, 특수공식)
# ============================================================================

class TestPressure:
    """A017~A021"""

    def test_A017_standard(self, profile):
        data = struct.pack('<h', 0) + struct.pack('>H', 40099) + b'\x00' * 23
        result = profile.parse(0, data)
        expected = round((40099 * 255 + 50000) / 4096.0, 2)
        assert result['pressure'] == pytest.approx(expected, abs=0.01)

    def test_A018_zero(self, profile):
        data = struct.pack('<h', 0) + struct.pack('>H', 0) + b'\x00' * 23
        result = profile.parse(0, data)
        expected = round((0 * 255 + 50000) / 4096.0, 2)
        assert result['pressure'] == pytest.approx(expected, abs=0.01)

    def test_A019_max(self, profile):
        data = struct.pack('<h', 0) + struct.pack('>H', 65535) + b'\x00' * 23
        result = profile.parse(0, data)
        expected = round((65535 * 255 + 50000) / 4096.0, 2)
        assert result['pressure'] == pytest.approx(expected, abs=0.01)

    def test_A021_too_short(self, profile):
        data = struct.pack('<h', 0) + b'\x00'  # 3바이트 (4 필요)
        result = profile.parse(0, data)
        assert 'pressure' not in result


# ============================================================================
# A-4~A-13. 나머지 필드 (전체 27바이트 빌드 테스트)
# ============================================================================

class TestFullParse:
    """A022~A050"""

    def test_A022_battery_100(self, profile):
        data = _build_full_data(battery=100)
        result = profile.parse(0, data)
        assert result['battery'] == 100

    def test_A023_battery_0(self, profile):
        data = _build_full_data(battery=0)
        result = profile.parse(0, data)
        assert result['battery'] == 0

    def test_A024_battery_255(self, profile):
        data = _build_full_data(battery=255)
        result = profile.parse(0, data)
        assert result['battery'] == 255

    def test_A026_version_mode(self, profile):
        data = _build_full_data(version=1, mode=0)
        result = profile.parse(0, data)
        assert result['version'] == 1
        assert result['mode'] == 0

    def test_A027_v2_mode3(self, profile):
        data = _build_full_data(version=2, mode=3)
        result = profile.parse(0, data)
        assert result['version'] == 2
        assert result['mode'] == 3

    def test_A028_v15_mode15(self, profile):
        data = _build_full_data(version=15, mode=15)
        result = profile.parse(0, data)
        assert result['version'] == 15
        assert result['mode'] == 15

    def test_A030_accel(self, profile):
        data = _build_full_data(ax=100, ay=200, az=300)
        result = profile.parse(0, data)
        assert result['accel_rms_x'] == pytest.approx(1.0, abs=0.01)
        assert result['accel_rms_y'] == pytest.approx(2.0, abs=0.01)
        assert result['accel_rms_z'] == pytest.approx(3.0, abs=0.01)

    def test_A031_accel_zero(self, profile):
        data = _build_full_data(ax=0, ay=0, az=0)
        result = profile.parse(0, data)
        assert result['accel_rms_x'] == 0.0
        assert result['accel_rms_y'] == 0.0
        assert result['accel_rms_z'] == 0.0

    def test_A032_accel_max(self, profile):
        data = _build_full_data(ax=65535, ay=65535, az=65535)
        result = profile.parse(0, data)
        assert result['accel_rms_x'] == pytest.approx(655.35, abs=0.01)

    def test_A035_velocity(self, profile):
        data = _build_full_data(velocity=1000)
        result = profile.parse(0, data)
        assert result['velocity_rms_under_1k'] == pytest.approx(10.0, abs=0.01)

    def test_A038_accel_1k5k(self, profile):
        data = _build_full_data(accel_1k5k=1000)
        result = profile.parse(0, data)
        assert result['accel_rms_1k_5k'] == pytest.approx(10.0, abs=0.01)

    def test_A040_vibration_peak(self, profile):
        data = _build_full_data(vib_peak=500)
        result = profile.parse(0, data)
        assert result['vibration_peak'] == 500

    def test_A042_harmony(self, profile):
        data = _build_full_data(harm_low=5, harm_high=10)
        result = profile.parse(0, data)
        assert result['harmony_cnt_under_1k'] == 5
        assert result['harmony_cnt_1k_5k'] == 10

    def test_A045_gravity(self, profile):
        data = _build_full_data(gravity=1000)
        result = profile.parse(0, data)
        assert result['gravity_mag_xyz'] == 1000

    def test_A046_sound(self, profile):
        data = _build_full_data(sound_db=60, sound_peak=500)
        result = profile.parse(0, data)
        assert result['sound_db'] == 60
        assert result['sound_peak'] == 500

    def test_A048_prob_temp_positive(self, profile):
        data = _build_full_data(prob_temp=2500)
        result = profile.parse(0, data)
        assert result['prob_temp'] == pytest.approx(25.0, abs=0.01)

    def test_A049_prob_temp_negative(self, profile):
        data = _build_full_data(prob_temp=-1000)
        result = profile.parse(0, data)
        assert result['prob_temp'] == pytest.approx(-10.0, abs=0.01)

    def test_A050_prob_temp_zero(self, profile):
        data = _build_full_data(prob_temp=0)
        result = profile.parse(0, data)
        assert result['prob_temp'] == 0.0


# ============================================================================
# A-14. 데이터 길이 경계 테스트
# ============================================================================

class TestDataLength:
    """A051~A062"""

    def test_A051_empty(self, profile):
        result = profile.parse(0x0A8C, b'')
        assert 'temperature' in result
        assert 'humidity' not in result

    def test_A052_1byte(self, profile):
        result = profile.parse(0, b'\x00')
        assert 'humidity' not in result

    def test_A053_2bytes(self, profile):
        result = profile.parse(0, b'\x00\x00')
        assert 'humidity' in result
        assert 'pressure' not in result

    def test_A054_5bytes(self, profile):
        result = profile.parse(0, b'\x00' * 5)
        assert 'battery' in result
        assert 'version' not in result

    def test_A055_6bytes(self, profile):
        result = profile.parse(0, b'\x00' * 6)
        assert 'version' in result
        assert 'mode' in result

    def test_A057_12bytes(self, profile):
        result = profile.parse(0, b'\x00' * 12)
        assert 'accel_rms_x' in result
        assert 'velocity_rms_under_1k' not in result

    def test_A058_14bytes(self, profile):
        result = profile.parse(0, b'\x00' * 14)
        assert 'velocity_rms_under_1k' in result

    def test_A061_27bytes_full(self, profile):
        data = _build_full_data()
        result = profile.parse(0, data)
        assert 'prob_temp' in result
        assert len([k for k in result if k in profile.FIELDS]) == 18

    def test_A062_30bytes_extra(self, profile):
        data = _build_full_data() + b'\x00\x00\x00'
        result = profile.parse(0, data)
        assert 'prob_temp' in result


# ============================================================================
# A-17. 프로파일 메타
# ============================================================================

class TestProfileMeta:
    """A095~A099"""

    def test_A095_field_count(self, profile):
        assert len(profile.get_field_names()) == 18

    def test_A096_name(self, profile):
        assert profile.name == "posiot"

    def test_A097_fields_match(self, profile):
        data = _build_full_data()
        result = profile.parse(0, data)
        for field in profile.get_field_names():
            assert field in result, f"Missing field: {field}"
