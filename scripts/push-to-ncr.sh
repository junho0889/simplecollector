#!/usr/bin/env bash
# ============================================================================
# push-to-ncr.sh
# ----------------------------------------------------------------------------
# 로컬 docker 이미지를 NCR(neuroforge-max-registry.kr.ncr.ntruss.com) 로 push.
# 우리 3개 (edge-collector-mc / edge-collector-ble / edge-publisher) 전용.
#
# NCR 컨벤션 (Cortex 최종 확정, 2026-05-26):
#   <NCR>/neuroforge/{group}-{role}:v<semver>  +  :latest
#   - namespace = neuroforge/ (단일)
#   - image     = {group}-{role}  (예: edge-collector-mc, edge-publisher, cortex-web)
#   - tag       = v<semver> + latest 겹침 (v prefix 필수)
#
# 사용:
#   bash scripts/push-to-ncr.sh                       # 3개 다
#   bash scripts/push-to-ncr.sh edge-collector-mc     # 일부만
#   bash scripts/push-to-ncr.sh edge-collector-mc edge-publisher
#
# 사전:
#   docker login neuroforge-max-registry.kr.ncr.ntruss.com   # 1회
#   python deploy/jem_ble/build_deploy.py                    # tar 생성
#   docker load -i deploy/jem_ble/<each>.tar                 # 로컬 이미지 로드
# ============================================================================
set -euo pipefail

NCR="${NCR:-neuroforge-max-registry.kr.ncr.ntruss.com}"
NS="${NS:-neuroforge}"   # registry namespace (단일)

IMAGES=(edge-collector-mc edge-collector-ble edge-publisher)

if [ $# -gt 0 ]; then
    SELECTED=("$@")
else
    SELECTED=("${IMAGES[@]}")
fi

pushed=0
skipped=0

for name in "${SELECTED[@]}"; do
    # build_deploy.py 산출 = "<name>:<semver>" (예: edge-collector-mc:0.4.2)
    local_tag="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${name}:[0-9]" | head -1)"
    if [ -z "${local_tag}" ]; then
        echo "[skip] 로컬 이미지 없음: ${name}:<version>  (build_deploy.py + docker load 먼저)"
        skipped=$((skipped+1))
        continue
    fi
    ver="${local_tag#*:}"
    # NCR 태그는 v 접두사 필수
    dst_ver="${NCR}/${NS}/${name}:v${ver}"
    dst_latest="${NCR}/${NS}/${name}:latest"

    echo "### ${local_tag}"

    # ─── 안전장치: semver 태그는 immutable. 같은 v<ver> 덮어쓰기 금지 ───
    # 이미 NCR 에 같은 ver 태그가 있으면 ver 푸시 skip (latest 만 갱신).
    # 코드 변경했는데 동일 ver 면 → APP_VERSION 범프 후 다시 실행 권장.
    if docker manifest inspect "${dst_ver}" >/dev/null 2>&1; then
        echo "    [GUARD] ${dst_ver} 이미 NCR 에 존재 — semver 덮어쓰기 차단"
        echo "            (의도된 재push 면 무해. 코드 변경했다면 APP_VERSION 범프 후 재실행)"
        skip_ver=1
    else
        skip_ver=0
    fi

    echo "    tag → ${dst_latest}"
    docker tag "${local_tag}" "${dst_latest}"
    if [ "${skip_ver}" -eq 0 ]; then
        echo "    tag → ${dst_ver}"
        docker tag "${local_tag}" "${dst_ver}"
        echo "    push → ${dst_ver}"
        docker push "${dst_ver}"
    fi
    echo "    push → ${dst_latest}"
    docker push "${dst_latest}"

    pushed=$((pushed+1))
done

echo
echo "DONE.  pushed=${pushed}  skipped=${skipped}  registry=${NCR}/${NS}"
