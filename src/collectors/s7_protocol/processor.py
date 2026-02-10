"""
S7 Protocol Data Processor
==========================

Siemens S7 PLC에서 수집된 바이트 데이터를 파싱합니다.

Features:
    - 다양한 S7 데이터 타입 지원
    - DB, I, Q, M 영역 파싱
    - Big Endian 바이트 순서 (S7 표준)

Example:
    processor = S7Processor("s7_processor")
    processor.register_tags_for_group("fast", tags)
    processed = await processor.process(collected_data)
"""

import struct
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import logging

from ...processors.base import BaseProcessor
from ...core.interfaces import CollectedData, ProcessedData, TagDefinition, DataType
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class S7Processor(BaseProcessor):
    """
    S7 프로토콜 데이터 처리기.

    수집된 바이트 데이터를 파싱하여 실제 값으로 변환합니다.
    S7은 Big Endian 바이트 순서를 사용합니다.

    Data Type Parsing:
        - BOOL: 비트 값 (DBX)
        - INT: 부호 있는 16비트 정수 (DBW)
        - DINT: 부호 있는 32비트 정수 (DBD)
        - REAL: IEEE 754 단정밀도 부동소수점 (DBD)
        - LREAL: IEEE 754 배정밀도 부동소수점
        - STRING: S7 String 타입
    """

    def __init__(self, name: str):
        """
        Args:
            name: 처리기 이름
        """
        super().__init__(name)

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        바이트 데이터 파싱.

        Args:
            data: 수집된 데이터
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []

        # 영역별 바이트 데이터 추출
        area_data = data.metadata.get('area_data', {})

        for tag in tags:
            try:
                # 주소 파싱
                area, db_num, byte_offset, bit_offset = self._parse_address(
                    tag.address, tag.memory
                )

                key = (area, db_num)
                byte_dict = area_data.get(key, {})

                # 값 파싱
                value = self._parse_value(
                    byte_dict,
                    byte_offset,
                    bit_offset,
                    tag.data_type,
                    tag.word_length
                )

                if value is not None:
                    results.append((tag, value))
                else:
                    logger.warning(
                        f"[{self._name}] Failed to parse tag {tag.tag_id} "
                        f"at {area}{db_num}.{byte_offset}"
                    )

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error parsing tag {tag.tag_id}: {e}"
                )

        return results

    def _parse_address(
        self,
        address: str,
        memory: str = ""
    ) -> Tuple[str, int, int, int]:
        """
        S7 주소 문자열 파싱.

        Args:
            address: 주소 문자열
            memory: 메모리 영역

        Returns:
            (영역, DB번호, 바이트오프셋, 비트오프셋) 튜플
        """
        address = address.strip().upper()
        bit_offset = 0

        # DB 주소
        if address.startswith('DB') or memory.upper() == 'DB':
            if memory.upper() == 'DB':
                parts = address.split('.')
                db_num = int(parts[0])
                offset_str = parts[1] if len(parts) > 1 else "0"
            else:
                db_part, offset_part = address.split('.', 1)
                db_num = int(db_part[2:])
                offset_str = offset_part

            if offset_str.startswith('DBX'):
                bit_parts = offset_str[3:].split('.')
                byte_offset = int(bit_parts[0])
                bit_offset = int(bit_parts[1]) if len(bit_parts) > 1 else 0
            elif offset_str.startswith('DBW'):
                byte_offset = int(offset_str[3:])
            elif offset_str.startswith('DBD'):
                byte_offset = int(offset_str[3:])
            elif offset_str.startswith('DBB'):
                byte_offset = int(offset_str[3:])
            else:
                byte_offset = int(offset_str)

            return 'DB', db_num, byte_offset, bit_offset

        # M, I, Q 영역
        for area in ['M', 'I', 'Q']:
            if address.startswith(area) or memory.upper() == area:
                if memory.upper() == area:
                    byte_offset = int(address)
                else:
                    if address[1] in 'WBDX':
                        if address[1] == 'X':
                            bit_parts = address[2:].split('.')
                            byte_offset = int(bit_parts[0])
                            bit_offset = int(bit_parts[1]) if len(bit_parts) > 1 else 0
                        else:
                            byte_offset = int(address[2:])
                    else:
                        byte_offset = int(address[1:])
                return area, 0, byte_offset, bit_offset

        return 'M', 0, int(address), 0

    def _parse_value(
        self,
        byte_dict: Dict[int, int],
        byte_offset: int,
        bit_offset: int,
        data_type: DataType,
        word_length: Optional[int] = None
    ) -> Optional[Any]:
        """
        바이트 데이터에서 값 파싱.

        Args:
            byte_dict: {바이트오프셋: 바이트값} 딕셔너리
            byte_offset: 시작 바이트 오프셋
            bit_offset: 비트 오프셋 (BOOL용)
            data_type: 데이터 타입
            word_length: 문자열 길이

        Returns:
            파싱된 값, 실패 시 None
        """
        try:
            if data_type == DataType.BOOL:
                return self._parse_bool(byte_dict, byte_offset, bit_offset)
            elif data_type == DataType.INT8:
                return self._parse_int8(byte_dict, byte_offset)
            elif data_type == DataType.UINT8:
                return self._parse_uint8(byte_dict, byte_offset)
            elif data_type == DataType.INT16:
                return self._parse_int16(byte_dict, byte_offset)
            elif data_type in (DataType.UINT16, DataType.WORD):
                return self._parse_uint16(byte_dict, byte_offset)
            elif data_type == DataType.INT32:
                return self._parse_int32(byte_dict, byte_offset)
            elif data_type in (DataType.UINT32, DataType.DWORD):
                return self._parse_uint32(byte_dict, byte_offset)
            elif data_type == DataType.FLOAT32:
                return self._parse_float32(byte_dict, byte_offset)
            elif data_type == DataType.FLOAT64:
                return self._parse_float64(byte_dict, byte_offset)
            elif data_type == DataType.STRING:
                return self._parse_string(byte_dict, byte_offset, word_length or 10)
            else:
                return self._parse_uint16(byte_dict, byte_offset)
        except Exception as e:
            logger.debug(f"Parse error at offset {byte_offset}: {e}")
            return None

    def _get_bytes(
        self,
        byte_dict: Dict[int, int],
        start: int,
        count: int
    ) -> Optional[bytes]:
        """연속 바이트 추출."""
        try:
            return bytes([byte_dict[start + i] for i in range(count)])
        except KeyError:
            return None

    def _parse_bool(
        self,
        byte_dict: Dict[int, int],
        byte_offset: int,
        bit_offset: int
    ) -> Optional[bool]:
        """BOOL 파싱."""
        if byte_offset not in byte_dict:
            return None
        byte_val = byte_dict[byte_offset]
        return bool(byte_val & (1 << bit_offset))

    def _parse_int8(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """INT8 파싱."""
        raw = self._get_bytes(byte_dict, offset, 1)
        if raw is None:
            return None
        return struct.unpack('>b', raw)[0]

    def _parse_uint8(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """UINT8 파싱."""
        if offset not in byte_dict:
            return None
        return byte_dict[offset]

    def _parse_int16(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """INT16 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 2)
        if raw is None:
            return None
        return struct.unpack('>h', raw)[0]

    def _parse_uint16(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """UINT16 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 2)
        if raw is None:
            return None
        return struct.unpack('>H', raw)[0]

    def _parse_int32(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """INT32 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 4)
        if raw is None:
            return None
        return struct.unpack('>i', raw)[0]

    def _parse_uint32(self, byte_dict: Dict[int, int], offset: int) -> Optional[int]:
        """UINT32 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 4)
        if raw is None:
            return None
        return struct.unpack('>I', raw)[0]

    def _parse_float32(self, byte_dict: Dict[int, int], offset: int) -> Optional[float]:
        """FLOAT32 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 4)
        if raw is None:
            return None
        return struct.unpack('>f', raw)[0]

    def _parse_float64(self, byte_dict: Dict[int, int], offset: int) -> Optional[float]:
        """FLOAT64 파싱 (Big Endian)."""
        raw = self._get_bytes(byte_dict, offset, 8)
        if raw is None:
            return None
        return struct.unpack('>d', raw)[0]

    def _parse_string(
        self,
        byte_dict: Dict[int, int],
        offset: int,
        max_length: int
    ) -> Optional[str]:
        """
        S7 String 파싱.

        S7 String 구조:
            - Byte 0: 최대 길이
            - Byte 1: 실제 길이
            - Byte 2+: 문자 데이터

        Args:
            byte_dict: 바이트 딕셔너리
            offset: 시작 오프셋
            max_length: 최대 길이

        Returns:
            파싱된 문자열
        """
        # 먼저 처음 2바이트로 실제 길이 확인 시도
        if offset in byte_dict and (offset + 1) in byte_dict:
            actual_length = byte_dict[offset + 1]
            if actual_length <= 254:
                raw = self._get_bytes(byte_dict, offset + 2, min(actual_length, max_length))
                if raw:
                    try:
                        return raw.decode('ascii', errors='replace').strip('\x00')
                    except Exception as e:
                        logger.warning(f"String decode error at offset {offset}: {e}")

        # 실패시 고정 길이로 읽기
        raw = self._get_bytes(byte_dict, offset, max_length)
        if raw is None:
            return None

        try:
            null_idx = raw.find(0)
            if null_idx != -1:
                raw = raw[:null_idx]
            return raw.decode('ascii', errors='replace').strip()
        except Exception:
            return raw.decode('utf-8', errors='replace').strip()
