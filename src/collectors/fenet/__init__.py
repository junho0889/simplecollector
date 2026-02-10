"""
FENET (LS Electric XGT) Collector Module
=========================================

LS Electric(구 LS산전)의 XGT 시리즈 PLC를 위한 FENET 프로토콜 수집기입니다.

Supported PLCs:
    - XGT Series: XGK, XGI, XGR
    - XGB Series: XBC, XEC (FEnet 모듈 장착)

Protocol:
    - XGT 전용 프로토콜 (LS Electric proprietary)
    - TCP 포트 2004 (기본)
    - UDP 포트 2005 (선택)

Example:
    collector = FenetCollector(
        plc_id=1,
        name="XGT_Collector",
        config=config,
        event_bus=event_bus,
    )

    await collector.start()
"""

from .collector import FenetCollector
from .processor import FenetProcessor

__all__ = ["FenetCollector", "FenetProcessor"]
