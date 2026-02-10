"""
FENET (LS Electric XGT) Collector
==================================

LS Electric XGT 시리즈 PLC의 FENET 프로토콜을 통한 데이터 수집기입니다.

Features:
    - 비동기 TCP 연결
    - XGT 전용 프로토콜 지원
    - 자동 재연결
    - 연속 주소 병합 읽기

Protocol Details:
    - TCP 포트: 2004 (기본)
    - 프레임 구조: LSIS-XGT 전용
    - 디바이스: D, M, T, C, P, L, K, F, U 등

Example:
    collector = FenetCollector(
        plc_id=1,
        name="XGT_PLC",
        config=collector_config,
        event_bus=event_bus,
    )

    await collector.start()
"""

import asyncio
import struct
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
import logging

from ...collectors.base import BaseCollector
from ...core.interfaces import CollectedData, TagDefinition, DataType, ConnectionState
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class FenetCollector(BaseCollector):
    """
    LS Electric FENET (XGT) 데이터 수집기.

    XGT 전용 프로토콜을 사용하여 LS Electric PLC와 통신합니다.

    Supported Devices:
        - D: 데이터 레지스터 (워드)
        - M: 내부 릴레이 (비트)
        - T: 타이머 (워드)
        - C: 카운터 (워드)
        - P: 입력 접점 (비트)
        - L: 링크 릴레이 (비트)
        - K: 유지 릴레이 (비트)
        - F: 특수 릴레이 (비트)
        - U: 사용자 정의 (워드)

    Attributes:
        _reader: asyncio StreamReader
        _writer: asyncio StreamWriter
        _read_groups: 최적화된 읽기 그룹
        _invoke_id: 요청 ID (증가)
    """

    # LSIS-XGT 프로토콜 상수
    COMPANY_ID = b'LSIS-XGT'
    HEADER_SIZE = 20
    MAX_WORDS_PER_READ = 60  # 한 번에 읽을 수 있는 최대 워드 수

    # 명령 타입
    CMD_READ_REQUEST = 0x0054      # 읽기 요청
    CMD_READ_RESPONSE = 0x0055     # 읽기 응답
    CMD_WRITE_REQUEST = 0x0058    # 쓰기 요청
    CMD_WRITE_RESPONSE = 0x0059   # 쓰기 응답

    # 데이터 타입
    DATA_TYPE_CONTINUOUS = 0x0000  # 연속 읽기
    DATA_TYPE_INDIVIDUAL = 0x0001  # 개별 읽기

    # 디바이스 매핑
    DEVICE_CODES = {
        'P': 0x00,   # 입력 접점
        'M': 0x01,   # 내부 릴레이
        'K': 0x02,   # 유지 릴레이
        'F': 0x03,   # 특수 릴레이
        'T': 0x04,   # 타이머 (현재값)
        'C': 0x05,   # 카운터 (현재값)
        'D': 0x06,   # 데이터 레지스터
        'L': 0x07,   # 링크 릴레이
        'U': 0x08,   # 사용자 정의
        'Z': 0x09,   # 파일 레지스터
        'R': 0x0A,   # 파일 레지스터 (확장)
    }

    # 워드 디바이스 (16비트 단위)
    WORD_DEVICES = {'D', 'T', 'C', 'U', 'Z', 'R'}
    # 비트 디바이스
    BIT_DEVICES = {'P', 'M', 'K', 'F', 'L'}

    def __init__(
        self,
        plc_id: int,
        name: str,
        config: CollectorConfig,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Args:
            plc_id: PLC 고유 식별자
            name: 수집기 이름
            config: 수집기 설정
            event_bus: 이벤트 버스
        """
        super().__init__(plc_id, name, config, event_bus)

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_groups: Dict[str, List['FenetReadGroup']] = {}
        self._invoke_id: int = 0
        self._connection_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5

        # 프로토콜 설정 추출
        if self._protocol_config:
            self._host = self._protocol_config.host
            self._port = self._protocol_config.port or 2004
            self._timeout = self._protocol_config.timeout_ms / 1000.0

            extra = self._protocol_config.extra or {}
            self._plc_type = extra.get('plc_type', 'XGK')
        else:
            self._host = "127.0.0.1"
            self._port = 2004
            self._timeout = 5.0
            self._plc_type = 'XGK'

    def register_tags(self, group: str, tags: List[TagDefinition]) -> None:
        """
        태그 등록 및 읽기 그룹 최적화.

        Args:
            group: 수집 그룹명
            tags: 태그 정의 리스트
        """
        super().register_tags(group, tags)

        # 읽기 그룹 최적화
        self._read_groups[group] = self._optimize_read_groups(tags)

        logger.info(
            f"[{self._name}] Optimized {len(tags)} tags into "
            f"{len(self._read_groups[group])} read groups for '{group}'"
        )

    def _optimize_read_groups(self, tags: List[TagDefinition]) -> List['FenetReadGroup']:
        """
        태그들을 연속 주소 그룹으로 최적화.

        Args:
            tags: 태그 정의 리스트

        Returns:
            최적화된 FenetReadGroup 리스트
        """
        if not tags:
            return []

        # 디바이스 타입별로 분류
        device_tags: Dict[str, List[Tuple[int, TagDefinition, int]]] = {}

        for tag in tags:
            device, address = self._parse_address(tag.address, tag.memory)
            size = self._get_word_size(tag.data_type)

            if device not in device_tags:
                device_tags[device] = []
            device_tags[device].append((address, tag, size))

        # 각 디바이스별로 그룹화
        groups: List[FenetReadGroup] = []
        for device, tag_list in device_tags.items():
            groups.extend(self._create_read_groups(device, tag_list))

        return groups

    def _create_read_groups(
        self,
        device: str,
        tags: List[Tuple[int, TagDefinition, int]]
    ) -> List['FenetReadGroup']:
        """
        연속 주소 태그들을 FenetReadGroup으로 묶기.

        Args:
            device: 디바이스 타입 (D, M, T 등)
            tags: (주소, 태그, 크기) 튜플 리스트

        Returns:
            FenetReadGroup 리스트
        """
        if not tags:
            return []

        # 주소 순 정렬
        sorted_tags = sorted(tags, key=lambda x: x[0])
        groups: List[FenetReadGroup] = []

        current_group = FenetReadGroup(
            device=device,
            start_address=sorted_tags[0][0],
            is_bit_device=device in self.BIT_DEVICES,
        )
        current_group.add_tag(sorted_tags[0][0], sorted_tags[0][1], sorted_tags[0][2])

        max_gap = 10  # 연속으로 간주할 최대 간격

        for i in range(1, len(sorted_tags)):
            address, tag, size = sorted_tags[i]
            prev_end = current_group.end_address
            gap = address - prev_end
            total_size = address + size - current_group.start_address

            # 그룹에 포함할지 결정
            if gap <= max_gap and total_size <= self.MAX_WORDS_PER_READ:
                current_group.add_tag(address, tag, size)
            else:
                groups.append(current_group)
                current_group = FenetReadGroup(
                    device=device,
                    start_address=address,
                    is_bit_device=device in self.BIT_DEVICES,
                )
                current_group.add_tag(address, tag, size)

        groups.append(current_group)
        return groups

    def _parse_address(self, address: str, memory: str = "") -> Tuple[str, int]:
        """
        주소 문자열 파싱.

        Args:
            address: 주소 문자열 (예: "100", "D100")
            memory: 메모리 영역 (CSV에서 분리된 경우)

        Returns:
            (디바이스 타입, 주소) 튜플
        """
        address = address.strip().upper()

        # memory 필드가 있으면 사용
        if memory:
            device = memory.upper()
            addr_num = int(address)
        else:
            # 주소에서 디바이스 타입 추출
            for dev in self.DEVICE_CODES.keys():
                if address.startswith(dev):
                    device = dev
                    addr_num = int(address[len(dev):])
                    break
            else:
                # 디바이스 타입 없으면 D로 간주
                device = 'D'
                addr_num = int(address)

        return device, addr_num

    def _get_word_size(self, data_type: DataType) -> int:
        """
        데이터 타입에 따른 워드 크기 반환.

        Args:
            data_type: 데이터 타입

        Returns:
            필요한 워드 수
        """
        size_map = {
            DataType.BOOL: 1,
            DataType.INT16: 1,
            DataType.UINT16: 1,
            DataType.INT32: 2,
            DataType.UINT32: 2,
            DataType.FLOAT32: 2,
            DataType.FLOAT64: 4,
            DataType.STRING: 1,
        }
        return size_map.get(data_type, 1)

    def _get_next_invoke_id(self) -> int:
        """다음 요청 ID 반환."""
        self._invoke_id = (self._invoke_id + 1) % 65536
        return self._invoke_id

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        FENET TCP 연결.

        Returns:
            연결 성공 여부
        """
        async with self._connection_lock:
            try:
                # 기존 연결 정리
                if self._writer:
                    try:
                        self._writer.close()
                        await self._writer.wait_closed()
                    except Exception as e:
                        logger.verbose(f"[{self._name}] Error during connection cleanup: {e}")

                # 새 연결 생성
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._timeout
                )

                self._consecutive_failures = 0
                logger.info(
                    f"[{self._name}] Connected to {self._host}:{self._port} (FENET)"
                )
                return True

            except asyncio.TimeoutError:
                logger.error(
                    f"[{self._name}] Connection timeout to {self._host}:{self._port}"
                )
                return False
            except Exception as e:
                logger.error(f"[{self._name}] Connection error: {e}")
                return False

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        async with self._connection_lock:
            if self._writer:
                try:
                    self._writer.close()
                    await self._writer.wait_closed()
                except Exception as e:
                    logger.warning(f"[{self._name}] Disconnect error: {e}")
                finally:
                    self._reader = None
                    self._writer = None

    async def _do_health_check(self) -> bool:
        """연결 상태 확인."""
        if not self._writer or self._writer.is_closing():
            return False
        return True

    async def _ensure_connection(self) -> bool:
        """연결 상태 확인 및 필요시 재연결."""
        if self._writer and not self._writer.is_closing():
            return True

        logger.warning(f"[{self._name}] Connection lost, attempting reconnect...")
        self._state = ConnectionState.RECONNECTING

        success = await self._do_connect()
        if success:
            self._state = ConnectionState.CONNECTED
        else:
            self._state = ConnectionState.ERROR

        return success

    # =========================================================================
    # Protocol Implementation
    # =========================================================================

    def _build_read_request(
        self,
        device: str,
        start_address: int,
        count: int,
        is_bit: bool = False
    ) -> bytes:
        """
        FENET 읽기 요청 프레임 생성.

        Args:
            device: 디바이스 타입
            start_address: 시작 주소
            count: 읽을 개수
            is_bit: 비트 디바이스 여부

        Returns:
            요청 프레임 바이트
        """
        invoke_id = self._get_next_invoke_id()

        # 변수명 생성: %DW100 (워드), %MX100 (비트)
        suffix = 'X' if is_bit else 'W'
        var_name = f"%{device}{suffix}{start_address}".encode('ascii')
        var_name_len = len(var_name)

        # 데이터 영역
        # [변수명 길이:2] [변수명] [읽을 개수:2]
        data = struct.pack('<H', var_name_len) + var_name + struct.pack('<H', count)
        data_len = len(data)

        # 헤더 구성 (LSIS-XGT 프로토콜)
        header = bytearray(self.HEADER_SIZE)
        header[0:8] = self.COMPANY_ID           # Company ID
        header[8:10] = struct.pack('<H', 0x0000)  # Reserved
        header[10:12] = struct.pack('<H', 0x0000) # Reserved
        header[12:14] = struct.pack('<H', data_len + 4)  # Data length
        header[14:15] = struct.pack('B', 0x00)   # Block info (연속 읽기)
        header[15:16] = struct.pack('B', 0x33)   # Reserved
        header[16:18] = struct.pack('<H', invoke_id)  # Invoke ID
        header[18:20] = struct.pack('<H', 0x0054)     # Command (Read)

        # 전체 프레임
        frame = bytes(header) + struct.pack('<H', 1) + data  # Block count = 1

        return frame

    def _parse_read_response(self, response: bytes) -> Optional[List[int]]:
        """
        읽기 응답 파싱.

        Args:
            response: 응답 프레임

        Returns:
            워드 값 리스트, 실패 시 None
        """
        if len(response) < self.HEADER_SIZE:
            logger.error(f"[{self._name}] Response too short: {len(response)} bytes")
            return None

        # 헤더 파싱
        company_id = response[0:8]
        if company_id != self.COMPANY_ID:
            logger.error(f"[{self._name}] Invalid company ID in response")
            return None

        # 데이터 길이
        data_len = struct.unpack('<H', response[12:14])[0]

        # 명령 확인
        cmd = struct.unpack('<H', response[18:20])[0]
        if cmd != self.CMD_READ_RESPONSE:
            logger.error(f"[{self._name}] Unexpected command: 0x{cmd:04X}")
            return None

        # 데이터 추출 (헤더 이후)
        if len(response) < self.HEADER_SIZE + data_len:
            logger.error(f"[{self._name}] Incomplete response data")
            return None

        # 블록 카운트와 에러 코드 확인
        offset = self.HEADER_SIZE
        block_count = struct.unpack('<H', response[offset:offset+2])[0]
        offset += 2

        # 데이터 타입
        data_type = struct.unpack('<H', response[offset:offset+2])[0]
        offset += 2

        # 에러 코드
        error_code = struct.unpack('<H', response[offset:offset+2])[0]
        if error_code != 0:
            logger.error(f"[{self._name}] Read error code: 0x{error_code:04X}")
            return None
        offset += 2

        # 데이터 개수
        word_count = struct.unpack('<H', response[offset:offset+2])[0]
        offset += 2

        # 워드 데이터 추출
        words = []
        for i in range(word_count):
            if offset + 2 <= len(response):
                word = struct.unpack('<H', response[offset:offset+2])[0]
                words.append(word)
                offset += 2
            else:
                break

        return words

    # =========================================================================
    # Data Collection
    # =========================================================================

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """
        FENET 데이터 수집.

        Args:
            group: 수집 그룹명

        Returns:
            수집된 데이터
        """
        read_groups = self._read_groups.get(group, [])
        if not read_groups:
            logger.warning(f"[{self._name}] No read groups for '{group}'")
            return None

        # 연결 확인
        if not await self._ensure_connection():
            self._consecutive_failures += 1
            return None

        source_time = datetime.now()
        all_registers: Dict[str, Dict[int, int]] = {}

        try:
            # 각 ReadGroup 읽기
            for rg in read_groups:
                registers = await self._read_group(rg)
                if registers is not None:
                    if rg.device not in all_registers:
                        all_registers[rg.device] = {}
                    all_registers[rg.device].update(registers)
                else:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        logger.error(
                            f"[{self._name}] Too many failures, marking connection as error"
                        )
                        self._state = ConnectionState.ERROR
                    return None

            # 성공
            self._consecutive_failures = 0

            return CollectedData(
                source_time=source_time,
                collection_time=datetime.now(),
                plc_id=self._plc_id,
                raw_data=b'',
                collection_group=group,
                metadata={
                    'registers': all_registers,
                    'tags': self._tags.get(group, []),
                },
            )

        except Exception as e:
            logger.error(f"[{self._name}] Collection error: {e}")
            self._consecutive_failures += 1
            return None

    async def _read_group(self, read_group: 'FenetReadGroup') -> Optional[Dict[int, int]]:
        """
        단일 ReadGroup 읽기.

        Args:
            read_group: 읽기 그룹

        Returns:
            {주소: 값} 딕셔너리, 실패 시 None
        """
        try:
            count = read_group.word_count

            # 요청 프레임 생성
            request = self._build_read_request(
                device=read_group.device,
                start_address=read_group.start_address,
                count=count,
                is_bit=read_group.is_bit_device,
            )

            # 요청 전송
            self._writer.write(request)
            await self._writer.drain()

            # 응답 읽기
            response = await asyncio.wait_for(
                self._reader.read(1024),
                timeout=self._timeout
            )

            if not response:
                logger.error(f"[{self._name}] Empty response")
                return None

            # 응답 파싱
            words = self._parse_read_response(response)
            if words is None:
                return None

            # 결과를 딕셔너리로 변환
            registers: Dict[int, int] = {}
            for i, value in enumerate(words):
                registers[read_group.start_address + i] = value

            return registers

        except asyncio.TimeoutError:
            logger.error(
                f"[{self._name}] Timeout reading "
                f"{read_group.device}[{read_group.start_address}]"
            )
            self._state = ConnectionState.ERROR
            return None

        except Exception as e:
            logger.error(f"[{self._name}] Read error: {e}")
            return None


class FenetReadGroup:
    """
    최적화된 FENET 읽기 그룹.

    연속된 주소의 태그들을 하나의 그룹으로 묶어
    한 번의 요청으로 읽습니다.

    Attributes:
        device: 디바이스 타입 (D, M, T 등)
        start_address: 시작 주소
        is_bit_device: 비트 디바이스 여부
        tags: 포함된 태그 정보 리스트
        end_address: 끝 주소
    """

    __slots__ = ['device', 'start_address', 'is_bit_device', 'tags', 'end_address']

    def __init__(self, device: str, start_address: int, is_bit_device: bool):
        """
        Args:
            device: 디바이스 타입
            start_address: 시작 주소
            is_bit_device: 비트 디바이스 여부
        """
        self.device = device
        self.start_address = start_address
        self.is_bit_device = is_bit_device
        self.tags: List[Tuple[int, TagDefinition, int]] = []
        self.end_address = start_address

    def add_tag(self, address: int, tag: TagDefinition, size: int) -> None:
        """태그 추가."""
        self.tags.append((address, tag, size))
        self.end_address = max(self.end_address, address + size)

    @property
    def word_count(self) -> int:
        """읽어야 할 워드 수."""
        return self.end_address - self.start_address

    def __repr__(self) -> str:
        return (
            f"FenetReadGroup({self.device}[{self.start_address}:"
            f"{self.end_address}], tags={len(self.tags)})"
        )
