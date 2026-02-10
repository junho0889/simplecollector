#!/bin/bash
# Simple Collector Deploy Script
# Usage: ./deploy.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE_NAME="simple-collector"
CONTAINER_NAME="simple-collector"
TAR_FILE="$SCRIPT_DIR/simple-collector-arm64-new.tar"

# ANSI progress 비활성화 (깨진 문자 방지)
export DOCKER_BUILDKIT=0
export COMPOSE_DOCKER_CLI_BUILD=0

echo "=== Simple Collector Deploy ==="

# 1. Stop and remove existing container
echo "[1/4] Stopping and removing existing container..."
docker compose -f "$SCRIPT_DIR/docker-compose.yml" down --remove-orphans 2>/dev/null || true
docker stop "$CONTAINER_NAME" 2>/dev/null || true
docker rm "$CONTAINER_NAME" 2>/dev/null || true

# 2. Remove existing image
if docker images --format '{{.Repository}}' | grep -q "^${IMAGE_NAME}$"; then
    echo "[2/4] Removing existing image..."
    docker rmi "$IMAGE_NAME" 2>/dev/null || true
else
    echo "[2/4] No existing image (skip)"
fi

# 3. Load new image
echo "[3/4] Loading new image: $TAR_FILE"
docker load -i "$TAR_FILE"

# 4. Start container
echo "[4/4] Starting container..."
docker compose --progress=plain -f "$SCRIPT_DIR/docker-compose.yml" up -d collector

# 5. Check status
echo ""
echo "=== Deploy Complete ==="
docker ps --filter "name=$CONTAINER_NAME" --format "Status: {{.Status}}"
echo ""
echo "Logs: docker compose logs -f collector"
