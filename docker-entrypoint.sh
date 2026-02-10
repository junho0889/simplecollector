#!/bin/sh
# ============================================================================
# Simple Collector - Docker Entrypoint
# ============================================================================
# Fixes bind mount permissions before starting as non-root user
# ============================================================================

# Fix ownership of mounted volumes
chown -R collector:collector /app/data /app/logs 2>/dev/null || true

# Switch to collector user and execute main command
exec gosu collector python -m src.main "$@"
