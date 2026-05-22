"""
Component Factory (Registry-based)
===================================

Registry를 사용하여 동적으로 컴포넌트를 생성합니다.
설치된 프로토콜만 사용 가능하며, 미설치 시 명확한 에러 메시지 제공.

Usage:
    from src.core.factory import ComponentFactory

    collector = ComponentFactory.create_collector(config, event_bus)
    processor = ComponentFactory.create_processor(config)
    publishers = ComponentFactory.create_publishers(config)
"""

import logging
from typing import List, Optional

from .registry import ProtocolRegistry, PublisherRegistry

logger = logging.getLogger(__name__)


class ComponentFactory:
    """컴포넌트 팩토리 (Registry 기반)."""

    @staticmethod
    def create_collector(config, event_bus):
        """
        Collector 생성.

        Args:
            config: AppConfig 인스턴스
            event_bus: EventBus 인스턴스

        Returns:
            Collector 인스턴스

        Raises:
            RuntimeError: 프로토콜을 사용할 수 없는 경우
        """
        protocol_type = config.collector.protocol.type.lower() if config.collector.protocol else "demo"

        # Registry에서 Collector 클래스 조회
        collector_cls = ProtocolRegistry.get_collector(protocol_type)

        if collector_cls is None:
            available = ProtocolRegistry.list_available()
            raise RuntimeError(
                f"Protocol '{protocol_type}' is not available.\n"
                f"Available protocols: {available}\n"
                f"Install with: pip install neuroforge-collector[{protocol_type}]"
            )

        return collector_cls(
            plc_id=config.collector.plc_id,
            name=config.collector.name,
            config=config.collector,
            event_bus=event_bus,
        )

    @staticmethod
    def create_processor(config):
        """
        Processor 생성.

        Args:
            config: AppConfig 인스턴스

        Returns:
            Processor 인스턴스
        """
        protocol_type = config.collector.protocol.type.lower() if config.collector.protocol else "demo"

        processor_cls = ProtocolRegistry.get_processor(protocol_type)

        if processor_cls is None:
            # Fallback to generic processor
            from src.processors.base import GenericProcessor
            return GenericProcessor(f"{config.collector.name}_processor")

        # 프로토콜별 설정
        extra = config.collector.protocol.extra if config.collector.protocol else {}

        # MC Protocol
        if protocol_type in ('mc_protocol', 'melsec', 'mc'):
            return processor_cls(
                name=f"{config.collector.name}_processor",
                word_order=extra.get('word_order', 'little'),
            )

        # Modbus
        elif protocol_type == 'modbus':
            return processor_cls(
                name=f"{config.collector.name}_processor",
                byte_order=extra.get('byte_order', 'big'),
                word_order=extra.get('word_order', 'big'),
            )

        # Default
        else:
            return processor_cls(name=f"{config.collector.name}_processor")

    @staticmethod
    def create_publishers(config) -> List:
        """
        Publisher 생성.

        Args:
            config: AppConfig 인스턴스

        Returns:
            Publisher 인스턴스 리스트
        """
        publishers = []

        # RabbitMQ Publisher
        if config.publisher.rabbitmq.enabled:
            publisher_cls = PublisherRegistry.get_publisher('rabbitmq')

            if publisher_cls is None:
                logger.warning(
                    "RabbitMQ publisher not available. "
                    "Install with: pip install neuroforge-collector[rabbitmq]"
                )
            else:
                publishers.append(publisher_cls(
                    name=f"{config.collector.name}_rmq_publisher",
                    config=config.publisher.rabbitmq,
                ))

        return publishers

    @staticmethod
    def check_requirements(config) -> List[str]:
        """
        설정에 필요한 패키지 확인.

        Returns:
            누락된 패키지 설치 명령어 리스트
        """
        missing = []

        # 프로토콜 확인
        if config.collector.protocol:
            protocol = config.collector.protocol.type.lower()
            if not ProtocolRegistry.is_available(protocol):
                missing.append(f"pip install neuroforge-collector[{protocol}]")

        # RabbitMQ 확인
        if config.publisher.rabbitmq.enabled:
            if not PublisherRegistry.is_available('rabbitmq'):
                missing.append("pip install neuroforge-collector[rabbitmq]")

        return missing
