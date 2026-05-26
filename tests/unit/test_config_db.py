"""config DB 로딩 경로 검증 (CONFIG_SOURCE=db).

build_config_from_db / build_tags_from_db 가 vw_collector/vw_device/vw_tag
행으로부터 파일 경로(ConfigLoader.load/load_tags/load_ble_tags)와 동등한
AppConfig + TagDefinition 을 만드는지 확인한다. 리브랜딩 sanity 포함.
"""
import os
from pathlib import Path

import pytest

from src.core.config import ConfigLoader, AppConfig
from src.core.interfaces import DataType
from src.version import APP_NAME


# ---------------------------------------------------------------------------
# 합성 행 (vw_* 가 반환하는 dict 모양) — PLC
# ---------------------------------------------------------------------------
def _plc_collector_row():
    return {
        "collector_id": 1, "collector_key": "PLC-A", "name": "PLC-A",
        "description": "YOKE ASSY", "site": "진영전기", "area": "릴레이", "line": "JH-2HO",
        "enabled": True, "device_type": "plc",
        "protocol_type": "mc_protocol", "host": "192.168.0.1", "port": 5007,
        "unit_id": 1, "timeout_ms": 5000, "reconnect_interval_ms": 3000,
        "plc_series": "iq-r", "frame_type": "binary_3e", "network_no": 0,
        "pc_no": 255, "unit_io": 1023, "unit_station": 0, "max_address_gap": 500,
        "cache_ttl": None, "duplicate_filter_s": None,
        "buf_max_size": 10000, "buf_batch_size": 100, "buf_threshold_ratio": 0.8,
        "buf_drop_oldest": True, "buf_persist_on_shutdown": True,
        "buf_persist_path": "data/buffer.pkl",
        "log_level": "INFO", "log_collection_level": "INFO", "log_publish_level": "INFO",
        "log_loss_level": "WARNING", "log_file_path": None, "log_format": None,
        "rmq_enabled": True, "rmq_exchange_name": "plc.data", "rmq_exchange_type": "topic",
        "rmq_routing_key_prefix": "plc", "rmq_compression": "zlib",
        "rmq_encryption_enabled": False, "rmq_heartbeat": 60, "rmq_delivery_mode": 2,
        "rmq_publish_interval_ms": 1000,
    }


def _plc_groups():
    base = dict(timeout_ms=5000, retry_count=2, retry_delay_ms=500,
               deadband=0.0, deadband_type="absolute")
    return [
        {"name": "plc_data", "interval_ms": 1000, "mode": "polling", **base},
        {"name": "alm", "interval_ms": 500, "mode": "on_change", **base},
    ]


def _plc_devices():
    return [{
        "device_type": "plc", "device_id": 1, "collector_id": 1, "collector_key": "PLC-A",
        "name": "PLC-A", "description": "YOKE ASSY",
        "site": "진영전기", "area": "릴레이", "line": "JH-2HO",
        "mac_address": None, "device_profile": None, "device_name_filter": None,
        "collect_yn": "Y", "protocol_type": "mc_protocol", "host": "192.168.0.1", "port": 5007,
    }]


def _plc_tag_row(**over):
    row = {
        "collector_id": 1, "device_type": "plc", "device_id": 1, "tag_id": 1,
        "tag_name": "D900", "collection_group": "plc_data", "data_type": "uint16",
        "memory": "D", "address": 900, "word_length": None, "string_length": None,
        "format": None, "raw_type": None, "scale": 1.0, "offset_value": 0.0,
        "decimals": None, "unit": "", "bool_true_value": None, "bool_false_value": None,
        "bool_invert": False, "byte_offset": None, "ble_mode": None, "description": "",
        "collect_yn": "Y", "mac_address": None, "device_name_filter": None,
        "device_profile": None,
    }
    row.update(over)
    return row


# ---------------------------------------------------------------------------
# 합성 행 — BLE
# ---------------------------------------------------------------------------
def _ble_collector_row():
    r = _plc_collector_row()
    r.update({
        "collector_id": 2, "collector_key": "BLE_JEM", "name": "BLE_JEM",
        "device_type": "ble", "protocol_type": "ble", "host": None, "port": None,
        "plc_series": None, "frame_type": None, "network_no": None, "pc_no": None,
        "unit_io": None, "unit_station": None, "max_address_gap": None,
        "cache_ttl": 30.0, "duplicate_filter_s": 4.0,
    })
    return r


def _ble_devices():
    return [{
        "device_type": "ble", "device_id": 1, "collector_id": 2, "collector_key": "BLE_JEM",
        "name": "sensor1", "description": "Air 청소 진동센서",
        "site": "진영전기", "area": "릴레이", "line": "JH-2HO",
        "mac_address": "F6:5A:83:C7:DB:C7", "device_profile": "pts-2305bp",
        "device_name_filter": "POSIOT", "collect_yn": "Y",
        "protocol_type": "ble", "host": None, "port": None,
    }]


def _ble_tag_row(**over):
    row = {
        "collector_id": 2, "device_type": "ble", "device_id": 1, "tag_id": 1,
        "tag_name": "temperature", "collection_group": "ble_data", "data_type": "int16",
        "memory": None, "address": None, "word_length": None, "string_length": None,
        "format": None, "raw_type": None, "scale": 1.0, "offset_value": 0.0,
        "decimals": 2, "unit": "℃", "bool_true_value": None, "bool_false_value": None,
        "bool_invert": False, "byte_offset": None, "ble_mode": None,
        "description": "Pulley 온도", "collect_yn": "Y",
        "mac_address": "F6:5A:83:C7:DB:C7", "device_name_filter": "POSIOT",
        "device_profile": "pts-2305bp",
    }
    row.update(over)
    return row


# ===========================================================================
# PLC config 빌드
# ===========================================================================
def test_plc_build_config_basic():
    cfg = ConfigLoader.build_config_from_db(
        _plc_collector_row(), _plc_groups(), _plc_devices())
    assert isinstance(cfg, AppConfig)
    assert cfg.collector.plc_id == 1
    assert cfg.collector.is_ble is False
    assert cfg.collector.name == "PLC-A"
    assert cfg.collector.protocol.type == "mc_protocol"
    assert cfg.collector.protocol.host == "192.168.0.1"
    assert cfg.collector.protocol.port == 5007


def test_plc_protocol_extra_reconstructed():
    cfg = ConfigLoader.build_config_from_db(
        _plc_collector_row(), _plc_groups(), _plc_devices())
    extra = cfg.collector.protocol.extra
    assert extra["plc_series"] == "iq-r"
    assert extra["frame_type"] == "binary_3e"
    assert extra["max_address_gap"] == 500
    assert extra["pc_no"] == 255
    # BLE 전용 키는 PLC에 없어야 함 (값이 None → 제외)
    assert "cache_ttl" not in extra


def test_plc_collection_groups():
    cfg = ConfigLoader.build_config_from_db(
        _plc_collector_row(), _plc_groups(), _plc_devices())
    names = {g.name: g for g in cfg.collector.collection_groups}
    assert set(names) == {"plc_data", "alm"}
    assert names["alm"].mode == "on_change"
    assert names["plc_data"].interval_ms == 1000


def test_plc_buffer_and_logging():
    cfg = ConfigLoader.build_config_from_db(
        _plc_collector_row(), _plc_groups(), _plc_devices())
    assert cfg.buffer.max_size == 10000
    assert cfg.buffer.threshold_ratio == 0.8
    assert cfg.logging.level == "INFO"
    assert cfg.logging.loss_level == "WARNING"


def test_rabbitmq_connection_from_env(monkeypatch):
    """RabbitMQ 접속(host/user/pass)은 env, 동작값은 컬럼."""
    monkeypatch.setenv("RABBITMQ_HOST", "broker.local")
    monkeypatch.setenv("RABBITMQ_USER", "neo")
    monkeypatch.setenv("RABBITMQ_PASSWORD", "secret")
    cfg = ConfigLoader.build_config_from_db(
        _plc_collector_row(), _plc_groups(), _plc_devices())
    rmq = cfg.publisher.rabbitmq
    assert rmq.host == "broker.local"          # env
    assert rmq.username == "neo"               # env
    assert rmq.password == "secret"            # env
    assert rmq.exchange_name == "plc.data"     # 컬럼
    assert rmq.compression == "zlib"           # 컬럼
    assert rmq.enabled is True


# ===========================================================================
# PLC 태그 빌드 (주소 조합 / 타입 해석은 기존 _parse_tag_row 재사용)
# ===========================================================================
def test_plc_tag_address_and_type():
    rows = [
        _plc_tag_row(tag_id=1, tag_name="D900", data_type="uint16",
                     memory="D", address=900),
        _plc_tag_row(tag_id=2, tag_name="Model", data_type="string",
                     memory="D", address=910, word_length=10),
        _plc_tag_row(tag_id=3, tag_name="Yield", data_type="float32",
                     memory="D", address=820, decimals=1, scale=1.0),
    ]
    tags = ConfigLoader.build_tags_from_db(rows, "plc")
    by_id = {t.tag_id: t for t in tags}
    assert by_id[1].address == "D900"
    assert by_id[1].data_type == DataType.UINT16
    # STRING + word_length → 주소에 :len 부착
    assert by_id[2].address == "D910:10"
    assert by_id[2].data_type == DataType.STRING
    assert by_id[3].data_type == DataType.FLOAT32
    assert by_id[3].decimals == 1


def test_plc_hex_device_address_preserved():
    # 16진수 주소(Y/X/B)는 config DB에 이미 정수로 저장됨 → 그대로 조합
    rows = [_plc_tag_row(tag_id=10, tag_name="M100", data_type="bool",
                         memory="M", address=100)]
    tags = ConfigLoader.build_tags_from_db(rows, "plc")
    assert tags[0].address == "M100"
    assert tags[0].data_type == DataType.BOOL


# ===========================================================================
# BLE config + 태그
# ===========================================================================
def test_ble_build_config_devices():
    cfg = ConfigLoader.build_config_from_db(
        _ble_collector_row(), [{"name": "ble_data", "interval_ms": 1000}], _ble_devices())
    assert cfg.collector.is_ble is True
    assert len(cfg.collector.devices) == 1
    dev = cfg.collector.devices[0]
    assert dev.device_id == 1
    assert dev.mac_address == "F6:5A:83:C7:DB:C7"
    assert dev.device_profile == "pts-2305bp"
    assert cfg.collector.protocol.type == "ble"
    assert cfg.collector.protocol.extra["cache_ttl"] == 30.0


def test_ble_tag_build():
    rows = [
        _ble_tag_row(tag_id=1, tag_name="temperature", data_type="int16", decimals=2),
        _ble_tag_row(tag_id=3, tag_name="pressure", data_type="uint16",
                     scale=0.0622559, offset_value=12.207, decimals=None, unit="hPa"),
    ]
    tags = ConfigLoader.build_tags_from_db(rows, "ble")
    by_id = {t.tag_id: t for t in tags}
    # BLE는 address 없음, tag_name이 프로파일 필드명
    assert by_id[1].address == ""
    assert by_id[1].mac_address == "F6:5A:83:C7:DB:C7"
    assert by_id[1].ble_mode == "pts-2305bp"   # device_profile.lower()
    assert by_id[1].device_id == 1
    assert by_id[1].decimals == 2
    # pressure: 선형 스케일 (profile이 raw 반환 → CSV scale/offset 단일 경로)
    assert by_id[3].scale == pytest.approx(0.0622559)
    assert by_id[3].offset == pytest.approx(12.207)
    assert by_id[3].data_type == DataType.UINT16


def test_collect_yn_filter_is_caller_side():
    """build_tags_from_db 는 받은 행을 모두 변환 (collect_yn 필터는 vw_tag/쿼리에서)."""
    rows = [_plc_tag_row(tag_id=1), _plc_tag_row(tag_id=2)]
    tags = ConfigLoader.build_tags_from_db(rows, "plc")
    assert len(tags) == 2


# ===========================================================================
# 리브랜딩 sanity
# ===========================================================================
def test_rebrand_app_name():
    assert APP_NAME == "NeuroForge Collector"


# ===========================================================================
# 파일 경로 회귀 — 기존 YAML/CSV 로딩이 여전히 동작 (디버그 config 있을 때만)
# ===========================================================================
def test_file_path_still_works_if_present():
    root = Path(__file__).resolve().parents[2]
    yaml_path = root / "config" / "test" / "collector_debug.yaml"
    if not yaml_path.exists():
        pytest.skip("config/test/collector_debug.yaml 없음")
    cfg = ConfigLoader.load(yaml_path)
    assert isinstance(cfg, AppConfig)
    assert cfg.collector.name  # 비어있지 않음
    tags_path = root / cfg.collector.tags_file
    if tags_path.exists():
        tags = ConfigLoader.load_tags(tags_path)
        assert len(tags) > 0
