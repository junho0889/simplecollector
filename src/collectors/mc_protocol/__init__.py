"""
MC Protocol Collector Module
============================

미쓰비시 PLC와 MC Protocol (MELSEC Communication Protocol)을 통해 통신합니다.

지원 PLC 시리즈:
    - iQ-R 시리즈 (R00, R04, R08, R16, R32, R120 등)
    - Q 시리즈 (Q00U, Q03UD, Q06UD 등)
    - L 시리즈 (L02, L06, L26 등)
    - iQ-F 시리즈 (FX5U, FX5UC 등)

지원 디바이스:
    - D: 데이터 레지스터
    - M: 내부 릴레이
    - X: 입력
    - Y: 출력
    - W: 링크 레지스터
    - R: 파일 레지스터
    - ZR: 확장 파일 레지스터

Usage:
    from src.collectors.mc_protocol import McProtocolCollector, McProtocolProcessor

    collector = McProtocolCollector(
        plc_id=1,
        name="PLC1",
        config=collector_config,
        event_bus=event_bus,
    )
"""

from .collector import McProtocolCollector
from .processor import McProtocolProcessor

__all__ = [
    "McProtocolCollector",
    "McProtocolProcessor",
]
