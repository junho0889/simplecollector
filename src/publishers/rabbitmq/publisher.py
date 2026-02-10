"""
RabbitMQ Publisher
==================

ProcessedData 배치를 RabbitMQ 토픽 교환기로 발행합니다.

Features:
    - 비동기 AMQP 통신 (aio-pika / aiormq)
    - 배치 직렬화 (JSON → 압축 → 암호화)
    - Topic Exchange 기반 라우팅
    - Robust 연결 (자동 재연결)
    - Persistent 메시지 (delivery_mode=2)

Message Format:
    - body: compressed+encrypted JSON array of ProcessedData.to_dict()
    - routing_key: "{prefix}.{plc_id}.data" (예: "plc.1.data")
    - headers:
        compression: "zlib" / "gzip" / "none"
        encrypted: "true" / "false"
        plc_id: int
        batch_count: int

Exchange Topology:
    Topic Exchange "plc.data" (durable)
    ├── Binding: "plc.*.data" → queue.db      (Database batch insert)
    └── Binding: "plc.*.data" → queue.mqtt    (MQTT forward)

Example:
    publisher = RabbitMQPublisher(
        name="rmq_publisher",
        rabbitmq_config=rabbitmq_config,
        publisher_config=publisher_config,
    )
    await publisher.start()
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

try:
    import aio_pika
    from aio_pika import Message, DeliveryMode, ExchangeType
    AIO_PIKA_AVAILABLE = True
except ImportError:
    AIO_PIKA_AVAILABLE = False

from ...publishers.base import BasePublisher
from ...core.interfaces import ProcessedData
from ...core.config import RabbitMQConfig, PublisherConfig
from ...utils.logging import LoggerFactory, VERBOSE
from .serializer import MessageSerializer

logger = LoggerFactory.get_publish_logger()


class RabbitMQPublisher(BasePublisher):
    """
    RabbitMQ 데이터 발행기.

    aio-pika를 사용하여 ProcessedData 배치를 RabbitMQ 토픽 교환기로 발행합니다.
    BasePublisher의 publish loop, retry, reconnect 로직을 그대로 활용합니다.

    Attributes:
        _rmq_config: RabbitMQ 설정
        _connection: aio_pika.RobustConnection
        _channel: aio_pika.Channel
        _exchange: aio_pika.Exchange
        _serializer: MessageSerializer (압축/암호화)
    """

    def __init__(
        self,
        name: str,
        rabbitmq_config: RabbitMQConfig,
        publisher_config: PublisherConfig,
    ):
        """
        Args:
            name: 발행기 이름
            rabbitmq_config: RabbitMQ 설정
            publisher_config: 발행기 공통 설정
        """
        super().__init__(name, publisher_config)

        if not AIO_PIKA_AVAILABLE:
            raise ImportError(
                "aio-pika required for RabbitMQ publisher. "
                "Install with: pip install aio-pika"
            )

        self._rmq_config = rabbitmq_config
        self._connection: Optional[Any] = None
        self._channel: Optional[Any] = None
        self._exchange: Optional[Any] = None

        # 메시지 직렬화 (압축 + 암호화)
        self._serializer = MessageSerializer(
            compression=rabbitmq_config.compression,
            encryption_enabled=rabbitmq_config.encryption_enabled,
            encryption_key=rabbitmq_config.encryption_key,
        )

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        RabbitMQ 연결 및 교환기 선언.

        Returns:
            연결 성공 여부
        """
        try:
            # AMQP URL 구성
            vhost = self._rmq_config.virtual_host
            if not vhost.startswith('/'):
                vhost = '/' + vhost

            url = (
                f"amqp://{self._rmq_config.username}:{self._rmq_config.password}"
                f"@{self._rmq_config.host}:{self._rmq_config.port}"
                f"{vhost}"
            )

            # Robust 연결 (자동 재연결 내장)
            self._connection = await aio_pika.connect_robust(
                url,
                heartbeat=self._rmq_config.heartbeat,
                timeout=self._rmq_config.connection_timeout,
            )

            # 채널 생성
            self._channel = await self._connection.channel()
            await self._channel.set_qos(prefetch_count=10)

            # Topic Exchange 선언 (durable)
            exchange_type = ExchangeType.TOPIC
            if self._rmq_config.exchange_type == "direct":
                exchange_type = ExchangeType.DIRECT
            elif self._rmq_config.exchange_type == "fanout":
                exchange_type = ExchangeType.FANOUT

            self._exchange = await self._channel.declare_exchange(
                self._rmq_config.exchange_name,
                exchange_type,
                durable=True,
            )

            logger.info(
                f"[{self._name}] Connected to RabbitMQ "
                f"{self._rmq_config.host}:{self._rmq_config.port} "
                f"exchange={self._rmq_config.exchange_name}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] RabbitMQ connection error: {e}")
            self._connection = None
            self._channel = None
            self._exchange = None
            return False

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        try:
            if self._connection and not self._connection.is_closed:
                await self._connection.close()
        except Exception as e:
            logger.warning(f"[{self._name}] Disconnect error: {e}")
        finally:
            self._connection = None
            self._channel = None
            self._exchange = None

    async def _do_health_check(self) -> bool:
        """연결 상태 확인."""
        return (
            self._connection is not None
            and not self._connection.is_closed
        )

    # =========================================================================
    # Data Publishing
    # =========================================================================

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 배치 발행.

        배치 전체를 하나의 메시지로 직렬화(JSON→압축→암호화)하여
        Topic Exchange에 발행합니다.

        Args:
            data: 발행할 ProcessedData 리스트

        Returns:
            발행 성공 여부
        """
        if not data:
            return True

        if not self._exchange:
            logger.warning(f"[{self._name}] Exchange not available")
            return False

        try:
            plc_id = data[0].plc_id
            routing_key = f"{self._rmq_config.routing_key_prefix}.{plc_id}.data"

            # 직렬화: List[ProcessedData] → compressed bytes
            dict_list = [item.to_dict() for item in data]
            body = self._serializer.serialize(dict_list)

            # Delivery mode
            delivery_mode = (
                DeliveryMode.PERSISTENT
                if self._rmq_config.delivery_mode == 2
                else DeliveryMode.NOT_PERSISTENT
            )

            # AMQP 메시지 생성
            message = Message(
                body=body,
                delivery_mode=delivery_mode,
                content_type="application/json",
                headers={
                    "compression": self._rmq_config.compression,
                    "encrypted": str(self._rmq_config.encryption_enabled).lower(),
                    "plc_id": plc_id,
                    "batch_count": len(data),
                },
                timestamp=datetime.now(),
            )

            # 발행
            await self._exchange.publish(
                message,
                routing_key=routing_key,
            )

            logger.log(
                VERBOSE,
                f"[{self._name}] Published {len(data)} records "
                f"to {routing_key} ({len(body)} bytes)"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] RabbitMQ publish error: {e}")
            # 연결 문제일 수 있으므로 연결 상태 갱신
            self._is_connected = False
            return False

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """통계 조회."""
        stats = super().get_stats()
        stats.update({
            "rabbitmq_host": self._rmq_config.host,
            "rabbitmq_port": self._rmq_config.port,
            "exchange_name": self._rmq_config.exchange_name,
            "routing_key_prefix": self._rmq_config.routing_key_prefix,
            "compression": self._rmq_config.compression,
            "encryption_enabled": self._rmq_config.encryption_enabled,
        })
        return stats
