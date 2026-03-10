"""
JEM 환경 DB 장애 복원력 테스트
===============================
1. 정상 데이터 주입 (10초)
2. DB stop → 데이터 계속 주입 (15초)
3. DB start → 복구 대기
4. 추가 데이터 주입 (5초)
5. 데이터 정합성 검증 (produced == DB rows)

Usage:
    cd D:/4.source/simpleCollector

    # Publisher 먼저 실행 (별도 터미널):
    cd d:\4.source\collector-publisher
    python -m src.main -c D:/4.source/simpleCollector/tests/jem_test/infra/publisher_local.yaml

    # 테스트 실행:
    python tests/jem_test/test_db_resilience.py
"""

import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from tests.jem_test.mock_producer import MockProducer

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("db-resilience-test")

DB_CONTAINER = "jem-local-db"
DB_DSN = "postgresql://user:neuro0901@localhost:55432/neurosense"
SCHEMA = "jem_test"


def docker_cmd(action: str, container: str) -> bool:
    """Docker 컨테이너 제어."""
    result = subprocess.run(
        ["docker", action, container],
        capture_output=True, text=True, timeout=30,
    )
    return result.returncode == 0


async def wait_db_healthy(timeout: int = 60) -> bool:
    """DB healthy 대기."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Health.Status}}", DB_CONTAINER],
            capture_output=True, text=True, timeout=10,
        )
        if result.stdout.strip() == "healthy":
            return True
        await asyncio.sleep(2)
    return False


async def get_db_count(table: str) -> int:
    """DB 테이블 row count."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        row = await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.{table}")
        return row
    finally:
        await conn.close()


async def get_all_counts() -> dict:
    """모든 주요 테이블 카운트."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        counts = {}
        for tbl in ["plc_data_integrated", "plc_data_latest", "alm_latest", "alm_history"]:
            counts[tbl] = await conn.fetchval(f"SELECT count(*) FROM {SCHEMA}.{tbl}")
        return counts
    finally:
        await conn.close()


async def truncate_all():
    """테스트 데이터 초기화."""
    conn = await asyncpg.connect(DB_DSN)
    try:
        await conn.execute(f"""
            TRUNCATE {SCHEMA}.plc_data_integrated, {SCHEMA}.plc_data_latest,
                     {SCHEMA}.alm_latest, {SCHEMA}.alm_history CASCADE
        """)
    finally:
        await conn.close()


async def run_test():
    """DB 장애 복원력 테스트."""
    producer = MockProducer(port=36672)
    producer.load_all_tags()

    logger.info("=" * 60)
    logger.info("DB 장애 복원력 테스트 시작")
    logger.info("=" * 60)

    # 0. 초기화
    await truncate_all()
    await producer.connect()
    producer.stats = {"batches": 0, "records": 0}

    plc_data_tags = 0
    for plc_id, tags in producer._tags.items():
        plc_data_tags += sum(1 for t in tags if t["collection_group"] == "plc_data")

    logger.info(f"plc_data 태그 수: {plc_data_tags} (이것이 매 사이클 integrated에 INSERT)")

    # 1. 정상 데이터 주입 (10초)
    logger.info("\n[Phase 1] 정상 데이터 주입 (10초)")
    phase1_start = producer.stats["records"]
    await producer.run_continuous(interval=1.0, duration=10.0)
    phase1_records = producer.stats["records"] - phase1_start

    await asyncio.sleep(3)  # flush 대기
    counts_after_p1 = await get_all_counts()
    logger.info(f"Phase 1 완료: 전송={phase1_records}, DB={counts_after_p1}")

    # 2. DB stop
    logger.info("\n[Phase 2] DB 중지 → 장애 중 데이터 주입 (15초)")
    docker_cmd("stop", DB_CONTAINER)
    logger.info("DB stopped")

    phase2_start = producer.stats["records"]
    await producer.run_continuous(interval=1.0, duration=15.0)
    phase2_records = producer.stats["records"] - phase2_start
    logger.info(f"장애 중 전송: {phase2_records} records")

    # 3. DB start + 복구 대기
    logger.info("\n[Phase 3] DB 복구")
    t_recovery = time.monotonic()
    docker_cmd("start", DB_CONTAINER)
    healthy = await wait_db_healthy(timeout=60)
    recovery_time = time.monotonic() - t_recovery

    if not healthy:
        logger.error("DB가 healthy 상태가 되지 않았습니다!")
        await producer.disconnect()
        return

    logger.info(f"DB healthy (복구 시간: {recovery_time:.1f}s)")

    # 4. 복구 후 추가 데이터 (5초)
    logger.info("\n[Phase 4] 복구 후 추가 데이터 주입 (5초)")
    phase4_start = producer.stats["records"]
    await producer.run_continuous(interval=1.0, duration=5.0)
    phase4_records = producer.stats["records"] - phase4_start

    total_produced = producer.stats["records"]
    total_plc_data_expected = (total_produced // 1812) * plc_data_tags  # 대략

    # 5. 검증 (publisher가 큐의 모든 메시지를 처리할 때까지 대기)
    logger.info("\n[Phase 5] 데이터 정합성 검증")
    logger.info(f"총 전송: {total_produced} records")
    logger.info("Publisher가 모든 큐 메시지를 처리할 때까지 대기...")

    # 최대 120초 대기, 5초마다 체크
    last_count = 0
    stable_count = 0
    for i in range(24):
        await asyncio.sleep(5)
        try:
            counts = await get_all_counts()
            current = counts["plc_data_integrated"]
            logger.info(
                f"  [{(i+1)*5}s] integrated={current}, "
                f"latest={counts['plc_data_latest']}, "
                f"alm_latest={counts['alm_latest']}, "
                f"alm_history={counts['alm_history']}"
            )
            if current == last_count and current > 0:
                stable_count += 1
                if stable_count >= 3:  # 15초간 변화 없으면 완료로 판단
                    break
            else:
                stable_count = 0
            last_count = current
        except Exception as e:
            logger.warning(f"  DB 조회 실패 (복구 중): {e}")
            stable_count = 0

    # 최종 결과
    final_counts = await get_all_counts()
    await producer.disconnect()

    # 계산
    total_cycles = producer.stats["batches"] // 20  # 10 PLCs × 2 groups = 20 batches/cycle
    expected_integrated = total_cycles * plc_data_tags

    logger.info("\n" + "=" * 60)
    logger.info("결과")
    logger.info("=" * 60)
    logger.info(f"총 사이클: {total_cycles}")
    logger.info(f"총 전송 레코드: {total_produced}")
    logger.info(f"")
    logger.info(f"plc_data_integrated: {final_counts['plc_data_integrated']} (예상: {expected_integrated})")
    logger.info(f"plc_data_latest:     {final_counts['plc_data_latest']} (예상: {plc_data_tags})")
    logger.info(f"alm_latest:          {final_counts['alm_latest']}")
    logger.info(f"alm_history:         {final_counts['alm_history']}")
    logger.info(f"")
    logger.info(f"DB 복구 시간:        {recovery_time:.1f}s")

    integrated_match = final_counts["plc_data_integrated"] == expected_integrated
    latest_match = final_counts["plc_data_latest"] == plc_data_tags

    data_loss = expected_integrated - final_counts["plc_data_integrated"]

    if integrated_match and latest_match:
        logger.info(f"\n✓ PASS — 데이터 무손실 (integrated={expected_integrated}, latest={plc_data_tags})")
    else:
        logger.error(f"\n✗ FAIL — 데이터 불일치!")
        if not integrated_match:
            logger.error(f"  integrated: 예상 {expected_integrated}, 실제 {final_counts['plc_data_integrated']} (차이: {data_loss})")
        if not latest_match:
            logger.error(f"  latest: 예상 {plc_data_tags}, 실제 {final_counts['plc_data_latest']}")

    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_test())
