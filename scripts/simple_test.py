#!/usr/bin/env python3
"""
간단한 MC Protocol 연결 테스트
"""

import asyncio
import struct
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def test_mc_connection():
    """MC Protocol 시뮬레이터 연결 테스트."""
    print("=" * 60)
    print("MC Protocol Connection Test (PLC-A 주소 범위)")
    print("=" * 60, flush=True)

    # 시뮬레이터에 연결
    host = "127.0.0.1"
    port = 5000

    print(f"\nConnecting to {host}:{port}...", flush=True)

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5.0
        )
        print("Connected!", flush=True)
    except Exception as e:
        print(f"Connection failed: {e}", flush=True)
        return

    try:
        # === D 레지스터 테스트 (0~4956) ===
        print("\n[D Register] Reading D200-D209 (생산 카운터 영역)...", flush=True)
        await test_read(writer, reader, 0xA8, 200, 10, "D200-D209")

        # === L 래치 릴레이 테스트 (5~3071) ===
        print("\n[L Relay] Reading L100-L115 (알람 영역)...", flush=True)
        await test_read(writer, reader, 0x92, 100, 16, "L100-L115")

        # === M 릴레이 테스트 (0~1009) ===
        print("\n[M Relay] Reading M800-M815...", flush=True)
        await test_read(writer, reader, 0x90, 800, 16, "M800-M815")

        # === X 입력 테스트 (110~410) ===
        print("\n[X Input] Reading X110-X125 (센서 입력)...", flush=True)
        await test_read(writer, reader, 0x9C, 110, 16, "X110-X125")

        # === Y 출력 테스트 (442~1115) ===
        print("\n[Y Output] Reading Y442-Y457...", flush=True)
        await test_read(writer, reader, 0x9D, 442, 16, "Y442-Y457")

    finally:
        writer.close()
        await writer.wait_closed()
        print("\nConnection closed.", flush=True)

    print("\n=== All tests completed successfully! ===", flush=True)


async def test_read(writer, reader, device_code: int, start_addr: int, count: int, desc: str):
    """디바이스 읽기 테스트."""
    request = build_read_request(device_code, start_addr, count)
    writer.write(request)
    await writer.drain()

    response = await asyncio.wait_for(reader.read(1024), timeout=5.0)

    if len(response) >= 11:
        end_code = struct.unpack('<H', response[9:11])[0]
        if end_code == 0:
            data = response[11:]
            # 비트 디바이스인지 워드 디바이스인지 판단
            if device_code in (0x92, 0x90, 0x9C, 0x9D):  # L, M, X, Y
                if len(data) >= 2:
                    word = struct.unpack('<H', data[0:2])[0]
                    bits = [(word >> i) & 1 for i in range(min(16, count))]
                    print(f"  {desc}: {bits} (OK)", flush=True)
            else:  # D 등 워드 디바이스
                values = []
                for i in range(0, min(len(data), count*2), 2):
                    if i + 1 < len(data):
                        values.append(struct.unpack('<H', data[i:i+2])[0])
                print(f"  {desc}: {values[:5]}... (OK)", flush=True)
        else:
            print(f"  {desc}: Error {hex(end_code)}", flush=True)
    else:
        print(f"  {desc}: Invalid response", flush=True)


def build_read_request(device_code: int, start_addr: int, count: int) -> bytes:
    """MC Protocol 3E Frame 읽기 요청 생성."""
    request = bytearray()

    # 서브헤더 (Big endian) - 0x5000
    request.extend(struct.pack('>H', 0x5000))

    # 네트워크 번호
    request.append(0x00)

    # PC 번호
    request.append(0xFF)

    # 요청 대상 모듈 IO 번호 (Little endian)
    request.extend(struct.pack('<H', 0x03FF))

    # 요청 대상 모듈 국번호
    request.append(0x00)

    # 요청 데이터 길이 (12바이트: command(2) + subcommand(2) + start(3) + device(1) + count(2) + timeout(2) = 12)
    request.extend(struct.pack('<H', 12))

    # 감시 타이머 (타임아웃, Little endian) - 0x0000 또는 필요한 값
    request.extend(struct.pack('<H', 0x0000))

    # 커맨드 (배치 읽기: 0x0401)
    request.extend(struct.pack('<H', 0x0401))

    # 서브커맨드 (워드 단위: 0x0000)
    request.extend(struct.pack('<H', 0x0000))

    # 시작 주소 (3바이트, Little endian)
    request.append(start_addr & 0xFF)
    request.append((start_addr >> 8) & 0xFF)
    request.append((start_addr >> 16) & 0xFF)

    # 디바이스 코드
    request.append(device_code)

    # 읽을 포인트 수 (Little endian)
    request.extend(struct.pack('<H', count))

    return bytes(request)


async def main():
    """메인 함수."""
    # 시뮬레이터 시작
    print("Starting simulator...", flush=True)

    from pathlib import Path
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from tests.mc_protocol_test.mc_simulator import McProtocolSimulator

    simulator = McProtocolSimulator(host="127.0.0.1", port=5000)

    async def run_simulator():
        await simulator.start()

    # 시뮬레이터를 백그라운드에서 실행
    sim_task = asyncio.create_task(run_simulator())

    # 시뮬레이터 시작 대기
    await asyncio.sleep(1)

    try:
        # 연결 테스트
        await test_mc_connection()
    finally:
        # 시뮬레이터 종료
        sim_task.cancel()
        try:
            await sim_task
        except asyncio.CancelledError:
            pass


if __name__ == "__main__":
    asyncio.run(main())
