"""
장애 복원력 테스트 러너
========================
시나리오별 N회 반복 실행 → 결과 집계 → 리포트 출력.

Usage:
    cd D:/4.source/simpleCollector

    # 전체 시나리오 100회 반복
    python -m tests.resilience.test_runner --iterations 100

    # 특정 시나리오만
    python -m tests.resilience.test_runner --scenarios db_restart_short,rmq_restart --iterations 10

    # 시나리오 목록 확인
    python -m tests.resilience.test_runner --list

Note:
    publisher는 별도 터미널에서 직접 실행해야 합니다:
    cd D:/4.source/collector-publisher
    python -m src.main -c D:/4.source/simpleCollector/tests/resilience/config/publisher_test.yaml
"""

import argparse
import asyncio
import json
import logging
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from .docker_control import DockerControl
from .message_producer import TestMessageProducer
from .db_verifier import DBVerifier
from .scenarios import ALL_SCENARIOS, ScenarioResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("resilience-test")

RESULTS_DIR = Path(__file__).parent / "results"


def generate_report(all_results: Dict[str, List[ScenarioResult]]) -> str:
    """테스트 결과 리포트 생성."""
    lines = []
    lines.append("=" * 72)
    lines.append("RESILIENCE TEST REPORT")
    lines.append(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 72)
    lines.append("")

    total_pass = 0
    total_count = 0

    for scenario_name, results in all_results.items():
        n = len(results)
        passed = sum(1 for r in results if r.passed)
        failed = n - passed
        total_pass += passed
        total_count += n

        lines.append(f"Scenario: {scenario_name}")
        if results and results[0].details == "" and not any(r.error for r in results):
            pass  # skip description for clean results

        lines.append(f"  Iterations: {passed}/{n} passed ({passed * 100 / n:.1f}%)")

        if passed > 0:
            durations = [r.duration_seconds for r in results if r.passed]
            recoveries = [r.recovery_seconds for r in results if r.passed and r.recovery_seconds > 0]

            lines.append(
                f"  Duration:   avg={statistics.mean(durations):.1f}s  "
                f"p95={sorted(durations)[int(len(durations) * 0.95)]:.1f}s  "
                f"max={max(durations):.1f}s"
            )

            if recoveries:
                lines.append(
                    f"  Recovery:   avg={statistics.mean(recoveries):.1f}s  "
                    f"p95={sorted(recoveries)[int(len(recoveries) * 0.95)]:.1f}s  "
                    f"max={max(recoveries):.1f}s"
                )

        data_losses = [r for r in results if r.data_loss > 0]
        lines.append(
            f"  Data Loss:  {len(data_losses)}/{n} iterations "
            f"({sum(r.data_loss for r in results)} records total)"
        )

        if failed > 0:
            lines.append(f"  Failures:")
            for r in results:
                if not r.passed:
                    err = r.error or f"data_loss={r.data_loss}"
                    lines.append(f"    - Iteration {r.iteration}: {err}")

        lines.append("")

    lines.append("-" * 72)
    pct = total_pass * 100 / total_count if total_count > 0 else 0
    lines.append(f"OVERALL: {total_pass}/{total_count} passed ({pct:.1f}%)")
    lines.append("=" * 72)

    return "\n".join(lines)


async def run_tests(
    scenario_names: List[str],
    iterations: int,
    early_stop: int = 5,
) -> Dict[str, List[ScenarioResult]]:
    """테스트 실행."""

    docker = DockerControl()
    producer = TestMessageProducer()
    verifier = DBVerifier()

    all_results: Dict[str, List[ScenarioResult]] = {}

    try:
        # Stack 시작
        await docker.start_stack()
        await asyncio.sleep(5)  # 안정화 대기

        # Producer + Verifier 연결
        await producer.connect()
        await verifier.connect()

        for scenario_name in scenario_names:
            scenario_cls = ALL_SCENARIOS.get(scenario_name)
            if not scenario_cls:
                logger.error(f"Unknown scenario: {scenario_name}")
                continue

            scenario = scenario_cls(docker, producer, verifier)
            results: List[ScenarioResult] = []
            consecutive_failures = 0

            logger.info(f"\n{'=' * 60}")
            logger.info(f"Scenario: {scenario_name} ({scenario.description})")
            logger.info(f"Iterations: {iterations}")
            logger.info(f"{'=' * 60}")

            for i in range(1, iterations + 1):
                logger.info(f"  [{i}/{iterations}] Running...")

                # 스택 상태 보장 (이전 시나리오에서 컨테이너 꺼졌을 수 있음)
                if not await docker.is_running("resilience-db"):
                    await docker.start_container("resilience-db")
                    await docker.wait_healthy("resilience-db", timeout=60)
                if not await docker.is_running("resilience-rmq"):
                    await docker.start_container("resilience-rmq")
                    await docker.wait_healthy("resilience-rmq", timeout=60)
                    # Producer 재연결
                    try:
                        await producer.disconnect()
                    except Exception:
                        pass
                    await producer.connect()

                # Verifier 재연결 (DB 재시작 후 pool이 끊겼을 수 있음)
                try:
                    await verifier.disconnect()
                except Exception:
                    pass
                await verifier.connect()

                result = await scenario.run(i)
                results.append(result)

                status = "PASS" if result.passed else "FAIL"
                extra = ""
                if result.error:
                    extra = f" ({result.error})"
                elif result.data_loss > 0:
                    extra = f" (loss={result.data_loss})"

                logger.info(
                    f"  [{i}/{iterations}] {status} "
                    f"({result.duration_seconds:.1f}s, "
                    f"recovery={result.recovery_seconds:.1f}s){extra}"
                )

                if result.passed:
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= early_stop:
                        logger.error(
                            f"  Early stop: {early_stop} consecutive failures"
                        )
                        break

            all_results[scenario_name] = results

    finally:
        # 정리
        try:
            await producer.disconnect()
        except Exception:
            pass
        try:
            await verifier.disconnect()
        except Exception:
            pass
        # Stack은 유지 (수동 정리: docker compose down)

    return all_results


def main():
    parser = argparse.ArgumentParser(
        description="Publisher Resilience Test Runner"
    )
    parser.add_argument(
        "--scenarios",
        default="all",
        help="콤마 구분 시나리오 이름 (기본: all)",
    )
    parser.add_argument(
        "--iterations", "-n",
        type=int,
        default=100,
        help="시나리오당 반복 횟수 (기본: 100)",
    )
    parser.add_argument(
        "--early-stop",
        type=int,
        default=5,
        help="연속 실패 시 조기 중단 횟수 (기본: 5)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="사용 가능한 시나리오 목록 출력",
    )
    args = parser.parse_args()

    if args.list:
        print("\nAvailable scenarios:")
        for name, cls in ALL_SCENARIOS.items():
            print(f"  {name:30s} {cls.description}")
        return

    if args.scenarios == "all":
        scenario_names = list(ALL_SCENARIOS.keys())
    else:
        scenario_names = [s.strip() for s in args.scenarios.split(",")]

    # 실행
    all_results = asyncio.run(
        run_tests(scenario_names, args.iterations, args.early_stop)
    )

    # 리포트 생성
    report = generate_report(all_results)
    print("\n" + report)

    # 파일 저장
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = RESULTS_DIR / f"resilience_report_{timestamp}.txt"
    report_path.write_text(report, encoding="utf-8")
    logger.info(f"Report saved: {report_path}")

    # JSON 저장 (상세 데이터)
    json_path = RESULTS_DIR / f"resilience_data_{timestamp}.json"
    json_data = {}
    for name, results in all_results.items():
        json_data[name] = [
            {
                "iteration": r.iteration,
                "passed": r.passed,
                "duration": round(r.duration_seconds, 2),
                "recovery": round(r.recovery_seconds, 2),
                "produced": r.messages_produced,
                "in_db": r.messages_in_db,
                "data_loss": r.data_loss,
                "error": r.error,
            }
            for r in results
        ]
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
    logger.info(f"Data saved: {json_path}")


if __name__ == "__main__":
    main()
