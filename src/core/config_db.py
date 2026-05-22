"""
Config DB Reader (collector-side)
=================================

neuroforge_config 스키마(권위 config)를 읽어 collector의 AppConfig + 태그를 구성한다.
YAML/CSV 파일 대신 DB에서 설정을 읽는 경로(CONFIG_SOURCE=db)에서 사용.

식별: 컨테이너 1개 = collector 1개 → 환경변수 COLLECTOR_KEY 로 vw_collector 행을 선택.

연결: 환경변수 CONFIG_DB_* (없으면 DB_* 폴백).
    CONFIG_DB_HOST / CONFIG_DB_PORT / CONFIG_DB_NAME / CONFIG_DB_USER / CONFIG_DB_PASSWORD
    CONFIG_DB_SCHEMA (기본: neuroforge_config)

DB를 못 읽으면 예외를 올린다 — 호출자(main)가 접속될 때까지 재시도한다.
(config 없이 수집은 무의미하므로 빈 config로 시작하지 않는다)
"""

import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_SCHEMA = "neuroforge_config"
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def config_source() -> str:
    """config 소스: 'db' 또는 'file' (기본 file)."""
    return os.environ.get("CONFIG_SOURCE", "file").strip().lower()


class CollectorConfigDbReader:
    """neuroforge_config 스키마에서 collector 설정을 읽는 리더."""

    def __init__(self, schema: Optional[str] = None):
        self._schema = schema or os.environ.get(
            "CONFIG_DB_SCHEMA", DEFAULT_CONFIG_SCHEMA)
        if not _IDENT_RE.match(self._schema):
            raise ValueError(f"Invalid config schema name: {self._schema!r}")
        self._pool = None  # asyncpg.Pool

    @staticmethod
    def _build_dsn() -> str:
        def env(*names: str, default: str = "") -> str:
            for n in names:
                v = os.environ.get(n)
                if v is not None:
                    return v
            return default
        host = env("CONFIG_DB_HOST", "DB_HOST", default="localhost")
        port = env("CONFIG_DB_PORT", "DB_PORT", default="5432")
        db = env("CONFIG_DB_NAME", "DB_NAME", default="neurosense")
        user = env("CONFIG_DB_USER", "DB_USER", default="postgres")
        pw = env("CONFIG_DB_PASSWORD", "DB_PASSWORD", default="")
        return f"postgresql://{user}:{pw}@{host}:{port}/{db}"

    async def connect(self) -> None:
        import asyncpg
        if self._pool is None:
            self._pool = await asyncpg.create_pool(
                dsn=self._build_dsn(), min_size=1, max_size=2, command_timeout=30)
            logger.info(f"Config DB connected (schema={self._schema})")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        async with self._pool.acquire(timeout=10) as conn:
            rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def read_collector(self, collector_key: str) -> Optional[Dict[str, Any]]:
        rows = await self._fetch(
            f"SELECT * FROM {self._schema}.vw_collector WHERE collector_key = $1",
            collector_key)
        return rows[0] if rows else None

    async def read_collector_groups(self, collector_id: int) -> List[Dict[str, Any]]:
        return await self._fetch(
            f"SELECT * FROM {self._schema}.collector_group "
            f"WHERE collector_id = $1 ORDER BY id", collector_id)

    async def read_devices(self, collector_id: int) -> List[Dict[str, Any]]:
        return await self._fetch(
            f"SELECT * FROM {self._schema}.vw_device "
            f"WHERE collector_id = $1 ORDER BY device_id", collector_id)

    async def read_tags(self, collector_id: int) -> List[Dict[str, Any]]:
        return await self._fetch(
            f"SELECT * FROM {self._schema}.vw_tag "
            f"WHERE collector_id = $1 AND collect_yn = 'Y' "
            f"ORDER BY device_id, tag_id", collector_id)

    async def load(
        self, collector_key: str
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]],
               List[Dict[str, Any]], List[Dict[str, Any]]]:
        """collector 설정 전체를 읽는다.

        Returns:
            (collector_row, group_rows, device_rows, tag_rows)

        Raises:
            KeyError: collector_key 에 해당하는 collector가 없음
        """
        collector = await self.read_collector(collector_key)
        if collector is None:
            raise KeyError(
                f"Collector not found in config DB: collector_key={collector_key!r}")
        collector_id = collector["collector_id"]
        groups = await self.read_collector_groups(collector_id)
        devices = await self.read_devices(collector_id)
        tags = await self.read_tags(collector_id)
        return collector, groups, devices, tags
