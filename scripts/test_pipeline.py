#!/usr/bin/env python3
"""간단한 파이프라인 테스트 - main.py import 없이."""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

print("Step 1: Import modules", flush=True)
from src.core.config import ConfigLoader
from src.core.events import EventBus
# 직접 collector와 processor import (main.py의 모듈 레벨 코드 피하기)
from src.collectors.mc_protocol import McProtocolCollector, McProtocolProcessor
from src.pipeline import Pipeline
from src.publishers.log_publisher import LogPublisher
print("Step 1: OK", flush=True)

print("Step 2: Load config", flush=True)
config = ConfigLoader.load("config/collector_plc_a_test.yaml")
print("Step 2: OK", flush=True)

print("Step 3: Load tags", flush=True)
tags = ConfigLoader.load_tags(config.collector.tags_file)
print(f"Step 3: OK - {len(tags)} tags loaded", flush=True)


async def main():
    print("Step 4: Create EventBus", flush=True)
    event_bus = EventBus()
    print("Step 4: OK", flush=True)

    print("Step 5: Create Collector", flush=True)
    collector = McProtocolCollector(
        plc_id=config.collector.plc_id,
        name=config.collector.name,
        config=config.collector,
        event_bus=event_bus,
    )
    # 태그는 Pipeline에서 자동으로 register_tags() 호출됨
    print(f"Step 5: OK - {type(collector).__name__}", flush=True)

    print("Step 6: Create Processor", flush=True)
    extra = config.collector.protocol.extra if config.collector.protocol else {}
    processor = McProtocolProcessor(
        name=f"{config.collector.name}_processor",
        word_order=extra.get('word_order', 'little'),
    )
    print(f"Step 6: OK - {type(processor).__name__}", flush=True)

    print("Step 7: Create Publisher", flush=True)
    publisher = LogPublisher(name="test_log_publisher", sample_size=5)
    print(f"Step 7: OK - {type(publisher).__name__}", flush=True)

    print("Step 8: Create Pipeline", flush=True)
    pipeline = Pipeline(
        name="test_pipeline",
        collector=collector,
        processor=processor,
        publisher=publisher,
        buffer_config=config.buffer,
        tags=tags,
        event_bus=event_bus,
    )
    print("Step 8: OK", flush=True)

    print("\n=== Starting collection for 5 seconds ===\n", flush=True)

    # EventBus 시작 (외부에서 전달된 경우 Pipeline에서 시작하지 않음)
    await event_bus.start()

    await pipeline.start()
    await asyncio.sleep(5)
    await pipeline.stop()

    # EventBus 종료
    await event_bus.stop()

    print("\n=== Test completed ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
