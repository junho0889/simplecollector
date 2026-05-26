# ============================================================================
# NeuroForge Collector - Dockerfile
# ============================================================================
# 멀티스테이지 빌드를 사용하여 최적화된 이미지 생성
# ============================================================================

# ----------------------------------------------------------------------------
# Stage 1: Builder
# ----------------------------------------------------------------------------
FROM python:3.11-slim as builder

WORKDIR /app

# 시스템 의존성 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pip 업그레이드 및 wheel 설치
RUN pip install --no-cache-dir --upgrade pip wheel

# requirements 먼저 복사 (캐싱 최적화)
COPY requirements.txt .

# 의존성 설치 (wheel 빌드)
RUN pip wheel --no-cache-dir --wheel-dir /app/wheels -r requirements.txt

# ----------------------------------------------------------------------------
# Stage 2: Runtime
# ----------------------------------------------------------------------------
FROM python:3.11-slim as runtime

# 프로토콜 선택 (mc_protocol, modbus, s7_protocol, opcua, fenet)
ARG PROTOCOL=mc_protocol

# 메타데이터 (빌드 시 metadata.json → LABEL 주입)
ARG BUILD_METADATA="{}"
ARG VERSION="0.0.0"
ARG GIT_SHA=""
LABEL maintainer="NEUROSENSE Inc."
LABEL com.neurosense.metadata=${BUILD_METADATA}
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.title="NeuroForge Collector (${PROTOCOL})"
LABEL org.opencontainers.image.source="https://git.neurosense.synology.me/NASDAQ/simpleCollector.git"
LABEL org.opencontainers.image.revision="${GIT_SHA}"

# 보안: 비루트 사용자 생성
RUN groupadd -r collector && useradd -r -g collector collector

WORKDIR /app

# 런타임 시스템 의존성만 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    gosu \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# wheels 복사 및 설치
COPY --from=builder /app/wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels \
    && pip uninstall -y pip wheel 2>/dev/null; \
    rm -rf /usr/local/lib/python3.11/site-packages/pip* \
           /usr/local/lib/python3.11/site-packages/wheel* \
    && find /usr/local/lib/python3.11/site-packages \
        \( -name '__pycache__' -o -name '*.pyc' \) \
        -exec rm -rf {} + 2>/dev/null; true

# 애플리케이션 코드 복사 (선택된 프로토콜만 포함)
ARG PROTOCOL
COPY --chown=collector:collector src/core/ ./src/core/
COPY --chown=collector:collector src/collectors/__init__.py ./src/collectors/
COPY --chown=collector:collector src/collectors/base.py ./src/collectors/
COPY --chown=collector:collector src/collectors/${PROTOCOL}/ ./src/collectors/${PROTOCOL}/
COPY --chown=collector:collector src/processors/ ./src/processors/
COPY --chown=collector:collector src/publishers/ ./src/publishers/
COPY --chown=collector:collector src/pipeline/ ./src/pipeline/
COPY --chown=collector:collector src/services/ ./src/services/
COPY --chown=collector:collector src/utils/ ./src/utils/
COPY --chown=collector:collector src/main.py ./src/
COPY --chown=collector:collector src/version.py ./src/
COPY --chown=collector:collector config/ ./config/

# 데이터/로그 디렉토리 생성
RUN mkdir -p /app/data /app/logs && \
    chown -R collector:collector /app/data /app/logs

# Entrypoint 스크립트 복사
COPY build/collector/docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 환경변수
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/collector.yaml \
    LOG_LEVEL=INFO

# 볼륨 마운트 포인트
VOLUME ["/app/config", "/app/data", "/app/logs"]

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Root로 시작 → entrypoint에서 collector 유저로 전환
ENTRYPOINT ["docker-entrypoint.sh"]

# 기본 인수 (설정 파일)
CMD ["--config", "/app/config/collector.yaml"]
