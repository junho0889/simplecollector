"""
NeuroForge Collector 메인 엔트리포인트
===================================

데이터 수집기 애플리케이션의 시작점입니다.

Usage:
    # 직접 실행
    python -m src.main

    # 설정 파일 지정
    python -m src.main --config config/collector.yaml

    # Docker
    docker-compose up

Environment Variables:
    CONFIG_PATH: 설정 파일 경로 (기본: config/collector.yaml)
    LOG_LEVEL: 로그 레벨 (기본: INFO)
"""

import argparse
import asyncio
import os
import sys
import platform
from pathlib import Path
from typing import List, Optional, Tuple

# Windows에서 asyncio SelectorEventLoop 사용 설정
# Python 3.14에서 ProactorEventLoop은 add_reader/add_writer를 지원하지 않음
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 프로젝트 루트를 파이썬 경로에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.core.config import ConfigLoader, AppConfig
from src.core.events import EventBus
from src.core.interfaces import TagDefinition
from src.pipeline import Pipeline, PipelineManager
from src.utils.logging import setup_logging, LoggerFactory
from src.version import APP_NAME, APP_VERSION, log_version_info


def parse_args() -> argparse.Namespace:
    """명령행 인수 파싱."""
    parser = argparse.ArgumentParser(
        description="NeuroForge Collector - Industrial Data Collection Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s                           # 기본 설정으로 실행
    %(prog)s --config my_config.yaml   # 커스텀 설정 파일
    %(prog)s --log-level DEBUG         # 디버그 로그 레벨
        """
    )

    parser.add_argument(
        "-c", "--config",
        type=str,
        default=os.environ.get("CONFIG_PATH", "config/collector.yaml"),
        help="설정 파일 경로 (기본: config/collector.yaml)"
    )

    parser.add_argument(
        "-l", "--log-level",
        type=str,
        default=os.environ.get("LOG_LEVEL"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="로그 레벨 (기본: 설정 파일 값)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="설정 검증만 수행하고 종료"
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help="데모 모드로 실행 (실제 연결 없이 테스트)"
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"{APP_NAME} v{APP_VERSION}"
    )

    return parser.parse_args()


# ============================================================================
# Multi-Publisher Wrapper
# ============================================================================

from src.publishers.base import BasePublisher as _BasePublisher


class MultiPublisher(_BasePublisher):
    """
    여러 Publisher에 동시 발행하는 래퍼.

    Pipeline이 하나의 publisher만 받으므로, 여러 publisher를 묶어서
    하나처럼 동작합니다. publish loop는 MultiPublisher가 관리하고,
    _do_publish에서 각 inner publisher에 fan-out합니다.
    """

    def __init__(self, name: str, publishers: list, config):
        super().__init__(name, config)
        self._publishers = publishers

    async def _do_connect(self) -> bool:
        results = await asyncio.gather(
            *[p.connect() for p in self._publishers],
            return_exceptions=True,
        )
        success_count = sum(1 for r in results if r is True)
        _logger = LoggerFactory.get_system_logger()
        _logger.info(
            f"[{self._name}] Connected {success_count}/{len(self._publishers)} publishers"
        )
        return success_count > 0

    async def _do_disconnect(self) -> None:
        await asyncio.gather(
            *[p.disconnect() for p in self._publishers],
            return_exceptions=True,
        )

    async def _do_publish(self, data) -> bool:
        results = await asyncio.gather(
            *[p.publish(data) for p in self._publishers],
            return_exceptions=True,
        )
        return any(r is True for r in results)

    async def _do_health_check(self) -> bool:
        checks = await asyncio.gather(
            *[p.health_check() for p in self._publishers],
            return_exceptions=True,
        )
        return any(c is True for c in checks)

    def get_stats(self):
        stats = super().get_stats()
        stats["publishers"] = [p.get_stats() for p in self._publishers]
        return stats


# ============================================================================
# Component Factory
# ============================================================================

class ComponentFactory:
    """
    컴포넌트 팩토리.

    설정에 따라 적절한 Collector, Processor, Publisher를 생성합니다.
    """

    @staticmethod
    def create_collector(config: AppConfig, event_bus: EventBus):
        """
        Collector 생성.

        Args:
            config: 애플리케이션 설정
            event_bus: 이벤트 버스

        Returns:
            Collector 인스턴스
        """
        protocol_type = config.collector.protocol.type.lower() if config.collector.protocol else "demo"

        if protocol_type == "modbus":
            from src.collectors.modbus import ModbusCollector
            return ModbusCollector(
                plc_id=config.collector.plc_id,
                name=config.collector.name,
                config=config.collector,
                event_bus=event_bus,
            )
        elif protocol_type in ("mc", "mc_protocol", "mcprotocol", "melsec"):
            from src.collectors.mc_protocol import McProtocolCollector
            return McProtocolCollector(
                plc_id=config.collector.plc_id,
                name=config.collector.name,
                config=config.collector,
                event_bus=event_bus,
            )
        elif protocol_type == "ble":
            # BLE 프로토콜: 태그에 mac_address가 있으면 멀티디바이스 모드
            # ble_id가 있으면 사용, 없으면 plc_id 사용
            from src.collectors.ble import BleCollector
            return BleCollector(
                plc_id=config.collector.device_id,
                name=config.collector.name,
                config=config.collector,
                event_bus=event_bus,
            )
        elif protocol_type in ("lora", "lora_rak5146"):
            from src.collectors.lora_rak5146 import LoRaRak5146Collector
            return LoRaRak5146Collector(
                plc_id=config.collector.plc_id,
                name=config.collector.name,
                config=config.collector,
                event_bus=event_bus,
            )
        else:
            # 데모/미지원 프로토콜
            return ComponentFactory._create_demo_collector(config, event_bus)

    @staticmethod
    def create_processor(config: AppConfig):
        """
        Processor 생성.

        Args:
            config: 애플리케이션 설정

        Returns:
            Processor 인스턴스
        """
        protocol_type = config.collector.protocol.type.lower() if config.collector.protocol else "demo"

        if protocol_type == "modbus":
            from src.collectors.modbus import ModbusProcessor
            # 프로토콜 extra 설정에서 바이트 오더 추출
            extra = config.collector.protocol.extra if config.collector.protocol else {}
            return ModbusProcessor(
                name=f"{config.collector.name}_processor",
                byte_order=extra.get('byte_order', 'big'),
                word_order=extra.get('word_order', 'big'),
            )
        elif protocol_type in ("mc", "mc_protocol", "mcprotocol", "melsec"):
            from src.collectors.mc_protocol import McProtocolProcessor
            extra = config.collector.protocol.extra if config.collector.protocol else {}
            return McProtocolProcessor(
                name=f"{config.collector.name}_processor",
                word_order=extra.get('word_order', 'little'),  # 미쓰비시 기본값
            )
        elif protocol_type == "ble":
            from src.collectors.ble import BleProcessor
            extra = config.collector.protocol.extra if config.collector.protocol else {}
            return BleProcessor(
                name=f"{config.collector.name}_processor",
                default_profile=extra.get('device_profile', 'pts-2305bp'),
            )
        elif protocol_type in ("lora", "lora_rak5146"):
            from src.collectors.lora_rak5146 import LoRaRak5146Processor
            extra = config.collector.protocol.extra if config.collector.protocol else {}
            return LoRaRak5146Processor(
                name=f"{config.collector.name}_processor",
                default_profile=extra.get('device_profile', 'posiot_lora'),
            )
        else:
            from src.processors.base import GenericProcessor
            return GenericProcessor(f"{config.collector.name}_processor")

    @staticmethod
    def create_publishers(config: AppConfig) -> list:
        """
        Publisher 리스트 생성.

        활성화된 Publisher만 생성합니다.

        Args:
            config: 애플리케이션 설정

        Returns:
            Publisher 인스턴스 리스트
        """
        publishers = []

        # RabbitMQ Publisher
        if config.publisher.rabbitmq.enabled:
            try:
                from src.publishers.rabbitmq import RabbitMQPublisher
                rmq_pub = RabbitMQPublisher(
                    name=f"{config.collector.name}_rmq_publisher",
                    rabbitmq_config=config.publisher.rabbitmq,
                    publisher_config=config.publisher,
                )
                # BLE 디바이스 메타데이터 설정
                rmq_pub._device_id_key = config.collector.device_id_key
                rmq_pub._collector_name = config.collector.name
                publishers.append(rmq_pub)
            except ImportError as e:
                LoggerFactory.get_system_logger().warning(
                    f"RabbitMQ publisher not available: {e}"
                )

        # Publisher가 없으면 데모 Publisher 사용
        if not publishers:
            publishers.append(ComponentFactory._create_demo_publisher(config))

        return publishers

    @staticmethod
    def _create_demo_collector(config: AppConfig, event_bus: EventBus):
        """데모 Collector 생성."""
        from src.collectors.base import BaseCollector
        from src.core.interfaces import CollectedData
        from datetime import datetime
        import random

        class DemoCollector(BaseCollector):
            """테스트용 데모 수집기."""

            async def _do_connect(self) -> bool:
                return True

            async def _do_disconnect(self) -> None:
                pass

            async def _do_collect(self, group: str) -> Optional[CollectedData]:
                """랜덤 데이터 생성."""
                tags = self._tags.get(group, [])
                values = {
                    tag.tag_id: random.uniform(0, 100)
                    for tag in tags
                }

                return CollectedData(
                    source_time=datetime.now(),
                    collection_time=datetime.now(),
                    plc_id=self._plc_id,
                    raw_data=b"",
                    collection_group=group,
                    metadata={"values": values},
                )

        return DemoCollector(
            plc_id=config.collector.plc_id,
            name=f"{config.collector.name}_demo",
            config=config.collector,
            event_bus=event_bus,
        )

    @staticmethod
    def _create_demo_publisher(config: AppConfig):
        """데모 Publisher 생성."""
        from src.publishers.base import BasePublisher

        class DemoPublisher(BasePublisher):
            """테스트용 데모 발행기."""

            async def _do_connect(self) -> bool:
                return True

            async def _do_disconnect(self) -> None:
                pass

            async def _do_publish(self, data: list) -> bool:
                """콘솔에 출력."""
                logger = LoggerFactory.get_publish_logger()
                for item in data[:3]:
                    logger.info(
                        f"[DEMO] PLC{item.plc_id} Tag{item.tag_id} = {item.value:.2f}"
                    )
                if len(data) > 3:
                    logger.info(f"[DEMO] ... and {len(data) - 3} more records")
                return True

        return DemoPublisher(f"{config.collector.name}_demo_publisher", config.publisher)


# ============================================================================
# Pipeline Creation
# ============================================================================

async def create_pipeline(
    config: AppConfig,
    event_bus: EventBus,
    tags: List[TagDefinition],
    demo_mode: bool = False,
) -> Pipeline:
    """
    파이프라인 생성.

    설정에 따라 적절한 컴포넌트를 생성하고 파이프라인을 구성합니다.

    Args:
        config: 애플리케이션 설정
        event_bus: 공유 이벤트 버스
        tags: 태그 정의 리스트
        demo_mode: 데모 모드 여부

    Returns:
        생성된 파이프라인
    """
    logger = LoggerFactory.get_system_logger()

    # BLE 멀티디바이스 판별
    protocol_type = (config.collector.protocol.type.lower()
                     if config.collector.protocol else "")
    has_ble_devices = (protocol_type == "ble" and
                       bool(config.collector.devices))
    has_ble_mac = (protocol_type == "ble" and
                   any(t.mac_address for t in tags))

    # 데모 모드
    if demo_mode:
        logger.info("Running in DEMO mode")
        collector = ComponentFactory._create_demo_collector(config, event_bus)
        from src.processors.base import GenericProcessor
        processor = GenericProcessor(f"{config.collector.name}_processor")
        publisher = ComponentFactory._create_demo_publisher(config)
    else:
        # 실제 모드
        if has_ble_devices or has_ble_mac:
            from src.collectors.ble import BleMultiCollector
            collector = BleMultiCollector(
                name=config.collector.name,
                config=config.collector,
                tags=tags,
                event_bus=event_bus,
            )
        else:
            collector = ComponentFactory.create_collector(config, event_bus)
        processor = ComponentFactory.create_processor(config)
        publishers = ComponentFactory.create_publishers(config)

        if not publishers:
            publisher = ComponentFactory._create_demo_publisher(config)
        elif len(publishers) == 1:
            publisher = publishers[0]
        else:
            # 여러 publisher → MultiPublisher로 래핑
            publisher = MultiPublisher(
                name=f"{config.collector.name}_multi_publisher",
                publishers=publishers,
                config=config.publisher,
            )

    logger.info(f"Created Collector: {collector.__class__.__name__}")
    logger.info(f"Created Processor: {processor.__class__.__name__}")
    if isinstance(publisher, MultiPublisher):
        for p in publisher._publishers:
            logger.info(f"Created Publisher: {p.__class__.__name__}")
    else:
        logger.info(f"Created Publisher: {publisher.__class__.__name__}")

    # 파이프라인 생성
    is_multi = config.collector.devices_file or has_ble_devices or has_ble_mac
    pipeline_name = (
        f"Pipeline_{config.collector.name}"
        if is_multi
        else f"Pipeline_PLC{config.collector.plc_id}"
    )
    pipeline = Pipeline(
        name=pipeline_name,
        collector=collector,
        processor=processor,
        publisher=publisher,
        buffer_config=config.buffer,
        tags=tags,
        event_bus=event_bus,
    )

    return pipeline


# ============================================================================
# Main
# ============================================================================

async def _load_config_from_db(
    reader, collector_key: str
) -> Tuple[AppConfig, List[TagDefinition]]:
    """config DB(neuroforge_config)에서 설정+태그를 로드.

    접속/조회 실패 시 지수 백오프(최대 30초)로 영구 재시도한다.
    (config 없이 수집은 무의미 → 빈 config로 시작하지 않고 DB 붙을 때까지 대기)
    로깅이 아직 초기화되기 전이므로 stderr로 출력한다.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            await reader.connect()
            collector_row, group_rows, device_rows, tag_rows = \
                await reader.load(collector_key)
            config = ConfigLoader.build_config_from_db(
                collector_row, group_rows, device_rows)
            tags = ConfigLoader.build_tags_from_db(
                tag_rows, collector_row.get('device_type', 'plc'))
            await reader.close()
            return config, tags
        except Exception as e:
            backoff = min(2 ** min(attempt, 5), 30)
            print(
                f"[config-db] load failed (attempt {attempt}): {e} "
                f"— retry in {backoff}s", file=sys.stderr)
            try:
                await reader.close()
            except Exception:
                pass
            await asyncio.sleep(backoff)


async def main(args: argparse.Namespace) -> int:
    """
    메인 실행 함수.

    Args:
        args: 명령행 인수

    Returns:
        종료 코드 (0: 성공, 1: 오류)
    """
    # 설정 로드 (config 소스: db 또는 file)
    from src.core.config_db import CollectorConfigDbReader, config_source

    db_tags: Optional[List[TagDefinition]] = None

    if config_source() == "db":
        collector_key = os.environ.get("COLLECTOR_KEY", "").strip()
        if not collector_key:
            print(
                "Error: CONFIG_SOURCE=db requires COLLECTOR_KEY env var",
                file=sys.stderr)
            return 1
        config, db_tags = await _load_config_from_db(
            CollectorConfigDbReader(), collector_key)
        config_source_label = f"config DB (collector_key={collector_key})"
    else:
        config_path = Path(args.config)
        if not config_path.exists():
            print(f"Error: Config file not found: {config_path}", file=sys.stderr)
            return 1
        try:
            config = ConfigLoader.load(config_path)
        except Exception as e:
            print(f"Error: Failed to load config: {e}", file=sys.stderr)
            return 1
        config_source_label = str(config_path)

    # 로그 레벨 오버라이드
    if args.log_level:
        config.logging.level = args.log_level

    # 로깅 초기화
    setup_logging(config.logging)
    logger = LoggerFactory.get_system_logger()

    logger.info("=" * 60)
    logger.info("NeuroForge Collector Starting...")
    logger.info("=" * 60)

    # 버전 정보 로깅
    log_version_info(logger)
    logger.info("-" * 60)

    # 설정 검증
    errors = ConfigLoader.validate_config(config)
    if errors:
        logger.error("Configuration errors:")
        for error in errors:
            logger.error(f"  - {error}")
        return 1

    logger.info(f"Configuration loaded from: {config_source_label}")
    logger.info(f"PLC ID: {config.collector.plc_id}")
    logger.info(f"Collector Name: {config.collector.name}")
    logger.info(f"Protocol: {config.collector.protocol.type if config.collector.protocol else 'N/A'}")
    if config.collector.protocol:
        logger.info(f"  - Host: {config.collector.protocol.host}:{config.collector.protocol.port}")
        if config.collector.protocol.extra:
            extra = config.collector.protocol.extra
            if 'plc_series' in extra:
                logger.info(f"  - PLC Series: {extra.get('plc_series')}")
            if 'frame_type' in extra:
                logger.info(f"  - Frame Type: {extra.get('frame_type')}")
    logger.info(f"Buffer Size: {config.buffer.max_size}")

    # Publisher 상태 로깅
    logger.info("Publishers:")
    rmq_config = config.publisher.rabbitmq
    if rmq_config.enabled:
        logger.info(f"  - RabbitMQ: ENABLED")
        logger.info(f"      Host: {rmq_config.host}:{rmq_config.port}")
        logger.info(f"      Exchange: {rmq_config.exchange_name}")
        logger.info(f"      Compression: {rmq_config.compression}")
    else:
        logger.info(f"  - RabbitMQ: DISABLED (demo publisher fallback)")

    # Dry-run 모드
    if args.dry_run:
        logger.info("Dry-run mode: Configuration is valid")
        return 0

    # 태그 로드 (db 소스면 이미 로드됨)
    tags: List[TagDefinition] = []
    tags_path = Path(config.collector.tags_file)
    if db_tags is not None:
        tags = db_tags
        groups = {}
        for tag in tags:
            groups[tag.collection_group] = groups.get(tag.collection_group, 0) + 1
        logger.info(f"Loaded {len(tags)} tags from config DB")
        for group, count in groups.items():
            logger.info(f"  - Group '{group}': {count} tags")
    elif tags_path.exists():
        try:
            # BLE devices 설정이 있으면 BLE 전용 로더 사용
            if config.collector.devices:
                tags = ConfigLoader.load_ble_tags(tags_path, config.collector.devices)
            else:
                tags = ConfigLoader.load_tags(tags_path)
            logger.info(f"Loaded {len(tags)} tags from: {tags_path}")

            # 그룹별 태그 수 로깅
            groups = {}
            for tag in tags:
                groups[tag.collection_group] = groups.get(tag.collection_group, 0) + 1
            for group, count in groups.items():
                logger.info(f"  - Group '{group}': {count} tags")
        except Exception as e:
            logger.warning(f"Failed to load tags: {e}")

    # 파이프라인 매니저 생성
    event_bus = EventBus(enable_history=True)
    manager = PipelineManager(shared_event_bus=event_bus)

    # 파이프라인 생성
    pipeline = await create_pipeline(
        config=config,
        event_bus=event_bus,
        tags=tags,
        demo_mode=args.demo,
    )
    manager.add_pipeline(pipeline)

    # 시그널 핸들러 설정
    manager.setup_signal_handlers()

    # Status API 서비스 (CollectorHub Agent 연동용)
    status_api = None
    status_api_enabled = os.environ.get("STATUS_API_ENABLED", "false").lower() == "true"
    status_api_port = int(os.environ.get("STATUS_API_PORT", "8090"))

    if status_api_enabled:
        try:
            from .services.status_api import StatusAPIService
            status_api = StatusAPIService(
                pipeline_manager=manager,
                host="0.0.0.0",
                port=status_api_port,
            )
            await status_api.start()
            logger.info(f"Status API enabled on port {status_api_port}")
        except Exception as e:
            logger.warning(f"Failed to start Status API: {e}")

    try:
        # 파이프라인 시작
        await manager.start_all()
        logger.info("All pipelines started. Press Ctrl+C to stop.")

        # 종료 시그널 대기 (Windows에서는 시그널 핸들러가 없으므로 KeyboardInterrupt로 종료)
        try:
            await manager.wait_for_shutdown()
        except asyncio.CancelledError:
            logger.info("Shutdown signal received")

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")

    except Exception as e:
        logger.exception(f"Runtime error: {e}")
        return 1

    finally:
        # 정리 (이벤트 루프 내에서 실행되므로 await 가능)
        logger.info("Shutting down...")

        # Status API 중지
        if status_api and status_api.is_running:
            try:
                await status_api.stop()
            except Exception as e:
                logger.warning(f"Error stopping Status API: {e}")

        try:
            await manager.stop_all()
        except Exception as e:
            logger.warning(f"Error during shutdown: {e}")
        logger.info("Shutdown complete")

    return 0


def run():
    """동기 엔트리포인트."""
    args = parse_args()
    exit_code = 0

    try:
        # Windows에서는 SelectorEventLoop를 사용하도록 이미 설정됨
        exit_code = asyncio.run(main(args))
    except KeyboardInterrupt:
        # KeyboardInterrupt가 asyncio.run 외부로 전파된 경우
        # (main에서 이미 처리되었으므로 여기서는 정상 종료로 처리)
        exit_code = 0
    except SystemExit as e:
        exit_code = e.code if isinstance(e.code, int) else 1
    except Exception as e:
        print(f"\nUnexpected error: {e}")
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    run()
