"""
Protocol & Publisher Registry
=============================

프로토콜과 퍼블리셔를 동적으로 등록/조회하는 레지스트리.
설치된 패키지만 로드하고, 미설치 패키지는 graceful하게 스킵.

Usage:
    from src.core.registry import ProtocolRegistry, PublisherRegistry

    # 사용 가능한 프로토콜 확인
    available = ProtocolRegistry.list_available()
    # ['mc_protocol', 'modbus']  # 설치된 것만

    # 프로토콜 로드
    collector_cls = ProtocolRegistry.get_collector('mc_protocol')
"""

import importlib
import logging
from typing import Dict, List, Optional, Type, Tuple, Any

logger = logging.getLogger(__name__)


class ProtocolRegistry:
    """프로토콜 Collector/Processor 레지스트리."""

    # 프로토콜별 모듈 정보: (모듈 경로, 필요 패키지)
    PROTOCOLS: Dict[str, Tuple[str, List[str]]] = {
        'mc_protocol': ('src.collectors.mc_protocol', []),  # 순수 Python
        'melsec': ('src.collectors.mc_protocol', []),       # alias
        'modbus': ('src.collectors.modbus', []),  # 순수 Python (RTU 시리얼: pyserial 선택)
        's7_protocol': ('src.collectors.s7_protocol', ['snap7']),
        's7': ('src.collectors.s7_protocol', ['snap7']),    # alias
        'opcua': ('src.collectors.opcua', ['asyncua']),
        'fenet': ('src.collectors.fenet', []),              # 순수 Python
        'ble': ('src.collectors.ble', ['bleak']),             # BLE advertisement
        'lora_rak5146': ('src.collectors.lora_rak5146', []),   # LoRa RAK5146 (SX1303 HAL)
        'lora': ('src.collectors.lora_rak5146', []),           # alias
    }

    _cache: Dict[str, Any] = {}

    @classmethod
    def _check_dependencies(cls, packages: List[str]) -> Tuple[bool, str]:
        """패키지 의존성 확인."""
        missing = []
        for pkg in packages:
            try:
                importlib.import_module(pkg)
            except ImportError:
                missing.append(pkg)

        if missing:
            return False, f"Missing packages: {', '.join(missing)}"
        return True, ""

    @classmethod
    def is_available(cls, protocol: str) -> bool:
        """프로토콜 사용 가능 여부 확인."""
        protocol = protocol.lower()
        if protocol not in cls.PROTOCOLS:
            return False

        _, required_packages = cls.PROTOCOLS[protocol]
        available, _ = cls._check_dependencies(required_packages)
        return available

    @classmethod
    def list_available(cls) -> List[str]:
        """사용 가능한 프로토콜 목록 반환."""
        available = []
        for protocol in cls.PROTOCOLS:
            if cls.is_available(protocol):
                available.append(protocol)
        return list(set(available))  # 중복 제거 (alias)

    @classmethod
    def list_all(cls) -> Dict[str, bool]:
        """전체 프로토콜과 사용 가능 여부 반환."""
        result = {}
        seen_modules = set()
        for protocol, (module, _) in cls.PROTOCOLS.items():
            if module in seen_modules:
                continue
            seen_modules.add(module)
            result[protocol] = cls.is_available(protocol)
        return result

    @classmethod
    def get_collector(cls, protocol: str) -> Optional[Type]:
        """Collector 클래스 반환."""
        protocol = protocol.lower()
        cache_key = f"{protocol}_collector"

        if cache_key in cls._cache:
            return cls._cache[cache_key]

        if protocol not in cls.PROTOCOLS:
            logger.error(f"Unknown protocol: {protocol}")
            return None

        module_path, required = cls.PROTOCOLS[protocol]
        available, msg = cls._check_dependencies(required)

        if not available:
            logger.error(f"Protocol '{protocol}' not available: {msg}")
            logger.info(f"Install with: pip install simple-collector[{protocol}]")
            return None

        try:
            module = importlib.import_module(module_path)

            # Collector 클래스 찾기
            for name in dir(module):
                if name.endswith('Collector') and name != 'BaseCollector':
                    collector_cls = getattr(module, name)
                    cls._cache[cache_key] = collector_cls
                    return collector_cls

            logger.error(f"No Collector class found in {module_path}")
            return None

        except Exception as e:
            logger.error(f"Failed to load protocol '{protocol}': {e}")
            return None

    @classmethod
    def get_processor(cls, protocol: str) -> Optional[Type]:
        """Processor 클래스 반환."""
        protocol = protocol.lower()
        cache_key = f"{protocol}_processor"

        if cache_key in cls._cache:
            return cls._cache[cache_key]

        if protocol not in cls.PROTOCOLS:
            return None

        module_path, required = cls.PROTOCOLS[protocol]
        available, _ = cls._check_dependencies(required)

        if not available:
            return None

        try:
            module = importlib.import_module(module_path)

            for name in dir(module):
                if name.endswith('Processor') and name != 'BaseProcessor':
                    processor_cls = getattr(module, name)
                    cls._cache[cache_key] = processor_cls
                    return processor_cls

            return None

        except Exception as e:
            logger.error(f"Failed to load processor for protocol '{protocol}': {e}")
            return None


class PublisherRegistry:
    """Publisher 레지스트리."""

    PUBLISHERS: Dict[str, Tuple[str, List[str]]] = {
        'rabbitmq': ('src.publishers.rabbitmq', ['aio_pika']),
    }

    _cache: Dict[str, Any] = {}

    @classmethod
    def is_available(cls, publisher: str) -> bool:
        """Publisher 사용 가능 여부 확인."""
        publisher = publisher.lower()
        if publisher not in cls.PUBLISHERS:
            return False

        _, required = cls.PUBLISHERS[publisher]
        try:
            for pkg in required:
                importlib.import_module(pkg)
            return True
        except ImportError:
            return False

    @classmethod
    def list_available(cls) -> List[str]:
        """사용 가능한 Publisher 목록."""
        return [p for p in cls.PUBLISHERS if cls.is_available(p)]

    @classmethod
    def get_publisher(cls, publisher: str) -> Optional[Type]:
        """Publisher 클래스 반환."""
        publisher = publisher.lower()

        if publisher in cls._cache:
            return cls._cache[publisher]

        if not cls.is_available(publisher):
            logger.error(f"Publisher '{publisher}' not available")
            return None

        try:
            module_path, _ = cls.PUBLISHERS[publisher]
            module = importlib.import_module(module_path)

            for name in dir(module):
                if name.endswith('Publisher') and name != 'BasePublisher':
                    publisher_cls = getattr(module, name)
                    cls._cache[publisher] = publisher_cls
                    return publisher_cls

            return None

        except Exception as e:
            logger.error(f"Failed to load publisher: {e}")
            return None


def check_environment() -> Dict[str, Dict[str, bool]]:
    """현재 환경에서 사용 가능한 모듈 확인."""
    return {
        'protocols': ProtocolRegistry.list_all(),
        'publishers': {
            'rabbitmq': PublisherRegistry.is_available('rabbitmq'),
        }
    }


def print_environment():
    """환경 정보 출력."""
    env = check_environment()

    print("\n=== Simple Collector - Available Modules ===\n")

    print("Protocols:")
    for protocol, available in env['protocols'].items():
        status = "✓" if available else "✗"
        print(f"  {status} {protocol}")

    print("\nPublishers:")
    for publisher, available in env['publishers'].items():
        status = "✓" if available else "✗"
        print(f"  {status} {publisher}")

    print("\nTo install missing modules:")
    print("  pip install simple-collector[modbus]")
    print("  pip install simple-collector[rabbitmq]")
    print("  pip install simple-collector[full]")
    print()
