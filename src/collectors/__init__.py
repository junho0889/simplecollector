"""
Collectors 모듈
===============

프로토콜별 데이터 수집기 구현을 제공합니다.

지원 프로토콜 (모듈로 확장):
    - Modbus TCP/RTU
    - FENET (LS산전)
    - MC Protocol (미쓰비시)
    - OPC UA
    - 기타 (커스텀 확장 가능)

Usage:
    from src.collectors import BaseCollector
    from src.collectors.modbus import ModbusCollector

    # 모듈화된 수집기 사용
    collector = ModbusCollector(plc_id=1, name="PLC1", config=modbus_config)
    await collector.connect()
    data = await collector.collect("1sec")
"""

from .base import BaseCollector

__all__ = [
    "BaseCollector",
]
