"""
Processors 모듈
===============

데이터 처리기 구현을 제공합니다.

역할:
    - Raw 데이터 파싱
    - 데이터 타입 변환
    - 스케일링 적용
    - 버퍼에 데이터 적재

Usage:
    from src.processors import BaseProcessor

    class MyProcessor(BaseProcessor):
        async def _parse_raw_data(self, data, tags):
            # 프로토콜별 파싱 로직
            pass
"""

from .base import BaseProcessor

__all__ = [
    "BaseProcessor",
]
