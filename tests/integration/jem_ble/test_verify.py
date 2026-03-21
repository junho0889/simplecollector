"""
JEM+BLE Integration Test — DB Verification
============================================

검증 항목:
  1. 스키마 초기화: plc_master + ble_master 테이블 존재
  2. PLC 그룹 테이블: plc_data_*, alm_* 존재
  3. BLE 그룹 테이블: ble_data_* 존재
  4. PLC 데이터: plc_data_integrated에 plc_id=1,2 레코드
  5. BLE 데이터: ble_data_integrated에 ble_id=1 레코드
  6. BLE latest: ble_data_latest에 PK별 1개
  7. 다중 디바이스 격리: PLC 테이블에 ble_id 없고, BLE 테이블에 plc_id만 있지 않음
  8. Master sync: plc_master, ble_master에 데이터 존재
"""

import asyncio
import argparse
import sys
import time

try:
    import asyncpg
except ImportError:
    print("asyncpg required: pip install asyncpg")
    sys.exit(1)


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name: str, detail: str = ""):
        self.passed += 1
        msg = f"  [PASS] {name}"
        if detail:
            msg += f" — {detail}"
        print(msg)

    def fail(self, name: str, detail: str):
        self.failed += 1
        self.errors.append((name, detail))
        print(f"  [FAIL] {name} — {detail}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"  Results: {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print(f"\n  Failures:")
            for name, detail in self.errors:
                print(f"    - {name}: {detail}")
        print(f"{'='*60}\n")
        return self.failed == 0


async def run_tests(dsn: str, schema: str):
    result = TestResult()

    print(f"\n{'='*60}")
    print(f"  JEM+BLE DB Verification")
    print(f"  Schema: {schema}")
    print(f"{'='*60}\n")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)

    async with pool.acquire() as conn:

        # ================================================================
        # 1. 마스터 테이블 존재
        # ================================================================
        print("--- 1. 마스터 테이블 ---")
        for table in ["plc_master", "ble_master"]:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=$1 AND table_name=$2)", schema, table
            )
            if exists:
                result.ok(f"table:{table}")
            else:
                result.fail(f"table:{table}", "테이블 없음")

        # ================================================================
        # 2. PLC 그룹 테이블
        # ================================================================
        print("\n--- 2. PLC 그룹 테이블 ---")
        plc_tables = [
            "plc_data_master", "plc_data_latest", "plc_data_integrated",
            "alm_master", "alm_latest",
        ]
        for table in plc_tables:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=$1 AND table_name=$2)", schema, table
            )
            if exists:
                result.ok(f"table:{table}")
            else:
                result.fail(f"table:{table}", "테이블 없음")

        # ================================================================
        # 3. BLE 그룹 테이블
        # ================================================================
        print("\n--- 3. BLE 그룹 테이블 ---")
        ble_tables = [
            "ble_data_master", "ble_data_latest", "ble_data_integrated",
        ]
        for table in ble_tables:
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=$1 AND table_name=$2)", schema, table
            )
            if exists:
                result.ok(f"table:{table}")
            else:
                result.fail(f"table:{table}", "테이블 없음")

        # ================================================================
        # 4. PLC 데이터 확인
        # ================================================================
        print("\n--- 4. PLC 데이터 ---")
        try:
            plc_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.plc_data_integrated"
            )
            if plc_count and plc_count > 0:
                result.ok("plc_data_integrated", f"{plc_count} rows")
            else:
                result.fail("plc_data_integrated", "0 rows — PLC 데이터 누락")

            # plc_id별 확인
            for plc_id in [1, 2]:
                cnt = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {schema}.plc_data_integrated WHERE plc_id=$1",
                    plc_id
                )
                if cnt and cnt > 0:
                    result.ok(f"plc_data:plc_id={plc_id}", f"{cnt} rows")
                else:
                    result.fail(f"plc_data:plc_id={plc_id}", "데이터 없음")
        except Exception as e:
            result.fail("plc_data_integrated", str(e))

        # alm
        try:
            alm_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.alm_latest"
            )
            if alm_count and alm_count > 0:
                result.ok("alm_latest", f"{alm_count} rows")
            else:
                result.fail("alm_latest", "0 rows — alm 데이터 누락")
        except Exception as e:
            result.fail("alm_latest", str(e))

        # ================================================================
        # 5. BLE 데이터 확인 (핵심 테스트)
        # ================================================================
        print("\n--- 5. BLE 데이터 (핵심) ---")
        try:
            ble_int_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.ble_data_integrated"
            )
            if ble_int_count and ble_int_count > 0:
                result.ok("ble_data_integrated", f"{ble_int_count} rows")
            else:
                result.fail("ble_data_integrated", "0 rows — BLE 데이터 누락")

            # ble_id 확인
            ble_id_count = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.ble_data_integrated WHERE ble_id=$1",
                1
            )
            if ble_id_count and ble_id_count > 0:
                result.ok("ble_data:ble_id=1", f"{ble_id_count} rows")
            else:
                result.fail("ble_data:ble_id=1", "ble_id=1 데이터 없음")
        except Exception as e:
            result.fail("ble_data_integrated", str(e))

        # ================================================================
        # 6. BLE latest 정합성
        # ================================================================
        print("\n--- 6. BLE latest ---")
        try:
            ble_latest = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.ble_data_latest"
            )
            if ble_latest and ble_latest > 0:
                result.ok("ble_data_latest", f"{ble_latest} rows")
            else:
                result.fail("ble_data_latest", "0 rows")

            # PK 중복 검사
            ble_dup = await conn.fetchval(f"""
                SELECT COUNT(*) FROM (
                    SELECT ble_id, tag_id, COUNT(*) as cnt
                    FROM {schema}.ble_data_latest
                    GROUP BY ble_id, tag_id
                    HAVING COUNT(*) > 1
                ) d
            """)
            if ble_dup == 0:
                result.ok("ble_latest_unique", "PK별 1개씩")
            else:
                result.fail("ble_latest_unique", f"{ble_dup}개 중복")
        except Exception as e:
            result.fail("ble_data_latest", str(e))

        # ================================================================
        # 7. 디바이스 타입 격리
        # ================================================================
        print("\n--- 7. 디바이스 타입 격리 ---")
        try:
            # ble_data_integrated에 ble_id 컬럼이 있는지
            has_ble_id = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name='ble_data_integrated' "
                "AND column_name='ble_id')", schema
            )
            if has_ble_id:
                result.ok("ble_table_has_ble_id", "ble_data에 ble_id 컬럼 존재")
            else:
                result.fail("ble_table_has_ble_id", "ble_data에 ble_id 컬럼 없음")

            # plc_data에는 plc_id가 있어야 함
            has_plc_id = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_schema=$1 AND table_name='plc_data_integrated' "
                "AND column_name='plc_id')", schema
            )
            if has_plc_id:
                result.ok("plc_table_has_plc_id", "plc_data에 plc_id 컬럼 존재")
            else:
                result.fail("plc_table_has_plc_id", "plc_data에 plc_id 컬럼 없음")
        except Exception as e:
            result.fail("device_isolation", str(e))

        # ================================================================
        # 8. Master sync 확인
        # ================================================================
        print("\n--- 8. Master sync ---")
        try:
            plc_master_cnt = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.plc_master"
            )
            if plc_master_cnt and plc_master_cnt > 0:
                result.ok("plc_master_sync", f"{plc_master_cnt} PLC(s)")
            else:
                result.fail("plc_master_sync", "plc_master 비어있음")

            ble_master_cnt = await conn.fetchval(
                f"SELECT COUNT(*) FROM {schema}.ble_master"
            )
            if ble_master_cnt and ble_master_cnt > 0:
                result.ok("ble_master_sync", f"{ble_master_cnt} BLE device(s)")
            else:
                result.fail("ble_master_sync", "ble_master 비어있음")
        except Exception as e:
            result.fail("master_sync", str(e))

        # ================================================================
        # 9. 데이터 무결성
        # ================================================================
        print("\n--- 9. 데이터 무결성 ---")
        for table in ["plc_data_integrated", "ble_data_integrated"]:
            try:
                null_ts = await conn.fetchval(
                    f"SELECT COUNT(*) FROM {schema}.{table} WHERE timestamp IS NULL"
                )
                if null_ts == 0:
                    result.ok(f"{table}:no_null_ts", "NULL timestamp 없음")
                else:
                    result.fail(f"{table}:no_null_ts", f"{null_ts}건 NULL")
            except Exception as e:
                result.fail(f"{table}:integrity", str(e))

        # ================================================================
        # 10. 값 타입 확인 (BLE float/int 혼합)
        # ================================================================
        print("\n--- 10. BLE 값 타입 ---")
        try:
            ble_float = await conn.fetchval(f"""
                SELECT COUNT(*) FROM {schema}.ble_data_integrated
                WHERE v_float IS NOT NULL
            """)
            ble_int = await conn.fetchval(f"""
                SELECT COUNT(*) FROM {schema}.ble_data_integrated
                WHERE v_int IS NOT NULL
            """)
            if ble_float and ble_float > 0:
                result.ok("ble_float_values", f"{ble_float}건")
            else:
                result.fail("ble_float_values", "BLE float 값 없음")
            if ble_int and ble_int > 0:
                result.ok("ble_int_values", f"{ble_int}건")
            else:
                result.fail("ble_int_values", "BLE int 값 없음")
        except Exception as e:
            result.fail("ble_value_types", str(e))

    await pool.close()
    return result.summary()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="timescaledb")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--db", default="test_collector")
    parser.add_argument("--user", default="postgres")
    parser.add_argument("--password", default="postgres")
    parser.add_argument("--schema", default="public")
    parser.add_argument("--wait", type=int, default=10)
    args = parser.parse_args()

    dsn = f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.db}"

    print(f"\nPublisher 처리 대기: {args.wait}초...")
    time.sleep(args.wait)

    success = asyncio.run(run_tests(dsn, args.schema))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
