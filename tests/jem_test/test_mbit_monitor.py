"""
M비트 모니터링 테스트 스크립트
PLC-D (192.168.0.54:5007)에서 M180, M188을 직접 읽어서 변화 감지

방법 1: 비트 읽기 (subcommand 0x0001) — collector와 동일
방법 2: 워드 읽기 (subcommand 0x0000) — HMI 방식

두 방식을 동시에 비교하여 차이를 확인
"""

import asyncio
import struct
import csv
import os
from datetime import datetime

# === MC Protocol 설정 ===
PLC_HOST = "192.168.0.54"
PLC_PORT = 5007
POLL_INTERVAL = 0.5  # 500ms

# M180 (tag_id=1021, Log1), M188 (tag_id=1022, Log3)
WATCH_BITS = [180, 184, 188]

# MC Protocol 디바이스 코드
DEVICE_M = 0x90
DEVICE_D = 0xA8

# CSV 저장 경로
CSV_PATH = os.path.join(os.path.dirname(__file__), "mbit_monitor_log.csv")


def build_3e_read_request(device_code: int, start_addr: int, count: int, subcommand: int) -> bytes:
    """MC Protocol 3E Binary 읽기 요청 프레임 생성"""
    # 데이터 부분
    data = struct.pack('<H', 0x0401)                    # 커맨드: 일괄 읽기
    data += struct.pack('<H', subcommand)                # 서브커맨드
    data += struct.pack('<I', start_addr)[:3]            # 시작 주소 (3바이트)
    data += struct.pack('<B', device_code)               # 디바이스 코드
    data += struct.pack('<H', count)                     # 포인트 수

    # 서브헤더
    header = struct.pack('>H', 0x5000)                   # 서브헤더 (Big-endian)
    header += struct.pack('<B', 0x00)                    # 네트워크 번호
    header += struct.pack('<B', 0xFF)                    # PC 번호
    header += struct.pack('<H', 0x03FF)                  # 요청 선 IO
    header += struct.pack('<B', 0x00)                    # 요청 선 국번호
    header += struct.pack('<H', len(data) + 2)           # 데이터 길이
    header += struct.pack('<H', 0x0010)                  # CPU 감시 타이머

    return header + data


def parse_bit_response(response: bytes, start_addr: int, count: int) -> dict:
    """비트 읽기 응답 파싱 (nibble packing)"""
    result = {}
    for i in range(count):
        byte_idx = i // 2
        if byte_idx < len(response):
            if i % 2 == 0:
                bit_value = response[byte_idx] & 0x0F
            else:
                bit_value = (response[byte_idx] >> 4) & 0x0F
            result[start_addr + i] = bit_value & 0x01
    return result


def parse_word_response(response: bytes, start_addr: int, count: int) -> dict:
    """워드 읽기 응답 파싱 → 비트 추출"""
    result = {}
    for i in range(count):
        if i * 2 + 1 < len(response):
            word = struct.unpack('<H', response[i*2:i*2+2])[0]
            # 이 워드에서 16개 비트 추출
            word_addr = start_addr + i
            for bit_pos in range(16):
                bit_addr = word_addr * 16 + bit_pos
                result[bit_addr] = (word >> bit_pos) & 0x01
    return result


async def mc_read(reader, writer, device_code, start_addr, count, subcommand) -> bytes | None:
    """MC Protocol 읽기 요청 → 응답"""
    request = build_3e_read_request(device_code, start_addr, count, subcommand)
    writer.write(request)
    await writer.drain()

    # 헤더 9바이트 수신
    header = await asyncio.wait_for(reader.readexactly(9), timeout=5.0)
    data_length = struct.unpack('<H', header[7:9])[0]

    # 데이터 수신
    data = await asyncio.wait_for(reader.readexactly(data_length), timeout=5.0)

    # 종료 코드 확인
    end_code = struct.unpack('<H', data[0:2])[0]
    if end_code != 0:
        print(f"  [ERROR] MC Protocol error: 0x{end_code:04X}")
        return None

    return data[2:]


async def read_m_bits_method1(reader, writer) -> dict:
    """방법 1: 비트 읽기 (subcommand=0x0001) — collector 방식"""
    start = min(WATCH_BITS)
    count = max(WATCH_BITS) - start + 1
    response = await mc_read(reader, writer, DEVICE_M, start, count, subcommand=0x0001)
    if response is None:
        return {}
    all_bits = parse_bit_response(response, start, count)
    return {addr: all_bits.get(addr, -1) for addr in WATCH_BITS}


async def read_m_bits_method2(reader, writer) -> dict:
    """방법 2: 워드 읽기 (subcommand=0x0000) — HMI 방식
    M180-M188은 워드 주소 11 (180//16=11), 12개 워드면 M176~M207 포함"""
    word_start_addr = min(WATCH_BITS) // 16  # 11
    word_end_addr = max(WATCH_BITS) // 16    # 11
    word_count = word_end_addr - word_start_addr + 1  # 1 워드

    # M디바이스 워드 읽기: 시작 주소는 비트 주소 (word_start * 16)
    bit_start = word_start_addr * 16  # 176
    response = await mc_read(reader, writer, DEVICE_M, bit_start, word_count, subcommand=0x0000)
    if response is None:
        return {}
    all_bits = parse_word_response(response, word_start_addr, word_count)
    return {addr: all_bits.get(addr, -1) for addr in WATCH_BITS}


async def main():
    print(f"=== M비트 모니터링 시작 ===")
    print(f"PLC: {PLC_HOST}:{PLC_PORT}")
    print(f"감시 비트: M{WATCH_BITS}")
    print(f"폴링 간격: {POLL_INTERVAL}s")
    print(f"CSV 저장: {CSV_PATH}")
    print()

    # CSV 초기화
    with open(CSV_PATH, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'method', 'M180', 'M184', 'M188', 'changed'])

    # PLC 연결
    try:
        reader, writer = await asyncio.open_connection(PLC_HOST, PLC_PORT)
        print(f"[OK] PLC 연결 성공")
    except Exception as e:
        print(f"[FAIL] PLC 연결 실패: {e}")
        return

    prev_m1 = {}
    prev_m2 = {}
    cycle = 0

    try:
        while True:
            cycle += 1
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

            try:
                # 방법 1: 비트 읽기 (collector 방식)
                m1 = await read_m_bits_method1(reader, writer)
                # 방법 2: 워드 읽기 (HMI 방식)
                m2 = await read_m_bits_method2(reader, writer)

                # 변경 감지
                m1_changed = m1 != prev_m1
                m2_changed = m2 != prev_m2

                # 출력 (변경 시 또는 매 20회)
                if m1_changed or m2_changed or cycle % 20 == 1:
                    tag = "***CHANGED***" if (m1_changed or m2_changed) and cycle > 1 else ""
                    print(
                        f"[{now}] "
                        f"BitRead: M180={m1.get(180, '?')} M184={m1.get(184, '?')} M188={m1.get(188, '?')} | "
                        f"WordRead: M180={m2.get(180, '?')} M184={m2.get(184, '?')} M188={m2.get(188, '?')} "
                        f"{tag}"
                    )

                # CSV 저장
                with open(CSV_PATH, 'a', newline='') as f:
                    csv_writer = csv.writer(f)
                    csv_writer.writerow([
                        now, 'bit_read',
                        m1.get(180, ''), m1.get(184, ''), m1.get(188, ''),
                        'Y' if m1_changed and cycle > 1 else ''
                    ])
                    csv_writer.writerow([
                        now, 'word_read',
                        m2.get(180, ''), m2.get(184, ''), m2.get(188, ''),
                        'Y' if m2_changed and cycle > 1 else ''
                    ])

                prev_m1 = m1
                prev_m2 = m2

            except asyncio.TimeoutError:
                print(f"[{now}] TIMEOUT - 응답 없음")
            except ConnectionResetError:
                print(f"[{now}] CONNECTION RESET - 재연결 시도...")
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                reader, writer = await asyncio.open_connection(PLC_HOST, PLC_PORT)
                print(f"[{now}] 재연결 성공")

            await asyncio.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n=== 모니터링 종료 (총 {cycle}회) ===")
    finally:
        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    asyncio.run(main())
