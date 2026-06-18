"""
jem_ble 기본 스키마(custom_init.sql 제외)를 cortex DB에 적용.
publisher 의 ConfigLoader + schema_init 으로 DDL 생성 → 문별 실행(중복 허용).
안전: jem_jh02 에 이미 테이블이 있으면 중단(덮어쓰기 방지).
"""
import asyncio, sys
sys.path.insert(0, r"d:\4.source\collector-publisher")

import asyncpg
from src.config import ConfigLoader
from src.publishers.schema_init import get_global_init_sql, get_group_init_sql

YAML = r"d:\4.source\simpleCollector\deploy\jem_ble\publisher_db1.yaml"
SCHEMA = "jem_jh02"
DSN = dict(host="192.168.0.252", port=5432, user="neuro",
           password=__import__("os").environ.get("PGPW", ""), database="neurosense2")

def build_statements():
    cfg = ConfigLoader.load(YAML).database
    devs = [d.lower() for d in cfg.device_types]
    stmts = []
    for dev in devs:
        stmts += get_global_init_sql(SCHEMA, device_type=dev)
    for gc in cfg.collection_groups:
        dev_list = [gc.device_type.lower()] if gc.device_type else devs
        for dev in dev_list:
            stmts += get_group_init_sql(
                SCHEMA, gc.name, mode=gc.mode,
                compression_after=cfg.resolve_compression_after(gc),
                compression_segmentby=cfg.compression_segmentby,
                compression_orderby=cfg.compression_orderby,
                retention_period=cfg.resolve_retention(gc),
                extensions=None, device_type=dev,   # SQL(트리거/함수/extension) 제외 — 순수 테이블만
            )
    return stmts

async def main():
    stmts = build_statements()
    print(f"[gen] {len(stmts)} DDL statements (schema={SCHEMA})")
    c = await asyncpg.connect(**DSN, timeout=15)
    try:
        existing = await c.fetchval(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema=$1", SCHEMA)
        print(f"[pre] jem_jh02 기존 테이블 수: {existing}")
        if existing and existing > 0:
            print("[ABORT] jem_jh02 에 이미 테이블이 있습니다 — 덮어쓰기 방지 위해 중단. "
                  "(IF NOT EXISTS라 무해하지만 확인 후 강제하려면 --force)")
            if "--force" not in sys.argv:
                return
        ok = skip = err = 0
        for s in stmts:
            s = s.strip()
            if not s:
                continue
            try:
                await c.execute(s)
                ok += 1
            except (asyncpg.exceptions.DuplicateTableError,
                    asyncpg.exceptions.DuplicateObjectError) as e:
                skip += 1
            except Exception as e:
                err += 1
                print(f"  [ERR] {type(e).__name__}: {str(e)[:120]}  ::  {s[:80]}")
        print(f"[apply] ok={ok} skip(exists)={skip} err={err}")
        rows = await c.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=$1 ORDER BY table_name", SCHEMA)
        print(f"[post] jem_jh02 테이블 {len(rows)}개:")
        for r in rows:
            print("   -", r["table_name"])
    finally:
        await c.close()

asyncio.run(main())
