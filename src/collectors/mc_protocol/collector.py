"""
MC Protocol TCP Collector
=========================

미쓰비시 PLC와 MC Protocol (Binary) 통신을 수행합니다.

Supported PLC Series:
    - iQ-R Series: 3E/4E Frame, Port 5007 (default)
    - Q Series: 3E Frame, Port 5000 (default)
    - L Series: 3E Frame, Port 5000 (default)
    - iQ-F Series: 3E Frame, Port 5000 (default)

Frame Formats:
    - 3E Frame: Q/L/iQ-F 시리즈 표준
    - 4E Frame: iQ-R 시리즈 확장 (더 큰 데이터 처리)

Device Types:
    - D (Data Register): 워드 디바이스
    - M (Internal Relay): 비트 디바이스
    - X (Input): 비트 디바이스
    - Y (Output): 비트 디바이스
    - W (Link Register): 워드 디바이스
    - R (File Register): 워드 디바이스
    - ZR (Extended File Register): 워드 디바이스

Connection Stability:
    - TCP Keep-alive
    - 자동 재연결
    - 타임아웃 처리

Example:
    collector = McProtocolCollector(
        plc_id=1,
        name="R_Series_PLC",
        config=collector_config,
        event_bus=event_bus,
    )

    await collector.start()
"""

import asyncio
import struct
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum, IntEnum
import logging

from ...collectors.base import BaseCollector
from ...core.interfaces import CollectedData, TagDefinition, DataType, ConnectionState
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class PlcSeries(Enum):
    """PLC 시리즈 타입."""
    IQ_R = "iq-r"      # iQ-R 시리즈
    Q = "q"            # Q 시리즈
    L = "l"            # L 시리즈
    IQ_F = "iq-f"      # iQ-F 시리즈


class FrameType(Enum):
    """프레임 타입."""
    FRAME_3E = "3E"        # MC Protocol 3E (서브헤더 0x5000)
    FRAME_4E = "4E"        # MC Protocol 4E (서브헤더 0x5400, 확장 헤더)
    SLMP_3E = "SLMP_3E"    # SLMP 3E Binary (서브헤더 0x5400, 3E 헤더)


class McCommand(IntEnum):
    """MC Protocol 커맨드 (읽기 전용)."""
    BATCH_READ = 0x0401       # 일괄 읽기
    # 쓰기 커맨드는 의도적으로 제외됨 (안전을 위해 읽기 전용)


class McErrorCode:
    """
    MC Protocol 에러 코드.

    PLC에서 반환하는 종료 코드(End Code)의 의미를 정의합니다.
    에러 코드는 응답 프레임의 데이터 부분 첫 2바이트에 포함됩니다.

    References:
        - MELSEC iQ-R Ethernet User's Manual (Application)
        - MELSEC-Q/L MELSEC Communication Protocol Reference Manual
    """
    # 정상
    SUCCESS = 0x0000

    # CPU 관련 에러 (0xC050 ~ 0xC05F)
    WRONG_COMMAND = 0xC050          # 커맨드/서브커맨드 지정 오류
    WRONG_FORMAT = 0xC051           # 요청 데이터 형식 오류
    WRONG_LENGTH = 0xC052           # 요청 데이터 길이 오류
    WRONG_DATA = 0xC053             # 요청 데이터 내용 오류
    REQUEST_TOO_LONG = 0xC054       # 요청 데이터가 너무 김
    REQUEST_POINT_EXCEEDED = 0xC05B # 요청 포인트 수 초과 (960워드 제한)
    WRONG_REQUEST = 0xC05C          # 요청 데이터 오류
    WRONG_DEVICE = 0xC05F           # 디바이스 지정 오류

    # CPU 상태 에러 (0xC060 ~ 0xC06F)
    CPU_BUSY = 0xC060               # CPU 처리 중 (재시도 필요)
    CPU_ERROR = 0xC061              # CPU 에러 상태

    # 접근 에러 (0xC070 ~ 0xC07F)
    DEVICE_RANGE_ERROR = 0xC070     # 디바이스 범위 초과
    ACCESS_DENIED = 0xC071          # 접근 금지 영역
    DEVICE_NOT_EXIST = 0xC072       # 존재하지 않는 디바이스

    # 통신 에러 (0xC0A0 ~ 0xC0AF)
    COMMUNICATION_ERROR = 0xC0A0    # 통신 에러

    # 에러 메시지 매핑
    MESSAGES = {
        0x0000: "Success",
        0xC050: "Wrong command or subcommand",
        0xC051: "Wrong request data format",
        0xC052: "Wrong request data length",
        0xC053: "Wrong request data content",
        0xC054: "Request data too long",
        0xC05B: "Request point exceeded (max 960 words)",
        0xC05C: "Wrong request data",
        0xC05F: "Wrong device specification",
        0xC060: "CPU busy (retry later)",
        0xC061: "CPU error state",
        0xC070: "Device address out of range",
        0xC071: "Access denied",
        0xC072: "Device does not exist",
        0xC0A0: "Communication error",
    }

    @classmethod
    def get_message(cls, code: int) -> str:
        """에러 코드에 해당하는 메시지 반환."""
        return cls.MESSAGES.get(code, f"Unknown error (0x{code:04X})")


class DeviceCode:
    """
    MC Protocol 디바이스 코드.

    Binary 통신에서 사용되는 디바이스 코드입니다.
    """
    # 비트 디바이스
    X = 0x9C    # 입력
    Y = 0x9D    # 출력
    M = 0x90    # 내부 릴레이
    L = 0x92    # 래치 릴레이
    F = 0x93    # 어넌시에이터
    V = 0x94    # 에지 릴레이
    B = 0xA0    # 링크 릴레이

    # 워드 디바이스
    D = 0xA8    # 데이터 레지스터
    W = 0xB4    # 링크 레지스터
    R = 0xAF    # 파일 레지스터
    ZR = 0xB0   # 확장 파일 레지스터
    TN = 0xC2   # 타이머 현재값
    CN = 0xC5   # 카운터 현재값

    # 디바이스 정보 (코드, 비트/워드, 기본주소진법)
    # 주의: X/Y의 주소 진법은 PLC 시리즈에 따라 다름
    #   - Q/L 시리즈: 8진수 (0-7)
    #   - iQ-R/iQ-F 시리즈: 16진수 (0-F)
    # Q/L 시리즈용 디바이스 코드
    DEVICE_INFO_Q = {
        'X':  (0x9C, 'bit', 8),    # Q시리즈: 8진수
        'Y':  (0x9D, 'bit', 8),    # Q시리즈: 8진수
        'M':  (0x90, 'bit', 10),
        'L':  (0x92, 'bit', 10),   # Q시리즈: 0x92
        'F':  (0x93, 'bit', 10),
        'V':  (0x94, 'bit', 10),
        'B':  (0xA0, 'bit', 16),
        'D':  (0xA8, 'word', 10),
        'W':  (0xB4, 'word', 16),
        'R':  (0xAF, 'word', 10),
        'ZR': (0xB0, 'word', 10),
        'TN': (0xC2, 'word', 10),
        'CN': (0xC5, 'word', 10),
    }

    # iQ-R/iQ-F 시리즈용 디바이스 코드
    DEVICE_INFO_R = {
        'X':  (0x9C, 'bit', 16),   # R시리즈: 16진수
        'Y':  (0x9D, 'bit', 16),   # R시리즈: 16진수
        'M':  (0x90, 'bit', 10),
        'L':  (0x92, 'bit', 10),   # Q/R 시리즈 공통: 0x92
        'F':  (0x93, 'bit', 10),
        'V':  (0x94, 'bit', 10),
        'B':  (0xA0, 'bit', 16),
        'D':  (0xA8, 'word', 10),
        'W':  (0xB4, 'word', 16),
        'R':  (0xAF, 'word', 10),
        'ZR': (0xB0, 'word', 10),
        'TN': (0xC2, 'word', 10),
        'CN': (0xC5, 'word', 10),
    }

    # 기본값 (Q 시리즈)
    DEVICE_INFO = DEVICE_INFO_Q

    # 현재 사용 중인 디바이스 맵 (시리즈에 따라 변경됨)
    _current_device_info = DEVICE_INFO_Q

    @classmethod
    def set_series(cls, series: str) -> None:
        """PLC 시리즈 설정 (디바이스 코드 맵 변경)."""
        series_lower = series.lower() if series else 'q'
        if series_lower in ('iq-r', 'iq-f', 'r', 'iqr'):
            cls._current_device_info = cls.DEVICE_INFO_R
        else:
            cls._current_device_info = cls.DEVICE_INFO_Q

    @classmethod
    def get_code(cls, device_name: str) -> int:
        """디바이스 이름으로 코드 조회."""
        info = cls._current_device_info.get(device_name.upper())
        if info:
            return info[0]
        raise ValueError(f"Unknown device: {device_name}")

    @classmethod
    def is_bit_device(cls, device_name: str) -> bool:
        """비트 디바이스 여부."""
        info = cls._current_device_info.get(device_name.upper())
        return info[1] == 'bit' if info else False

    @classmethod
    def needs_word_based_read(cls, device_name: str) -> bool:
        """
        워드 단위로 읽어서 비트 파싱해야 하는 디바이스 여부.
        L 디바이스는 iQ-R 시리즈에서 비트 단위 읽기(0x0001)가 동작하지 않아
        워드 단위(0x0000)로 읽고 비트를 추출해야 함.
        """
        return device_name.upper() == 'L'

    @classmethod
    def get_address_base(cls, device_name: str) -> int:
        """주소 진법 조회 (현재 시리즈 기준)."""
        info = cls._current_device_info.get(device_name.upper())
        return info[2] if info else 10


class McProtocolCollector(BaseCollector):
    """
    MC Protocol 데이터 수집기.

    미쓰비시 PLC와 Binary MC Protocol로 통신합니다.
    iQ-R 시리즈를 포함한 모든 주요 시리즈를 지원합니다.

    Attributes:
        _reader: TCP 읽기 스트림
        _writer: TCP 쓰기 스트림
        _read_groups: 최적화된 읽기 그룹
        _frame_type: 프레임 타입 (3E/4E)
        _plc_series: PLC 시리즈
    """

    # 한 번에 읽을 수 있는 최대 포인트 수
    MAX_READ_POINTS_3E = 960     # 3E 프레임
    MAX_READ_POINTS_4E = 1920    # 4E 프레임

    # 연속 주소로 간주할 최대 간격 (기본값)
    # 예: D0, D100 → 100 이하면 한 번에 읽음
    DEFAULT_MAX_GAP = 200

    # 최대 허용 간격 (이 이상은 무조건 분리)
    ABSOLUTE_MAX_GAP = 1000

    # 기본 포트
    DEFAULT_PORTS = {
        PlcSeries.IQ_R: 5007,
        PlcSeries.Q: 5000,
        PlcSeries.L: 5000,
        PlcSeries.IQ_F: 5000,
    }

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
        self._read_groups: Dict[str, List['McReadGroup']] = {}
        self._connection_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

        self._consecutive_failures = 0
        self._max_consecutive_failures = 5

        # 프로토콜 설정 추출
        if self._protocol_config:
            self._host = self._protocol_config.host
            self._port = self._protocol_config.port
            self._timeout = self._protocol_config.timeout_ms / 1000.0

            # extra에서 MC Protocol 특화 설정 추출
            extra = self._protocol_config.extra or {}
            series_str = extra.get('plc_series', 'iq-r').lower()
            self._plc_series = PlcSeries(series_str) if series_str in [s.value for s in PlcSeries] else PlcSeries.IQ_R
            # PLC 시리즈에 따른 디바이스 코드 맵 설정
            DeviceCode.set_series(series_str)
            frame_str = extra.get('frame_type', '3E').upper()
            if frame_str == '4E':
                self._frame_type = FrameType.FRAME_4E
            elif frame_str in ('SLMP_3E', 'SLMP-3E', 'SLMP3E', 'SLMP'):
                self._frame_type = FrameType.SLMP_3E
            else:
                self._frame_type = FrameType.FRAME_3E
            self._network_no = extra.get('network_no', 0)
            self._pc_no = extra.get('pc_no', 0xFF)
            self._unit_io = extra.get('unit_io', 0x03FF)
            self._unit_station = extra.get('unit_station', 0)

            # 최적화 설정
            self._max_address_gap = min(
                extra.get('max_address_gap', self.DEFAULT_MAX_GAP),
                self.ABSOLUTE_MAX_GAP
            )
        else:
            self._host = "127.0.0.1"
            self._port = 5007
            self._timeout = 5.0
            self._plc_series = PlcSeries.IQ_R
            DeviceCode.set_series('iq-r')  # 기본값: iQ-R
            self._frame_type = FrameType.FRAME_3E
            self._network_no = 0
            self._pc_no = 0xFF
            self._unit_io = 0x03FF
            self._unit_station = 0
            self._max_address_gap = self.DEFAULT_MAX_GAP

        # 디버깅용 패킷 저장
        self._last_request: bytes = b''
        self._last_response_raw: bytes = b''
        self._last_request_context: Dict[str, Any] = {}  # 요청 컨텍스트 (에러 로그용)

        # 포트가 지정되지 않은 경우 기본값 사용
        if self._port == 0:
            self._port = self.DEFAULT_PORTS.get(self._plc_series, 5000)

        # 최대 읽기 포인트
        if self._frame_type == FrameType.FRAME_4E:
            self._max_read_points = self.MAX_READ_POINTS_4E
        else:  # 3E 또는 SLMP_3E
            self._max_read_points = self.MAX_READ_POINTS_3E

        logger.info(
            f"[{self._name}] MC Protocol initialized - "
            f"Series: {self._plc_series.value}, Frame: {self._frame_type.value}"
        )

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

    def _optimize_read_groups(self, tags: List[TagDefinition]) -> List['McReadGroup']:
        """
        태그들을 연속 주소 그룹으로 최적화.

        Args:
            tags: 태그 정의 리스트

        Returns:
            최적화된 McReadGroup 리스트
        """
        if not tags:
            return []

        # 디바이스 타입별로 분류
        device_tags: Dict[str, List[Tuple[int, TagDefinition, int]]] = {}

        for tag in tags:
            device, address = self._parse_address(tag.address)
            size = self._get_register_size_for_tag(tag)

            if device not in device_tags:
                device_tags[device] = []
            device_tags[device].append((address, tag, size))

        # 디바이스별로 그룹화
        groups: List[McReadGroup] = []

        for device, tag_list in device_tags.items():
            groups.extend(self._create_read_groups(device, tag_list))

        return groups

    def _create_read_groups(
        self,
        device: str,
        tags: List[Tuple[int, TagDefinition, int]]
    ) -> List['McReadGroup']:
        """
        연속 주소 태그들을 McReadGroup으로 묶기 (최적화).

        최적화 전략:
            - 간격이 max_address_gap 이하면 한 번에 읽음
            - 총 읽기 크기가 _max_read_points 이하여야 함
            - 예: D0, D100, D1000 → [D0-D100], [D1000] (2 요청)

        L 디바이스 특별 처리:
            - 워드 단위로 읽어서 비트 파싱 (비트 단위 읽기가 iQ-R에서 동작 안함)
            - bit_address // 16 = word_address 로 변환하여 그룹화
            - 응답에서 비트 추출

        Args:
            device: 디바이스 타입 (D, M, W 등)
            tags: (주소, 태그, 크기) 튜플 리스트

        Returns:
            McReadGroup 리스트
        """
        if not tags:
            return []

        # L 디바이스: 워드 기반 비트 읽기
        needs_word_read = DeviceCode.needs_word_based_read(device)
        is_bit = DeviceCode.is_bit_device(device)

        if needs_word_read:
            # L 디바이스: 워드 단위로 그룹화 (최대 960워드)
            # 워드 단위 읽기는 일반 워드 디바이스와 동일한 제한
            max_points = min(960, self._max_read_points)
            max_gap = self._max_address_gap  # gap 늘려서 패킷 뭉치기
        elif is_bit:
            # 일반 비트 디바이스 (M, X, Y): 비트 단위 제한
            max_points = min(960, self._max_read_points)
            max_gap = min(64, self._max_address_gap)
        else:
            # 워드 디바이스: 기본 설정 사용
            max_points = self._max_read_points
            max_gap = self._max_address_gap

        # 주소 순 정렬
        sorted_tags = sorted(tags, key=lambda x: x[0])
        groups: List[McReadGroup] = []

        if needs_word_read:
            # L 디바이스: 워드 주소 기반 그룹화
            current_group = McReadGroup(
                device=device,
                start_address=sorted_tags[0][0],
                word_based_bit=True,  # 워드 기반 비트 읽기 플래그
            )
            current_group.add_tag(sorted_tags[0][0], sorted_tags[0][1], sorted_tags[0][2])

            for i in range(1, len(sorted_tags)):
                address, tag, size = sorted_tags[i]
                prev_end = current_group.end_address
                gap = address - prev_end

                # 워드 주소로 변환하여 크기 계산
                start_word = current_group.start_address // 16
                end_word = (address + size + 15) // 16
                total_words = end_word - start_word

                # 워드 간격 계산
                prev_word_end = (prev_end + 15) // 16
                curr_word_start = address // 16
                word_gap = curr_word_start - prev_word_end

                if word_gap <= max_gap and total_words <= max_points:
                    current_group.add_tag(address, tag, size)
                else:
                    groups.append(current_group)
                    current_group = McReadGroup(
                        device=device,
                        start_address=address,
                        word_based_bit=True,
                    )
                    current_group.add_tag(address, tag, size)

            groups.append(current_group)
        else:
            # 일반 그룹화 (기존 로직)
            current_group = McReadGroup(
                device=device,
                start_address=sorted_tags[0][0],
            )
            current_group.add_tag(sorted_tags[0][0], sorted_tags[0][1], sorted_tags[0][2])

            for i in range(1, len(sorted_tags)):
                address, tag, size = sorted_tags[i]
                prev_end = current_group.end_address
                gap = address - prev_end
                total_size = address + size - current_group.start_address

                if gap <= max_gap and total_size <= max_points:
                    current_group.add_tag(address, tag, size)
                else:
                    groups.append(current_group)
                    current_group = McReadGroup(
                        device=device,
                        start_address=address,
                    )
                    current_group.add_tag(address, tag, size)

            groups.append(current_group)

        # 최적화 로그
        total_tags = len(sorted_tags)
        total_requests = len(groups)
        if total_tags > 0:
            mode = "word-based-bit" if needs_word_read else ("bit" if is_bit else "word")
            logger.debug(
                f"[{self._name}] Optimization: {total_tags} tags → {total_requests} requests "
                f"(device={device}, mode={mode}, gap={max_gap}, max_points={max_points})"
            )

        return groups

    def _parse_address(self, address: str) -> Tuple[str, int]:
        """
        주소 문자열 파싱.

        Args:
            address: 주소 문자열 (예: "D0", "M100", "W1A", "D100:15")

        Returns:
            (디바이스 타입, 주소) 튜플
        """
        device, addr, _ = self._parse_address_with_length(address)
        return device, addr

    def _parse_address_with_length(self, address: str) -> Tuple[str, int, int]:
        """
        주소 문자열 파싱 (길이 포함).

        Args:
            address: 주소 문자열 (예: "D0", "D100:15")

        Returns:
            (디바이스 타입, 주소, 워드 수) 튜플
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

        # 디바이스 타입 추출 (ZR은 2글자)
        if address.startswith('ZR'):
            device = 'ZR'
            addr_str = address[2:]
        else:
            device = address[0]
            addr_str = address[1:]

        # 주소 변환 (진법 고려 - 시리즈별)
        base = self._get_address_base(device)
        try:
            addr = int(addr_str, base)
        except ValueError:
            addr = int(addr_str, 10)

        return device, addr, word_count

    def _get_address_base(self, device: str) -> int:
        """
        디바이스 주소 진법 반환 (시리즈별).

        Args:
            device: 디바이스 타입

        Returns:
            주소 진법 (8, 10, 16)
        """
        # X/Y 디바이스는 시리즈에 따라 다름
        if device in ('X', 'Y'):
            if self._plc_series in (PlcSeries.Q, PlcSeries.L):
                return 8   # Q/L 시리즈: 8진수
            else:
                return 16  # iQ-R/iQ-F 시리즈: 16진수

        return DeviceCode.get_address_base(device)

    def _get_register_size(self, data_type: DataType) -> int:
        """
        데이터 타입에 따른 레지스터 크기 반환.

        Args:
            data_type: 데이터 타입

        Returns:
            필요한 워드 수 (16비트 단위)
        """
        size_map = {
            DataType.BOOL: 1,
            DataType.INT16: 1,
            DataType.UINT16: 1,
            DataType.INT32: 2,
            DataType.UINT32: 2,
            DataType.FLOAT32: 2,
            DataType.FLOAT64: 4,
            DataType.STRING: 1,  # 기본값, 실제론 주소에서 파싱
        }
        return size_map.get(data_type, 1)

    def _get_register_size_for_tag(self, tag: TagDefinition) -> int:
        """
        태그 정의에 따른 레지스터 크기 반환.

        STRING 타입의 경우 주소에서 길이를 파싱합니다.
        예: D100:15 → 15워드

        Args:
            tag: 태그 정의

        Returns:
            필요한 워드 수 (16비트 단위)
        """
        if tag.data_type == DataType.STRING:
            _, _, word_count = self._parse_address_with_length(tag.address)
            return word_count

        return self._get_register_size(tag.data_type)

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        MC Protocol TCP 연결.

        Returns:
            연결 성공 여부
        """
        async with self._connection_lock:
            try:
                # 기존 연결 정리
                await self._close_connection()

                # TCP 연결
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._timeout
                )

                # TCP Keep-alive 설정
                sock = self._writer.get_extra_info('socket')
                if sock:
                    import socket
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)

                self._consecutive_failures = 0
                logger.info(
                    f"[{self._name}] Connected to {self._host}:{self._port} "
                    f"(MC Protocol {self._frame_type.value})"
                )
                return True

            except asyncio.TimeoutError:
                logger.error(f"[{self._name}] Connection timeout")
                return False
            except Exception as e:
                logger.error(f"[{self._name}] Connection error: {e}")
                return False

    async def _close_connection(self) -> None:
        """연결 닫기."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception as e:
                logger.verbose(f"[{self._name}] Error during connection close: {e}")
            finally:
                self._writer = None
                self._reader = None

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        async with self._connection_lock:
            await self._close_connection()

    async def _do_health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결 정상 여부
        """
        if not self._reader or not self._writer:
            return False

        if self._writer.is_closing():
            return False

        return True

    async def _ensure_connection(self) -> bool:
        """
        연결 상태 확인 및 필요시 재연결.

        Returns:
            연결 사용 가능 여부
        """
        if self._reader and self._writer and not self._writer.is_closing():
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
        MC Protocol 데이터 수집.

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
        all_data: Dict[str, Dict[int, int]] = {}

        try:
            # 각 ReadGroup 읽기
            for rg in read_groups:
                data = await self._read_group(rg)
                if data is not None:
                    if rg.device not in all_data:
                        all_data[rg.device] = {}
                    all_data[rg.device].update(data)
                else:
                    # 읽기 실패
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
                    'devices': all_data,
                    'tags': self._tags.get(group, []),
                },
            )

        except Exception as e:
            logger.error(f"[{self._name}] Collection error: {e}")
            self._consecutive_failures += 1
            return None

    async def _read_group(self, read_group: 'McReadGroup') -> Optional[Dict[int, int]]:
        """
        단일 ReadGroup 읽기.

        Args:
            read_group: 읽기 그룹

        Returns:
            {주소: 값} 딕셔너리, 실패 시 None
        """
        async with self._request_lock:
            try:
                is_bit = DeviceCode.is_bit_device(read_group.device)
                word_based_bit = read_group.word_based_bit

                if word_based_bit:
                    # L 디바이스: 워드 주소로 읽기
                    read_address = read_group.word_start_address
                    read_count = read_group.point_count  # 워드 수
                    logger.verbose(
                        f"[{self._name}] Reading {read_group.device}[{read_address}:{read_count}] "
                        f"(word-based-bit, bit_range={read_group.start_address}-{read_group.end_address})"
                    )
                else:
                    read_address = read_group.start_address
                    read_count = read_group.point_count
                    logger.verbose(
                        f"[{self._name}] Reading {read_group.device}[{read_address}:{read_count}] "
                        f"({'bit' if is_bit else 'word'})"
                    )

                # 요청 프레임 생성
                request = self._build_read_request(
                    device=read_group.device,
                    start_address=read_address,
                    count=read_count,
                    word_based_bit=word_based_bit,
                )

                # 디버깅용 저장
                self._last_request = request
                self._last_request_context = {
                    'device': read_group.device,
                    'start_address': read_address,
                    'count': read_count,
                    'mode': 'word-based-bit' if word_based_bit else ('bit' if is_bit else 'word'),
                    'bit_range': f"{read_group.start_address}-{read_group.end_address}" if word_based_bit else None,
                }

                # 전송
                self._writer.write(request)
                await self._writer.drain()

                # 응답 수신
                response = await self._receive_response()

                if response is None:
                    return None

                # 결과를 딕셔너리로 변환
                result: Dict[int, int] = {}

                if word_based_bit:
                    # L 디바이스: 워드에서 비트 추출
                    # 응답은 워드 데이터 (각 2바이트)
                    word_start = read_group.word_start_address
                    for i in range(read_count):
                        if i * 2 + 1 < len(response):
                            word_value = struct.unpack('<H', response[i*2:i*2+2])[0]
                            # 이 워드에 해당하는 16개 비트 추출
                            word_addr = word_start + i
                            for bit_pos in range(16):
                                bit_addr = word_addr * 16 + bit_pos
                                # 요청 범위 내의 비트만 저장
                                if read_group.start_address <= bit_addr < read_group.end_address:
                                    bit_value = (word_value >> bit_pos) & 0x01
                                    result[bit_addr] = bit_value
                elif is_bit:
                    # 일반 비트 디바이스 (M, X, Y): Binary 응답은 1바이트에 2비트 패킹
                    # 하위 니블(bit 0-3) = 짝수 번째, 상위 니블(bit 4-7) = 홀수 번째
                    for i in range(read_count):
                        byte_idx = i // 2
                        if byte_idx < len(response):
                            if i % 2 == 0:
                                bit_value = response[byte_idx] & 0x0F
                            else:
                                bit_value = (response[byte_idx] >> 4) & 0x0F
                            result[read_group.start_address + i] = bit_value & 0x01
                else:
                    # 워드 디바이스: 각 워드를 순서대로 저장
                    for i in range(read_count):
                        if i * 2 + 1 < len(response):
                            value = struct.unpack('<H', response[i*2:i*2+2])[0]
                            result[read_group.start_address + i] = value

                return result

            except asyncio.TimeoutError:
                logger.error(f"[{self._name}] Read timeout for {read_group}")
                self._state = ConnectionState.ERROR
                return None

            except Exception as e:
                logger.error(f"[{self._name}] Read error: {e}")
                return None

    # =========================================================================
    # Frame Building
    # =========================================================================

    def _build_read_request(
        self,
        device: str,
        start_address: int,
        count: int,
        word_based_bit: bool = False,
    ) -> bytes:
        """
        읽기 요청 프레임 생성.

        Args:
            device: 디바이스 타입
            start_address: 시작 주소
            count: 읽을 포인트 수
            word_based_bit: 워드 단위로 읽어서 비트 파싱 여부 (L 디바이스용)

        Returns:
            요청 바이트
        """
        if self._frame_type == FrameType.FRAME_4E:
            return self._build_4e_read_request(device, start_address, count, word_based_bit)
        elif self._frame_type == FrameType.SLMP_3E:
            return self._build_slmp_3e_read_request(device, start_address, count, word_based_bit)
        else:
            return self._build_3e_read_request(device, start_address, count, word_based_bit)

    def _build_3e_read_request(
        self,
        device: str,
        start_address: int,
        count: int,
        word_based_bit: bool = False,
    ) -> bytes:
        """3E 프레임 읽기 요청 생성."""
        device_code = DeviceCode.get_code(device)
        is_bit = DeviceCode.is_bit_device(device)

        # 서브커맨드 결정:
        # - word_based_bit=True (L 디바이스): 0x0000 (워드 단위로 읽기)
        # - 일반 비트 디바이스 (M, X, Y): 0x0001 (비트 단위)
        # - 워드 디바이스: 0x0000
        if word_based_bit:
            subcommand = 0x0000  # L 디바이스: 워드 단위로 읽기
        elif is_bit:
            subcommand = 0x0001  # 일반 비트 디바이스
        else:
            subcommand = 0x0000  # 워드 디바이스

        # 데이터 부분
        data = struct.pack('<H', McCommand.BATCH_READ)      # 커맨드
        data += struct.pack('<H', subcommand)                # 서브커맨드
        data += struct.pack('<I', start_address)[:3]         # 시작 디바이스 (3바이트)
        data += struct.pack('<B', device_code)               # 디바이스 코드
        data += struct.pack('<H', count)                     # 디바이스 점수

        # 서브헤더 (Big-endian으로 0x5000)
        subheader = struct.pack('>H', 0x5000)                # 서브헤더 (Big-endian!)
        subheader += struct.pack('<B', self._network_no)     # 네트워크 번호
        subheader += struct.pack('<B', self._pc_no)          # PC 번호
        subheader += struct.pack('<H', self._unit_io)        # 요청 선 IO 번호
        subheader += struct.pack('<B', self._unit_station)   # 요청 선 국번호
        subheader += struct.pack('<H', len(data) + 2)        # 요청 데이터 길이
        subheader += struct.pack('<H', 0x0010)               # CPU 감시 타이머 (16 x 250ms)

        return subheader + data

    def _build_slmp_3e_read_request(
        self,
        device: str,
        start_address: int,
        count: int,
        word_based_bit: bool = False,
    ) -> bytes:
        """SLMP 3E Binary 프레임 읽기 요청 생성."""
        device_code = DeviceCode.get_code(device)
        is_bit = DeviceCode.is_bit_device(device)

        # 서브커맨드 결정
        if word_based_bit:
            subcommand = 0x0000  # L 디바이스: 워드 단위로 읽기
        elif is_bit:
            subcommand = 0x0001  # 일반 비트 디바이스
        else:
            subcommand = 0x0000  # 워드 디바이스

        # 데이터 부분
        data = struct.pack('<H', McCommand.BATCH_READ)      # 커맨드
        data += struct.pack('<H', subcommand)                # 서브커맨드
        data += struct.pack('<I', start_address)[:3]         # 시작 디바이스 (3바이트)
        data += struct.pack('<B', device_code)               # 디바이스 코드
        data += struct.pack('<H', count)                     # 디바이스 점수

        # 서브헤더 (Big-endian으로 0x5000)
        subheader = struct.pack('>H', 0x5000)                # 서브헤더 (Big-endian!)
        subheader += struct.pack('<B', self._network_no)     # 네트워크 번호
        subheader += struct.pack('<B', self._pc_no)          # PC 번호
        subheader += struct.pack('<H', self._unit_io)        # 요청 선 IO 번호
        subheader += struct.pack('<B', self._unit_station)   # 요청 선 국번호
        subheader += struct.pack('<H', len(data) + 2)        # 요청 데이터 길이
        subheader += struct.pack('<H', 0x0010)               # CPU 감시 타이머 (16 x 250ms)

        return subheader + data

    def _build_4e_read_request(
        self,
        device: str,
        start_address: int,
        count: int,
        word_based_bit: bool = False,
    ) -> bytes:
        """4E 프레임 읽기 요청 생성 (iQ-R 시리즈용)."""
        device_code = DeviceCode.get_code(device)
        is_bit = DeviceCode.is_bit_device(device)

        # 서브커맨드 결정
        if word_based_bit:
            subcommand = 0x0000  # L 디바이스: 워드 단위로 읽기
        elif is_bit:
            subcommand = 0x0001  # 일반 비트 디바이스
        else:
            subcommand = 0x0000  # 워드 디바이스

        # 데이터 부분
        data = struct.pack('<H', McCommand.BATCH_READ)      # 커맨드
        data += struct.pack('<H', subcommand)                # 서브커맨드
        data += struct.pack('<I', start_address)[:3]         # 시작 디바이스 (3바이트)
        data += struct.pack('<B', device_code)               # 디바이스 코드
        data += struct.pack('<H', count)                     # 디바이스 점수

        # 서브헤더 (4E 프레임)
        subheader = struct.pack('<H', 0x5400)                # 서브헤더 (4E)
        subheader += struct.pack('<H', 0x0000)               # 시리얼 번호
        subheader += struct.pack('<H', 0x0000)               # 예약
        subheader += struct.pack('<B', self._network_no)     # 네트워크 번호
        subheader += struct.pack('<B', self._pc_no)          # PC 번호
        subheader += struct.pack('<H', self._unit_io)        # 요청 선 IO 번호
        subheader += struct.pack('<B', self._unit_station)   # 요청 선 국번호
        subheader += struct.pack('<H', len(data) + 2)        # 요청 데이터 길이
        subheader += struct.pack('<H', 0x0010)               # CPU 감시 타이머

        return subheader + data

    async def _receive_response(self) -> Optional[bytes]:
        """
        응답 수신 및 파싱.

        Returns:
            데이터 부분 바이트, 실패 시 None
        """
        try:
            if self._frame_type == FrameType.FRAME_4E:
                return await self._receive_4e_response()
            elif self._frame_type == FrameType.SLMP_3E:
                return await self._receive_slmp_3e_response()
            else:
                return await self._receive_3e_response()

        except asyncio.TimeoutError:
            logger.error(f"[{self._name}] Response timeout")
            return None
        except Exception as e:
            logger.error(f"[{self._name}] Response error: {e}")
            return None

    async def _receive_3e_response(self) -> Optional[bytes]:
        """3E 프레임 응답 수신."""
        # 서브헤더 수신 (9바이트)
        header = await asyncio.wait_for(
            self._reader.readexactly(9),
            timeout=self._timeout
        )

        # 응답 데이터 길이
        data_length = struct.unpack('<H', header[7:9])[0]

        # 데이터 수신
        data = await asyncio.wait_for(
            self._reader.readexactly(data_length),
            timeout=self._timeout
        )

        # 디버깅용 저장 (전체 응답)
        self._last_response_raw = header + data

        # 종료 코드 확인
        end_code = struct.unpack('<H', data[0:2])[0]
        if end_code != 0:
            ctx = self._last_request_context
            logger.error(f"[{self._name}] MC Protocol error code: 0x{end_code:04X} - {McErrorCode.get_message(end_code)}")
            logger.error(f"[{self._name}]   Device: {ctx.get('device', '?')}[{ctx.get('start_address', '?')}], Count: {ctx.get('count', '?')}, Mode: {ctx.get('mode', '?')}")
            if ctx.get('bit_range'):
                logger.error(f"[{self._name}]   Bit range: {ctx.get('bit_range')}")
            logger.error(f"[{self._name}]   Request  (hex): {self._last_request.hex()}")
            logger.error(f"[{self._name}]   Response (hex): {self._last_response_raw.hex()}")
            return None

        return data[2:]  # 종료 코드 제외한 데이터

    async def _receive_slmp_3e_response(self) -> Optional[bytes]:
        """SLMP 3E Binary 프레임 응답 수신."""
        # 서브헤더 수신 (9바이트)
        header = await asyncio.wait_for(
            self._reader.readexactly(9),
            timeout=self._timeout
        )

        # 응답 서브헤더 확인 (Big-endian 0xD000)
        subheader = struct.unpack('>H', header[0:2])[0]
        if subheader != 0xD000:
            logger.warning(
                f"[{self._name}] Unexpected subheader: 0x{subheader:04X} (expected 0xD000)"
            )

        # 응답 데이터 길이
        data_length = struct.unpack('<H', header[7:9])[0]

        # 데이터 수신
        data = await asyncio.wait_for(
            self._reader.readexactly(data_length),
            timeout=self._timeout
        )

        # 디버깅용 저장 (전체 응답)
        self._last_response_raw = header + data

        # 종료 코드 확인
        end_code = struct.unpack('<H', data[0:2])[0]
        if end_code != 0:
            ctx = self._last_request_context
            logger.error(f"[{self._name}] SLMP error code: 0x{end_code:04X} - {McErrorCode.get_message(end_code)}")
            logger.error(f"[{self._name}]   Device: {ctx.get('device', '?')}[{ctx.get('start_address', '?')}], Count: {ctx.get('count', '?')}, Mode: {ctx.get('mode', '?')}")
            if ctx.get('bit_range'):
                logger.error(f"[{self._name}]   Bit range: {ctx.get('bit_range')}")
            logger.error(f"[{self._name}]   Request  (hex): {self._last_request.hex()}")
            logger.error(f"[{self._name}]   Response (hex): {self._last_response_raw.hex()}")
            return None

        return data[2:]  # 종료 코드 제외한 데이터

    async def _receive_4e_response(self) -> Optional[bytes]:
        """4E 프레임 응답 수신."""
        # 서브헤더 수신 (11바이트)
        header = await asyncio.wait_for(
            self._reader.readexactly(11),
            timeout=self._timeout
        )

        # 응답 데이터 길이
        data_length = struct.unpack('<H', header[9:11])[0]

        # 데이터 수신
        data = await asyncio.wait_for(
            self._reader.readexactly(data_length),
            timeout=self._timeout
        )

        # 디버깅용 저장 (전체 응답)
        self._last_response_raw = header + data

        # 종료 코드 확인
        end_code = struct.unpack('<H', data[0:2])[0]
        if end_code != 0:
            ctx = self._last_request_context
            logger.error(f"[{self._name}] MC Protocol error code: 0x{end_code:04X} - {McErrorCode.get_message(end_code)}")
            logger.error(f"[{self._name}]   Device: {ctx.get('device', '?')}[{ctx.get('start_address', '?')}], Count: {ctx.get('count', '?')}, Mode: {ctx.get('mode', '?')}")
            if ctx.get('bit_range'):
                logger.error(f"[{self._name}]   Bit range: {ctx.get('bit_range')}")
            logger.error(f"[{self._name}]   Request  (hex): {self._last_request.hex()}")
            logger.error(f"[{self._name}]   Response (hex): {self._last_response_raw.hex()}")
            return None

        return data[2:]


class McReadGroup:
    """
    최적화된 MC Protocol 읽기 그룹.

    연속된 주소의 태그들을 하나의 그룹으로 묶어
    한 번의 요청으로 읽습니다.
    """

    __slots__ = ['device', 'start_address', 'tags', 'end_address', 'word_based_bit']

    def __init__(self, device: str, start_address: int, word_based_bit: bool = False):
        """
        Args:
            device: 디바이스 타입 (D, M, W 등)
            start_address: 시작 주소
            word_based_bit: 워드 단위로 읽어서 비트 파싱 여부 (L 디바이스용)
        """
        self.device = device
        self.start_address = start_address
        self.tags: List[Tuple[int, TagDefinition, int]] = []
        self.end_address = start_address
        self.word_based_bit = word_based_bit

    def add_tag(self, address: int, tag: TagDefinition, size: int) -> None:
        """태그 추가."""
        self.tags.append((address, tag, size))
        self.end_address = max(self.end_address, address + size)

    @property
    def point_count(self) -> int:
        """읽어야 할 포인트 수."""
        if self.word_based_bit:
            # 워드 기반 비트 읽기: 워드 수 반환
            start_word = self.start_address // 16
            end_word = (self.end_address + 15) // 16
            return end_word - start_word
        return self.end_address - self.start_address

    @property
    def word_start_address(self) -> int:
        """워드 기반 비트 읽기용 워드 시작 주소."""
        if self.word_based_bit:
            return self.start_address // 16
        return self.start_address

    def __repr__(self) -> str:
        mode = "word-bit" if self.word_based_bit else "normal"
        return (
            f"McReadGroup({self.device}[{self.start_address}:"
            f"{self.end_address}], tags={len(self.tags)}, mode={mode})"
        )
