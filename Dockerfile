# ============================================================================
# Simple Collector - Dockerfile
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

# 메타데이터
LABEL maintainer="SimpleCollector Team"
LABEL version="1.0.0"
LABEL description="Industrial Data Collection Framework"

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
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# 애플리케이션 코드 복사
COPY --chown=collector:collector src/ ./src/
COPY --chown=collector:collector config/ ./config/

# 데이터/로그 디렉토리 생성
RUN mkdir -p /app/data /app/logs && \
    chown -R collector:collector /app/data /app/logs

# Entrypoint 스크립트 복사
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 환경변수
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_PATH=/app/config/collector_mc_r.yaml \
    LOG_LEVEL=INFO

# 볼륨 마운트 포인트
VOLUME ["/app/config", "/app/data", "/app/logs"]

# 헬스체크
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# Root로 시작 → entrypoint에서 collector 유저로 전환
ENTRYPOINT ["docker-entrypoint.sh"]

# 기본 인수 (설정 파일)
CMD ["--config", "/app/config/collector_mc_r.yaml"]
