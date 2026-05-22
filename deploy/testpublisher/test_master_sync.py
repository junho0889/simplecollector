"""
Master Sync 테스트 스크립트
===========================

테스트 시나리오:
  1. 초기 sync: CSV 5개 태그 → master 테이블에 5개 행 확인
  2. 태그 삭제: CSV에서 태그 2개 제거 → resync → master에서도 사라졌는지 확인
  3. 태그 추가: CSV에 새 태그 3개 추가 → resync → master에 반영 확인
  4. 태그 변경: 기존 태그의 description 변경 → resync → 반영 확인

사용법:
  1. docker compose up -d  (TimescaleDB 실행)
  2. python test_master_sync.py
"""

import asyncio
import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Any

# collector-publisher 소스 경로 추가
PUBLISHER_SRC = Path(__file__).resolve().parent.parent.parent.parent / "collector-publisher"
sys.path.insert(0, str(PUBLISHER_SRC))

import asyncpg

from src.publishers.schema_init import get_global_init_sql, get_group_init_sql
from src.publishers.master_sync import MasterDataSync

# DB 설정
DB_DSN = "postgresql://postgres:postgres@localhost:15432/testdb"
SCHEMA = "public"

# 테스트 데이터
COLLECTOR_YAML_TEMPLATE = """\
collector:
  plc_id: 1
  name: "PLC-TEST"
  description: "Master sync test PLC"
  tags_file: "config/tags_test.csv"
  protocol:
    type: "mc_protocol"
    host: "192.168.0.100"
    port: 5000
    unit_id: 1
    timeout_ms: 5000
    reconnect_interval_ms: 3000
  collection_groups:
    - name: "plc_data"
      interval_ms: 1000
    - name: "alm"
      interval_ms: 1000
"""

CSV_HEADER = "tag_id,tag_name,memory,address,data_type,collection_group,scale,offset,decimals,word_length,format,unit,description"

TAGS_INITIAL = [
    "1,온도센서A,D,100,uint16,plc_data,1,0,1,,,,온도센서 A",
    "2,압력센서B,D,102,float32,plc_data,1,0,2,,,,압력센서 B",
    "3,모터속도,D,104,int16,plc_data,1,0,,2,,,모터 RPM",
    "4,알람1,L,500,bool,alm,1,0,,,,,비상정지 알람",
    "5,알람2,L,501,bool,alm,1,0,,,,,과열 알람",
]

TAGS_AFTER_DELETE = [
    # tag 2, 5 삭제
    "1,온도센서A,D,100,uint16,plc_data,1,0,1,,,,온도센서 A",
    "3,모터속도,D,104,int16,plc_data,1,0,,2,,,모터 RPM",
    "4,알람1,L,500,bool,alm,1,0,,,,,비상정지 알람",
]

TAGS_AFTER_ADD = [
    "1,온도센서A,D,100,uint16,plc_data,1,0,1,,,,온도센서 A",
    "3,모터속도,D,104,int16,plc_data,1,0,,2,,,모터 RPM",
    "4,알람1,L,500,bool,alm,1,0,,,,,비상정지 알람",
    "6,유량계C,D,200,uint32,plc_data,1,0,,4,,,유량계 C",
    "7,알람3,L,502,bool,alm,1,0,,,,,과부하 알람",
    "8,알람4,L,503,bool,alm,1,0,,,,,통신이상 알람",
]

TAGS_AFTER_MODIFY = [
    "1,온도센서A_수정,D,100,uint16,plc_data,1,0,1,,,,온도센서 A (수정됨)",
    "3,모터속도,D,104,int16,plc_data,1,0,,2,,,모터 RPM",
    "4,알람1,L,500,bool,alm,1,0,,,,,비상정지 알람 (수정)",
    "6,유량계C,D,200,uint32,plc_data,1,0,,4,,,유량계 C",
    "7,알람3,L,502,bool,alm,1,0,,,,,과부하 알람",
    "8,알람4,L,503,bool,alm,1,0,,,,,통신이상 알람",
]


def write_csv(csv_path: Path, rows: List[str]) -> None:
    """테스트 CSV 작성."""
    csv_path.write_text(
        CSV_HEADER + "\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )


async def init_schema(pool: asyncpg.Pool) -> None:
    """테이블 생성."""
    stmts = get_global_init_sql(SCHEMA)
    for group_name in ("plc_data", "alm"):
        stmts.extend(get_group_init_sql(SCHEMA, group_name, mode="latest"))

    async with pool.acquire() as conn:
        for sql in stmts:
            try:
                await conn.execute(sql.strip())
            except (asyncpg.DuplicateTableError, asyncpg.DuplicateObjectError):
                pass
            except Exception as e:
                # TimescaleDB 관련 에러는 무시 (hypertable 등)
                if "already a hypertable" not in str(e):
                    print(f"  [WARN] Schema init: {e}")


async def clean_tables(pool: asyncpg.Pool) -> None:
    """테스트 전 마스터 테이블 초기화."""
    async with pool.acquire() as conn:
        for table in ("plc_data_master", "alm_master", "plc_master"):
            try:
                await conn.execute(f"DELETE FROM {SCHEMA}.{table}")
            except asyncpg.UndefinedTableError:
                pass


async def get_master_rows(pool: asyncpg.Pool, table: str) -> List[Dict[str, Any]]:
    """마스터 테이블 조회."""
    async with pool.acquire() as conn:
        try:
            rows = await conn.fetch(
                f"SELECT plc_id, tag_id, tag_name, description FROM {SCHEMA}.{table} ORDER BY tag_id"
            )
            return [dict(r) for r in rows]
        except asyncpg.UndefinedTableError:
            return []


async def run_sync(pool: asyncpg.Pool, config_dir: str) -> None:
    """MasterDataSync 실행."""
    sync = MasterDataSync(config_dir, SCHEMA)
    await sync.sync(pool)


def assert_tags(actual: List[Dict], expected_tag_ids: List[int], label: str) -> bool:
    """태그 ID 목록 검증."""
    actual_ids = sorted([r["tag_id"] for r in actual])
    expected_ids = sorted(expected_tag_ids)
    if actual_ids == expected_ids:
        print(f"  [PASS] {label}: tag_ids={actual_ids}")
        return True
    else:
        print(f"  [FAIL] {label}: expected={expected_ids}, actual={actual_ids}")
        return False


def assert_description(actual: List[Dict], tag_id: int, expected_desc: str, label: str) -> bool:
    """특정 태그의 description 검증."""
    for r in actual:
        if r["tag_id"] == tag_id:
            if r["description"] == expected_desc:
                print(f"  [PASS] {label}: tag_id={tag_id} description='{expected_desc}'")
                return True
            else:
                print(f"  [FAIL] {label}: tag_id={tag_id} expected='{expected_desc}', actual='{r['description']}'")
                return False
    print(f"  [FAIL] {label}: tag_id={tag_id} not found")
    return False


async def main():
    print("=" * 60)
    print("Master Sync 테스트")
    print("=" * 60)

    # 임시 config 디렉토리
    tmpdir = Path(tempfile.mkdtemp(prefix="testpub_"))
    csv_path = tmpdir / "tags_test.csv"
    yaml_path = tmpdir / "collector_plc1.yaml"
    yaml_path.write_text(COLLECTOR_YAML_TEMPLATE, encoding="utf-8")

    passed = 0
    failed = 0

    try:
        pool = await asyncpg.create_pool(dsn=DB_DSN, min_size=1, max_size=3, command_timeout=10)
    except Exception as e:
        print(f"\n[ERROR] DB 연결 실패: {e}")
        print("  → docker compose up -d 로 TimescaleDB를 먼저 실행하세요")
        return

    try:
        # 스키마 초기화
        print("\n[Setup] 스키마 초기화...")
        await init_schema(pool)
        await clean_tables(pool)
        print("  Done")

        # ===================================================================
        # Test 1: 초기 sync (5 tags)
        # ===================================================================
        print("\n[Test 1] 초기 sync (5 tags: plc_data 3 + alm 2)")
        write_csv(csv_path, TAGS_INITIAL)
        await run_sync(pool, str(tmpdir))

        plc_data = await get_master_rows(pool, "plc_data_master")
        alm_data = await get_master_rows(pool, "alm_master")

        if assert_tags(plc_data, [1, 2, 3], "plc_data_master"):
            passed += 1
        else:
            failed += 1
        if assert_tags(alm_data, [4, 5], "alm_master"):
            passed += 1
        else:
            failed += 1

        # ===================================================================
        # Test 2: 태그 삭제 (tag 2, 5 제거)
        # ===================================================================
        print("\n[Test 2] 태그 삭제 (tag 2, 5 제거 → plc_data 2 + alm 1)")
        write_csv(csv_path, TAGS_AFTER_DELETE)
        await run_sync(pool, str(tmpdir))

        plc_data = await get_master_rows(pool, "plc_data_master")
        alm_data = await get_master_rows(pool, "alm_master")

        if assert_tags(plc_data, [1, 3], "plc_data_master (삭제 반영)"):
            passed += 1
        else:
            failed += 1
        if assert_tags(alm_data, [4], "alm_master (삭제 반영)"):
            passed += 1
        else:
            failed += 1

        # ===================================================================
        # Test 3: 태그 추가 (tag 6, 7, 8 추가)
        # ===================================================================
        print("\n[Test 3] 태그 추가 (tag 6, 7, 8 추가 → plc_data 3 + alm 3)")
        write_csv(csv_path, TAGS_AFTER_ADD)
        await run_sync(pool, str(tmpdir))

        plc_data = await get_master_rows(pool, "plc_data_master")
        alm_data = await get_master_rows(pool, "alm_master")

        if assert_tags(plc_data, [1, 3, 6], "plc_data_master (추가 반영)"):
            passed += 1
        else:
            failed += 1
        if assert_tags(alm_data, [4, 7, 8], "alm_master (추가 반영)"):
            passed += 1
        else:
            failed += 1

        # ===================================================================
        # Test 4: 태그 변경 (tag 1 tag_name/desc 변경, tag 4 desc 변경)
        # ===================================================================
        print("\n[Test 4] 태그 변경 (tag 1, 4 description 수정)")
        write_csv(csv_path, TAGS_AFTER_MODIFY)
        await run_sync(pool, str(tmpdir))

        plc_data = await get_master_rows(pool, "plc_data_master")
        alm_data = await get_master_rows(pool, "alm_master")

        # 태그 수 동일
        if assert_tags(plc_data, [1, 3, 6], "plc_data_master (수 유지)"):
            passed += 1
        else:
            failed += 1

        # description 변경 확인
        if assert_description(plc_data, 1, "온도센서 A (수정됨)", "plc_data_master desc"):
            passed += 1
        else:
            failed += 1
        if assert_description(alm_data, 4, "비상정지 알람 (수정)", "alm_master desc"):
            passed += 1
        else:
            failed += 1

        # ===================================================================
        # 결과
        # ===================================================================
        print("\n" + "=" * 60)
        total = passed + failed
        print(f"결과: {passed}/{total} PASSED, {failed}/{total} FAILED")
        if failed == 0:
            print("모든 테스트 통과!")
        else:
            print("일부 테스트 실패 — master_sync.py 확인 필요")
        print("=" * 60)

    except Exception as e:
        print(f"\n[ERROR] 테스트 중 에러: {e}")
        import traceback
        traceback.print_exc()

    finally:
        await pool.close()
        # 임시 디렉토리 정리
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
