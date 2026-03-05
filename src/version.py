"""
Simple Collector Version Information
====================================

버전 정보의 단일 소스 (Single Source of Truth).
모든 모듈, 로그, metadata.json은 이 파일의 APP_VERSION을 참조합니다.
"""

import sys
import platform
from typing import Dict, List, Tuple

# =============================================================================
# Application Version
# =============================================================================
APP_NAME = "Simple Collector"
APP_VERSION = "0.3.1"
APP_BUILD_DATE = "2026-03-05"

# =============================================================================
# Changelog
# =============================================================================
CHANGELOG: List[Dict[str, str]] = [
    {
        "version": "0.3.1",
        "date": "2026-03-05",
        "changes": (
            "fix: L 디바이스 워드 기반 비트 읽기 주소 버그 수정 "
            "(워드 인덱스 대신 비트 주소를 PLC에 전송)"
        ),
    },
    {
        "version": "0.3.0",
        "date": "2026-02-27",
        "changes": (
            "MC/Modbus collector 부분 실패 허용 (이미 읽은 데이터 유지), "
            "재연결 지수 백오프 (1s~5s), "
            "Processor 재연결 시 on_change 캐시 초기화, "
            "Modbus coil/discrete 레지스터 + STRING 타입 파싱"
        ),
    },
    {
        "version": "0.2.3-beta",
        "date": "2026-02-09",
        "changes": "MC Protocol VERBOSE 로깅, apply_scaling NaN/Inf 검증",
    },
    {
        "version": "0.2.0-beta",
        "date": "2026-01-15",
        "changes": "태그 설정 확장, 마스터 동기화, 성능 최적화",
    },
    {
        "version": "0.1.0",
        "date": "2025-12-01",
        "changes": "초기 릴리스 (MC Protocol + RabbitMQ)",
    },
]

# =============================================================================
# Module Versions
# =============================================================================
MODULE_VERSIONS: Dict[str, str] = {
    "core": "0.2.0",           # 핵심 인터페이스, 설정
    "collectors": "0.3.1",     # 데이터 수집기 (L 디바이스 주소 버그 수정)
    "processors": "0.3.0",     # 데이터 처리기 (재연결 캐시 초기화)
    "publishers": "0.3.0",     # 데이터 발행기 (지수 백오프)
    "pipeline": "0.2.0",       # 파이프라인 관리
    "services": "0.2.0",       # 부가 서비스 (Status API 등)
    "utils": "0.2.1",          # 유틸리티 (VERBOSE 로그 레벨 추가)
}

# =============================================================================
# Protocol Support
# =============================================================================
SUPPORTED_PROTOCOLS: Dict[str, str] = {
    "mc_protocol": "0.3.1",    # 미쓰비시 MC Protocol (L 디바이스 주소 수정)
    "modbus": "0.2.0",         # Modbus TCP/RTU (coil/discrete/STRING 추가)
}

# =============================================================================
# Publisher Support
# =============================================================================
SUPPORTED_PUBLISHERS: Dict[str, str] = {
    "rabbitmq": "0.2.0",       # RabbitMQ (aio_pika)
}


def get_version_string() -> str:
    """간단한 버전 문자열 반환."""
    return f"{APP_NAME} v{APP_VERSION}"


def get_full_version_info() -> Dict[str, any]:
    """전체 버전 정보 딕셔너리 반환."""
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "build_date": APP_BUILD_DATE,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "modules": MODULE_VERSIONS.copy(),
        "protocols": SUPPORTED_PROTOCOLS.copy(),
        "publishers": SUPPORTED_PUBLISHERS.copy(),
    }


def get_dependency_versions() -> Dict[str, str]:
    """주요 의존성 버전 반환."""
    deps = {}

    # aio_pika (RabbitMQ)
    try:
        import aio_pika
        deps["aio-pika"] = aio_pika.__version__
    except (ImportError, AttributeError):
        deps["aio-pika"] = "not installed"

    # PyYAML
    try:
        import yaml
        deps["pyyaml"] = yaml.__version__
    except (ImportError, AttributeError):
        deps["pyyaml"] = "not installed"

    return deps


def log_version_info(logger) -> None:
    """버전 정보를 로거에 출력."""
    logger.info(f"{APP_NAME} v{APP_VERSION} (build: {APP_BUILD_DATE})")
    logger.info(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")

    # 모듈 버전
    logger.info("Modules:")
    for module, version in MODULE_VERSIONS.items():
        logger.info(f"  - {module}: v{version}")

    # 프로토콜 지원
    logger.info("Supported Protocols:")
    for protocol, version in SUPPORTED_PROTOCOLS.items():
        logger.info(f"  - {protocol}: v{version}")

    # Publisher 지원
    logger.info("Supported Publishers:")
    for publisher, version in SUPPORTED_PUBLISHERS.items():
        logger.info(f"  - {publisher}: v{version}")

    # 의존성 버전
    deps = get_dependency_versions()
    logger.info("Dependencies:")
    for dep, version in deps.items():
        logger.info(f"  - {dep}: {version}")
