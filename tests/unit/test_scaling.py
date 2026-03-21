"""G. 태그 스케일링 테스트 (G001~G100) + H. 데이터 타입 (H001~H060)"""
import math
import pytest
from src.core.interfaces import TagDefinition, DataType


def tag(data_type=DataType.UINT16, scale=1.0, offset=0.0, decimals=None,
        fmt="", bool_true_value=None, bool_false_value=None, bool_invert=False):
    return TagDefinition(
        tag_id=1, tag_name="test", address="D0",
        data_type=data_type, scale=scale, offset=offset,
        decimals=decimals, format=fmt,
        bool_true_value=bool_true_value, bool_false_value=bool_false_value,
        bool_invert=bool_invert,
    )


# ============================================================================
# G-1. 기본 스케일링 (raw * scale + offset)
# ============================================================================

class TestBasicScaling:
    """G001~G007"""

    def test_G001_identity(self):
        assert tag().apply_scaling(100) == 100

    def test_G002_scale_2(self):
        assert tag(scale=2.0).apply_scaling(100) == 200

    def test_G003_offset(self):
        assert tag(offset=50.0).apply_scaling(100) == 150

    def test_G004_scale_and_offset(self):
        assert tag(scale=0.5, offset=-10.0).apply_scaling(100) == 40.0

    def test_G005_zero_raw(self):
        assert tag(scale=5.0, offset=10.0).apply_scaling(0) == 10.0

    def test_G006_negative_raw(self):
        t = tag(data_type=DataType.INT16)
        assert t.apply_scaling(-100) == -100

    def test_G007_large_scale(self):
        result = tag(scale=0.001).apply_scaling(65535)
        assert result == pytest.approx(65.535, abs=0.001)


# ============================================================================
# G-2. decimals — 정수 타입 (÷10^decimals)
# ============================================================================

class TestDecimalsInteger:
    """G008~G015"""

    def test_G008_uint16_dec2(self):
        result = tag(decimals=2).apply_scaling(3061)
        assert result == pytest.approx(30.61, abs=0.001)

    def test_G009_uint16_dec1(self):
        result = tag(decimals=1).apply_scaling(3061)
        assert result == pytest.approx(306.1, abs=0.01)

    def test_G010_uint16_dec3(self):
        result = tag(decimals=3).apply_scaling(3061)
        assert result == pytest.approx(3.061, abs=0.0001)

    def test_G011_uint16_dec0(self):
        result = tag(decimals=0).apply_scaling(3061)
        assert result == 3061

    def test_G012_int32_dec2(self):
        result = tag(data_type=DataType.INT32, decimals=2).apply_scaling(100)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_G013_uint16_1_dec2(self):
        result = tag(decimals=2).apply_scaling(1)
        assert result == pytest.approx(0.01, abs=0.001)

    def test_G014_uint16_0_dec2(self):
        result = tag(decimals=2).apply_scaling(0)
        assert result == 0.0

    def test_G015_uint16_max_dec2(self):
        result = tag(decimals=2).apply_scaling(65535)
        assert result == pytest.approx(655.35, abs=0.01)


# ============================================================================
# G-3. decimals — float 타입 (round만)
# ============================================================================

class TestDecimalsFloat:
    """G016~G020"""

    def test_G016_float32_dec1(self):
        result = tag(data_type=DataType.FLOAT32, decimals=1).apply_scaling(69.123)
        assert result == pytest.approx(69.1, abs=0.01)

    def test_G017_float32_round_up(self):
        result = tag(data_type=DataType.FLOAT32, decimals=1).apply_scaling(69.156)
        assert result == pytest.approx(69.2, abs=0.01)

    def test_G019_float64_dec2(self):
        result = tag(data_type=DataType.FLOAT64, decimals=2).apply_scaling(3.14159)
        assert result == pytest.approx(3.14, abs=0.001)

    def test_G020_float64_dec4(self):
        result = tag(data_type=DataType.FLOAT64, decimals=4).apply_scaling(3.14159)
        assert result == pytest.approx(3.1416, abs=0.00001)


# ============================================================================
# G-4. NaN/Inf 검증
# ============================================================================

class TestNanInf:
    """G021~G024"""

    def test_G021_nan(self):
        assert tag().apply_scaling(float('nan')) is None

    def test_G022_inf(self):
        assert tag().apply_scaling(float('inf')) is None

    def test_G023_neg_inf(self):
        assert tag().apply_scaling(float('-inf')) is None

    def test_G024_normal_float(self):
        assert tag().apply_scaling(42.0) == 42.0


# ============================================================================
# G-5. None 처리
# ============================================================================

class TestNone:
    """G025~G026"""

    def test_G025_none(self):
        assert tag().apply_scaling(None) is None

    def test_G026_none_with_scale(self):
        assert tag(scale=2.0).apply_scaling(None) is None


# ============================================================================
# G-6. STRING 타입
# ============================================================================

class TestString:
    """G027~G029"""

    def test_G027_string(self):
        assert tag(data_type=DataType.STRING).apply_scaling("hello") == "hello"

    def test_G028_empty_string(self):
        assert tag(data_type=DataType.STRING).apply_scaling("") == ""

    def test_G029_numeric_string(self):
        assert tag(data_type=DataType.STRING).apply_scaling("123") == "123"


# ============================================================================
# G-7. BOOL 타입
# ============================================================================

class TestBool:
    """G030~G040"""

    def test_G030_true(self):
        assert tag(data_type=DataType.BOOL).apply_scaling(True) is True

    def test_G031_false(self):
        assert tag(data_type=DataType.BOOL).apply_scaling(False) is False

    def test_G032_invert_true(self):
        assert tag(data_type=DataType.BOOL, bool_invert=True).apply_scaling(True) is False

    def test_G033_invert_false(self):
        assert tag(data_type=DataType.BOOL, bool_invert=True).apply_scaling(False) is True

    def test_G034_int_1(self):
        assert tag(data_type=DataType.BOOL).apply_scaling(1) is True

    def test_G035_int_0(self):
        assert tag(data_type=DataType.BOOL).apply_scaling(0) is False

    def test_G036_true_value_5_ge_3(self):
        assert tag(data_type=DataType.BOOL, bool_true_value=3).apply_scaling(5) is True

    def test_G037_true_value_2_lt_3(self):
        assert tag(data_type=DataType.BOOL, bool_true_value=3).apply_scaling(2) is False

    def test_G038_false_value_5_gt_3(self):
        assert tag(data_type=DataType.BOOL, bool_false_value=3).apply_scaling(5) is True

    def test_G039_false_value_3_eq_3(self):
        assert tag(data_type=DataType.BOOL, bool_false_value=3).apply_scaling(3) is False

    def test_G040_invalid_string(self):
        assert tag(data_type=DataType.BOOL).apply_scaling("invalid") is False


# ============================================================================
# G-8. scale + decimals 조합
# ============================================================================

class TestScaleDecimalCombo:
    """G041~G045"""

    def test_G041_scale_offset_dec_int(self):
        # uint16: (100*2+10) / 100 = 2.10
        result = tag(scale=2.0, offset=10, decimals=2).apply_scaling(100)
        assert result == pytest.approx(2.10, abs=0.001)

    def test_G042_scale_offset_dec_float(self):
        # float32: round(100*2+10, 2) = 210.0
        result = tag(data_type=DataType.FLOAT32, scale=2.0, offset=10, decimals=2).apply_scaling(100)
        assert result == pytest.approx(210.0, abs=0.01)

    def test_G043_classic_plc(self):
        # uint16, decimals=2: 3061/100 = 30.61
        result = tag(decimals=2).apply_scaling(3061)
        assert result == pytest.approx(30.61, abs=0.001)

    def test_G044_float_no_divide(self):
        # float32, decimals=2: round(3061, 2) = 3061.0
        result = tag(data_type=DataType.FLOAT32, decimals=2).apply_scaling(3061)
        assert result == pytest.approx(3061.0, abs=0.01)


# ============================================================================
# H-1. byte_size
# ============================================================================

class TestByteSize:
    """H051~H060"""

    @pytest.mark.parametrize("dt,expected", [
        (DataType.BOOL, 1),
        (DataType.UINT8, 1), (DataType.INT8, 1), (DataType.BYTE, 1),
        (DataType.UINT16, 2), (DataType.INT16, 2), (DataType.WORD, 2),
        (DataType.UINT32, 4), (DataType.INT32, 4), (DataType.DWORD, 4), (DataType.FLOAT32, 4),
        (DataType.UINT64, 8), (DataType.INT64, 8), (DataType.LWORD, 8), (DataType.FLOAT64, 8),
        (DataType.STRING, 1),
    ])
    def test_byte_size(self, dt, expected):
        assert dt.byte_size == expected


# ============================================================================
# H-2. output_type
# ============================================================================

class TestOutputType:
    """H026~H035"""

    def test_H026_uint16_plain(self):
        assert tag().output_type == DataType.UINT16

    def test_H027_uint16_scale(self):
        assert tag(scale=2.0).output_type == DataType.FLOAT32

    def test_H028_uint16_decimals(self):
        assert tag(decimals=2).output_type == DataType.FLOAT32

    def test_H030_float32(self):
        assert tag(data_type=DataType.FLOAT32).output_type == DataType.FLOAT32

    def test_H031_bool(self):
        assert tag(data_type=DataType.BOOL).output_type == DataType.BOOL

    def test_H032_string(self):
        assert tag(data_type=DataType.STRING).output_type == DataType.STRING

    def test_H033_format_float32(self):
        assert tag(data_type=DataType.INT16, fmt="float32").output_type == DataType.FLOAT32
