"""QA 감사 High 회귀 테스트 (H-1/H-2/H-4).

실행: python3 docs/regression_qa_high.py (simpleCollector repo)
H-4는 형제 디렉토리의 collector-publisher를 서브프로세스로 검증.
외부 인프라 불필요.
"""
import asyncio, sys, logging
from pathlib import Path
_SC = Path(__file__).resolve().parents[1]
_CP = _SC.parent / 'collector-publisher'
logging.basicConfig(level=logging.CRITICAL)

R=[]
def rep(n,ok,d=""):
    R.append(ok); print(f"{'PASS' if ok else 'FAIL'}  {n}  {d}")

# ---------------- H-1/H-2 (simpleCollector)
sys.path.insert(0, str(_SC))
from datetime import datetime
from src.core.interfaces import CollectedData, TagDefinition, DataType
from src.collectors.mc_protocol.processor import McProtocolProcessor

async def t_h1():
    proc = McProtocolProcessor("t")
    tags = [
        TagDefinition(tag_id=1, tag_name="D200", address="D200",
                      data_type=DataType.UINT16, collection_group="plc_data"),
        TagDefinition(tag_id=2, tag_name="D201", address="D201",
                      data_type=DataType.FLOAT32, collection_group="plc_data"),
    ]
    proc.register_tags_for_group("plc_data", tags)
    failed = CollectedData(
        source_time=datetime.now(), collection_time=datetime.now(), plc_id=1,
        raw_data=b"", collection_group="plc_data",
        metadata={"failed": True, "reason": "connection lost", "quality_code": 0,
                  "values": {1: None, 2: None}})
    out = await proc.process(failed)
    ok = (len(out) == 2 and all(p.quality_code == 0 for p in out)
          and all(p.v_int is None and p.v_float is None for p in out))
    rep("H-1 실패데이터 -> 전 태그 quality_code=0 행 생성",
        ok, f"rows={len(out)} q={[p.quality_code for p in out]}")
    # 정상 데이터는 기존 경로 그대로
    normal = CollectedData(
        source_time=datetime.now(), collection_time=datetime.now(), plc_id=1,
        raw_data=b"", collection_group="plc_data",
        metadata={"devices": {"D": {200: 123, 201: 0, 202: 0}}})
    out2 = await proc.process(normal)
    ok2 = any(p.tag_id == 1 and p.quality_code == 1 for p in out2)
    rep("H-1 정상데이터 경로 무영향", ok2, f"rows={len(out2)}")

def t_h2():
    proc = McProtocolProcessor("t")
    # 감사 재현 케이스: M4=1 저장 상태에서 M64 조회 → 이전엔 1.0 (오염), 이제 None
    r1 = proc._extract_bool({4: 1}, 64, "M")
    rep("H-2 M64 조회 시 M4 값 오염 없음 (None)", r1 is None, f"got={r1}")
    r2 = proc._extract_bool({64: 1}, 64, "M")
    rep("H-2 비트 디바이스 직접 조회", r2 == 1.0, f"got={r2}")
    r3 = proc._extract_bool({64: 0}, 64, "M")
    rep("H-2 비트 0 값", r3 == 0.0, f"got={r3}")
    # 워드 디바이스(D)의 비트 해석은 기존 동작 유지: word 4의 bit 5
    r4 = proc._extract_bool({4: 0b100000}, 69, "D")
    rep("H-2 워드 디바이스 비트 해석 유지 (D word4 bit5)", r4 == 1.0, f"got={r4}")

# ---------------- H-4 (collector-publisher) — 별도 프로세스 불필요: src 패키지 충돌 방지
async def t_h4():
    import subprocess, textwrap
    code_t = textwrap.dedent('''
        import asyncio, sys, logging
        logging.basicConfig(level=logging.CRITICAL)
        sys.path.insert(0, r"{CP}")
        import asyncpg
        from src.config import DatabasePublisherConfig, CollectionGroupConfig
        from src.publishers.database import DatabasePublisher

        class FakeAcq:
            def __init__(self, exc): self.exc = exc
            async def __aenter__(self): raise self.exc
            async def __aexit__(self, *a): return False
        class FakePool:
            def __init__(self, exc): self.exc = exc
            def acquire(self, timeout=None): return FakeAcq(self.exc)

        async def main():
            cfg = DatabasePublisherConfig(
                collection_groups=[CollectionGroupConfig(name="alm", mode="latest")])
            pub = DatabasePublisher(cfg)
            recs = [{"source_time": "2026-06-10T10:00:00", "plc_id": 1, "tag_id": 1,
                     "v_bool": True, "quality": 1, "collection_group": "alm"}]
            # (1) 일반 예외: raise 전파 → upsert_latest False
            pub._pool = FakePool(RuntimeError("db error"))
            async def ok_pool(): return True
            pub._ensure_pool = ok_pool
            async def noop(g, device_type=None): pass
            pub._ensure_group_tables = noop
            r1 = await pub.upsert_latest(recs)
            print("GENERIC_FALSE" if r1 is False else f"GENERIC_BAD:{r1}")
            # (2) 데드락 3회: raise 전파 → upsert_latest False
            pub._pool = FakePool(asyncpg.exceptions.DeadlockDetectedError("deadlock"))
            r2 = await pub.upsert_latest(recs)
            print("DEADLOCK_FALSE" if r2 is False else f"DEADLOCK_BAD:{r2}")
        asyncio.run(main())
    ''')
    code = code_t.replace('{CP}', str(_CP))
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
    out = p.stdout
    rep("H-4 일반 예외 -> upsert_latest False (nack 경로)", "GENERIC_FALSE" in out, out.strip()[:80])
    rep("H-4 데드락 3회 초과 -> upsert_latest False (배치 보존)", "DEADLOCK_FALSE" in out, p.stderr.strip()[-80:] if "DEADLOCK_FALSE" not in out else "")

async def main():
    await t_h1()
    t_h2()
    await t_h4()
    print(f"\n=== {sum(R)}/{len(R)} PASS ===")
    sys.exit(0 if all(R) else 1)
asyncio.run(main())
