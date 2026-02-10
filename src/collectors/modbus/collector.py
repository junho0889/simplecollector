"""
Modbus Collector (Pure Python)
==============================

외부 라이브러리 없이 Raw TCP/Serial 통신으로 Modbus 프로토콜을 구현한 수집기.

Supported Modes:
    - TCP: 표준 Modbus TCP (MBAP 헤더)
    - RTU: Modbus RTU over Serial (pyserial 필요)
    - RTU over TCP: RTU 프레임을 TCP로 전송 (게이트웨이용)

Supported Function Codes:
    - FC03: Read Holding Registers
    - FC04: Read Input Registers

Address Format:
    - D0, D100: Holding Register (D 접두사)
    - I0, I100: Input Register (I 접두사)
    - HR0, HR100: Holding Register (HR 접두사)
    - IR0, IR100: Input Register (IR 접두사)
    - 숫자만: Holding Register로 간주

Connection Stability:
    - 요청 전 연결 상태 확인
    - 타임아웃 발생 시 연결 재설정
    - 연속 실패 시 백오프 재연결

Optimization:
    - 연속 주소 병합: D0~D10을 한 번에 읽기
    - CRC-16 lookup table 사전 계산 (RTU용)
    - Transport 추상화로 모드 간 코드 재사용

Example:
    collector = ModbusCollector(
        plc_id=1,
        name="PLC1",
        config=collector_config,
        event_bus=event_bus,
    )

    await collector.start()
"""

import asyncio
import struct
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any
import logging

from ...collectors.base import BaseCollector
from ...core.interfaces import CollectedData, TagDefinition, DataType, ConnectionState
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


# =============================================================================
# Enums & Constants
# =============================================================================

class ModbusMode(Enum):
    """Modbus 전송 모드."""
    TCP = "tcp"                # 표준 Modbus TCP (MBAP 헤더)
    RTU = "rtu"                # Modbus RTU over Serial
    RTU_OVER_TCP = "rtu_tcp"   # RTU 프레임 over TCP (게이트웨이)


# Modbus 예외 코드 → 설명
MODBUS_EXCEPTIONS: Dict[int, str] = {
    0x01: "Illegal Function",
    0x02: "Illegal Data Address",
    0x03: "Illegal Data Value",
    0x04: "Slave Device Failure",
    0x05: "Acknowledge",
    0x06: "Slave Device Busy",
    0x08: "Memory Parity Error",
    0x0A: "Gateway Path Unavailable",
    0x0B: "Gateway Target Device Failed to Respond",
}


# =============================================================================
# CRC-16/Modbus
# =============================================================================

class ModbusCRC:
    """
    CRC-16/Modbus 계산기 (순수 Python).

    다항식: 0xA001 (bit-reversed 0x8005)
    초기값: 0xFFFF
    256 엔트리 lookup table로 고속 계산.
    """

    _TABLE: List[int] = []

    @classmethod
    def _init_table(cls) -> None:
        """CRC-16 lookup table 생성 (모듈 로드 시 1회)."""
        if cls._TABLE:
            return
        for i in range(256):
            crc = i
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
            cls._TABLE.append(crc)

    @classmethod
    def calculate(cls, data: bytes) -> int:
        """CRC-16/Modbus 계산."""
        crc = 0xFFFF
        for byte in data:
            crc = (crc >> 8) ^ cls._TABLE[(crc ^ byte) & 0xFF]
        return crc

    @classmethod
    def append(cls, data: bytes) -> bytes:
        """데이터에 CRC-16 추가 (little-endian, Modbus RTU 표준)."""
        crc = cls.calculate(data)
        return data + struct.pack('<H', crc)

    @classmethod
    def verify(cls, frame: bytes) -> bool:
        """전체 RTU 프레임 CRC 검증 (데이터 + CRC 포함)."""
        if len(frame) < 3:
            return False
        return cls.calculate(frame) == 0


# 모듈 로드 시 CRC table 초기화
ModbusCRC._init_table()


# =============================================================================
# Transport 추상 계층
# =============================================================================

class ModbusTransport(ABC):
    """
    Modbus 전송 계층 추상 클래스.

    프로토콜 프레이밍(MBAP/RTU)과 물리 전송(TCP/Serial)을 캡슐화.
    모든 Transport는 register data를 big-endian bytes로 반환.
    """

    def __init__(self, timeout: float):
        self._timeout = timeout
        self._connection_lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

    @abstractmethod
    async def connect(self) -> bool:
        """연결. 성공 시 True."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """연결 해제."""
        ...

    @abstractmethod
    async def is_connected(self) -> bool:
        """연결 상태 확인."""
        ...

    @abstractmethod
    async def send_request(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        quantity: int,
    ) -> Optional[bytes]:
        """
        Modbus 읽기 요청 전송 및 응답 수신.

        Args:
            unit_id: Slave ID
            function_code: FC03 or FC04
            start_address: 시작 레지스터 주소
            quantity: 읽을 레지스터 수

        Returns:
            레지스터 데이터 bytes (big-endian, 레지스터당 2바이트)
            실패 시 None
        """
        ...


# =============================================================================
# Modbus TCP Transport
# =============================================================================

class ModbusTcpTransport(ModbusTransport):
    """
    Modbus TCP 전송 (MBAP 헤더).

    MBAP Header (7 bytes):
        Transaction ID  : 2 bytes (big-endian, 요청마다 증가)
        Protocol ID     : 2 bytes (항상 0x0000)
        Length          : 2 bytes (Unit ID + PDU 길이)
        Unit ID         : 1 byte

    Request PDU (FC03/FC04, 5 bytes):
        Function Code   : 1 byte
        Start Address   : 2 bytes (big-endian)
        Quantity        : 2 bytes (big-endian)

    Response PDU:
        Function Code   : 1 byte
        Byte Count      : 1 byte
        Register Data   : N bytes
    """

    MBAP_HEADER_SIZE = 7

    def __init__(self, host: str, port: int, timeout: float):
        super().__init__(timeout)
        self._host = host
        self._port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._transaction_id: int = 0

    def _next_transaction_id(self) -> int:
        """Transaction ID 순환 증가 (0~65535)."""
        self._transaction_id = (self._transaction_id + 1) % 65536
        return self._transaction_id

    async def connect(self) -> bool:
        async with self._connection_lock:
            try:
                await self._close()
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._timeout,
                )
                # TCP Keep-alive
                sock = self._writer.get_extra_info('socket')
                if sock:
                    import socket
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                return True
            except asyncio.TimeoutError:
                logger.error(f"Modbus TCP connection timeout: {self._host}:{self._port}")
                return False
            except Exception as e:
                logger.error(f"Modbus TCP connection error: {e}")
                return False

    async def _close(self) -> None:
        """연결 정리."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            finally:
                self._writer = None
                self._reader = None

    async def disconnect(self) -> None:
        async with self._connection_lock:
            await self._close()

    async def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def send_request(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        quantity: int,
    ) -> Optional[bytes]:
        async with self._request_lock:
            try:
                if not await self.is_connected():
                    return None

                tid = self._next_transaction_id()

                # --- 요청 빌드 ---
                # PDU: FC(1) + StartAddr(2) + Quantity(2) = 5 bytes
                pdu = struct.pack('>BHH', function_code, start_address, quantity)

                # MBAP: TransID(2) + ProtoID(2) + Length(2) + UnitID(1)
                # Length = UnitID(1) + PDU(5) = 6
                mbap = struct.pack('>HHHB',
                    tid,
                    0x0000,            # Protocol ID (Modbus)
                    len(pdu) + 1,      # Length (unit_id + pdu)
                    unit_id,
                )
                request = mbap + pdu

                # --- 전송 ---
                self._writer.write(request)
                await self._writer.drain()

                # --- 응답 수신: MBAP 헤더 (7 bytes) ---
                header = await asyncio.wait_for(
                    self._reader.readexactly(self.MBAP_HEADER_SIZE),
                    timeout=self._timeout,
                )
                _, _, resp_length, _ = struct.unpack('>HHHB', header)

                # --- 나머지 데이터 수신 ---
                remaining = resp_length - 1  # UnitID는 MBAP에 포함
                data = await asyncio.wait_for(
                    self._reader.readexactly(remaining),
                    timeout=self._timeout,
                )

                # --- 응답 검증 ---
                resp_fc = data[0]

                # Exception response (FC의 bit 7 설정)
                if resp_fc & 0x80:
                    exc_code = data[1] if len(data) > 1 else 0
                    exc_msg = MODBUS_EXCEPTIONS.get(exc_code, f"Unknown(0x{exc_code:02X})")
                    logger.error(
                        f"Modbus exception FC{function_code:02X} addr={start_address}: "
                        f"0x{exc_code:02X} {exc_msg}"
                    )
                    return None

                if resp_fc != function_code:
                    logger.error(
                        f"Modbus FC mismatch: expected 0x{function_code:02X}, "
                        f"got 0x{resp_fc:02X}"
                    )
                    return None

                byte_count = data[1]
                register_data = data[2:2 + byte_count]

                return register_data

            except asyncio.TimeoutError:
                logger.error(
                    f"Modbus TCP timeout: FC{function_code:02X} "
                    f"addr={start_address} qty={quantity}"
                )
                return None
            except (ConnectionResetError, BrokenPipeError, ConnectionError) as e:
                logger.error(f"Modbus TCP connection lost: {e}")
                await self._close()
                return None
            except asyncio.IncompleteReadError:
                logger.error("Modbus TCP incomplete response")
                await self._close()
                return None
            except Exception as e:
                logger.error(f"Modbus TCP error: {e}")
                return None


# =============================================================================
# Modbus RTU over TCP Transport
# =============================================================================

class ModbusRtuOverTcpTransport(ModbusTransport):
    """
    Modbus RTU over TCP 전송 (게이트웨이용).

    RTU 프레임(CRC 포함)을 TCP 소켓으로 전송.
    MBAP 헤더 없음. Serial-to-Ethernet 변환기에서 주로 사용.

    RTU Frame:
        Request:  [UnitID:1][FC:1][StartAddr:2][Qty:2][CRC:2] = 8 bytes
        Response: [UnitID:1][FC:1][ByteCount:1][Data...][CRC:2]
    """

    def __init__(self, host: str, port: int, timeout: float):
        super().__init__(timeout)
        self._host = host
        self._port = port
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None

    async def connect(self) -> bool:
        async with self._connection_lock:
            try:
                await self._close()
                self._reader, self._writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=self._timeout,
                )
                # TCP Keep-alive
                sock = self._writer.get_extra_info('socket')
                if sock:
                    import socket
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                return True
            except asyncio.TimeoutError:
                logger.error(f"Modbus RTU/TCP connection timeout: {self._host}:{self._port}")
                return False
            except Exception as e:
                logger.error(f"Modbus RTU/TCP connection error: {e}")
                return False

    async def _close(self) -> None:
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
            finally:
                self._writer = None
                self._reader = None

    async def disconnect(self) -> None:
        async with self._connection_lock:
            await self._close()

    async def is_connected(self) -> bool:
        return self._writer is not None and not self._writer.is_closing()

    async def send_request(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        quantity: int,
    ) -> Optional[bytes]:
        async with self._request_lock:
            try:
                if not await self.is_connected():
                    return None

                # --- RTU 프레임 빌드 ---
                pdu = struct.pack('>BHH', function_code, start_address, quantity)
                frame_no_crc = struct.pack('B', unit_id) + pdu
                frame = ModbusCRC.append(frame_no_crc)

                # --- 전송 ---
                self._writer.write(frame)
                await self._writer.drain()

                # --- 응답 수신: 헤더 3 bytes (UnitID + FC + ByteCount) ---
                header = await asyncio.wait_for(
                    self._reader.readexactly(3),
                    timeout=self._timeout,
                )

                resp_fc = header[1]

                # Exception response
                if resp_fc & 0x80:
                    # exception_code(1) + CRC(2) 소진
                    await asyncio.wait_for(
                        self._reader.readexactly(3),
                        timeout=self._timeout,
                    )
                    exc_code = header[2]
                    exc_msg = MODBUS_EXCEPTIONS.get(exc_code, f"Unknown(0x{exc_code:02X})")
                    logger.error(
                        f"Modbus RTU/TCP exception FC{function_code:02X} addr={start_address}: "
                        f"0x{exc_code:02X} {exc_msg}"
                    )
                    return None

                byte_count = header[2]

                # --- 데이터 + CRC 수신 ---
                remaining = await asyncio.wait_for(
                    self._reader.readexactly(byte_count + 2),
                    timeout=self._timeout,
                )

                # --- CRC 검증 ---
                full_frame = header + remaining
                if not ModbusCRC.verify(full_frame):
                    logger.error(
                        f"Modbus RTU/TCP CRC error: FC{function_code:02X} addr={start_address}"
                    )
                    return None

                register_data = remaining[:byte_count]
                return register_data

            except asyncio.TimeoutError:
                logger.error(
                    f"Modbus RTU/TCP timeout: FC{function_code:02X} "
                    f"addr={start_address} qty={quantity}"
                )
                return None
            except (ConnectionResetError, BrokenPipeError, ConnectionError) as e:
                logger.error(f"Modbus RTU/TCP connection lost: {e}")
                await self._close()
                return None
            except asyncio.IncompleteReadError:
                logger.error("Modbus RTU/TCP incomplete response")
                await self._close()
                return None
            except Exception as e:
                logger.error(f"Modbus RTU/TCP error: {e}")
                return None


# =============================================================================
# Modbus RTU Serial Transport
# =============================================================================

class ModbusRtuTransport(ModbusTransport):
    """
    Modbus RTU 시리얼 전송.

    RTU Frame:
        Request:  [SlaveAddr:1][FC:1][StartAddr:2][Qty:2][CRC:2]
        Response: [SlaveAddr:1][FC:1][ByteCount:1][Data...][CRC:2]

    Timing:
        Inter-frame gap: >= 3.5 character times silence
        9600 baud, 11 bits/char → ~4.01ms

    Requires: pip install pyserial
    """

    def __init__(
        self,
        port: str,
        baudrate: int,
        timeout: float,
        parity: str = 'N',
        stopbits: int = 1,
        bytesize: int = 8,
    ):
        super().__init__(timeout)
        self._port = port
        self._baudrate = baudrate
        self._parity = parity
        self._stopbits = stopbits
        self._bytesize = bytesize
        self._serial = None
        self._inter_frame_delay = self._calc_inter_frame_delay()

    def _calc_inter_frame_delay(self) -> float:
        """3.5 character times 계산 (초)."""
        bits_per_char = 1 + self._bytesize + (1 if self._parity != 'N' else 0) + self._stopbits
        char_time = bits_per_char / self._baudrate
        delay = 3.5 * char_time
        # Modbus 스펙: 19200 baud 초과 시 최소 1.75ms
        if self._baudrate > 19200:
            return max(delay, 0.00175)
        return delay

    async def connect(self) -> bool:
        async with self._connection_lock:
            try:
                import serial as pyserial

                # parity 매핑
                parity_map = {
                    'N': pyserial.PARITY_NONE,
                    'E': pyserial.PARITY_EVEN,
                    'O': pyserial.PARITY_ODD,
                }
                # stopbits 매핑
                stopbits_map = {
                    1: pyserial.STOPBITS_ONE,
                    2: pyserial.STOPBITS_TWO,
                }

                if self._serial and self._serial.is_open:
                    self._serial.close()

                self._serial = pyserial.Serial(
                    port=self._port,
                    baudrate=self._baudrate,
                    parity=parity_map.get(self._parity, pyserial.PARITY_NONE),
                    stopbits=stopbits_map.get(self._stopbits, pyserial.STOPBITS_ONE),
                    bytesize=self._bytesize,
                    timeout=self._timeout,
                )
                return self._serial.is_open

            except ImportError:
                logger.error(
                    "pyserial 패키지가 설치되지 않았습니다. "
                    "RTU 모드 사용: pip install pyserial"
                )
                return False
            except Exception as e:
                logger.error(f"Modbus RTU serial open error ({self._port}): {e}")
                return False

    async def disconnect(self) -> None:
        async with self._connection_lock:
            if self._serial and self._serial.is_open:
                try:
                    self._serial.close()
                except Exception:
                    pass
            self._serial = None

    async def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    async def send_request(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        quantity: int,
    ) -> Optional[bytes]:
        async with self._request_lock:
            try:
                if not await self.is_connected():
                    return None

                loop = asyncio.get_running_loop()
                return await loop.run_in_executor(
                    None,
                    self._sync_send_request,
                    unit_id, function_code, start_address, quantity,
                )
            except Exception as e:
                logger.error(f"Modbus RTU error: {e}")
                return None

    def _sync_send_request(
        self,
        unit_id: int,
        function_code: int,
        start_address: int,
        quantity: int,
    ) -> Optional[bytes]:
        """동기 시리얼 송수신 (executor 스레드에서 실행)."""
        import time

        try:
            # RTU 프레임 빌드
            pdu = struct.pack('>BHH', function_code, start_address, quantity)
            frame_no_crc = struct.pack('B', unit_id) + pdu
            frame = ModbusCRC.append(frame_no_crc)

            # Inter-frame delay
            time.sleep(self._inter_frame_delay)

            # 입력 버퍼 클리어
            self._serial.reset_input_buffer()

            # 전송
            self._serial.write(frame)
            self._serial.flush()

            # 응답 헤더: SlaveAddr(1) + FC(1) + ByteCount(1) = 3 bytes
            header = self._serial.read(3)
            if len(header) < 3:
                logger.error(
                    f"Modbus RTU incomplete header ({len(header)}/3 bytes) "
                    f"from {self._port}"
                )
                return None

            resp_fc = header[1]

            # Exception response
            if resp_fc & 0x80:
                # Exception의 경우: header는 [unit, fc|0x80, exc_code]
                # CRC 2바이트 소진
                self._serial.read(2)
                exc_code = header[2]
                exc_msg = MODBUS_EXCEPTIONS.get(exc_code, f"Unknown(0x{exc_code:02X})")
                logger.error(
                    f"Modbus RTU exception FC{function_code:02X} addr={start_address}: "
                    f"0x{exc_code:02X} {exc_msg}"
                )
                return None

            byte_count = header[2]

            # 데이터 + CRC 수신
            remaining = self._serial.read(byte_count + 2)
            if len(remaining) < byte_count + 2:
                logger.error(
                    f"Modbus RTU incomplete data ({len(remaining)}/{byte_count + 2} bytes) "
                    f"from {self._port}"
                )
                return None

            # CRC 검증
            full_frame = header + remaining
            if not ModbusCRC.verify(full_frame):
                logger.error(
                    f"Modbus RTU CRC error: FC{function_code:02X} addr={start_address}"
                )
                return None

            register_data = remaining[:byte_count]
            return register_data

        except Exception as e:
            logger.error(f"Modbus RTU serial error: {e}")
            return None


# =============================================================================
# ReadGroup (기존 코드 유지)
# =============================================================================

class ReadGroup:
    """
    최적화된 레지스터 읽기 그룹.

    연속된 주소의 태그들을 하나의 그룹으로 묶어
    한 번의 Modbus 요청으로 읽습니다.

    Attributes:
        start_address: 시작 주소
        register_type: 레지스터 타입 ('holding' or 'input')
        tags: 포함된 태그 정보 리스트
        end_address: 끝 주소 (마지막 태그의 마지막 레지스터)
    """

    __slots__ = ['start_address', 'register_type', 'tags', 'end_address']

    def __init__(self, start_address: int, register_type: str):
        """
        Args:
            start_address: 시작 주소
            register_type: 레지스터 타입
        """
        self.start_address = start_address
        self.register_type = register_type
        self.tags: List[Tuple[int, TagDefinition, int]] = []  # (address, tag, size)
        self.end_address = start_address

    def add_tag(self, address: int, tag: TagDefinition, size: int) -> None:
        """
        태그 추가.

        Args:
            address: 레지스터 주소
            tag: 태그 정의
            size: 레지스터 크기
        """
        self.tags.append((address, tag, size))
        self.end_address = max(self.end_address, address + size)

    @property
    def register_count(self) -> int:
        """읽어야 할 레지스터 수."""
        return self.end_address - self.start_address

    def __repr__(self) -> str:
        return (
            f"ReadGroup({self.register_type}[{self.start_address}:"
            f"{self.end_address}], tags={len(self.tags)})"
        )


# =============================================================================
# Modbus Collector
# =============================================================================

class ModbusCollector(BaseCollector):
    """
    Modbus 데이터 수집기 (Pure Python).

    TCP, RTU, RTU-over-TCP 3개 모드를 지원합니다.
    TCP/RTU-over-TCP는 외부 라이브러리 불필요.
    RTU 시리얼은 pyserial 필요 (lazy import).

    Supported Registers:
        - Holding Registers (Function Code 03)
        - Input Registers (Function Code 04)

    Address Format:
        - D0, D100: Holding Register
        - I0, I100: Input Register
        - HR0, HR100: Holding Register
        - IR0, IR100: Input Register
        - 숫자만: Holding Register로 간주
    """

    # 한 번에 읽을 수 있는 최대 레지스터 수 (Modbus 제한)
    MAX_REGISTERS_PER_READ = 125

    # 연속 주소로 간주할 최대 간격 (기본값)
    DEFAULT_MAX_GAP = 100

    # 최대 허용 간격
    ABSOLUTE_MAX_GAP = 500

    # Function Codes
    FC_READ_HOLDING = 0x03
    FC_READ_INPUT = 0x04

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

        self._read_groups: Dict[str, List[ReadGroup]] = {}
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5

        # 프로토콜 설정 추출
        if self._protocol_config:
            self._host = self._protocol_config.host
            self._port = self._protocol_config.port
            self._unit_id = self._protocol_config.unit_id
            self._timeout = self._protocol_config.timeout_ms / 1000.0

            extra = self._protocol_config.extra or {}
            self._max_address_gap = min(
                extra.get('max_address_gap', self.DEFAULT_MAX_GAP),
                self.ABSOLUTE_MAX_GAP
            )

            # 모드 결정
            mode_str = extra.get('mode', 'tcp').lower()
            try:
                self._mode = ModbusMode(mode_str)
            except ValueError:
                logger.warning(
                    f"[{self._name}] Unknown mode '{mode_str}', falling back to TCP"
                )
                self._mode = ModbusMode.TCP
        else:
            self._host = "127.0.0.1"
            self._port = 502
            self._unit_id = 1
            self._timeout = 5.0
            self._max_address_gap = self.DEFAULT_MAX_GAP
            self._mode = ModbusMode.TCP

        # Transport 생성
        self._transport = self._create_transport()

        logger.info(
            f"[{self._name}] Modbus initialized - Mode: {self._mode.value}, "
            f"Target: {self._get_target_info()}"
        )

    def _create_transport(self) -> ModbusTransport:
        """모드에 따른 Transport 생성."""
        extra = (self._protocol_config.extra or {}) if self._protocol_config else {}

        if self._mode == ModbusMode.TCP:
            return ModbusTcpTransport(
                host=self._host,
                port=self._port,
                timeout=self._timeout,
            )
        elif self._mode == ModbusMode.RTU:
            return ModbusRtuTransport(
                port=extra.get('serial_port', 'COM1'),
                baudrate=extra.get('baudrate', 9600),
                timeout=self._timeout,
                parity=extra.get('parity', 'N'),
                stopbits=extra.get('stopbits', 1),
                bytesize=extra.get('bytesize', 8),
            )
        elif self._mode == ModbusMode.RTU_OVER_TCP:
            return ModbusRtuOverTcpTransport(
                host=self._host,
                port=self._port,
                timeout=self._timeout,
            )
        else:
            raise ValueError(f"Unknown Modbus mode: {self._mode}")

    def _get_target_info(self) -> str:
        """로그용 대상 정보 문자열."""
        if self._mode == ModbusMode.RTU:
            extra = (self._protocol_config.extra or {}) if self._protocol_config else {}
            port = extra.get('serial_port', 'COM1')
            baud = extra.get('baudrate', 9600)
            return f"{port}@{baud}"
        return f"{self._host}:{self._port}"

    # =========================================================================
    # Tag Registration & Optimization (기존 로직 그대로)
    # =========================================================================

    def register_tags(self, group: str, tags: List[TagDefinition]) -> None:
        """
        태그 등록 및 읽기 그룹 최적화.

        Args:
            group: 수집 그룹명
            tags: 태그 정의 리스트
        """
        super().register_tags(group, tags)

        self._read_groups[group] = self._optimize_read_groups(tags)

        logger.info(
            f"[{self._name}] Optimized {len(tags)} tags into "
            f"{len(self._read_groups[group])} read groups for '{group}'"
        )

    def _optimize_read_groups(self, tags: List[TagDefinition]) -> List[ReadGroup]:
        """
        태그들을 연속 주소 그룹으로 최적화.

        Args:
            tags: 태그 정의 리스트

        Returns:
            최적화된 ReadGroup 리스트
        """
        if not tags:
            return []

        parsed_tags: List[Tuple[int, str, TagDefinition]] = []
        for tag in tags:
            address, reg_type = self._parse_address(tag.address)
            size = self._get_register_size(tag.data_type)
            parsed_tags.append((address, reg_type, tag, size))

        holding_tags = [(a, t, s) for a, rt, t, s in parsed_tags if rt == 'holding']
        input_tags = [(a, t, s) for a, rt, t, s in parsed_tags if rt == 'input']

        groups: List[ReadGroup] = []
        groups.extend(self._create_read_groups(holding_tags, 'holding'))
        groups.extend(self._create_read_groups(input_tags, 'input'))

        return groups

    def _create_read_groups(
        self,
        tags: List[Tuple[int, TagDefinition, int]],
        reg_type: str
    ) -> List[ReadGroup]:
        """
        연속 주소 태그들을 ReadGroup으로 묶기.

        Args:
            tags: (주소, 태그, 크기) 튜플 리스트
            reg_type: 레지스터 타입

        Returns:
            ReadGroup 리스트
        """
        if not tags:
            return []

        sorted_tags = sorted(tags, key=lambda x: x[0])
        groups: List[ReadGroup] = []

        current_group = ReadGroup(
            start_address=sorted_tags[0][0],
            register_type=reg_type,
        )
        current_group.add_tag(sorted_tags[0][0], sorted_tags[0][1], sorted_tags[0][2])

        for i in range(1, len(sorted_tags)):
            address, tag, size = sorted_tags[i]
            prev_end = current_group.end_address
            gap = address - prev_end
            total_size = address + size - current_group.start_address

            if gap <= self._max_address_gap and total_size <= self.MAX_REGISTERS_PER_READ:
                current_group.add_tag(address, tag, size)
            else:
                groups.append(current_group)
                current_group = ReadGroup(
                    start_address=address,
                    register_type=reg_type,
                )
                current_group.add_tag(address, tag, size)

        groups.append(current_group)

        total_tags = len(sorted_tags)
        total_requests = len(groups)
        if total_tags > 0:
            logger.debug(
                f"[{self._name}] Optimization: {total_tags} tags → {total_requests} requests "
                f"({reg_type}, gap={self._max_address_gap})"
            )

        return groups

    def _parse_address(self, address: str) -> Tuple[int, str]:
        """
        주소 문자열 파싱.

        Args:
            address: 주소 문자열 (예: "D0", "I100", "100")

        Returns:
            (레지스터 주소, 레지스터 타입) 튜플
        """
        address = address.strip().upper()

        if address.startswith('D'):
            return int(address[1:]), 'holding'
        elif address.startswith('I'):
            return int(address[1:]), 'input'
        elif address.startswith('HR'):
            return int(address[2:]), 'holding'
        elif address.startswith('IR'):
            return int(address[2:]), 'input'
        else:
            return int(address), 'holding'

    def _get_register_size(self, data_type: DataType) -> int:
        """
        데이터 타입에 따른 레지스터 크기 반환.

        Args:
            data_type: 데이터 타입

        Returns:
            필요한 레지스터 수 (16비트 단위)
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

    # =========================================================================
    # Connection Management (Transport 위임)
    # =========================================================================

    async def _do_connect(self) -> bool:
        """연결."""
        success = await self._transport.connect()
        if success:
            self._consecutive_failures = 0
            logger.info(
                f"[{self._name}] Connected ({self._mode.value}) "
                f"to {self._get_target_info()}"
            )
        else:
            logger.error(
                f"[{self._name}] Failed to connect ({self._mode.value}) "
                f"to {self._get_target_info()}"
            )
        return success

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        await self._transport.disconnect()

    async def _do_health_check(self) -> bool:
        """연결 상태 확인."""
        return await self._transport.is_connected()

    async def _ensure_connection(self) -> bool:
        """연결 상태 확인 및 필요시 재연결."""
        if await self._transport.is_connected():
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
        Modbus 데이터 수집.

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
        all_registers: Dict[str, Dict[int, int]] = {
            'holding': {},
            'input': {},
        }

        try:
            for rg in read_groups:
                registers = await self._read_group(rg)
                if registers is not None:
                    all_registers[rg.register_type].update(registers)
                else:
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_consecutive_failures:
                        logger.error(
                            f"[{self._name}] Too many failures, marking connection as error"
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
                    'registers': all_registers,
                    'tags': self._tags.get(group, []),
                },
            )

        except Exception as e:
            logger.error(f"[{self._name}] Collection error: {e}")
            self._consecutive_failures += 1
            return None

    async def _read_group(self, read_group: ReadGroup) -> Optional[Dict[int, int]]:
        """
        단일 ReadGroup 읽기.

        Transport에서 받은 raw bytes를 {주소: 값} dict로 변환.

        Args:
            read_group: 읽기 그룹

        Returns:
            {주소: 값} 딕셔너리, 실패 시 None
        """
        try:
            count = read_group.register_count
            fc = (self.FC_READ_HOLDING
                  if read_group.register_type == 'holding'
                  else self.FC_READ_INPUT)

            register_data = await self._transport.send_request(
                unit_id=self._unit_id,
                function_code=fc,
                start_address=read_group.start_address,
                quantity=count,
            )

            if register_data is None:
                return None

            # bytes → register dict
            # Modbus 레지스터 데이터는 항상 big-endian, 레지스터당 2바이트
            registers: Dict[int, int] = {}
            for i in range(count):
                offset = i * 2
                if offset + 2 <= len(register_data):
                    value = struct.unpack('>H', register_data[offset:offset + 2])[0]
                    registers[read_group.start_address + i] = value

            return registers

        except asyncio.TimeoutError:
            logger.error(
                f"[{self._name}] Timeout reading "
                f"{read_group.register_type}[{read_group.start_address}]"
            )
            self._state = ConnectionState.ERROR
            return None

        except Exception as e:
            logger.error(f"[{self._name}] Read error: {e}")
            return None
