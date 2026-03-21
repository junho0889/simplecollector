#!/bin/bash
# ============================================================================
# JEM+BLE Integration Test 실행
# ============================================================================
# Usage: bash run_test.sh
# ============================================================================

set -e
cd "$(dirname "$0")"

echo "============================================"
echo "  JEM+BLE Integration Test"
echo "============================================"

# 1. 기존 컨테이너 정리
echo "[1/6] 기존 컨테이너 정리..."
docker compose down -v 2>/dev/null || true

# 2. 인프라 시작
echo "[2/6] 인프라 시작 (RabbitMQ + TimescaleDB)..."
docker compose up -d rabbitmq timescaledb
echo "  healthy 대기..."
sleep 10

# 3. Publisher 시작
echo "[3/6] Publisher 시작 (device_types: plc+ble)..."
docker compose up -d publisher
sleep 8

# 4. Mock Producer 실행
echo "[4/6] Mock Producer 실행 (PLC + BLE 데이터 주입)..."
docker compose run --rm mock-producer

# 5. Publisher 처리 대기
echo "[5/6] Publisher 처리 대기 (15초)..."
sleep 15

# 6. Test Runner
echo "[6/6] DB 검증..."
docker compose run --rm test-runner --wait 5

EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo ""
    echo "============================================"
    echo "  ALL TESTS PASSED ✓"
    echo "============================================"
else
    echo ""
    echo "============================================"
    echo "  SOME TESTS FAILED ✗"
    echo "============================================"
    echo ""
    echo "디버깅:"
    echo "  docker compose logs publisher"
    echo "  docker compose exec timescaledb psql -U postgres -d test_collector -c '\\dt'"
fi

echo ""
read -p "컨테이너 정리? (y/N) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker compose down -v
    echo "정리 완료."
fi

exit $EXIT_CODE
