"""
MC Protocol Simulator
======================

미쓰비시 PLC를 시뮬레이션하는 간단한 TCP 서버.
테스트용으로 다양한 디바이스 값을 반환합니다.

MC Protocol (Binary, 3E Frame):
- 포트: 5000 (기본)
- D, L, M, X, Y 레지스터 읽기 지원
"""

import asyncio
import struct
import random
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


# 디바이스 코드 상수
DEVICE_CODES = {
    0xA8: 'D',   # Data Register (word)
    0x92: 'L',   # Latch Relay (bit)
    0x90: 'M',   # Internal Relay (bit)
    0x9C: 'X',   # Input (bit)
    0x9D: 'Y',   # Output (bit)
    0xB4: 'W',   # Link Register (word)
    0xAF: 'R',   # File Register (word)
}

# 비트 디바이스 목록
BIT_DEVICES = {'L', 'M', 'X', 'Y', 'B', 'F', 'V'}


class McProtocolSimulator:
    """MC Protocol 시뮬레이터."""

    def __init__(self, host: str = "0.0.0.0", port: int = 5000):
        self.host = host
        self.port = port
        self.running = False

        # 디바이스별 메모리
        self.d_registers: dict[int, int] = {}   # D: 워드 디바이스
        self.l_bits: dict[int, bool] = {}       # L: 래치 릴레이 (비트)
        self.m_bits: dict[int, bool] = {}       # M: 내부 릴레이 (비트)
        self.x_bits: dict[int, bool] = {}       # X: 입력 (비트)
        self.y_bits: dict[int, bool] = {}       # Y: 출력 (비트)

        # 초기 데이터 설정
        self._init_registers()

    def _init_registers(self):
        """레지스터 초기값 설정."""
        # === D 레지스터 (워드 디바이스) ===

        # D0-D39999: 일반 레지스터 (INT16/UINT32/FLOAT32)
        for i in range(40000):
            self.d_registers[i] = random.randint(0, 65535)

        # D61-D72: 점검 결과 (INT16) - 위에서 이미 설정됨
        for i in range(61, 73):
            self.d_registers[i] = random.randint(0, 2)  # 0=OK, 1=NG, 2=미검사

        # D100: 현재모델 (INT16)
        self.d_registers[100] = 24

        # D200-D201: 총카운트 (UINT32) = 123456
        self.d_registers[200] = 123456 & 0xFFFF
        self.d_registers[201] = (123456 >> 16) & 0xFFFF

        # D202-D203: 양품카운트 (UINT32) = 120000
        self.d_registers[202] = 120000 & 0xFFFF
        self.d_registers[203] = (120000 >> 16) & 0xFFFF

        # D204-D205: 불량카운트 (UINT32) = 3456
        self.d_registers[204] = 3456 & 0xFFFF
        self.d_registers[205] = (3456 >> 16) & 0xFFFF

        # D206-D207: 합격률 (FLOAT32) = 97.2
        self._set_float32(206, 97.2)

        # D208-D209: 불량률 (FLOAT32) = 2.8
        self._set_float32(208, 2.8)

        # D210-D218: 수율/효율 (FLOAT32)
        self._set_float32(210, 98.5)   # Yield_Rate
        self._set_float32(212, 87.2)   # OEE
        self._set_float32(214, 92.1)   # Availability
        self._set_float32(216, 94.8)   # Performance
        self._set_float32(218, 99.1)   # Quality

        # D220-D224: 시간/속도
        self._set_uint32(220, 12500)   # Cycle_Time (12.5초, scale 0.001)
        self.d_registers[222] = 350    # Tact_Time (3.5초, scale 0.01)
        self.d_registers[224] = 125    # Line_Speed (12.5 m/min, scale 0.1)

        # D300-D311: 센서 데이터
        self.d_registers[300] = 285    # Zone1_Temp (28.5C)
        self.d_registers[301] = 290    # Zone2_Temp (29.0C)
        self.d_registers[302] = 275    # Zone3_Temp (27.5C)
        self.d_registers[310] = 520    # Main_Pressure (5.20 MPa)
        self.d_registers[311] = 380    # Sub_Pressure (3.80 MPa)

        # D280, D282: ON/OFF 지연 (UINT16, scale 0.01)
        self.d_registers[280] = 50   # 0.5초
        self.d_registers[282] = 30   # 0.3초

        # D390-D406: CCD 간극 상한/하한 (FLOAT32)
        for i in range(390, 408, 2):
            self._set_float32(i, random.uniform(0.1, 0.5))

        # D796-D816: 각종 카운터 (UINT32)
        for addr in [796, 798, 800, 802, 804, 806, 808, 810, 812, 816]:
            self._set_uint32(addr, random.randint(100, 10000))

        # D820-D876: 각종 불량률 (FLOAT32)
        for addr in range(820, 878, 2):
            self._set_float32(addr, random.uniform(0.1, 5.0))

        # D900-D914: 현재 모델 문자열 (STRING, 15워드)
        model_name = "MODEL-A1234"
        for i, char in enumerate(model_name):
            if i < 15:
                self.d_registers[900 + i] = ord(char)

        # D1000-D4000: 기타 레지스터 초기화 (일부만)
        for addr in range(1000, 4000, 2):
            if random.random() < 0.3:  # 30% 확률로 값 설정
                self.d_registers[addr] = random.randint(0, 65535)

        # === L 릴레이 (래치 릴레이 - 비트) ===
        # L0-L9999: 일반 래치 릴레이
        for i in range(10000):
            self.l_bits[i] = random.random() < 0.2  # 20% ON

        # L0-L12: 시스템 상태 (오버라이드)
        self.l_bits[0] = True    # ON: 시스템 준비완료
        self.l_bits[1] = True    # ON: 자동운전 모드
        self.l_bits[2] = False   # OFF: 수동운전 모드
        self.l_bits[10] = True   # ON: 운전중
        self.l_bits[11] = False  # OFF: 일시정지
        self.l_bits[12] = False  # OFF: 비상정지

        # L100-L107: 알람 상태
        for i in range(100, 108):
            self.l_bits[i] = random.random() < 0.1  # 10% 알람

        # L200-L205: 레시피/모드 상태
        self.l_bits[200] = True  # 레시피 로드됨
        self.l_bits[201] = True  # 자동시작 허용
        self.l_bits[202] = False # 원격모드
        self.l_bits[203] = True  # 로컬모드
        self.l_bits[204] = False # 유지보수모드
        self.l_bits[205] = True  # 예열완료

        # L101-L170: 스테이션 상태
        for i in range(101, 171):
            self.l_bits[i] = random.random() < 0.1  # 10% ON

        # L581-L3056: 각종 알람/상태
        for i in range(581, 3057):
            self.l_bits[i] = random.random() < 0.05  # 5% ON

        # === M 릴레이 (내부 릴레이 - 비트) ===
        # M0-M1100: PLC-A 주소 범위 커버 (0~1009)
        for i in range(1101):
            self.m_bits[i] = random.random() < 0.3  # 30% ON

        # M0-M3: 운전 모드 (오버라이드)
        self.m_bits[0] = True    # ON: 사이클 시작
        self.m_bits[1] = False   # OFF: 사이클 완료
        self.m_bits[2] = False   # OFF: 정리중
        self.m_bits[10] = True   # ON: 부품 감지
        self.m_bits[20] = True   # ON: 클램프 닫힘
        self.m_bits[21] = False  # OFF: 클램프 열림

        # M410, M802, M803, M806: 버튼/표시
        self.m_bits[410] = False
        self.m_bits[802] = False
        self.m_bits[803] = True   # 레시피 변경 표시
        self.m_bits[806] = False

        # M2000-M2035: 테스트 결과
        for i in range(2000, 2036):
            self.m_bits[i] = random.random() < 0.9  # 90% OK

        # === X 입력 (비트) ===
        # X0-X500: PLC-A 주소 범위 커버 (110~410)
        for addr in range(501):
            self.x_bits[addr] = random.random() < 0.3  # 30% ON

        # === Y 출력 (비트) ===
        # Y0-Y1200: PLC-A 주소 범위 커버 (442~1115)
        for addr in range(1201):
            self.y_bits[addr] = random.random() < 0.2  # 20% ON

        logger.info("Registers initialized (D, L, M, X, Y)")

    def _set_float32(self, addr: int, value: float):
        """FLOAT32 값을 D 레지스터에 설정."""
        float_bytes = struct.pack('<f', value)
        word1, word2 = struct.unpack('<HH', float_bytes)
        self.d_registers[addr] = word1
        self.d_registers[addr + 1] = word2

    def _set_uint32(self, addr: int, value: int):
        """UINT32 값을 D 레지스터에 설정."""
        self.d_registers[addr] = value & 0xFFFF
        self.d_registers[addr + 1] = (value >> 16) & 0xFFFF

    def _update_registers(self):
        """레지스터 값 업데이트 (시뮬레이션)."""
        # 카운트 증가
        total = (self.d_registers.get(201, 0) << 16) | self.d_registers.get(200, 0)
        total += random.randint(0, 5)
        self.d_registers[200] = total & 0xFFFF
        self.d_registers[201] = (total >> 16) & 0xFFFF

        good = (self.d_registers.get(203, 0) << 16) | self.d_registers.get(202, 0)
        good += random.randint(0, 4)
        self.d_registers[202] = good & 0xFFFF
        self.d_registers[203] = (good >> 16) & 0xFFFF

        bad = (self.d_registers.get(205, 0) << 16) | self.d_registers.get(204, 0)
        bad += random.randint(0, 1)
        self.d_registers[204] = bad & 0xFFFF
        self.d_registers[205] = (bad >> 16) & 0xFFFF

        # 합격률 계산
        if total > 0:
            yield_rate = (good / total) * 100
            self._set_float32(206, yield_rate)
            self._set_float32(208, 100 - yield_rate)

        # 일부 비트 토글 (시뮬레이션)
        if random.random() < 0.1:
            addr = random.choice(list(self.l_bits.keys()))
            self.l_bits[addr] = not self.l_bits[addr]

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """클라이언트 연결 처리."""
        addr = writer.get_extra_info('peername')
        logger.info(f"Client connected: {addr}")

        try:
            while self.running:
                # 요청 헤더 읽기 (최소 21바이트)
                header = await asyncio.wait_for(reader.read(21), timeout=30.0)
                if not header:
                    break

                if len(header) < 21:
                    logger.warning(f"Short header: {len(header)} bytes")
                    continue

                # MC Protocol 3E Frame 파싱
                response = self._process_request(header, reader)
                if response:
                    writer.write(response)
                    await writer.drain()

                # 값 업데이트
                self._update_registers()

        except asyncio.TimeoutError:
            logger.info(f"Client timeout: {addr}")
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info(f"Client disconnected: {addr}")

    def _process_request(self, data: bytes, reader) -> bytes:
        """MC Protocol 요청 처리."""
        if len(data) < 21:
            return self._error_response(0x0001)

        subheader = struct.unpack('>H', data[0:2])[0]  # Big-endian!
        if subheader != 0x5000:
            logger.warning(f"Invalid subheader: {hex(subheader)}")
            return self._error_response(0x0001)

        command = struct.unpack('<H', data[11:13])[0]

        # 배치 읽기 커맨드 (0x0401)
        if command == 0x0401:
            return self._read_device(data)

        logger.warning(f"Unsupported command: {hex(command)}")
        return self._error_response(0x0002)

    def _read_device(self, data: bytes) -> bytes:
        """디바이스 읽기 처리."""
        # 시작 주소 (3바이트, 리틀 엔디안)
        start_addr = data[15] | (data[16] << 8) | (data[17] << 16)
        device_code = data[18]
        count = struct.unpack('<H', data[19:21])[0]

        device_name = DEVICE_CODES.get(device_code, None)
        if device_name is None:
            logger.warning(f"Unsupported device code: {hex(device_code)}")
            return self._error_response(0x0003)

        logger.debug(f"Read {device_name}{start_addr}, count={count}")

        # 디바이스 타입에 따라 읽기
        if device_name == 'D':
            values = self._read_word_device(self.d_registers, start_addr, count)
        elif device_name == 'L':
            values = self._read_bit_device(self.l_bits, start_addr, count)
        elif device_name == 'M':
            values = self._read_bit_device(self.m_bits, start_addr, count)
        elif device_name == 'X':
            values = self._read_bit_device(self.x_bits, start_addr, count)
        elif device_name == 'Y':
            values = self._read_bit_device(self.y_bits, start_addr, count)
        else:
            # 기타 워드 디바이스는 0으로 응답
            values = [0] * count

        return self._success_response(values)

    def _read_word_device(self, registers: dict, start_addr: int, count: int) -> list[int]:
        """워드 디바이스 읽기."""
        values = []
        for i in range(count):
            addr = start_addr + i
            value = registers.get(addr, 0)
            values.append(value)
        return values

    def _read_bit_device(self, bits: dict, start_addr: int, count: int) -> list[int]:
        """비트 디바이스 읽기 (워드 단위로 반환)."""
        # MC Protocol에서 비트 디바이스 읽기는 포인트 수만큼 비트를 읽고
        # 워드로 패킹하여 반환합니다.
        # count는 읽을 비트 수
        values = []

        for i in range(0, count, 16):
            word = 0
            for bit_offset in range(16):
                bit_addr = start_addr + i + bit_offset
                if i + bit_offset < count:
                    if bits.get(bit_addr, False):
                        word |= (1 << bit_offset)
            values.append(word)

        # 개별 비트를 워드로 반환할 수도 있음 (단일 비트 읽기)
        if count == 1:
            return [1 if bits.get(start_addr, False) else 0]

        return values

    def _success_response(self, values: list[int]) -> bytes:
        """성공 응답 생성."""
        # 응답 데이터
        data_bytes = b''.join(struct.pack('<H', v) for v in values)

        # 응답 헤더
        response = bytearray()
        response.extend(struct.pack('<H', 0xD000))  # 서브헤더
        response.extend(b'\x00\xFF')  # 네트워크 번호, PC 번호
        response.extend(struct.pack('<H', 0x03FF))  # 요청 대상 모듈
        response.extend(b'\x00')  # 국번호
        response.extend(struct.pack('<H', len(data_bytes) + 2))  # 응답 데이터 길이
        response.extend(struct.pack('<H', 0x0000))  # 종료 코드 (정상)
        response.extend(data_bytes)

        return bytes(response)

    def _error_response(self, error_code: int) -> bytes:
        """에러 응답 생성."""
        response = bytearray()
        response.extend(struct.pack('<H', 0xD000))
        response.extend(b'\x00\xFF')
        response.extend(struct.pack('<H', 0x03FF))
        response.extend(b'\x00')
        response.extend(struct.pack('<H', 2))  # 응답 데이터 길이
        response.extend(struct.pack('<H', error_code))  # 에러 코드
        return bytes(response)

    async def start(self):
        """서버 시작."""
        self.running = True
        server = await asyncio.start_server(
            self.handle_client,
            self.host,
            self.port
        )

        addr = server.sockets[0].getsockname()
        logger.info(f"MC Protocol Simulator started on {addr}")

        async with server:
            await server.serve_forever()


async def main():
    simulator = McProtocolSimulator(port=5000)
    await simulator.start()


if __name__ == "__main__":
    asyncio.run(main())
