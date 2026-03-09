"""
BLE 테스트용 간이 RabbitMQ → MQTT 브릿지
==========================================
RabbitMQ에서 BLE 데이터를 받아 Mosquitto MQTT로 forward합니다.

사용법:
    python deploy/ble/test_mqtt_bridge.py

    # 다른 터미널에서 MQTT 수신 확인:
    docker exec ble-mosquitto mosquitto_sub -t 'ble/#' -v
"""

import asyncio
import json
import zlib
import sys
from pathlib import Path

# Windows stdout 버퍼링 해제
sys.stdout.reconfigure(line_buffering=True)

# 프로젝트 루트 추가
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


async def main():
    try:
        import aio_pika
    except ImportError:
        print("pip install aio-pika 필요")
        return

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("pip install paho-mqtt 필요")
        return

    # MQTT 클라이언트
    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    mqtt_client.connect("localhost", 1883, 60)
    mqtt_client.loop_start()
    print("[MQTT] Connected to localhost:1883")

    # RabbitMQ 연결
    connection = await aio_pika.connect_robust("amqp://admin:admin@localhost/")
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    exchange = await channel.get_exchange("plc.data")

    # 임시 큐 생성 + 바인딩
    queue = await channel.declare_queue("test.ble.mqtt", auto_delete=True)
    await queue.bind(exchange, routing_key="plc.*.data")
    print("[RabbitMQ] Queue 'test.ble.mqtt' bound to plc.data exchange")
    print("[Bridge] Waiting for messages... (Ctrl+C to stop)\n")

    msg_count = 0

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                try:
                    # zlib 압축 해제
                    raw = message.body
                    try:
                        raw = zlib.decompress(raw)
                    except zlib.error:
                        pass  # 압축 안 된 경우

                    records = json.loads(raw.decode('utf-8'))
                    msg_count += 1

                    # 포맷: [{source_time, plc_id, tag_id, quality, v_float, collection_group}, ...]
                    if not isinstance(records, list):
                        records = [records]

                    # 그룹별로 분류
                    groups = {}
                    for r in records:
                        g = r.get('collection_group', 'unknown')
                        groups.setdefault(g, []).append(r)

                    plc_id = records[0].get('plc_id', '?') if records else '?'

                    for group, items in groups.items():
                        topic = f"ble/{plc_id}/{group}"
                        payload = json.dumps(items, default=str, ensure_ascii=False)
                        mqtt_client.publish(topic, payload)

                        # 요약 출력
                        sample = {}
                        for r in items[:5]:
                            tid = r.get('tag_id', '?')
                            val = r.get('v_float') or r.get('v_int') or r.get('v_bool') or r.get('v_text')
                            sample[f"tag_{tid}"] = val
                        print(
                            f"[#{msg_count}] plc={plc_id} group={group} "
                            f"tags={len(items)} → MQTT {topic}"
                        )
                        print(f"  sample: {sample}")

                except Exception as e:
                    print(f"[Error] {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
