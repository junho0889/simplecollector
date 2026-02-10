"""
이벤트 시스템 모듈
==================

비동기 이벤트 기반 통신을 위한 EventBus 구현.
Collector → Processor → Publisher 간의 느슨한 결합을 제공합니다.

Architecture:
    EventBus는 Pub/Sub 패턴을 구현합니다.

    ┌──────────────┐                      ┌──────────────┐
    │  Collector   │ ──emit()──▶          │  Processor   │
    └──────────────┘           │          └──────────────┘
                               │                  ▲
                               ▼                  │
                          ┌─────────┐             │
                          │EventBus │─subscribe()─┘
                          └─────────┘
                               │
                               ▼
                          ┌──────────────┐
                          │  Publisher   │
                          └──────────────┘

Event Flow:
    1. Collector가 데이터 수집 완료 → DATA_COLLECTED 이벤트 발생
    2. Processor가 이벤트 수신 → 데이터 파싱 → BUFFER_UPDATED 이벤트 발생
    3. Publisher가 이벤트 수신 → 버퍼에서 데이터 가져와 전송

Thread Safety:
    - asyncio.Queue를 사용하여 스레드 안전성 보장
    - 이벤트 핸들러는 비동기 함수로 구현

Usage:
    # 이벤트 버스 생성
    bus = EventBus()

    # 이벤트 구독
    async def on_data_collected(event: Event):
        print(f"Data collected: {event.data}")

    bus.subscribe(EventType.DATA_COLLECTED, on_data_collected)

    # 이벤트 발생
    await bus.emit(Event(
        event_type=EventType.DATA_COLLECTED,
        source="modbus_collector",
        data=collected_data
    ))
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class EventType(Enum):
    """
    이벤트 타입 열거형.

    시스템에서 발생하는 모든 이벤트 유형을 정의합니다.
    """
    # Collector 관련 이벤트
    COLLECTOR_STARTED = auto()       # 수집기 시작됨
    COLLECTOR_STOPPED = auto()       # 수집기 중지됨
    COLLECTOR_CONNECTED = auto()     # 연결 성공
    COLLECTOR_DISCONNECTED = auto()  # 연결 해제
    COLLECTOR_ERROR = auto()         # 수집기 오류
    DATA_COLLECTED = auto()          # 데이터 수집 완료 (핵심 이벤트)

    # Processor 관련 이벤트
    PROCESSOR_STARTED = auto()       # 처리기 시작됨
    PROCESSOR_STOPPED = auto()       # 처리기 중지됨
    DATA_PROCESSED = auto()          # 데이터 처리 완료

    # Buffer 관련 이벤트
    BUFFER_UPDATED = auto()          # 버퍼에 데이터 추가됨
    BUFFER_THRESHOLD = auto()        # 버퍼 임계값 도달
    BUFFER_OVERFLOW = auto()         # 버퍼 오버플로우

    # Publisher 관련 이벤트
    PUBLISHER_STARTED = auto()       # 발행기 시작됨
    PUBLISHER_STOPPED = auto()       # 발행기 중지됨
    PUBLISHER_CONNECTED = auto()     # 연결 성공
    PUBLISHER_DISCONNECTED = auto()  # 연결 해제
    PUBLISHER_ERROR = auto()         # 발행기 오류
    DATA_PUBLISHED = auto()          # 데이터 발행 완료
    PUBLISH_FAILED = auto()          # 발행 실패

    # Pipeline 관련 이벤트
    PIPELINE_STARTED = auto()        # 파이프라인 시작
    PIPELINE_STOPPED = auto()        # 파이프라인 중지
    PIPELINE_ERROR = auto()          # 파이프라인 오류

    # System 이벤트
    SYSTEM_SHUTDOWN = auto()         # 시스템 종료 요청
    HEALTH_CHECK = auto()            # 헬스체크


class EventPriority(Enum):
    """이벤트 우선순위."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


@dataclass
class Event:
    """
    이벤트 데이터 클래스.

    시스템 내에서 전달되는 이벤트의 표준 구조입니다.

    Attributes:
        event_type: 이벤트 유형
        source: 이벤트 발생 소스 (컴포넌트 이름)
        data: 이벤트 데이터 (Any 타입)
        timestamp: 이벤트 발생 시간
        priority: 이벤트 우선순위
        correlation_id: 연관된 이벤트 추적용 ID
    """
    event_type: EventType
    source: str
    data: Any = None
    timestamp: datetime = field(default_factory=datetime.now)
    priority: EventPriority = EventPriority.NORMAL
    correlation_id: Optional[str] = None

    def __post_init__(self):
        """correlation_id 자동 생성."""
        if self.correlation_id is None:
            import uuid
            self.correlation_id = str(uuid.uuid4())[:8]


# 이벤트 핸들러 타입 정의
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """
    비동기 이벤트 버스.

    Pub/Sub 패턴을 구현하여 컴포넌트 간 느슨한 결합을 제공합니다.

    Features:
        - 비동기 이벤트 처리
        - 다중 구독자 지원
        - 우선순위 기반 처리
        - 이벤트 히스토리 (선택적)
        - 와일드카드 구독

    Thread Safety:
        asyncio 기반으로 동작하며, 단일 이벤트 루프 내에서 안전합니다.

    Example:
        bus = EventBus()

        @bus.on(EventType.DATA_COLLECTED)
        async def handle_data(event: Event):
            print(f"Received: {event.data}")

        await bus.emit(Event(EventType.DATA_COLLECTED, "collector", data))
    """

    def __init__(self, enable_history: bool = False, history_size: int = 1000):
        """
        Args:
            enable_history: 이벤트 히스토리 기록 여부
            history_size: 히스토리 최대 크기
        """
        self._handlers: Dict[EventType, List[EventHandler]] = defaultdict(list)
        self._global_handlers: List[EventHandler] = []  # 모든 이벤트 수신
        self._enable_history = enable_history
        self._history_size = history_size
        self._history: List[Event] = []
        self._event_queue: asyncio.Queue[Event] = asyncio.Queue()
        self._is_running = False
        self._processor_task: Optional[asyncio.Task] = None
        self._pending_events: Set[str] = set()  # correlation_id 추적

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler
    ) -> Callable[[], None]:
        """
        이벤트 타입에 핸들러 등록.

        Args:
            event_type: 구독할 이벤트 타입
            handler: 비동기 핸들러 함수

        Returns:
            구독 해제 함수

        Example:
            unsubscribe = bus.subscribe(EventType.DATA_COLLECTED, my_handler)
            # 나중에 구독 해제
            unsubscribe()
        """
        self._handlers[event_type].append(handler)
        logger.debug(f"Handler subscribed to {event_type.name}")

        def unsubscribe():
            self._handlers[event_type].remove(handler)
            logger.debug(f"Handler unsubscribed from {event_type.name}")

        return unsubscribe

    def subscribe_all(self, handler: EventHandler) -> Callable[[], None]:
        """
        모든 이벤트에 핸들러 등록 (글로벌 리스너).

        Args:
            handler: 비동기 핸들러 함수

        Returns:
            구독 해제 함수
        """
        self._global_handlers.append(handler)

        def unsubscribe():
            self._global_handlers.remove(handler)

        return unsubscribe

    def on(self, event_type: EventType) -> Callable[[EventHandler], EventHandler]:
        """
        데코레이터로 이벤트 핸들러 등록.

        Args:
            event_type: 구독할 이벤트 타입

        Returns:
            데코레이터 함수

        Example:
            @bus.on(EventType.DATA_COLLECTED)
            async def handle_data(event: Event):
                process(event.data)
        """
        def decorator(handler: EventHandler) -> EventHandler:
            self.subscribe(event_type, handler)
            return handler
        return decorator

    async def emit(self, event: Event) -> None:
        """
        이벤트 발생 (큐에 추가).

        Args:
            event: 발생시킬 이벤트

        Note:
            이벤트는 큐에 추가되며, 이벤트 프로세서가 순차 처리합니다.
            즉시 처리가 필요하면 emit_now()를 사용하세요.
        """
        await self._event_queue.put(event)
        self._pending_events.add(event.correlation_id)
        logger.debug(
            f"Event queued: {event.event_type.name} "
            f"from {event.source} [id={event.correlation_id}]"
        )

    async def emit_now(self, event: Event) -> None:
        """
        이벤트 즉시 처리 (큐를 거치지 않음).

        Args:
            event: 발생시킬 이벤트

        Note:
            동기적으로 모든 핸들러를 호출합니다.
            핸들러 실행 순서는 등록 순서입니다.
        """
        await self._process_event(event)

    async def _process_event(self, event: Event) -> None:
        """
        단일 이벤트 처리.

        Args:
            event: 처리할 이벤트
        """
        # 히스토리 기록
        if self._enable_history:
            self._history.append(event)
            if len(self._history) > self._history_size:
                self._history = self._history[-self._history_size:]

        # 등록된 핸들러 실행
        handlers = self._handlers.get(event.event_type, [])
        all_handlers = handlers + self._global_handlers

        for handler in all_handlers:
            try:
                await handler(event)
            except Exception as e:
                logger.error(
                    f"Error in event handler for {event.event_type.name}: {e}",
                    exc_info=True
                )

        # 처리 완료 추적
        if event.correlation_id in self._pending_events:
            self._pending_events.discard(event.correlation_id)

        logger.debug(
            f"Event processed: {event.event_type.name} "
            f"[id={event.correlation_id}] handlers={len(all_handlers)}"
        )

    async def _event_processor(self) -> None:
        """이벤트 큐 프로세서 (백그라운드 태스크)."""
        logger.info("EventBus processor started")
        while self._is_running:
            try:
                # 타임아웃으로 주기적 체크
                event = await asyncio.wait_for(
                    self._event_queue.get(),
                    timeout=1.0
                )
                await self._process_event(event)
                self._event_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"EventBus processor error: {e}", exc_info=True)

        logger.info("EventBus processor stopped")

    async def start(self) -> None:
        """이벤트 버스 시작."""
        if self._is_running:
            return

        self._is_running = True
        self._processor_task = asyncio.create_task(self._event_processor())
        logger.info("EventBus started")

    async def stop(self, timeout: float = 5.0) -> None:
        """
        이벤트 버스 중지.

        Args:
            timeout: 대기 중인 이벤트 처리 타임아웃 (초)
        """
        self._is_running = False

        # 대기 중인 이벤트 처리
        try:
            await asyncio.wait_for(
                self._event_queue.join(),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"EventBus stop timeout: {self._event_queue.qsize()} events pending"
            )

        # 프로세서 태스크 취소
        if self._processor_task:
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass

        logger.info("EventBus stopped")

    async def wait_for(
        self,
        event_type: EventType,
        timeout: Optional[float] = None,
        predicate: Optional[Callable[[Event], bool]] = None
    ) -> Optional[Event]:
        """
        특정 이벤트 대기.

        Args:
            event_type: 대기할 이벤트 타입
            timeout: 타임아웃 (초), None이면 무한 대기
            predicate: 이벤트 필터 함수

        Returns:
            수신된 이벤트, 타임아웃 시 None
        """
        result_event: Optional[Event] = None
        received = asyncio.Event()

        async def handler(event: Event):
            nonlocal result_event
            if predicate is None or predicate(event):
                result_event = event
                received.set()

        unsubscribe = self.subscribe(event_type, handler)
        try:
            await asyncio.wait_for(received.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            unsubscribe()

        return result_event

    def get_history(
        self,
        event_type: Optional[EventType] = None,
        limit: int = 100
    ) -> List[Event]:
        """
        이벤트 히스토리 조회.

        Args:
            event_type: 필터할 이벤트 타입 (None이면 전체)
            limit: 최대 반환 개수

        Returns:
            이벤트 리스트 (최신순)
        """
        if not self._enable_history:
            return []

        history = self._history
        if event_type:
            history = [e for e in history if e.event_type == event_type]

        return list(reversed(history[-limit:]))

    @property
    def queue_size(self) -> int:
        """현재 대기 중인 이벤트 수."""
        return self._event_queue.qsize()

    @property
    def handler_count(self) -> Dict[str, int]:
        """이벤트 타입별 핸들러 수."""
        return {
            event_type.name: len(handlers)
            for event_type, handlers in self._handlers.items()
        }
