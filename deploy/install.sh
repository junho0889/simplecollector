#!/bin/bash
# ============================================================================
# Collector System - Install / Reinstall Script
# ============================================================================
# Cleanup existing containers/images -> load tar -> docker compose up
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGES_DIR="$SCRIPT_DIR/images"

echo "============================================="
echo " Collector System Installer"
echo "============================================="
echo ""
echo "Directory: $SCRIPT_DIR"
echo ""

# -------------------------------------------------------------------------
# 1. Stop and remove existing containers
# -------------------------------------------------------------------------
echo "[1/4] Stopping existing containers..."

if [ -f "$SCRIPT_DIR/docker-compose.yml" ]; then
    cd "$SCRIPT_DIR"
    docker compose down --remove-orphans 2>/dev/null || true
fi

for name in rabbitmq collector-plc1 collector-plc2 neuro_publisher; do
    if docker ps -a --format '{{.Names}}' | grep -q "^${name}$"; then
        echo "  Removing container: $name"
        docker rm -f "$name" 2>/dev/null || true
    fi
done
echo "  Done."

# -------------------------------------------------------------------------
# 2. Remove existing images
# -------------------------------------------------------------------------
echo ""
echo "[2/4] Removing existing images..."

for img in "neuro_collector_mc:mc-latest" "neuro_publisher:latest"; do
    if docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${img}$"; then
        echo "  Removing image: $img"
        docker rmi "$img" 2>/dev/null || true
    fi
done
echo "  Done."

# -------------------------------------------------------------------------
# 3. Load images from tar
# -------------------------------------------------------------------------
echo ""
echo "[3/4] Loading images from tar..."

if [ ! -d "$IMAGES_DIR" ]; then
    echo "  ERROR: images directory not found: $IMAGES_DIR"
    exit 1
fi

tar_count=0
for tar_file in "$IMAGES_DIR"/*.tar; do
    if [ -f "$tar_file" ]; then
        echo "  Loading: $(basename "$tar_file")"
        docker load -i "$tar_file"
        tar_count=$((tar_count + 1))
    fi
done

if [ "$tar_count" -eq 0 ]; then
    echo "  ERROR: No tar files found in $IMAGES_DIR/"
    exit 1
fi
echo "  $tar_count image(s) loaded."

# -------------------------------------------------------------------------
# 4. Docker Compose up
# -------------------------------------------------------------------------
echo ""
echo "[4/4] Starting Docker Compose..."

cd "$SCRIPT_DIR"
docker compose up -d

echo ""
echo "============================================="
echo " Installation complete"
echo "============================================="
echo ""
echo "Status:  docker compose ps"
echo "Logs:    docker compose logs -f"
echo "Stop:    docker compose down"
echo ""
