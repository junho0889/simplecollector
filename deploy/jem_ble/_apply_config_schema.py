"""neurosense2 에 neuroforge_config 스키마(권위 config DDL) 적용.
+ 앞서 잘못 만든 jem_jh02(데이터 스키마) 제거.
ConfigDbReader.apply_schema 와 동일하게 DDL 파일을 통째로 실행(IF NOT EXISTS, idempotent)."""
import asyncio, asyncpg

DSN = dict(host="192.168.0.252", port=5432, user="neuro",
           password=__import__("os").environ.get("PGPW", ""), database="neurosense2", timeout=20)
DDL = r"d:\4.source\collector-publisher\sql\neuroforge_config_schema.sql"

async def main():
    sql = open(DDL, encoding="utf-8").read()
    c = await asyncpg.connect(**DSN)
    try:
        # 1) 잘못 만든 데이터 스키마 정리
        await c.execute("DROP SCHEMA IF EXISTS jem_jh02 CASCADE")
        print("[cleanup] dropped jem_jh02 (mistaken data schema)")

        # 2) neuroforge_config DDL 적용 (파일 통째 실행)
        await c.execute(sql)
        print("[apply] neuroforge_config_schema.sql executed")

        # 3) 결과
        tabs = await c.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='neuroforge_config' AND table_type='BASE TABLE' ORDER BY 1")
        views = await c.fetch(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_schema='neuroforge_config' ORDER BY 1")
        print(f"\n[result] neuroforge_config: tables={len(tabs)}  views={len(views)}")
        print(" TABLES:", ", ".join(r["table_name"] for r in tabs))
        print(" VIEWS :", ", ".join(r["table_name"] for r in views))
    finally:
        await c.close()

asyncio.run(main())
