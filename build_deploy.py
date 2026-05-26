"""
JEM 배포용 Docker 이미지 빌드 스크립트.

simpleCollector(MC Protocol) + collector-publisher를 ARM64로 빌드하고
deploy/jem/ 디렉토리에 tar 파일로 내보냅니다.

Usage:
    python build_deploy.py              # 전체 빌드 (collector + publisher)
    python build_deploy.py --collector   # collector만 빌드
    python build_deploy.py --publisher   # publisher만 빌드
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# 경로 설정
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent
COLLECTOR_DIR = SCRIPT_DIR  # simpleCollector 루트
PUBLISHER_DIR = SCRIPT_DIR.parent / "collector-publisher"
DEPLOY_DIR = SCRIPT_DIR / "deploy" / "jem"

# ============================================================================
# 빌드 설정
# ============================================================================
PLATFORM = "linux/arm64"


def _read_app_version(version_py: Path, default: str = "0.0.0") -> str:
    """src/version.py 의 APP_VERSION 상수 읽기."""
    try:
        text = version_py.read_text(encoding="utf-8")
        m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
        return m.group(1) if m else default
    except OSError:
        return default


def _git_sha(repo_dir: Path, length: int = 12) -> str:
    """현재 HEAD 의 짧은 sha (실패 시 빈 문자열)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return out.stdout.strip()[:length]
    except Exception:
        return ""


COLLECTOR_VERSION = _read_app_version(COLLECTOR_DIR / "src" / "version.py")
PUBLISHER_VERSION = _read_app_version(PUBLISHER_DIR / "src" / "version.py")
COLLECTOR_SHA = _git_sha(COLLECTOR_DIR)
PUBLISHER_SHA = _git_sha(PUBLISHER_DIR)


BUILDS = {
    "collector": {
        "context": COLLECTOR_DIR,
        "dockerfile": COLLECTOR_DIR / "build" / "collector" / "Dockerfile",
        # NCR 컨벤션: neuroforge/edge-collector-mc:v<ver> — 로컬 태그 base
        "image": f"edge-collector-mc:{COLLECTOR_VERSION}",
        "output": DEPLOY_DIR / "edge-collector-mc.tar",
        "build_args": {
            "PROTOCOL": "mc_protocol",
            "VERSION": COLLECTOR_VERSION,
            "GIT_SHA": COLLECTOR_SHA,
        },
    },
    "publisher": {
        "context": PUBLISHER_DIR,
        "dockerfile": PUBLISHER_DIR / "Dockerfile",
        "image": f"edge-publisher:{PUBLISHER_VERSION}",
        "output": DEPLOY_DIR / "edge-publisher.tar",
        "build_args": {
            "VERSION": PUBLISHER_VERSION,
            "GIT_SHA": PUBLISHER_SHA,
        },
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

    # 경로 검증
    if not context.exists():
        print(f"  [ERROR] Context 디렉토리 없음: {context}")
        return False
    if not dockerfile.exists():
        print(f"  [ERROR] Dockerfile 없음: {dockerfile}")
        return False

    # 빌드 커맨드 구성
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
    parser = argparse.ArgumentParser(description="JEM 배포용 Docker 이미지 빌드")
    parser.add_argument("--collector", action="store_true", help="collector만 빌드")
    parser.add_argument("--publisher", action="store_true", help="publisher만 빌드")
    args = parser.parse_args()

    # 둘 다 지정 안 하면 전체 빌드
    build_all = not args.collector and not args.publisher
    targets = []
    if build_all or args.collector:
        targets.append("collector")
    if build_all or args.publisher:
        targets.append("publisher")

    # deploy 디렉토리 확인
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)

    print(f"JEM 배포 빌드 시작 (platform: {PLATFORM})")
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
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
