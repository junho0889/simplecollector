"""
Publishers 모듈
===============

데이터 발행기 구현을 제공합니다.

지원 발행 대상:
    - RabbitMQ (메인 발행 대상)

Usage:
    from src.publishers import BasePublisher
    from src.publishers.rabbitmq import RabbitMQPublisher
"""

from .base import BasePublisher

__all__ = [
    "BasePublisher",
]
