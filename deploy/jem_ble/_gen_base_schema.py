"""
jem_ble publisher 기본 스키마 DDL 생성기 (custom_init.sql 제외).
publisher 의 ConfigLoader + schema_init 을 그대로 사용해, auto_init_schema 가
만드는 것과 동일한 글로벌/그룹 테이블 DDL 만 추출한다. (실행/접속 없음)

usage: python _gen_base_schema.py <publisher_yaml> [schema_override]
"""
import sys

sys.path.insert(0, "/cp")  # collector-publisher 루트 (마운트)

from src.config import ConfigLoader
from src.publishers.schema_init import get_global_init_sql, get_group_init_sql

yaml_path = sys.argv[1]
cfg = ConfigLoader.load(yaml_path).database
schema = sys.argv[2] if len(sys.argv) > 2 else cfg.schema_name

device_types = [d.lower() for d in cfg.device_types]

stmts = []
# 글로벌 ({dev}_master + quality_master, CREATE SCHEMA 포함)
for dev in device_types:
    stmts += get_global_init_sql(schema, device_type=dev)

# 그룹별 (mode/extensions/retention/compression)
for gc in cfg.collection_groups:
    dev_list = [gc.device_type.lower()] if gc.device_type else device_types
    for dev in dev_list:
        stmts += get_group_init_sql(
            schema, gc.name,
            mode=gc.mode,
            compression_after=cfg.resolve_compression_after(gc),
            compression_segmentby=cfg.compression_segmentby,
            compression_orderby=cfg.compression_orderby,
            retention_period=cfg.resolve_retention(gc),
            extensions=gc.extensions,
            device_type=dev,
        )

# 출력 (각 문을 ; 로 종료해 psql -f 로 적용 가능하게)
out = []
out.append("-- ============================================================")
out.append(f"-- jem_ble 기본 스키마 (custom_init.sql 제외) — schema={schema}")
out.append(f"-- groups={[g.name for g in cfg.collection_groups]}")
out.append(f"-- device_types={device_types}")
out.append("-- 생성: publisher schema_init (auto_init_schema 동등)")
out.append("-- ============================================================\n")
for s in stmts:
    s = s.strip()
    if not s:
        continue
    if not s.endswith(";"):
        s += ";"
    out.append(s + "\n")

print("\n".join(out))
sys.stderr.write(f"[gen] statements={len(stmts)} schema={schema}\n")
