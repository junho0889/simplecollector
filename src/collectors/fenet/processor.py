"""
FENET Data Processor
====================

LS Electric XGT PLC에서 수집된 레지스터 데이터를 파싱합니다.

Features:
    - 다양한 데이터 타입 지원
    - 디바이스 타입별 파싱 (D, M, T, C 등)
    - 스케일링 및 오프셋 적용
    - 문자열 데이터 처리

Example:
    processor = FenetProcessor("fenet_processor")
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


class FenetProcessor(BaseProcessor):
    """
    FENET (LS Electric XGT) 데이터 처리기.

    수집된 디바이스 데이터를 파싱하여 실제 값으로 변환합니다.

    Data Type Parsing:
        - BOOL: 비트 값 (0/1)
        - INT16: 부호 있는 16비트 정수
        - UINT16: 부호 없는 16비트 정수
        - INT32: 부호 있는 32비트 정수 (2 워드)
        - UINT32: 부호 없는 32비트 정수 (2 워드)
        - FLOAT32: IEEE 754 단정밀도 부동소수점 (2 워드)
        - STRING: ASCII 문자열
    """

    # 워드 디바이스
    WORD_DEVICES = {'D', 'T', 'C', 'U', 'Z', 'R'}
    # 비트 디바이스
    BIT_DEVICES = {'P', 'M', 'K', 'F', 'L'}

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
        디바이스 데이터 파싱.

        Args:
            data: 수집된 데이터 (metadata에 registers 딕셔너리 포함)
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []

        # 레지스터 데이터 추출
        registers = data.metadata.get('registers', {})

        for tag in tags:
            try:
                # 디바이스와 주소 파싱
                device, address = self._parse_address(tag.address, tag.memory)

                # 해당 디바이스의 레지스터 가져오기
                device_regs = registers.get(device, {})

                # 값 파싱
                value = self._parse_value(
                    device_regs,
                    address,
                    tag.data_type,
                    device,
                    tag.word_length
                )

                if value is not None:
                    results.append((tag, value))
                else:
                    logger.warning(
                        f"[{self._name}] Failed to parse tag {tag.tag_id} "
                        f"at {device}{address}"
                    )

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error parsing tag {tag.tag_id}: {e}"
                )

        return results

    def _parse_address(self, address: str, memory: str = "") -> Tuple[str, int]:
        """
        주소 문자열 파싱.

        Args:
            address: 주소 문자열 (예: "100" 또는 "D100")
            memory: 메모리 영역 (CSV에서 분리된 경우)

        Returns:
            (디바이스 타입, 주소) 튜플
        """
        address = address.strip().upper()

        if memory:
            device = memory.upper()
            addr_num = int(address)
        else:
            # 주소에서 디바이스 타입 추출
            for dev in ['D', 'M', 'T', 'C', 'P', 'L', 'K', 'F', 'U', 'Z', 'R']:
                if address.startswith(dev):
                    device = dev
                    addr_num = int(address[len(dev):])
                    break
            else:
                device = 'D'
                addr_num = int(address)

        return device, addr_num

    def _parse_value(
        self,
        registers: Dict[int, int],
        address: int,
        data_type: DataType,
        device: str,
        word_length: Optional[int] = None
    ) -> Optional[Any]:
        """
        레지스터에서 값 파싱.

        Args:
            registers: {주소: 값} 딕셔너리
            address: 시작 주소
            data_type: 데이터 타입
            device: 디바이스 타입
            word_length: 문자열 길이 (워드 단위)

        Returns:
            파싱된 값, 실패 시 None
        """
        try:
            if data_type == DataType.BOOL:
                return self._parse_bool(registers, address)
            elif data_type == DataType.INT16:
                return self._parse_int16(registers, address)
            elif data_type in (DataType.UINT16, DataType.WORD):
                return self._parse_uint16(registers, address)
            elif data_type == DataType.INT32:
                return self._parse_int32(registers, address)
            elif data_type in (DataType.UINT32, DataType.DWORD):
                return self._parse_uint32(registers, address)
            elif data_type == DataType.FLOAT32:
                return self._parse_float32(registers, address)
            elif data_type == DataType.FLOAT64:
                return self._parse_float64(registers, address)
            elif data_type == DataType.STRING:
                return self._parse_string(registers, address, word_length or 1)
            else:
                return self._parse_uint16(registers, address)
        except Exception as e:
            logger.debug(f"Parse error at {device}{address}: {e}")
            return None

    def _parse_bool(self, registers: Dict[int, int], address: int) -> Optional[bool]:
        """BOOL 파싱."""
        if address not in registers:
            return None
        return registers[address] != 0

    def _parse_int16(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """INT16 파싱 (부호 있는 16비트)."""
        if address not in registers:
            return None

        value = registers[address]
        if value >= 0x8000:
            value -= 0x10000
        return value

    def _parse_uint16(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """UINT16 파싱 (부호 없는 16비트)."""
        if address not in registers:
            return None
        return registers[address]

    def _parse_int32(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """INT32 파싱 (부호 있는 32비트, 2 워드, Little Endian)."""
        if address not in registers or (address + 1) not in registers:
            return None

        low = registers[address]
        high = registers[address + 1]

        # LS Electric는 Little Endian Word Order
        data = struct.pack('<HH', low, high)
        return struct.unpack('<i', data)[0]

    def _parse_uint32(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """UINT32 파싱 (부호 없는 32비트, 2 워드, Little Endian)."""
        if address not in registers or (address + 1) not in registers:
            return None

        low = registers[address]
        high = registers[address + 1]

        data = struct.pack('<HH', low, high)
        return struct.unpack('<I', data)[0]

    def _parse_float32(self, registers: Dict[int, int], address: int) -> Optional[float]:
        """FLOAT32 파싱 (IEEE 754 단정밀도, 2 워드)."""
        if address not in registers or (address + 1) not in registers:
            return None

        low = registers[address]
        high = registers[address + 1]

        data = struct.pack('<HH', low, high)
        return struct.unpack('<f', data)[0]

    def _parse_float64(self, registers: Dict[int, int], address: int) -> Optional[float]:
        """FLOAT64 파싱 (IEEE 754 배정밀도, 4 워드)."""
        required = [address + i for i in range(4)]
        if not all(addr in registers for addr in required):
            return None

        words = [registers[addr] for addr in required]
        data = struct.pack('<HHHH', *words)
        return struct.unpack('<d', data)[0]

    def _parse_string(
        self,
        registers: Dict[int, int],
        address: int,
        word_length: int
    ) -> Optional[str]:
        """
        STRING 파싱.

        Args:
            registers: 레지스터 딕셔너리
            address: 시작 주소
            word_length: 워드 수 (1워드 = 2바이트)

        Returns:
            파싱된 문자열
        """
        required = [address + i for i in range(word_length)]
        if not all(addr in registers for addr in required):
            return None

        # 워드를 바이트로 변환
        raw_bytes = bytearray()
        for addr in required:
            word = registers[addr]
            # Little Endian: low byte first
            raw_bytes.append(word & 0xFF)
            raw_bytes.append((word >> 8) & 0xFF)

        # NULL 문자까지 또는 전체를 문자열로 변환
        try:
            null_idx = raw_bytes.find(0)
            if null_idx != -1:
                raw_bytes = raw_bytes[:null_idx]
            return raw_bytes.decode('ascii', errors='replace').strip()
        except Exception as e:
            logger.verbose(f"ASCII decode failed, using UTF-8 fallback: {e}")
            return raw_bytes.decode('utf-8', errors='replace').strip()
