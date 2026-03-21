"""
JEM+BLE Integration Test — Mock Producer
=========================================
PLC 2대 + BLE 1대(2센서)의 데이터를 RabbitMQ로 직접 주입.

테스트 시나리오:
  1. PLC1 plc_data: bool/int/float 정상 데이터
  2. PLC2 plc_data: float 데이터
  3. PLC1 alm: on_change 알람
  4. BLE1 ble_data: 온도/습도/배터리/RSSI (2센서)
  5. 혼합 배치: PLC + BLE 데이터가 같은 exchange를 통과
"""

import asyncio
import json
import zlib
import random
import time
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List


def serialize(records: List[Dict[str, Any]]) -> bytes:
    json_bytes = json.dumps(
        records, ensure_ascii=False, separators=(',', ':')
    ).encode('utf-8')
    return zlib.compress(json_bytes, level=6)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def make_record(
    device_id_key: str, device_id: int, tag_id: int,
    group: str = "plc_data", quality: int = 1,
    tag_name: str = "", **values
) -> Dict[str, Any]:
    record = {
        "source_time": now_iso(),
        device_id_key: device_id,
        "tag_id": tag_id,
        "quality": quality,
        "collection_group": group,
    }
    for k, v in values.items():
        if v is not None:
            record[k] = v
    if tag_name:
        record["tag_name"] = tag_name
    return record


# ============================================================================
# Scenarios
# ============================================================================

def scenario_plc1_data() -> List[Dict[str, Any]]:
    """PLC1 plc_data: 모든 타입."""
    records = []
    for _ in range(10):
        records.append(make_record("plc_id", 1, 1, "plc_data",
            v_int=random.randint(0, 65535), tag_name="생산수량"))
        records.append(make_record("plc_id", 1, 2, "plc_data",
            v_float=round(random.uniform(20.0, 80.0), 2), tag_name="온도"))
        records.append(make_record("plc_id", 1, 3, "plc_data",
            v_bool=random.choice([True, False]), tag_name="운전상태"))
    return records


def scenario_plc2_data() -> List[Dict[str, Any]]:
    """PLC2 plc_data: 멀티 PLC 확인."""
    records = []
    for _ in range(10):
        records.append(make_record("plc_id", 2, 1, "plc_data",
            v_float=round(random.uniform(0.0, 100.0), 2), tag_name="압력"))
    return records


def scenario_plc1_alm() -> List[Dict[str, Any]]:
    """PLC1 alm: 알람 on_change."""
    records = []
    for _ in range(3):
        records.append(make_record("plc_id", 1, 4, "alm",
            v_bool=False, tag_name="비상정지"))
    records.append(make_record("plc_id", 1, 4, "alm",
        v_bool=True, tag_name="비상정지"))
    records.append(make_record("plc_id", 1, 4, "alm",
        v_bool=False, tag_name="비상정지"))
    return records


def scenario_ble_data() -> List[Dict[str, Any]]:
    """BLE ble_data: 2센서 온도/습도/배터리/RSSI."""
    records = []
    for _ in range(10):
        # 센서 1 (tag 1~4)
        records.append(make_record("ble_id", 1, 1, "ble_data",
            v_float=round(random.uniform(18.0, 30.0), 2), tag_name="온도"))
        records.append(make_record("ble_id", 1, 2, "ble_data",
            v_float=round(random.uniform(30.0, 90.0), 2), tag_name="습도"))
        records.append(make_record("ble_id", 1, 3, "ble_data",
            v_int=random.randint(2800, 3300), tag_name="배터리"))
        records.append(make_record("ble_id", 1, 4, "ble_data",
            v_int=random.randint(-90, -30), tag_name="RSSI"))
        # 센서 2 (tag 5~6)
        records.append(make_record("ble_id", 1, 5, "ble_data",
            v_float=round(random.uniform(18.0, 30.0), 2), tag_name="온도"))
        records.append(make_record("ble_id", 1, 6, "ble_data",
            v_float=round(random.uniform(30.0, 90.0), 2), tag_name="습도"))
    return records


# ============================================================================
# Publisher
# ============================================================================

SCENARIOS = [
    ("plc1_data", scenario_plc1_data),
    ("plc2_data", scenario_plc2_data),
    ("plc1_alm", scenario_plc1_alm),
    ("ble_data", scenario_ble_data),
]


async def publish_records(
    host: str, port: int, user: str, password: str,
    name: str, records: List[Dict[str, Any]],
):
    import aio_pika

    url = f"amqp://{user}:{password}@{host}:{port}/"
    connection = await aio_pika.connect_robust(url)

    async with connection:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            "plc.data", aio_pika.ExchangeType.TOPIC, durable=True
        )

        batch_size = 500
        total = len(records)
        sent = 0

        for i in range(0, total, batch_size):
            batch = records[i:i + batch_size]
            body = serialize(batch)

            first = batch[0]
            device_id = first.get("plc_id") or first.get("ble_id", 0)
            device_key = "plc" if "plc_id" in first else "ble"
            routing_key = f"{device_key}.{device_id}.data"

            message = aio_pika.Message(
                body=body,
                headers={
                    "compression": "zlib",
                    "encrypted": "false",
                    "batch_count": str(len(batch)),
                },
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )
            await exchange.publish(message, routing_key=routing_key)
            sent += len(batch)

        print(f"  [{name}] {sent}/{total} records → routing_key={device_key}.{device_id}.data")


async def main():
    host = "rabbitmq"
    port = 5672

    print(f"\n{'='*60}")
    print(f"  JEM+BLE Mock Producer")
    print(f"  RabbitMQ: {host}:{port}")
    print(f"{'='*60}\n")

    start = time.time()
    total = 0

    for name, gen_fn in SCENARIOS:
        records = gen_fn()
        await publish_records(host, port, "admin", "admin", name, records)
        total += len(records)
        await asyncio.sleep(0.5)

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"  완료: {total} records, {elapsed:.1f}s")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
