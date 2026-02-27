"""
기본 발행기 모듈
================

모든 데이터 발행기의 기본 클래스를 제공합니다.
버퍼에서 데이터를 가져와 외부 시스템으로 전송합니다.

Architecture:
    BasePublisher는 버퍼를 모니터링하며 데이터를 전송합니다:

    1. 버퍼에서 배치 데이터 추출
    2. 전송 준비 (직렬화, 암호화 등)
    3. 외부 시스템으로 전송
    4. 성공/실패 처리
    5. 실패 시 버퍼에 되돌리기

    ┌──────────────────────────────────────┐
    │            BasePublisher              │
    │                                        │
    │  ┌─────────────────────────────────┐  │
    │  │      Publish Loop (Task)         │  │
    │  │                                   │  │
    │  │  while running:                   │  │
    │  │    batch = buffer.get_batch()     │  │
    │  │    success = publish(batch)       │  │
    │  │    if not success:                │  │
    │  │      buffer.put_front(batch)      │  │
    │  └─────────────────────────────────┘  │
    │                                        │
    │  ┌────────────┐    ┌────────────────┐ │
    │  │   Buffer   │───▶│ External System│ │
    │  └────────────┘    │   (RabbitMQ)    │ │
    │                    └────────────────┘ │
    └──────────────────────────────────────┘

Extension:
    각 발행 대상별 구현체는 BasePublisher를 상속받아 구현합니다.

    class RabbitMQPublisher(BasePublisher):
        async def _do_connect(self) -> bool:
            self._connection = await aio_pika.connect(...)
            return True

        async def _do_publish(self, data: List[ProcessedData]) -> bool:
            await self._exchange.publish(message, routing_key)
            return True

Example:
    publisher = MyPublisher("publisher1", config)
    publisher.set_buffer(buffer)
    publisher.set_event_bus(event_bus)

    await publisher.start()
"""

import asyncio
from abc import abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
import logging

from ..core.interfaces import IPublisher, ProcessedData
from ..core.buffer import DataBuffer
from ..core.events import EventBus, Event, EventType
from ..core.config import PublisherConfig
from ..utils.logging import LoggerFactory

logger = LoggerFactory.get_publish_logger()


class BasePublisher(IPublisher):
    """
    기본 데이터 발행기 클래스.

    버퍼에서 데이터를 가져와 외부 시스템으로 전송합니다.

    Features:
        - 배치 처리
        - 자동 재시도
        - 자동 재연결
        - 실패 데이터 보존

    Attributes:
        name: 발행기 이름
        config: 발행기 설정
        buffer: 데이터 버퍼
        event_bus: 이벤트 버스

    Abstract Methods (구현 필요):
        _do_connect: 실제 연결 로직
        _do_disconnect: 실제 연결 해제 로직
        _do_publish: 실제 데이터 전송 로직
    """

    def __init__(self, name: str, config: PublisherConfig):
        """
        Args:
            name: 발행기 이름 (로깅용)
            config: 발행기 설정
        """
        super().__init__(name)

        self._config = config
        self._buffer: Optional[DataBuffer] = None
        self._event_bus: Optional[EventBus] = None

        # 태스크 관리
        self._publish_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        # 통계
        self._total_published = 0
        self._total_failed = 0
        self._total_retries = 0
        self._last_publish_time: Optional[datetime] = None
        self._consecutive_failures = 0

    def set_buffer(self, buffer: DataBuffer) -> None:
        """
        데이터 버퍼 설정.

        Args:
            buffer: 데이터를 가져올 버퍼
        """
        self._buffer = buffer

    def set_event_bus(self, event_bus: EventBus) -> None:
        """
        이벤트 버스 설정.

        Args:
            event_bus: 이벤트 통신용 버스
        """
        self._event_bus = event_bus

    # =========================================================================
    # Public Methods (IPublisher 구현)
    # =========================================================================

    async def connect(self) -> bool:
        """
        대상 시스템에 연결.

        Returns:
            연결 성공 여부
        """
        logger.info(f"[{self._name}] Connecting...")

        try:
            success = await self._do_connect()

            if success:
                self._is_connected = True
                self._consecutive_failures = 0
                await self._emit_event(EventType.PUBLISHER_CONNECTED)
                logger.info(f"[{self._name}] Connected successfully")
            else:
                self._is_connected = False
                await self._emit_event(EventType.PUBLISHER_ERROR, {
                    "error": "Connection failed"
                })
                logger.error(f"[{self._name}] Connection failed")

            return success

        except Exception as e:
            self._is_connected = False
            await self._emit_event(EventType.PUBLISHER_ERROR, {"error": str(e)})
            logger.exception(f"[{self._name}] Connection error: {e}")
            return False

    async def disconnect(self) -> None:
        """연결 해제."""
        logger.info(f"[{self._name}] Disconnecting...")

        try:
            await self._do_disconnect()
        except Exception as e:
            logger.warning(f"[{self._name}] Disconnect error: {e}")
        finally:
            self._is_connected = False
            await self._emit_event(EventType.PUBLISHER_DISCONNECTED)
            logger.info(f"[{self._name}] Disconnected")

    async def publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 발행.

        Args:
            data: 발행할 처리된 데이터 리스트

        Returns:
            발행 성공 여부
        """
        if not data:
            return True

        if not self._is_connected:
            logger.warning(f"[{self._name}] Cannot publish, not connected")
            return False

        try:
            success = await self._do_publish(data)

            if success:
                self._total_published += len(data)
                self._last_publish_time = datetime.now()
                self._consecutive_failures = 0

                await self._emit_event(EventType.DATA_PUBLISHED, {
                    "count": len(data),
                })

                logger.debug(f"[{self._name}] Published {len(data)} records")

            else:
                self._total_failed += len(data)
                self._consecutive_failures += 1

                await self._emit_event(EventType.PUBLISH_FAILED, {
                    "count": len(data),
                    "consecutive_failures": self._consecutive_failures,
                })

                logger.error(f"[{self._name}] Failed to publish {len(data)} records")

            return success

        except Exception as e:
            self._total_failed += len(data)
            self._consecutive_failures += 1

            await self._emit_event(EventType.PUBLISHER_ERROR, {
                "error": str(e),
                "count": len(data),
            })

            logger.exception(f"[{self._name}] Publish error: {e}")
            return False

    async def health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결이 정상이면 True
        """
        if not self._is_connected:
            return False

        try:
            return await self._do_health_check()
        except Exception as e:
            logger.warning(f"[{self._name}] Health check failed: {e}")
            return False

    async def start(self) -> None:
        """
        발행기 시작.

        연결 후 발행 루프를 시작합니다.
        """
        await super().start()

        # 연결 시도
        if not await self.connect():
            # 재연결 태스크 시작
            self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        # 발행 루프 시작
        self._publish_task = asyncio.create_task(self._publish_loop())

        await self._emit_event(EventType.PUBLISHER_STARTED)
        logger.info(f"[{self._name}] Publisher started")

    async def stop(self) -> None:
        """
        발행기 중지.

        모든 태스크를 취소하고 연결을 해제합니다.
        """
        self._is_running = False

        # 재연결 태스크 취소
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # 발행 태스크 취소
        if self._publish_task:
            self._publish_task.cancel()
            try:
                await self._publish_task
            except asyncio.CancelledError:
                pass

        # 남은 데이터 처리 시도
        await self._flush_remaining()

        # 연결 해제
        await self.disconnect()

        await self._emit_event(EventType.PUBLISHER_STOPPED)
        logger.info(f"[{self._name}] Publisher stopped")

    # =========================================================================
    # Publish Loop
    # =========================================================================

    async def _publish_loop(self) -> None:
        """
        발행 루프.

        버퍼를 모니터링하며 데이터가 있으면 발행합니다.
        """
        interval = self._config.publish_interval_ms / 1000.0

        while self._is_running:
            try:
                if not self._is_connected:
                    await asyncio.sleep(interval)
                    continue

                if not self._buffer:
                    await asyncio.sleep(interval)
                    continue

                # 버퍼에서 배치 가져오기
                batch = await self._buffer.get_batch(timeout=interval)

                if batch:
                    # 재시도 로직으로 발행
                    success = await self._publish_with_retry(batch)

                    if not success:
                        # 실패한 데이터 버퍼에 되돌리기
                        await self._buffer.put_front(batch)
                        logger.warning(
                            f"[{self._name}] Returned {len(batch)} records to buffer"
                        )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception(f"[{self._name}] Publish loop error: {e}")
                await asyncio.sleep(interval)

    async def _publish_with_retry(self, data: List[ProcessedData]) -> bool:
        """
        재시도 로직이 포함된 데이터 발행.

        Args:
            data: 발행할 데이터 리스트

        Returns:
            최종 발행 성공 여부
        """
        for attempt in range(self._config.max_retries + 1):
            success = await self.publish(data)

            if success:
                return True

            self._total_retries += 1

            if attempt < self._config.max_retries:
                logger.warning(
                    f"[{self._name}] Publish failed, retrying "
                    f"({attempt + 1}/{self._config.max_retries + 1})"
                )
                await asyncio.sleep(self._config.retry_delay_ms / 1000.0)

        return False

    # =========================================================================
    # Reconnection
    # =========================================================================

    async def _reconnect_loop(self) -> None:
        """
        재연결 루프 (지수 백오프, 최대 5초).

        연결이 끊어진 경우 지수 백오프로 재연결을 시도합니다.
        성공 시 백오프가 초기값으로 리셋됩니다.
        """
        BACKOFF_INITIAL = 1.0   # 초기 대기 (초)
        BACKOFF_MAX = 5.0       # 최대 대기 (초, 하드캡)
        BACKOFF_FACTOR = 2.0    # 배수

        backoff = BACKOFF_INITIAL
        attempt = 0

        while self._is_running:
            try:
                await asyncio.sleep(backoff)

                if not self._is_connected:
                    attempt += 1
                    logger.info(
                        f"[{self._name}] Attempting reconnection "
                        f"(attempt={attempt}, backoff={backoff:.1f}s)..."
                    )

                    if await self.connect():
                        logger.info(f"[{self._name}] Reconnected successfully")
                        backoff = BACKOFF_INITIAL
                        attempt = 0
                    else:
                        logger.warning(
                            f"[{self._name}] Reconnection failed "
                            f"(next backoff={min(backoff * BACKOFF_FACTOR, BACKOFF_MAX):.1f}s)"
                        )
                        backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] Reconnection error: {e}")
                backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX)

    # =========================================================================
    # Shutdown
    # =========================================================================

    async def _flush_remaining(self) -> None:
        """
        종료 전 남은 데이터 처리 시도.

        버퍼에 남은 데이터를 최대한 발행합니다.
        """
        if not self._buffer or not self._is_connected:
            return

        remaining_count = self._buffer.size
        if remaining_count == 0:
            return

        logger.info(
            f"[{self._name}] Flushing {remaining_count} remaining records..."
        )

        # 최대 3번 시도
        for _ in range(3):
            batch = await self._buffer.get_batch(
                size=min(remaining_count, 1000),
                timeout=1.0
            )

            if not batch:
                break

            await self.publish(batch)

    # =========================================================================
    # Event Emission
    # =========================================================================

    async def _emit_event(
        self,
        event_type: EventType,
        data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        이벤트 발생.

        Args:
            event_type: 이벤트 타입
            data: 이벤트 데이터
        """
        if self._event_bus:
            await self._event_bus.emit(Event(
                event_type=event_type,
                source=self._name,
                data=data or {},
            ))

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        발행기 통계 조회.

        Returns:
            통계 딕셔너리
        """
        return {
            "name": self._name,
            "is_running": self._is_running,
            "is_connected": self._is_connected,
            "total_published": self._total_published,
            "total_failed": self._total_failed,
            "total_retries": self._total_retries,
            "consecutive_failures": self._consecutive_failures,
            "last_publish_time": (
                self._last_publish_time.isoformat()
                if self._last_publish_time else None
            ),
            "buffer_size": self._buffer.size if self._buffer else 0,
        }

    # =========================================================================
    # Abstract Methods (구현 필요)
    # =========================================================================

    @abstractmethod
    async def _do_connect(self) -> bool:
        """
        실제 연결 로직 (대상별 구현 필요).

        Returns:
            연결 성공 여부
        """
        pass

    @abstractmethod
    async def _do_disconnect(self) -> None:
        """실제 연결 해제 로직 (대상별 구현 필요)."""
        pass

    @abstractmethod
    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        실제 데이터 전송 로직 (대상별 구현 필요).

        Args:
            data: 발행할 데이터 리스트

        Returns:
            발행 성공 여부
        """
        pass

    async def _do_health_check(self) -> bool:
        """
        실제 헬스체크 로직 (대상별 구현 선택).

        기본 구현은 연결 상태만 확인합니다.

        Returns:
            연결이 정상이면 True
        """
        return self._is_connected
