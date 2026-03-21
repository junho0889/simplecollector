"""K. 직렬화 테스트 (K001~K040)"""
import json
import zlib
import pytest
from src.publishers.rabbitmq.serializer import MessageSerializer


@pytest.fixture
def ser_zlib():
    return MessageSerializer(compression="zlib")


@pytest.fixture
def ser_none():
    return MessageSerializer(compression="none")


def _sample_records(n=1):
    return [
        {"source_time": "2026-03-21T00:00:00.000Z", "plc_id": 1,
         "tag_id": i, "v_float": 23.45, "quality": 1, "tag_name": "온도"}
        for i in range(n)
    ]


# ============================================================================
# K-1. JSON
# ============================================================================

class TestJSON:
    """K001~K010"""

    def test_K001_korean(self, ser_zlib):
        records = [{"name": "온도센서"}]
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result[0]["name"] == "온도센서"

    def test_K003_empty_list(self, ser_zlib):
        data = ser_zlib.serialize([])
        result = ser_zlib.deserialize(data)
        assert result == []

    def test_K004_single_record(self, ser_zlib):
        records = _sample_records(1)
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result[0]["tag_id"] == 0
        assert result[0]["v_float"] == 23.45

    def test_K005_1000_records(self, ser_zlib):
        records = _sample_records(1000)
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert len(result) == 1000

    def test_K006_special_chars(self, ser_zlib):
        records = [{"text": 'value "with" \\ special / chars'}]
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result[0]["text"] == 'value "with" \\ special / chars'

    def test_K008_none_value(self, ser_zlib):
        records = [{"value": None}]
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result[0]["value"] is None


# ============================================================================
# K-2. 압축
# ============================================================================

class TestCompression:
    """K011~K018"""

    def test_K011_zlib(self, ser_zlib):
        records = _sample_records(100)
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert len(result) == 100

    def test_K013_none(self, ser_none):
        records = _sample_records(10)
        data = ser_none.serialize(records)
        result = ser_none.deserialize(data)
        assert len(result) == 10

    def test_K014_zlib_decompresses(self, ser_zlib):
        records = _sample_records(10)
        data = ser_zlib.serialize(records)
        # 직접 zlib decompress 가능 확인
        decompressed = zlib.decompress(data)
        parsed = json.loads(decompressed)
        assert len(parsed) == 10

    def test_K017_compression_ratio(self, ser_zlib):
        records = _sample_records(100)
        json_bytes = json.dumps(records, ensure_ascii=False, separators=(',', ':')).encode()
        compressed = ser_zlib.serialize(records)
        assert len(compressed) < len(json_bytes)


# ============================================================================
# K-4. 전체 파이프라인 왕복
# ============================================================================

class TestRoundTrip:
    """K026~K032"""

    def test_K026_roundtrip(self, ser_zlib):
        records = _sample_records(50)
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result == records

    def test_K028_none_roundtrip(self, ser_none):
        records = _sample_records(5)
        data = ser_none.serialize(records)
        result = ser_none.deserialize(data)
        assert result == records

    def test_K030_korean_roundtrip(self, ser_zlib):
        records = [{"tag_name": "온도", "description": "1호기 온도 센서"}]
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert result[0]["tag_name"] == "온도"

    def test_K031_empty_roundtrip(self, ser_zlib):
        data = ser_zlib.serialize([])
        assert ser_zlib.deserialize(data) == []

    def test_K032_large_batch(self, ser_zlib):
        records = _sample_records(10000)
        data = ser_zlib.serialize(records)
        result = ser_zlib.deserialize(data)
        assert len(result) == 10000
