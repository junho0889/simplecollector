"""
데이터 버퍼 모듈
================

스레드 안전한 데이터 버퍼 구현.
Processor에서 처리된 데이터를 임시 저장하고, Publisher가 배치로 가져갑니다.

Architecture:
    ┌──────────────┐                     ┌──────────────┐
    │  Processor   │ ──put()──▶ Buffer ──get_batch()──▶ │  Publisher   │
    └──────────────┘                     └──────────────┘

Features:
    - 스레드/비동기 안전
    - 최대 크기 제한 (오버플로우 방지)
    - 배치 단위 데이터 추출
    - 임계값 도달 시 이벤트 발생
    - 데이터 영속성 (선택적)

Thread Safety:
    asyncio.Lock을 사용하여 동시 접근 보호.
    여러 Processor가 동시에 put() 호출해도 안전합니다.

Usage:
    buffer = DataBuffer(max_size=10000, batch_size=100)

    # 데이터 추가 (Processor에서)
    await buffer.put(processed_data)

    # 배치 가져오기 (Publisher에서)
    batch = await buffer.get_batch()
    if batch:
        success = await publisher.publish(batch)
        if not success:
            await buffer.put_back(batch)  # 실패 시 되돌리기
"""

import asyncio
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Coroutine, Deque, List, Optional, Any
import logging
import pickle
from pathlib import Path

from .interfaces import ProcessedData
from .events import EventBus, Event, EventType

logger = logging.getLogger(__name__)


@dataclass
class BufferStats:
    """
    버퍼 통계 정보.

    Attributes:
        total_put: 총 추가된 데이터 수
        total_get: 총 가져간 데이터 수
        total_overflow: 오버플로우로 삭제된 데이터 수
        current_size: 현재 버퍼 크기
        max_size_reached: 도달한 최대 크기
        last_put_time: 마지막 데이터 추가 시간
        last_get_time: 마지막 데이터 추출 시간
    """
    total_put: int = 0
    total_get: int = 0
    total_overflow: int = 0
    current_size: int = 0
    max_size_reached: int = 0
    last_put_time: Optional[datetime] = None
    last_get_time: Optional[datetime] = None


class DataBuffer:
    """
    스레드 안전한 데이터 버퍼.

    ProcessedData를 임시 저장하고 배치 단위로 제공합니다.

    Features:
        - FIFO (First In First Out) 순서 보장
        - 최대 크기 제한 및 오버플로우 정책
        - 배치 추출 및 원자적 작업
        - 이벤트 기반 알림

    Overflow Policy:
        버퍼가 가득 차면:
        1. drop_oldest=True: 가장 오래된 데이터 삭제 (기본)
        2. drop_oldest=False: 새 데이터 거부

    Example:
        # 버퍼 생성
        buffer = DataBuffer(
            max_size=10000,
            batch_size=100,
            threshold_ratio=0.8
        )

        # 이벤트 버스 연결
        buffer.set_event_bus(event_bus)

        # 데이터 추가
        await buffer.put(processed_data)
        await buffer.put_many([data1, data2, data3])

        # 배치 추출
        batch = await buffer.get_batch()  # 최대 100개
        batch = await buffer.get_batch(50)  # 50개만

        # 처리 실패 시 되돌리기
        if not await publish(batch):
            await buffer.put_front(batch)
    """

    def __init__(
        self,
        max_size: int = 10000,
        batch_size: int = 100,
        threshold_ratio: float = 0.8,
        drop_oldest: bool = True,
        persist_path: Optional[Path] = None,
    ):
        """
        Args:
            max_size: 버퍼 최대 크기
            batch_size: 기본 배치 크기
            threshold_ratio: 이벤트 발생 임계값 비율 (0.0~1.0)
            drop_oldest: 오버플로우 시 오래된 데이터 삭제 여부
            persist_path: 데이터 영속화 파일 경로 (None이면 비활성화)
        """
        self._buffer: Deque[ProcessedData] = deque(maxlen=None)
        self._max_size = max_size
        self._batch_size = batch_size
        self._threshold_size = int(max_size * threshold_ratio)
        self._drop_oldest = drop_oldest
        self._persist_path = persist_path

        self._lock = asyncio.Lock()
        self._not_empty = asyncio.Condition(self._lock)

        self._stats = BufferStats()
        self._event_bus: Optional[EventBus] = None
        self._threshold_notified = False

        # 영속화된 데이터 복구
        if persist_path and persist_path.exists():
            self._load_from_disk()

    def set_event_bus(self, event_bus: EventBus) -> None:
        """이벤트 버스 연결."""
        self._event_bus = event_bus

    @property
    def size(self) -> int:
        """현재 버퍼 크기."""
        return len(self._buffer)

    @property
    def is_empty(self) -> bool:
        """버퍼가 비어있는지 여부."""
        return len(self._buffer) == 0

    @property
    def is_full(self) -> bool:
        """버퍼가 가득 찼는지 여부."""
        return len(self._buffer) >= self._max_size

    @property
    def stats(self) -> BufferStats:
        """버퍼 통계 정보."""
        self._stats.current_size = len(self._buffer)
        return self._stats

    async def put(self, data: ProcessedData) -> bool:
        """
        단일 데이터 추가.

        Args:
            data: 추가할 처리된 데이터

        Returns:
            추가 성공 여부 (drop_oldest=False이고 버퍼가 가득 찬 경우 False)
        """
        async with self._lock:
            return await self._put_internal(data)

    async def put_many(self, data_list: List[ProcessedData]) -> int:
        """
        다중 데이터 추가.

        Args:
            data_list: 추가할 데이터 리스트

        Returns:
            실제 추가된 데이터 수
        """
        async with self._lock:
            count = 0
            for data in data_list:
                if await self._put_internal(data):
                    count += 1
            return count

    async def put_front(self, data_list: List[ProcessedData]) -> int:
        """
        데이터를 앞쪽에 추가 (되돌리기용).

        Publisher에서 전송 실패한 데이터를 다시 버퍼에 넣을 때 사용합니다.
        FIFO 순서를 유지하기 위해 앞쪽에 추가합니다.

        Args:
            data_list: 추가할 데이터 리스트

        Returns:
            실제 추가된 데이터 수
        """
        async with self._lock:
            count = 0
            # 역순으로 앞에 추가하여 원래 순서 유지
            for data in reversed(data_list):
                if len(self._buffer) >= self._max_size:
                    if self._drop_oldest:
                        self._buffer.pop()
                        self._stats.total_overflow += 1
                    else:
                        break

                self._buffer.appendleft(data)
                count += 1

            if count > 0:
                self._not_empty.notify_all()
                logger.debug(f"Put back {count} items to buffer front")

            return count

    async def _put_internal(self, data: ProcessedData) -> bool:
        """
        내부 데이터 추가 로직 (lock 획득 상태에서 호출).

        Args:
            data: 추가할 데이터

        Returns:
            추가 성공 여부
        """
        # 오버플로우 처리
        if len(self._buffer) >= self._max_size:
            if self._drop_oldest:
                self._buffer.popleft()
                self._stats.total_overflow += 1
                await self._emit_event(EventType.BUFFER_OVERFLOW, {
                    "dropped_count": 1,
                    "current_size": len(self._buffer),
                })
            else:
                logger.warning(f"Buffer full, rejecting data: {data.tag_id}")
                return False

        # 데이터 추가
        self._buffer.append(data)
        self._stats.total_put += 1
        self._stats.last_put_time = datetime.now()

        # 최대 크기 기록 갱신
        if len(self._buffer) > self._stats.max_size_reached:
            self._stats.max_size_reached = len(self._buffer)

        # 대기 중인 consumer 알림
        self._not_empty.notify()

        # 임계값 이벤트
        await self._check_threshold()

        # 버퍼 업데이트 이벤트
        await self._emit_event(EventType.BUFFER_UPDATED, {
            "current_size": len(self._buffer),
        })

        return True

    async def get_batch(
        self,
        size: Optional[int] = None,
        timeout: Optional[float] = None
    ) -> List[ProcessedData]:
        """
        배치 데이터 추출.

        지정된 크기만큼 데이터를 가져옵니다.
        데이터가 없으면 대기합니다.

        Args:
            size: 가져올 데이터 수 (None이면 기본 batch_size)
            timeout: 대기 타임아웃 (초)

        Returns:
            추출된 데이터 리스트 (타임아웃 시 빈 리스트)
        """
        size = size or self._batch_size

        async with self._not_empty:
            # 데이터가 있을 때까지 대기
            try:
                await asyncio.wait_for(
                    self._wait_for_data(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                return []

            # 데이터 추출
            batch: List[ProcessedData] = []
            for _ in range(min(size, len(self._buffer))):
                batch.append(self._buffer.popleft())

            self._stats.total_get += len(batch)
            self._stats.last_get_time = datetime.now()

            # 임계값 아래로 떨어지면 플래그 리셋
            if len(self._buffer) < self._threshold_size:
                self._threshold_notified = False

            logger.debug(f"Got batch: {len(batch)} items, remaining: {len(self._buffer)}")
            return batch

    async def _wait_for_data(self) -> None:
        """데이터가 있을 때까지 대기."""
        while len(self._buffer) == 0:
            await self._not_empty.wait()

    async def get_all(self) -> List[ProcessedData]:
        """
        모든 데이터 추출 (버퍼 비움).

        Returns:
            버퍼의 모든 데이터
        """
        async with self._lock:
            batch = list(self._buffer)
            self._buffer.clear()
            self._stats.total_get += len(batch)
            self._stats.last_get_time = datetime.now()
            self._threshold_notified = False
            return batch

    async def peek(self, size: int = 1) -> List[ProcessedData]:
        """
        데이터 미리보기 (제거하지 않음).

        Args:
            size: 미리볼 데이터 수

        Returns:
            데이터 리스트 (복사본)
        """
        async with self._lock:
            return list(self._buffer)[:size]

    async def clear(self) -> int:
        """
        버퍼 비우기.

        Returns:
            삭제된 데이터 수
        """
        async with self._lock:
            count = len(self._buffer)
            self._buffer.clear()
            self._threshold_notified = False
            logger.info(f"Buffer cleared: {count} items removed")
            return count

    async def _check_threshold(self) -> None:
        """임계값 체크 및 이벤트 발생."""
        if not self._threshold_notified and len(self._buffer) >= self._threshold_size:
            self._threshold_notified = True
            await self._emit_event(EventType.BUFFER_THRESHOLD, {
                "current_size": len(self._buffer),
                "threshold_size": self._threshold_size,
                "max_size": self._max_size,
            })
            logger.warning(
                f"Buffer threshold reached: {len(self._buffer)}/{self._max_size}"
            )

    async def _emit_event(self, event_type: EventType, data: dict) -> None:
        """이벤트 발생."""
        if self._event_bus:
            await self._event_bus.emit(Event(
                event_type=event_type,
                source="buffer",
                data=data,
            ))

    def _save_to_disk(self) -> None:
        """버퍼 데이터를 디스크에 저장."""
        if not self._persist_path:
            return

        try:
            with open(self._persist_path, 'wb') as f:
                pickle.dump(list(self._buffer), f)
            logger.info(f"Buffer saved: {len(self._buffer)} items to {self._persist_path}")
        except Exception as e:
            logger.error(f"Failed to save buffer: {e}")

    def _load_from_disk(self) -> None:
        """디스크에서 버퍼 데이터 복구."""
        if not self._persist_path or not self._persist_path.exists():
            return

        try:
            with open(self._persist_path, 'rb') as f:
                data = pickle.load(f)
                self._buffer.extend(data)
            logger.info(f"Buffer loaded: {len(data)} items from {self._persist_path}")
        except Exception as e:
            logger.error(f"Failed to load buffer: {e}")

    async def shutdown(self) -> None:
        """
        버퍼 종료 처리.

        미처리 데이터를 디스크에 저장합니다.
        """
        if self._persist_path and len(self._buffer) > 0:
            self._save_to_disk()
        logger.info("Buffer shutdown complete")


class PriorityBuffer:
    """
    우선순위 기반 데이터 버퍼.

    높은 우선순위 데이터를 먼저 처리합니다.
    실시간 알람 등 중요 데이터에 사용합니다.

    Note:
        기본 DataBuffer로 충분하지 않은 경우에만 사용하세요.
        대부분의 산업용 데이터 수집에는 DataBuffer가 적합합니다.
    """

    def __init__(self, max_size: int = 10000):
        """
        Args:
            max_size: 버퍼 최대 크기
        """
        self._high_priority: Deque[ProcessedData] = deque()
        self._normal_priority: Deque[ProcessedData] = deque()
        self._low_priority: Deque[ProcessedData] = deque()
        self._max_size = max_size
        self._lock = asyncio.Lock()

    async def put(
        self,
        data: ProcessedData,
        priority: str = "normal"
    ) -> bool:
        """
        우선순위와 함께 데이터 추가.

        Args:
            data: 추가할 데이터
            priority: 우선순위 ("high", "normal", "low")

        Returns:
            추가 성공 여부
        """
        async with self._lock:
            total_size = (
                len(self._high_priority) +
                len(self._normal_priority) +
                len(self._low_priority)
            )

            if total_size >= self._max_size:
                # 낮은 우선순위부터 삭제
                if self._low_priority:
                    self._low_priority.popleft()
                elif self._normal_priority:
                    self._normal_priority.popleft()
                else:
                    return False

            if priority == "high":
                self._high_priority.append(data)
            elif priority == "low":
                self._low_priority.append(data)
            else:
                self._normal_priority.append(data)

            return True

    async def get_batch(self, size: int = 100) -> List[ProcessedData]:
        """
        우선순위 순서로 배치 추출.

        Args:
            size: 가져올 데이터 수

        Returns:
            추출된 데이터 리스트
        """
        async with self._lock:
            batch: List[ProcessedData] = []

            # 높은 우선순위 먼저
            for queue in [self._high_priority, self._normal_priority, self._low_priority]:
                while len(batch) < size and queue:
                    batch.append(queue.popleft())

            return batch

    @property
    def size(self) -> int:
        """전체 버퍼 크기."""
        return (
            len(self._high_priority) +
            len(self._normal_priority) +
            len(self._low_priority)
        )
