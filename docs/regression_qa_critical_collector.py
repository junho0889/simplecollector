"""QA 감사 Critical 회귀 테스트 (collector: C-1/C-2/C-3) — 실제 TCP 목서버 기반.

실행: python3 docs/regression_qa_critical_collector.py (repo 루트 기준 어디서든 가능)
외부 인프라 불필요 (브로커/PLC/DB 없음).
"""
import asyncio, struct, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.modbus.collector import ModbusTcpTransport
from src.publishers.base import BasePublisher
from src.core.config import PublisherConfig, BufferConfig
from src.core.buffer import DataBuffer

RESULTS = []
def report(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")

class ModbusMock:
    def __init__(self, mode_seq):
        self.mode_seq = list(mode_seq)
        self.server = None; self.port = None; self.late_task = None
    async def start(self):
        self.server = await asyncio.start_server(self._handle, '127.0.0.1', 0)
        self.port = self.server.sockets[0].getsockname()[1]
    async def _handle(self, reader, writer):
        try:
            while True:
                try:
                    mbap = await reader.readexactly(7)
                except (asyncio.IncompleteReadError, ConnectionResetError):
                    return
                tid, proto, length, unit = struct.unpack('>HHHB', mbap)
                pdu = await reader.readexactly(length - 1)
                fc, addr, qty = struct.unpack('>BHH', pdu)
                mode = self.mode_seq.pop(0) if self.mode_seq else 'ok'
                data = b''.join(struct.pack('>H', addr + i) for i in range(qty))
                resp_pdu = struct.pack('>BB', fc, len(data)) + data
                def frame(t):
                    return struct.pack('>HHHB', t, 0, len(resp_pdu) + 1, unit) + resp_pdu
                if mode == 'ok':
                    writer.write(frame(tid)); await writer.drain()
                elif mode == 'wrong_tid':
                    writer.write(frame((tid + 999) % 65536)); await writer.drain()
                elif mode == 'delay':
                    async def late(w=writer, f=frame(tid)):
                        await asyncio.sleep(1.2)
                        try: w.write(f); await w.drain()
                        except Exception: pass
                    self.late_task = asyncio.create_task(late())
        except Exception:
            return
    async def stop(self):
        if self.late_task: self.late_task.cancel()
        self.server.close(); await self.server.wait_closed()

async def t_modbus_wrong_tid():
    m = ModbusMock(['wrong_tid', 'ok']); await m.start()
    tr = ModbusTcpTransport('127.0.0.1', m.port, timeout=0.5)
    assert await tr.connect()
    r1 = await tr.send_request(1, 3, 100, 2)
    closed = not await tr.is_connected()
    report("C-3a Modbus TID 불일치 -> None + 소켓 폐기", r1 is None and closed,
           f"resp={r1} closed={closed}")
    assert await tr.connect()
    r2 = await tr.send_request(1, 3, 200, 2)
    vals = struct.unpack('>HH', r2) if r2 else None
    report("C-3a 재연결 후 정상 수신", vals == (200, 201), f"vals={vals}")
    await tr.disconnect(); await m.stop()

async def t_modbus_stale_contamination():
    m = ModbusMock(['delay', 'ok']); await m.start()
    tr = ModbusTcpTransport('127.0.0.1', m.port, timeout=0.5)
    assert await tr.connect()
    r1 = await tr.send_request(1, 3, 100, 2)
    closed = not await tr.is_connected()
    report("C-3b Modbus 타임아웃 -> None + 소켓 폐기", r1 is None and closed,
           f"resp={r1} closed={closed}")
    await asyncio.sleep(1.5)
    assert await tr.connect()
    r2 = await tr.send_request(1, 3, 500, 2)
    vals = struct.unpack('>HH', r2) if r2 else None
    report("C-3b stale 응답 오염 없음 (500,501 수신)", vals == (500, 501), f"vals={vals}")
    await tr.disconnect(); await m.stop()

async def t_mc_timeout_closes_socket():
    from src.collectors.mc_protocol.collector import McProtocolCollector
    from src.core.interfaces import ConnectionState
    from src.core.config import CollectorConfig
    import inspect
    disconnected = asyncio.Event()
    async def handle(reader, writer):
        try:
            await reader.read(4096)
            await reader.read(1)
        finally:
            disconnected.set()
    server = await asyncio.start_server(handle, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    sig = inspect.signature(CollectorConfig)
    kw = {}
    for k, v in (('host','127.0.0.1'), ('port',port), ('timeout',0.5)):
        if k in sig.parameters: kw[k] = v
    cfg = CollectorConfig(**kw)
    col = McProtocolCollector(1, "test", cfg)
    col._host, col._port, col._timeout = '127.0.0.1', port, 0.5
    ok = await col._do_connect()
    assert ok, "mock 연결 실패"
    resp = await col._receive_response()
    closed = col._writer is None
    state_err = col._state == ConnectionState.ERROR
    report("C-2 MC 타임아웃 -> 소켓 폐기 + ERROR", resp is None and closed and state_err,
           f"resp={resp} writer_closed={closed} state={col._state}")
    try:
        await asyncio.wait_for(disconnected.wait(), 2)
        report("C-2 서버측 연결 종료(EOF) 감지", True, "")
    except asyncio.TimeoutError:
        report("C-2 서버측 연결 종료(EOF) 감지", False, "EOF 미감지")
    server.close(); await server.wait_closed()

class StubPublisher(BasePublisher):
    def __init__(self, name, config):
        super().__init__(name, config)
        self.connect_calls = 0
    async def _do_connect(self):
        self.connect_calls += 1
        return True
    async def _do_disconnect(self): pass
    async def _do_publish(self, data): return True

async def t_publisher_always_reconnect():
    cfg = PublisherConfig(publish_interval_ms=50, max_retries=0, retry_delay_ms=10)
    pub = StubPublisher("stub", cfg)
    buf = DataBuffer(1000)
    pub.set_buffer(buf)
    await pub.start()
    task_alive = pub._reconnect_task is not None and not pub._reconnect_task.done()
    report("C-1 최초 연결 성공 후에도 재연결 태스크 상시 기동", task_alive, "")
    pub._is_connected = False
    t0 = time.monotonic()
    while not pub._is_connected and time.monotonic() - t0 < 5:
        await asyncio.sleep(0.1)
    report("C-1 운영 중 단절 -> 자동 복구", pub._is_connected,
           f"connect_calls={pub.connect_calls} elapsed={time.monotonic()-t0:.1f}s")
    await pub.stop()

async def main():
    await t_modbus_wrong_tid()
    await t_modbus_stale_contamination()
    await t_mc_timeout_closes_socket()
    await t_publisher_always_reconnect()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n=== {len(RESULTS)-len(fails)}/{len(RESULTS)} PASS ===")
    sys.exit(1 if fails else 0)

asyncio.run(main())
