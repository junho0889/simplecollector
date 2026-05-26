"""
DbLogHandler — neuroforge_logs.<component>_log 에 배치 INSERT.

비동기 배치 (asyncpg) + 백그라운드 스레드 + 비차단 emit + 큐 가득시 drop.
DB 실패해도 앱은 멈추지 않음. stdout 핸들러는 별도로 부착(setup_logging).

표준 구현 — 핸드오프 스펙: collector-publisher/docs/handoff/neuroforge_logs_spec.md
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional


def build_logs_dsn() -> Optional[str]:
    """LOGS_DB_* → CONFIG_DB_* → DB_* 우선순위로 DSN 조립. host 없으면 None."""
    def env(*names: str, default: str = "") -> str:
        for n in names:
            v = os.environ.get(n)
            if v is not None and v != "":
                return v
        return default

    host = env("LOGS_DB_HOST", "CONFIG_DB_HOST", "DB_HOST")
    if not host:
        return None
    port = env("LOGS_DB_PORT", "CONFIG_DB_PORT", "DB_PORT", default="5432")
    name = env("LOGS_DB_NAME", "CONFIG_DB_NAME", "DB_NAME", default="neurosense")
    user = env("LOGS_DB_USER", "CONFIG_DB_USER", "DB_USER", default="postgres")
    pw = env("LOGS_DB_PASSWORD", "CONFIG_DB_PASSWORD", "DB_PASSWORD", default="")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


class DbLogHandler(logging.Handler):
    """neuroforge_logs.<component>_log 행 배치 INSERT.

    사용:
        handler = DbLogHandler("neuroforge_logs.collector_log",
                               instance_id=os.environ.get("COLLECTOR_KEY", "unknown"))
        handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(handler)
    """

    # asyncpg 자체가 찍는 로그(연결/쿼리) 가 이 핸들러로 와서 무한 루프 방지
    _SKIP_LOGGER_PREFIXES = ("asyncpg",)

    def __init__(
        self,
        component_table: str,                 # 예: "neuroforge_logs.collector_log"
        instance_id: str,
        *,
        dsn: Optional[str] = None,
        batch_size: int = 100,
        flush_interval_ms: int = 2000,
        queue_max: int = 10_000,
        level: int = logging.INFO,
    ):
        super().__init__(level=level)
        self._table = component_table
        self._instance_id = instance_id
        self._dsn = dsn or build_logs_dsn()
        self._batch_size = max(1, int(batch_size))
        self._flush_interval = max(0.1, flush_interval_ms / 1000.0)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_max)
        self._dropped_count = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        if self._dsn is None:
            # 접속 정보 없음 → no-op handler (emit 시 silently drop)
            sys.stderr.write(
                "[DbLogHandler] DSN 환경변수 없음 — DB 로깅 비활성, stdout fallback only\n"
            )
            return

        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name=f"DbLogHandler({self._table})"
        )
        self._thread.start()

    # ----- Handler API -----
    def emit(self, record: logging.LogRecord) -> None:
        if self._dsn is None:
            return
        if any(record.name.startswith(p) for p in self._SKIP_LOGGER_PREFIXES):
            return
        try:
            ctx_obj = getattr(record, "context", None)
            ctx_json = (
                json.dumps(ctx_obj, ensure_ascii=False, default=str)
                if ctx_obj else None
            )
            exc_str = None
            if record.exc_info:
                exc_str = "".join(traceback.format_exception(*record.exc_info))
            msg = (
                self.format(record) if self.formatter
                else record.getMessage()
            )
            payload = (
                datetime.now(timezone.utc),
                self._instance_id,
                record.levelname,
                record.name,
                msg,
                exc_str,
                ctx_json,
            )
            self._queue.put_nowait(payload)
        except queue.Full:
            self._dropped_count += 1
            if self._dropped_count in (1, 100, 1000) or self._dropped_count % 10_000 == 0:
                sys.stderr.write(
                    f"[DbLogHandler] queue full — total dropped: {self._dropped_count}\n"
                )
        except Exception:
            # 핸들러 자체 에러는 stdlib 표준 경로
            self.handleError(record)

    def close(self) -> None:
        self._stop.set()
        super().close()

    # ----- Background thread -----
    def _run_loop(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._worker())
        except Exception as e:
            sys.stderr.write(f"[DbLogHandler] background loop died: {e}\n")

    async def _worker(self) -> None:
        try:
            import asyncpg
        except ImportError:
            sys.stderr.write("[DbLogHandler] asyncpg 미설치 — DB 로깅 비활성\n")
            return

        # 풀 생성 — 지수 백오프, 영구 재시도(앱 살아있는 동안 계속)
        pool = None
        attempt = 0
        while not self._stop.is_set() and pool is None:
            try:
                pool = await asyncpg.create_pool(
                    dsn=self._dsn, min_size=1, max_size=2, command_timeout=10
                )
            except Exception as e:
                attempt += 1
                backoff = min(2 ** min(attempt, 5), 30)
                if attempt in (1, 5) or attempt % 20 == 0:
                    sys.stderr.write(
                        f"[DbLogHandler] DB 연결 실패(시도 {attempt}, "
                        f"{backoff}s 후 재시도): {e}\n"
                    )
                await asyncio.sleep(backoff)

        if pool is None:
            return

        sql = (
            f"INSERT INTO {self._table} "
            f"(ts, instance_id, level, logger, message, exception, context) "
            f"VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb)"
        )

        while not self._stop.is_set():
            batch: list = []
            # 첫 항목 — 블로킹 with timeout (큐 비어있을 때 휴식)
            try:
                first = await asyncio.to_thread(
                    self._queue.get, True, self._flush_interval
                )
                batch.append(first)
            except queue.Empty:
                continue

            # 추가 — 즉시 가져갈 수 있는 만큼만 (배치 채우기)
            while len(batch) < self._batch_size:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            # INSERT — 실패하면 다음 사이클 재시도 (이 배치는 loss 가능)
            try:
                async with pool.acquire(timeout=10) as conn:
                    await conn.executemany(sql, batch)
            except Exception as e:
                sys.stderr.write(
                    f"[DbLogHandler] batch INSERT 실패({len(batch)}건 loss): {e}\n"
                )
                await asyncio.sleep(2)

        try:
            await pool.close()
        except Exception:
            pass
