"""
S7 Protocol (Siemens) Collector Module
======================================

Siemens S7 시리즈 PLC를 위한 S7 프로토콜 수집기입니다.

Supported PLCs:
    - S7-300 / S7-400
    - S7-1200 / S7-1500 (PUT/GET 활성화 필요)

Protocol:
    - S7comm over ISO-on-TCP (RFC1006)
    - TCP 포트 102

Dependencies:
    - python-snap7 라이브러리

Example:
    collector = S7Collector(
        plc_id=1,
        name="S7_1500_Collector",
        config=config,
        event_bus=event_bus,
    )

    await collector.start()
"""

from .collector import S7Collector
from .processor import S7Processor

__all__ = ["S7Collector", "S7Processor"]
