"""
Simple Collector 메인 엔트리포인트
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
        description="Simple Collector - Industrial Data Collection Framework",
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

        # Database Publisher
        if config.publisher.database.enabled:
            try:
                from src.publishers.database import DatabasePublisher
                publishers.append(DatabasePublisher(
                    name=f"{config.collector.name}_db_publisher",
                    db_config=config.publisher.database,
                    publisher_config=config.publisher,
                ))
            except ImportError as e:
                LoggerFactory.get_system_logger().warning(
                    f"Database publisher not available: {e}"
                )

        # MQTT Publisher
        if config.publisher.mqtt.enabled:
            try:
                from src.publishers.mqtt import MqttPublisher
                publishers.append(MqttPublisher(
                    name=f"{config.collector.name}_mqtt_publisher",
                    mqtt_config=config.publisher.mqtt,
                    publisher_config=config.publisher,
                ))
            except ImportError as e:
                LoggerFactory.get_system_logger().warning(
                    f"MQTT publisher not available: {e}"
                )

        # JSON File Publisher
        if config.publisher.json_file.enabled:
            try:
                from src.publishers.json_file_publisher import JsonFilePublisher
                publishers.append(JsonFilePublisher(
                    name=f"{config.collector.name}_json_publisher",
                    json_config=config.publisher.json_file,
                    publisher_config=config.publisher,
                ))
            except ImportError as e:
                LoggerFactory.get_system_logger().warning(
                    f"JSON file publisher not available: {e}"
                )

        # RabbitMQ Publisher
        if config.publisher.rabbitmq.enabled:
            try:
                from src.publishers.rabbitmq import RabbitMQPublisher
                publishers.append(RabbitMQPublisher(
                    name=f"{config.collector.name}_rmq_publisher",
                    rabbitmq_config=config.publisher.rabbitmq,
                    publisher_config=config.publisher,
                ))
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

    # 데모 모드
    if demo_mode:
        logger.info("Running in DEMO mode")
        collector = ComponentFactory._create_demo_collector(config, event_bus)
        from src.processors.base import GenericProcessor
        processor = GenericProcessor(f"{config.collector.name}_processor")
        publisher = ComponentFactory._create_demo_publisher(config)
    else:
        # 실제 모드
        collector = ComponentFactory.create_collector(config, event_bus)
        processor = ComponentFactory.create_processor(config)
        publishers = ComponentFactory.create_publishers(config)
        publisher = publishers[0] if publishers else ComponentFactory._create_demo_publisher(config)

    logger.info(f"Created Collector: {collector.__class__.__name__}")
    logger.info(f"Created Processor: {processor.__class__.__name__}")
    logger.info(f"Created Publisher: {publisher.__class__.__name__}")

    # 파이프라인 생성
    pipeline = Pipeline(
        name=f"Pipeline_PLC{config.collector.plc_id}",
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

async def main(args: argparse.Namespace) -> int:
    """
    메인 실행 함수.

    Args:
        args: 명령행 인수

    Returns:
        종료 코드 (0: 성공, 1: 오류)
    """
    # 설정 로드
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: Config file not found: {config_path}", file=sys.stderr)
        return 1

    try:
        config = ConfigLoader.load(config_path)
    except Exception as e:
        print(f"Error: Failed to load config: {e}", file=sys.stderr)
        return 1

    # 로그 레벨 오버라이드
    if args.log_level:
        config.logging.level = args.log_level

    # 로깅 초기화
    setup_logging(config.logging)
    logger = LoggerFactory.get_system_logger()

    logger.info("=" * 60)
    logger.info("Simple Collector Starting...")
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

    logger.info(f"Configuration loaded from: {config_path}")
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
    db_config = config.publisher.database
    if db_config.enabled:
        logger.info(f"  - Database: ENABLED")
        logger.info(f"      Host: {db_config.host}:{db_config.port}/{db_config.database}")
        if db_config.master_sync_enabled:
            logger.info(f"      Master Sync: {db_config.master_sync_schema}")
    else:
        logger.info(f"  - Database: DISABLED")

    mqtt_config = config.publisher.mqtt
    if mqtt_config.enabled:
        logger.info(f"  - MQTT: ENABLED")
        logger.info(f"      Host: {mqtt_config.host}:{mqtt_config.port}")
        if mqtt_config.topic_prefix:
            logger.info(f"      Topic: {mqtt_config.topic_prefix}")
    else:
        logger.info(f"  - MQTT: DISABLED")

    json_file_config = config.publisher.json_file
    if json_file_config.enabled:
        logger.info(f"  - JSON File: ENABLED")
        logger.info(f"      Path: {json_file_config.file_path}")
        logger.info(f"      Mode: {json_file_config.mode}")
    else:
        logger.info(f"  - JSON File: DISABLED")

    rmq_config = config.publisher.rabbitmq
    if rmq_config.enabled:
        logger.info(f"  - RabbitMQ: ENABLED")
        logger.info(f"      Host: {rmq_config.host}:{rmq_config.port}")
        logger.info(f"      Exchange: {rmq_config.exchange_name}")
        logger.info(f"      Compression: {rmq_config.compression}")
    else:
        logger.info(f"  - RabbitMQ: DISABLED")

    # 모두 비활성화면 Log Publisher 사용 안내
    if not db_config.enabled and not mqtt_config.enabled and not json_file_config.enabled and not rmq_config.enabled:
        logger.info(f"  - Log: ENABLED (fallback)")

    # Dry-run 모드
    if args.dry_run:
        logger.info("Dry-run mode: Configuration is valid")
        return 0

    # 태그 로드
    tags: List[TagDefinition] = []
    tags_path = Path(config.collector.tags_file)
    if tags_path.exists():
        try:
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

    # 마스터 테이블 동기화 (DB 활성화 + 동기화 활성화 시)
    db_config = config.publisher.database
    if db_config.enabled and db_config.master_sync_enabled:
        from .services.master_sync import MasterSyncService

        logger.info(f"Starting master table sync to schema '{db_config.master_sync_schema}'...")
        sync_service = MasterSyncService(
            db_config=db_config,
            schema=db_config.master_sync_schema,
        )

        if await sync_service.connect():
            try:
                # 테이블 존재 확인
                if await sync_service.ensure_tables_exist():
                    # PLC 마스터 동기화
                    await sync_service.sync_plc(config.collector)

                    # 태그 마스터 동기화
                    if tags:
                        synced = await sync_service.sync_tags(
                            config.collector.plc_id, tags
                        )
                        logger.info(f"Master sync completed: {synced} tags synced")
                else:
                    logger.warning(
                        "Master tables not found, skipping sync. "
                        "Create tables using scripts/init-db.sql"
                    )
            except Exception as e:
                logger.warning(f"Master sync failed (non-fatal): {e}")
            finally:
                await sync_service.disconnect()
        else:
            logger.warning("Could not connect for master sync, continuing without sync")

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
