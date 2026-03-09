"""
LoRa RAK5146 Collector
======================

RAK5146 기반 LoRa 디바이스 수집기. BaseCollector를 상속합니다.

Connection Model:
    - connect()  = LoRaScanner 참조 획득 + HAL 초기화
    - disconnect() = LoRaScanner 참조 해제
    - collect()  = LoRaScanner 캐시에서 최신 패킷 조회

YAML Config (protocol.extra):
    device_id: 1                              # 대상 end device ID (필수, 1~255)
    device_profile: "posiot_lora"             # 프로파일 이름 (기본 "posiot_lora")
    lib_path: "/app/lib/libloragw.so"         # HAL 라이브러리 경로
    spi_path: "/dev/spidev0.0"                # SPI 디바이스 경로
    freq_hz: 923300000                        # 중심 주파수 (Hz)
    cache_ttl: 30.0                           # 캐시 만료 (초)
    poll_interval: 0.01                       # 수신 폴링 간격 (초)
"""

import logging
from datetime import datetime
from typing import Optional

from ..base import BaseCollector
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...core.interfaces import CollectedData
from .scanner import LoRaScanner

logger = logging.getLogger('collector.collection')


class LoRaRak5146Collector(BaseCollector):
    """
    LoRa RAK5146 수집기.

    하나의 end device (device_id)에서 LoRa 패킷을 수집합니다.
    LoRaScanner 싱글톤을 통해 HAL 수신 데이터를 공유합니다.
    """

    def __init__(
        self,
        plc_id: int,
        name: str,
        config: CollectorConfig,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(plc_id, name, config, event_bus)

        extra = self._protocol_config.extra if self._protocol_config else {}
        self._device_id: int = int(extra.get('device_id', 0))
        self._device_profile: str = extra.get('device_profile', 'posiot_lora')
        self._lib_path: str = extra.get('lib_path', '/app/lib/libloragw.so')
        self._spi_path: str = extra.get('spi_path', '/dev/spidev0.0')
        self._freq_hz: int = int(extra.get('freq_hz', 923_300_000))
        self._cache_ttl: float = float(extra.get('cache_ttl', 30.0))
        self._poll_interval: float = float(extra.get('poll_interval', 0.01))

        self._scanner: Optional[LoRaScanner] = None

        if not self._device_id:
            logger.warning(
                f"[{self._name}] device_id not configured in protocol.extra"
            )

    async def _do_connect(self) -> bool:
        """LoRa 스캐너 참조 획득 + HAL 초기화."""
        try:
            self._scanner = LoRaScanner.get_instance()
            await self._scanner.acquire(
                lib_path=self._lib_path,
                spi_path=self._spi_path,
                freq_hz=self._freq_hz,
                cache_ttl=self._cache_ttl,
                poll_interval=self._poll_interval,
            )

            logger.info(
                f"[{self._name}] LoRa scanner acquired, "
                f"device_id={self._device_id} "
                f"(profile={self._device_profile})"
            )
            return True

        except RuntimeError as e:
            logger.error(
                f"[{self._name}] HAL init failed: {e}"
            )
            return False
        except Exception as e:
            logger.error(
                f"[{self._name}] LoRa scanner acquire failed: {e}"
            )
            return False

    async def _do_disconnect(self) -> None:
        """스캐너 참조 해제."""
        if self._scanner:
            await self._scanner.release()
            self._scanner = None
            logger.info(f"[{self._name}] LoRa scanner released")

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """LoRaScanner 캐시에서 최신 패킷 데이터 조회."""
        if not self._scanner or not self._device_id:
            return None

        entry = self._scanner.get_latest(self._device_id)

        if entry is None:
            return None

        return CollectedData(
            source_time=entry.timestamp,
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=entry.payload,
            collection_group=group,
            metadata={
                'device_id': entry.device_id,
                'rssi': entry.rssi,
                'snr': entry.snr,
                'freq_hz': entry.freq_hz,
                'datarate': entry.datarate,
                'device_profile': self._device_profile,
            },
        )

    async def _do_health_check(self) -> bool:
        """디바이스가 최근 TTL 내에 패킷을 보냈는지 확인."""
        if not self._scanner or not self._device_id:
            return False
        return self._scanner.is_device_available(self._device_id)
