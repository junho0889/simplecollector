"""
Extensions 기능 테스트 스크립트
===============================

config 파싱 → DDL 생성 → DB 실행 → 트리거 동작을 검증합니다.

테스트 항목:
    1. Config 파싱: YAML extensions → dataclass 변환
    2. DDL 생성: history/snapshot SQL 생성 확인
    3. DB 실행: 테이블/트리거/함수 생성 확인
    4. 트리거 동작: INSERT → UPDATE → history 기록 확인

Usage:
    # Config + DDL 테스트만 (DB 불필요)
    python test_extensions.py --no-db

    # 전체 테스트 (TimescaleDB 필요)
    python test_extensions.py [--host localhost] [--port 5432]

    # Docker 테스트 환경 사용 시
    python test_extensions.py --host localhost --port 5432 --schema jem_ext_test
"""

import asyncio
import argparse
import sys
import os
import textwrap

# collector-publisher의 src를 import path에 추가
PUBLISHER_SRC = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'collector-publisher')
)
sys.path.insert(0, PUBLISHER_SRC)


# ============================================================================
# 1. Config 파싱 테스트
# ============================================================================

def test_config_parsing():
    """Config 파싱: extensions YAML → dataclass 변환."""
    from src.config import ConfigLoader, ExtensionsConfig, HistoryExtConfig

    print("\n" + "=" * 60)
    print("TEST 1: Config 파싱")
    print("=" * 60)

    # ---- 1a. publisher_test.yaml 파싱 ----
    yaml_path = os.path.join(os.path.dirname(__file__), 'config', 'publisher_test.yaml')
    if os.path.exists(yaml_path):
        config = ConfigLoader.load(yaml_path)

        alm_group = config.database.get_group_config('alm')
        print(f"  alm group: mode={alm_group.mode}, has_latest={alm_group.has_latest}")
        print(f"  alm extensions: {alm_group.extensions}")
        print(f"  alm has_history: {alm_group.has_history}")
        print(f"  alm has_snapshot: {alm_group.has_snapshot}")

        assert alm_group.has_history, "alm should have history extension"
        assert alm_group.extensions.history.trigger_on == "v_bool", \
            f"trigger_on should be v_bool, got {alm_group.extensions.history.trigger_on}"
        assert not alm_group.has_snapshot, "alm should not have snapshot"

        plc_data_group = config.database.get_group_config('plc_data')
        assert not plc_data_group.has_history, "plc_data should not have history"
        assert not plc_data_group.has_snapshot, "plc_data should not have snapshot"

        print("  [PASS] publisher_test.yaml 파싱 성공")
    else:
        print(f"  [SKIP] {yaml_path} not found")

    # ---- 1b. 프로그래매틱 파싱 (history + snapshot) ----
    ext_raw = {
        'history': {'trigger_on': 'v_bool'},
        'snapshot': {
            'triggers': [
                {'watch_tag': 1, 'capture_tags': [1, 2, 3]},
                {'watch_tag': 10, 'capture_tags': [10, 11, 12]},
            ]
        }
    }
    ext = ConfigLoader._parse_extensions(ext_raw)
    assert ext is not None, "extensions should not be None"
    assert ext.history is not None, "history should not be None"
    assert ext.history.trigger_on == "v_bool"
    assert ext.snapshot is not None, "snapshot should not be None"
    assert len(ext.snapshot.triggers) == 2
    assert ext.snapshot.triggers[0].watch_tag == 1
    assert ext.snapshot.triggers[0].capture_tags == [1, 2, 3]
    print("  [PASS] history + snapshot 파싱 성공")

    # ---- 1c. history: true 단축형 ----
    ext2 = ConfigLoader._parse_extensions({'history': True})
    assert ext2 is not None
    assert ext2.history is not None
    assert ext2.history.trigger_on == "all"  # 기본값
    print("  [PASS] history: true 단축형 파싱 성공")

    # ---- 1d. extensions 없음 ----
    ext3 = ConfigLoader._parse_extensions(None)
    assert ext3 is None
    ext4 = ConfigLoader._parse_extensions({})
    assert ext4 is None
    print("  [PASS] extensions 없음 처리 성공")

    print("\n  === Config 파싱 테스트 전체 PASS ===")
    return ext  # DDL 테스트에서 사용


# ============================================================================
# 2. DDL 생성 테스트
# ============================================================================

def test_ddl_generation(ext):
    """DDL 생성: history/snapshot SQL이 올바르게 생성되는지 확인."""
    from src.publishers.schema_init import get_group_init_sql

    print("\n" + "=" * 60)
    print("TEST 2: DDL 생성")
    print("=" * 60)

    schema = "test_ext"

    # ---- 2a. history extension DDL ----
    from src.config import ExtensionsConfig, HistoryExtConfig
    hist_ext = ExtensionsConfig(history=HistoryExtConfig(trigger_on="v_bool"))

    stmts = get_group_init_sql(
        schema, "alm", mode="latest",
        compression_after="1 day",
        retention_period="1 year",
        extensions=hist_ext,
    )

    sql_text = "\n".join(stmts)

    # 기본 테이블
    assert f"{schema}.alm_master" in sql_text, "alm_master DDL missing"
    assert f"{schema}.alm_latest" in sql_text, "alm_latest DDL missing"
    # integrated는 mode=latest이므로 없어야 함
    assert f"{schema}.alm_integrated" not in sql_text, "alm_integrated should not exist in latest mode"

    # history extension
    assert f"{schema}.alm_history" in sql_text, "alm_history DDL missing"
    assert "fn_alm_history_on_update" in sql_text, "history UPDATE trigger function missing"
    assert "fn_alm_history_on_insert" in sql_text, "history INSERT trigger function missing"
    assert "trg_alm_history_update" in sql_text, "history UPDATE trigger missing"
    assert "trg_alm_history_insert" in sql_text, "history INSERT trigger missing"
    assert "OLD.v_bool IS DISTINCT FROM NEW.v_bool" in sql_text, \
        "trigger_on=v_bool condition missing"
    assert "create_hypertable" in sql_text, "hypertable DDL missing"
    assert "add_compression_policy" in sql_text, "compression policy missing"
    assert "add_retention_policy" in sql_text, "retention policy missing"

    print(f"  [PASS] history DDL 생성 ({len(stmts)} statements)")

    # ---- 2b. snapshot extension DDL ----
    from src.config import SnapshotExtConfig, SnapshotTriggerConfig
    snap_ext = ExtensionsConfig(
        snapshot=SnapshotExtConfig(triggers=[
            SnapshotTriggerConfig(watch_tag=1, capture_tags=[1, 2, 3]),
            SnapshotTriggerConfig(watch_tag=10, capture_tags=[10, 11, 12]),
        ])
    )

    stmts2 = get_group_init_sql(
        schema, "log", mode="all",
        compression_after="1 day",
        extensions=snap_ext,
    )
    sql_text2 = "\n".join(stmts2)

    assert f"{schema}.log_snapshot" in sql_text2, "log_snapshot DDL missing"
    assert "fn_log_snapshot_on_update" in sql_text2, "snapshot trigger function missing"
    assert "trg_log_snapshot_update" in sql_text2, "snapshot trigger missing"
    assert "NEW.tag_id = 1" in sql_text2, "watch_tag=1 IF block missing"
    assert "NEW.tag_id = 10" in sql_text2, "watch_tag=10 IF block missing"
    assert "IN (1, 2, 3)" in sql_text2, "capture_tags for watch_tag=1 missing"
    assert "IN (10, 11, 12)" in sql_text2, "capture_tags for watch_tag=10 missing"

    print(f"  [PASS] snapshot DDL 생성 ({len(stmts2)} statements)")

    # ---- 2c. extensions 없는 그룹은 기존 동작 ----
    stmts3 = get_group_init_sql(
        schema, "plc_data", mode="all",
        compression_after="1 day",
    )
    sql_text3 = "\n".join(stmts3)
    assert "history" not in sql_text3.lower() or "history" not in sql_text3, \
        "plc_data should not have history DDL"
    assert "snapshot" not in sql_text3.lower() or "snapshot" not in sql_text3, \
        "plc_data should not have snapshot DDL"
    print(f"  [PASS] extensions 없는 그룹은 기존 동작 유지")

    # ---- 2d. mode=none/integrated면 extensions 생성 안 됨 (has_latest=False) ----
    stmts4 = get_group_init_sql(
        schema, "alm", mode="none",
        compression_after="1 day",
        extensions=hist_ext,
    )
    sql_text4 = "\n".join(stmts4)
    assert "alm_history" not in sql_text4, \
        "mode=none should not create history table"
    print(f"  [PASS] mode=none에서는 extensions 미생성")

    stmts5 = get_group_init_sql(
        schema, "alm", mode="integrated",
        compression_after="1 day",
        extensions=hist_ext,
    )
    sql_text5 = "\n".join(stmts5)
    assert "alm_history" not in sql_text5, \
        "mode=integrated should not create history table"
    print(f"  [PASS] mode=integrated에서는 extensions 미생성")

    print("\n  === DDL 생성 테스트 전체 PASS ===")


# ============================================================================
# 3. DB 실행 테스트
# ============================================================================

async def test_db_execution(args):
    """DB 실행: 테이블/트리거/함수 생성 + 트리거 동작 검증."""
    try:
        import asyncpg
    except ImportError:
        print("\n  [ERROR] asyncpg 필요: pip install asyncpg")
        return False

    from src.publishers.schema_init import get_global_init_sql, get_group_init_sql
    from src.config import (
        ExtensionsConfig, HistoryExtConfig,
        SnapshotExtConfig, SnapshotTriggerConfig,
    )

    print("\n" + "=" * 60)
    print("TEST 3: DB 실행 + 트리거 동작")
    print("=" * 60)

    schema = args.schema
    dsn = f"postgresql://{args.user}:{args.password}@{args.host}:{args.port}/{args.database}"

    try:
        conn = await asyncpg.connect(dsn)
    except Exception as e:
        print(f"\n  [ERROR] DB 연결 실패: {e}")
        return False

    try:
        # ---- 3a. 기존 테스트 스키마 정리 ----
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
        print(f"  스키마 초기화: {schema}")

        # ---- 3b. 글로벌 테이블 생성 ----
        global_stmts = get_global_init_sql(schema)
        for sql in global_stmts:
            await conn.execute(sql.strip())
        print(f"  [PASS] 글로벌 테이블 생성 ({len(global_stmts)} statements)")

        # ---- 3c. alm 그룹 (history extension) ----
        hist_ext = ExtensionsConfig(history=HistoryExtConfig(trigger_on="v_bool"))
        alm_stmts = get_group_init_sql(
            schema, "alm", mode="latest",
            compression_after="1 day",
            retention_period="1 year",
            extensions=hist_ext,
        )
        errors = []
        for sql in alm_stmts:
            try:
                await conn.execute(sql.strip())
            except Exception as e:
                errors.append(str(e))
                print(f"    [WARN] {str(e)[:100]}")
        if errors:
            print(f"  [WARN] alm 그룹 생성 시 {len(errors)} 경고 ({len(alm_stmts)} statements)")
        else:
            print(f"  [PASS] alm 그룹 + history extension 생성 ({len(alm_stmts)} statements)")

        # ---- 3d. 테이블 존재 확인 ----
        tables = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = $1
            ORDER BY table_name
        """, schema)
        table_names = [r['table_name'] for r in tables]
        print(f"  테이블 목록: {table_names}")

        for expected in ['plc_master', 'quality_master', 'alm_master', 'alm_latest', 'alm_history']:
            assert expected in table_names, f"테이블 {expected} 없음"
        print(f"  [PASS] 필수 테이블 존재 확인")

        # ---- 3e. 트리거 존재 확인 ----
        triggers = await conn.fetch("""
            SELECT trigger_name, event_manipulation, action_timing
            FROM information_schema.triggers
            WHERE trigger_schema = $1
              AND event_object_table = 'alm_latest'
            ORDER BY trigger_name
        """, schema)
        trigger_names = [r['trigger_name'] for r in triggers]
        print(f"  트리거 목록: {trigger_names}")

        assert 'trg_alm_history_update' in trigger_names, "UPDATE 트리거 없음"
        assert 'trg_alm_history_insert' in trigger_names, "INSERT 트리거 없음"
        print(f"  [PASS] history 트리거 존재 확인")

        # ---- 3f. 트리거 함수 존재 확인 ----
        functions = await conn.fetch("""
            SELECT routine_name FROM information_schema.routines
            WHERE routine_schema = $1
              AND routine_name LIKE 'fn_alm_history_%'
        """, schema)
        func_names = [r['routine_name'] for r in functions]
        print(f"  함수 목록: {func_names}")

        assert 'fn_alm_history_on_update' in func_names, "UPDATE 함수 없음"
        assert 'fn_alm_history_on_insert' in func_names, "INSERT 함수 없음"
        print(f"  [PASS] history 트리거 함수 존재 확인")

        # ---- 3g. hypertable 확인 ----
        ht = await conn.fetchval("""
            SELECT count(*) FROM timescaledb_information.hypertables
            WHERE hypertable_schema = $1
              AND hypertable_name = 'alm_history'
        """, schema)
        assert ht > 0, "alm_history가 hypertable이 아님"
        print(f"  [PASS] alm_history hypertable 확인")

        # ---- 3h. plc_master에 테스트 PLC 등록 ----
        await conn.execute(f"""
            INSERT INTO {schema}.plc_master (plc_id, plc_name, protocol_type, host, port)
            VALUES (1, 'TEST-PLC', 'mc', '127.0.0.1', 5001)
        """)

        # ---- 3i. INSERT 트리거 테스트 ----
        # 첫 INSERT → alm_history에도 기록되어야 함
        await conn.execute(f"""
            INSERT INTO {schema}.alm_latest (plc_id, tag_id, timestamp, v_bool, quality_code)
            VALUES (1, 1, NOW(), FALSE, 1)
        """)
        count_after_insert = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.alm_history"
        )
        assert count_after_insert == 1, \
            f"INSERT 후 history 레코드 = {count_after_insert}, expected 1"
        print(f"  [PASS] INSERT 트리거: alm_history에 첫 값 기록 (count={count_after_insert})")

        # ---- 3j. UPDATE 트리거 (값 변경 없음 → history 기록 없음) ----
        await conn.execute(f"""
            UPDATE {schema}.alm_latest
            SET timestamp = NOW(), v_bool = FALSE, updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        count_no_change = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.alm_history"
        )
        assert count_no_change == 1, \
            f"값 변경 없는 UPDATE 후 history = {count_no_change}, expected 1"
        print(f"  [PASS] UPDATE (변경 없음): history 기록 안 됨 (count={count_no_change})")

        # ---- 3k. UPDATE 트리거 (값 변경 → history 기록) ----
        await conn.execute(f"""
            UPDATE {schema}.alm_latest
            SET timestamp = NOW(), v_bool = TRUE, updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        count_after_change = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.alm_history"
        )
        assert count_after_change == 2, \
            f"값 변경 UPDATE 후 history = {count_after_change}, expected 2"
        print(f"  [PASS] UPDATE (FALSE→TRUE): history 기록됨 (count={count_after_change})")

        # ---- 3l. 한번 더 변경 (TRUE→FALSE) ----
        await conn.execute(f"""
            UPDATE {schema}.alm_latest
            SET timestamp = NOW(), v_bool = FALSE, updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        count_final = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.alm_history"
        )
        assert count_final == 3, \
            f"두 번째 변경 후 history = {count_final}, expected 3"
        print(f"  [PASS] UPDATE (TRUE→FALSE): history 기록됨 (count={count_final})")

        # ---- 3m. history 데이터 확인 ----
        history_rows = await conn.fetch(f"""
            SELECT plc_id, tag_id, v_bool, timestamp
            FROM {schema}.alm_history
            ORDER BY timestamp
        """)
        print(f"\n  history 데이터:")
        for i, row in enumerate(history_rows, 1):
            print(f"    {i}. plc_id={row['plc_id']}, tag_id={row['tag_id']}, "
                  f"v_bool={row['v_bool']}, ts={row['timestamp']}")

        # 첫 INSERT(FALSE), UPDATE→TRUE, UPDATE→FALSE 순서
        assert history_rows[0]['v_bool'] is False
        assert history_rows[1]['v_bool'] is True
        assert history_rows[2]['v_bool'] is False
        print(f"  [PASS] history 값 순서 확인 (FALSE → TRUE → FALSE)")

        # ---- 3n. snapshot extension 테스트 ----
        print(f"\n  --- Snapshot Extension 테스트 ---")

        snap_ext = ExtensionsConfig(
            snapshot=SnapshotExtConfig(triggers=[
                SnapshotTriggerConfig(watch_tag=1, capture_tags=[1, 2, 3]),
            ])
        )

        # log 그룹 생성 (snapshot 포함)
        log_stmts = get_group_init_sql(
            schema, "log", mode="latest",
            compression_after="1 day",
            extensions=snap_ext,
        )
        log_errors = []
        for sql in log_stmts:
            try:
                await conn.execute(sql.strip())
            except Exception as e:
                log_errors.append(str(e))
                print(f"    [WARN] {str(e)[:100]}")
        if log_errors:
            print(f"  [WARN] log 그룹 생성 시 {len(log_errors)} 경고")
        else:
            print(f"  [PASS] log 그룹 + snapshot extension 생성 ({len(log_stmts)} statements)")

        # log_snapshot 테이블 확인
        tables2 = await conn.fetch("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = $1 AND table_name = 'log_snapshot'
        """, schema)
        assert len(tables2) == 1, "log_snapshot 테이블 없음"
        print(f"  [PASS] log_snapshot 테이블 존재")

        # snapshot 트리거 확인
        snap_triggers = await conn.fetch("""
            SELECT trigger_name FROM information_schema.triggers
            WHERE trigger_schema = $1
              AND event_object_table = 'log_latest'
              AND trigger_name = 'trg_log_snapshot_update'
        """, schema)
        assert len(snap_triggers) == 1, "snapshot 트리거 없음"
        print(f"  [PASS] snapshot 트리거 존재")

        # snapshot 트리거 동작 테스트
        # 먼저 log_latest에 tag_id 1,2,3을 INSERT
        for tid in [1, 2, 3]:
            await conn.execute(f"""
                INSERT INTO {schema}.log_latest
                    (plc_id, tag_id, timestamp, v_bool, v_int, quality_code)
                VALUES (1, {tid}, NOW(), FALSE, {tid * 100}, 1)
            """)

        # tag_id=1의 v_bool을 TRUE로 변경 → rising edge → snapshot 캡처
        await conn.execute(f"""
            UPDATE {schema}.log_latest
            SET v_bool = TRUE, timestamp = NOW(), updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)

        snap_count = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.log_snapshot"
        )
        assert snap_count == 3, \
            f"snapshot 후 레코드 = {snap_count}, expected 3 (capture_tags=[1,2,3])"
        print(f"  [PASS] snapshot rising edge 캡처 (count={snap_count})")

        # 다시 TRUE → TRUE 업데이트 (rising edge 아님) → snapshot 안 됨
        await conn.execute(f"""
            UPDATE {schema}.log_latest
            SET v_bool = TRUE, timestamp = NOW(), updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        snap_count2 = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.log_snapshot"
        )
        assert snap_count2 == 3, \
            f"TRUE→TRUE 후 snapshot = {snap_count2}, expected 3 (변경 없어야 함)"
        print(f"  [PASS] TRUE→TRUE: snapshot 미발생 (count={snap_count2})")

        # FALSE로 바꾸고 다시 TRUE → 또 캡처
        await conn.execute(f"""
            UPDATE {schema}.log_latest
            SET v_bool = FALSE, timestamp = NOW(), updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        await conn.execute(f"""
            UPDATE {schema}.log_latest
            SET v_bool = TRUE, timestamp = NOW(), updated_at = NOW()
            WHERE plc_id = 1 AND tag_id = 1
        """)
        snap_count3 = await conn.fetchval(
            f"SELECT count(*) FROM {schema}.log_snapshot"
        )
        assert snap_count3 == 6, \
            f"두 번째 rising edge 후 snapshot = {snap_count3}, expected 6"
        print(f"  [PASS] 두 번째 rising edge 캡처 (count={snap_count3})")

        # snapshot 데이터 확인
        snap_rows = await conn.fetch(f"""
            SELECT plc_id, tag_id, v_bool, v_int, timestamp
            FROM {schema}.log_snapshot
            ORDER BY timestamp, tag_id
        """)
        print(f"\n  snapshot 데이터:")
        for i, row in enumerate(snap_rows, 1):
            print(f"    {i}. plc_id={row['plc_id']}, tag_id={row['tag_id']}, "
                  f"v_bool={row['v_bool']}, v_int={row['v_int']}")

        print(f"\n  === DB 실행 + 트리거 테스트 전체 PASS ===")

        # ---- 정리 ----
        if args.cleanup:
            await conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            print(f"\n  스키마 {schema} 정리 완료")

        return True

    except AssertionError as e:
        print(f"\n  [FAIL] {e}")
        return False
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await conn.close()


# ============================================================================
# Main
# ============================================================================

async def main():
    parser = argparse.ArgumentParser(description="Extensions 기능 테스트")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--database", default="neurosense")
    parser.add_argument("--user", default="user")
    parser.add_argument("--password", default="neuro0901")
    parser.add_argument("--schema", default="jem_ext_test")
    parser.add_argument("--no-db", action="store_true", help="DB 테스트 스킵")
    parser.add_argument("--cleanup", action="store_true", default=True,
                        help="테스트 후 스키마 삭제 (기본: True)")
    parser.add_argument("--no-cleanup", action="store_false", dest="cleanup",
                        help="테스트 후 스키마 유지")
    args = parser.parse_args()

    print("=" * 60)
    print("Extensions 기능 테스트")
    print("=" * 60)

    all_pass = True

    # 1. Config 파싱 테스트
    try:
        ext = test_config_parsing()
    except AssertionError as e:
        print(f"\n  [FAIL] Config 파싱: {e}")
        all_pass = False
        ext = None

    # 2. DDL 생성 테스트
    try:
        test_ddl_generation(ext)
    except AssertionError as e:
        print(f"\n  [FAIL] DDL 생성: {e}")
        all_pass = False

    # 3. DB 실행 + 트리거 테스트
    if not args.no_db:
        db_pass = await test_db_execution(args)
        if not db_pass:
            all_pass = False
    else:
        print("\n  [SKIP] DB 테스트 (--no-db)")

    # 결과
    print("\n" + "=" * 60)
    if all_pass:
        print("RESULT: ALL TESTS PASSED")
    else:
        print("RESULT: SOME TESTS FAILED")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
