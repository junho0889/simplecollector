"""
Modbus TCP Simulator
====================
Modbus TCP 프로토콜을 시뮬레이션하는 간단한 서버.
FC03 (Read Holding Registers), FC04 (Read Input Registers)를 지원합니다.

레지스터 값은 주기적으로 변경되어 실제 PLC처럼 동작합니다.

Usage:
    python modbus_simulator.py [--host 0.0.0.0] [--port 502] [--unit-id 1]
"""

import asyncio
import struct
import random
import math
import time
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("modbus_sim")

# MBAP Header size
MBAP_HEADER_SIZE = 7


class ModbusSimulator:
    """Modbus TCP 시뮬레이터."""

    def __init__(self, unit_id: int = 1):
        self.unit_id = unit_id
        # Holding Registers (FC03): 0~999
        self.holding_registers: dict[int, int] = {}
        # Input Registers (FC04): 0~999
        self.input_registers: dict[int, int] = {}
        self._start_time = time.time()
        self._init_registers()

    def _init_registers(self):
        """초기 레지스터 값 설정."""
        # Holding Registers: 0~199
        for i in range(200):
            self.holding_registers[i] = 0

        # Input Registers: 0~99
        for i in range(100):
            self.input_registers[i] = 0

    def update_registers(self):
        """레지스터 값 업데이트 (시뮬레이션)."""
        elapsed = time.time() - self._start_time

        # --- Holding Registers ---
        # D0: 카운터 (0~65535 순환)
        self.holding_registers[0] = int(elapsed) % 65536

        # D1: 사인파 (0~1000)
        self.holding_registers[1] = int(500 + 500 * math.sin(elapsed * 0.5))

        # D2: 코사인파 (0~1000)
        self.holding_registers[2] = int(500 + 500 * math.cos(elapsed * 0.5))

        # D3: 랜덤 노이즈 (0~100)
        self.holding_registers[3] = random.randint(0, 100)

        # D10-D11: 온도 (int32, 2 registers) - 2500 = 25.00°C 기준
        temp = int(2500 + 200 * math.sin(elapsed * 0.1) + random.randint(-10, 10))
        self.holding_registers[10] = (temp >> 16) & 0xFFFF  # High word
        self.holding_registers[11] = temp & 0xFFFF           # Low word

        # D12-D13: 압력 (float32, 2 registers) - 1.0~5.0 bar
        pressure = 3.0 + 2.0 * math.sin(elapsed * 0.2)
        p_bytes = struct.pack('>f', pressure)
        self.holding_registers[12] = struct.unpack('>H', p_bytes[0:2])[0]
        self.holding_registers[13] = struct.unpack('>H', p_bytes[2:4])[0]

        # D20-D21: 유량 (float32)
        flow = 100.0 + 50.0 * math.sin(elapsed * 0.3) + random.uniform(-5, 5)
        f_bytes = struct.pack('>f', flow)
        self.holding_registers[20] = struct.unpack('>H', f_bytes[0:2])[0]
        self.holding_registers[21] = struct.unpack('>H', f_bytes[2:4])[0]

        # D30: 상태 비트 (0/1 토글)
        self.holding_registers[30] = 1 if int(elapsed) % 10 < 5 else 0

        # D31: 알람 코드
        self.holding_registers[31] = random.choice([0, 0, 0, 0, 1, 2, 3])

        # D100-D109: 생산 카운터들 (uint16)
        for i in range(10):
            self.holding_registers[100 + i] = (
                int(elapsed * (i + 1) * 0.1) % 10000
            )

        # --- Input Registers ---
        # I0: 전압 (uint16, x10 = 2200 → 220.0V)
        self.input_registers[0] = int(2200 + 100 * math.sin(elapsed * 0.4))

        # I1: 전류 (uint16, x100 = 500 → 5.00A)
        self.input_registers[1] = int(500 + 200 * math.sin(elapsed * 0.3))

        # I2: 주파수 (uint16, x10 = 600 → 60.0Hz)
        self.input_registers[2] = int(600 + random.randint(-2, 2))

    def read_holding_registers(self, start: int, count: int) -> bytes | None:
        """FC03: Read Holding Registers."""
        data = bytearray()
        for addr in range(start, start + count):
            val = self.holding_registers.get(addr, 0)
            data.extend(struct.pack('>H', val))
        return bytes(data)

    def read_input_registers(self, start: int, count: int) -> bytes | None:
        """FC04: Read Input Registers."""
        data = bytearray()
        for addr in range(start, start + count):
            val = self.input_registers.get(addr, 0)
            data.extend(struct.pack('>H', val))
        return bytes(data)


class ModbusTcpServer:
    """Modbus TCP 서버."""

    def __init__(self, host: str, port: int, simulator: ModbusSimulator):
        self.host = host
        self.port = port
        self.sim = simulator
        self._update_task: asyncio.Task | None = None

    async def start(self):
        """서버 시작."""
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        # 주기적 레지스터 업데이트 시작
        self._update_task = asyncio.create_task(self._periodic_update())

        addr = server.sockets[0].getsockname()
        logger.info(f"Modbus TCP Simulator listening on {addr[0]}:{addr[1]}")
        logger.info(f"Unit ID: {self.sim.unit_id}")
        logger.info("Registers: Holding[0-199], Input[0-99]")

        async with server:
            await server.serve_forever()

    async def _periodic_update(self):
        """500ms 주기로 레지스터 업데이트."""
        while True:
            self.sim.update_registers()
            await asyncio.sleep(0.5)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        """클라이언트 연결 처리."""
        peer = writer.get_extra_info("peername")
        logger.info(f"Client connected: {peer}")

        try:
            while True:
                # MBAP Header (7 bytes)
                header = await reader.readexactly(MBAP_HEADER_SIZE)
                tid, proto_id, length, unit_id = struct.unpack('>HHHB', header)

                # PDU 수신
                pdu = await reader.readexactly(length - 1)
                fc = pdu[0]

                # 요청 처리
                response_pdu = self._process_request(unit_id, fc, pdu)

                # MBAP 응답 헤더
                resp_length = len(response_pdu) + 1  # unit_id + pdu
                resp_header = struct.pack('>HHHB', tid, proto_id, resp_length, unit_id)

                writer.write(resp_header + response_pdu)
                await writer.drain()

        except asyncio.IncompleteReadError:
            pass
        except ConnectionResetError:
            pass
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            logger.info(f"Client disconnected: {peer}")
            writer.close()

    def _process_request(self, unit_id: int, fc: int, pdu: bytes) -> bytes:
        """Modbus 요청 처리."""
        # Unit ID 확인
        if unit_id != self.sim.unit_id:
            # Gateway target device failed
            return bytes([fc | 0x80, 0x0B])

        if fc == 0x03:  # Read Holding Registers
            start_addr = struct.unpack('>H', pdu[1:3])[0]
            quantity = struct.unpack('>H', pdu[3:5])[0]

            if quantity < 1 or quantity > 125:
                return bytes([fc | 0x80, 0x03])  # Illegal data value

            data = self.sim.read_holding_registers(start_addr, quantity)
            byte_count = quantity * 2
            return struct.pack('BB', fc, byte_count) + data

        elif fc == 0x04:  # Read Input Registers
            start_addr = struct.unpack('>H', pdu[1:3])[0]
            quantity = struct.unpack('>H', pdu[3:5])[0]

            if quantity < 1 or quantity > 125:
                return bytes([fc | 0x80, 0x03])

            data = self.sim.read_input_registers(start_addr, quantity)
            byte_count = quantity * 2
            return struct.pack('BB', fc, byte_count) + data

        else:
            # Illegal function
            return bytes([fc | 0x80, 0x01])


async def main():
    parser = argparse.ArgumentParser(description="Modbus TCP Simulator")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--port", type=int, default=5020, help="Listen port")
    parser.add_argument("--unit-id", type=int, default=1, help="Modbus Unit ID")
    args = parser.parse_args()

    sim = ModbusSimulator(unit_id=args.unit_id)
    server = ModbusTcpServer(args.host, args.port, sim)
    await server.start()


if __name__ == "__main__":
    asyncio.run(main())
