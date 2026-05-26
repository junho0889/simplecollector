"""YAML/CSV → neuroforge_config DB 마이그레이션 도구.

기존 deploy/jem_ble/ 같은 파일 기반 config를 통째로 DB 스키마에 idempotent UPSERT.

흐름:
    1) ConfigLoader.load() / load_tags() / load_ble_tags() 로 YAML+CSV 파싱 (기존 코드 그대로 재사용)
    2) AppConfig dataclass + TagDefinition 리스트를 neuroforge_config 행으로 매핑
    3) UPSERT (collector_key/PK ON CONFLICT) — 재실행 안전

대상 디렉토리에서 자동 발견:
    - collector_*.yaml   (각각 1 collector)
    - tags_file가 가리키는 CSV
    - publisher_*.yaml   (1개: settings + collection_groups → publisher_group/snapshot/settings)

사용 예:
    python scripts/yaml_to_config_db.py --dir deploy/jem_ble \
        --db-host localhost --db-port 5435 --db-name neurosense \
        --db-user postgres --db-password ******

    # 또는 env (CONFIG_DB_*)로 접속 정보 주입
    CONFIG_DB_HOST=localhost CONFIG_DB_PORT=5435 ... python scripts/yaml_to_config_db.py --dir deploy/jem_ble
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import asyncpg
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src.core.config import ConfigLoader, AppConfig
from src.core.interfaces import TagDefinition

logger = logging.getLogger("yaml_to_config_db")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _dsn_from_args_or_env(args) -> str:
    def env(*ns, default=""):
        for n in ns:
            v = os.environ.get(n)
            if v is not None:
                return v
        return default
    host = args.db_host or env("CONFIG_DB_HOST", "DB_HOST", default="localhost")
    port = args.db_port or env("CONFIG_DB_PORT", "DB_PORT", default="5432")
    name = args.db_name or env("CONFIG_DB_NAME", "DB_NAME", default="neurosense")
    user = args.db_user or env("CONFIG_DB_USER", "DB_USER", default="postgres")
    pw = args.db_password or env("CONFIG_DB_PASSWORD", "DB_PASSWORD", default="")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


def _address_to_int(memory: str, addr_str: str) -> Optional[int]:
    """TagDefinition.address (예: 'D900' 또는 'D900:10') → 정수 주소.
    Y/X/B 디바이스는 16진수, 그 외는 10진수 (master_sync 와 동일 규칙)."""
    if not addr_str:
        return None
    a = addr_str.strip()
    if ":" in a:
        a = a.split(":", 1)[0]
    m = (memory or "").upper()
    if m and a.upper().startswith(m):
        a = a[len(m):]
    base = 16 if m in ("Y", "X", "B") else 10
    try:
        return int(a, base)
    except ValueError:
        return None


def _discover_files(d: Path) -> Dict[str, Any]:
    """디렉토리에서 collector_*.yaml + publisher_*.yaml 발견."""
    if not d.exists():
        raise FileNotFoundError(f"디렉토리 없음: {d}")
    collectors = sorted(d.glob("collector_*.yaml"))
    publishers = sorted(d.glob("publisher_*.yaml"))
    return {"collectors": collectors, "publishers": publishers}


def _parse_publisher_yaml(path: Path) -> Dict[str, Any]:
    """publisher YAML → settings + collection_groups dict."""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    db = raw.get("database", {})
    return {
        "settings": {
            "schema_name": db.get("schema_name", "public"),
            "insert_method": db.get("insert_method", "copy"),
            "auto_init_schema": db.get("auto_init_schema", True),
            "master_sync_enabled": db.get("master_sync_enabled", True),
            "compression_after": db.get("compression_after", "7 days"),
            "compression_segmentby": db.get("compression_segmentby", "plc_id, tag_id"),
            "compression_orderby": db.get("compression_orderby", "timestamp DESC"),
            "retention_period": db.get("retention_period", ""),
            "device_types": db.get("device_types", ["plc"]),
            "pool_size": int(db.get("pool_size", 5)),
            "pool_min_size": int(db.get("pool_min_size", 2)),
        },
        "groups": db.get("collection_groups", []) or [],
    }


# ---------------------------------------------------------------------------
# Inserters
# ---------------------------------------------------------------------------
async def upsert_collector(
    conn: asyncpg.Connection, schema: str, collector_key: str, cfg: AppConfig
) -> int:
    """collector + 1:1 디테일 + collector_group 행 → collector_id 반환."""
    c = cfg.collector
    device_type = c.device_type  # 'plc' | 'ble'

    row = await conn.fetchrow(
        f"""
        INSERT INTO {schema}.collector
          (collector_key, name, description, site, area, line, enabled, device_type)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        ON CONFLICT (collector_key) DO UPDATE SET
            name=EXCLUDED.name, description=EXCLUDED.description,
            site=EXCLUDED.site, area=EXCLUDED.area, line=EXCLUDED.line,
            enabled=EXCLUDED.enabled, device_type=EXCLUDED.device_type
        RETURNING collector_id
        """,
        collector_key, c.name, c.description or None,
        c.site or None, c.area or None, c.line or None,
        bool(c.enabled), device_type,
    )
    collector_id = row["collector_id"]

    # collector_protocol (1:1)
    p = c.protocol
    if p is not None:
        extra = p.extra or {}
        def _int(v): return int(v) if v is not None and v != "" else None
        await conn.execute(
            f"""
            INSERT INTO {schema}.collector_protocol
              (collector_id, protocol_type, host, port, unit_id, timeout_ms, reconnect_interval_ms,
               plc_series, frame_type, network_no, pc_no, unit_io, unit_station, max_address_gap,
               cache_ttl, duplicate_filter_s)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (collector_id) DO UPDATE SET
                protocol_type=EXCLUDED.protocol_type, host=EXCLUDED.host, port=EXCLUDED.port,
                unit_id=EXCLUDED.unit_id, timeout_ms=EXCLUDED.timeout_ms,
                reconnect_interval_ms=EXCLUDED.reconnect_interval_ms,
                plc_series=EXCLUDED.plc_series, frame_type=EXCLUDED.frame_type,
                network_no=EXCLUDED.network_no, pc_no=EXCLUDED.pc_no,
                unit_io=EXCLUDED.unit_io, unit_station=EXCLUDED.unit_station,
                max_address_gap=EXCLUDED.max_address_gap,
                cache_ttl=EXCLUDED.cache_ttl, duplicate_filter_s=EXCLUDED.duplicate_filter_s
            """,
            collector_id, p.type, p.host or None, p.port or None,
            p.unit_id, p.timeout_ms, p.reconnect_interval_ms,
            extra.get("plc_series"), extra.get("frame_type"),
            _int(extra.get("network_no")), _int(extra.get("pc_no")),
            _int(extra.get("unit_io")), _int(extra.get("unit_station")),
            _int(extra.get("max_address_gap")),
            float(extra["cache_ttl"]) if extra.get("cache_ttl") is not None else None,
            float(extra["duplicate_filter_s"]) if extra.get("duplicate_filter_s") is not None else None,
        )

    # collector_buffer (1:1)
    b = cfg.buffer
    await conn.execute(
        f"""
        INSERT INTO {schema}.collector_buffer
          (collector_id, max_size, batch_size, threshold_ratio,
           drop_oldest, persist_on_shutdown, persist_path)
        VALUES ($1,$2,$3,$4,$5,$6,$7)
        ON CONFLICT (collector_id) DO UPDATE SET
            max_size=EXCLUDED.max_size, batch_size=EXCLUDED.batch_size,
            threshold_ratio=EXCLUDED.threshold_ratio, drop_oldest=EXCLUDED.drop_oldest,
            persist_on_shutdown=EXCLUDED.persist_on_shutdown,
            persist_path=EXCLUDED.persist_path
        """,
        collector_id, b.max_size, b.batch_size, b.threshold_ratio,
        b.drop_oldest, b.persist_on_shutdown, b.persist_path,
    )

    # collector_logging (1:1)
    lg = cfg.logging
    await conn.execute(
        f"""
        INSERT INTO {schema}.collector_logging
          (collector_id, level, collection_level, publish_level, loss_level,
           file_path, max_size_mb, backup_count, format, compress_enabled,
           compress_after_days, archive_after_months, max_archive_count, archive_path,
           json_enabled, json_file_path, ecs_enabled,
           error_detail_enabled, include_traceback, include_context)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20)
        ON CONFLICT (collector_id) DO UPDATE SET
            level=EXCLUDED.level, collection_level=EXCLUDED.collection_level,
            publish_level=EXCLUDED.publish_level, loss_level=EXCLUDED.loss_level,
            file_path=EXCLUDED.file_path, format=EXCLUDED.format
        """,
        collector_id, lg.level, lg.collection_level, lg.publish_level, lg.loss_level,
        lg.file_path, lg.max_size_mb, lg.backup_count, lg.format, lg.compress_enabled,
        lg.compress_after_days, lg.archive_after_months, lg.max_archive_count, lg.archive_path,
        lg.json_enabled, lg.json_file_path, lg.ecs_enabled,
        lg.error_detail_enabled, lg.include_traceback, lg.include_context,
    )

    # collector_rabbitmq (1:1) — 동작값만 (host/user/pass 는 env, 저장 안 함)
    rmq = cfg.publisher.rabbitmq
    await conn.execute(
        f"""
        INSERT INTO {schema}.collector_rabbitmq
          (collector_id, enabled, exchange_name, exchange_type, routing_key_prefix,
           compression, encryption_enabled, heartbeat, connection_timeout, delivery_mode,
           publish_interval_ms, max_retries, retry_delay_ms)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        ON CONFLICT (collector_id) DO UPDATE SET
            enabled=EXCLUDED.enabled, exchange_name=EXCLUDED.exchange_name,
            exchange_type=EXCLUDED.exchange_type, routing_key_prefix=EXCLUDED.routing_key_prefix,
            compression=EXCLUDED.compression, encryption_enabled=EXCLUDED.encryption_enabled,
            heartbeat=EXCLUDED.heartbeat, connection_timeout=EXCLUDED.connection_timeout,
            delivery_mode=EXCLUDED.delivery_mode,
            publish_interval_ms=EXCLUDED.publish_interval_ms,
            max_retries=EXCLUDED.max_retries, retry_delay_ms=EXCLUDED.retry_delay_ms
        """,
        collector_id, rmq.enabled, rmq.exchange_name, rmq.exchange_type, rmq.routing_key_prefix,
        rmq.compression, rmq.encryption_enabled, rmq.heartbeat, rmq.connection_timeout,
        rmq.delivery_mode, cfg.publisher.publish_interval_ms,
        cfg.publisher.max_retries, cfg.publisher.retry_delay_ms,
    )

    # collector_group (1:N) — 깔끔하게 delete then insert
    await conn.execute(
        f"DELETE FROM {schema}.collector_group WHERE collector_id = $1", collector_id)
    for g in c.collection_groups:
        await conn.execute(
            f"""
            INSERT INTO {schema}.collector_group
              (collector_id, name, interval_ms, timeout_ms, retry_count, retry_delay_ms,
               mode, deadband, deadband_type)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            """,
            collector_id, g.name, g.interval_ms, g.timeout_ms,
            g.retry_count, g.retry_delay_ms, g.mode, g.deadband, g.deadband_type,
        )

    return collector_id


async def upsert_devices_and_tags(
    conn: asyncpg.Connection, schema: str, collector_id: int,
    cfg: AppConfig, tags: List[TagDefinition],
) -> Dict[str, int]:
    """device + tag UPSERT. 기존 tag 는 device → tag CASCADE 로 자동 정리되도록
    이 collector 의 device 들의 tag 만 삭제 후 re-insert."""
    c = cfg.collector

    # device
    if c.is_ble:
        for d in c.devices:
            await conn.execute(
                f"""
                INSERT INTO {schema}.device
                  (device_type, device_id, collector_id, name, description,
                   mac_address, device_profile, device_name_filter, collect_yn)
                VALUES ('ble', $1, $2, $3, $4, $5, $6, $7, 'Y')
                ON CONFLICT (device_type, device_id) DO UPDATE SET
                    collector_id=EXCLUDED.collector_id,
                    name=EXCLUDED.name, description=EXCLUDED.description,
                    mac_address=EXCLUDED.mac_address,
                    device_profile=EXCLUDED.device_profile,
                    device_name_filter=EXCLUDED.device_name_filter
                """,
                d.device_id, collector_id,
                d.description or f"BLE{d.device_id}", d.description,
                d.mac_address, d.device_profile, d.device_name_filter,
            )
    else:
        await conn.execute(
            f"""
            INSERT INTO {schema}.device
              (device_type, device_id, collector_id, name, description,
               site, area, line, collect_yn)
            VALUES ('plc', $1, $2, $3, $4, $5, $6, $7, 'Y')
            ON CONFLICT (device_type, device_id) DO UPDATE SET
                collector_id=EXCLUDED.collector_id,
                name=EXCLUDED.name, description=EXCLUDED.description,
                site=EXCLUDED.site, area=EXCLUDED.area, line=EXCLUDED.line
            """,
            c.plc_id, collector_id, c.name, c.description or None,
            c.site or None, c.area or None, c.line or None,
        )

    # tag — 이 collector 소속 device 의 기존 태그만 삭제 후 재삽입
    await conn.execute(
        f"""
        DELETE FROM {schema}.tag
        WHERE (device_type, device_id) IN (
            SELECT device_type, device_id FROM {schema}.device WHERE collector_id = $1
        )
        """,
        collector_id,
    )

    tag_count = 0
    for t in tags:
        if c.is_ble:
            dev_id = t.device_id  # BLE 태그는 자체 device_id 보유
            mem, addr_int, wlen = None, None, None
        else:
            dev_id = c.plc_id
            mem = (t.memory or "").upper() or None
            addr_int = _address_to_int(mem or "", t.address)
            wlen = t.word_length

        if dev_id is None:
            logger.warning(f"  skip tag (device_id 미정): {t.tag_id} {t.tag_name}")
            continue

        await conn.execute(
            f"""
            INSERT INTO {schema}.tag (
                device_type, device_id, tag_id, tag_name, collection_group,
                data_type, memory, address, word_length, string_length, format, raw_type,
                scale, offset_value, decimals, unit,
                bool_true_value, bool_false_value, bool_invert,
                byte_offset, ble_mode, description, collect_yn)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,
                    $17,$18,$19,$20,$21,$22,'Y')
            """,
            "ble" if c.is_ble else "plc", dev_id, t.tag_id, t.tag_name, t.collection_group,
            t.data_type.value, mem, addr_int, wlen, t.string_length,
            t.format or None, t.raw_type or None,
            t.scale, t.offset, t.decimals, t.unit or None,
            t.bool_true_value, t.bool_false_value, t.bool_invert,
            t.byte_offset, t.ble_mode or None, t.description or None,
        )
        tag_count += 1

    return {"devices": len(c.devices) if c.is_ble else 1, "tags": tag_count}


async def upsert_publisher(
    conn: asyncpg.Connection, schema: str, pub: Dict[str, Any]
) -> Dict[str, int]:
    """publisher_settings + publisher_group + snapshot trigger."""
    s = pub["settings"]
    await conn.execute(
        f"""
        INSERT INTO {schema}.publisher_settings (id, schema_name, insert_method,
            auto_init_schema, master_sync_enabled, compression_after,
            compression_segmentby, compression_orderby, retention_period,
            device_types, pool_size, pool_min_size)
        VALUES (1,$1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        ON CONFLICT (id) DO UPDATE SET
            schema_name=EXCLUDED.schema_name, insert_method=EXCLUDED.insert_method,
            auto_init_schema=EXCLUDED.auto_init_schema,
            master_sync_enabled=EXCLUDED.master_sync_enabled,
            compression_after=EXCLUDED.compression_after,
            compression_segmentby=EXCLUDED.compression_segmentby,
            compression_orderby=EXCLUDED.compression_orderby,
            retention_period=EXCLUDED.retention_period,
            device_types=EXCLUDED.device_types,
            pool_size=EXCLUDED.pool_size, pool_min_size=EXCLUDED.pool_min_size
        """,
        s["schema_name"], s["insert_method"], s["auto_init_schema"],
        s["master_sync_enabled"], s["compression_after"], s["compression_segmentby"],
        s["compression_orderby"], s["retention_period"], list(s["device_types"]),
        s["pool_size"], s["pool_min_size"],
    )

    group_count = 0
    trig_count = 0
    for g in pub["groups"]:
        ext = g.get("extensions", {}) or {}
        hist = ext.get("history") or {}
        snap = ext.get("snapshot") or {}
        row = await conn.fetchrow(
            f"""
            INSERT INTO {schema}.publisher_group (name, mode, device_type,
                retention_period, compression_after,
                history_enabled, history_trigger_on)
            VALUES ($1,$2,$3,$4,$5,$6,$7)
            ON CONFLICT (name) DO UPDATE SET
                mode=EXCLUDED.mode, device_type=EXCLUDED.device_type,
                retention_period=EXCLUDED.retention_period,
                compression_after=EXCLUDED.compression_after,
                history_enabled=EXCLUDED.history_enabled,
                history_trigger_on=EXCLUDED.history_trigger_on
            RETURNING id
            """,
            g["name"], g.get("mode", "all"), g.get("device_type") or None,
            g.get("retention_period") or None, g.get("compression_after") or None,
            bool(hist), hist.get("trigger_on", "all"),
        )
        group_id = row["id"]
        group_count += 1

        # snapshot triggers: 기존 삭제 후 재삽입
        await conn.execute(
            f"DELETE FROM {schema}.publisher_group_snapshot_trigger WHERE group_id = $1",
            group_id,
        )
        for trig in snap.get("triggers", []) or []:
            await conn.execute(
                f"""
                INSERT INTO {schema}.publisher_group_snapshot_trigger
                  (group_id, watch_tag, plc_ids, capture_tags, enabled)
                VALUES ($1, $2, $3, $4, TRUE)
                """,
                group_id, int(trig["watch_tag"]),
                list(trig.get("plc_ids", []) or []),
                list(trig.get("capture_tags", []) or []),
            )
            trig_count += 1

    return {"groups": group_count, "triggers": trig_count}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    src_dir = Path(args.dir).resolve()
    files = _discover_files(src_dir)

    logger.info(f"디렉토리: {src_dir}")
    logger.info(f"  collector_*.yaml : {len(files['collectors'])}개")
    logger.info(f"  publisher_*.yaml : {len(files['publishers'])}개")

    # 1) YAML/CSV 파싱 (기존 ConfigLoader 재사용)
    plans = []
    for c_yaml in files["collectors"]:
        cfg = ConfigLoader.load(c_yaml)
        # tags_file 경로 보정 (yaml 의 상대경로 → 같은 dir 안에서 찾기)
        tags_rel = Path(cfg.collector.tags_file)
        tags_path = src_dir / tags_rel.name if not (src_dir / tags_rel).exists() else (src_dir / tags_rel)
        if not tags_path.exists():
            # config/ 접두어 빠진 경우 대비
            cand = src_dir / Path(cfg.collector.tags_file).name
            tags_path = cand if cand.exists() else tags_path
        if cfg.collector.devices:
            tags = ConfigLoader.load_ble_tags(tags_path, cfg.collector.devices)
        else:
            tags = ConfigLoader.load_tags(tags_path)
        # collector_key = YAML 파일명 stem (예: collector_plc1 → 'collector_plc1')
        key = c_yaml.stem
        plans.append({"key": key, "yaml": c_yaml, "cfg": cfg, "tags": tags})
        logger.info(f"  parsed {c_yaml.name}: {cfg.collector.name} "
                    f"({'BLE×' + str(len(cfg.collector.devices)) if cfg.collector.devices else 'PLC plc_id=' + str(cfg.collector.plc_id)}), "
                    f"{len(tags)} tags")

    pub_data: Optional[Dict[str, Any]] = None
    pub_file = None
    if files["publishers"]:
        pub_file = files["publishers"][0] if not args.publisher else (src_dir / args.publisher)
        pub_data = _parse_publisher_yaml(pub_file)
        logger.info(f"  parsed {pub_file.name}: "
                    f"{len(pub_data['groups'])} groups, schema_name={pub_data['settings']['schema_name']}")

    if args.dry_run:
        logger.info("[dry-run] DB 변경 없이 종료")
        return 0

    # 2) DB 연결
    dsn = _dsn_from_args_or_env(args)
    schema = args.schema
    logger.info(f"DB 접속: {dsn.replace(args.db_password or os.environ.get('CONFIG_DB_PASSWORD',''), '****') if (args.db_password or os.environ.get('CONFIG_DB_PASSWORD')) else dsn}")
    pool = await asyncpg.create_pool(dsn=dsn, min_size=1, max_size=2)

    # 3) UPSERT
    totals = {"collectors": 0, "devices": 0, "tags": 0, "pub_groups": 0, "pub_triggers": 0}
    async with pool.acquire() as conn:
        async with conn.transaction():
            for p in plans:
                cid = await upsert_collector(conn, schema, p["key"], p["cfg"])
                stats = await upsert_devices_and_tags(conn, schema, cid, p["cfg"], p["tags"])
                totals["collectors"] += 1
                totals["devices"] += stats["devices"]
                totals["tags"] += stats["tags"]
                logger.info(f"  ✓ {p['key']:30s} id={cid} devices={stats['devices']} tags={stats['tags']}")

            if pub_data is not None:
                ps = await upsert_publisher(conn, schema, pub_data)
                totals["pub_groups"] = ps["groups"]
                totals["pub_triggers"] = ps["triggers"]
                logger.info(f"  ✓ publisher: groups={ps['groups']} snapshot_triggers={ps['triggers']}")

    await pool.close()

    logger.info("")
    logger.info("===== 완료 =====")
    logger.info(f"  collectors     : {totals['collectors']}")
    logger.info(f"  devices        : {totals['devices']}")
    logger.info(f"  tags           : {totals['tags']}")
    logger.info(f"  pub groups     : {totals['pub_groups']}")
    logger.info(f"  pub snapshots  : {totals['pub_triggers']}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="YAML/CSV → neuroforge_config 마이그레이션")
    ap.add_argument("--dir", required=True, help="collector_*.yaml + tags + publisher_*.yaml 디렉토리")
    ap.add_argument("--publisher", help="publisher YAML 파일명(여러 개일 때, dir 기준 상대경로)")
    ap.add_argument("--schema", default=os.environ.get("CONFIG_DB_SCHEMA", "neuroforge_config"))
    ap.add_argument("--db-host", default=None)
    ap.add_argument("--db-port", default=None)
    ap.add_argument("--db-name", default=None)
    ap.add_argument("--db-user", default=None)
    ap.add_argument("--db-password", default=None)
    ap.add_argument("--dry-run", action="store_true", help="DB 변경 없이 파싱만")
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
