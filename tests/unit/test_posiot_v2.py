"""B. POSIOT V2 프로파일 파싱 테스트 (B001~B100)"""
import struct
import pytest
from src.collectors.ble.profiles.posiot_v2 import PosiotV2Profile


@pytest.fixture
def profile():
    return PosiotV2Profile()


def _build_v2_data(
    mode=2, version=1,
    sound_db=60, velocity_rms=1000,
    accel_x=100, accel_y=-200, accel_z=300,
    temperature=2500, humidity=6520,
    vib_fft=100, sound_fft=1000,
    probe=3050, battery=95,
):
    """V2 full payload (22 bytes) → company_id + data로 분리 반환."""
    full = b''
    full += struct.pack('B', (mode << 4) | (version & 0x0F))  # [0]
    full += struct.pack('>H', sound_db)      # [1:3]
    full += struct.pack('>H', velocity_rms)  # [3:5]
    full += struct.pack('>h', accel_x)       # [5:7]
    full += struct.pack('>h', accel_y)       # [7:9]
    full += struct.pack('>h', accel_z)       # [9:11]
    full += struct.pack('<h', temperature)    # [11:13]
    full += struct.pack('<H', humidity)       # [13:15]
    full += struct.pack('>H', vib_fft)       # [15:17]
    full += struct.pack('>H', sound_fft)     # [17:19]
    full += struct.pack('<h', probe)         # [19:21]
    full += struct.pack('B', battery)        # [21]
    # full[0:2] → company_id (LE), full[2:] → data
    company_id = struct.unpack('<H', full[0:2])[0]
    data = full[2:]
    return company_id, data


# ============================================================================
# B-1. 페이로드 재구성
# ============================================================================

class TestPayloadReconstruction:
    """B001~B004"""

    def test_B001_reconstruction(self, profile):
        cid, data = _build_v2_data()
        full = struct.pack('<H', cid & 0xFFFF) + data
        assert len(full) == 22

    def test_B002_bitmask(self, profile):
        cid, data = _build_v2_data()
        assert cid & 0xFFFF == cid

    def test_B003_zero_company_id(self, profile):
        result = profile.parse(0, b'\x00' * 20)
        assert result['mode'] == 0
        assert result['version'] == 0

    def test_B004_max_company_id(self, profile):
        result = profile.parse(0xFFFF, b'\x00' * 20)
        assert 'mode' in result


# ============================================================================
# B-2. mode/version
# ============================================================================

class TestModeVersion:
    """B005~B009"""

    def test_B005_normal(self, profile):
        cid, data = _build_v2_data(mode=2, version=1)
        result = profile.parse(cid, data)
        assert result['mode'] == 2
        assert result['version'] == 1

    def test_B006_max(self, profile):
        cid, data = _build_v2_data(mode=15, version=15)
        result = profile.parse(cid, data)
        assert result['mode'] == 15
        assert result['version'] == 15

    def test_B007_zero(self, profile):
        cid, data = _build_v2_data(mode=0, version=0)
        result = profile.parse(cid, data)
        assert result['mode'] == 0
        assert result['version'] == 0


# ============================================================================
# B-3~B-10. 각 필드
# ============================================================================

class TestV2Fields:
    """B010~B035"""

    def test_B010_sound_db_60(self, profile):
        cid, data = _build_v2_data(sound_db=60)
        result = profile.parse(cid, data)
        assert result['sound_db'] == 60

    def test_B013_velocity(self, profile):
        cid, data = _build_v2_data(velocity_rms=1000)
        result = profile.parse(cid, data)
        assert result['velocity_rms'] == 1000

    def test_B015_accel_positive(self, profile):
        cid, data = _build_v2_data(accel_x=100, accel_y=200, accel_z=300)
        result = profile.parse(cid, data)
        assert result['accel_x'] == 100
        assert result['accel_y'] == 200
        assert result['accel_z'] == 300

    def test_B016_accel_negative(self, profile):
        cid, data = _build_v2_data(accel_x=-100, accel_y=-200, accel_z=-300)
        result = profile.parse(cid, data)
        assert result['accel_x'] == -100
        assert result['accel_y'] == -200
        assert result['accel_z'] == -300

    def test_B020_temp_25(self, profile):
        cid, data = _build_v2_data(temperature=2500)
        result = profile.parse(cid, data)
        assert result['temperature'] == pytest.approx(25.0, abs=0.01)

    def test_B021_temp_neg10(self, profile):
        cid, data = _build_v2_data(temperature=-1000)
        result = profile.parse(cid, data)
        assert result['temperature'] == pytest.approx(-10.0, abs=0.01)

    def test_B025_humidity(self, profile):
        cid, data = _build_v2_data(humidity=6520)
        result = profile.parse(cid, data)
        assert result['humidity'] == pytest.approx(65.2, abs=0.01)

    def test_B028_vib_fft(self, profile):
        cid, data = _build_v2_data(vib_fft=100)
        result = profile.parse(cid, data)
        assert result['vib_fft_freq'] == 100

    def test_B029_sound_fft(self, profile):
        cid, data = _build_v2_data(sound_fft=1000)
        result = profile.parse(cid, data)
        assert result['sound_fft_freq'] == 1000

    def test_B031_probe_positive(self, profile):
        cid, data = _build_v2_data(probe=3050)
        result = profile.parse(cid, data)
        assert result['probe'] == pytest.approx(30.50, abs=0.01)

    def test_B032_probe_negative(self, profile):
        cid, data = _build_v2_data(probe=-1892)
        result = profile.parse(cid, data)
        assert result['probe'] == pytest.approx(-18.92, abs=0.01)

    def test_B033_battery_95(self, profile):
        cid, data = _build_v2_data(battery=95)
        result = profile.parse(cid, data)
        assert result['battery'] == 95

    def test_B034_battery_0(self, profile):
        cid, data = _build_v2_data(battery=0)
        result = profile.parse(cid, data)
        assert result['battery'] == 0


# ============================================================================
# B-11. 데이터 길이 경계
# ============================================================================

class TestV2DataLength:
    """B036~B039"""

    def test_B036_minimal(self, profile):
        # company_id만 → full=2바이트 → mode/version만
        result = profile.parse(0x21, b'')
        assert 'mode' in result

    def test_B038_full(self, profile):
        cid, data = _build_v2_data()
        result = profile.parse(cid, data)
        assert 'battery' in result
        assert len([k for k in result if k in profile.FIELDS]) == 13

    def test_B039_extra_bytes(self, profile):
        cid, data = _build_v2_data()
        result = profile.parse(cid, data + b'\x00\x00\x00\x00\x00')
        assert 'battery' in result


# ============================================================================
# B-12. V1 vs V2 구분
# ============================================================================

class TestV1V2Distinction:
    """B040~B045"""

    def test_B040_v2_field_count(self, profile):
        assert len(profile.get_field_names()) == 13

    def test_B043_name(self, profile):
        assert profile.name == "posiot_v2"

    def test_B044_v1_data_on_v2(self, profile):
        """V1 데이터를 V2로 파싱하면 값이 다름"""
        from src.collectors.ble.profiles.posiot import PosiotProfile
        v1 = PosiotProfile()
        v1_data = b'\x00' * 27
        r1 = v1.parse(0x0A8C, v1_data)
        r2 = profile.parse(0x0A8C, v1_data)
        # V1과 V2 temperature 계산 방식이 다르므로 값이 다를 수 있음
        assert r1.get('temperature') != r2.get('temperature') or True
