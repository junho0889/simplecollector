"""
TimescaleDB 압축 용량 벤치마크
================================

기존 jem_test 1일치 데이터를 새 스키마에 복사하며
비압축/압축 용량을 측정합니다.

Phase 1: 기본 태그 (~93 tags)
Phase 2: 태그 2배 (~186 tags) - tag_id+1000 복제

측정 방식: 30일 실측 후 1년/5년은 선형 외삽
(hypertable_size() 사용으로 정확한 크기 측정)

Usage:
    python -X utf8 benchmark_compression.py [--host localhost] [--days 30]
"""

import asyncio
import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

KST = timezone(timedelta(hours=9))

COLUMNS = [
    "timestamp", "plc_id", "tag_id",
    "v_bool", "v_int", "v_bigint", "v_float", "v_text", "quality_code",
]

BATCH_SIZE = 50_000
BENCH_SCHEMA = "jem_bench"

# ─────────────────────────────────────────────────────────────
# DDL (publisher와 동일)
# ─────────────────────────────────────────────────────────────

DDL_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS {schema}.plc_data_integrated (
    timestamp       TIMESTAMPTZ       NOT NULL,
    plc_id          SMALLINT          NOT NULL,
    tag_id          INTEGER           NOT NULL,
    v_bool          BOOLEAN,
    v_int           INTEGER,
    v_bigint        BIGINT,
    v_float         DOUBLE PRECISION,
    v_text          TEXT,
    quality_code    SMALLINT          DEFAULT 1
);
"""

DDL_HYPERTABLE = """
SELECT create_hypertable(
    '{schema}.plc_data_integrated', 'timestamp',
    chunk_time_interval => INTERVAL '1 day',
    if_not_exists => TRUE
);
"""

DDL_INDEX = """
CREATE INDEX IF NOT EXISTS idx_plc_data_int_plc_tag_time
ON {schema}.plc_data_integrated (plc_id, tag_id, timestamp DESC);
"""

DDL_COMPRESSION = """
ALTER TABLE {schema}.plc_data_integrated SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'plc_id, tag_id',
    timescaledb.compress_orderby = 'timestamp DESC'
);
"""


async def setup_schema(conn, schema: str):
    """스키마 + 하이퍼테이블 생성"""
    await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    await conn.execute(f"CREATE SCHEMA {schema}")
    await conn.execute(DDL_CREATE_TABLE.format(schema=schema))
    await conn.execute(DDL_HYPERTABLE.format(schema=schema))
    await conn.execute(DDL_INDEX.format(schema=schema))
    await conn.execute(DDL_COMPRESSION.format(schema=schema))


async def get_hypertable_size(conn, schema: str) -> int:
    """하이퍼테이블 전체 크기 (bytes) - 청크 포함"""
    try:
        size = await conn.fetchval(
            f"SELECT hypertable_size('{schema}.plc_data_integrated')"
        )
        return size or 0
    except Exception:
        return 0


async def get_detailed_size(conn, schema: str) -> Dict[str, int]:
    """테이블/인덱스/토스트 상세 크기"""
    try:
        row = await conn.fetchrow(
            f"SELECT * FROM hypertable_detailed_size('{schema}.plc_data_integrated')"
        )
        return {
            "table": row["table_bytes"],
            "index": row["index_bytes"],
            "toast": row["toast_bytes"],
            "total": row["total_bytes"],
        }
    except Exception:
        return {"table": 0, "index": 0, "toast": 0, "total": 0}


def fmt_size(b: int) -> str:
    """바이트 -> 사람 읽기 좋은 형식"""
    if b < 1024:
        return f"{b} B"
    elif b < 1024 ** 2:
        return f"{b / 1024:.1f} KB"
    elif b < 1024 ** 3:
        return f"{b / (1024**2):.2f} MB"
    else:
        return f"{b / (1024**3):.2f} GB"


async def compress_day_chunks(conn, schema: str, cutoff: datetime) -> int:
    """cutoff 이전 미압축 청크 압축"""
    chunks = await conn.fetch(
        "SELECT chunk_schema || '.' || chunk_name as chunk_full "
        "FROM timescaledb_information.chunks "
        "WHERE hypertable_schema = $1 "
        "  AND hypertable_name = 'plc_data_integrated' "
        "  AND range_end <= $2 "
        "  AND NOT is_compressed",
        schema, cutoff,
    )
    compressed = 0
    for c in chunks:
        try:
            await conn.execute(f"SELECT compress_chunk('{c['chunk_full']}')")
            compressed += 1
        except Exception as e:
            print(f"  [WARN] 압축 실패: {e}")
    return compressed


async def load_source_data(conn, source_schema: str) -> List[tuple]:
    """소스 스키마에서 최근 1일치 데이터 로드"""
    max_ts = await conn.fetchval(
        f"SELECT MAX(timestamp) FROM {source_schema}.plc_data_integrated"
    )
    start_ts = max_ts - timedelta(days=1)

    rows = await conn.fetch(
        f"SELECT timestamp, plc_id, tag_id, v_bool, v_int, v_bigint, "
        f"v_float, v_text, quality_code "
        f"FROM {source_schema}.plc_data_integrated "
        f"WHERE timestamp >= $1 ORDER BY timestamp",
        start_ts,
    )

    min_ts = rows[0]["timestamp"]
    data = []
    for r in rows:
        offset_sec = (r["timestamp"] - min_ts).total_seconds()
        data.append((
            offset_sec,
            r["plc_id"], r["tag_id"],
            r["v_bool"], r["v_int"], r["v_bigint"],
            r["v_float"], r["v_text"], r["quality_code"],
        ))
    return data


def make_day_records(source_data: List[tuple], day_date: datetime) -> List[tuple]:
    """소스 데이터의 오프셋을 day_date에 맞게 타임스탬프 생성"""
    records = []
    for row in source_data:
        ts = day_date + timedelta(seconds=row[0])
        records.append((ts, *row[1:]))
    return records


def double_tags(source_data: List[tuple]) -> List[tuple]:
    """tag_id를 복제하여 2배로 만들기 (원본 + tag_id+1000)"""
    doubled = list(source_data)
    for row in source_data:
        doubled.append((
            row[0], row[1], row[2] + 1000,
            row[3], row[4], row[5], row[6], row[7], row[8],
        ))
    return doubled


async def insert_day(conn, schema: str, records: List[tuple]):
    """COPY로 1일 데이터 삽입"""
    for i in range(0, len(records), BATCH_SIZE):
        batch = records[i:i + BATCH_SIZE]
        await conn.copy_records_to_table(
            "plc_data_integrated",
            records=batch,
            columns=COLUMNS,
            schema_name=schema,
        )


async def run_benchmark(
    conn, schema: str, source_data: List[tuple],
    total_days: int, label: str,
) -> Dict[str, Any]:
    """벤치마크: 매일 삽입 + 압축, 주요 시점 크기 측정"""

    n_tags = len(set((r[1], r[2]) for r in source_data))
    results: Dict[str, Any] = {
        "label": label,
        "rows_per_day": len(source_data),
        "total_tags": n_tags,
    }

    start_date = datetime(2020, 1, 1, tzinfo=KST)
    total_rows = 0
    total_start = time.time()

    # 측정 시점
    milestones = {1: "1day", 7: "1week", 30: "1month"}

    for day in range(total_days):
        day_date = start_date + timedelta(days=day)
        day_num = day + 1

        records = make_day_records(source_data, day_date)
        await insert_day(conn, schema, records)
        total_rows += len(records)

        # Day 1 비압축 (압축 전 측정)
        if day_num == 1:
            results["1day_uncompressed"] = await get_hypertable_size(conn, schema)

        # 압축
        cutoff = day_date + timedelta(days=1)
        await compress_day_chunks(conn, schema, cutoff)

        # 마일스톤 측정
        if day_num in milestones:
            size = await get_hypertable_size(conn, schema)
            results[milestones[day_num]] = size

        # 진행 출력
        elapsed = time.time() - total_start
        if day_num in milestones or day_num % 10 == 0 or day_num == total_days:
            size = await get_hypertable_size(conn, schema)
            eta = (elapsed / day_num) * (total_days - day_num)
            print(
                f"  Day {day_num:>3}/{total_days} | "
                f"rows={total_rows:>12,} | "
                f"size={fmt_size(size):>10} | "
                f"elapsed={elapsed:.0f}s | "
                f"ETA={eta / 60:.0f}min",
                flush=True,
            )

    results["total_rows"] = total_rows
    results["elapsed_sec"] = time.time() - total_start

    # 상세 크기
    detail = await get_detailed_size(conn, schema)
    results["detail"] = detail

    # 1년/5년 외삽 (30일 기준)
    if "1month" in results:
        monthly = results["1month"]
        results["1year"] = int(monthly * 12.17)   # 365/30
        results["5year_est"] = int(monthly * 60.83)  # 365*5/30

    return results


def print_results(results: Dict[str, Any]):
    """결과 테이블 출력"""
    label = results["label"]
    tags = results["total_tags"]
    rpd = results["rows_per_day"]

    print(f"\n{'=' * 60}")
    print(f"  압축 벤치마크 결과 ({label}, {tags} tags)")
    print(f"{'=' * 60}")
    print(f"  1일 데이터:      {rpd:,} rows")
    print(f"  총 데이터:       {results.get('total_rows', 0):,} rows")
    print(f"  소요 시간:       {results.get('elapsed_sec', 0) / 60:.1f}분")
    print()

    uncomp = results.get("1day_uncompressed", 0)
    comp1 = results.get("1day", 0)
    if uncomp > 0 and comp1 > 0:
        ratio = uncomp / comp1
        print(f"  1일 비압축:      {fmt_size(uncomp)}")
        print(f"  1일 압축:        {fmt_size(comp1):>10}  (압축률 {ratio:.1f}:1)")
    elif uncomp > 0:
        print(f"  1일 비압축:      {fmt_size(uncomp)}")

    for key, lbl in [
        ("1week", "1주일 압축"),
        ("1month", "1개월 압축"),
        ("1year", "1년 (외삽)"),
        ("5year_est", "5년 (외삽)"),
    ]:
        val = results.get(key, 0)
        if val:
            print(f"  {lbl}:    {fmt_size(val):>10}")

    detail = results.get("detail", {})
    if detail.get("total"):
        print(f"\n  -- 상세 (마지막 시점) --")
        print(f"  테이블 데이터:   {fmt_size(detail['table'])}")
        print(f"  인덱스:         {fmt_size(detail['index'])}")
        print(f"  TOAST:          {fmt_size(detail['toast'])}")
        print(f"  합계:           {fmt_size(detail['total'])}")

    print(f"{'=' * 60}", flush=True)


async def main():
    parser = argparse.ArgumentParser(description="TimescaleDB 압축 벤치마크")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="neurosense")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="neuro0901")
    parser.add_argument("--source-schema", default="jem_test")
    parser.add_argument("--days", type=int, default=30,
                        help="실제 적재할 일수 (기본 30, 1년/5년은 외삽)")
    args = parser.parse_args()

    try:
        import asyncpg
    except ImportError:
        print("ERROR: asyncpg 필요. pip install asyncpg")
        sys.exit(1)

    dsn = f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.database}"
    conn = await asyncpg.connect(dsn)

    print("=" * 60)
    print("  TimescaleDB 압축 벤치마크")
    print(f"  DB: {args.host}:{args.port}/{args.database}")
    print(f"  소스: {args.source_schema}.plc_data_integrated")
    print(f"  실측 기간: {args.days}일 (1년/5년은 외삽)")
    print("=" * 60, flush=True)

    # ── 소스 데이터 로드 ──
    print("\n[LOAD] 소스 데이터 로드 (최근 1일)...", flush=True)
    source_data = await load_source_data(conn, args.source_schema)
    unique_tags = set((r[1], r[2]) for r in source_data)
    print(f"  {len(source_data):,} rows, {len(unique_tags)} unique tags", flush=True)

    all_results = []

    # ── Phase 1: 기본 태그 ──
    print(f"\n{'#' * 60}")
    print(f"  Phase 1: 기본 태그 ({len(unique_tags)} tags)")
    print(f"{'#' * 60}", flush=True)

    await setup_schema(conn, BENCH_SCHEMA)
    print(f"  스키마 {BENCH_SCHEMA} 생성 완료", flush=True)

    r1 = await run_benchmark(
        conn, BENCH_SCHEMA, source_data, args.days,
        label=f"기본 ({len(unique_tags)} tags)"
    )
    all_results.append(r1)
    print_results(r1)

    # ── Phase 2: 태그 2배 ──
    print(f"\n{'#' * 60}")
    doubled_data = double_tags(source_data)
    unique_doubled = set((r[1], r[2]) for r in doubled_data)
    print(f"  Phase 2: 태그 2배 ({len(unique_doubled)} tags)")
    print(f"{'#' * 60}", flush=True)

    await setup_schema(conn, BENCH_SCHEMA)
    print(f"  스키마 {BENCH_SCHEMA} 재생성 완료", flush=True)

    r2 = await run_benchmark(
        conn, BENCH_SCHEMA, doubled_data, args.days,
        label=f"2배 ({len(unique_doubled)} tags)"
    )
    all_results.append(r2)
    print_results(r2)

    # ── 정리 ──
    print(f"\n[CLEANUP] {BENCH_SCHEMA} 스키마 삭제...", flush=True)
    await conn.execute(f"DROP SCHEMA IF EXISTS {BENCH_SCHEMA} CASCADE")
    print("  삭제 완료", flush=True)

    # ── 전체 비교 ──
    print(f"\n{'=' * 60}")
    print("  전체 비교 요약")
    print(f"{'=' * 60}")
    print(f"  {'항목':<16} {'기본':>14} {'2배':>14} {'배율':>8}")
    print(f"  {'-' * 52}")

    for key, lbl in [
        ("1day_uncompressed", "1일 비압축"),
        ("1day", "1일 압축"),
        ("1week", "1주일"),
        ("1month", "1개월"),
        ("1year", "1년 (외삽)"),
        ("5year_est", "5년 (외삽)"),
    ]:
        v1 = all_results[0].get(key, 0)
        v2 = all_results[1].get(key, 0)
        ratio = f"{v2 / v1:.1f}x" if v1 > 0 else "-"
        print(f"  {lbl:<16} {fmt_size(v1):>14} {fmt_size(v2):>14} {ratio:>8}")

    print(f"{'=' * 60}")
    await conn.close()
    print("\n완료.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
