import asyncio, asyncpg

async def main():
    c = await asyncpg.connect(host='192.168.0.252', port=5433, user='user',
                              password=__import__("os").environ.get("PGPW", ""), database='cortex')
    print('connected:', await c.fetchval('select current_database()'))
    print('server_ver:', (await c.fetchval('show server_version')))
    print('timescaledb_installed:', await c.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname='timescaledb'"))
    print('timescaledb_available:', await c.fetchval(
        "SELECT default_version FROM pg_available_extensions WHERE name='timescaledb'"))
    print('is_superuser:', await c.fetchval('SELECT current_setting($1, true)', 'is_superuser'))
    se = await c.fetchval("SELECT 1 FROM information_schema.schemata WHERE schema_name='jem_jh02'")
    print('jem_jh02_schema_exists:', bool(se))
    print('jem_jh02_table_count:', await c.fetchval(
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='jem_jh02'"))
    await c.close()

asyncio.run(main())
