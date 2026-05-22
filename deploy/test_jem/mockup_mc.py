"""
MC Protocol Mockup — PLC-D 시뮬레이터
======================================

실제 PLC 없이 tags CSV를 읽어 가짜 데이터를 생성하고
RabbitMQ에 실제 collector와 동일한 포맷으로 발행합니다.

Usage:
    python deploy/test_jem/mockup_mc.py
"""

import asyncio
import csv
import json
import random
import sys
import zlib
from datetime import datetime
from pathlib import Path

import aio_pika

# ============================================================================
# 설정
# ============================================================================
PLC_ID = 4
PLC_NAME = "PLC-D"
TAGS_CSV = Path(__file__).parent / "tags_mc.csv"

RMQ_HOST = "127.0.0.1"
RMQ_PORT = 5672
RMQ_USER = "admin"
RMQ_PASS = "admin"
EXCHANGE = "plc.data"
ROUTING_KEY = f"plc.{PLC_ID}.data"

INTERVAL_SEC = 1.0  # plc_data 발행 주기


# ============================================================================
# 태그 로드
# ============================================================================
def load_tags(csv_path: Path):
    tags = []
    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        lines = [line for line in f if not line.strip().startswith('#')]
        reader = csv.DictReader(lines)
        for row in reader:
            try:
                tag_id = int(row.get('tag_id', 0))
                if tag_id == 0:
                    continue
                tags.append({
                    'tag_id': tag_id,
                    'tag_name': row.get('tag_name', ''),
                    'data_type': row.get('data_type', '').lower(),
                    'collection_group': row.get('collection_group', 'plc_data'),
                    'scale': float(row.get('scale', 1) or 1),
                    'decimals': int(row.get('decimals', 0) or 0),
                })
            except (ValueError, KeyError):
                continue
    return tags


def generate_value(tag):
    dt = tag['data_type']
    if dt == 'bool':
        return random.choice([True, False])
    elif dt in ('uint16', 'uint32', 'int16', 'int32'):
        return random.randint(0, 1000)
    elif dt in ('float32', 'float64'):
        return round(random.uniform(0, 100), 2)
    elif dt == 'string':
        return "MOCKUP_DATA"
    return random.randint(0, 100)


def make_record(tag, plc_id=PLC_ID):
    now = datetime.now()
    dt = tag['data_type']
    record = {
        "source_time": now.isoformat(timespec='milliseconds'),
        "plc_id": plc_id,
        "tag_id": tag['tag_id'],
        "quality": 1,
        "collection_group": tag['collection_group'],
        "tag_name": tag['tag_name'],
    }
    value = generate_value(tag)
    if dt == 'bool':
        record["v_bool"] = value
    elif dt in ('uint16', 'int16', 'uint32', 'int32'):
        record["v_int"] = value
    elif dt in ('float32', 'float64'):
        record["v_float"] = value
    elif dt == 'string':
        record["v_text"] = value
    else:
        record["v_int"] = value
    return record


# ============================================================================
# 발행
# ============================================================================
async def main():
    print(f"MC Mockup: PLC-D (plc_id={PLC_ID})")
    print(f"Tags CSV: {TAGS_CSV}")
    print(f"RabbitMQ: {RMQ_HOST}:{RMQ_PORT} exchange={EXCHANGE}")
    print(f"Routing: {ROUTING_KEY}")
    print(f"Interval: {INTERVAL_SEC}s")
    print("-" * 50)

    tags = load_tags(TAGS_CSV)
    groups = {}
    for t in tags:
        g = t['collection_group']
        if g not in groups:
            groups[g] = []
        groups[g].append(t)

    print(f"Loaded {len(tags)} tags:")
    for g, ts in sorted(groups.items()):
        print(f"  {g}: {len(ts)} tags")

    # RabbitMQ 연결
    connection = await aio_pika.connect_robust(
        f"amqp://{RMQ_USER}:{RMQ_PASS}@{RMQ_HOST}:{RMQ_PORT}/",
    )
    channel = await connection.channel()
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)

    print(f"\nConnected to RabbitMQ. Publishing every {INTERVAL_SEC}s...", flush=True)
    print("Press Ctrl+C to stop.\n")

    cycle = 0
    try:
        while True:
            cycle += 1
            now = datetime.now()

            # plc_data: 매 사이클
            plc_data_tags = groups.get('plc_data', [])
            records = [make_record(t) for t in plc_data_tags]

            # alm: 5사이클마다 일부 변경
            if cycle % 5 == 0:
                alm_tags = groups.get('alm', [])
                # 랜덤 10개만
                sample = random.sample(alm_tags, min(10, len(alm_tags)))
                records += [make_record(t) for t in sample]

            # log: 10사이클마다
            if cycle % 10 == 0:
                log_tags = groups.get('log', [])
                records += [make_record(t) for t in log_tags]

            # action: 10사이클마다
            if cycle % 10 == 0:
                action_tags = groups.get('action', [])
                sample = random.sample(action_tags, min(20, len(action_tags)))
                records += [make_record(t) for t in sample]

            # 직렬화: JSON → zlib (실제 collector와 동일)
            payload = json.dumps(records, ensure_ascii=False).encode('utf-8')
            compressed = zlib.compress(payload)

            headers = {
                "compression": "zlib",
                "encrypted": "false",
                "plc_id": str(PLC_ID),
                "collector_name": PLC_NAME,
                "batch_count": str(len(records)),
            }

            message = aio_pika.Message(
                body=compressed,
                headers=headers,
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            )

            await exchange.publish(message, routing_key=ROUTING_KEY)

            ts = now.strftime("%H:%M:%S")
            log_count = len(groups.get('log', []))
            extra = ""
            if cycle % 5 == 0:
                extra += ", alm=10"
            if cycle % 10 == 0:
                extra += f", log={log_count}"
            print(f"[{ts}] cycle={cycle} published={len(records)} (plc_data={len(plc_data_tags)}{extra})", flush=True)

            await asyncio.sleep(INTERVAL_SEC)

    except KeyboardInterrupt:
        print(f"\nStopped after {cycle} cycles.")
    finally:
        await connection.close()


if __name__ == "__main__":
    asyncio.run(main())
