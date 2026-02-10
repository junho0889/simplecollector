"""
RabbitMQ Publisher 모듈
=======================

수집된 데이터를 RabbitMQ 토픽 교환기로 발행합니다.

Architecture:
    Collector → Processor → Buffer → RabbitMQPublisher → RabbitMQ Exchange
                                                            │
                                                    ┌───────┴───────┐
                                                    ▼               ▼
                                                queue.db       queue.mqtt
                                                    │               │
                                            Publisher Service  Publisher Service
                                            (DB Insert)       (MQTT Forward)
"""
from .publisher import RabbitMQPublisher

__all__ = ["RabbitMQPublisher"]
