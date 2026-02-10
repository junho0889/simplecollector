"""
Modbus TCP Collector 모듈
=========================

Modbus TCP 프로토콜을 통한 PLC 데이터 수집을 제공합니다.

Features:
    - 비동기 Modbus TCP 통신
    - 자동 재연결
    - 연속 주소 병합 읽기 (최적화)
    - 연결 상태 모니터링

Usage:
    from src.collectors.modbus import ModbusCollector, ModbusProcessor

    collector = ModbusCollector(plc_id=1, name="PLC1", config=config)
    await collector.connect()
    data = await collector.collect("1sec")
"""

from .collector import ModbusCollector
from .processor import ModbusProcessor

__all__ = [
    "ModbusCollector",
    "ModbusProcessor",
]
