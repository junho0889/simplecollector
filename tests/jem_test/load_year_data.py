"""
JEM 1년치 데이터 적재 스크립트
================================

test_data.csv의 패턴을 기반으로 1년치(365일) 시계열 데이터를 TimescaleDB에 직접 적재합니다.
파이프라인(collector→rabbit→publisher) 경유 없이 asyncpg COPY로 고속 삽입.

Usage:
    pip install asyncpg
    python load_year_data.py [--host localhost] [--port 5432] [--days 365] [--max-gb 400]

미션 2 목표:
  - 1년치 데이터 적재 (365일 × 86,400초 × ~96행/초)
  - 하루 적재 완료마다 TimescaleDB 압축 실행
  - 최종 DB 크기 / 압축률 / 조회 성능 리포트
"""

import asyncio
import csv
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import List, Tuple, Optional, Dict, Any


KST = timezone(timedelta(hours=9))

# plc_data_integrated 컬럼 순서
COLUMNS = [
    "timestamp", "plc_id", "tag_id",
    "v_bool", "v_int", "v_bigint", "v_float", "v_text", "quality_code",
]

BATCH_SIZE = 50_000  # COPY 배치 크기


def parse_csv_row(row: list) -> Optional[dict]:
    """CSV 행을 dict로 변환 (test_data.csv 형식)"""
    if len(row) < 9:
        return None
    try:
        d = {
            "plc_id": int(row[1]),
            "tag_id": int(row[2]),
            "v_bool": None,
            "v_int": None,
            "v_bigint": None,
            "v_float": None,
            "v_text": None,
            "quality_code": int(row[8]) if row[8] else 1,
        }
        if row[3]:
            d["v_bool"] = row[3].lower() in ("true", "1", "t")
        if row[4]:
            d["v_int"] = int(row[4])
        if row[5]:
            d["v_bigint"] = int(row[5])
        if row[6]:
            d["v_float"] = float(row[6])
        if row[7]:
            d["v_text"] = row[7]
        return d
    except (ValueError, IndexError):
        return None


def load_csv_frames(csv_path: str) -> List[List[dict]]:
    """
    CSV를 '프레임' 단위로 그룹화.
    같은 timestamp의 행들을 하나의 프레임으로 묶음.
    """
    frames_by_ts = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or len(row) < 9:
                continue
            ts_str = row[0].strip()
            d = parse_csv_row(row)
            if d:
                frames_by_ts[ts_str].append(d)

    # 타임스탬프 순서로 프레임 리스트
    sorted_ts = sorted(frames_by_ts.keys())
    return [frames_by_ts[ts] for ts in sorted_ts]


def make_record_tuple(d: dict, ts: datetime) -> tuple:
    """dict + timestamp → COPY용 tuple"""
    return (
        ts,
        d["plc_id"],
        d["tag_id"],
        d["v_bool"],
        d["v_int"],
        d["v_bigint"],
        d["v_float"],
        d["v_text"],
        d["quality_code"],
    )


async def get_db_size_bytes(conn, db_name: str) -> int:
    return await conn.fetchval("SELECT pg_database_size($1)", db_name)


async def compress_old_chunks(conn, schema: str, table: str, cutoff: datetime):
    """cutoff 이전 청크 압축"""
    chunks = await conn.fetch(
        """
        SELECT chunk_schema || '.' || chunk_name as chunk_full
        FROM timescaledb_information.chunks
        WHERE hypertable_schema = $1
          AND hypertable_name = $2
          AND range_end < $3
          AND NOT is_compressed
        """,
        schema, table, cutoff,
    )
    compressed = 0
    for c in chunks:
        try:
            await conn.execute(
                f"SELECT compress_chunk('{c['chunk_full']}')"
            )
            compressed += 1
        except Exception as e:
            print(f"  [WARN] 압축 실패 {c['chunk_full']}: {e}")
    return compressed


async def run_query_benchmark(conn, schema: str) -> Dict[str, float]:
    """조회 성능 벤치마크"""
    results = {}
    table = f"{schema}.plc_data_integrated"

    # 1. COUNT(*)
    t0 = time.time()
    count = await conn.fetchval(f"SELECT COUNT(*) FROM {table}")
    results["count_all"] = (time.time() - t0) * 1000
    results["total_rows"] = count

    # 2. 최근 1시간, 특정 PLC
    t0 = time.time()
    await conn.fetch(
        f"SELECT * FROM {table} WHERE plc_id = 1 AND timestamp > NOW() - INTERVAL '1 hour'"
    )
    results["recent_1h_plc1"] = (time.time() - t0) * 1000

    # 3. 하루치 AVG by tag
    t0 = time.time()
    await conn.fetch(
        f"""SELECT plc_id, tag_id, AVG(v_float), AVG(v_int)
            FROM {table}
            WHERE timestamp >= '2025-06-15' AND timestamp < '2025-06-16'
            GROUP BY plc_id, tag_id"""
    )
    results["daily_avg"] = (time.time() - t0) * 1000

    # 4. 1개월 집계
    t0 = time.time()
    await conn.fetch(
        f"""SELECT plc_id, tag_id,
                   AVG(v_float) as avg_float,
                   MIN(v_int) as min_int,
                   MAX(v_bigint) as max_bigint,
                   COUNT(*) as cnt
            FROM {table}
            WHERE timestamp >= '2025-06-01' AND timestamp < '2025-07-01'
            GROUP BY plc_id, tag_id"""
    )
    results["monthly_agg"] = (time.time() - t0) * 1000

    # 5. 특정 태그 시계열 (7일)
    t0 = time.time()
    await conn.fetch(
        f"""SELECT timestamp, v_float
            FROM {table}
            WHERE plc_id = 1 AND tag_id = 235
              AND timestamp >= '2025-09-01' AND timestamp < '2025-09-08'
            ORDER BY timestamp"""
    )
    results["tag_timeseries_7d"] = (time.time() - t0) * 1000

    return results


async def main():
    parser = argparse.ArgumentParser(description="JEM 1년치 데이터 적재")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="neurosense")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="neuro0901")
    parser.add_argument("--schema", default="jem_test")
    parser.add_argument("--csv", default="test_data.csv")
    parser.add_argument("--days", type=int, default=365, help="적재할 일수 (기본 365)")
    parser.add_argument("--max-gb", type=float, default=400, help="DB 크기 상한 (GB)")
    parser.add_argument("--start-date", default="2025-03-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--skip-compress", action="store_true", help="압축 스킵")
    parser.add_argument("--report-only", action="store_true", help="적재 없이 리포트만")
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
    max_bytes = int(args.max_gb * 1024 * 1024 * 1024)

    if args.report_only:
        await print_report(conn, schema, args.database)
        await conn.close()
        return

    # CSV 프레임 로드
    print(f"=== JEM 1년치 데이터 적재 ===")
    print(f"CSV: {args.csv}")
    frames = load_csv_frames(args.csv)
    rows_per_frame = sum(len(f) for f in frames)
    print(f"프레임: {len(frames)}개, 총 행: {rows_per_frame}행/사이클")
    print(f"적재 기간: {args.start_date} ~ +{args.days}일")
    print(f"예상 총 행: {args.days * 86_400 * rows_per_frame / len(frames):,.0f}")
    print()

    # 시작일
    start = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=KST)

    total_rows = 0
    total_start = time.time()
    frame_idx = 0  # 프레임 순환 인덱스

    for day in range(args.days):
        day_start_time = time.time()
        day_date = start + timedelta(days=day)
        day_rows = 0
        batch: List[tuple] = []

        for second in range(86_400):
            ts = day_date + timedelta(seconds=second)
            frame = frames[frame_idx % len(frames)]
            frame_idx += 1

            for d in frame:
                batch.append(make_record_tuple(d, ts))

            if len(batch) >= BATCH_SIZE:
                await conn.copy_records_to_table(
                    "plc_data_integrated",
                    records=batch,
                    columns=COLUMNS,
                    schema_name=schema,
                )
                day_rows += len(batch)
                batch.clear()

        # 잔여 배치
        if batch:
            await conn.copy_records_to_table(
                "plc_data_integrated",
                records=batch,
                columns=COLUMNS,
                schema_name=schema,
            )
            day_rows += len(batch)
            batch.clear()

        total_rows += day_rows
        day_elapsed = time.time() - day_start_time

        # 하루치 완료 → 압축
        compressed = 0
        if not args.skip_compress:
            cutoff = day_date + timedelta(days=1)  # 오늘까지 압축
            compressed = await compress_old_chunks(
                conn, schema, "plc_data_integrated", cutoff
            )

        # DB 크기 체크
        db_size = await get_db_size_bytes(conn, args.database)
        db_gb = db_size / (1024 ** 3)

        elapsed_total = time.time() - total_start
        eta_sec = (elapsed_total / (day + 1)) * (args.days - day - 1)

        print(
            f"Day {day + 1:3d}/{args.days} ({day_date.strftime('%Y-%m-%d')}) | "
            f"{day_rows:>10,} rows ({day_elapsed:.1f}s) | "
            f"총 {total_rows:>13,} | "
            f"DB {db_gb:.2f}GB | "
            f"압축 {compressed}청크 | "
            f"ETA {eta_sec / 60:.0f}분"
        )

        # 크기 초과 체크
        if db_size > max_bytes:
            print(f"\n[STOP] DB 크기 {db_gb:.1f}GB > {args.max_gb}GB 상한 도달. 중단.")
            break

    total_elapsed = time.time() - total_start
    print(f"\n적재 완료: {total_rows:,} rows in {total_elapsed / 60:.1f}분")

    # 최종 리포트
    await print_report(conn, schema, args.database)
    await conn.close()


async def print_report(conn, schema: str, database: str):
    """최종 리포트 출력"""
    print(f"\n{'=' * 60}")
    print(f"=== 1년치 데이터 리포트 ===")
    print(f"{'=' * 60}")

    # 총 행 수
    count = await conn.fetchval(
        f"SELECT COUNT(*) FROM {schema}.plc_data_integrated"
    )
    print(f"\n[DATA] plc_data_integrated: {count:,} rows")

    # PLC별 행 수
    plc_counts = await conn.fetch(
        f"SELECT plc_id, COUNT(*) as cnt FROM {schema}.plc_data_integrated GROUP BY plc_id ORDER BY plc_id"
    )
    for r in plc_counts:
        print(f"  PLC {r['plc_id']:2d}: {r['cnt']:>13,} rows")

    # 시간 범위
    ts_range = await conn.fetchrow(
        f"SELECT MIN(timestamp) as first_ts, MAX(timestamp) as last_ts FROM {schema}.plc_data_integrated"
    )
    if ts_range["first_ts"]:
        print(f"\n[TIME] {ts_range['first_ts']} ~ {ts_range['last_ts']}")

    # alm
    alm_latest = await conn.fetchval(
        f"SELECT COUNT(*) FROM {schema}.alm_latest"
    )
    alm_hist = await conn.fetchval(
        f"SELECT COUNT(*) FROM {schema}.alm_history"
    )
    print(f"\n[ALM] alm_latest: {alm_latest:,}, alm_history: {alm_hist:,}")

    # DB 크기
    db_size = await conn.fetchval(
        "SELECT pg_size_pretty(pg_database_size($1))", database
    )
    print(f"\n[SIZE] DB 전체: {db_size}")

    # 테이블별 크기
    table_sizes = await conn.fetch(
        f"""SELECT tablename,
                   pg_size_pretty(pg_total_relation_size(schemaname || '.' || tablename)) as total_size,
                   pg_total_relation_size(schemaname || '.' || tablename) as total_bytes
            FROM pg_tables
            WHERE schemaname = $1
            ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC""",
        schema,
    )
    for t in table_sizes:
        print(f"  {t['tablename']:25s}: {t['total_size']}")

    # 압축 상태
    try:
        chunks = await conn.fetch(
            """SELECT hypertable_name,
                      COUNT(*) as total_chunks,
                      COUNT(*) FILTER (WHERE is_compressed) as compressed,
                      COUNT(*) FILTER (WHERE NOT is_compressed) as uncompressed,
                      pg_size_pretty(SUM(
                          CASE WHEN is_compressed THEN compressed_total_bytes
                          ELSE uncompressed_total_bytes END
                      )::bigint) as effective_size
               FROM timescaledb_information.chunks
               WHERE hypertable_schema = $1
               GROUP BY hypertable_name""",
            schema,
        )
        if chunks:
            print(f"\n[COMPRESSION]")
            for c in chunks:
                print(
                    f"  {c['hypertable_name']}: "
                    f"{c['total_chunks']} chunks "
                    f"(compressed={c['compressed']}, uncompressed={c['uncompressed']}) "
                    f"effective={c['effective_size']}"
                )

        # 압축률 계산
        comp_stats = await conn.fetch(
            """SELECT hypertable_name,
                      SUM(uncompressed_total_bytes)::bigint as before_bytes,
                      SUM(compressed_total_bytes)::bigint as after_bytes
               FROM timescaledb_information.chunks
               WHERE hypertable_schema = $1 AND is_compressed
               GROUP BY hypertable_name""",
            schema,
        )
        for s in comp_stats:
            if s["after_bytes"] and s["after_bytes"] > 0:
                ratio = s["before_bytes"] / s["after_bytes"]
                print(
                    f"  {s['hypertable_name']} 압축률: "
                    f"{s['before_bytes'] / (1024**3):.2f}GB → "
                    f"{s['after_bytes'] / (1024**3):.2f}GB "
                    f"({ratio:.1f}:1)"
                )
    except Exception as e:
        print(f"  [WARN] 압축 통계 조회 실패: {e}")

    # 조회 성능 벤치마크
    print(f"\n[BENCHMARK] 조회 성능:")
    try:
        bench = await run_query_benchmark(conn, schema)
        print(f"  SELECT COUNT(*) [{bench['total_rows']:,} rows]: {bench['count_all']:.0f}ms")
        print(f"  최근 1시간 PLC1: {bench['recent_1h_plc1']:.0f}ms")
        print(f"  하루 집계 (AVG by tag): {bench['daily_avg']:.0f}ms")
        print(f"  1개월 집계: {bench['monthly_agg']:.0f}ms")
        print(f"  태그 시계열 7일: {bench['tag_timeseries_7d']:.0f}ms")
    except Exception as e:
        print(f"  [WARN] 벤치마크 실패: {e}")

    print(f"\n{'=' * 60}")


if __name__ == "__main__":
    asyncio.run(main())
