"""
BLE Multi-Device Collector
==========================

여러 BLE 디바이스를 하나의 컨테이너에서 수집하는 컬렉터.
YAML devices 설정에서 디바이스 목록을 로드하고,
디바이스별로 device_id를 부여하여 CollectedData를 발행합니다.

Data Flow:
    [N개 BLE 센서] ─광고패킷─> [BleScanner 싱글톤]
                                    ↓ (MAC 캐시)
                               [BleMultiCollector]
                                    ↓ device_id별 순회
                               [CollectedData × N] → Pipeline → RabbitMQ
                               (각 CollectedData.plc_id = device_id)
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from ..base import BaseCollector
from ...core.config import CollectorConfig, CollectionGroup, BleDeviceEntry
from ...core.events import EventBus
from ...core.interfaces import CollectedData, TagDefinition
from .scanner import BleScanner, _sanitize_float

logger = logging.getLogger('collector.collection')


@dataclass
class BleDeviceInfo:
    """BLE 디바이스 정보 (YAML devices + CSV 태그에서 병합)."""
    device_id: int
    mac_address: str
    device_name_filter: str = ""
    device_profile: str = ""
    description: str = ""
    tag_count: int = 0


class BleMultiCollector(BaseCollector):
    """
    BLE 멀티디바이스 수집기.

    YAML devices 섹션에서 디바이스 목록을 로드하고,
    BleScanner 싱글톤을 공유하여 모든 디바이스를 수집합니다.

    각 디바이스별로 device_id를 CollectedData.plc_id에 설정하여
    publisher에서 ble_id로 구분합니다.
    """

    def __init__(
        self,
        name: str,
        config: CollectorConfig,
        tags: List[TagDefinition],
        event_bus: Optional[EventBus] = None,
    ):
        # plc_id=0 (멀티디바이스 — 개별 device_id는 CollectedData에서 설정)
        super().__init__(plc_id=0, name=name, config=config, event_bus=event_bus)

        # YAML devices에서 디바이스 맵 구축
        self._devices: Dict[str, BleDeviceInfo] = {}
        self._device_id_to_mac: Dict[int, str] = {}
        self._build_device_map(config.devices, tags)

        extra = self._protocol_config.extra if self._protocol_config else {}
        self._cache_ttl: float = _sanitize_float(
            extra.get('cache_ttl', 30.0), 30.0, "cache_ttl",
            allow_zero=False,
        )
        self._duplicate_filter_s: float = _sanitize_float(
            extra.get('duplicate_filter_s', 4.0), 4.0, "duplicate_filter_s",
            allow_zero=True,
        )

        self._scanner: Optional[BleScanner] = None

        # 디바이스별 마지막 수집 시각
        self._device_last_seen: Dict[str, Optional[datetime]] = {
            mac: None for mac in self._devices
        }

        logger.info(
            f"[{self._name}] BleMultiCollector initialized with "
            f"{len(self._devices)} devices from YAML config"
        )
        for mac, info in self._devices.items():
            logger.info(
                f"  device_id={info.device_id} mac={mac} "
                f"profile='{info.device_profile}' "
                f"name_filter='{info.device_name_filter}' "
                f"tags={info.tag_count}"
            )

    def _build_device_map(
        self,
        devices: List[BleDeviceEntry],
        tags: List[TagDefinition],
    ) -> None:
        """YAML devices + CSV 태그에서 디바이스 맵 구축.

        설정 오류(orphan 디바이스 / orphan 태그)를 startup log로 노출해
        silently data loss 방지.
        """
        # YAML devices 기본 등록
        for d in devices:
            if not d.mac_address:
                logger.warning(
                    f"[{self._name}] device_id={d.device_id} has empty mac_address — skip"
                )
                continue
            mac = d.mac_address.upper()
            if mac in self._devices:
                logger.warning(
                    f"[{self._name}] duplicate mac_address {mac} "
                    f"(device_id={d.device_id}) — overwriting earlier entry"
                )
            self._devices[mac] = BleDeviceInfo(
                device_id=d.device_id,
                mac_address=mac,
                device_name_filter=d.device_name_filter,
                device_profile=d.device_profile,
                description=d.description,
                tag_count=0,
            )
            self._device_id_to_mac[d.device_id] = mac

        # CSV 태그에서 태그 카운트 집계 + orphan 태그 감지
        orphan_device_ids: set = set()
        orphan_macs: set = set()
        for tag in tags:
            if tag.device_id is not None:
                mac = self._device_id_to_mac.get(tag.device_id)
                if mac and mac in self._devices:
                    self._devices[mac].tag_count += 1
                else:
                    orphan_device_ids.add(tag.device_id)
            elif tag.mac_address:
                # 레거시: mac_address가 CSV에 있는 경우 (하위 호환)
                mac = tag.mac_address.upper()
                if mac in self._devices:
                    self._devices[mac].tag_count += 1
                else:
                    orphan_macs.add(mac)

        # 설정 오류 경고
        if orphan_device_ids:
            logger.warning(
                f"[{self._name}] CSV tags reference unknown device_id(s) "
                f"{sorted(orphan_device_ids)} (not in YAML devices) — these tags ignored"
            )
        if orphan_macs:
            logger.warning(
                f"[{self._name}] CSV tags reference unknown mac_address(es) "
                f"{sorted(orphan_macs)} (not in YAML devices) — these tags ignored"
            )
        # 태그 없는 디바이스 (설정 오류일 가능성)
        for mac, info in self._devices.items():
            if info.tag_count == 0:
                logger.warning(
                    f"[{self._name}] device_id={info.device_id} mac={mac} "
                    f"has 0 tags in CSV — device will be collected but no data published"
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

        매 사이클마다 모든 디바이스를 순회하고,
        데이터가 있는 디바이스마다 device_id를 plc_id에 설정하여
        CollectedData를 개별 발행합니다.
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
        """단일 디바이스에서 데이터 수집. plc_id = device_id."""
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
            plc_id=device_info.device_id,  # device_id를 plc_id로 설정
            raw_data=b'',
            collection_group=group,
            metadata={
                'manufacturer_data': {
                    k: bytes(v) for k, v in entry.manufacturer_data.items()
                },
                'device_name': entry.device_name,
                'rssi': entry.rssi,
                'mac_address': entry.mac_address,
                'device_profile': device_info.device_profile,
                'device_id': device_info.device_id,
            },
        )

    def get_stats(self) -> Dict:
        """멀티디바이스 통계."""
        stats = super().get_stats()
        stats['device_count'] = len(self._devices)
        stats['devices'] = {
            mac: {
                'device_id': info.device_id,
                'profile': info.device_profile,
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
