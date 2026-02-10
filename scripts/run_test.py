#!/usr/bin/env python3
"""
MC Protocol 테스트 실행 스크립트
================================

시뮬레이터와 Collector를 함께 실행하여 테스트합니다.

Usage:
    python scripts/run_test.py
"""

import asyncio
import sys
import os
from pathlib import Path

# 프로젝트 루트 경로 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Windows asyncio 설정
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def run_simulator():
    """시뮬레이터 실행."""
    from tests.mc_protocol_test.mc_simulator import McProtocolSimulator

    simulator = McProtocolSimulator(host="127.0.0.1", port=5000)
    await simulator.start()


async def run_collector():
    """Collector 실행."""
    import logging

    # 콘솔 로깅 설정
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    # 설정 로드 (PLC-A 1001 태그 테스트)
    from src.core.config import ConfigLoader
    config = ConfigLoader.load("config/collector_plc_a_test.yaml")

    # 태그 로드
    from src.core.config import ConfigLoader as CL
    tags = CL.load_tags(config.collector.tags_file)
    print(f"Loaded {len(tags)} tags", flush=True)

    # 컴포넌트 생성
    from src.core.events import EventBus
    from src.main import ComponentFactory
    from src.pipeline import Pipeline
    from src.publishers.log_publisher import LogPublisher

    print("Creating event bus...", flush=True)
    event_bus = EventBus()

    # Collector 생성
    print("Creating collector...", flush=True)
    collector = ComponentFactory.create_collector(config, event_bus)
    collector.set_tags(tags)
    print(f"Collector created: {type(collector).__name__}", flush=True)

    # Processor 생성
    print("Creating processor...", flush=True)
    processor = ComponentFactory.create_processor(config)
    print(f"Processor created: {type(processor).__name__}", flush=True)

    # Publisher (Log - 로그 출력)
    print("Creating publisher...", flush=True)
    publisher = LogPublisher(name="test_log_publisher", sample_size=10)
    print(f"Publisher created: {type(publisher).__name__}", flush=True)

    # Pipeline 생성
    print("Creating pipeline...", flush=True)
    pipeline = Pipeline(
        name="test_pipeline",
        collector=collector,
        processor=processor,
        publisher=publisher,
        buffer_config=config.buffer,
        tags=tags,
        event_bus=event_bus,
    )
    print("Pipeline created!", flush=True)

    # 10초 동안 수집 후 종료
    print("\n=== Starting collection for 10 seconds ===\n", flush=True)

    await pipeline.start()
    await asyncio.sleep(10)
    await pipeline.stop()

    print("\n=== Test completed ===")


async def main():
    """메인 함수."""
    print("=" * 60)
    print("MC Protocol Test")
    print("=" * 60)

    # 시뮬레이터를 별도 태스크로 실행
    simulator_task = asyncio.create_task(run_simulator())

    # 시뮬레이터 시작 대기
    await asyncio.sleep(1)

    try:
        # Collector 실행
        await run_collector()
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 시뮬레이터 종료
        simulator_task.cancel()
        try:
            await simulator_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
