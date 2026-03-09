"""
LoRa Gateway 배포용 Docker 이미지 빌드 스크립트.

simpleCollector(LoRa) + collector-publisher를 ARM64로 빌드하고
deploy/lora/ 디렉토리에 tar 파일로 내보냅니다.

Usage:
    python deploy/lora/build_deploy_lora.py              # 전체 빌드
    python deploy/lora/build_deploy_lora.py --collector   # collector만
    python deploy/lora/build_deploy_lora.py --publisher   # publisher만
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# 경로 설정
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
COLLECTOR_DIR = SCRIPT_DIR.parent.parent          # simpleCollector 루트
PUBLISHER_DIR = COLLECTOR_DIR.parent / "collector-publisher"
DEPLOY_DIR = SCRIPT_DIR                           # deploy/lora/

# ============================================================================
# 빌드 설정
# ============================================================================
PLATFORM = "linux/arm64"

BUILDS = {
    "collector": {
        "context": COLLECTOR_DIR,
        "dockerfile": COLLECTOR_DIR / "build" / "collector" / "Dockerfile",
        "image": "neuro_collector_lora:latest",
        "output": DEPLOY_DIR / "neuro_collector_lora.tar",
        "build_args": {"PROTOCOL": "lora_rak5146"},
    },
    "publisher": {
        "context": PUBLISHER_DIR,
        "dockerfile": PUBLISHER_DIR / "Dockerfile",
        "image": "neuro_publisher:latest",
        "output": DEPLOY_DIR / "neuro_publisher.tar",
        "build_args": {},
    },
}


def run_cmd(cmd: list[str], desc: str) -> bool:
    """커맨드 실행 및 결과 출력."""
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


def build_image(name: str, cfg: dict) -> bool:
    """Docker 이미지 빌드 + tar 내보내기."""
    context = cfg["context"]
    dockerfile = cfg["dockerfile"]
    image = cfg["image"]
    output = cfg["output"]
    build_args = cfg["build_args"]

    if not context.exists():
        print(f"  [ERROR] Context 디렉토리 없음: {context}")
        return False
    if not dockerfile.exists():
        print(f"  [ERROR] Dockerfile 없음: {dockerfile}")
        return False

    cmd = [
        "docker", "buildx", "build",
        "--platform", PLATFORM,
        "-t", image,
        "-f", str(dockerfile),
    ]

    for key, val in build_args.items():
        cmd.extend(["--build-arg", f"{key}={val}"])

    cmd.extend([
        "--output", f"type=docker,dest={output}",
        str(context),
    ])

    success = run_cmd(cmd, f"{name} 빌드 ({PLATFORM})")

    if success and output.exists():
        size_mb = output.stat().st_size / (1024 * 1024)
        print(f"  -> {output.name} ({size_mb:.1f} MB)")

    return success


def main():
    parser = argparse.ArgumentParser(description="LoRa Gateway 배포용 Docker 이미지 빌드")
    parser.add_argument("--collector", action="store_true", help="collector만 빌드")
    parser.add_argument("--publisher", action="store_true", help="publisher만 빌드")
    args = parser.parse_args()

    build_all = not args.collector and not args.publisher
    targets = []
    if build_all or args.collector:
        targets.append("collector")
    if build_all or args.publisher:
        targets.append("publisher")

    print(f"LoRa Gateway 배포 빌드 시작 (platform: {PLATFORM})")
    print(f"대상: {', '.join(targets)}")

    results = {}
    total_start = time.time()

    for name in targets:
        results[name] = build_image(name, BUILDS[name])

    total_elapsed = time.time() - total_start

    # 결과 요약
    print(f"\n{'='*60}")
    print(f"  빌드 결과 (총 {total_elapsed:.1f}s)")
    print(f"{'='*60}")
    for name, success in results.items():
        status = "OK" if success else "FAIL"
        output = BUILDS[name]["output"]
        if success and output.exists():
            size_mb = output.stat().st_size / (1024 * 1024)
            print(f"  [{status}] {name:12s} -> {output.name} ({size_mb:.1f} MB)")
        else:
            print(f"  [{status}] {name:12s}")

    if all(results.values()):
        print(f"\n  배포 파일 위치: {DEPLOY_DIR}")
        print(f"\n  게이트웨이 배포:")
        print(f"    scp deploy/lora/*.tar user@gateway:/path/")
        print(f"    scp deploy/lora/docker-compose.yml deploy/lora/*.yaml deploy/lora/*.csv deploy/lora/mosquitto.conf user@gateway:/path/")
        print(f"    # 게이트웨이에서:")
        print(f"    docker load -i neuro_collector_lora.tar")
        print(f"    docker load -i neuro_publisher.tar")
        print(f"    docker-compose up -d")
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
