"""
MC Protocol 끊김 → reconnect 동작 검증
======================================

가설: `_close_connection`의 `wait_closed()` / `_send_request`의 `drain()`이
       타임아웃 없이 호출되어 반-열림 소켓에서 영원 대기 → connection_lock
       데드락 → 좀비.

각 시나리오:
  1. 가짜 MC 서버 띄움 (kill_signal 통해 client 강제 종료 가능)
  2. 실제 McProtocolCollector로 연결
  3. 서버 측에서 client 강제 종료 (close / RST / hang)
  4. collector.connect() 다시 호출 → 짧은 시간 안에 성공해야 정상
  5. timeout = 버그 확인
"""

import asyncio
import logging
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.collectors.mc_protocol.collector import McProtocolCollector
from src.core.config import CollectorConfig, ProtocolConfig

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("test")

HOST = "127.0.0.1"
PORT = 25007


class FakeMcServer:
    """장애 주입 가능한 가짜 MC 3E 서버."""

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self.server = None
        self.fault_mode = "none"  # none | close | rst | hang
        self.kill_event = asyncio.Event()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            while not reader.at_eof():
                if self.kill_event.is_set():
                    if self.fault_mode == "rst":
                        sock = writer.get_extra_info("socket")
                        if sock:
                            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                            struct.pack("ii", 1, 0))
                    writer.close()
                    try:
                        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)
                    except Exception:
                        pass
                    return

                if self.fault_mode == "hang":
                    await asyncio.wait_for(self.kill_event.wait(), timeout=3600)
                    continue

                try:
                    data = await asyncio.wait_for(reader.read(2048), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    break

                # dummy 정상 응답 (D 영역 64워드)
                payload = b"\x00\x00" * 64
                length = len(payload) + 2
                resp = (b"\xd7\x00\x00\xff\xff\x03\x00"
                        + length.to_bytes(2, "little")
                        + b"\x00\x00" + payload)
                writer.write(resp)
                await writer.drain()
        except Exception as e:
            logger.debug(f"handler exception: {e}")

    async def start(self):
        self.server = await asyncio.start_server(self._handle, self.host, self.port)

    async def shutdown(self):
        # Python 3.13+: 핸들러 종료 강제
        self.kill_event.set()
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), timeout=2.0)
            except Exception:
                pass

    async def kill_clients(self):
        """서버 유지하면서 현재 client 연결만 강제 종료."""
        self.kill_event.set()
        await asyncio.sleep(0.5)
        self.kill_event.clear()


def make_collector() -> McProtocolCollector:
    proto = ProtocolConfig(
        type="mc_protocol", host=HOST, port=PORT, unit_id=1,
        timeout_ms=2000, reconnect_interval_ms=1000,
        extra={
            "plc_series": "iq-r", "frame_type": "binary_3e",
            "network_no": 0, "pc_no": 0xFF,
            "unit_io": 0x03FF, "unit_station": 0,
        },
    )
    cfg = CollectorConfig(plc_id=99, name="TEST", protocol=proto,
                          collection_groups=[], tags_file="")
    return McProtocolCollector(99, "TEST", cfg)


async def reconnect_within(collector, timeout: float) -> tuple[bool, float]:
    """connect()가 timeout 안에 True 반환되는지 + 소요 시간."""
    import time
    t0 = time.monotonic()
    try:
        ok = await asyncio.wait_for(collector.connect(), timeout=timeout)
        return bool(ok), time.monotonic() - t0
    except asyncio.TimeoutError:
        return False, time.monotonic() - t0


async def scenario(name: str, fault_mode: str) -> str:
    server = FakeMcServer()
    await server.start()
    try:
        collector = make_collector()
        ok = await asyncio.wait_for(collector.connect(), timeout=5.0)
        if not ok:
            return f"FAIL: 초기 연결 실패"
        logger.info(f"[{name}] initial connected")

        # 장애 주입
        server.fault_mode = fault_mode
        await server.kill_clients()
        logger.info(f"[{name}] fault injected ({fault_mode})")

        # 재연결 시도
        recovered, elapsed = await reconnect_within(collector, timeout=5.0)
        if recovered:
            return f"PASS (recovered in {elapsed:.2f}s)"
        return f"FAIL: 재연결 timeout ({elapsed:.2f}s) — 데드락 의심"
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"
    finally:
        try:
            await server.shutdown()
        except Exception:
            pass


async def main():
    results = {}
    for name, mode in [("close", "close"), ("rst", "rst"), ("hang", "hang")]:
        print(f"\n===== {name} =====")
        try:
            results[name] = await asyncio.wait_for(scenario(name, mode), timeout=20.0)
        except asyncio.TimeoutError:
            results[name] = "TIMEOUT (시나리오 자체가 hang)"
        await asyncio.sleep(0.5)

    print("\n===== 결과 =====")
    for k, v in results.items():
        print(f"  {k:8s} {v}")


if __name__ == "__main__":
    asyncio.run(main())
