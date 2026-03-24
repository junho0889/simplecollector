"""
BLE Processor
=============

BLE advertisement 데이터를 DeviceProfile 또는 manual byte_offset으로 파싱합니다.

파싱 모드 (태그 CSV의 mode 컬럼):
    manual:     CSV의 byte_offset("0:2" 슬라이스)으로 직접 바이트 추출
    <프로파일명>: ProfileRegistry에 등록된 프로파일로 파싱
                 (posiot, posiot_v2, PTS-0624B 등)
    빈값:       자동 감지 — 모든 프로파일 시도 후 최다 필드 매칭

같은 MAC에 다른 mode 태그가 혼합 가능.
새 센서 추가: ProfileRegistry에 등록 → CSV mode 컬럼에 이름 기입.
"""

import logging
import struct
from typing import Any, Dict, List, Optional, Tuple

from ...core.interfaces import CollectedData, DataType, TagDefinition
from ...processors.base import BaseProcessor
from .profiles import ProfileRegistry

logger = logging.getLogger('collector.collection')

# manual 모드 struct 포맷 매핑
_STRUCT_FORMAT: Dict[DataType, str] = {
    DataType.UINT8: 'B',
    DataType.INT8: 'b',
    DataType.UINT16: '<H',
    DataType.INT16: '<h',
    DataType.UINT32: '<I',
    DataType.INT32: '<i',
    DataType.FLOAT32: '<f',
    DataType.FLOAT64: '<d',
}


class BleProcessor(BaseProcessor):
    """
    BLE 데이터 처리기.

    태그의 ble_mode로 파싱 전략 결정:
      - "manual"       → byte_offset 슬라이스 파싱
      - "posiot" 등    → 해당 이름의 DeviceProfile
      - "" (빈값)      → 자동 감지 (모든 프로파일 시도)

    멀티디바이스 모드에서는 mac_address로 태그를 필터링합니다.
    """

    def __init__(self, name: str, default_profile: str = 'pts-2305bp'):
        super().__init__(name)
        self._default_profile = default_profile
        # (MAC, profile_name) → 파싱 결과 캐시 (같은 사이클 내 중복 파싱 방지)
        self._mac_profile_cache: Dict[str, str] = {}

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition],
    ) -> List[Tuple[TagDefinition, Any]]:
        """BLE advertisement 데이터 파싱."""
        mfr_data = data.metadata.get('manufacturer_data', {})
        company_id: Optional[int] = None
        raw_bytes: bytes = b''

        if mfr_data:
            # 마지막 항목 = 최신 advertisement (bleak이 company_id별로 누적하므로)
            *_, last = mfr_data.items()
            company_id, raw_bytes = last

        mac = data.metadata.get('mac_address', '').upper()

        # META 필드 (rssi, device_name 등 — 모든 모드에서 공통)
        meta_values = {
            'rssi': data.metadata.get('rssi'),
            'device_name': data.metadata.get('device_name'),
            'mac_address': data.metadata.get('mac_address'),
        }

        # 프로파일별 파싱 결과 캐시 (같은 패킷을 여러 번 파싱하지 않도록)
        profile_cache: Dict[str, Dict[str, Any]] = {}

        results: List[Tuple[TagDefinition, Any]] = []

        device_id = data.metadata.get('device_id')

        for tag in tags:
            # 디바이스 필터링: device_id 우선, 없으면 MAC 필터
            if tag.device_id is not None and device_id is not None:
                if tag.device_id != device_id:
                    continue
            elif tag.mac_address and mac:
                if tag.mac_address.upper() != mac:
                    continue

            mode = tag.ble_mode

            if mode == 'manual':
                # byte_offset 슬라이스 기반 파싱
                value = self._resolve_manual(
                    tag, raw_bytes, company_id, meta_values,
                )
            else:
                # 프로파일 기반 파싱 (mode=프로파일명 또는 빈값=자동감지)
                value = self._resolve_profile(
                    tag, mode, company_id, raw_bytes,
                    data.metadata, meta_values, profile_cache,
                )

            if value is not None:
                results.append((tag, value))

        return results

    # =========================================================================
    # Profile 파싱
    # =========================================================================

    def _resolve_profile(
        self,
        tag: TagDefinition,
        mode: str,
        company_id: Optional[int],
        raw_bytes: bytes,
        metadata: Dict[str, Any],
        meta_values: Dict[str, Any],
        profile_cache: Dict[str, Dict[str, Any]],
    ) -> Optional[Any]:
        """프로파일 기반 파싱. mode가 프로파일 이름이면 직접 조회, 빈값이면 자동 감지."""
        field_name = tag.tag_name.lower()

        # META 필드 우선
        if field_name in meta_values:
            return meta_values.get(field_name)

        if company_id is None:
            return None

        # 프로파일 결정
        profile_name = mode if mode else self._detect_profile_name(metadata)

        # 캐시에서 조회 (같은 패킷 + 같은 프로파일은 한 번만 파싱)
        if profile_name not in profile_cache:
            parsed = self._parse_with_profile(
                profile_name, company_id, raw_bytes, metadata,
            )
            if parsed is not None:
                profile_cache[profile_name] = parsed
            else:
                return None

        return profile_cache[profile_name].get(field_name)

    def _parse_with_profile(
        self,
        profile_name: str,
        company_id: int,
        raw_bytes: bytes,
        metadata: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """지정된 프로파일로 파싱. 이름이 없으면 자동 감지."""
        if profile_name:
            profile = ProfileRegistry.get(profile_name)
            if profile:
                return profile.parse(company_id, raw_bytes)
            logger.warning(
                f"[{self._name}] Profile '{profile_name}' not found in registry"
            )
            return None

        # 자동 감지: 모든 프로파일 시도
        return self._auto_detect_parse(company_id, raw_bytes, metadata)

    def _detect_profile_name(self, metadata: Dict[str, Any]) -> str:
        """MAC 캐시에서 프로파일 이름 조회. 없으면 빈 문자열(자동 감지 대상)."""
        mac = metadata.get('mac_address', '').upper()
        return self._mac_profile_cache.get(mac, '')

    def _auto_detect_parse(
        self,
        company_id: int,
        raw_bytes: bytes,
        metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        """모든 프로파일 시도, 가장 많은 필드 파싱한 것 선택 + 캐시."""
        best_result: Dict[str, Any] = {}
        best_name = self._default_profile

        for name in ProfileRegistry.list_profiles():
            profile = ProfileRegistry.get(name)
            if not profile:
                continue
            try:
                result = profile.parse(company_id, raw_bytes)
                if len(result) > len(best_result):
                    best_result = result
                    best_name = name
            except Exception as e:
                logger.debug(
                    f"[{self._name}] Profile '{name}' parse failed: {e}"
                )
                continue

        mac = metadata.get('mac_address', '').upper()
        if mac:
            self._mac_profile_cache[mac] = best_name
            logger.info(
                f"[{self._name}] Auto-detected profile for {mac}: "
                f"{best_name} ({len(best_result)} fields)"
            )

        return best_result

    # =========================================================================
    # Manual 파싱
    # =========================================================================

    def _resolve_manual(
        self,
        tag: TagDefinition,
        raw_bytes: bytes,
        company_id: Optional[int],
        meta_values: Dict[str, Any],
    ) -> Optional[Any]:
        """manual 모드: META 체크 → byte_offset 파싱."""
        field_name = tag.tag_name.lower()

        if field_name in meta_values:
            return meta_values.get(field_name)

        if tag.byte_offset and raw_bytes:
            return self._parse_manual(
                tag.byte_offset, raw_bytes, tag.data_type, company_id,
            )
        return None

    def _parse_manual(
        self,
        byte_offset: str,
        raw_bytes: bytes,
        data_type: DataType,
        company_id: Optional[int] = None,
    ) -> Optional[Any]:
        """
        byte_offset 슬라이스로 바이트 추출 후 data_type 변환.

        byte_offset 형식:
            "0:2"  → raw_bytes[0:2]
            "5"    → raw_bytes[5:5+type_size]
            "cid"  → company_id 값 (2바이트)
        """
        try:
            # company_id 특수 키워드
            if byte_offset.lower() == 'cid':
                if company_id is None:
                    return None
                cid_bytes = struct.pack('<H', company_id & 0xFFFF)
                fmt = _STRUCT_FORMAT.get(data_type)
                if fmt and len(cid_bytes) >= struct.calcsize(fmt):
                    return struct.unpack(fmt, cid_bytes[:struct.calcsize(fmt)])[0]
                return company_id

            # 슬라이스 파싱
            if ':' in byte_offset:
                parts = byte_offset.split(':')
                start = int(parts[0])
                end = int(parts[1])
            else:
                start = int(byte_offset)
                fmt = _STRUCT_FORMAT.get(data_type)
                end = start + (struct.calcsize(fmt) if fmt else data_type.byte_size)

            if start >= len(raw_bytes):
                return None

            chunk = raw_bytes[start:end]
            if not chunk:
                return None

            fmt = _STRUCT_FORMAT.get(data_type)
            if fmt:
                expected_size = struct.calcsize(fmt)
                if len(chunk) < expected_size:
                    return None
                return struct.unpack(fmt, chunk[:expected_size])[0]

            # fallback: 정수로 해석
            return int.from_bytes(chunk, byteorder='little', signed=False)

        except (ValueError, struct.error) as e:
            logger.warning(
                f"[{self._name}] manual parse error: "
                f"byte_offset={byte_offset}, error={e}"
            )
            return None
