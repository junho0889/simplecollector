"""
파이프라인 관리자 모듈
======================

데이터 수집 파이프라인의 생명주기를 관리합니다.

Architecture:
    PipelineManager는 여러 Pipeline을 관리합니다.
    각 Pipeline은 하나의 PLC에 대한 완전한 데이터 흐름을 담당합니다.

    ┌───────────────────────────────────────────────────────────────────┐
    │                        PipelineManager                             │
    │                                                                    │
    │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
    │  │ Pipeline 1  │  │ Pipeline 2  │  │ Pipeline 3  │  ...          │
    │  │   (PLC 1)   │  │   (PLC 2)   │  │   (PLC 3)   │               │
    │  └─────────────┘  └─────────────┘  └─────────────┘               │
    │         │               │               │                         │
    │         ▼               ▼               ▼                         │
    │  ┌─────────────────────────────────────────────────────────────┐ │
    │  │                      Shared EventBus                         │ │
    │  └─────────────────────────────────────────────────────────────┘ │
    │                              │                                    │
    │         ┌────────────────────┼────────────────────┐              │
    │         ▼                    ▼                    ▼              │
    │  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐        │
    │  │  Collector  │     │  Processor  │     │  Publisher  │        │
    │  │    Task     │     │    Task     │     │    Task     │        │
    │  └─────────────┘     └─────────────┘     └─────────────┘        │
    └───────────────────────────────────────────────────────────────────┘

Pipeline Structure:
    각 Pipeline은 다음 컴포넌트로 구성됩니다:
    - Collector: 데이터 수집 (프로토콜별)
    - Processor: 데이터 처리 (파싱, 스케일링)
    - Buffer: 데이터 임시 저장
    - Publisher: 데이터 발행 (RabbitMQ)

Usage:
    # 컴포넌트 생성
    collector = ModbusCollector(plc_id=1, name="PLC1", config=collector_config)
    processor = ModbusProcessor("processor1")
    publisher = RabbitMQPublisher("rmq_publisher", publisher_config)

    # 파이프라인 생성
    pipeline = Pipeline(
        name="PLC1_Pipeline",
        collector=collector,
        processor=processor,
        publisher=publisher,
        buffer_config=buffer_config,
    )

    # 매니저로 관리
    manager = PipelineManager()
    manager.add_pipeline(pipeline)

    await manager.start_all()
    # ... 운영 중 ...
    await manager.stop_all()
"""

import asyncio
import signal
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from ..core.interfaces import TagDefinition
from ..core.buffer import DataBuffer
from ..core.events import EventBus, Event, EventType
from ..core.config import AppConfig, BufferConfig, CollectorConfig
from ..collectors.base import BaseCollector
from ..processors.base import BaseProcessor
from ..publishers.base import BasePublisher
from ..utils.logging import LoggerFactory

logger = LoggerFactory.get_system_logger()


@dataclass
class PipelineStatus:
    """
    파이프라인 상태 정보.

    Attributes:
        name: 파이프라인 이름
        is_running: 실행 중 여부
        collector_state: 수집기 상태
        processor_running: 처리기 실행 여부
        publisher_connected: 발행기 연결 여부
        buffer_size: 현재 버퍼 크기
        last_update: 마지막 업데이트 시간
    """
    name: str
    is_running: bool
    collector_state: str
    processor_running: bool
    publisher_connected: bool
    buffer_size: int
    last_update: datetime


class Pipeline:
    """
    단일 데이터 파이프라인.

    하나의 PLC에 대한 완전한 데이터 수집 흐름을 관리합니다.
    Collector → Processor → Buffer → Publisher 구조입니다.

    Attributes:
        name: 파이프라인 이름
        collector: 데이터 수집기
        processor: 데이터 처리기
        publisher: 데이터 발행기
        buffer: 데이터 버퍼
        event_bus: 이벤트 버스

    Example:
        pipeline = Pipeline(
            name="PLC1_Pipeline",
            collector=modbus_collector,
            processor=modbus_processor,
            publisher=db_publisher,
            buffer_config=BufferConfig(max_size=10000),
        )

        await pipeline.start()
    """

    def __init__(
        self,
        name: str,
        collector: BaseCollector,
        processor: BaseProcessor,
        publisher: BasePublisher,
        buffer_config: BufferConfig,
        tags: Optional[List[TagDefinition]] = None,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Args:
            name: 파이프라인 이름
            collector: 데이터 수집기
            processor: 데이터 처리기
            publisher: 데이터 발행기
            buffer_config: 버퍼 설정
            tags: 태그 정의 리스트 (선택)
            event_bus: 외부 이벤트 버스 (선택, 없으면 자체 생성)
        """
        self._name = name
        self._collector = collector
        self._processor = processor
        self._publisher = publisher
        self._is_running = False

        # 이벤트 버스 (공유 또는 자체)
        self._event_bus = event_bus or EventBus()
        self._owns_event_bus = event_bus is None

        # 버퍼 생성
        persist_path = None
        if buffer_config.persist_on_shutdown:
            persist_path = Path(buffer_config.persist_path)
            persist_path.parent.mkdir(parents=True, exist_ok=True)

        self._buffer = DataBuffer(
            max_size=buffer_config.max_size,
            batch_size=buffer_config.batch_size,
            threshold_ratio=buffer_config.threshold_ratio,
            drop_oldest=buffer_config.drop_oldest,
            persist_path=persist_path,
        )

        # 컴포넌트에 이벤트 버스/버퍼 주입
        self._collector.set_on_data_callback(self._on_data_collected)
        self._processor.set_buffer(self._buffer)
        self._processor.set_event_bus(self._event_bus)
        self._publisher.set_buffer(self._buffer)
        self._publisher.set_event_bus(self._event_bus)
        self._buffer.set_event_bus(self._event_bus)

        # 태그 등록
        if tags:
            self._register_tags(tags)

    @property
    def name(self) -> str:
        """파이프라인 이름."""
        return self._name

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._is_running

    @property
    def event_bus(self) -> EventBus:
        """이벤트 버스."""
        return self._event_bus

    @property
    def buffer(self) -> DataBuffer:
        """데이터 버퍼."""
        return self._buffer

    def _register_tags(self, tags: List[TagDefinition]) -> None:
        """
        태그 등록.

        수집기와 처리기에 태그를 그룹별로 등록합니다.

        Args:
            tags: 태그 정의 리스트
        """
        # 그룹별로 분류
        groups: Dict[str, List[TagDefinition]] = {}
        for tag in tags:
            group = tag.collection_group
            if group not in groups:
                groups[group] = []
            groups[group].append(tag)

        # 수집기에 등록
        for group, group_tags in groups.items():
            self._collector.register_tags(group, group_tags)

        # 처리기에 등록
        for group, group_tags in groups.items():
            self._processor.register_tags_for_group(group, group_tags)

        logger.info(
            f"[{self._name}] Registered {len(tags)} tags in {len(groups)} groups"
        )

    async def _on_data_collected(self, data) -> None:
        """
        데이터 수집 완료 콜백.

        Collector에서 직접 호출됩니다 (이벤트 버스 대신).

        Args:
            data: 수집된 Raw 데이터
        """
        # 처리기로 전달 (이벤트로)
        await self._event_bus.emit(Event(
            event_type=EventType.DATA_COLLECTED,
            source=self._collector.name,
            data=data,
        ))

    async def start(self) -> None:
        """
        파이프라인 시작.

        모든 컴포넌트를 순서대로 시작합니다.
        """
        if self._is_running:
            logger.warning(f"[{self._name}] Pipeline already running")
            return

        logger.info(f"[{self._name}] Starting pipeline...")

        # 이벤트 버스 시작
        if self._owns_event_bus:
            await self._event_bus.start()

        # 컴포넌트 시작 (순서 중요)
        await self._processor.start()
        await self._publisher.start()
        await self._collector.start()

        self._is_running = True

        await self._event_bus.emit(Event(
            event_type=EventType.PIPELINE_STARTED,
            source=self._name,
        ))

        logger.info(f"[{self._name}] Pipeline started successfully")

    async def stop(self) -> None:
        """
        파이프라인 중지.

        모든 컴포넌트를 역순으로 중지합니다.
        """
        if not self._is_running:
            return

        logger.info(f"[{self._name}] Stopping pipeline...")

        self._is_running = False

        # 컴포넌트 중지 (역순)
        await self._collector.stop()
        await self._processor.stop()
        await self._publisher.stop()

        # 버퍼 종료
        await self._buffer.shutdown()

        # 이벤트 버스 중지
        if self._owns_event_bus:
            await self._event_bus.stop()

        await self._event_bus.emit(Event(
            event_type=EventType.PIPELINE_STOPPED,
            source=self._name,
        ))

        logger.info(f"[{self._name}] Pipeline stopped")

    def get_status(self) -> PipelineStatus:
        """
        파이프라인 상태 조회.

        Returns:
            PipelineStatus 객체
        """
        return PipelineStatus(
            name=self._name,
            is_running=self._is_running,
            collector_state=self._collector.state.value,
            processor_running=self._processor.is_running,
            publisher_connected=self._publisher.is_connected,
            buffer_size=self._buffer.size,
            last_update=datetime.now(),
        )

    def get_stats(self) -> Dict[str, Any]:
        """
        파이프라인 상세 통계 조회.

        Returns:
            통계 딕셔너리
        """
        return {
            "name": self._name,
            "is_running": self._is_running,
            "collector": self._collector.get_stats(),
            "processor": self._processor.get_stats(),
            "publisher": self._publisher.get_stats(),
            "buffer": {
                "size": self._buffer.size,
                "stats": self._buffer.stats.__dict__,
            },
            "event_bus": {
                "queue_size": self._event_bus.queue_size,
                "handlers": self._event_bus.handler_count,
            },
        }


class PipelineManager:
    """
    다중 파이프라인 관리자.

    여러 Pipeline의 생명주기를 관리하고 모니터링합니다.

    Features:
        - 다중 파이프라인 관리
        - 일괄 시작/중지
        - 상태 모니터링
        - 시그널 핸들링 (SIGTERM, SIGINT)

    Example:
        manager = PipelineManager()

        # 파이프라인 추가
        manager.add_pipeline(pipeline1)
        manager.add_pipeline(pipeline2)

        # 모두 시작
        await manager.start_all()

        # 시그널 대기 (SIGTERM, SIGINT)
        await manager.wait_for_shutdown()

        # 모두 중지
        await manager.stop_all()
    """

    def __init__(self, shared_event_bus: Optional[EventBus] = None):
        """
        Args:
            shared_event_bus: 공유 이벤트 버스 (선택)
        """
        self._pipelines: Dict[str, Pipeline] = {}
        self._shared_event_bus = shared_event_bus
        self._shutdown_event = asyncio.Event()
        self._is_running = False

    def add_pipeline(self, pipeline: Pipeline) -> None:
        """
        파이프라인 추가.

        Args:
            pipeline: 추가할 파이프라인

        Raises:
            ValueError: 동일 이름의 파이프라인이 이미 존재할 때
        """
        if pipeline.name in self._pipelines:
            raise ValueError(f"Pipeline '{pipeline.name}' already exists")

        self._pipelines[pipeline.name] = pipeline
        logger.info(f"Added pipeline: {pipeline.name}")

    def remove_pipeline(self, name: str) -> Optional[Pipeline]:
        """
        파이프라인 제거.

        Args:
            name: 제거할 파이프라인 이름

        Returns:
            제거된 파이프라인 또는 None
        """
        pipeline = self._pipelines.pop(name, None)
        if pipeline:
            logger.info(f"Removed pipeline: {name}")
        return pipeline

    def get_pipeline(self, name: str) -> Optional[Pipeline]:
        """
        파이프라인 조회.

        Args:
            name: 파이프라인 이름

        Returns:
            파이프라인 또는 None
        """
        return self._pipelines.get(name)

    async def start_all(self) -> None:
        """모든 파이프라인 시작."""
        if self._is_running:
            logger.warning("PipelineManager already running")
            return

        logger.info(f"Starting {len(self._pipelines)} pipelines...")

        # 공유 이벤트 버스 시작
        if self._shared_event_bus:
            await self._shared_event_bus.start()

        # 병렬로 시작
        start_tasks = [
            pipeline.start()
            for pipeline in self._pipelines.values()
        ]

        await asyncio.gather(*start_tasks, return_exceptions=True)

        self._is_running = True
        logger.info("All pipelines started")

    async def stop_all(self) -> None:
        """모든 파이프라인 중지."""
        if not self._is_running:
            return

        logger.info(f"Stopping {len(self._pipelines)} pipelines...")

        # 병렬로 중지
        stop_tasks = [
            pipeline.stop()
            for pipeline in self._pipelines.values()
        ]

        await asyncio.gather(*stop_tasks, return_exceptions=True)

        # 공유 이벤트 버스 중지
        if self._shared_event_bus:
            await self._shared_event_bus.stop()

        self._is_running = False
        logger.info("All pipelines stopped")

    async def start_pipeline(self, name: str) -> bool:
        """
        특정 파이프라인 시작.

        Args:
            name: 파이프라인 이름

        Returns:
            시작 성공 여부
        """
        pipeline = self._pipelines.get(name)
        if not pipeline:
            logger.error(f"Pipeline not found: {name}")
            return False

        try:
            await pipeline.start()
            return True
        except Exception as e:
            logger.exception(f"Failed to start pipeline '{name}': {e}")
            return False

    async def stop_pipeline(self, name: str) -> bool:
        """
        특정 파이프라인 중지.

        Args:
            name: 파이프라인 이름

        Returns:
            중지 성공 여부
        """
        pipeline = self._pipelines.get(name)
        if not pipeline:
            logger.error(f"Pipeline not found: {name}")
            return False

        try:
            await pipeline.stop()
            return True
        except Exception as e:
            logger.exception(f"Failed to stop pipeline '{name}': {e}")
            return False

    def setup_signal_handlers(self) -> None:
        """
        시그널 핸들러 설정.

        SIGTERM, SIGINT 시그널을 받으면 shutdown_event를 설정합니다.
        """
        loop = asyncio.get_event_loop()

        def signal_handler(sig):
            logger.info(f"Received signal {sig.name}, initiating shutdown...")
            self._shutdown_event.set()

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, signal_handler, sig)
            logger.debug("Signal handlers registered")
        except NotImplementedError:
            # Windows에서는 add_signal_handler가 지원되지 않음
            logger.debug("Signal handlers not supported on this platform")

    async def wait_for_shutdown(self) -> None:
        """
        종료 시그널 대기.

        SIGTERM 또는 SIGINT가 수신될 때까지 대기합니다.
        """
        await self._shutdown_event.wait()

    def get_all_status(self) -> Dict[str, PipelineStatus]:
        """
        모든 파이프라인 상태 조회.

        Returns:
            이름 → 상태 딕셔너리
        """
        return {
            name: pipeline.get_status()
            for name, pipeline in self._pipelines.items()
        }

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        모든 파이프라인 통계 조회.

        Returns:
            이름 → 통계 딕셔너리
        """
        return {
            name: pipeline.get_stats()
            for name, pipeline in self._pipelines.items()
        }

    @property
    def pipeline_count(self) -> int:
        """등록된 파이프라인 수."""
        return len(self._pipelines)

    @property
    def pipeline_names(self) -> List[str]:
        """등록된 파이프라인 이름 목록."""
        return list(self._pipelines.keys())
