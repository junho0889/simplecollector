"""
LoRa Processor
==============

LoRa 패킷 데이터를 DeviceProfile을 사용하여 파싱합니다.

Tag Address Mapping:
    CSV의 memory="LORA", address="temperature" → 프로파일의 'temperature' 필드
    CSV의 memory="META", address="rssi"        → LoRa 메타데이터 (신호 강도)
    CSV의 memory="META", address="snr"         → LoRa 메타데이터 (신호 대 잡음비)
"""

import logging
from typing import Any, List, Optional, Tuple

from ...core.interfaces import CollectedData, TagDefinition
from ...processors.base import BaseProcessor
from .profiles import ProfileRegistry

logger = logging.getLogger('collector.collection')


class LoRaRak5146Processor(BaseProcessor):
    """
    LoRa 데이터 처리기.

    DeviceProfile로 패킷 payload를 파싱하고,
    태그의 address 필드를 프로파일 필드 이름으로 매핑합니다.
    """

    def __init__(self, name: str, default_profile: str = 'posiot_lora'):
        super().__init__(name)
        self._default_profile = default_profile

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition],
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        LoRa 패킷 데이터 파싱.

        raw_data: end device payload 바이트 (device_id 제외)
        metadata:
            device_id: int
            rssi: float
            snr: float
            freq_hz: int
            datarate: int
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

        # 페이로드 파싱
        parsed_values: dict = {}
        if data.raw_data:
            parsed_values = profile.parse(data.raw_data)

        # META 필드 추가
        parsed_values['rssi'] = data.metadata.get('rssi')
        parsed_values['snr'] = data.metadata.get('snr')
        parsed_values['device_id'] = data.metadata.get('device_id')
        parsed_values['freq_hz'] = data.metadata.get('freq_hz')
        parsed_values['datarate'] = data.metadata.get('datarate')

        # 태그 매핑: tag.address = 프로파일 필드 이름
        results: List[Tuple[TagDefinition, Any]] = []

        for tag in tags:
            # config loader가 memory+address를 연결 (예: "LORAtemperature")
            # memory prefix를 제거하여 프로파일 필드 이름과 매칭
            if tag.memory and tag.address:
                field_name = tag.address[len(tag.memory):].lower()
            else:
                field_name = tag.address.lower() if tag.address else ''
            value = parsed_values.get(field_name)

            if value is not None:
                results.append((tag, value))

        return results
