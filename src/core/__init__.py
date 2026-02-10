"""
Core 모듈 - 프레임워크의 핵심 컴포넌트
======================================

이 모듈은 데이터 수집기의 핵심 인터페이스와 공통 컴포넌트를 제공합니다.

주요 클래스:
    - ICollector: 데이터 수집기 인터페이스
    - IProcessor: 데이터 처리기 인터페이스
    - IPublisher: 데이터 발행기 인터페이스
    - DataBuffer: 스레드 안전 데이터 버퍼
    - EventBus: 이벤트 기반 통신 시스템
    - ConfigLoader: 설정 파일 로더
"""

from .interfaces import (
    ICollector,
    IProcessor,
    IPublisher,
    CollectedData,
    ProcessedData,
    TagDefinition,
)
from .buffer import DataBuffer
from .events import EventBus, Event, EventType
from .config import ConfigLoader, CollectorConfig

__all__ = [
    # 인터페이스
    "ICollector",
    "IProcessor",
    "IPublisher",
    # 데이터 모델
    "CollectedData",
    "ProcessedData",
    "TagDefinition",
    # 핵심 컴포넌트
    "DataBuffer",
    "EventBus",
    "Event",
    "EventType",
    "ConfigLoader",
    "CollectorConfig",
]
