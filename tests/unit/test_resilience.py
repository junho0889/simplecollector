"""
Resilience tests for BaseCollector — v0.3.6 패치 검증.

검증 범위:
  R-1. 영구 재연결: _reconnect_loop이 collector 수명 동안 항상 실행되며,
       운영 중 끊김 후에도 자동 복구
  R-2. LOSS 로그 throttle: 그룹별 첫 실패 + 30초 주기 + 복구 1줄
  R-3. 재연결 ERROR 로그 throttle: 첫 실패 + 60초 주기
  R-4. quality_code=0 데이터는 throttle 없이 매 실패마다 생성
"""
import asyncio
import logging
from typing import List, Optional

import pytest

from src.collectors.base import BaseCollector
from src.core.config import CollectorConfig, ProtocolConfig, CollectionGroup
from src.core.interfaces import CollectedData, ConnectionState


# ---------------------------------------------------------------------------
# Fake collector — _do_connect / _do_collect 결과를 외부에서 제어 가능
# ---------------------------------------------------------------------------
class FakeCollector(BaseCollector):
    def __init__(self, config: CollectorConfig):
        super().__init__(plc_id=99, name="FAKE", config=config)
        self.connect_should_succeed = True
        self.collect_should_succeed = True
        self.connect_calls = 0
        self.collect_calls = 0

    async def _do_connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_should_succeed

    async def _do_disconnect(self) -> None:
        pass

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        self.collect_calls += 1
        if not self.collect_should_succeed:
            return None
        from datetime import datetime
        return CollectedData(
            source_time=datetime.now(),
            collection_time=datetime.now(),
            plc_id=99,
            raw_data=b"\x00\x00",
            collection_group=group,
        )


def make_config() -> CollectorConfig:
    return CollectorConfig(
        plc_id=99, name="FAKE",
        protocol=ProtocolConfig(
            type="mc_protocol", host="127.0.0.1", port=15007,
            unit_id=1, timeout_ms=2000, reconnect_interval_ms=1000,
            extra={},
        ),
        collection_groups=[
            CollectionGroup(
                name="g1", interval_ms=50, timeout_ms=2000,
                retry_count=0, retry_delay_ms=10,
            ),
        ],
        tags_file="",
    )


# ---------------------------------------------------------------------------
# R-2: LOSS 로그 throttle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_R2_loss_log_throttle_first_failure_then_silent(caplog):
    """첫 실패는 항상 로그, 이후 30초 이내 추가 실패는 throttle."""
    from datetime import datetime
    c = FakeCollector(make_config())
    caplog.set_level(logging.WARNING, logger="collector.loss")

    # 100회 연속 실패
    for _ in range(100):
        await c._handle_collection_failure("g1", datetime.now(), "not_connected")

    # 로그 throttle 검증: 첫 실패 1줄만 (interval 30초가 아직 지나지 않음)
    loss_logs = [r for r in caplog.records
                 if r.name == "collector.loss" and "LOSS group=" in r.message]
    assert len(loss_logs) == 1, (
        f"Expected 1 LOSS log (first failure only), got {len(loss_logs)}: "
        f"{[r.message for r in loss_logs]}"
    )

    # 카운터는 throttle 없이 정확히 누적되어야 함
    assert c._consecutive_loss["g1"] == 100
    assert c._total_loss["g1"] == 100


@pytest.mark.asyncio
async def test_R2_loss_log_emits_again_after_interval(caplog):
    """interval 경과 후 다시 로그 출력."""
    from datetime import datetime
    c = FakeCollector(make_config())
    c._loss_log_interval_sec = 0.05  # 빠른 검증을 위해 50ms
    caplog.set_level(logging.WARNING, logger="collector.loss")

    await c._handle_collection_failure("g1", datetime.now(), "not_connected")  # 1st log
    await c._handle_collection_failure("g1", datetime.now(), "not_connected")  # throttled
    await asyncio.sleep(0.06)  # interval 경과
    await c._handle_collection_failure("g1", datetime.now(), "not_connected")  # 2nd log

    loss_logs = [r for r in caplog.records
                 if r.name == "collector.loss" and "LOSS group=" in r.message]
    assert len(loss_logs) == 2, (
        f"Expected 2 LOSS logs, got {len(loss_logs)}"
    )


# ---------------------------------------------------------------------------
# R-4: quality_code=0 데이터는 throttle 없이 매 실패마다 생성
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_R4_failed_data_emitted_every_failure_despite_log_throttle():
    """로그가 throttle돼도 failed_data는 매 실패마다 생성되어야 publisher가 quality=0을 받을 수 있음."""
    from datetime import datetime
    c = FakeCollector(make_config())

    received: List[CollectedData] = []

    async def cb(data):
        received.append(data)

    c.set_on_data_callback(cb)

    # 태그가 없으므로 _create_failed_data는 None을 반환할 수 있음 — 그룹에 태그를 등록
    from src.core.interfaces import TagDefinition, DataType
    c._tags = {"g1": [TagDefinition(
        tag_id=1, tag_name="t1", address="D0", data_type=DataType.UINT16,
    )]}

    for _ in range(50):
        await c._handle_collection_failure("g1", datetime.now(), "not_connected")

    assert len(received) == 50, f"Expected 50 failed_data emissions, got {len(received)}"
    assert all(r.metadata.get("failed") for r in received)
    assert all(r.metadata.get("quality_code") == 0 for r in received)


# ---------------------------------------------------------------------------
# R-1: 영구 재연결 — _reconnect_loop이 collector 수명 동안 항상 실행
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_R1_reconnect_loop_recovers_from_runtime_disconnect(caplog):
    """
    시나리오:
      1. 첫 연결 성공
      2. 운영 중 state를 ERROR로 강제 (끊김 시뮬레이션)
      3. _reconnect_loop이 자동 재연결 시도하여 CONNECTED로 복구
    """
    c = FakeCollector(make_config())
    caplog.set_level(logging.INFO)

    await c.start()
    try:
        # 시작 후 정상 연결 + reconnect_task 항상 살아있어야 함
        assert c.state == ConnectionState.CONNECTED
        assert c._reconnect_task is not None
        assert not c._reconnect_task.done()
        initial_connect_calls = c.connect_calls
        assert initial_connect_calls == 1

        # 운영 중 끊김 시뮬레이션 — state를 ERROR로 강제
        c._state = ConnectionState.ERROR

        # _reconnect_loop이 1초 백오프 후 재연결 시도
        await asyncio.sleep(2.0)

        # 자동으로 CONNECTED로 복구되어야 함
        assert c.state == ConnectionState.CONNECTED, (
            f"Expected auto-recovery to CONNECTED, but state={c.state.value}"
        )
        assert c.connect_calls > initial_connect_calls, (
            "Expected _reconnect_loop to call connect() during recovery"
        )

        # 복구 INFO 로그 검증
        recovery_logs = [r for r in caplog.records
                         if "Reconnected to" in r.message and "after" in r.message]
        assert len(recovery_logs) >= 1, "Expected 'Reconnected to ... after N attempt(s)' INFO log"
    finally:
        await c.stop()


@pytest.mark.asyncio
async def test_R1_reconnect_loop_starts_even_when_first_connect_succeeds():
    """
    회귀 테스트: 0.3.5에서는 첫 connect 성공 시 _reconnect_loop이 시작되지 않아
    운영 중 끊김 시 영원히 not_connected 상태였음. 0.3.6에서 수정됨.
    """
    c = FakeCollector(make_config())
    c.connect_should_succeed = True

    await c.start()
    try:
        assert c._reconnect_task is not None, (
            "_reconnect_task must be created at start() regardless of first connect outcome"
        )
        assert not c._reconnect_task.done(), (
            "_reconnect_task must be running for the lifetime of the collector"
        )
    finally:
        await c.stop()


# ---------------------------------------------------------------------------
# R-3: 재연결 실패 ERROR 로그 throttle
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_R3_reconnect_error_log_throttled(caplog):
    """
    재연결이 계속 실패할 때 ERROR 로그는 첫 실패 1회 + interval 주기로만.
    매 시도마다 ERROR가 찍히면 안 됨.
    """
    c = FakeCollector(make_config())
    c.connect_should_succeed = False  # 모든 connect 실패하게
    c._reconnect_log_interval_sec = 5.0  # 짧은 검증
    caplog.set_level(logging.ERROR)

    await c.start()
    try:
        # 1초 백오프 + 2초, 4초 — 총 ~3초간 N번의 시도. ERROR 로그는 첫 1회만.
        await asyncio.sleep(3.0)
        cannot_reconnect_logs = [r for r in caplog.records
                                 if "Cannot reconnect" in r.message]
        # 첫 실패 1줄 + interval 5초 안 지났으니 추가 로그 없어야 함
        assert len(cannot_reconnect_logs) == 1, (
            f"Expected 1 'Cannot reconnect' ERROR (first failure only within interval), "
            f"got {len(cannot_reconnect_logs)}: {[r.message for r in cannot_reconnect_logs]}"
        )
    finally:
        await c.stop()
