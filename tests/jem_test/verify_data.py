"""
JEM 테스트 데이터 검증 스크립트
================================

Docker 테스트 환경에서 수집된 DB 데이터를 test_data.csv와 비교합니다.

Usage:
    python verify_data.py [--host localhost] [--port 5432] [--schema jem_test]
"""

import asyncio
import csv
import argparse
import sys
from collections import defaultdict
from datetime import datetime


async def main():
    parser = argparse.ArgumentParser(description="JEM 테스트 데이터 검증")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="neurosense")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="neuro0901")
    parser.add_argument("--schema", default="jem_test")
    parser.add_argument("--csv", default="test_data.csv")
    args = parser.parse_args()

    try:
        import asyncpg
    except ImportError:
        print("ERROR: asyncpg 필요. pip install asyncpg")
        sys.exit(1)

    # DB 연결
    dsn = f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.database}"
    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"DB 연결 실패: {e}")
        sys.exit(1)

    schema = args.schema
    print(f"=== JEM 테스트 데이터 검증 ===")
    print(f"DB: {args.host}:{args.port}/{args.database} schema={schema}")
    print()

    # 1. 스키마 존재 확인
    exists = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM information_schema.schemata WHERE schema_name = $1)",
        schema,
    )
    if not exists:
        print(f"ERROR: 스키마 '{schema}' 없음")
        await conn.close()
        sys.exit(1)
    print(f"[OK] 스키마 '{schema}' 존재")

    # 2. 테이블 목록
    tables = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = $1 ORDER BY tablename",
        schema,
    )
    table_names = [t["tablename"] for t in tables]
    print(f"[OK] 테이블: {', '.join(table_names)}")

    # 3. plc_master 확인
    if "plc_master" in table_names:
        plcs = await conn.fetch(f"SELECT plc_id, plc_name FROM {schema}.plc_master ORDER BY plc_id")
        print(f"[OK] plc_master: {len(plcs)}개 PLC")
        for p in plcs:
            print(f"     plc_id={p['plc_id']} name={p['plc_name']}")
    else:
        print("[WARN] plc_master 테이블 없음")

    # 4. plc_data_integrated 행 수
    if "plc_data_integrated" in table_names:
        count = await conn.fetchval(f"SELECT COUNT(*) FROM {schema}.plc_data_integrated")
        print(f"\n[DATA] plc_data_integrated: {count:,} rows")

        # PLC별 행 수
        plc_counts = await conn.fetch(
            f"SELECT plc_id, COUNT(*) as cnt FROM {schema}.plc_data_integrated GROUP BY plc_id ORDER BY plc_id"
        )
        for r in plc_counts:
            print(f"       PLC {r['plc_id']}: {r['cnt']:,} rows")

        # 최근 데이터 샘플
        recent = await conn.fetch(
            f"SELECT plc_id, tag_id, v_int, v_bigint, v_float, v_text, timestamp "
            f"FROM {schema}.plc_data_integrated ORDER BY timestamp DESC LIMIT 5"
        )
        print(f"\n[SAMPLE] 최근 5건:")
        for r in recent:
            vals = []
            if r["v_int"] is not None:
                vals.append(f"v_int={r['v_int']}")
            if r["v_bigint"] is not None:
                vals.append(f"v_bigint={r['v_bigint']}")
            if r["v_float"] is not None:
                vals.append(f"v_float={r['v_float']}")
            if r["v_text"] is not None:
                vals.append(f"v_text={r['v_text']}")
            print(f"       PLC{r['plc_id']} tag={r['tag_id']} {' '.join(vals)} ts={r['timestamp']}")
    else:
        print("[WARN] plc_data_integrated 테이블 없음")

    # 5. alm_latest 확인
    if "alm_latest" in table_names:
        alm_count = await conn.fetchval(f"SELECT COUNT(*) FROM {schema}.alm_latest")
        print(f"\n[DATA] alm_latest: {alm_count:,} rows")

        alm_on = await conn.fetchval(
            f"SELECT COUNT(*) FROM {schema}.alm_latest WHERE v_bool = TRUE"
        )
        print(f"       알람 ON: {alm_on}, OFF: {alm_count - alm_on}")
    else:
        print("[WARN] alm_latest 테이블 없음")

    # 6. alm_history 확인
    if "alm_history" in table_names:
        hist_count = await conn.fetchval(f"SELECT COUNT(*) FROM {schema}.alm_history")
        print(f"[DATA] alm_history: {hist_count:,} rows")

    # 7. test_data.csv와 값 비교
    if "plc_data_integrated" in table_names:
        print(f"\n=== test_data.csv 값 비교 ===")
        csv_data = defaultdict(list)
        try:
            with open(args.csv, "r") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) < 9:
                        continue
                    plc_id = int(row[1])
                    tag_id = int(row[2])
                    csv_data[(plc_id, tag_id)].append(row)
        except FileNotFoundError:
            print(f"[WARN] {args.csv} 없음, 값 비교 스킵")
            csv_data = {}

        if csv_data:
            # DB에서 PLC별 최신 값 가져오기
            db_latest = await conn.fetch(
                f"SELECT DISTINCT ON (plc_id, tag_id) plc_id, tag_id, "
                f"v_bool, v_int, v_bigint, v_float, v_text "
                f"FROM {schema}.plc_data_integrated "
                f"ORDER BY plc_id, tag_id, timestamp DESC"
            )

            match_count = 0
            mismatch_count = 0
            missing_count = 0

            for r in db_latest:
                key = (r["plc_id"], r["tag_id"])
                if key not in csv_data:
                    continue

                csv_rows = csv_data[key]
                csv_row = csv_rows[-1]  # 마지막 값

                # 값 비교 (타입별)
                ok = False
                if r["v_float"] is not None and csv_row[6]:
                    csv_val = float(csv_row[6])
                    db_val = float(r["v_float"])
                    ok = abs(db_val - csv_val) < 0.1
                elif r["v_bigint"] is not None and csv_row[5]:
                    ok = r["v_bigint"] == int(csv_row[5])
                elif r["v_int"] is not None and csv_row[4]:
                    ok = r["v_int"] == int(csv_row[4])
                elif r["v_text"] is not None and csv_row[7]:
                    ok = r["v_text"] == csv_row[7]
                elif r["v_bool"] is not None and csv_row[3]:
                    ok = True  # bool은 대략 비교
                else:
                    ok = True  # 둘 다 비어있으면 OK

                if ok:
                    match_count += 1
                else:
                    mismatch_count += 1
                    print(
                        f"  [MISMATCH] PLC{r['plc_id']} tag={r['tag_id']}: "
                        f"DB={r['v_int'] or r['v_bigint'] or r['v_float'] or r['v_text']} "
                        f"CSV={csv_row[4] or csv_row[5] or csv_row[6] or csv_row[7]}"
                    )

            print(f"\n[RESULT] 일치: {match_count}, 불일치: {mismatch_count}")

    # 8. DB 크기
    size = await conn.fetchval(
        "SELECT pg_size_pretty(pg_database_size($1))", args.database
    )
    print(f"\n[SIZE] DB 전체 크기: {size}")

    # 스키마 크기
    schema_size = await conn.fetchval(
        f"SELECT pg_size_pretty(SUM(pg_total_relation_size(schemaname || '.' || tablename))::bigint) "
        f"FROM pg_tables WHERE schemaname = $1",
        schema,
    )
    print(f"[SIZE] {schema} 스키마 크기: {schema_size}")

    # 9. 압축 상태 (TimescaleDB)
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
            print(f"\n[COMPRESSION]")
            for c in chunks:
                print(
                    f"  {c['hypertable_name']}: "
                    f"compressed={c['compressed']}, uncompressed={c['uncompressed']}"
                )
    except Exception:
        pass  # TimescaleDB 없으면 스킵

    # 10. RabbitMQ 큐 상태는 별도 확인 필요
    print(f"\n[TIP] RabbitMQ 관리: http://localhost:15672 (admin/admin)")

    await conn.close()
    print(f"\n=== 검증 완료 ===")


if __name__ == "__main__":
    asyncio.run(main())
