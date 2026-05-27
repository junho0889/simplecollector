"""
JEM 배포용 Docker 이미지 빌드 스크립트.

simpleCollector(MC Protocol) + collector-publisher(+ BLE) 를 multi-arch
(amd64 + arm64) 로 빌드하여 NCR 에 곧바로 push 합니다.

기본 동작 (2026-05-27 변경):
    1) docker buildx build --platform linux/amd64,linux/arm64 ... --push
       → NCR (neuroforge-max-registry.kr.ncr.ntruss.com/neuroforge/) 에 multi-arch
         manifest list 로 직접 push. 로컬 daemon 에는 안 올라감 (multi-arch + --load 불가).
    2) v<APP_VERSION> + :latest 두 태그 모두 push (--load 흐름과 동일한 컨벤션).
    3) semver immutable GUARD — 같은 v<ver> 가 NCR 에 이미 있으면 ver 태그 push 자동 skip,
       :latest 만 갱신. 코드 변경했는데 GUARD 가 뜨면 APP_VERSION 범프 부터.

이전 단일 arch + 로컬 --load 흐름은 --legacy-load 로 유지.

Usage:
    python build_deploy.py              # 전체 빌드 + push (mc + ble + publisher)
    python build_deploy.py --mc          # MC collector 만
    python build_deploy.py --ble         # BLE collector 만
    python build_deploy.py --publisher   # publisher 만
    python build_deploy.py --legacy-load # 옛 흐름 (arm64 단일, 로컬 daemon 로드, push 안 함)
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
MULTIARCH_PLATFORMS = "linux/amd64,linux/arm64"
LEGACY_PLATFORM = "linux/arm64"

# NCR 컨벤션 — neuroforge-max-registry.kr.ncr.ntruss.com/neuroforge/<image>:v<ver>
NCR = "neuroforge-max-registry.kr.ncr.ntruss.com"
NCR_NS = "neuroforge"


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
    "mc": {
        "context": COLLECTOR_DIR,
        "dockerfile": COLLECTOR_DIR / "build" / "collector" / "Dockerfile",
        "ncr_name": "edge-collector-mc",
        "version": COLLECTOR_VERSION,
        "build_args": {
            "PROTOCOL": "mc_protocol",
            "VERSION": COLLECTOR_VERSION,
            "GIT_SHA": COLLECTOR_SHA,
        },
    },
    "ble": {
        "context": COLLECTOR_DIR,
        "dockerfile": COLLECTOR_DIR / "deploy" / "ble" / "Dockerfile.ble",
        "ncr_name": "edge-collector-ble",
        "version": COLLECTOR_VERSION,
        "build_args": {
            "VERSION": COLLECTOR_VERSION,
            "GIT_SHA": COLLECTOR_SHA,
        },
    },
    "publisher": {
        "context": PUBLISHER_DIR,
        "dockerfile": PUBLISHER_DIR / "Dockerfile",
        "ncr_name": "edge-publisher",
        "version": PUBLISHER_VERSION,
        "build_args": {
            "VERSION": PUBLISHER_VERSION,
            "GIT_SHA": PUBLISHER_SHA,
        },
    },
}


def run_cmd(cmd: list[str], desc: str, check_ok: bool = True) -> bool:
    """커맨드 실행 및 결과 출력."""
    print(f"\n{'='*60}")
    print(f"  {desc}")
    print(f"{'='*60}")
    print(f"  $ {' '.join(cmd)}\n")

    start = time.time()
    result = subprocess.run(cmd, capture_output=False)
    elapsed = time.time() - start

    ok = result.returncode == 0
    if ok:
        print(f"\n  [OK] {desc} ({elapsed:.1f}s)")
    elif check_ok:
        print(f"\n  [FAIL] {desc} (exit code {result.returncode})")
    return ok


def _ncr_ref(name: str, tag: str) -> str:
    return f"{NCR}/{NCR_NS}/{name}:{tag}"


def _ncr_has_tag(name: str, tag: str) -> bool:
    """NCR 에 해당 태그가 이미 있는지 검사 (immutable GUARD 용)."""
    ref = _ncr_ref(name, tag)
    out = subprocess.run(
        ["docker", "manifest", "inspect", ref],
        capture_output=True, text=True, check=False,
    )
    return out.returncode == 0 and bool(out.stdout.strip())


def build_image_multiarch(name: str, cfg: dict) -> bool:
    """multi-arch 빌드 + NCR push (v<ver> + :latest).

    GUARD: 같은 v<ver> 가 NCR 에 이미 있으면 ver 태그 push 는 skip (semver immutable),
    :latest 만 새로 push 한다. 코드 변경했는데 GUARD 가 뜨면 APP_VERSION 범프 필요.
    """
    context = cfg["context"]
    dockerfile = cfg["dockerfile"]
    ncr_name = cfg["ncr_name"]
    version = cfg["version"]
    build_args = cfg["build_args"]

    if not context.exists():
        print(f"  [ERROR] Context 디렉토리 없음: {context}")
        return False
    if not dockerfile.exists():
        print(f"  [ERROR] Dockerfile 없음: {dockerfile}")
        return False

    ver_tag = f"v{version}"
    ver_ref = _ncr_ref(ncr_name, ver_tag)
    latest_ref = _ncr_ref(ncr_name, "latest")

    # GUARD — 같은 ver 가 이미 NCR 에 있으면 ver 태그 skip
    ver_exists = _ncr_has_tag(ncr_name, ver_tag)
    if ver_exists:
        print(
            f"  [GUARD] {ver_ref} 가 이미 NCR 에 존재 — ver 태그는 skip, "
            f":latest 만 새로 push 합니다. (코드 변경했으면 APP_VERSION 범프부터)"
        )
        tag_args = ["-t", latest_ref]
    else:
        tag_args = ["-t", ver_ref, "-t", latest_ref]

    cmd = [
        "docker", "buildx", "build",
        "--platform", MULTIARCH_PLATFORMS,
        *tag_args,
        "-f", str(dockerfile),
    ]
    for key, val in build_args.items():
        cmd.extend(["--build-arg", f"{key}={val}"])
    # multi-arch 는 --load 불가 → --push 로 곧바로 NCR 에 manifest list push
    cmd.extend(["--push", str(context)])

    return run_cmd(cmd, f"{name} multi-arch 빌드 + NCR push ({MULTIARCH_PLATFORMS})")


def build_image_legacy(name: str, cfg: dict) -> bool:
    """옛 흐름: arm64 단일, --load 로 로컬 daemon 등록 (push 안 함)."""
    context = cfg["context"]
    dockerfile = cfg["dockerfile"]
    ncr_name = cfg["ncr_name"]
    version = cfg["version"]
    build_args = cfg["build_args"]

    if not context.exists() or not dockerfile.exists():
        print(f"  [ERROR] context/Dockerfile 누락")
        return False

    image = f"{ncr_name}:{version}"
    cmd = [
        "docker", "buildx", "build",
        "--platform", LEGACY_PLATFORM,
        "-t", image,
        "-f", str(dockerfile),
    ]
    for key, val in build_args.items():
        cmd.extend(["--build-arg", f"{key}={val}"])
    cmd.extend(["--load", str(context)])

    ok = run_cmd(cmd, f"{name} 빌드 (legacy, {LEGACY_PLATFORM})")
    if ok:
        print(f"  -> 로컬 이미지 등록: {image} (push 안 함, 별도 scripts/push-to-ncr.sh 필요)")
    return ok


def main():
    parser = argparse.ArgumentParser(description="JEM 배포용 Docker 이미지 빌드 (multi-arch + NCR push)")
    parser.add_argument("--mc", action="store_true", help="MC collector 만")
    parser.add_argument("--ble", action="store_true", help="BLE collector 만")
    parser.add_argument("--publisher", action="store_true", help="publisher 만")
    parser.add_argument("--collector", action="store_true",
                        help="(deprecated) --mc 의 별칭 — 호환용")
    parser.add_argument("--legacy-load", action="store_true",
                        help="옛 흐름: arm64 단일, 로컬 daemon --load (push 안 함, scripts/push-to-ncr.sh 필요)")
    args = parser.parse_args()

    # 타깃 결정
    explicit = args.mc or args.ble or args.publisher or args.collector
    targets = []
    if args.mc or args.collector:
        targets.append("mc")
    if args.ble:
        targets.append("ble")
    if args.publisher:
        targets.append("publisher")
    if not explicit:
        targets = ["mc", "ble", "publisher"]

    mode = "legacy-load (arm64 단일)" if args.legacy_load else f"multi-arch + NCR push ({MULTIARCH_PLATFORMS})"
    print(f"JEM 배포 빌드 시작 — mode: {mode}")
    print(f"대상: {', '.join(targets)}")

    results = {}
    total_start = time.time()
    for name in targets:
        cfg = BUILDS[name]
        if args.legacy_load:
            results[name] = build_image_legacy(name, cfg)
        else:
            results[name] = build_image_multiarch(name, cfg)

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"  빌드 결과 (총 {total_elapsed:.1f}s)")
    print(f"{'='*60}")
    for name, ok in results.items():
        status = "OK" if ok else "FAIL"
        cfg = BUILDS[name]
        if args.legacy_load:
            print(f"  [{status}] {name:10s} -> {cfg['ncr_name']}:{cfg['version']} (local daemon)")
        else:
            print(f"  [{status}] {name:10s} -> {NCR}/{NCR_NS}/{cfg['ncr_name']}:v{cfg['version']} (+ :latest)")

    if all(results.values()):
        if args.legacy_load:
            print("\n  다음 단계: bash scripts/push-to-ncr.sh   (NCR 배포)")
        else:
            print("\n  ✓ NCR push 완료. 운영 환경에서 docker pull 가능.")
        return 0
    else:
        return 1


if __name__ == "__main__":
    sys.exit(main())
