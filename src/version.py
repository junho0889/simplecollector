"""
Simple Collector Version Information
====================================

각 모듈의 버전 정보를 관리합니다.
"""

import sys
import platform
from typing import Dict, List, Tuple

# =============================================================================
# Application Version
# =============================================================================
APP_NAME = "Simple Collector"
APP_VERSION = "0.2.3-beta"
APP_BUILD_DATE = "2026-02-09"

# =============================================================================
# Module Versions
# =============================================================================
MODULE_VERSIONS: Dict[str, str] = {
    "core": "0.2.0",           # 핵심 인터페이스, 설정
    "collectors": "0.2.3",     # 데이터 수집기 (VERBOSE 로그 지원)
    "processors": "0.2.0",     # 데이터 처리기
    "publishers": "0.2.1",     # 데이터 발행기 (VERBOSE 로그 지원)
    "pipeline": "0.2.0",       # 파이프라인 관리
    "services": "0.2.0",       # 부가 서비스 (Status API 등)
    "utils": "0.2.1",          # 유틸리티 (VERBOSE 로그 레벨 추가)
}

# =============================================================================
# Protocol Support
# =============================================================================
SUPPORTED_PROTOCOLS: Dict[str, str] = {
    "mc_protocol": "0.2.3",    # 미쓰비시 MC Protocol (Binary 3E/4E, VERBOSE 로그)
    "modbus": "0.1.0",         # Modbus TCP/RTU
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
