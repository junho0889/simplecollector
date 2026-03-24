"""
BLE 배포용 Docker 이미지 빌드 + RPi 배포 스크립트.

Usage:
    python deploy/ble/build_deploy_ble.py                  # 빌드만
    python deploy/ble/build_deploy_ble.py --deploy          # 빌드 + 배포
    python deploy/ble/build_deploy_ble.py --deploy-only     # 배포만 (이미 빌드됨)
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# 경로 설정
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent          # deploy/ble/
PROJECT_ROOT = SCRIPT_DIR.parent.parent               # simpleCollector/
PUBLISHER_DIR = PROJECT_ROOT.parent / "collector-publisher"

# ============================================================================
# 빌드 설정
# ============================================================================
PLATFORM = "linux/arm64"
IMAGE_NAME = "neuro_collector_ble:latest"
PUBLISHER_IMAGE = "neuro_publisher:latest"
OUTPUT_TAR = SCRIPT_DIR / "neuro_collector_ble.tar"
PUBLISHER_TAR = SCRIPT_DIR / "neuro_publisher.tar"

# ============================================================================
# 배포 설정
# ============================================================================
PI_HOST = "pi@192.168.0.14"
PI_DEPLOY_DIR = "/home/pi/deploy/ble"

# 배포할 파일 목록
DEPLOY_FILES = [
    # Docker images
    OUTPUT_TAR,
    PUBLISHER_TAR,
    # Configs — 멀티디바이스 통합 구조
    SCRIPT_DIR / "docker-compose.yml",
    SCRIPT_DIR / "collector_ble_multi.yaml",
    SCRIPT_DIR / "tags_ble_multi.csv",
    SCRIPT_DIR / "publisher_ble.yaml",
    SCRIPT_DIR / "mosquitto.conf",
]


def run_cmd(cmd: list[str], desc: str) -> bool:
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    print(f"  $ {' '.join(cmd)}\n")

    start = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - start

    if result.returncode == 0:
        print(f"\n  [OK] {desc} ({elapsed:.1f}s)")
        return True
    else:
        print(f"\n  [FAIL] {desc} (exit code {result.returncode})")
        return False


def build_ble_collector() -> bool:
    """BLE collector Docker 이미지 빌드."""
    cmd = [
        "docker", "buildx", "build",
        "--platform", PLATFORM,
        "-t", IMAGE_NAME,
        "-f", str(SCRIPT_DIR / "Dockerfile.ble"),
        "--output", f"type=docker,dest={OUTPUT_TAR}",
        str(PROJECT_ROOT),
    ]
    success = run_cmd(cmd, f"BLE Collector 빌드 ({PLATFORM})")
    if success and OUTPUT_TAR.exists():
        size_mb = OUTPUT_TAR.stat().st_size / (1024 * 1024)
        print(f"  -> {OUTPUT_TAR.name} ({size_mb:.1f} MB)")
    return success


def build_publisher() -> bool:
    """Publisher Docker 이미지 빌드."""
    if not PUBLISHER_DIR.exists():
        print(f"  [SKIP] collector-publisher 디렉토리 없음: {PUBLISHER_DIR}")
        return False

    cmd = [
        "docker", "buildx", "build",
        "--platform", PLATFORM,
        "-t", PUBLISHER_IMAGE,
        "-f", str(PUBLISHER_DIR / "Dockerfile"),
        "--output", f"type=docker,dest={PUBLISHER_TAR}",
        str(PUBLISHER_DIR),
    ]
    success = run_cmd(cmd, f"Publisher 빌드 ({PLATFORM})")
    if success and PUBLISHER_TAR.exists():
        size_mb = PUBLISHER_TAR.stat().st_size / (1024 * 1024)
        print(f"  -> {PUBLISHER_TAR.name} ({size_mb:.1f} MB)")
    return success


def deploy_to_pi() -> bool:
    """RPi로 파일 전송 + Docker 이미지 로드 + 서비스 시작."""
    print(f"\n{'='*60}")
    print(f"  RPi 배포: {PI_HOST}:{PI_DEPLOY_DIR}")
    print(f"{'='*60}")

    # 1. 원격 디렉토리 생성
    if not run_cmd(
        ["ssh", PI_HOST, f"mkdir -p {PI_DEPLOY_DIR}"],
        "원격 디렉토리 생성"
    ):
        return False

    # 2. 파일 전송
    files_to_send = [str(f) for f in DEPLOY_FILES if f.exists()]
    if not files_to_send:
        print("  [ERROR] 전송할 파일 없음")
        return False

    if not run_cmd(
        ["scp"] + files_to_send + [f"{PI_HOST}:{PI_DEPLOY_DIR}/"],
        f"파일 전송 ({len(files_to_send)}개)"
    ):
        return False

    # 3. Docker 이미지 로드
    remote_cmds = []
    if OUTPUT_TAR.exists():
        remote_cmds.append(
            f"docker load -i {PI_DEPLOY_DIR}/neuro_collector_ble.tar"
        )
    if PUBLISHER_TAR.exists():
        remote_cmds.append(
            f"docker load -i {PI_DEPLOY_DIR}/neuro_publisher.tar"
        )

    if remote_cmds:
        if not run_cmd(
            ["ssh", PI_HOST, " && ".join(remote_cmds)],
            "Docker 이미지 로드"
        ):
            return False

    # 4. 서비스 시작
    if not run_cmd(
        ["ssh", PI_HOST,
         f"cd {PI_DEPLOY_DIR} && docker compose down && docker compose up -d"],
        "서비스 시작"
    ):
        return False

    # 5. 상태 확인
    run_cmd(
        ["ssh", PI_HOST, f"cd {PI_DEPLOY_DIR} && docker compose ps"],
        "서비스 상태"
    )

    print(f"\n  Node-RED: http://192.168.0.14:1882")
    print(f"  RabbitMQ: http://192.168.0.14:15682 (admin/admin)")
    return True


def main():
    parser = argparse.ArgumentParser(description="BLE 배포용 빌드/배포")
    parser.add_argument("--deploy", action="store_true",
                        help="빌드 후 RPi 배포")
    parser.add_argument("--deploy-only", action="store_true",
                        help="배포만 (빌드 없이)")
    parser.add_argument("--no-publisher", action="store_true",
                        help="publisher 빌드 제외")
    args = parser.parse_args()

    if not args.deploy_only:
        print("BLE 배포 빌드 시작")
        if not build_ble_collector():
            return 1
        if not args.no_publisher:
            build_publisher()  # publisher 실패해도 계속

    if args.deploy or args.deploy_only:
        if not deploy_to_pi():
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
