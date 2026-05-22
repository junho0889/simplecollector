"""
장애 복원력 테스트 시나리오
============================
각 시나리오는 장애 주입 → 복구 → 검증 흐름을 따릅니다.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

from .docker_control import DockerControl
from .message_producer import TestMessageProducer
from .db_verifier import DBVerifier

logger = logging.getLogger(__name__)

DB_CONTAINER = "resilience-db"
RMQ_CONTAINER = "resilience-rmq"


@dataclass
class ScenarioResult:
    """시나리오 실행 결과."""
    name: str
    iteration: int
    passed: bool
    duration_seconds: float
    recovery_seconds: float = 0.0
    messages_produced: int = 0
    messages_in_db: int = 0
    data_loss: int = 0
    error: str = ""
    details: str = ""


class BaseScenario:
    """시나리오 기본 클래스."""

    name: str = "base"
    description: str = ""

    def __init__(
        self,
        docker: DockerControl,
        producer: TestMessageProducer,
        verifier: DBVerifier,
    ):
        self.docker = docker
        self.producer = producer
        self.verifier = verifier

    async def run(self, iteration: int) -> ScenarioResult:
        """시나리오 실행. 하위 클래스에서 구현."""
        raise NotImplementedError


class DBRestartShort(BaseScenario):
    """시나리오 1: DB 짧은 재시작 (10초)."""

    name = "db_restart_short"
    description = "DB stop → 10초 → start (queue.db 데이터 무손실 확인)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            # 1. 기준 메시지 생성
            self.producer.reset_counter()
            await self.verifier.truncate_all()
            baseline = await self.producer.produce_batch(tag_count=22)

            # DB에 저장될 시간 대기
            await asyncio.sleep(3)

            # 2. DB 중지
            await self.docker.stop_container(DB_CONTAINER)

            # 3. 장애 중 메시지 계속 생성 (10초)
            during_fault = await self.producer.produce_continuous(
                interval=1.0, duration=10.0, tag_count=22
            )

            # 4. DB 복구
            await self.docker.start_container(DB_CONTAINER)
            t_recovery_start = time.monotonic()
            healthy = await self.docker.wait_healthy(DB_CONTAINER, timeout=60)
            if not healthy:
                return ScenarioResult(
                    name=self.name, iteration=iteration, passed=False,
                    duration_seconds=time.monotonic() - t0,
                    error="DB failed to become healthy",
                )

            # 5. 복구 후 메시지 추가 생성
            post_recovery = await self.producer.produce_batch(tag_count=22)

            # 6. 데이터 정합성 검증 (모든 메시지가 DB에 도착 대기)
            total_produced = self.producer.total_produced
            reached, actual = await self.verifier.wait_for_count(
                total_produced, timeout=60
            )
            recovery_time = time.monotonic() - t_recovery_start

            data_loss = max(0, total_produced - actual)
            passed = reached and data_loss == 0

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=passed,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=total_produced,
                messages_in_db=actual,
                data_loss=data_loss,
                details=f"baseline={baseline}, during={during_fault}, post={post_recovery}",
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


class DBExtendedOutage(BaseScenario):
    """시나리오 2: DB 장기 중단 (2분)."""

    name = "db_extended_outage"
    description = "DB stop → 120초 → start (queue.db 무손실, 백오프 확인)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            self.producer.reset_counter()
            await self.verifier.truncate_all()

            # 1. 기준 메시지
            await self.producer.produce_batch(tag_count=22)
            await asyncio.sleep(3)

            # 2. DB 중지
            await self.docker.stop_container(DB_CONTAINER)

            # 3. 장애 중 메시지 생성 (2분, 5초 간격)
            await self.producer.produce_continuous(
                interval=5.0, duration=120.0, tag_count=22
            )

            # 4. DB 복구
            await self.docker.start_container(DB_CONTAINER)
            t_recovery_start = time.monotonic()
            await self.docker.wait_healthy(DB_CONTAINER, timeout=60)

            # 5. 검증 (장기 중단이므로 넉넉히 대기)
            total_produced = self.producer.total_produced
            reached, actual = await self.verifier.wait_for_count(
                total_produced, timeout=120
            )
            recovery_time = time.monotonic() - t_recovery_start
            data_loss = max(0, total_produced - actual)

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=reached,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=total_produced,
                messages_in_db=actual,
                data_loss=data_loss,
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


class RMQRestart(BaseScenario):
    """시나리오 4: RabbitMQ 재시작."""

    name = "rmq_restart"
    description = "RabbitMQ restart (connect_robust 자동 복구 확인)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            self.producer.reset_counter()
            await self.verifier.truncate_all()

            # 1. 기준 메시지
            await self.producer.produce_batch(tag_count=22)
            await asyncio.sleep(3)

            # 2. RabbitMQ 재시작
            await self.docker.restart_container(RMQ_CONTAINER)
            t_recovery_start = time.monotonic()

            # 3. RMQ가 다시 올라올 때까지 대기
            await self.docker.wait_healthy(RMQ_CONTAINER, timeout=60)

            # 4. Producer 재연결 (robust connection이 자동 처리)
            try:
                await self.producer.disconnect()
            except Exception:
                pass
            await asyncio.sleep(3)
            await self.producer.connect()

            # 5. 복구 후 메시지 생성
            post_recovery = await self.producer.produce_batch(tag_count=22)
            total_produced = post_recovery  # 재시작 전 메시지는 큐에서 유실 가능

            # 6. 검증 (복구 후 메시지만 검증)
            reached, actual = await self.verifier.wait_for_count(
                post_recovery, timeout=30
            )
            recovery_time = time.monotonic() - t_recovery_start

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=reached,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=total_produced,
                messages_in_db=actual,
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


class RMQNetworkPartition(BaseScenario):
    """시나리오 5: RabbitMQ 네트워크 파티션 (pause 30초)."""

    name = "rmq_network_partition"
    description = "RabbitMQ pause → 30초 → unpause (heartbeat timeout 처리)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            self.producer.reset_counter()
            await self.verifier.truncate_all()

            # 1. 기준 메시지
            await self.producer.produce_batch(tag_count=22)
            await asyncio.sleep(3)

            # 2. RabbitMQ pause
            await self.docker.pause_container(RMQ_CONTAINER)

            # 3. 30초 대기
            await asyncio.sleep(30)

            # 4. RabbitMQ unpause
            await self.docker.unpause_container(RMQ_CONTAINER)
            t_recovery_start = time.monotonic()

            # 5. Producer 재연결
            await asyncio.sleep(5)
            try:
                await self.producer.disconnect()
            except Exception:
                pass
            await self.producer.connect()

            # 6. 복구 후 메시지
            post = await self.producer.produce_batch(tag_count=22)

            # 7. 검증
            reached, actual = await self.verifier.wait_for_count(post, timeout=30)
            recovery_time = time.monotonic() - t_recovery_start

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=reached,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=self.producer.total_produced,
                messages_in_db=actual,
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


class DualFailure(BaseScenario):
    """시나리오 6: DB + RMQ 동시 장애."""

    name = "dual_failure"
    description = "DB + RMQ 동시 stop → RMQ start → DB start (전체 복구)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            self.producer.reset_counter()
            await self.verifier.truncate_all()

            # 1. 기준 메시지
            await self.producer.produce_batch(tag_count=22)
            await asyncio.sleep(3)

            # 2. 둘 다 중지
            await self.docker.stop_container(DB_CONTAINER)
            await self.docker.stop_container(RMQ_CONTAINER)

            await asyncio.sleep(15)

            # 3. RMQ 먼저 복구
            await self.docker.start_container(RMQ_CONTAINER)
            await self.docker.wait_healthy(RMQ_CONTAINER, timeout=60)

            # Producer 재연결
            try:
                await self.producer.disconnect()
            except Exception:
                pass
            await self.producer.connect()

            await asyncio.sleep(5)

            # 4. DB 복구
            await self.docker.start_container(DB_CONTAINER)
            t_recovery_start = time.monotonic()
            await self.docker.wait_healthy(DB_CONTAINER, timeout=60)

            # 5. 복구 후 메시지
            post = await self.producer.produce_batch(tag_count=22)

            # 6. 검증
            reached, actual = await self.verifier.wait_for_count(post, timeout=60)
            recovery_time = time.monotonic() - t_recovery_start

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=reached,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=self.producer.total_produced,
                messages_in_db=actual,
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


class MessageBacklog(BaseScenario):
    """시나리오 9: 대량 백로그 처리."""

    name = "message_backlog"
    description = "DB stop → 500 메시지 → DB start (전체 처리 확인)"

    async def run(self, iteration: int) -> ScenarioResult:
        t0 = time.monotonic()
        try:
            self.producer.reset_counter()
            await self.verifier.truncate_all()

            # 1. DB 중지
            await self.docker.stop_container(DB_CONTAINER)
            await asyncio.sleep(2)

            # 2. 대량 메시지 생성 (500 배치 = 11000 레코드)
            for _ in range(500):
                await self.producer.produce_batch(tag_count=22)

            total_produced = self.producer.total_produced

            # 3. DB 복구
            await self.docker.start_container(DB_CONTAINER)
            t_recovery_start = time.monotonic()
            await self.docker.wait_healthy(DB_CONTAINER, timeout=60)

            # 4. 검증 (대량이므로 넉넉히 대기)
            reached, actual = await self.verifier.wait_for_count(
                total_produced, timeout=180
            )
            recovery_time = time.monotonic() - t_recovery_start
            data_loss = max(0, total_produced - actual)

            return ScenarioResult(
                name=self.name, iteration=iteration, passed=reached,
                duration_seconds=time.monotonic() - t0,
                recovery_seconds=recovery_time,
                messages_produced=total_produced,
                messages_in_db=actual,
                data_loss=data_loss,
            )

        except Exception as e:
            return ScenarioResult(
                name=self.name, iteration=iteration, passed=False,
                duration_seconds=time.monotonic() - t0,
                error=str(e),
            )


# 모든 시나리오 레지스트리
ALL_SCENARIOS = {
    "db_restart_short": DBRestartShort,
    "db_extended_outage": DBExtendedOutage,
    "rmq_restart": RMQRestart,
    "rmq_network_partition": RMQNetworkPartition,
    "dual_failure": DualFailure,
    "message_backlog": MessageBacklog,
}
