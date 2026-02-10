"""
Database Publisher 모듈
=======================

TimescaleDB/PostgreSQL로 데이터를 저장합니다.

Features:
    - 비동기 커넥션 풀 (asyncpg)
    - 배치 INSERT
    - 자동 재연결
    - 트랜잭션 관리

Usage:
    from src.publishers.database import DatabasePublisher

    publisher = DatabasePublisher("db_pub", db_config, publisher_config)
    await publisher.connect()
    await publisher.publish(data_list)
"""

from .publisher import DatabasePublisher

__all__ = [
    "DatabasePublisher",
]
