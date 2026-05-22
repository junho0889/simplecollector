"""
기본 수집기 모듈
================

모든 프로토콜 수집기의 기본 클래스를 제공합니다.
공통 로직(재연결, 스케줄링, 이벤트 발생)을 구현합니다.

Architecture:
    BaseCollector는 다음 기능을 제공합니다:
    - 자동 재연결 (연결 끊김 시)
    - 그룹별 수집 스케줄링 (1초, 1분 등 다른 주기)
    - 이벤트 기반 데이터 전달
    - 헬스체크 및 상태 모니터링

Thread Model:
    각 수집 그룹은 독립적인 asyncio Task로 실행됩니다.

    ┌─────────────────────────────────┐
    │         BaseCollector           │
    │  ┌─────────┐  ┌─────────┐      │
    │  │ 1sec    │  │ 1min    │ ...  │  ← 그룹별 Task
    │  │ Task    │  │ Task    │      │
    │  └────┬────┘  └────┬────┘      │
    │       │            │           │
    │       ▼            ▼           │
    │    collect()    collect()      │  ← 프로토콜 구현 (추상)
    └─────────────────────────────────┘

Extension:
    프로토콜별 구현체는 BaseCollector를 상속받아 구현합니다.

    class ModbusCollector(BaseCollector):
        async def _do_connect(self) -> bool:
            # Modbus 연결 로직
            pass

        async def _do_collect(self, group: str) -> Optional[CollectedData]:
            # Modbus 데이터 읽기 로직
            pass

Example:
    class MyProtocolCollector(BaseCollector):
        async def _do_connect(self) -> bool:
            self._client = MyProtocolClient(self._host, self._port)
            return await self._client.connect()

        async def _do_disconnect(self) -> None:
            await self._client.disconnect()

        async def _do_collect(self, group: str) -> Optional[CollectedData]:
            tags = self._tags.get(group, [])
            raw_data = await self._client.read(tags)
            return CollectedData(
                source_time=datetime.now(),
                collection_time=datetime.now(),
                plc_id=self._plc_id,
                raw_data=raw_data,
                collection_group=group,
            )
"""

import asyncio
import time
from abc import abstractmethod
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any
import logging

from ..core.interfaces import (
    ICollector,
    CollectedData,
    TagDefinition,
    ConnectionState,
)
from ..core.events import EventBus, Event, EventType
from ..core.config import CollectorConfig, CollectionGroup, ProtocolConfig
from ..utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class BaseCollector(ICollector):
    """
    기본 수집기 클래스.

    모든 프로토콜 수집기의 공통 기능을 구현합니다.
    프로토콜별 구현체는 이 클래스를 상속받아 _do_* 메서드를 구현합니다.

    Features:
        - 자동 재연결 (설정 가능한 간격)
        - 그룹별 독립 스케줄링
        - 이벤트 기반 데이터 전달
        - 통계 수집

    Attributes:
        plc_id: PLC 식별자
        name: 수집기 이름
        config: 수집기 설정
        event_bus: 이벤트 버스 (데이터 전달용)

    Abstract Methods (구현 필요):
        _do_connect: 실제 연결 로직
        _do_disconnect: 실제 연결 해제 로직
        _do_collect: 실제 데이터 수집 로직
        _do_health_check: 실제 헬스체크 로직
    """

    def __init__(
        self,
        plc_id: int,
        name: str,
        config: CollectorConfig,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Args:
            plc_id: PLC 고유 식별자
            name: 수집기 이름 (로깅용)
            config: 수집기 설정
            event_bus: 이벤트 버스 (None이면 이벤트 발생 안 함)
        """
        super().__init__(plc_id, name)

        self._config = config
        self._event_bus = event_bus
        self._protocol_config: Optional[ProtocolConfig] = config.protocol
        self._collection_groups: Dict[str, CollectionGroup] = {
            g.name: g for g in config.collection_groups
        }

        # 태스크 관리
        self._collection_tasks: Dict[str, asyncio.Task] = {}
        self._reconnect_task: Optional[asyncio.Task] = None

        # 상태
        self._last_success_time: Dict[str, Optional[datetime]] = {}
        self._error_count: Dict[str, int] = {}
        self._total_collected: Dict[str, int] = {}

        # 손실 추적 (시작 시점부터)
        self._start_time: Optional[datetime] = None
        self._total_loss: Dict[str, int] = {}  # 그룹별 손실 횟수
        self._consecutive_loss: Dict[str, int] = {}  # 연속 손실 횟수

        # 손실 로그 throttle (group별, 동일 사유 반복 시 폭주 방지)
        # _last_loss_log_at: 마지막 LOSS WARNING을 출력한 monotonic time
        self._last_loss_log_at: Dict[str, float] = {}
        self._loss_log_interval_sec: float = 30.0

        # 재연결 로그 throttle
        self._last_reconnect_log_at: float = 0.0
        self._reconnect_log_interval_sec: float = 60.0

        # 콜백 (선택적)
        self._on_data_callback: Optional[Callable[[CollectedData], Any]] = None

        # 손실 로거
        self._loss_logger = LoggerFactory.get_loss_logger()

    @property
    def protocol_config(self) -> Optional[ProtocolConfig]:
        """프로토콜 설정."""
        return self._protocol_config

    def set_on_data_callback(
        self,
        callback: Callable[[CollectedData], Any]
    ) -> None:
        """
        데이터 수집 콜백 설정.

        이벤트 버스 대신 직접 콜백을 사용할 때 설정합니다.

        Args:
            callback: 수집 완료 시 호출될 콜백 함수
        """
        self._on_data_callback = callback

    # =========================================================================
    # Public Methods (ICollector 구현)
    # =========================================================================

    async def connect(self) -> bool:
        """
        대상 장비에 연결.

        Returns:
            연결 성공 여부
        """
        self._state = ConnectionState.CONNECTING
        logger.info(f"[{self._name}] Connecting to PLC {self._plc_id}...")

        try:
            success = await self._do_connect()

            if success:
                self._state = ConnectionState.CONNECTED
                await self._emit_event(EventType.COLLECTOR_CONNECTED)
                logger.info(f"[{self._name}] Connected successfully")
            else:
                self._state = ConnectionState.ERROR
                await self._emit_event(EventType.COLLECTOR_ERROR, {
                    "error": "Connection failed"
                })
                logger.error(f"[{self._name}] Connection failed")

            return success

        except Exception as e:
            self._state = ConnectionState.ERROR
            await self._emit_event(EventType.COLLECTOR_ERROR, {"error": str(e)})
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
            self._state = ConnectionState.DISCONNECTED
            await self._emit_event(EventType.COLLECTOR_DISCONNECTED)
            logger.info(f"[{self._name}] Disconnected")

    async def collect(self, group: str) -> Optional[CollectedData]:
        """
        지정된 그룹의 데이터 수집.

        Args:
            group: 수집 그룹명

        Returns:
            수집된 Raw 데이터, 실패 시 None
        """
        if self._state != ConnectionState.CONNECTED:
            logger.warning(
                f"[{self._name}] Cannot collect, not connected "
                f"(state={self._state.value})"
            )
            return None

        try:
            data = await self._do_collect(group)

            if data:
                self._last_success_time[group] = datetime.now()
                self._total_collected[group] = \
                    self._total_collected.get(group, 0) + 1
                self._error_count[group] = 0

                logger.debug(
                    f"[{self._name}] Collected group '{group}': "
                    f"{len(data.raw_data)} bytes"
                )

            return data

        except Exception as e:
            self._error_count[group] = self._error_count.get(group, 0) + 1
            logger.error(
                f"[{self._name}] Collection error for group '{group}': {e}"
            )
            return None

    async def health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결이 정상이면 True
        """
        if self._state != ConnectionState.CONNECTED:
            return False

        try:
            return await self._do_health_check()
        except Exception as e:
            logger.warning(f"[{self._name}] Health check failed: {e}")
            return False

    async def start(self) -> None:
        """
        수집기 시작.

        모든 수집 그룹에 대한 태스크를 생성합니다.
        """
        await super().start()

        # 시작 시간 기록 (손실 추적용)
        self._start_time = datetime.now()
        self._loss_logger.info(
            f"[{self._name}] Collector started at {self._start_time.isoformat()}"
        )

        # 첫 연결 시도 (실패해도 _reconnect_loop이 책임지고 복구)
        await self.connect()

        # 재연결 루프는 collector 수명 동안 항상 실행 — 운영 중 끊김도 자동 복구
        # (정상 상태에서는 짧게 대기만, 끊긴 상태에서는 지수 백오프로 connect 시도)
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

        # 각 그룹별 수집 태스크 시작
        for group_name, group_config in self._collection_groups.items():
            task = asyncio.create_task(
                self._collection_loop(group_name, group_config)
            )
            self._collection_tasks[group_name] = task
            logger.info(
                f"[{self._name}] Started collection task for group '{group_name}' "
                f"(interval={group_config.interval_ms}ms)"
            )

        await self._emit_event(EventType.COLLECTOR_STARTED)
        logger.info(f"[{self._name}] Collector started")

    async def stop(self) -> None:
        """
        수집기 중지.

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

        # 수집 태스크 취소
        for group_name, task in self._collection_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.debug(f"[{self._name}] Stopped collection task: {group_name}")

        self._collection_tasks.clear()

        # 연결 해제
        await self.disconnect()

        await self._emit_event(EventType.COLLECTOR_STOPPED)
        logger.info(f"[{self._name}] Collector stopped")

    # =========================================================================
    # Collection Loop
    # =========================================================================

    async def _collection_loop(
        self,
        group_name: str,
        group_config: CollectionGroup
    ) -> None:
        """
        그룹별 수집 루프.

        설정된 주기에 따라 데이터를 수집합니다.
        실패 시 quality_code=0인 데이터를 생성하여 손실을 기록합니다.

        Args:
            group_name: 수집 그룹명
            group_config: 그룹 설정
        """
        interval = group_config.interval_ms / 1000.0  # ms → seconds

        while self._is_running:
            start_time = asyncio.get_event_loop().time()
            collection_time = datetime.now()

            # 연결 상태 확인
            if self._state == ConnectionState.CONNECTED:
                # 데이터 수집
                data = await self._collect_with_retry(group_name, group_config)

                if data:
                    # 성공: 연속 손실 누적이 있었다면 복구 알림
                    prior_consecutive = self._consecutive_loss.get(group_name, 0)
                    if prior_consecutive > 0:
                        self._loss_logger.info(
                            f"[{self._name}] Recovered group='{group_name}' "
                            f"after {prior_consecutive} consecutive losses"
                        )
                    self._consecutive_loss[group_name] = 0
                    self._last_loss_log_at[group_name] = 0.0
                    await self._notify_data_collected(data)
                else:
                    # 실패: 손실 기록 및 quality_code=0 데이터 생성
                    await self._handle_collection_failure(
                        group_name, collection_time, "collection_failed"
                    )
            else:
                # 연결 안됨: 손실 기록
                await self._handle_collection_failure(
                    group_name, collection_time, "not_connected"
                )

            # 다음 수집까지 대기
            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0, interval - elapsed)

            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    async def _collect_with_retry(
        self,
        group_name: str,
        group_config: CollectionGroup
    ) -> Optional[CollectedData]:
        """
        재시도 로직이 포함된 데이터 수집.

        Args:
            group_name: 수집 그룹명
            group_config: 그룹 설정

        Returns:
            수집된 데이터 또는 None
        """
        for attempt in range(group_config.retry_count + 1):
            try:
                # 타임아웃 적용
                data = await asyncio.wait_for(
                    self.collect(group_name),
                    timeout=group_config.timeout_ms / 1000.0
                )

                if data:
                    return data

            except asyncio.TimeoutError:
                logger.warning(
                    f"[{self._name}] Collection timeout for group '{group_name}' "
                    f"(attempt {attempt + 1}/{group_config.retry_count + 1})"
                )
            except Exception as e:
                logger.error(
                    f"[{self._name}] Collection error: {e} "
                    f"(attempt {attempt + 1}/{group_config.retry_count + 1})"
                )

            # 재시도 전 대기
            if attempt < group_config.retry_count:
                await asyncio.sleep(group_config.retry_delay_ms / 1000.0)

        return None

    # =========================================================================
    # Failure Handling (손실 처리)
    # =========================================================================

    async def _handle_collection_failure(
        self,
        group_name: str,
        collection_time: datetime,
        reason: str
    ) -> None:
        """
        수집 실패 처리.

        손실을 기록하고 quality_code=0인 데이터를 생성하여 전달합니다.

        Args:
            group_name: 수집 그룹명
            collection_time: 수집 시도 시간
            reason: 실패 사유 (collection_failed, not_connected, timeout 등)
        """
        # 손실 카운트 증가 (실제 손실 통계는 항상 정확하게 누적)
        self._total_loss[group_name] = self._total_loss.get(group_name, 0) + 1
        self._consecutive_loss[group_name] = self._consecutive_loss.get(group_name, 0) + 1

        total_loss = self._total_loss[group_name]
        consecutive = self._consecutive_loss[group_name]

        # 로그 throttle: 첫 실패 + N초 주기로만 WARNING 출력 (PLC 다운 시 폭주 방지)
        # 카운터/quality_code=0 데이터는 throttle 없이 항상 처리
        now = time.monotonic()
        last_at = self._last_loss_log_at.get(group_name, 0.0)
        should_log = consecutive == 1 or (now - last_at) >= self._loss_log_interval_sec

        if should_log:
            self._loss_logger.warning(
                f"[{self._name}] LOSS group='{group_name}' reason={reason} "
                f"consecutive={consecutive} total={total_loss} "
                f"time={collection_time.isoformat()}"
            )
            self._last_loss_log_at[group_name] = now

        # quality_code=0인 실패 데이터 생성 및 전달
        failed_data = self._create_failed_data(group_name, collection_time, reason)
        if failed_data:
            await self._notify_data_collected(failed_data)

    def _create_failed_data(
        self,
        group_name: str,
        collection_time: datetime,
        reason: str
    ) -> Optional[CollectedData]:
        """
        실패 데이터 생성 (quality_code=0).

        Processor에서 이 데이터를 받으면 모든 태그에 대해
        quality_code=0인 ProcessedData를 생성합니다.

        Args:
            group_name: 수집 그룹명
            collection_time: 수집 시도 시간
            reason: 실패 사유

        Returns:
            실패를 나타내는 CollectedData (raw_data 비어있음, metadata에 실패 정보)
        """
        tags = self._tags.get(group_name, [])
        if not tags:
            return None

        # 그룹 설정 가져오기
        group_config = self._collection_groups.get(group_name)

        return CollectedData(
            source_time=collection_time,
            collection_time=collection_time,
            plc_id=self._plc_id,
            raw_data=b"",  # 빈 데이터
            collection_group=group_name,
            metadata={
                "failed": True,
                "reason": reason,
                "quality_code": 0,
                # 태그별 None 값 (Processor에서 사용)
                "values": {tag.tag_id: None for tag in tags},
                # on_change 모드 정보
                "mode": group_config.mode if group_config else "polling",
                "deadband": group_config.deadband if group_config else 0.0,
                "deadband_type": group_config.deadband_type if group_config else "absolute",
            },
        )

    # =========================================================================
    # Reconnection
    # =========================================================================

    async def _reconnect_loop(self) -> None:
        """
        재연결 루프 (collector 수명 동안 항상 동작).

        - 정상(CONNECTED) 상태에서는 짧은 주기로 sleep만 — 상태 변화 감시
        - 끊긴 상태(ERROR/RECONNECTING/DISCONNECTED)면 지수 백오프로 connect 재시도
        - 실패 시 최초 1회 + N회마다 ERROR 로그 (throttle)
        - 성공 시 'Reconnected to PLC after N attempt(s)' INFO 1줄
        """
        if not self._protocol_config:
            return

        BACKOFF_INITIAL = 1.0    # 초기 대기 (초)
        BACKOFF_MAX = 30.0       # 최대 대기 — 끊긴 PLC를 매초 두드릴 필요 없음
        BACKOFF_FACTOR = 2.0
        HEALTHY_POLL_INTERVAL = 1.0  # 정상 상태 감시 주기 (초)

        backoff = BACKOFF_INITIAL
        attempt = 0

        host = self._protocol_config.host
        port = self._protocol_config.port

        while self._is_running:
            try:
                # 정상 상태면 가볍게 폴링만
                if self._state == ConnectionState.CONNECTED:
                    await asyncio.sleep(HEALTHY_POLL_INTERVAL)
                    continue

                # 끊긴 상태 — 백오프 후 재연결 시도
                await asyncio.sleep(backoff)
                if not self._is_running:
                    break
                # 백오프 sleep 동안 다른 경로(예: _ensure_connection)로 복구된 경우
                if self._state == ConnectionState.CONNECTED:
                    backoff = BACKOFF_INITIAL
                    attempt = 0
                    self._last_reconnect_log_at = 0.0
                    continue

                attempt += 1
                self._state = ConnectionState.RECONNECTING

                # connect()는 내부적으로 self._state를 갱신 (성공: CONNECTED, 실패: ERROR)
                success = await self.connect()
                if success:
                    # _reconnect_loop은 끊긴 상태에서만 진입하므로 성공은 항상 '복구'
                    # (start 시점 첫 연결 성공은 _reconnect_loop을 거치지 않고 connect() 직접 호출)
                    logger.info(
                        f"[{self._name}] Reconnected to {host}:{port} "
                        f"after {attempt} attempt(s)"
                    )
                    backoff = BACKOFF_INITIAL
                    attempt = 0
                    self._last_reconnect_log_at = 0.0
                else:
                    next_backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX)
                    # 첫 실패 + 60초 주기 ERROR 로그 (스팸 방지)
                    now = time.monotonic()
                    if attempt == 1 or (now - self._last_reconnect_log_at) >= self._reconnect_log_interval_sec:
                        logger.error(
                            f"[{self._name}] Cannot reconnect to PLC {host}:{port} "
                            f"(attempt={attempt}, next retry in {next_backoff:.0f}s)"
                        )
                        self._last_reconnect_log_at = now
                    backoff = next_backoff

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self._name}] Reconnect loop error: {e}")
                backoff = min(backoff * BACKOFF_FACTOR, BACKOFF_MAX)

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

    async def _notify_data_collected(self, data: CollectedData) -> None:
        """
        데이터 수집 완료 알림.

        콜백이 설정된 경우 콜백만 호출하고, 그렇지 않으면 이벤트 버스를 사용합니다.
        이렇게 하면 중복 알림을 방지합니다.

        Args:
            data: 수집된 데이터
        """
        # 그룹 설정에서 mode 정보 추가 (Processor에서 on_change 처리용)
        group_config = self._collection_groups.get(data.collection_group)
        if group_config and "mode" not in data.metadata:
            data.metadata["mode"] = group_config.mode
            data.metadata["deadband"] = group_config.deadband
            data.metadata["deadband_type"] = group_config.deadband_type

        # 콜백이 설정된 경우 콜백만 사용 (중복 방지)
        if self._on_data_callback:
            try:
                result = self._on_data_callback(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"[{self._name}] Callback error: {e}")
        # 콜백이 없을 때만 이벤트 버스 사용
        elif self._event_bus:
            await self._event_bus.emit(Event(
                event_type=EventType.DATA_COLLECTED,
                source=self._name,
                data=data,
            ))

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        수집기 통계 조회.

        Returns:
            통계 딕셔너리
        """
        # 전체 손실 합계
        total_loss_all = sum(self._total_loss.values())

        return {
            "plc_id": self._plc_id,
            "name": self._name,
            "state": self._state.value,
            "is_running": self._is_running,
            "start_time": (
                self._start_time.isoformat() if self._start_time else None
            ),
            "total_loss": total_loss_all,
            "groups": {
                group: {
                    "last_success": (
                        self._last_success_time.get(group).isoformat()
                        if self._last_success_time.get(group) else None
                    ),
                    "error_count": self._error_count.get(group, 0),
                    "total_collected": self._total_collected.get(group, 0),
                    "total_loss": self._total_loss.get(group, 0),
                    "consecutive_loss": self._consecutive_loss.get(group, 0),
                }
                for group in self._collection_groups
            },
        }

    # =========================================================================
    # Abstract Methods (구현 필요)
    # =========================================================================

    @abstractmethod
    async def _do_connect(self) -> bool:
        """
        실제 연결 로직 (프로토콜별 구현 필요).

        Returns:
            연결 성공 여부
        """
        pass

    @abstractmethod
    async def _do_disconnect(self) -> None:
        """실제 연결 해제 로직 (프로토콜별 구현 필요)."""
        pass

    @abstractmethod
    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """
        실제 데이터 수집 로직 (프로토콜별 구현 필요).

        Args:
            group: 수집 그룹명

        Returns:
            수집된 Raw 데이터
        """
        pass

    async def _do_health_check(self) -> bool:
        """
        실제 헬스체크 로직 (프로토콜별 구현 선택).

        기본 구현은 연결 상태만 확인합니다.

        Returns:
            연결이 정상이면 True
        """
        return self._state == ConnectionState.CONNECTED
