"""
MQTT Collector
==============

MQTT 브로커 구독 수집기. BaseCollector를 상속.

Connection Model (BLE 스캐너 패턴):
    connect()    = MqttSubscriber 생성 + 브로커 연결 + 토픽 구독 + 메시지 루프 시작
    disconnect() = 구독 종료 + 캐시 무효화
    collect()    = 구독 캐시에서 TTL 내 신선한 토픽 payload를 모아 CollectedData 1건 생성
                   (파싱은 MqttProcessor가 tag.address[="토픽#json_path"]로 매핑)

YAML Config (protocol):
    type: "mqtt"
    host: "192.168.0.50"        # 브로커 (extra.host로도 가능)
    port: 1883
    extra:
        username: "${MQTT_USER:}"     # 시크릿은 env
        password: "${MQTT_PASS:}"
        client_id: "neuroforge-col-20"
        keepalive: 60
        qos: 1
        tls: false
        cache_ttl_s: 30               # 이 시간 넘게 갱신 없는 토픽은 stale 처리
        subscribe_topics: ["factory/line1/#"]   # 또는 base_topic
        # base_topic: "factory/line1/#"          # subscribe_topics 미지정 시 사용

Tags CSV:
    address = "토픽" 또는 "토픽#json_path"  (memory 컬럼은 비움)
        예) factory/line1/data#temperature  → JSON payload의 temperature 필드
            factory/line1/status            → payload 전체(스칼라)
"""

import logging
from datetime import datetime
from typing import Optional

from ..base import BaseCollector
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...core.interfaces import CollectedData
from .subscriber import MqttSubscriber

logger = logging.getLogger('collector.collection')


class MqttCollector(BaseCollector):
    """MQTT 브로커 구독 수집기 (1 collector = 1 브로커 = 1 device)."""

    def __init__(
        self,
        plc_id: int,
        name: str,
        config: CollectorConfig,
        event_bus: Optional[EventBus] = None,
    ):
        super().__init__(plc_id, name, config, event_bus)

        proto = self._protocol_config
        extra = proto.extra if proto else {}

        self._host: str = str((proto.host if proto else '') or extra.get('host', 'localhost'))
        self._port: int = int((proto.port if proto else 0) or extra.get('port', 1883))
        self._username: Optional[str] = str(extra.get('username') or '') or None
        self._password: Optional[str] = str(extra.get('password') or '') or None
        self._client_id: str = str(extra.get('client_id') or f"neuroforge-col-{plc_id}")
        self._keepalive: int = int(extra.get('keepalive', 60))
        self._qos: int = int(extra.get('qos', 1))
        self._tls: bool = bool(extra.get('tls', False))
        self._cache_ttl: float = float(extra.get('cache_ttl_s', 30.0))

        # 구독 토픽: 명시 리스트 우선, 없으면 base_topic 와일드카드
        topics = extra.get('subscribe_topics')
        if isinstance(topics, str):
            topics = [topics]
        if not topics:
            topics = [str(extra.get('base_topic') or '#')]
        self._topics = topics

        self._subscriber: Optional[MqttSubscriber] = None

    async def _do_connect(self) -> bool:
        """브로커 연결 + 구독 시작."""
        self._subscriber = MqttSubscriber(
            host=self._host, port=self._port,
            username=self._username, password=self._password,
            client_id=self._client_id, keepalive=self._keepalive,
            qos=self._qos, tls=self._tls, topics=self._topics,
        )
        ok = await self._subscriber.start()
        if ok:
            logger.info(
                f"[{self._name}] MQTT connected {self._host}:{self._port} "
                f"topics={self._topics}"
            )
        else:
            self._subscriber = None
        return ok

    async def _do_disconnect(self) -> None:
        """구독 종료 + 캐시 무효화."""
        if self._subscriber:
            await self._subscriber.stop()
            self._subscriber = None
            logger.info(f"[{self._name}] MQTT disconnected")

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """구독 캐시에서 신선한 토픽 payload를 모아 CollectedData 생성."""
        if not self._subscriber:
            return None

        fresh = self._subscriber.get_fresh(self._cache_ttl)
        if not fresh:
            return None

        newest = max(ts for _, ts in fresh.values())
        return CollectedData(
            source_time=newest,
            collection_time=datetime.now(),
            plc_id=self._plc_id,
            raw_data=b'',
            collection_group=group,
            metadata={
                'topic_payloads': {topic: payload for topic, (payload, _) in fresh.items()},
            },
        )

    async def _do_health_check(self) -> bool:
        """브로커 연결 상태."""
        return self._subscriber is not None and self._subscriber.is_connected
