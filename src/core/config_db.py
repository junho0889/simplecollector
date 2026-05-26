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

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_SCHEMA = "neuroforge_config"
# neuroforge_config 계약 버전 — publisher 쪽과 동일 값 유지 (schema_meta에 기록)
CONFIG_SCHEMA_VERSION = "1.0.0"
# catalog_publish_log 중복 INSERT 방지 간격
PUBLISH_LOG_DEDUP_MIN = 30
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

    @staticmethod
    def build_collector_capabilities() -> Dict[str, Any]:
        """collector가 publish하는 메타 capabilities.

        - enum_meta: collector 정의 enum (device_type/protocol_type/data_type/memory/...)
        - schema_meta_tables: collector가 사용하는 테이블 목록
        (table_naming/constraint/queues는 publisher 소관)
        """
        data_types = [
            "bool", "int8", "int16", "int32", "int64",
            "uint8", "uint16", "uint32", "uint64",
            "float32", "float64", "byte", "word", "dword", "lword", "string",
        ]
        memory_codes = [
            ("D", "데이터 레지스터 (word)"), ("W", "링크 레지스터 (word)"),
            ("R", "파일 레지스터 (word)"), ("ZR", "확장 파일 레지스터 (word)"),
            ("TN", "타이머 현재값 (word)"), ("CN", "카운터 현재값 (word)"),
            ("X", "입력 (bit)"), ("Y", "출력 (bit)"),
            ("M", "내부 릴레이 (bit)"), ("L", "래치 릴레이 (bit)"),
            ("F", "어넌시에이터 (bit)"), ("V", "에지 릴레이 (bit)"),
            ("B", "링크 릴레이 (bit)"),
        ]
        protocol_types = [
            ("mc_protocol", "Mitsubishi MC Protocol"),
            ("modbus", "Modbus TCP/RTU"),
            ("ble", "BLE"),
            ("lora_rak5146", "LoRa (RAK5146)"),
        ]
        return {
            "schema_meta_tables": [
                "collector", "collector_protocol", "collector_buffer",
                "collector_logging", "collector_rabbitmq", "collector_group",
                "device", "tag",
            ],
            "enum_meta": (
                [{"table_name": "collector", "column_name": "device_type",
                  "value": v, "label": l, "sort_order": i + 1}
                 for i, (v, l) in enumerate([("plc", "PLC"), ("ble", "BLE")])]
                + [{"table_name": "collector_protocol", "column_name": "protocol_type",
                    "value": v, "label": l, "sort_order": i + 1}
                   for i, (v, l) in enumerate(protocol_types)]
                + [{"table_name": "tag", "column_name": "data_type",
                    "value": v, "label": v.upper(), "sort_order": i + 1}
                   for i, v in enumerate(data_types)]
                + [{"table_name": "tag", "column_name": "memory",
                    "value": v, "label": l, "sort_order": i + 1}
                   for i, (v, l) in enumerate(memory_codes)]
                + [{"table_name": "collector_group", "column_name": "mode",
                    "value": v, "label": l, "sort_order": i + 1}
                   for i, (v, l) in enumerate([
                       ("polling", "폴링 (주기)"), ("on_change", "변경 시에만")])]
                + [{"table_name": "collector_group", "column_name": "deadband_type",
                    "value": v, "label": l, "sort_order": i + 1}
                   for i, (v, l) in enumerate([
                       ("absolute", "절대값"), ("percent", "퍼센트")])]
            ),
        }

    async def publish_catalog(
        self, *, component: str, instance_id: str, version: str,
        capabilities: Dict[str, Any],
        image_tag: Optional[str] = None, git_sha: Optional[str] = None,
    ) -> bool:
        """schema_meta + 메타 UPSERT + catalog_publish_log INSERT(변경 시).

        실패 시 예외 전파 (호출자=부팅 path 블로킹). collector는 DDL 소유자가
        아니므로 ddl_sha=NULL. 테이블은 publisher가 만들었다고 가정 (없으면 raise).
        Returns: 로그에 INSERT됐으면 True.
        """
        caps_json = json.dumps(
            capabilities, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        caps_hash = hashlib.sha256(caps_json.encode("utf-8")).hexdigest()

        async with self._pool.acquire(timeout=10) as conn:
            async with conn.transaction():
                # 1. schema_meta (collector는 ddl_sha NULL)
                await conn.execute(f"""
                    INSERT INTO {self._schema}.schema_meta
                        (component, schema_version, ddl_sha, applied_at)
                    VALUES ($1, $2, NULL, NOW())
                    ON CONFLICT (component) DO UPDATE SET
                        schema_version = EXCLUDED.schema_version,
                        applied_at = NOW()
                """, component, version)

                # 2. enum_meta
                for e in capabilities.get("enum_meta", []):
                    await conn.execute(f"""
                        INSERT INTO {self._schema}.enum_meta
                            (table_name, column_name, value, label, sort_order)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (table_name, column_name, value) DO UPDATE SET
                            label = EXCLUDED.label,
                            sort_order = EXCLUDED.sort_order
                    """, e["table_name"], e["column_name"], e["value"],
                        e.get("label"), e.get("sort_order", 0))

                # 3. catalog_publish_log (dedup by hash + 30min window)
                last = await conn.fetchrow(f"""
                    SELECT capabilities_hash, started_at
                    FROM {self._schema}.catalog_publish_log
                    WHERE component = $1 AND instance_id = $2
                    ORDER BY started_at DESC LIMIT 1
                """, component, instance_id)

                reason: Optional[str] = None
                if last is None:
                    reason = "first publish"
                elif last["capabilities_hash"] != caps_hash:
                    reason = "capabilities changed"
                elif (datetime.now(timezone.utc) - last["started_at"]
                      ) > timedelta(minutes=PUBLISH_LOG_DEDUP_MIN):
                    reason = f">{PUBLISH_LOG_DEDUP_MIN}min since last"

                if reason:
                    await conn.execute(f"""
                        INSERT INTO {self._schema}.catalog_publish_log
                            (component, instance_id, version, image_tag, git_sha,
                             capabilities, capabilities_hash)
                        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
                    """, component, instance_id, version, image_tag, git_sha,
                        caps_json, caps_hash)
                    logger.info(
                        f"catalog_publish_log: NEW ({component}/{instance_id}, "
                        f"{reason}, hash={caps_hash[:12]})")
                    return True
                logger.debug(
                    f"catalog_publish_log: SKIP "
                    f"({component}/{instance_id}, hash unchanged, <{PUBLISH_LOG_DEDUP_MIN}min)")
                return False

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
