"""
jem_ble collector YAML + tags CSV + publisher YAML -> neuroforge_config (neurosense2).
collectorhub StackImportService 의 컬럼 매핑을 그대로 따른다 (컨테이너 docker cp 대신 로컬 파일).
collector_key = docker-compose 의 container_name (Cortex 재import 시 중복 방지).
"""
import asyncio, yaml, csv, io
from pathlib import Path
import asyncpg

JEM = Path(r"d:\4.source\simpleCollector\deploy\jem_ble")
DSN = dict(host="192.168.0.252", port=5432, user="neuro",
           password=__import__("os").environ.get("PGPW", ""), database="neurosense2", timeout=20)

def _opt_int(v):
    if v is None: return None
    try: return int(v)
    except Exception: return None

def compose_collectors():
    """docker-compose.yml -> [(container_name, collector_yaml_file)] (collector 서비스만)."""
    doc = yaml.safe_load(open(JEM / "docker-compose.yml", encoding="utf-8"))
    out = []
    for svc, spec in (doc.get("services") or {}).items():
        if "collector" not in (spec.get("image") or ""):
            continue
        cname = spec.get("container_name", svc)
        coll_yaml = None
        for v in spec.get("volumes", []) or []:
            if not isinstance(v, str): continue
            parts = v.split(":")
            if len(parts) >= 2 and parts[1].endswith("/collector.yaml"):
                coll_yaml = parts[0].lstrip("./")
        if coll_yaml and (JEM / coll_yaml).is_file():
            out.append((cname, coll_yaml))
    return out

async def import_collector(conn, container_name, yaml_file):
    doc = yaml.safe_load(open(JEM / yaml_file, encoding="utf-8")) or {}
    coll = doc.get("collector") or {}
    proto = coll.get("protocol") or {}
    extra = proto.get("extra") or {}
    name = coll.get("name") or container_name
    plc_id = coll.get("plc_id")
    ptype = proto.get("type", "")
    device_type = "ble" if "ble" in ptype.lower() else "plc"

    existing = await conn.fetchrow(
        "SELECT collector_id FROM neuroforge_config.collector WHERE collector_key=$1", container_name)
    if existing:
        cid = existing["collector_id"]
    else:
        cid = await conn.fetchval(
            """INSERT INTO neuroforge_config.collector
               (collector_key,name,description,site,area,line,enabled,device_type)
               VALUES ($1,$2,$3,$4,$5,$6,true,$7) RETURNING collector_id""",
            container_name, name, coll.get("description",""), coll.get("site",""),
            coll.get("area",""), coll.get("line",""), device_type)
        await conn.execute(
            """INSERT INTO neuroforge_config.collector_protocol
               (collector_id,protocol_type,host,port,unit_id,timeout_ms,plc_series,frame_type,
                network_no,pc_no,unit_io,unit_station,max_address_gap)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT (collector_id) DO NOTHING""",
            cid, ptype, proto.get("host",""), _opt_int(proto.get("port")),
            _opt_int(proto.get("unit_id")) or 1, _opt_int(proto.get("timeout_ms")) or 5000,
            extra.get("plc_series"), extra.get("frame_type"), _opt_int(extra.get("network_no")),
            _opt_int(extra.get("pc_no")), _opt_int(extra.get("unit_io")),
            _opt_int(extra.get("unit_station")), _opt_int(extra.get("max_address_gap")))
        for g in coll.get("collection_groups", []):
            m = (g.get("mode") or "polling").lower()
            m = m if m in ("polling","on_change") else "polling"
            await conn.execute(
                """INSERT INTO neuroforge_config.collector_group
                   (collector_id,name,interval_ms,timeout_ms,retry_count,retry_delay_ms,mode,deadband)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT (collector_id,name) DO NOTHING""",
                cid, g.get("name",""), int(g.get("interval_ms",1000)), int(g.get("timeout_ms",5000)),
                int(g.get("retry_count",0)), int(g.get("retry_delay_ms",0)), m, float(g.get("deadband",0)))

    if device_type == "plc" and plc_id is not None:
        await conn.execute(
            """INSERT INTO neuroforge_config.device
               (device_type,device_id,collector_id,name,description,site,area,line)
               VALUES ('plc',$1,$2,$3,$4,$5,$6,$7) ON CONFLICT (device_type,device_id) DO NOTHING""",
            int(plc_id), cid, name, coll.get("description",""),
            coll.get("site",""), coll.get("area",""), coll.get("line",""))

    # tags
    inserted = 0
    tags_file = coll.get("tags_file","")
    if tags_file and plc_id is not None:
        csvpath = JEM / Path(tags_file).name
        if csvpath.is_file():
            inserted = await import_tags(conn, open(csvpath, encoding="utf-8-sig").read(), "plc", int(plc_id))
        else:
            print(f"   ! tags CSV not found: {csvpath.name}")
    return container_name, plc_id, inserted

async def import_tags(conn, csv_text, device_type, device_id):
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    ins = 0
    for row in rows:
        try:
            tid = int(row.get("tag_id") or 0)
            if tid <= 0: continue
            r = await conn.execute(
                """INSERT INTO neuroforge_config.tag
                   (device_type,device_id,tag_id,tag_name,collection_group,data_type,memory,address,
                    word_length,format,scale,offset_value,decimals,unit,description)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15) ON CONFLICT DO NOTHING""",
                device_type, device_id, tid, (row.get("tag_name") or "")[:100],
                (row.get("collection_group") or "default")[:50], (row.get("data_type") or "")[:30],
                (row.get("memory") or None), int(row["address"]) if row.get("address") else None,
                int(row["word_length"]) if row.get("word_length") else None, (row.get("format") or None),
                float(row["scale"]) if row.get("scale") else 1.0,
                float(row["offset"]) if row.get("offset") else 0.0,
                int(row["decimals"]) if row.get("decimals") else None,
                (row.get("unit") or None), row.get("description") or "")
            if r.endswith(" 1"): ins += 1
        except Exception as e:
            print(f"   ! tag {row.get('tag_id')}: {e}")
    return ins

async def import_publisher(conn):
    doc = yaml.safe_load(open(JEM / "publisher_db1.yaml", encoding="utf-8")) or {}
    db = doc.get("database") or {}
    schema = db.get("schema_name") or db.get("schema")
    if schema:
        await conn.execute("UPDATE neuroforge_config.publisher_settings SET schema_name=$1 WHERE id=1", schema)
    n = 0
    for g in db.get("collection_groups") or []:
        hist = (g.get("extensions") or {}).get("history") or {}
        await conn.execute(
            """INSERT INTO neuroforge_config.publisher_group
               (name,mode,device_type,retention_period,compression_after,history_enabled,history_trigger_on)
               VALUES ($1,$2,$3,$4,$5,$6,$7)
               ON CONFLICT (name) DO UPDATE SET mode=EXCLUDED.mode,device_type=EXCLUDED.device_type,
                 retention_period=EXCLUDED.retention_period,compression_after=EXCLUDED.compression_after,
                 history_enabled=EXCLUDED.history_enabled,history_trigger_on=EXCLUDED.history_trigger_on""",
            str(g.get("name",""))[:50], str(g.get("mode","all"))[:20], g.get("device_type") or None,
            g.get("retention_period") or None, g.get("compression_after") or None,
            bool(hist), str(hist.get("trigger_on","all"))[:20])
        n += 1
    return schema, n

async def main():
    cols = compose_collectors()
    print(f"compose collectors: {len(cols)} -> {[c for c,_ in cols]}")
    c = await asyncpg.connect(**DSN)
    try:
        total_tags = 0
        for cname, yf in cols:
            cn, pid, ins = await import_collector(c, cname, yf)
            total_tags += ins
            print(f"  [{cn}] plc_id={pid} tags+={ins}")
        sch, ng = await import_publisher(c)
        print(f"  [publisher] schema_name={sch} groups={ng}")
        print(f"\n[total] tags inserted={total_tags}")
        for t in ["collector","collector_protocol","collector_group","device","tag","publisher_group"]:
            print(f"  neuroforge_config.{t:20} = {await c.fetchval(f'SELECT count(*) FROM neuroforge_config.{t}')}")
    finally:
        await c.close()

asyncio.run(main())
