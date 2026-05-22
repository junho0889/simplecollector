"""
JEM 테스트 — plc_data_integrated 검증 스크립트
================================================

DB의 plc_data_integrated 테이블에서 최신 데이터 기준 하루 전 데이터를 조회하여
10개 PLC CSV 태그 정의와 비교합니다.

검증 항목:
  1. 10개 PLC 모두 데이터 존재 여부
  2. CSV에 정의된 plc_data 태그가 모두 DB에 존재하는지
  3. 각 태그의 값 타입(v_int, v_bigint, v_float, v_text)이 data_type에 맞는지
  4. 값이 NULL이 아닌 유효한 데이터인지
  5. test_data.csv 기대값과 DB 값 비교 (load_year_data가 같은 프레임을 반복)

Usage:
    python verify_integrated.py [--host localhost] [--port 5432] [--schema jem_test]
"""

import asyncio
import csv
import argparse
import os
import sys
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

# 스크립트 경로 기준으로 config/ 폴더 찾기
SCRIPT_DIR = Path(__file__).parent
CONFIG_DIR = SCRIPT_DIR / "config"
TEST_DATA_CSV = SCRIPT_DIR / "test_data.csv"

# plc_id → collector YAML → tags CSV 매핑 (collector_plcN.yaml에서 파싱하지 않고 직접 정의)
PLC_CSV_MAP = {
    1: "tags_04_PLC-A.csv",
    2: "tags_02_PLC-B.csv",
    3: "tags_05_PLC-C.csv",
    4: "tags_10_PLC-D.csv",
    5: "tags_01_PLC-EFG.csv",
    6: "tags_06_PLC-H.csv",
    7: "tags_07_PLC-I.csv",
    8: "tags_03_PLC-J.csv",
    9: "tags_08_PLC-K.csv",
    10: "tags_09_PLC-L.csv",
}

PLC_NAMES = {
    1: "PLC-A (PS 접점 조립)",
    2: "PLC-B (YOKE 조립)",
    3: "PLC-C (TER 조립)",
    4: "PLC-D (중간 검사)",
    5: "PLC-EFG (AIR 청소)",
    6: "PLC-H (1차 SEAL 도포)",
    7: "PLC-I (예비납땜)",
    8: "PLC-J (최종검사)",
    9: "PLC-K (LASER 날인)",
    10: "PLC-L (동작검사)",
}

# data_type → DB에서 값이 저장되는 컬럼
DATATYPE_COLUMN_MAP = {
    "uint16": "v_int",
    "int16": "v_int",
    "uint32": "v_bigint",
    "int32": "v_bigint",
    "float32": "v_float",
    "float64": "v_float",
    "string": "v_text",
}


def load_csv_plc_data_tags(csv_path: Path) -> list[dict]:
    """CSV에서 collection_group=plc_data 인 태그만 추출"""
    tags = []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("collection_group", "").strip() == "plc_data":
                tags.append({
                    "tag_id": int(row["tag_id"]),
                    "tag_name": row["tag_name"].strip(),
                    "data_type": row["data_type"].strip(),
                    "scale": float(row.get("scale", 1) or 1),
                    "offset": float(row.get("offset", 0) or 0),
                    "decimals": int(row.get("decimals", 0) or 0),
                })
    return tags


def load_test_data_expected() -> dict[tuple[int, int], list[dict]]:
    """test_data.csv에서 (plc_id, tag_id) → 모든 기대값 리스트 반환

    load_year_data.py가 프레임을 순환하면서 데이터를 넣으므로
    DB의 최신 값이 CSV의 어떤 프레임 값이든 일치하면 PASS.
    """
    expected: dict[tuple[int, int], list[dict]] = defaultdict(list)
    if not TEST_DATA_CSV.exists():
        return expected

    with open(TEST_DATA_CSV, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if len(row) < 9:
                continue
            plc_id = int(row[1])
            tag_id = int(row[2])
            expected[(plc_id, tag_id)].append({
                "v_bool": row[3] if row[3] else None,
                "v_int": int(row[4]) if row[4] else None,
                "v_bigint": int(row[5]) if row[5] else None,
                "v_float": float(row[6]) if row[6] else None,
                "v_text": row[7] if row[7] else None,
            })
    return expected


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.details: list[str] = []

    def ok(self, msg: str):
        self.passed += 1
        self.details.append(f"  [PASS] {msg}")

    def fail(self, msg: str):
        self.failed += 1
        self.details.append(f"  [FAIL] {msg}")

    def warn(self, msg: str):
        self.warnings += 1
        self.details.append(f"  [WARN] {msg}")

    def info(self, msg: str):
        self.details.append(f"  [INFO] {msg}")

    def print_all(self):
        for d in self.details:
            print(d)

    def summary(self) -> str:
        total = self.passed + self.failed
        return f"PASS: {self.passed}/{total}, FAIL: {self.failed}, WARN: {self.warnings}"


async def main():
    parser = argparse.ArgumentParser(description="JEM plc_data_integrated 검증")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="neurosense")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="neuro0901")
    parser.add_argument("--schema", default="jem_test")
    args = parser.parse_args()

    try:
        import asyncpg
    except ImportError:
        print("ERROR: asyncpg 필요. pip install asyncpg")
        sys.exit(1)

    dsn = f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.database}"
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"DB 연결 실패: {e}")
        sys.exit(1)

    schema = args.schema
    result = TestResult()

    print("=" * 70)
    print("  JEM 테스트 - plc_data_integrated 검증")
    print(f"  DB: {args.host}:{args.port}/{args.database} schema={schema}")
    print("=" * 70)

    # ──────────────────────────────────────────────────────────────────
    # 0. 기본 정보
    # ──────────────────────────────────────────────────────────────────
    print("\n[0] 기본 정보")

    table_exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pg_tables "
        "WHERE schemaname = $1 AND tablename = 'plc_data_integrated')",
        schema,
    )
    if not table_exists:
        print("  ERROR: plc_data_integrated 테이블 없음")
        await conn.close()
        sys.exit(1)

    total_rows = await conn.fetchval(
        f"SELECT COUNT(*) FROM {schema}.plc_data_integrated"
    )
    ts_range = await conn.fetchrow(
        f"SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts "
        f"FROM {schema}.plc_data_integrated"
    )
    max_ts = ts_range["max_ts"]
    start_ts = max_ts - timedelta(days=1)

    print(f"  전체 행 수: {total_rows:,}")
    print(f"  시간 범위: {ts_range['min_ts']} ~ {max_ts}")
    print(f"  검증 대상: {start_ts} ~ {max_ts} (최신 기준 1일)")

    # 1일간 데이터 건수
    day_count = await conn.fetchval(
        f"SELECT COUNT(*) FROM {schema}.plc_data_integrated "
        f"WHERE timestamp >= $1",
        start_ts,
    )
    print(f"  1일간 행 수: {day_count:,}")

    # ──────────────────────────────────────────────────────────────────
    # 1. 10개 PLC 모두 데이터 존재 확인
    # ──────────────────────────────────────────────────────────────────
    print("\n[1] PLC 존재 확인 (plc_id 1~10)")

    plc_counts = await conn.fetch(
        f"SELECT plc_id, COUNT(*) as cnt "
        f"FROM {schema}.plc_data_integrated "
        f"WHERE timestamp >= $1 "
        f"GROUP BY plc_id ORDER BY plc_id",
        start_ts,
    )
    plc_count_map = {r["plc_id"]: r["cnt"] for r in plc_counts}

    for plc_id in range(1, 11):
        cnt = plc_count_map.get(plc_id, 0)
        name = PLC_NAMES.get(plc_id, "?")
        if cnt > 0:
            result.ok(f"PLC {plc_id:>2} ({name}): {cnt:,} rows")
        else:
            result.fail(f"PLC {plc_id:>2} ({name}): 데이터 없음!")

    result.print_all()
    result.details.clear()

    # ──────────────────────────────────────────────────────────────────
    # 2. CSV 태그 정의 vs DB 존재 확인
    # ──────────────────────────────────────────────────────────────────
    print(f"\n[2] CSV 태그 정의 vs DB 태그 존재 확인")

    # DB에서 1일간의 distinct (plc_id, tag_id)
    db_tags = await conn.fetch(
        f"SELECT DISTINCT plc_id, tag_id "
        f"FROM {schema}.plc_data_integrated "
        f"WHERE timestamp >= $1",
        start_ts,
    )
    db_tag_set = {(r["plc_id"], r["tag_id"]) for r in db_tags}

    # CSV에서 기대하는 태그
    csv_all_tags: dict[int, list[dict]] = {}
    for plc_id, csv_file in PLC_CSV_MAP.items():
        csv_path = CONFIG_DIR / csv_file
        if not csv_path.exists():
            result.fail(f"PLC {plc_id}: CSV 파일 없음 ({csv_file})")
            continue
        tags = load_csv_plc_data_tags(csv_path)
        csv_all_tags[plc_id] = tags

    total_csv_tags = 0
    total_found = 0
    total_missing = 0

    for plc_id in sorted(csv_all_tags):
        tags = csv_all_tags[plc_id]
        total_csv_tags += len(tags)
        missing = []
        found = []
        for t in tags:
            if (plc_id, t["tag_id"]) in db_tag_set:
                found.append(t["tag_id"])
            else:
                missing.append(t["tag_id"])

        total_found += len(found)
        total_missing += len(missing)

        if missing:
            result.fail(
                f"PLC {plc_id:>2}: {len(found)}/{len(tags)} tags — "
                f"MISSING: {missing}"
            )
        else:
            result.ok(f"PLC {plc_id:>2}: {len(found)}/{len(tags)} tags 모두 존재")

    result.print_all()
    print(f"  ────────────────────────────")
    print(f"  CSV 정의 총 태그: {total_csv_tags}, DB 존재: {total_found}, 누락: {total_missing}")
    result.details.clear()

    # ──────────────────────────────────────────────────────────────────
    # 3. 값 타입 검증 + 값 유효성 + test_data.csv 기대값 비교
    # ──────────────────────────────────────────────────────────────────
    print(f"\n[3] 값 타입/유효성/기대값 검증 (최신 1일 데이터 기준)")

    # DB에서 최신 값 per (plc_id, tag_id) - 1일 이내
    db_latest = await conn.fetch(
        f"SELECT DISTINCT ON (plc_id, tag_id) "
        f"plc_id, tag_id, v_bool, v_int, v_bigint, v_float, v_text, timestamp "
        f"FROM {schema}.plc_data_integrated "
        f"WHERE timestamp >= $1 "
        f"ORDER BY plc_id, tag_id, timestamp DESC",
        start_ts,
    )
    db_latest_map = {
        (r["plc_id"], r["tag_id"]): r for r in db_latest
    }

    # test_data.csv 기대값
    expected = load_test_data_expected()

    for plc_id in sorted(csv_all_tags):
        tags = csv_all_tags[plc_id]
        name = PLC_NAMES.get(plc_id, "?")
        print(f"\n  ── PLC {plc_id} ({name}) ──")

        for t in tags:
            tag_id = t["tag_id"]
            data_type = t["data_type"]
            tag_name = t["tag_name"]
            expected_col = DATATYPE_COLUMN_MAP.get(data_type)

            key = (plc_id, tag_id)
            db_row = db_latest_map.get(key)

            if db_row is None:
                result.fail(f"tag={tag_id} ({tag_name}): DB에 값 없음")
                continue

            # 타입 검증: 해당 컬럼에 값이 있어야 함
            if expected_col and db_row[expected_col] is not None:
                db_val = db_row[expected_col]
                type_ok = True
            elif data_type == "string" and db_row["v_text"] is not None:
                db_val = db_row["v_text"]
                type_ok = True
            else:
                # 다른 컬럼에라도 값이 있는지 확인
                db_val = None
                type_ok = False
                for col in ("v_int", "v_bigint", "v_float", "v_text"):
                    if db_row[col] is not None:
                        db_val = db_row[col]
                        break

                if db_val is not None:
                    result.warn(
                        f"tag={tag_id} ({tag_name}): type={data_type} → "
                        f"기대 컬럼={expected_col} 비어있지만 다른 컬럼에 값={db_val}"
                    )
                    type_ok = True  # 값 자체는 있으므로 비교 진행
                else:
                    result.fail(
                        f"tag={tag_id} ({tag_name}): 모든 값 컬럼 NULL"
                    )
                    continue

            # test_data.csv 기대값 비교
            # load_year_data.py가 프레임을 순환하므로 DB 최신값은
            # CSV의 어떤 프레임 값이든 일치하면 PASS
            exp_list = expected.get(key)
            if not exp_list:
                result.ok(
                    f"tag={tag_id} ({tag_name}): type={data_type} "
                    f"val={db_val} (test_data.csv에 없어 값 존재만 확인)"
                )
                continue

            # 모든 프레임에서 기대값 추출 (중복 제거)
            exp_vals = set()
            for exp in exp_list:
                exp_val = None
                if expected_col == "v_int" and exp["v_int"] is not None:
                    exp_val = exp["v_int"]
                elif expected_col == "v_bigint" and exp["v_bigint"] is not None:
                    exp_val = exp["v_bigint"]
                elif expected_col == "v_float" and exp["v_float"] is not None:
                    exp_val = exp["v_float"]
                elif expected_col == "v_text" and exp["v_text"] is not None:
                    exp_val = exp["v_text"]
                else:
                    for k in ("v_int", "v_bigint", "v_float", "v_text"):
                        if exp[k] is not None:
                            exp_val = exp[k]
                            break
                if exp_val is not None:
                    exp_vals.add(exp_val)

            if not exp_vals:
                result.ok(
                    f"tag={tag_id} ({tag_name}): type={data_type} "
                    f"val={db_val} (기대값 없음, 존재만 확인)"
                )
                continue

            # DB 값이 어떤 기대값과라도 일치하는지 확인
            match = False
            for exp_val in exp_vals:
                if isinstance(exp_val, str):
                    if str(db_val).strip() == exp_val.strip():
                        match = True
                        break
                elif isinstance(exp_val, float) or isinstance(db_val, float):
                    try:
                        if abs(float(db_val) - float(exp_val)) < 0.1:
                            match = True
                            break
                    except (ValueError, TypeError):
                        pass
                else:
                    try:
                        if int(db_val) == int(exp_val):
                            match = True
                            break
                    except (ValueError, TypeError):
                        pass

            exp_vals_str = sorted(str(v) for v in exp_vals)
            if match:
                result.ok(
                    f"tag={tag_id} ({tag_name}): type={data_type} "
                    f"DB={db_val} in CSV{exp_vals_str}"
                )
            else:
                result.fail(
                    f"tag={tag_id} ({tag_name}): type={data_type} "
                    f"DB={db_val} not in CSV{exp_vals_str}"
                )

        result.print_all()
        result.details.clear()

    # ──────────────────────────────────────────────────────────────────
    # 4. PLC별 데이터 연속성 확인 (1일 내 gap 체크)
    # ──────────────────────────────────────────────────────────────────
    print(f"\n[4] 데이터 연속성 확인 (1일 내 10분 이상 gap)")

    for plc_id in range(1, 11):
        gap_rows = await conn.fetch(
            f"SELECT ts, prev_ts, ts - prev_ts as gap "
            f"FROM ("
            f"  SELECT timestamp as ts, "
            f"    LAG(timestamp) OVER (ORDER BY timestamp) as prev_ts "
            f"  FROM {schema}.plc_data_integrated "
            f"  WHERE plc_id = $1 AND timestamp >= $2 "
            f"  AND tag_id = ("
            f"    SELECT MIN(tag_id) FROM {schema}.plc_data_integrated "
            f"    WHERE plc_id = $1 AND timestamp >= $2"
            f"  )"
            f") sub "
            f"WHERE ts - prev_ts > INTERVAL '10 minutes' "
            f"ORDER BY ts LIMIT 5",
            plc_id, start_ts,
        )
        if gap_rows:
            for g in gap_rows:
                result.warn(
                    f"PLC {plc_id:>2}: gap {g['gap']} at {g['ts']}"
                )
        else:
            result.ok(f"PLC {plc_id:>2}: 10분 이상 gap 없음")

    result.print_all()
    result.details.clear()

    # ──────────────────────────────────────────────────────────────────
    # 5. DB 크기 정보
    # ──────────────────────────────────────────────────────────────────
    print(f"\n[5] DB 크기 정보")

    db_size = await conn.fetchval(
        "SELECT pg_size_pretty(pg_database_size($1))", args.database
    )
    schema_size = await conn.fetchval(
        f"SELECT pg_size_pretty("
        f"  SUM(pg_total_relation_size(schemaname || '.' || tablename))::bigint"
        f") FROM pg_tables WHERE schemaname = $1",
        schema,
    )
    print(f"  DB 전체: {db_size}")
    print(f"  {schema} 스키마: {schema_size}")

    try:
        chunks = await conn.fetch(
            "SELECT hypertable_name, "
            "COUNT(*) FILTER (WHERE is_compressed) as compressed, "
            "COUNT(*) FILTER (WHERE NOT is_compressed) as uncompressed "
            "FROM timescaledb_information.chunks "
            "WHERE hypertable_schema = $1 "
            "GROUP BY hypertable_name",
            schema,
        )
        if chunks:
            for c in chunks:
                print(
                    f"  {c['hypertable_name']}: "
                    f"compressed={c['compressed']}, uncompressed={c['uncompressed']}"
                )
    except Exception:
        pass

    # ──────────────────────────────────────────────────────────────────
    # 최종 결과
    # ──────────────────────────────────────────────────────────────────
    await conn.close()

    print("\n" + "=" * 70)
    total = result.passed + result.failed
    if result.failed == 0:
        status = "ALL PASSED"
    else:
        status = "FAILED"
    print(f"  결과: {status} — {result.summary()}")
    print("=" * 70)

    sys.exit(1 if result.failed > 0 else 0)


if __name__ == "__main__":
    asyncio.run(main())
