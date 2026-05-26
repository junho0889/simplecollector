#!/usr/bin/env bash
# ============================================================================
# push-to-ncr.sh
# ----------------------------------------------------------------------------
# 로컬 docker 이미지를 NCR(neuroforge-max-registry.kr.ncr.ntruss.com) 로 push.
# 우리 3개(collector-mc / collector-ble / publisher) 전용 단순 버전.
# Cortex 레포에 정본(scripts/push-to-ncr.sh)이 있으면 그쪽을 따르고 이 파일은
# 보조용. 자체 사용 시 build_deploy.py 가 만든 `<name>:<version>` 태그를 자동
# 감지해서 NCR 의 edge/<name>:<version> + edge/<name>:latest 로 푸시.
#
# 사용:
#   bash scripts/push-to-ncr.sh                # 3개 다
#   bash scripts/push-to-ncr.sh collector-mc   # 일부만
#
# 사전:
#   docker login neuroforge-max-registry.kr.ncr.ntruss.com  # 1회
#   python build_deploy.py                                  # 또는 jem_ble/...
# ============================================================================
set -euo pipefail

NCR="${NCR:-neuroforge-max-registry.kr.ncr.ntruss.com}"
NS="${NS:-edge}"

IMAGES=(collector-mc collector-ble publisher)

if [ $# -gt 0 ]; then
    SELECTED=("$@")
else
    SELECTED=("${IMAGES[@]}")
fi

pushed=0
skipped=0

for name in "${SELECTED[@]}"; do
    # build_deploy.py 산출 = "<name>:<semver>" (예: collector-mc:0.4.2)
    local_tag="$(docker images --format '{{.Repository}}:{{.Tag}}' | grep -E "^${name}:[0-9]" | head -1)"
    if [ -z "${local_tag}" ]; then
        echo "[skip] 로컬 이미지 없음: ${name}:<version>  (build_deploy.py 먼저 실행 + docker load)"
        skipped=$((skipped+1))
        continue
    fi
    ver="${local_tag#*:}"
    dst_ver="${NCR}/${NS}/${name}:${ver}"
    dst_latest="${NCR}/${NS}/${name}:latest"

    echo "### ${local_tag}"
    echo "    tag → ${dst_ver}"
    echo "    tag → ${dst_latest}"
    docker tag "${local_tag}" "${dst_ver}"
    docker tag "${local_tag}" "${dst_latest}"

    echo "    push → ${dst_ver}"
    docker push "${dst_ver}"
    echo "    push → ${dst_latest}"
    docker push "${dst_latest}"

    pushed=$((pushed+1))
done

echo
echo "DONE.  pushed=${pushed}  skipped=${skipped}  registry=${NCR}/${NS}"
