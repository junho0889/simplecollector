"""
DB 데이터 정합성 검증기
========================
TimescaleDB에서 데이터 정합성을 검증합니다.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg

logger = logging.getLogger(__name__)


class DBVerifier:
    """TimescaleDB 데이터 검증."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 25432,
        database: str = "test_resilience",
        user: str = "testuser",
        password: str = "testpass",
        schema: str = "public",
    ):
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self.schema = schema
        self._pool: Optional[asyncpg.Pool] = None

    async def connect(self) -> None:
        """DB 연결."""
        dsn = (
            f"postgresql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )
        self._pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=3)
        logger.info(f"Verifier connected: {self.host}:{self.port}/{self.database}")

    async def disconnect(self) -> None:
        """DB 연결 해제."""
        if self._pool:
            await self._pool.close()
        self._pool = None

    async def count_integrated(self, group: str = "plc_data") -> int:
        """integrated 테이블 행 수."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) as cnt FROM {self.schema}.{group}_integrated"
            )
            return row["cnt"] if row else 0

    async def count_latest(self, group: str = "plc_data") -> int:
        """latest 테이블 행 수."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) as cnt FROM {self.schema}.{group}_latest"
            )
            return row["cnt"] if row else 0

    async def get_latest_max_timestamp(self, group: str = "plc_data") -> Optional[datetime]:
        """latest 테이블 최신 timestamp."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT MAX(timestamp) as max_ts FROM {self.schema}.{group}_latest"
            )
            return row["max_ts"] if row else None

    async def verify_no_data_loss(
        self,
        expected_count: int,
        group: str = "plc_data",
        tolerance: float = 0.0,
    ) -> tuple[bool, int]:
        """
        데이터 손실 검증.

        Returns:
            (pass, actual_count)
        """
        actual = await self.count_integrated(group)
        min_expected = int(expected_count * (1 - tolerance))
        passed = actual >= min_expected
        if not passed:
            logger.warning(
                f"Data loss detected: expected>={min_expected}, actual={actual} "
                f"(missing={min_expected - actual})"
            )
        return passed, actual

    async def verify_latest_freshness(
        self, group: str = "plc_data", max_age_seconds: int = 60
    ) -> bool:
        """latest 값의 신선도 검증."""
        max_ts = await self.get_latest_max_timestamp(group)
        if not max_ts:
            return False
        age = (datetime.now(timezone.utc) - max_ts.replace(tzinfo=timezone.utc)).total_seconds()
        fresh = age <= max_age_seconds
        if not fresh:
            logger.warning(f"Latest data stale: age={age:.1f}s (max={max_age_seconds}s)")
        return fresh

    async def truncate_all(self, group: str = "plc_data") -> None:
        """테스트 데이터 초기화."""
        async with self._pool.acquire() as conn:
            for table in [f"{group}_integrated", f"{group}_latest"]:
                try:
                    await conn.execute(
                        f"TRUNCATE TABLE {self.schema}.{table}"
                    )
                except Exception:
                    pass  # 테이블 없으면 무시

    async def wait_for_count(
        self,
        expected: int,
        group: str = "plc_data",
        timeout: int = 60,
        poll_interval: float = 1.0,
    ) -> tuple[bool, int]:
        """expected 건수에 도달할 때까지 대기."""
        import time
        deadline = time.monotonic() + timeout
        actual = 0
        while time.monotonic() < deadline:
            actual = await self.count_integrated(group)
            if actual >= expected:
                return True, actual
            await asyncio.sleep(poll_interval)
        return False, actual
