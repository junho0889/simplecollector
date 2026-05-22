"""
JEM+BLE 통합 배포 — Docker 이미지 빌드 + RPi 배포 스크립트.

3개 이미지를 ARM64로 빌드하여 tar로 내보내고, RPi에 배포합니다.
  - neuroforge_collector_mc:mc-latest    (MC Protocol 수집기)
  - neuroforge_collector_ble:latest      (BLE 수집기)
  - neuroforge_publisher:latest          (DB 저장)

Usage:
    python deploy/jem_ble/build_deploy.py                    # 전체 빌드
    python deploy/jem_ble/build_deploy.py --mc               # MC collector만
    python deploy/jem_ble/build_deploy.py --ble              # BLE collector만
    python deploy/jem_ble/build_deploy.py --publisher        # Publisher만
    python deploy/jem_ble/build_deploy.py --deploy           # 전체 빌드 + RPi 배포
    python deploy/jem_ble/build_deploy.py --deploy-only      # 배포만 (이미 빌드됨)
    python deploy/jem_ble/build_deploy.py --deploy --host pi@10.0.0.5  # 호스트 지정
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

# ============================================================================
# 경로 설정
# ============================================================================
SCRIPT_DIR = Path(__file__).resolve().parent          # deploy/jem_ble/
PROJECT_ROOT = SCRIPT_DIR.parent.parent               # simpleCollector/
PUBLISHER_DIR = PROJECT_ROOT.parent / "collector-publisher"

# ============================================================================
# 빌드 설정
# ============================================================================
PLATFORM = "linux/arm64"

BUILDS = {
    "mc": {
        "desc": "MC Protocol Collector",
        "context": PROJECT_ROOT,
        "dockerfile": PROJECT_ROOT / "build" / "collector" / "Dockerfile",
        "image": "neuroforge_collector_mc:mc-latest",
        "output": SCRIPT_DIR / "neuroforge_collector_mc.tar",
        "build_args": {"PROTOCOL": "mc_protocol"},
    },
    "ble": {
        "desc": "BLE Collector",
        "context": PROJECT_ROOT,
        "dockerfile": PROJECT_ROOT / "deploy" / "ble" / "Dockerfile.ble",
        "image": "neuroforge_collector_ble:latest",
        "output": SCRIPT_DIR / "neuroforge_collector_ble.tar",
        "build_args": {},
    },
    "publisher": {
        "desc": "Publisher",
        "context": PUBLISHER_DIR,
        "dockerfile": PUBLISHER_DIR / "Dockerfile",
        "image": "neuroforge_publisher:latest",
        "output": SCRIPT_DIR / "neuroforge_publisher.tar",
        "build_args": {},
    },
}

# ============================================================================
# 배포 설정
# ============================================================================
DEFAULT_PI_HOST = "pi@192.168.0.14"
DEFAULT_SSH_KEY = Path.home() / ".ssh" / "id_ed25519"
PI_DEPLOY_DIR = "/home/pi/deploy/jem_ble"

# 배포할 설정 파일 (이미지 tar 제외)
CONFIG_GLOBS = [
    "docker-compose.yml",
    "collector_*.yaml",
    "publisher_*.yaml",
    "tags_*.csv",
    "custom_init.sql",
    "backfill_statistics.sql",
]


def run_cmd(cmd: list[str], desc: str, check: bool = True) -> bool:
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
        return not check


def build_image(name: str, cfg: dict) -> bool:
    """Docker 이미지 빌드 + tar 내보내기."""
    context = cfg["context"]
    dockerfile = cfg["dockerfile"]

    if not context.exists():
        print(f"  [ERROR] Context 디렉토리 없음: {context}")
        return False
    if not dockerfile.exists():
        print(f"  [ERROR] Dockerfile 없음: {dockerfile}")
        return False

    cmd = [
        "docker", "buildx", "build",
        "--platform", PLATFORM,
        "-t", cfg["image"],
        "-f", str(dockerfile),
    ]
    for key, val in cfg["build_args"].items():
        cmd.extend(["--build-arg", f"{key}={val}"])
    cmd.extend([
        "--output", f"type=docker,dest={cfg['output']}",
        str(context),
    ])

    success = run_cmd(cmd, f"{cfg['desc']} 빌드 ({PLATFORM})")
    if success and cfg["output"].exists():
        size_mb = cfg["output"].stat().st_size / (1024 * 1024)
        print(f"  -> {cfg['output'].name} ({size_mb:.1f} MB)")
    return success


def collect_deploy_files(targets: list[str]) -> list[Path]:
    """배포할 파일 목록 수집."""
    files = []

    # tar 이미지
    for name in targets:
        tar = BUILDS[name]["output"]
        if tar.exists():
            files.append(tar)
        else:
            print(f"  [WARN] {tar.name} 없음 — 빌드 먼저 실행하세요")

    # 설정 파일
    for pattern in CONFIG_GLOBS:
        for f in sorted(SCRIPT_DIR.glob(pattern)):
            if f not in files:
                files.append(f)

    return files


def deploy_to_pi(targets: list[str], host: str, ssh_key: Path) -> bool:
    """RPi로 파일 전송 + Docker 이미지 로드 + 서비스 시작."""
    ssh_base = ["ssh", "-i", str(ssh_key), "-o", "StrictHostKeyChecking=no"]
    scp_base = ["scp", "-i", str(ssh_key), "-o", "StrictHostKeyChecking=no"]

    print(f"\n{'='*60}")
    print(f"  RPi 배포: {host}:{PI_DEPLOY_DIR}")
    print(f"  SSH key:  {ssh_key}")
    print(f"{'='*60}")

    # 1. 원격 디렉토리 생성
    if not run_cmd(
        ssh_base + [host, f"mkdir -p {PI_DEPLOY_DIR}"],
        "원격 디렉토리 생성"
    ):
        return False

    # 2. 파일 수집 및 전송
    files = collect_deploy_files(targets)
    if not files:
        print("  [ERROR] 전송할 파일 없음")
        return False

    tar_files = [f for f in files if f.suffix == ".tar"]
    config_files = [f for f in files if f.suffix != ".tar"]

    # 설정 파일 먼저 (가벼움)
    if config_files:
        if not run_cmd(
            scp_base + [str(f) for f in config_files] + [f"{host}:{PI_DEPLOY_DIR}/"],
            f"설정 파일 전송 ({len(config_files)}개)"
        ):
            return False

    # tar 이미지 (무거움)
    for tar in tar_files:
        size_mb = tar.stat().st_size / (1024 * 1024)
        if not run_cmd(
            scp_base + [str(tar), f"{host}:{PI_DEPLOY_DIR}/"],
            f"{tar.name} 전송 ({size_mb:.0f} MB)"
        ):
            return False

    # 3. Docker 이미지 로드
    load_cmds = []
    for name in targets:
        tar = BUILDS[name]["output"]
        if tar.exists():
            load_cmds.append(f"docker load -i {PI_DEPLOY_DIR}/{tar.name}")

    if load_cmds:
        if not run_cmd(
            ssh_base + [host, " && ".join(load_cmds)],
            f"Docker 이미지 로드 ({len(load_cmds)}개)"
        ):
            return False

    # 4. 서비스 재시작
    if not run_cmd(
        ssh_base + [host,
                    f"cd {PI_DEPLOY_DIR} && docker compose down && docker compose up -d"],
        "서비스 시작 (docker compose up -d)"
    ):
        return False

    # 5. 상태 확인
    run_cmd(
        ssh_base + [host, f"cd {PI_DEPLOY_DIR} && docker compose ps"],
        "서비스 상태 확인",
        check=False,
    )

    return True


def main():
    parser = argparse.ArgumentParser(
        description="JEM+BLE 통합 배포 — 빌드/배포",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--mc", action="store_true", help="MC collector 빌드")
    parser.add_argument("--ble", action="store_true", help="BLE collector 빌드")
    parser.add_argument("--publisher", action="store_true", help="Publisher 빌드")
    parser.add_argument("--deploy", action="store_true", help="빌드 후 RPi 배포")
    parser.add_argument("--deploy-only", action="store_true", help="배포만 (빌드 없이)")
    parser.add_argument("--host", default=DEFAULT_PI_HOST,
                        help=f"배포 대상 (default: {DEFAULT_PI_HOST})")
    parser.add_argument("--ssh-key", type=Path, default=DEFAULT_SSH_KEY,
                        help=f"SSH 키 경로 (default: {DEFAULT_SSH_KEY})")
    args = parser.parse_args()

    # 대상 결정
    explicit = args.mc or args.ble or args.publisher
    targets = []
    if not explicit:
        targets = ["mc", "ble", "publisher"]  # 전체
    else:
        if args.mc:
            targets.append("mc")
        if args.ble:
            targets.append("ble")
        if args.publisher:
            targets.append("publisher")

    # 빌드
    if not args.deploy_only:
        print(f"JEM+BLE 배포 빌드 시작 (platform: {PLATFORM})")
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
                print(f"  [{status}] {BUILDS[name]['desc']:25s} -> {output.name} ({size_mb:.1f} MB)")
            else:
                print(f"  [{status}] {BUILDS[name]['desc']}")

        if not all(results.values()):
            print("\n  [WARN] 일부 빌드 실패")
            if not args.deploy:
                return 1

    # 배포
    if args.deploy or args.deploy_only:
        if not deploy_to_pi(targets, args.host, args.ssh_key):
            return 1
        print(f"\n  배포 완료: {args.host}:{PI_DEPLOY_DIR}")

    if not args.deploy and not args.deploy_only:
        print(f"\n  tar 파일 위치: {SCRIPT_DIR}")
        print(f"  배포하려면: python {Path(__file__).name} --deploy")

    return 0


if __name__ == "__main__":
    sys.exit(main())
