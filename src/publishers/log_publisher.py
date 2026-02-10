"""
로그 발행기
===========

테스트용으로 데이터를 로그로 출력하는 발행기입니다.
"""

import logging
from typing import List, Optional

from .base import BasePublisher
from ..core.interfaces import ProcessedData
from ..core.config import PublisherConfig
from ..utils.logging import LoggerFactory, VERBOSE


logger = LoggerFactory.get_publish_logger()


class LogPublisher(BasePublisher):
    """
    로그 발행기.

    버퍼에서 데이터를 가져와 로그로 출력합니다.
    테스트/디버깅용으로 사용됩니다.
    """

    def __init__(
        self,
        name: str,
        publisher_config: Optional[PublisherConfig] = None,
        log_level: int = logging.INFO,
        log_sample_only: bool = True,
        sample_size: int = 5,
    ):
        """
        Args:
            name: 발행기 이름
            publisher_config: 발행기 설정
            log_level: 로그 레벨
            log_sample_only: True면 샘플만 출력
            sample_size: 샘플 크기
        """
        # config가 None이면 기본값 사용
        if publisher_config is None:
            publisher_config = PublisherConfig()

        super().__init__(
            name=name,
            config=publisher_config,
        )
        self._log_level = log_level
        self._log_sample_only = log_sample_only
        self._sample_size = sample_size
        self._total_published = 0

    async def _do_connect(self) -> bool:
        """연결 (로그 발행기는 항상 성공)."""
        logger.info(f"[{self._name}] Log publisher ready")
        return True

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        logger.info(f"[{self._name}] Log publisher stopped. Total published: {self._total_published}")

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 발행 (로그 출력).

        Args:
            data: 발행할 데이터 리스트

        Returns:
            True (항상 성공)
        """
        if not data:
            return True

        self._total_published += len(data)

        # 샘플 출력
        sample = data[:self._sample_size] if self._log_sample_only else data

        logger.log(self._log_level, f"[{self._name}] Published {len(data)} records (total: {self._total_published})")

        for item in sample:
            # ProcessedData에서 값 추출 (VERBOSE 레벨로 개별 항목 출력)
            value = item.v_float or item.v_int or item.v_bigint or item.v_bool or item.v_text or item.v_byte
            logger.verbose(f"  - Tag{item.tag_id}: {value} ({item.data_type.name})")

        if self._log_sample_only and len(data) > self._sample_size:
            logger.verbose(f"  ... and {len(data) - self._sample_size} more")

        return True
