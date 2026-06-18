"""cortex jem_jh02 에서 모든 트리거/함수 + extension 테이블(alm_history, log_snapshot) 제거.
→ 순수 테이블(master/latest/integrated)만 남김. (SQL 로직 전부 제외)
"""
import asyncio, asyncpg

DSN = dict(host="192.168.0.252", port=5433, user="cortex",
           password=__import__("os").environ.get("PGPW", ""), database="cortex", timeout=15)
SCHEMA = "jem_jh02"
EXT_TABLES = ["alm_history", "log_snapshot"]  # extension 전용 테이블

async def main():
    c = await asyncpg.connect(**DSN)
    try:
        # 1) 트리거 조회 + 제거
        trigs = await c.fetch("""
            SELECT t.tgname, cl.relname AS tbl
            FROM pg_trigger t JOIN pg_class cl ON cl.oid=t.tgrelid
            JOIN pg_namespace n ON n.oid=cl.relnamespace
            WHERE n.nspname=$1 AND NOT t.tgisinternal""", SCHEMA)
        for r in trigs:
            await c.execute(f'DROP TRIGGER IF EXISTS {r["tgname"]} ON {SCHEMA}.{r["tbl"]}')
        print(f"[drop] triggers={len(trigs)}: " + ", ".join(r["tgname"] for r in trigs))

        # 2) 함수 조회 + 제거 (시그니처 포함)
        funcs = await c.fetch("""
            SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=$1""", SCHEMA)
        for r in funcs:
            await c.execute(f'DROP FUNCTION IF EXISTS {SCHEMA}.{r["proname"]}({r["args"]}) CASCADE')
        print(f"[drop] functions={len(funcs)}: " + ", ".join(r["proname"] for r in funcs))

        # 3) extension 전용 테이블 제거
        for t in EXT_TABLES:
            await c.execute(f"DROP TABLE IF EXISTS {SCHEMA}.{t} CASCADE")
        print(f"[drop] ext_tables: {', '.join(EXT_TABLES)}")

        # 4) 최종 상태
        tbls = await c.fetch("SELECT table_name FROM information_schema.tables WHERE table_schema=$1 ORDER BY 1", SCHEMA)
        tg = await c.fetchval("SELECT count(*) FROM pg_trigger t JOIN pg_class cl ON cl.oid=t.tgrelid JOIN pg_namespace n ON n.oid=cl.relnamespace WHERE n.nspname=$1 AND NOT t.tgisinternal", SCHEMA)
        fn = await c.fetchval("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=$1", SCHEMA)
        print(f"\n[final] tables={len(tbls)} triggers={tg} functions={fn}")
        for r in tbls:
            print("   -", r["table_name"])
    finally:
        await c.close()

asyncio.run(main())
