"""
BLE Multi-Device Collector
==========================

여러 BLE 디바이스를 하나의 컨테이너에서 수집하는 컬렉터.
태그 CSV의 mac_address 컬럼으로 디바이스를 자동 식별하고,
MAC별로 그룹핑하여 각 디바이스의 advertisement 데이터를 수집합니다.

1개의 CSV 파일로 1000개 이상의 BLE 센서를 관리할 수 있습니다.

Data Flow:
    [N개 BLE 센서] ─광고패킷─> [BleScanner 싱글톤]
                                    ↓ (MAC 캐시)
                               [BleMultiCollector]
                                    ↓ MAC별 순회
                               [CollectedData × N] → Pipeline → RabbitMQ
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set

from ..base import BaseCollector
from ...core.config import CollectorConfig, CollectionGroup
from ...core.events import EventBus
from ...core.interfaces import CollectedData, TagDefinition
from .scanner import BleScanner

logger = logging.getLogger('collector.collection')


@dataclass
class BleDeviceInfo:
    """태그 CSV에서 추출한 BLE 디바이스 정보."""
    mac_address: str
    device_name_filter: str = ""
    tag_count: int = 0


class BleMultiCollector(BaseCollector):
    """
    BLE 멀티디바이스 수집기.

    태그 CSV의 mac_address 컬럼에서 디바이스 목록을 자동 추출하고,
    BleScanner 싱글톤을 공유하여 모든 디바이스를 수집합니다.

    plc_id는 YAML config에서 하나만 사용 (tag_id가 전역 고유).
    """

    def __init__(
        self,
        name: str,
        config: CollectorConfig,
        tags: List[TagDefinition],
        event_bus: Optional[EventBus] = None,
    ):
        plc_id = config.plc_id if config.plc_id > 0 else 0
        super().__init__(plc_id=plc_id, name=name, config=config, event_bus=event_bus)

        # MAC별 디바이스 정보 추출
        self._devices: Dict[str, BleDeviceInfo] = {}
        self._build_device_map(tags)

        extra = self._protocol_config.extra if self._protocol_config else {}
        self._cache_ttl: float = float(extra.get('cache_ttl', 30.0))
        self._duplicate_filter_s: float = float(
            extra.get('duplicate_filter_s', 4.0)
        )

        self._scanner: Optional[BleScanner] = None

        # 디바이스별 마지막 수집 시각
        self._device_last_seen: Dict[str, Optional[datetime]] = {
            mac: None for mac in self._devices
        }

        logger.info(
            f"[{self._name}] BleMultiCollector initialized with "
            f"{len(self._devices)} devices from tags CSV"
        )
        for mac, info in self._devices.items():
            logger.info(
                f"  mac={mac} name_filter='{info.device_name_filter}' "
                f"tags={info.tag_count}"
            )

    def _build_device_map(self, tags: List[TagDefinition]) -> None:
        """태그 목록에서 MAC 주소별 디바이스 정보를 추출."""
        for tag in tags:
            if not tag.mac_address:
                continue

            mac = tag.mac_address.upper()
            if mac not in self._devices:
                self._devices[mac] = BleDeviceInfo(
                    mac_address=mac,
                    device_name_filter=tag.device_name_filter,
                    tag_count=0,
                )
            self._devices[mac].tag_count += 1

    async def _do_connect(self) -> bool:
        """BLE 스캐너 참조 획득."""
        try:
            self._scanner = BleScanner.get_instance()
            await self._scanner.acquire(
                cache_ttl=self._cache_ttl,
                duplicate_filter_s=self._duplicate_filter_s,
            )
            logger.info(
                f"[{self._name}] BLE scanner acquired for "
                f"{len(self._devices)} devices"
            )
            return True
        except ImportError:
            logger.error(
                f"[{self._name}] bleak 패키지 미설치. pip install bleak"
            )
            return False
        except Exception as e:
            logger.error(f"[{self._name}] BLE scanner acquire failed: {e}")
            return False

    async def _do_disconnect(self) -> None:
        """스캐너 참조 해제."""
        if self._scanner:
            await self._scanner.release()
            self._scanner = None
            logger.info(f"[{self._name}] BLE scanner released")

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """단일 디바이스 수집 (사용하지 않음 — _collection_loop 오버라이드)."""
        return None

    async def _do_health_check(self) -> bool:
        """디바이스 중 하나라도 TTL 내에 보였으면 정상."""
        if not self._scanner:
            return False
        return any(
            self._scanner.is_device_available(mac)
            for mac in self._devices
        )

    # =========================================================================
    # Collection Loop Override
    # =========================================================================

    async def _collection_loop(
        self,
        group_name: str,
        group_config: CollectionGroup,
    ) -> None:
        """
        멀티디바이스 수집 루프.

        매 사이클마다 모든 MAC을 순회하고,
        데이터가 있는 디바이스마다 CollectedData를 개별 발행합니다.
        """
        interval = group_config.interval_ms / 1000.0
        from ...core.interfaces import ConnectionState

        while self._is_running:
            start_time = asyncio.get_event_loop().time()
            collection_time = datetime.now()

            if self._state == ConnectionState.CONNECTED:
                collected_count = 0

                for mac, device_info in self._devices.items():
                    data = self._collect_device(
                        mac, device_info, group_name, collection_time,
                    )
                    if data:
                        collected_count += 1
                        self._device_last_seen[mac] = collection_time
                        await self._notify_data_collected(data)

                if collected_count > 0:
                    self._consecutive_loss[group_name] = 0
                    self._total_collected[group_name] = (
                        self._total_collected.get(group_name, 0) + collected_count
                    )

                    if logger.isEnabledFor(logging.DEBUG):
                        logger.debug(
                            f"[{self._name}] group='{group_name}' "
                            f"collected {collected_count}/{len(self._devices)} devices"
                        )
                else:
                    await self._handle_collection_failure(
                        group_name, collection_time, "no_device_data"
                    )
            else:
                await self._handle_collection_failure(
                    group_name, collection_time, "not_connected"
                )

            # 다음 사이클까지 대기
            elapsed = asyncio.get_event_loop().time() - start_time
            sleep_time = max(0, interval - elapsed)

            try:
                await asyncio.sleep(sleep_time)
            except asyncio.CancelledError:
                break

    def _collect_device(
        self,
        mac: str,
        device_info: BleDeviceInfo,
        group: str,
        collection_time: datetime,
    ) -> Optional[CollectedData]:
        """단일 디바이스에서 데이터 수집."""
        if not self._scanner:
            return None

        entry = self._scanner.get_latest(mac)
        if entry is None:
            return None

        # device_name_filter 적용
        if device_info.device_name_filter and entry.device_name:
            if device_info.device_name_filter not in entry.device_name:
                return None

        return CollectedData(
            source_time=entry.timestamp,
            collection_time=collection_time,
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
            },
        )

    def get_stats(self) -> Dict:
        """멀티디바이스 통계."""
        stats = super().get_stats()
        stats['device_count'] = len(self._devices)
        stats['devices'] = {
            mac: {
                'name_filter': info.device_name_filter,
                'tag_count': info.tag_count,
                'last_seen': (
                    self._device_last_seen[mac].isoformat()
                    if self._device_last_seen.get(mac) else None
                ),
            }
            for mac, info in self._devices.items()
        }
        return stats
