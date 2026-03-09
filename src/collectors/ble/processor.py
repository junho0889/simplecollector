"""
BLE Processor
=============

BLE advertisement 데이터를 DeviceProfile을 사용하여 파싱합니다.

Tag Address Mapping:
    CSV의 memory="ADV", address="temperature" → 프로파일의 'temperature' 필드
    CSV의 memory="META", address="rssi"       → BLE 메타데이터 (신호 강도)
"""

import logging
from typing import Any, List, Optional, Tuple

from ...core.interfaces import CollectedData, TagDefinition
from ...processors.base import BaseProcessor
from .profiles import ProfileRegistry

logger = logging.getLogger('collector.collection')


class BleProcessor(BaseProcessor):
    """
    BLE 데이터 처리기.

    DeviceProfile로 manufacturer_data를 파싱하고,
    태그의 address 필드를 프로파일 필드 이름으로 매핑합니다.
    """

    def __init__(self, name: str, default_profile: str = 'posiot'):
        super().__init__(name)
        self._default_profile = default_profile

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition],
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        BLE advertisement 데이터 파싱.

        metadata:
            manufacturer_data: {company_id: bytes}
            device_name: str
            rssi: int
            mac_address: str
            device_profile: str
        """
        profile_name = data.metadata.get(
            'device_profile', self._default_profile
        )
        profile = ProfileRegistry.get(profile_name)

        if not profile:
            logger.error(
                f"[{self._name}] Unknown device profile: {profile_name}"
            )
            return []

        # manufacturer_data 파싱
        mfr_data = data.metadata.get('manufacturer_data', {})
        parsed_values: dict = {}

        if mfr_data:
            company_id, raw_bytes = next(iter(mfr_data.items()))
            parsed_values = profile.parse(company_id, raw_bytes)

        # META 필드 추가
        parsed_values['rssi'] = data.metadata.get('rssi')
        parsed_values['device_name'] = data.metadata.get('device_name')
        parsed_values['mac_address'] = data.metadata.get('mac_address')

        # 태그 매핑: tag.address = 프로파일 필드 이름
        results: List[Tuple[TagDefinition, Any]] = []

        for tag in tags:
            field_name = tag.address.lower() if tag.address else ''
            value = parsed_values.get(field_name)

            if value is not None:
                results.append((tag, value))

        return results
