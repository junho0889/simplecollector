"""
Modbus Data Processor
=====================

Modbus 레지스터/비트 데이터를 파싱하고 스케일링을 적용합니다.

Features:
    - 4종 레지스터 타입 지원 (Coil, Discrete Input, Holding, Input)
    - 다양한 데이터 타입 지원 (INT16, UINT16, INT32, FLOAT32, STRING 등)
    - 바이트 오더 설정 (Big/Little Endian)
    - 워드 오더 설정 (32비트 이상 데이터용)
    - 효율적인 메모리 사용

Data Type Parsing:
    - BOOL: 레지스터/coil 값 != 0
    - INT16: 부호 있는 16비트 정수
    - UINT16: 부호 없는 16비트 정수
    - INT32: 부호 있는 32비트 정수 (2 레지스터)
    - UINT32: 부호 없는 32비트 정수 (2 레지스터)
    - FLOAT32: IEEE 754 단정밀도 부동소수점 (2 레지스터)
    - FLOAT64: IEEE 754 배정밀도 부동소수점 (4 레지스터)
    - STRING: ASCII 문자열 (N 레지스터, 레지스터당 2문자)

Example:
    processor = ModbusProcessor("modbus_processor")
    processor.register_tags_for_group("1sec", tags)
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


class ModbusProcessor(BaseProcessor):
    """
    Modbus 데이터 처리기.

    수집된 레지스터 데이터를 파싱하여 실제 값으로 변환합니다.

    Byte Order Options:
        - big: Big Endian (Modbus 표준)
        - little: Little Endian

    Word Order Options (32비트 이상):
        - big: 상위 워드가 낮은 주소
        - little: 하위 워드가 낮은 주소

    Attributes:
        byte_order: 바이트 순서 ('big' or 'little')
        word_order: 워드 순서 ('big' or 'little')
    """

    def __init__(
        self,
        name: str,
        byte_order: str = 'big',
        word_order: str = 'big',
    ):
        """
        Args:
            name: 처리기 이름
            byte_order: 바이트 순서 ('big' or 'little')
            word_order: 워드 순서 ('big' or 'little')
        """
        super().__init__(name)
        self.byte_order = byte_order
        self.word_order = word_order

        # struct 포맷 prefix
        self._byte_prefix = '>' if byte_order == 'big' else '<'

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        레지스터 데이터 파싱.

        Args:
            data: 수집된 데이터 (metadata에 registers 딕셔너리 포함)
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []

        # 레지스터 데이터 추출
        registers = data.metadata.get('registers', {})
        coil_regs = registers.get('coil', {})
        discrete_regs = registers.get('discrete', {})
        holding_regs = registers.get('holding', {})
        input_regs = registers.get('input', {})

        reg_map = {
            'coil': coil_regs,
            'discrete': discrete_regs,
            'holding': holding_regs,
            'input': input_regs,
        }

        for tag in tags:
            try:
                # 주소 파싱
                address, reg_type = self._parse_address(tag.address)

                # 레지스터 선택
                regs = reg_map.get(reg_type, holding_regs)

                # 값 파싱
                value = self._parse_value(regs, address, tag.data_type, tag)

                if value is not None:
                    results.append((tag, value))
                else:
                    logger.warning(
                        f"[{self._name}] Failed to parse tag {tag.tag_id} "
                        f"at address {tag.address}"
                    )

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error parsing tag {tag.tag_id}: {e}"
                )

        return results

    def _parse_address(self, address: str) -> Tuple[int, str]:
        """
        주소 문자열 파싱.

        Args:
            address: 주소 문자열

        Returns:
            (레지스터 주소, 레지스터 타입) 튜플
            타입: 'holding', 'input', 'coil', 'discrete'
        """
        address = address.strip().upper()

        # DI를 D보다 먼저 검사 (longest prefix match)
        if address.startswith('DI'):
            return int(address[2:]), 'discrete'
        elif address.startswith('C'):
            return int(address[1:]), 'coil'
        elif address.startswith('D'):
            return int(address[1:]), 'holding'
        elif address.startswith('I'):
            return int(address[1:]), 'input'
        elif address.startswith('HR'):
            return int(address[2:]), 'holding'
        elif address.startswith('IR'):
            return int(address[2:]), 'input'
        else:
            return int(address), 'holding'

    def _parse_value(
        self,
        registers: Dict[int, int],
        address: int,
        data_type: DataType,
        tag: Optional[TagDefinition] = None,
    ) -> Optional[Any]:
        """
        레지스터에서 값 파싱.

        Args:
            registers: {주소: 값} 딕셔너리
            address: 시작 주소
            data_type: 데이터 타입
            tag: 태그 정의 (STRING의 word_length 참조용)

        Returns:
            파싱된 값, 실패 시 None
        """
        try:
            if data_type == DataType.BOOL:
                return self._parse_bool(registers, address)
            elif data_type == DataType.INT16:
                return self._parse_int16(registers, address)
            elif data_type == DataType.UINT16:
                return self._parse_uint16(registers, address)
            elif data_type == DataType.INT32:
                return self._parse_int32(registers, address)
            elif data_type == DataType.UINT32:
                return self._parse_uint32(registers, address)
            elif data_type == DataType.FLOAT32:
                return self._parse_float32(registers, address)
            elif data_type == DataType.FLOAT64:
                return self._parse_float64(registers, address)
            elif data_type == DataType.STRING:
                return self._parse_string(registers, address, tag)
            else:
                # 기본값: UINT16
                return self._parse_uint16(registers, address)
        except Exception as e:
            logger.debug(f"Parse error at {address}: {e}")
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
        # 부호 변환
        if value >= 0x8000:
            value -= 0x10000
        return value

    def _parse_uint16(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """UINT16 파싱 (부호 없는 16비트)."""
        if address not in registers:
            return None
        return registers[address]

    def _parse_int32(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """INT32 파싱 (부호 있는 32비트, 2 레지스터)."""
        if address not in registers or (address + 1) not in registers:
            return None

        high = registers[address]
        low = registers[address + 1]

        # 워드 오더 적용
        if self.word_order == 'little':
            high, low = low, high

        # 바이트로 변환 후 파싱
        data = struct.pack('>HH', high, low)
        return struct.unpack(f'{self._byte_prefix}i', data)[0]

    def _parse_uint32(self, registers: Dict[int, int], address: int) -> Optional[int]:
        """UINT32 파싱 (부호 없는 32비트, 2 레지스터)."""
        if address not in registers or (address + 1) not in registers:
            return None

        high = registers[address]
        low = registers[address + 1]

        if self.word_order == 'little':
            high, low = low, high

        data = struct.pack('>HH', high, low)
        return struct.unpack(f'{self._byte_prefix}I', data)[0]

    def _parse_float32(self, registers: Dict[int, int], address: int) -> Optional[float]:
        """FLOAT32 파싱 (IEEE 754 단정밀도, 2 레지스터)."""
        if address not in registers or (address + 1) not in registers:
            return None

        high = registers[address]
        low = registers[address + 1]

        if self.word_order == 'little':
            high, low = low, high

        data = struct.pack('>HH', high, low)
        return struct.unpack(f'{self._byte_prefix}f', data)[0]

    def _parse_float64(self, registers: Dict[int, int], address: int) -> Optional[float]:
        """FLOAT64 파싱 (IEEE 754 배정밀도, 4 레지스터)."""
        required = [address + i for i in range(4)]
        if not all(addr in registers for addr in required):
            return None

        words = [registers[addr] for addr in required]

        if self.word_order == 'little':
            words = list(reversed(words))

        data = struct.pack('>HHHH', *words)
        return struct.unpack(f'{self._byte_prefix}d', data)[0]

    def _parse_string(
        self,
        registers: Dict[int, int],
        address: int,
        tag: Optional[TagDefinition] = None,
    ) -> Optional[str]:
        """
        STRING 파싱 (레지스터 → ASCII 문자열).

        Modbus 표준: 1 레지스터(16비트) = 2 ASCII 문자 (big-endian, 상위바이트 먼저).
        word_length로 읽을 레지스터 수를 결정합니다.

        Args:
            registers: {주소: 값} 딕셔너리
            address: 시작 주소
            tag: 태그 정의 (word_length 참조)

        Returns:
            파싱된 문자열, 실패 시 None
        """
        word_count = 1
        if tag and tag.word_length:
            word_count = tag.word_length

        if address not in registers:
            return None

        chars: list = []
        for i in range(word_count):
            addr = address + i
            if addr not in registers:
                break
            reg_val = registers[addr]
            high_byte = (reg_val >> 8) & 0xFF
            low_byte = reg_val & 0xFF
            if high_byte:
                chars.append(chr(high_byte))
            if low_byte:
                chars.append(chr(low_byte))

        return ''.join(chars).rstrip('\x00')
