"""
S7 Protocol (Siemens) Collector
================================

Siemens S7 시리즈 PLC의 S7 프로토콜을 통한 데이터 수집기입니다.

Features:
    - 비동기 데이터 수집 (스레드 풀 활용)
    - DB, Input, Output, Memory, Timer, Counter 영역 접근
    - 자동 재연결
    - 연속 주소 병합 읽기

Protocol Details:
    - S7comm over ISO-on-TCP (RFC1006)
    - TCP 포트: 102
    - TSAP 기반 연결

Security Note:
    - S7-1200/1500: PUT/GET 통신 활성화 필요
    - TIA Portal에서 "Permit access with PUT/GET" 체크

Example:
    collector = S7Collector(
        plc_id=1,
        name="S7_PLC",
        config=collector_config,
        event_bus=event_bus,
    )

    await collector.start()
"""

import asyncio
import struct
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from concurrent.futures import ThreadPoolExecutor
import logging

try:
    import snap7
    from snap7.util import get_bool, get_int, get_uint, get_real, get_dword, get_string
    SNAP7_AVAILABLE = True
except ImportError:
    SNAP7_AVAILABLE = False

from ...collectors.base import BaseCollector
from ...core.interfaces import CollectedData, TagDefinition, DataType, ConnectionState
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class S7Collector(BaseCollector):
    """
    Siemens S7 데이터 수집기.

    snap7 라이브러리를 사용하여 S7 프로토콜 통신을 수행합니다.

    Supported Areas:
        - DB: 데이터 블록
        - I: 입력 (Input)
        - Q: 출력 (Output)
        - M: 메모리 (Merker)
        - T: 타이머
        - C: 카운터

    Address Format:
        - DB1.DBW0: DB1의 워드 0
        - DB1.DBD10: DB1의 더블워드 10
        - DB1.DBX0.0: DB1의 비트 0.0
        - MW100: 메모리 워드 100
        - IW0: 입력 워드 0
        - QW0: 출력 워드 0

    Attributes:
        _client: snap7 클라이언트
        _read_groups: 최적화된 읽기 그룹
        _executor: 스레드 풀 (동기 snap7 호출용)
    """

    # S7 영역 코드
    AREA_CODES = {
        'DB': 0x84,  # Data Block
        'I': 0x81,   # Input
        'Q': 0x82,   # Output
        'M': 0x83,   # Memory (Merker)
        'T': 0x1D,   # Timer
        'C': 0x1C,   # Counter
    }

    # 한 번에 읽을 수 있는 최대 바이트 수
    MAX_BYTES_PER_READ = 200

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
        if not SNAP7_AVAILABLE:
            raise ImportError(
                "snap7 library is required for S7 Protocol. "
                "Install with: pip install python-snap7"
            )

        super().__init__(plc_id, name, config, event_bus)

        self._client: Optional[snap7.client.Client] = None
        self._read_groups: Dict[str, List['S7ReadGroup']] = {}
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._connection_lock = asyncio.Lock()
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5

        # 프로토콜 설정 추출
        if self._protocol_config:
            self._host = self._protocol_config.host
            self._port = self._protocol_config.port or 102
            self._timeout = self._protocol_config.timeout_ms / 1000.0

            extra = self._protocol_config.extra or {}
            self._rack = extra.get('rack', 0)
            self._slot = extra.get('slot', 1)  # S7-1500: slot=0, S7-300/400: slot=2
            self._plc_type = extra.get('plc_type', 'S7-1500')
        else:
            self._host = "127.0.0.1"
            self._port = 102
            self._timeout = 5.0
            self._rack = 0
            self._slot = 1
            self._plc_type = 'S7-1500'

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

    def _optimize_read_groups(self, tags: List[TagDefinition]) -> List['S7ReadGroup']:
        """
        태그들을 읽기 그룹으로 최적화.

        Args:
            tags: 태그 정의 리스트

        Returns:
            최적화된 S7ReadGroup 리스트
        """
        if not tags:
            return []

        # DB별, 영역별로 분류
        db_tags: Dict[Tuple[str, int], List[Tuple[int, TagDefinition, int]]] = {}

        for tag in tags:
            area, db_num, byte_offset, bit_offset = self._parse_address(
                tag.address, tag.memory
            )
            size = self._get_byte_size(tag.data_type, tag.word_length)

            key = (area, db_num)
            if key not in db_tags:
                db_tags[key] = []
            db_tags[key].append((byte_offset, tag, size))

        # 각 영역별로 그룹화
        groups: List[S7ReadGroup] = []
        for (area, db_num), tag_list in db_tags.items():
            groups.extend(self._create_read_groups(area, db_num, tag_list))

        return groups

    def _create_read_groups(
        self,
        area: str,
        db_num: int,
        tags: List[Tuple[int, TagDefinition, int]]
    ) -> List['S7ReadGroup']:
        """
        연속 주소 태그들을 S7ReadGroup으로 묶기.

        Args:
            area: 영역 타입 (DB, M, I, Q)
            db_num: DB 번호 (DB 영역인 경우)
            tags: (바이트 오프셋, 태그, 크기) 튜플 리스트

        Returns:
            S7ReadGroup 리스트
        """
        if not tags:
            return []

        sorted_tags = sorted(tags, key=lambda x: x[0])
        groups: List[S7ReadGroup] = []

        current_group = S7ReadGroup(
            area=area,
            db_number=db_num,
            start_byte=sorted_tags[0][0],
        )
        current_group.add_tag(sorted_tags[0][0], sorted_tags[0][1], sorted_tags[0][2])

        max_gap = 20  # 연속으로 간주할 최대 간격 (바이트)

        for i in range(1, len(sorted_tags)):
            byte_offset, tag, size = sorted_tags[i]
            prev_end = current_group.end_byte
            gap = byte_offset - prev_end
            total_size = byte_offset + size - current_group.start_byte

            if gap <= max_gap and total_size <= self.MAX_BYTES_PER_READ:
                current_group.add_tag(byte_offset, tag, size)
            else:
                groups.append(current_group)
                current_group = S7ReadGroup(
                    area=area,
                    db_number=db_num,
                    start_byte=byte_offset,
                )
                current_group.add_tag(byte_offset, tag, size)

        groups.append(current_group)
        return groups

    def _parse_address(
        self,
        address: str,
        memory: str = ""
    ) -> Tuple[str, int, int, int]:
        """
        S7 주소 문자열 파싱.

        Formats:
            - DB1.DBW0 → area='DB', db_num=1, byte=0, bit=0
            - DB1.DBX0.1 → area='DB', db_num=1, byte=0, bit=1
            - MW100 → area='M', db_num=0, byte=100, bit=0
            - IW0 → area='I', db_num=0, byte=0, bit=0

        Args:
            address: 주소 문자열
            memory: 메모리 영역 (CSV에서 분리된 경우)

        Returns:
            (영역, DB번호, 바이트오프셋, 비트오프셋) 튜플
        """
        address = address.strip().upper()
        bit_offset = 0

        # DB 주소: DB1.DBW0, DB1.DBX0.1
        if address.startswith('DB') or memory.upper() == 'DB':
            if memory.upper() == 'DB':
                # memory가 DB이고 address가 "1.DBW0" 형식
                parts = address.split('.')
                db_num = int(parts[0])
                if len(parts) > 1:
                    offset_str = parts[1]
                else:
                    offset_str = "0"
            else:
                # address가 "DB1.DBW0" 형식
                db_part, offset_part = address.split('.', 1)
                db_num = int(db_part[2:])
                offset_str = offset_part

            # DBW, DBD, DBX 파싱
            if offset_str.startswith('DBX'):
                # 비트 주소: DBX0.1
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

        # 메모리 영역: MW100, IW0, QW0
        for area in ['M', 'I', 'Q']:
            if address.startswith(area) or memory.upper() == area:
                if memory.upper() == area:
                    byte_offset = int(address)
                else:
                    # MW100, MB100, MD100
                    if address[1] in 'WBDX':
                        if address[1] == 'X':
                            # 비트 주소: MX0.1
                            bit_parts = address[2:].split('.')
                            byte_offset = int(bit_parts[0])
                            bit_offset = int(bit_parts[1]) if len(bit_parts) > 1 else 0
                        else:
                            byte_offset = int(address[2:])
                    else:
                        byte_offset = int(address[1:])
                return area, 0, byte_offset, bit_offset

        # 기본값: 메모리 영역
        return 'M', 0, int(address), 0

    def _get_byte_size(self, data_type: DataType, word_length: Optional[int] = None) -> int:
        """
        데이터 타입에 따른 바이트 크기 반환.

        Args:
            data_type: 데이터 타입
            word_length: 문자열 길이

        Returns:
            바이트 크기
        """
        size_map = {
            DataType.BOOL: 1,
            DataType.INT8: 1,
            DataType.UINT8: 1,
            DataType.INT16: 2,
            DataType.UINT16: 2,
            DataType.INT32: 4,
            DataType.UINT32: 4,
            DataType.FLOAT32: 4,
            DataType.FLOAT64: 8,
        }

        if data_type == DataType.STRING and word_length:
            return word_length * 2  # 워드당 2바이트

        return size_map.get(data_type, 2)

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        S7 연결.

        Returns:
            연결 성공 여부
        """
        async with self._connection_lock:
            try:
                # 기존 연결 정리
                if self._client:
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass

                # 새 클라이언트 생성
                self._client = snap7.client.Client()

                # 연결 (동기 호출을 스레드 풀에서 실행)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    self._executor,
                    self._client.connect,
                    self._host,
                    self._rack,
                    self._slot
                )

                if self._client.get_connected():
                    self._consecutive_failures = 0
                    logger.info(
                        f"[{self._name}] Connected to {self._host} "
                        f"(Rack={self._rack}, Slot={self._slot})"
                    )
                    return True
                else:
                    logger.error(
                        f"[{self._name}] Failed to connect to {self._host}"
                    )
                    return False

            except Exception as e:
                logger.error(f"[{self._name}] Connection error: {e}")
                return False

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        async with self._connection_lock:
            if self._client:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        self._executor,
                        self._client.disconnect
                    )
                except Exception as e:
                    logger.warning(f"[{self._name}] Disconnect error: {e}")
                finally:
                    self._client = None

    async def _do_health_check(self) -> bool:
        """연결 상태 확인."""
        if not self._client:
            return False
        try:
            return self._client.get_connected()
        except Exception:
            return False

    async def _ensure_connection(self) -> bool:
        """연결 상태 확인 및 필요시 재연결."""
        if self._client and self._client.get_connected():
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
    # Data Collection
    # =========================================================================

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """
        S7 데이터 수집.

        Args:
            group: 수집 그룹명

        Returns:
            수집된 데이터
        """
        read_groups = self._read_groups.get(group, [])
        if not read_groups:
            logger.warning(f"[{self._name}] No read groups for '{group}'")
            return None

        if not await self._ensure_connection():
            self._consecutive_failures += 1
            return None

        source_time = datetime.now()
        all_data: Dict[Tuple[str, int], Dict[int, bytes]] = {}

        try:
            loop = asyncio.get_event_loop()

            for rg in read_groups:
                raw_bytes = await self._read_group(rg, loop)
                if raw_bytes is not None:
                    key = (rg.area, rg.db_number)
                    if key not in all_data:
                        all_data[key] = {}
                    # 바이트 오프셋별로 저장
                    for i, b in enumerate(raw_bytes):
                        all_data[key][rg.start_byte + i] = b
                else:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        logger.error(
                            f"[{self._name}] Too many failures"
                        )
                        self._state = ConnectionState.ERROR
                    return None

            self._consecutive_failures = 0

            return CollectedData(
                source_time=source_time,
                collection_time=datetime.now(),
                plc_id=self._plc_id,
                raw_data=b'',
                collection_group=group,
                metadata={
                    'area_data': all_data,
                    'tags': self._tags.get(group, []),
                },
            )

        except Exception as e:
            logger.error(f"[{self._name}] Collection error: {e}")
            self._consecutive_failures += 1
            return None

    async def _read_group(
        self,
        read_group: 'S7ReadGroup',
        loop: asyncio.AbstractEventLoop
    ) -> Optional[bytes]:
        """
        단일 ReadGroup 읽기.

        Args:
            read_group: 읽기 그룹
            loop: 이벤트 루프

        Returns:
            읽은 바이트 데이터, 실패 시 None
        """
        try:
            byte_count = read_group.byte_count

            # snap7 영역 코드
            area_code = self.AREA_CODES.get(read_group.area, 0x84)

            # 동기 호출을 스레드 풀에서 실행
            if read_group.area == 'DB':
                raw_bytes = await loop.run_in_executor(
                    self._executor,
                    self._client.db_read,
                    read_group.db_number,
                    read_group.start_byte,
                    byte_count
                )
            else:
                raw_bytes = await loop.run_in_executor(
                    self._executor,
                    self._client.read_area,
                    area_code,
                    0,  # DB 번호 (비DB 영역은 0)
                    read_group.start_byte,
                    byte_count
                )

            return bytes(raw_bytes)

        except Exception as e:
            logger.error(
                f"[{self._name}] Read error at "
                f"{read_group.area}{read_group.db_number}:"
                f"{read_group.start_byte}: {e}"
            )
            return None

    async def stop(self) -> None:
        """수집기 중지."""
        await super().stop()
        self._executor.shutdown(wait=False)


class S7ReadGroup:
    """
    S7 읽기 그룹.

    연속된 주소의 태그들을 하나의 그룹으로 묶어
    한 번의 요청으로 읽습니다.

    Attributes:
        area: 영역 타입 (DB, M, I, Q)
        db_number: DB 번호 (DB 영역인 경우)
        start_byte: 시작 바이트 오프셋
        tags: 포함된 태그 정보
        end_byte: 끝 바이트 오프셋
    """

    __slots__ = ['area', 'db_number', 'start_byte', 'tags', 'end_byte']

    def __init__(self, area: str, db_number: int, start_byte: int):
        self.area = area
        self.db_number = db_number
        self.start_byte = start_byte
        self.tags: List[Tuple[int, TagDefinition, int]] = []
        self.end_byte = start_byte

    def add_tag(self, byte_offset: int, tag: TagDefinition, size: int) -> None:
        """태그 추가."""
        self.tags.append((byte_offset, tag, size))
        self.end_byte = max(self.end_byte, byte_offset + size)

    @property
    def byte_count(self) -> int:
        """읽어야 할 바이트 수."""
        return self.end_byte - self.start_byte

    def __repr__(self) -> str:
        db_str = f"DB{self.db_number}." if self.area == 'DB' else ""
        return (
            f"S7ReadGroup({db_str}{self.area}[{self.start_byte}:"
            f"{self.end_byte}], tags={len(self.tags)})"
        )
