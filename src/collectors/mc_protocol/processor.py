"""
MC Protocol Data Processor
==========================

MC Protocol로 수집된 원시 데이터를 처리합니다.

Features:
    - 다양한 데이터 타입 변환 (INT16, UINT16, INT32, FLOAT32 등)
    - 바이트 오더 처리 (리틀 엔디안 기본)
    - 비트 디바이스 처리 (M, X, Y)
    - 태그별 스케일링/오프셋

Data Types:
    - BOOL: 비트 값 (0/1)
    - INT16: 부호 있는 16비트 정수
    - UINT16: 부호 없는 16비트 정수
    - INT32: 부호 있는 32비트 정수 (2워드)
    - UINT32: 부호 없는 32비트 정수 (2워드)
    - FLOAT32: 32비트 부동소수점 (2워드)
    - FLOAT64: 64비트 부동소수점 (4워드)

Example:
    processor = McProtocolProcessor("processor_1")
    processed_list = await processor.process(collected_data)
"""

import json
import struct
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...processors.base import BaseProcessor
from ...core.interfaces import CollectedData, ProcessedData, TagDefinition, DataType
from ...utils.logging import LoggerFactory, VERBOSE

logger = LoggerFactory.get_collection_logger()


class McProtocolProcessor(BaseProcessor):
    """
    MC Protocol 데이터 처리기.

    수집된 레지스터 데이터를 태그 정의에 따라 변환합니다.

    Byte Order:
        미쓰비시 PLC는 기본적으로 리틀 엔디안을 사용합니다.
        - 워드 내: 리틀 엔디안
        - 32비트 값: 하위 워드 먼저 (기본)

    Attributes:
        _word_order: 32비트 값의 워드 순서 ('little' or 'big')
    """

    def __init__(
        self,
        name: str,
        word_order: str = 'little',  # 미쓰비시 기본값
    ):
        """
        Args:
            name: 처리기 이름
            word_order: 32비트 값의 워드 순서 ('little': 하위 먼저, 'big': 상위 먼저)
        """
        super().__init__(name)
        self._word_order = word_order.lower()

        # 그룹별 태그 정보
        self._tag_map: Dict[str, Dict[str, List[TagDefinition]]] = {}

    def register_tags(self, group: str, tags: List[TagDefinition]) -> None:
        """
        태그 등록.

        Args:
            group: 수집 그룹명
            tags: 태그 정의 리스트
        """
        # 디바이스별로 태그 분류
        device_tags: Dict[str, List[TagDefinition]] = {}

        for tag in tags:
            device = self._extract_device(tag.address)
            if device not in device_tags:
                device_tags[device] = []
            device_tags[device].append(tag)

        self._tag_map[group] = device_tags
        logger.debug(f"[{self._name}] Registered {len(tags)} tags for group '{group}'")

    def _extract_device(self, address: str) -> str:
        """주소에서 디바이스 타입 추출."""
        address = address.strip().upper()
        if address.startswith('ZR'):
            return 'ZR'
        return address[0]

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        MC Protocol 원시 데이터 파싱.

        Args:
            data: 수집된 데이터 (metadata에 devices 딕셔너리 포함)
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        devices = data.metadata.get('devices', {})

        if not devices:
            return []

        results: List[Tuple[TagDefinition, Any]] = []

        for tag in tags:
            try:
                value = self._extract_value(tag, devices)

                if value is not None:
                    results.append((tag, value))

            except Exception as e:
                logger.warning(
                    f"[{self._name}] Failed to parse tag {tag.tag_id} "
                    f"({tag.address}): {e}"
                )

        logger.debug(
            f"[{self._name}] Parsed {len(results)} values from group "
            f"'{data.collection_group}'"
        )

        # VERBOSE: 그룹 전체 파싱 결과를 JSON으로 출력
        if results and logger.isEnabledFor(VERBOSE):
            snapshot = {tag.tag_name: value for tag, value in results}
            logger.log(
                VERBOSE,
                f"[{self._name}] [{data.collection_group}] "
                f"{json.dumps(snapshot, ensure_ascii=False)}"
            )

        return results

    def _extract_value(
        self,
        tag: TagDefinition,
        devices: Dict[str, Dict[int, int]]
    ) -> Optional[Any]:
        """
        태그에 해당하는 값 추출.

        Args:
            tag: 태그 정의
            devices: {디바이스: {주소: 값}} 딕셔너리

        Returns:
            추출된 값 (숫자 또는 문자열)
        """
        device, address, word_count = self._parse_address_with_length(tag.address)
        device_data = devices.get(device, {})

        if not device_data:
            return None

        data_type = tag.data_type

        # 데이터 타입에 따른 값 추출
        if data_type == DataType.BOOL:
            value = self._extract_bool(device_data, address, device)
        elif data_type == DataType.INT16:
            raw = device_data.get(address)
            value = struct.unpack('<h', struct.pack('<H', raw))[0] if raw is not None else None
        elif data_type == DataType.UINT16:
            value = device_data.get(address)
        elif data_type == DataType.INT32:
            value = self._extract_int32(device_data, address)
        elif data_type == DataType.UINT32:
            value = self._extract_uint32(device_data, address)
        elif data_type == DataType.FLOAT32:
            value = self._extract_float32(device_data, address)
        elif data_type == DataType.FLOAT64:
            value = self._extract_float64(device_data, address)
        elif data_type == DataType.STRING:
            value = self._extract_string(device_data, address, word_count)
        else:
            value = None

        return value

    def _parse_address(self, address: str) -> Tuple[str, int]:
        """
        주소 문자열 파싱.

        R시리즈 주소 형식:
            - D0, D100: 데이터 레지스터 (10진수)
            - M0, M100: 내부 릴레이 (10진수)
            - L0, L100: 래치 릴레이 (10진수)
            - X0, X1F: 입력 (16진수)
            - Y0, Y1F: 출력 (16진수)
            - W0, W1A: 링크 레지스터 (16진수)
            - R0, R100: 파일 레지스터 (10진수)
            - ZR0: 확장 파일 레지스터 (10진수)
        """
        device, addr, _ = self._parse_address_with_length(address)
        return device, addr

    def _parse_address_with_length(self, address: str) -> Tuple[str, int, int]:
        """
        주소 문자열 파싱 (길이 포함).

        주소 형식:
            - D100: 단일 워드
            - D100:15: D100부터 15워드 (STRING용)

        Returns:
            (디바이스, 주소, 워드 수) 튜플
        """
        address = address.strip().upper()

        # 길이 지정자 확인 (예: D100:15)
        word_count = 1
        if ':' in address:
            address, count_str = address.split(':', 1)
            try:
                word_count = int(count_str)
            except ValueError:
                word_count = 1

        if address.startswith('ZR'):
            device = 'ZR'
            addr_str = address[2:]
        else:
            device = address[0]
            addr_str = address[1:]

        # 16진수 주소 처리 (W, B, X, Y 디바이스) - R시리즈
        if device in ('W', 'B', 'X', 'Y'):
            addr = int(addr_str, 16)
        else:
            addr = int(addr_str, 10)

        return device, addr, word_count

    def _extract_bool(
        self,
        device_data: Dict[int, int],
        address: int,
        device: str
    ) -> Optional[float]:
        """
        비트 값 추출.

        비트 디바이스(M, X, Y 등)의 경우 워드로 읽어서 비트 추출.

        Args:
            device_data: 디바이스 데이터
            address: 비트 주소
            device: 디바이스 타입

        Returns:
            0.0 또는 1.0
        """
        # H-2: 디바이스 타입으로 분기. 수집기는 비트 디바이스(M/X/Y/L 등)를
        # 비트주소 키로 저장하므로 곧장 비트주소 조회해야 한다.
        # (이전: 무조건 word_addr=address//16 을 먼저 조회 → 같은 그룹에
        #  비트주소 16 이상 태그가 있으면 다른 비트주소(address//16)의 값과
        #  충돌해 알람 오발생/미발생)
        from .collector import DeviceCode

        if DeviceCode.is_bit_device(device):
            raw = device_data.get(address)
            if raw is not None:
                return float(raw & 1)
            return None

        # 워드 디바이스(D, W 등)의 특정 비트: 워드주소 + 비트위치 해석
        word_addr = address // 16
        bit_pos = address % 16

        raw = device_data.get(word_addr)
        if raw is not None:
            bit_value = (raw >> bit_pos) & 1
            return float(bit_value)

        return None

    def _extract_int32(self, device_data: Dict[int, int], address: int) -> Optional[int]:
        """
        32비트 부호 있는 정수 추출.

        Args:
            device_data: 디바이스 데이터
            address: 시작 주소

        Returns:
            INT32 값
        """
        low = device_data.get(address)
        high = device_data.get(address + 1)

        if low is None or high is None:
            return None

        if self._word_order == 'little':
            # 하위 워드 먼저 (미쓰비시 기본)
            raw = (high << 16) | low
        else:
            # 상위 워드 먼저
            raw = (low << 16) | high

        # 부호 있는 정수로 변환
        return struct.unpack('<i', struct.pack('<I', raw))[0]

    def _extract_uint32(self, device_data: Dict[int, int], address: int) -> Optional[int]:
        """
        32비트 부호 없는 정수 추출.

        Args:
            device_data: 디바이스 데이터
            address: 시작 주소

        Returns:
            UINT32 값
        """
        low = device_data.get(address)
        high = device_data.get(address + 1)

        if low is None or high is None:
            return None

        if self._word_order == 'little':
            return (high << 16) | low
        else:
            return (low << 16) | high

    def _extract_float32(self, device_data: Dict[int, int], address: int) -> Optional[float]:
        """
        32비트 부동소수점 추출.

        Args:
            device_data: 디바이스 데이터
            address: 시작 주소

        Returns:
            FLOAT32 값
        """
        low = device_data.get(address)
        high = device_data.get(address + 1)

        if low is None or high is None:
            return None

        if self._word_order == 'little':
            raw_bytes = struct.pack('<HH', low, high)
        else:
            raw_bytes = struct.pack('<HH', high, low)

        return struct.unpack('<f', raw_bytes)[0]

    def _extract_float64(self, device_data: Dict[int, int], address: int) -> Optional[float]:
        """
        64비트 부동소수점 추출.

        Args:
            device_data: 디바이스 데이터
            address: 시작 주소

        Returns:
            FLOAT64 값
        """
        words = [device_data.get(address + i) for i in range(4)]

        if any(w is None for w in words):
            return None

        if self._word_order == 'little':
            raw_bytes = struct.pack('<HHHH', *words)
        else:
            raw_bytes = struct.pack('<HHHH', words[3], words[2], words[1], words[0])

        return struct.unpack('<d', raw_bytes)[0]

    def _extract_string(
        self,
        device_data: Dict[int, int],
        address: int,
        word_count: int
    ) -> Optional[str]:
        """
        문자열 추출 (여러 워드에서).

        미쓰비시 PLC 문자열 형식:
            - 각 워드(16비트)에 2개의 ASCII 문자 저장
            - Little-endian: 하위 바이트가 첫 번째 문자
            - 예: 워드값 0x4241 → "AB" (0x41='A', 0x42='B')

        Args:
            device_data: 디바이스 데이터
            address: 시작 주소
            word_count: 읽을 워드 수 (문자 수 = word_count * 2)

        Returns:
            추출된 문자열 (NULL 종료 또는 전체 길이)
        """
        if word_count < 1:
            word_count = 1

        chars = []
        for i in range(word_count):
            word = device_data.get(address + i)
            if word is None:
                break

            # Little-endian: 하위 바이트 먼저
            low_byte = word & 0xFF
            high_byte = (word >> 8) & 0xFF

            # NULL(0x00) 종료 확인
            if low_byte == 0:
                break
            chars.append(chr(low_byte))

            if high_byte == 0:
                break
            chars.append(chr(high_byte))

        # 문자열 생성 및 정리
        result = ''.join(chars)

        # 제어 문자 제거 및 공백 정리
        result = result.rstrip('\x00').strip()

        return result if result else None
