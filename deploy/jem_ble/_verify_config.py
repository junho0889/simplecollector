import asyncio, asyncpg, csv, io
from pathlib import Path
JEM = Path(r"d:\4.source\simpleCollector\deploy\jem_ble")
DSN = dict(host="192.168.0.252", port=5432, user="neuro", password=__import__("os").environ.get("PGPW", ""), database="neurosense2", timeout=20)

def csv_count(name):
    f = JEM / name
    if not f.is_file(): return None
    rows = list(csv.DictReader(open(f, encoding="utf-8-sig")))
    return sum(1 for r in rows if (r.get("tag_id") or "").strip())

async def main():
    c = await asyncpg.connect(**DSN)
    print("="*70)
    print("1) COLLECTOR + PROTOCOL")
    for r in await c.fetch("""
        SELECT co.collector_id id, co.collector_key, co.name, co.device_type, co.site, co.line,
               p.protocol_type, p.host, p.port, p.plc_series, p.frame_type
        FROM neuroforge_config.collector co
        LEFT JOIN neuroforge_config.collector_protocol p USING(collector_id)
        ORDER BY co.collector_id"""):
        print(f"  [{r['id']:2}] {r['collector_key']:22} {r['name']:8} {r['device_type']} "
              f"{r['protocol_type']:11} {r['host']}:{r['port']} {r['plc_series']}/{r['frame_type']}")

    print("="*70)
    print("2) DEVICE + TAG COUNT (DB vs CSV)")
    csvmap = {1:"tags_04_PLC-A.csv",2:"tags_02_PLC-B.csv",3:"tags_05_PLC-C.csv",4:"tags_10_PLC-D.csv",
              5:"tags_01_PLC-EFG.csv",6:"tags_06_PLC-H.csv",7:"tags_07_PLC-I.csv",8:"tags_03_PLC-J.csv",
              9:"tags_08_PLC-K.csv",10:"tags_09_PLC-L.csv"}
    tot_db=tot_csv=0
    for r in await c.fetch("""SELECT d.device_id, d.name, count(t.*) tags
        FROM neuroforge_config.device d LEFT JOIN neuroforge_config.tag t
          ON t.device_type=d.device_type AND t.device_id=d.device_id
        GROUP BY d.device_id, d.name ORDER BY d.device_id"""):
        cc = csv_count(csvmap.get(r['device_id'],""))
        miss = (cc - r['tags']) if cc else 0
        tot_db += r['tags']; tot_csv += (cc or 0)
        print(f"  plc_id={r['device_id']:2} {r['name']:8} DB={r['tags']:5}  CSV={cc}  누락(hex)={miss}")
    print(f"  ── 합계: DB={tot_db}  CSV={tot_csv}  누락={tot_csv-tot_db}")

    print("="*70)
    print("3) collection_group / tag 분포")
    print("  groups:", await c.fetchval("SELECT count(*) FROM neuroforge_config.collector_group"),
          " (10 collector x 7 = 70 기대)")
    for r in await c.fetch("""SELECT collection_group, count(*) n FROM neuroforge_config.tag
        GROUP BY collection_group ORDER BY n DESC"""):
        print(f"    {r['collection_group']:20} {r['n']}")

    print("="*70)
    print("4) 샘플 — 각 PLC 생산수량 태그(D200 근처) 확인")
    for r in await c.fetch("""SELECT device_id, tag_id, tag_name, memory, address, data_type, collection_group,
        scale, decimals, description FROM neuroforge_config.tag
        WHERE description ILIKE '%production_count%' OR description ILIKE '%생산수량%'
        ORDER BY device_id, tag_id LIMIT 12"""):
        print(f"  plc{r['device_id']:2} tag{r['tag_id']:>4} {r['tag_name']:6} {r['memory']}{r['address']} "
              f"{r['data_type']:7} grp={r['collection_group']:9} sc={r['scale']} dec={r['decimals']} :: {r['description']}")

    print("="*70)
    print("5) PUBLISHER")
    print("  settings:", dict(await c.fetchrow("SELECT id, schema_name FROM neuroforge_config.publisher_settings LIMIT 1")))
    for r in await c.fetch("SELECT name, mode, device_type, history_enabled FROM neuroforge_config.publisher_group ORDER BY name"):
        print(f"    {r['name']:20} mode={r['mode']:11} dev={r['device_type']} history={r['history_enabled']}")

    print("="*70)
    print("6) VIEW 동작 (vw_*)")
    for v in ["vw_collector","vw_device","vw_tag","vw_publisher_group"]:
        try:
            n = await c.fetchval(f"SELECT count(*) FROM neuroforge_config.{v}")
            print(f"  {v:22} rows={n}")
        except Exception as e:
            print(f"  {v:22} ERR {e}")
    await c.close()

asyncio.run(main())
