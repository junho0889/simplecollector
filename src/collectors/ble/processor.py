"""
BLE Processor
=============

BLE advertisement 데이터를 DeviceProfile을 사용하여 파싱합니다.

Tag Address Mapping:
    CSV의 address="temperature" → 프로파일의 'temperature' 필드
    CSV의 address="rssi"        → BLE 메타데이터 (신호 강도)

Profile Auto-Detection:
    metadata에 device_profile이 없으면 등록된 모든 프로파일로 파싱 시도.
    가장 많은 필드를 파싱한 프로파일을 선택합니다.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

from ...core.interfaces import CollectedData, TagDefinition
from ...processors.base import BaseProcessor
from .profiles import ProfileRegistry

logger = logging.getLogger('collector.collection')


class BleProcessor(BaseProcessor):
    """
    BLE 데이터 처리기.

    DeviceProfile로 manufacturer_data를 파싱하고,
    태그의 address 필드를 프로파일 필드 이름으로 매핑합니다.
    멀티디바이스 모드에서는 mac_address로 태그를 필터링합니다.
    """

    def __init__(self, name: str, default_profile: str = 'posiot'):
        super().__init__(name)
        self._default_profile = default_profile
        # MAC별 프로파일 캐시 (자동 감지 결과)
        self._mac_profile_cache: Dict[str, str] = {}

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
            device_profile: str (선택 — 없으면 자동 감지)
        """
        mfr_data = data.metadata.get('manufacturer_data', {})
        parsed_values: dict = {}

        if mfr_data:
            company_id, raw_bytes = next(iter(mfr_data.items()))
            parsed_values = self._parse_with_profile(
                company_id, raw_bytes, data.metadata,
            )

        # META 필드 추가
        parsed_values['rssi'] = data.metadata.get('rssi')
        parsed_values['device_name'] = data.metadata.get('device_name')
        parsed_values['mac_address'] = data.metadata.get('mac_address')

        # 멀티디바이스: mac_address로 태그 필터링
        mac = data.metadata.get('mac_address', '').upper()

        # 태그 매핑: tag.address = 프로파일 필드 이름
        results: List[Tuple[TagDefinition, Any]] = []

        for tag in tags:
            # mac_address가 있는 태그는 해당 디바이스 데이터만 매핑
            if tag.mac_address and mac:
                if tag.mac_address.upper() != mac:
                    continue

            field_name = tag.address.lower() if tag.address else ''
            value = parsed_values.get(field_name)

            if value is not None:
                results.append((tag, value))

        return results

    def _parse_with_profile(
        self,
        company_id: int,
        raw_bytes: bytes,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """프로파일로 manufacturer_data 파싱. 자동 감지 지원."""
        # 1. metadata에 명시적 device_profile이 있으면 사용
        profile_name = metadata.get('device_profile')
        if profile_name:
            profile = ProfileRegistry.get(profile_name)
            if profile:
                return profile.parse(company_id, raw_bytes)
            logger.error(
                f"[{self._name}] Unknown device profile: {profile_name}"
            )
            return {}

        # 2. MAC별 캐시된 프로파일 확인
        mac = metadata.get('mac_address', '').upper()
        if mac in self._mac_profile_cache:
            cached_name = self._mac_profile_cache[mac]
            profile = ProfileRegistry.get(cached_name)
            if profile:
                return profile.parse(company_id, raw_bytes)

        # 3. 자동 감지: 모든 프로파일 시도, 가장 많은 필드 파싱한 것 선택
        best_result: Dict[str, Any] = {}
        best_profile_name = self._default_profile

        for name in ProfileRegistry.list_profiles():
            profile = ProfileRegistry.get(name)
            if not profile:
                continue
            try:
                result = profile.parse(company_id, raw_bytes)
                if len(result) > len(best_result):
                    best_result = result
                    best_profile_name = name
            except Exception:
                continue

        # 캐시에 저장
        if mac:
            self._mac_profile_cache[mac] = best_profile_name
            logger.info(
                f"[{self._name}] Auto-detected profile for {mac}: "
                f"{best_profile_name} ({len(best_result)} fields)"
            )

        return best_result
