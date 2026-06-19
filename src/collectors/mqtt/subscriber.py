"""
MQTT Subscriber
===============

aiomqtt 클라이언트 래퍼 — 토픽 구독 + 토픽별 최신 payload 캐시.
BLE의 scanner.py 대응: push(브로커 발행)를 캐시에 받아두고,
Collector의 폴링 루프(_do_collect)가 캐시를 읽는다.

연결 수명:
    start()  → 백그라운드 _run() 태스크 생성(async with Client) + 구독 + 메시지 루프.
              connected 이벤트가 set될 때까지(타임아웃) 대기 후 성공 여부 반환.
    stop()   → 태스크 cancel → async with 종료(브로커 disconnect) + 캐시 비움.
    오류 시  → _run 종료, is_connected=False → Collector의 _do_health_check가 False →
              BaseCollector의 _reconnect_loop가 재연결 구동(프레임워크 표준).
"""

import asyncio
import logging
import ssl
from datetime import datetime
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger('collector.collection')


class MqttSubscriber:
    """단일 브로커 구독 + 토픽별 최신값 캐시."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 1883,
        username: Optional[str] = None,
        password: Optional[str] = None,
        client_id: str = "neuroforge-collector",
        keepalive: int = 60,
        qos: int = 1,
        tls: bool = False,
        topics: Optional[List[str]] = None,
    ):
        self._host = host
        self._port = port
        self._username = username or None
        self._password = password or None
        self._client_id = client_id
        self._keepalive = keepalive
        self._qos = qos
        self._tls = tls
        self._topics = topics or ["#"]

        # topic -> (payload_bytes, 수신시각)
        self._cache: Dict[str, Tuple[bytes, datetime]] = {}
        self._connected = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._closing = False

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    async def start(self, connect_timeout: float = 10.0) -> bool:
        """구독 태스크 시작 + 연결 완료 대기. 성공 시 True."""
        self._closing = False
        self._connected.clear()
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=connect_timeout)
            return True
        except asyncio.TimeoutError:
            logger.error(
                f"[mqtt] connect timeout {self._host}:{self._port} "
                f"({connect_timeout}s)"
            )
            return False

    async def _run(self) -> None:
        """async with Client → 구독 → 메시지 루프(캐시 갱신). 오류/cancel 시 종료."""
        try:
            import aiomqtt
        except ImportError:
            logger.error("[mqtt] aiomqtt 미설치. pip install aiomqtt")
            return

        tls_ctx = ssl.create_default_context() if self._tls else None
        try:
            async with aiomqtt.Client(
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                identifier=self._client_id,
                keepalive=self._keepalive,
                tls_context=tls_ctx,
            ) as client:
                for t in self._topics:
                    await client.subscribe(t, qos=self._qos)
                logger.info(
                    f"[mqtt] subscribed {self._topics} @ {self._host}:{self._port} "
                    f"(qos={self._qos})"
                )
                self._connected.set()
                async for message in client.messages:
                    if self._closing:
                        break
                    try:
                        payload = bytes(message.payload) if message.payload is not None else b''
                    except (TypeError, ValueError):
                        payload = b''
                    self._cache[str(message.topic)] = (payload, datetime.now())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 연결 단절/오류 — 프레임워크 _reconnect_loop가 재시도 (여기선 자체 재시도 안 함)
            logger.error(f"[mqtt] connection error {self._host}:{self._port}: {e}")
        finally:
            self._connected.clear()

    def get_fresh(self, ttl_s: float) -> Dict[str, Tuple[bytes, datetime]]:
        """TTL 내 신선한 토픽만 {topic: (payload, ts)} 로 반환 (ttl<=0이면 전체)."""
        now = datetime.now()
        out: Dict[str, Tuple[bytes, datetime]] = {}
        for topic, (payload, ts) in self._cache.items():
            if ttl_s <= 0 or (now - ts).total_seconds() <= ttl_s:
                out[topic] = (payload, ts)
        return out

    def clear(self) -> None:
        self._cache.clear()

    async def stop(self) -> None:
        """구독 태스크 종료 + 캐시 무효화(재연결 시 stale 방지)."""
        self._closing = True
        self._connected.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        self.clear()
