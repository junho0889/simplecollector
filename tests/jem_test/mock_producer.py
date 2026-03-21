"""
JEM 목업 데이터 생성기
======================
실제 JEM 태그 CSV를 읽어서 simpleCollector와 동일한 형식의
메시지를 RabbitMQ에 주입합니다. (collector/PLC 없이 테스트 가능)

Usage:
    cd D:/4.source/simpleCollector

    # 기본: 10 PLC, 1초 간격, 60초 실행
    python tests/jem_test/mock_producer.py

    # 옵션 지정
    python tests/jem_test/mock_producer.py --interval 0.5 --duration 120 --plc-ids 1,2,3

    # 단발 (1회 전체 PLC 전송)
    python tests/jem_test/mock_producer.py --once
"""

import argparse
import asyncio
import csv
import logging
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# simpleCollector serializer 사용
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.publishers.rabbitmq.serializer import MessageSerializer

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mock-producer")

# 태그 CSV → collector YAML 매핑 (plc_id → tags_file)
INFRA_DIR = Path(__file__).parent / "infra"
CONFIG_DIR = Path(__file__).parent / "config"

# plc_id → (collector_yaml, tags_csv) 매핑 (JEM 기준)
PLC_MAP = {
    1: ("collector_plc1.yaml", "tags_04_PLC-A.csv"),
    2: ("collector_plc2.yaml", "tags_02_PLC-B.csv"),
    3: ("collector_plc3.yaml", "tags_05_PLC-C.csv"),
    4: ("collector_plc4.yaml", "tags_10_PLC-D.csv"),
    5: ("collector_plc5.yaml", "tags_01_PLC-EFG.csv"),
    6: ("collector_plc6.yaml", "tags_06_PLC-H.csv"),
    7: ("collector_plc7.yaml", "tags_07_PLC-I.csv"),
    8: ("collector_plc8.yaml", "tags_03_PLC-J.csv"),
    9: ("collector_plc9.yaml", "tags_08_PLC-K.csv"),
    10: ("collector_plc10.yaml", "tags_09_PLC-L.csv"),
}


def load_tags(csv_path: Path) -> List[dict]:
    """태그 CSV 파싱."""
    tags = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tag = {
                "tag_id": int(row["tag_id"].strip()),
                "tag_name": row["tag_name"].strip(),
                "data_type": row["data_type"].strip().lower(),
                "collection_group": row["collection_group"].strip(),
                "scale": float(row.get("scale", "1").strip() or "1"),
                "offset": float(row.get("offset", "0").strip() or "0"),
                "decimals": int(row["decimals"].strip()) if row.get("decimals", "").strip() else 0,
                "description": row.get("description", "").strip(),
            }
            tags.append(tag)
    return tags


def generate_value(tag: dict) -> dict:
    """태그 타입에 맞는 랜덤 값 생성."""
    dtype = tag["data_type"]
    result = {
        "v_bool": None,
        "v_int": None,
        "v_bigint": None,
        "v_float": None,
        "v_text": None,
    }

    if dtype == "bool":
        result["v_bool"] = random.choice([True, False])
    elif dtype in ("uint16", "int16"):
        result["v_int"] = random.randint(0, 65535)
    elif dtype in ("uint32", "int32"):
        result["v_bigint"] = random.randint(0, 4294967295)
    elif dtype in ("float32", "float64"):
        result["v_float"] = round(random.uniform(0, 1000), 2)
    else:
        result["v_int"] = random.randint(0, 65535)

    return result


def generate_batch(plc_id: int, tags: List[dict], group: str) -> List[dict]:
    """1개 배치(특정 그룹)의 레코드 목록 생성."""
    now = datetime.now(timezone.utc).isoformat()
    records = []
    group_tags = [t for t in tags if t["collection_group"] == group]

    for tag in group_tags:
        values = generate_value(tag)
        record = {
            "plc_id": plc_id,
            "tag_id": tag["tag_id"],
            "tag_name": tag["tag_name"],
            "data_type": tag["data_type"].upper(),
            "source_time": now,
            "collection_group": group,
            "quality": 1,
            **values,
        }
        records.append(record)

    return records


class MockProducer:
    """JEM 형식 목업 데이터 RabbitMQ 주입기."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 36672,
        username: str = "admin",
        password: str = "admin",
        plc_ids: Optional[List[int]] = None,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.plc_ids = plc_ids or list(range(1, 11))
        self._connection: Optional[aio_pika.RobustConnection] = None
        self._exchange: Optional[aio_pika.Exchange] = None
        self._serializer = MessageSerializer(compression="zlib")
        self._tags: Dict[int, List[dict]] = {}
        self.stats = {"batches": 0, "records": 0}

    def load_all_tags(self) -> None:
        """모든 PLC 태그 CSV 로드."""
        for plc_id in self.plc_ids:
            if plc_id not in PLC_MAP:
                logger.warning(f"Unknown plc_id: {plc_id}")
                continue
            _, csv_file = PLC_MAP[plc_id]
            # infra 디렉토리에서 먼저 찾고, 없으면 config 디렉토리
            csv_path = INFRA_DIR / csv_file
            if not csv_path.exists():
                csv_path = CONFIG_DIR / csv_file
            if not csv_path.exists():
                logger.error(f"Tags CSV not found: {csv_file}")
                continue
            self._tags[plc_id] = load_tags(csv_path)
            logger.info(f"Loaded {len(self._tags[plc_id])} tags for PLC {plc_id} ({csv_file})")

        total = sum(len(t) for t in self._tags.values())
        logger.info(f"Total: {total} tags across {len(self._tags)} PLCs")

    async def connect(self) -> None:
        """RabbitMQ 연결."""
        url = f"amqp://{self.username}:{self.password}@{self.host}:{self.port}/"
        self._connection = await aio_pika.connect_robust(url, timeout=10)
        channel = await self._connection.channel()
        self._exchange = await channel.declare_exchange(
            "plc.data", ExchangeType.TOPIC, durable=True
        )
        logger.info(f"Connected to RabbitMQ: {self.host}:{self.port}")

    async def disconnect(self) -> None:
        """연결 해제."""
        if self._connection and not self._connection.is_closed:
            await self._connection.close()

    async def publish_batch(self, plc_id: int, records: List[dict]) -> None:
        """1개 배치를 RabbitMQ에 발행."""
        if not self._exchange or not records:
            return

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
        self.stats["batches"] += 1
        self.stats["records"] += len(records)

    async def produce_all_groups(self) -> int:
        """모든 PLC의 모든 그룹에 대해 1회 배치 생성. 총 레코드 수 반환."""
        total = 0
        for plc_id, tags in self._tags.items():
            groups = set(t["collection_group"] for t in tags)
            for group in groups:
                records = generate_batch(plc_id, tags, group)
                if records:
                    await self.publish_batch(plc_id, records)
                    total += len(records)
        return total

    async def run_continuous(self, interval: float = 1.0, duration: float = 60.0) -> None:
        """지정 시간 동안 연속 생성."""
        deadline = time.monotonic() + duration
        cycle = 0
        while time.monotonic() < deadline:
            cycle += 1
            t0 = time.monotonic()
            n = await self.produce_all_groups()
            elapsed = time.monotonic() - t0
            logger.info(
                f"Cycle {cycle}: {n} records sent ({elapsed:.3f}s) | "
                f"Total: {self.stats['records']} records, {self.stats['batches']} batches"
            )
            remaining = interval - elapsed
            if remaining > 0:
                await asyncio.sleep(remaining)

        logger.info(
            f"Done. {self.stats['records']} records in {self.stats['batches']} batches "
            f"over {duration}s"
        )


async def main():
    parser = argparse.ArgumentParser(description="JEM Mock Data Producer")
    parser.add_argument("--host", default="localhost", help="RabbitMQ host")
    parser.add_argument("--port", type=int, default=36672, help="RabbitMQ port")
    parser.add_argument("--interval", type=float, default=1.0, help="전송 간격 (초)")
    parser.add_argument("--duration", type=float, default=60.0, help="실행 시간 (초)")
    parser.add_argument("--plc-ids", default="", help="PLC ID 목록 (콤마 구분, 기본: 1~10)")
    parser.add_argument("--once", action="store_true", help="1회만 전송 후 종료")
    args = parser.parse_args()

    plc_ids = None
    if args.plc_ids:
        plc_ids = [int(x.strip()) for x in args.plc_ids.split(",")]

    producer = MockProducer(
        host=args.host,
        port=args.port,
        plc_ids=plc_ids,
    )
    producer.load_all_tags()

    try:
        await producer.connect()

        if args.once:
            n = await producer.produce_all_groups()
            logger.info(f"Sent {n} records (one-shot)")
        else:
            await producer.run_continuous(
                interval=args.interval,
                duration=args.duration,
            )
    finally:
        await producer.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
