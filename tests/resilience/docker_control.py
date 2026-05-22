"""
Docker 컨테이너 제어 유틸리티
==============================
테스트 시나리오에서 RabbitMQ, TimescaleDB 컨테이너를 제어합니다.
"""

import asyncio
import logging
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

COMPOSE_DIR = str(__import__("pathlib").Path(__file__).parent)


def _run(cmd: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """동기 subprocess 실행."""
    return subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )


class DockerControl:
    """Docker 컨테이너 제어."""

    def __init__(self, compose_dir: str = COMPOSE_DIR):
        self.compose_dir = compose_dir

    def _compose(self, subcmd: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return _run(
            f'docker compose -f "{self.compose_dir}/docker-compose.yml" {subcmd}',
            timeout=timeout,
        )

    # ── Stack 제어 ──────────────────────────────────────────────

    async def start_stack(self) -> None:
        """docker compose up -d + 헬스체크 대기."""
        logger.info("Starting test stack...")
        result = self._compose("up -d --wait", timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"Stack start failed: {result.stderr}")
        logger.info("Test stack ready")

    async def stop_stack(self) -> None:
        """docker compose down -v (볼륨 포함 정리)."""
        logger.info("Stopping test stack...")
        self._compose("down -v", timeout=60)
        logger.info("Test stack stopped")

    # ── 컨테이너 제어 ──────────────────────────────────────────

    async def stop_container(self, name: str) -> None:
        """docker stop."""
        logger.info(f"Stopping container: {name}")
        _run(f"docker stop {name}")

    async def start_container(self, name: str) -> None:
        """docker start."""
        logger.info(f"Starting container: {name}")
        _run(f"docker start {name}")

    async def restart_container(self, name: str) -> None:
        """docker restart."""
        logger.info(f"Restarting container: {name}")
        _run(f"docker restart {name}")

    async def pause_container(self, name: str) -> None:
        """docker pause (네트워크 동결 시뮬레이션)."""
        logger.info(f"Pausing container: {name}")
        _run(f"docker pause {name}")

    async def unpause_container(self, name: str) -> None:
        """docker unpause."""
        logger.info(f"Unpausing container: {name}")
        _run(f"docker unpause {name}")

    async def kill_container(self, name: str, signal: str = "KILL") -> None:
        """docker kill -s <signal>."""
        logger.info(f"Killing container: {name} (signal={signal})")
        _run(f"docker kill -s {signal} {name}")

    # ── 상태 확인 ──────────────────────────────────────────────

    async def is_running(self, name: str) -> bool:
        """컨테이너 실행 중 여부."""
        result = _run(f'docker inspect -f "{{{{.State.Running}}}}" {name}')
        return result.stdout.strip() == "true"

    async def wait_healthy(self, name: str, timeout: int = 60) -> bool:
        """컨테이너가 healthy 상태가 될 때까지 대기."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = _run(
                f'docker inspect -f "{{{{.State.Health.Status}}}}" {name}'
            )
            status = result.stdout.strip()
            if status == "healthy":
                return True
            await asyncio.sleep(1)
        logger.warning(f"Container {name} not healthy after {timeout}s")
        return False

    async def get_container_logs(
        self, name: str, since: str = "30s"
    ) -> str:
        """컨테이너 로그 가져오기."""
        result = _run(f"docker logs --since {since} {name} 2>&1")
        return result.stdout

    # ── RabbitMQ 유틸 ──────────────────────────────────────────

    async def get_rmq_queue_depth(self, queue_name: str) -> int:
        """RabbitMQ 큐 메시지 수 조회 (management API)."""
        import json
        result = _run(
            f'docker exec resilience-rmq rabbitmqctl list_queues name messages '
            f'--formatter json'
        )
        try:
            queues = json.loads(result.stdout)
            for q in queues:
                if q.get("name") == queue_name:
                    return q.get("messages", 0)
        except (json.JSONDecodeError, KeyError):
            pass
        return 0
