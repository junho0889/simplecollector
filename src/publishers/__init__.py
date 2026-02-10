"""
Publishers 모듈
===============

데이터 발행기 구현을 제공합니다.

지원 발행 대상 (모듈로 확장):
    - Database (PostgreSQL/TimescaleDB)
    - MQTT
    - JSON File
    - HTTP/REST API
    - 기타 (커스텀 확장 가능)

Usage:
    from src.publishers import BasePublisher, JsonFilePublisher
    from src.publishers.database import DatabasePublisher

    publisher = DatabasePublisher("db_publisher", db_config)
    await publisher.connect()
    await publisher.publish(data_list)
"""

from .base import BasePublisher
from .json_file_publisher import JsonFilePublisher

__all__ = [
    "BasePublisher",
    "JsonFilePublisher",
]
