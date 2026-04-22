"""
BLE Collector
=============

BLE 디바이스 수집기. BaseCollector를 상속하여 BLE 스캐너를 통해 데이터를 수집합니다.

Connection Model:
    - connect()  = BleScanner 참조 획득 + 어댑터 가용성 확인
    - disconnect() = BleScanner 참조 해제
    - collect()  = BleScanner 캐시에서 최신 advertisement 데이터 조회

YAML Config (protocol.extra):
    mac_address: "AA:BB:CC:DD:EE:FF"    # 대상 디바이스 MAC (필수)
    device_name_filter: "POSIOT"         # 이름 필터 (선택)
    device_profile: "pts-2305bp"          # 프로파일 이름 (기본 "pts-2305bp")
    cache_ttl: 30.0                      # 캐시 만료 (초)
    duplicate_filter_s: 4.0              # 중복 필터 (초)
"""

import logging
from datetime import datetime
from typing import Optional

from ..base import BaseCollector
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...core.interfaces import CollectedData
from .scanner import BleScanner, _sanitize_float

logger = logging.getLogger('collector.collection')


class BleCollector(BaseCollector):
    """
    BLE 디바이스 수집기.

    하나의 BLE 디바이스(MAC 주소)에서 advertisement 데이터를 수집합니다.
    BleScanner 싱글톤을 통해 스캔 데이터를 공유합니다.
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
        raw_mac = extra.get('mac_address') or ''
        self._mac_address: str = str(raw_mac).upper()
        self._device_name_filter: str = str(extra.get('device_name_filter') or '')
        self._device_profile: str = str(
            extra.get('device_profile') or 'pts-2305bp'
        )
        self._cache_ttl: float = _sanitize_float(
            extra.get('cache_ttl', 30.0), 30.0, "cache_ttl",
            allow_zero=False,
        )
        self._duplicate_filter_s: float = _sanitize_float(
            extra.get('duplicate_filter_s', 4.0), 4.0, "duplicate_filter_s",
            allow_zero=True,
        )

        self._scanner: Optional[BleScanner] = None

        if not self._mac_address:
            logger.warning(
                f"[{self._name}] mac_address not configured in protocol.extra"
            )

    async def _do_connect(self) -> bool:
        """BLE 스캐너 참조 획득."""
        try:
            self._scanner = BleScanner.get_instance()
            await self._scanner.acquire(
                cache_ttl=self._cache_ttl,
                duplicate_filter_s=self._duplicate_filter_s,
            )

            logger.info(
                f"[{self._name}] BLE scanner acquired, "
                f"target: {self._mac_address} "
                f"(profile={self._device_profile})"
            )
            return True

        except ImportError:
            logger.error(
                f"[{self._name}] bleak 패키지 미설치. pip install bleak"
            )
            return False
        except Exception as e:
            logger.error(
                f"[{self._name}] BLE scanner acquire failed: {e}"
            )
            return False

    async def _do_disconnect(self) -> None:
        """스캐너 참조 해제."""
        if self._scanner:
            await self._scanner.release()
            self._scanner = None
            logger.info(f"[{self._name}] BLE scanner released")

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """BleScanner 캐시에서 최신 advertisement 데이터 조회."""
        if not self._scanner or not self._mac_address:
            return None

        entry = self._scanner.get_latest(self._mac_address)

        if entry is None:
            return None

        # device_name_filter 적용
        if self._device_name_filter and entry.device_name:
            if self._device_name_filter not in entry.device_name:
                logger.debug(
                    f"[{self._name}] Device name mismatch: "
                    f"'{entry.device_name}' != '{self._device_name_filter}'"
                )
                return None

        return CollectedData(
            source_time=entry.timestamp,
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=b'',
            collection_group=group,
            metadata={
                'manufacturer_data': {
                    k: bytes(v) for k, v in entry.manufacturer_data.items()
                },
                'device_name': entry.device_name,
                'rssi': entry.rssi,
                'mac_address': entry.mac_address,
                'device_profile': self._device_profile,
            },
        )

    async def _do_health_check(self) -> bool:
        """디바이스가 최근 TTL 내에 보였는지 확인."""
        if not self._scanner or not self._mac_address:
            return False
        return self._scanner.is_device_available(self._mac_address)
