"""
테스트 메시지 생성기
====================
RabbitMQ에 simpleCollector와 동일한 형식의 테스트 메시지를 생성합니다.
"""

import asyncio
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# simpleCollector serializer 사용
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.publishers.rabbitmq.serializer import MessageSerializer

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message

logger = logging.getLogger(__name__)

DATA_TYPES = ["UINT16", "INT16", "UINT32", "FLOAT32"]


class TestMessageProducer:
    """RabbitMQ 테스트 메시지 생성기."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 35672,
        username: str = "admin",
        password: str = "admin",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._exchange: Optional[aio_pika.Exchange] = None
        self._serializer = MessageSerializer(compression="zlib")
        self.total_produced = 0

    async def connect(self) -> None:
        """RabbitMQ 연결."""
        url = f"amqp://{self.username}:{self.password}@{self.host}:{self.port}/"
        self._connection = await aio_pika.connect_robust(url, timeout=10)
        channel = await self._connection.channel()
        self._exchange = await channel.declare_exchange(
            "plc.data", ExchangeType.TOPIC, durable=True
        )
        logger.info(f"Producer connected: {self.host}:{self.port}")

    async def disconnect(self) -> None:
        """RabbitMQ 연결 해제."""
        if self._connection and not self._connection.is_closed:
            await self._connection.close()
        self._connection = None
        self._exchange = None

    def _generate_batch(
        self,
        plc_id: int,
        tag_count: int,
        collection_group: str = "plc_data",
    ) -> list:
        """배치 데이터 생성 (ProcessedData.to_dict() 형식)."""
        now = datetime.now(timezone.utc).isoformat()
        records = []
        for tag_id in range(1, tag_count + 1):
            dtype = DATA_TYPES[(tag_id - 1) % 4]
            record = {
                "plc_id": plc_id,
                "tag_id": tag_id,
                "tag_name": f"tag_{tag_id:04d}",
                "data_type": dtype,
                "source_time": now,
                "server_time": now,
                "collection_group": collection_group,
                "quality": 1,
                "v_bool": None,
                "v_byte": None,
                "v_int": random.randint(0, 65535) if dtype in ("UINT16", "INT16") else None,
                "v_bigint": random.randint(0, 4294967295) if dtype == "UINT32" else None,
                "v_float": round(random.uniform(0, 1000), 2) if dtype == "FLOAT32" else None,
                "v_text": None,
            }
            records.append(record)
        return records

    async def produce_batch(
        self,
        plc_id: int = 99,
        tag_count: int = 22,
        collection_group: str = "plc_data",
    ) -> int:
        """1개 배치 생성. 생성된 레코드 수 반환."""
        if not self._exchange:
            raise RuntimeError("Not connected")

        records = self._generate_batch(plc_id, tag_count, collection_group)
        body = self._serializer.serialize(records)

        message = Message(
            body=body,
            delivery_mode=DeliveryMode.PERSISTENT,
            content_type="application/json",
            headers={
                "compression": "zlib",
                "encrypted": "false",
                "plc_id": plc_id,
                "batch_count": len(records),
            },
        )
        await self._exchange.publish(message, routing_key=f"plc.{plc_id}.data")
        self.total_produced += len(records)
        return len(records)

    async def produce_continuous(
        self,
        interval: float = 1.0,
        duration: float = 10.0,
        plc_id: int = 99,
        tag_count: int = 22,
    ) -> int:
        """지정 시간 동안 연속 생성. 총 생성 레코드 수 반환."""
        count = 0
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            n = await self.produce_batch(plc_id, tag_count)
            count += n
            await asyncio.sleep(interval)
        return count

    def reset_counter(self) -> None:
        """생성 카운터 초기화."""
        self.total_produced = 0
