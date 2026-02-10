"""
MQTT Publisher
==============

MQTT 브로커로 데이터를 JSON 형태로 발행합니다.

Features:
    - 비동기 MQTT 통신 (aiomqtt)
    - JSON 직렬화 (경량화)
    - 자동 재연결
    - QoS 레벨 설정
    - 배치 발행 (여러 데이터를 하나의 메시지로)
    - 개별 발행 (태그별 별도 토픽)

Message Format (JSON):
    배치 모드:
    {
        "plc_id": 1,
        "timestamp": "2024-01-01T12:00:00.000Z",
        "data": [
            {"tag_id": 1, "value": 25.5, "source_time": "..."},
            {"tag_id": 2, "value": 100.0, "source_time": "..."}
        ]
    }

    개별 모드:
    {
        "plc_id": 1,
        "tag_id": 1,
        "value": 25.5,
        "source_time": "2024-01-01T12:00:00.000Z",
        "server_time": "2024-01-01T12:00:00.100Z"
    }

Connection Stability:
    - 연결 끊김 자동 감지
    - 지수 백오프 재연결
    - 발행 실패 시 버퍼에 데이터 보존

Example:
    publisher = MqttPublisher(
        name="mqtt_publisher",
        mqtt_config=mqtt_config,
        publisher_config=publisher_config,
    )

    await publisher.start()
"""

import asyncio
import json
import ssl
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

try:
    import aiomqtt
    AIOMQTT_AVAILABLE = True
except ImportError:
    AIOMQTT_AVAILABLE = False

try:
    import paho.mqtt.client as paho_mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False

from ...publishers.base import BasePublisher
from ...core.interfaces import ProcessedData
from ...core.config import MqttConfig, PublisherConfig
from ...utils.logging import LoggerFactory, VERBOSE

logger = LoggerFactory.get_publish_logger()


class MqttPublisher(BasePublisher):
    """
    MQTT 데이터 발행기.

    수집된 데이터를 JSON 형태로 MQTT 브로커에 발행합니다.

    Publish Modes:
        - batch: 여러 데이터를 하나의 메시지로 발행 (기본)
        - individual: 태그별로 별도 토픽에 발행

    Attributes:
        mqtt_config: MQTT 설정
        publish_mode: 발행 모드 ('batch' or 'individual')
        _client: MQTT 클라이언트
    """

    def __init__(
        self,
        name: str,
        mqtt_config: MqttConfig,
        publisher_config: PublisherConfig,
        publish_mode: str = 'batch',
    ):
        """
        Args:
            name: 발행기 이름
            mqtt_config: MQTT 설정
            publisher_config: 발행기 설정
            publish_mode: 발행 모드 ('batch' or 'individual')
        """
        super().__init__(name, publisher_config)

        self._mqtt_config = mqtt_config
        self._publish_mode = publish_mode

        # MQTT 클라이언트 (aiomqtt 우선)
        self._client: Optional[Any] = None
        self._client_lock = asyncio.Lock()

        # Paho 클라이언트 (aiomqtt 없을 때 폴백)
        self._paho_client: Optional[Any] = None
        self._paho_loop_task: Optional[asyncio.Task] = None

        # 재연결 관련
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

        # 라이브러리 확인
        if not AIOMQTT_AVAILABLE and not PAHO_AVAILABLE:
            raise ImportError(
                "MQTT library not found. Install 'aiomqtt' or 'paho-mqtt'"
            )

        self._use_aiomqtt = AIOMQTT_AVAILABLE

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        MQTT 브로커 연결.

        Returns:
            연결 성공 여부
        """
        if self._use_aiomqtt:
            return await self._connect_aiomqtt()
        else:
            return await self._connect_paho()

    async def _connect_aiomqtt(self) -> bool:
        """aiomqtt로 연결."""
        try:
            # TLS 설정
            tls_context = None
            if self._mqtt_config.use_tls:
                tls_context = ssl.create_default_context()

            # 클라이언트 생성은 컨텍스트 매니저로 사용해야 함
            # 연결 테스트만 수행
            async with aiomqtt.Client(
                hostname=self._mqtt_config.host,
                port=self._mqtt_config.port,
                username=self._mqtt_config.username or None,
                password=self._mqtt_config.password or None,
                identifier=self._mqtt_config.client_id,
                tls_context=tls_context,
            ) as client:
                # 연결 테스트 성공
                pass

            self._reconnect_delay = 1.0
            logger.info(
                f"[{self._name}] Connected to MQTT broker "
                f"{self._mqtt_config.host}:{self._mqtt_config.port}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] MQTT connection error: {e}")
            return False

    async def _connect_paho(self) -> bool:
        """paho-mqtt로 연결."""
        try:
            self._paho_client = paho_mqtt.Client(
                client_id=self._mqtt_config.client_id,
                protocol=paho_mqtt.MQTTv311,
            )

            # 인증 설정
            if self._mqtt_config.username:
                self._paho_client.username_pw_set(
                    self._mqtt_config.username,
                    self._mqtt_config.password,
                )

            # TLS 설정
            if self._mqtt_config.use_tls:
                self._paho_client.tls_set()

            # 연결
            self._paho_client.connect(
                self._mqtt_config.host,
                self._mqtt_config.port,
                keepalive=60,
            )

            # 루프 시작
            self._paho_client.loop_start()

            self._reconnect_delay = 1.0
            logger.info(
                f"[{self._name}] Connected to MQTT broker "
                f"{self._mqtt_config.host}:{self._mqtt_config.port}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] MQTT connection error: {e}")
            return False

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        if self._paho_client:
            try:
                self._paho_client.loop_stop()
                self._paho_client.disconnect()
            except Exception as e:
                logger.warning(f"[{self._name}] Disconnect error: {e}")
            finally:
                self._paho_client = None

    async def _do_health_check(self) -> bool:
        """연결 상태 확인."""
        if self._use_aiomqtt:
            # aiomqtt는 매번 새 연결을 사용하므로 항상 True
            return True
        else:
            return self._paho_client is not None and self._paho_client.is_connected()

    # =========================================================================
    # Data Publishing
    # =========================================================================

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 발행.

        Args:
            data: 발행할 데이터 리스트

        Returns:
            발행 성공 여부
        """
        if not data:
            return True

        try:
            if self._publish_mode == 'batch':
                return await self._publish_batch(data)
            else:
                return await self._publish_individual(data)
        except Exception as e:
            logger.error(f"[{self._name}] Publish error: {e}")
            return False

    async def _publish_batch(self, data: List[ProcessedData]) -> bool:
        """
        배치 발행 (여러 데이터를 하나의 메시지로).

        Args:
            data: 발행할 데이터 리스트

        Returns:
            발행 성공 여부
        """
        # JSON 메시지 생성
        message = self._create_batch_message(data)
        payload = json.dumps(message, ensure_ascii=False, separators=(',', ':'))

        return await self._publish_message(
            topic=self._mqtt_config.topic,
            payload=payload,
        )

    async def _publish_individual(self, data: List[ProcessedData]) -> bool:
        """
        개별 발행 (태그별 별도 토픽).

        Args:
            data: 발행할 데이터 리스트

        Returns:
            발행 성공 여부
        """
        success_count = 0

        for item in data:
            message = self._create_individual_message(item)
            payload = json.dumps(message, ensure_ascii=False, separators=(',', ':'))

            # 토픽: base_topic/plc_id/tag_id
            topic = f"{self._mqtt_config.topic}/{item.plc_id}/{item.tag_id}"

            if await self._publish_message(topic, payload):
                success_count += 1

        return success_count == len(data)

    async def _publish_message(self, topic: str, payload: str) -> bool:
        """
        단일 메시지 발행.

        Args:
            topic: 토픽
            payload: 페이로드

        Returns:
            발행 성공 여부
        """
        if self._use_aiomqtt:
            return await self._publish_aiomqtt(topic, payload)
        else:
            return await self._publish_paho(topic, payload)

    async def _publish_aiomqtt(self, topic: str, payload: str) -> bool:
        """aiomqtt로 발행."""
        try:
            tls_context = None
            if self._mqtt_config.use_tls:
                tls_context = ssl.create_default_context()

            async with aiomqtt.Client(
                hostname=self._mqtt_config.host,
                port=self._mqtt_config.port,
                username=self._mqtt_config.username or None,
                password=self._mqtt_config.password or None,
                identifier=self._mqtt_config.client_id,
                tls_context=tls_context,
            ) as client:
                await client.publish(
                    topic=topic,
                    payload=payload.encode('utf-8'),
                    qos=self._mqtt_config.qos,
                )

            logger.verbose(f"[{self._name}] Published to {topic}: {len(payload)} bytes")
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Publish error: {e}")
            return False

    async def _publish_paho(self, topic: str, payload: str) -> bool:
        """paho-mqtt로 발행."""
        if not self._paho_client:
            return False

        try:
            result = self._paho_client.publish(
                topic=topic,
                payload=payload.encode('utf-8'),
                qos=self._mqtt_config.qos,
            )

            # QoS > 0이면 확인 대기
            if self._mqtt_config.qos > 0:
                result.wait_for_publish(timeout=5.0)

            logger.verbose(f"[{self._name}] Published to {topic}: {len(payload)} bytes")
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Publish error: {e}")
            return False

    # =========================================================================
    # Message Creation
    # =========================================================================

    def _create_batch_message(self, data: List[ProcessedData]) -> Dict[str, Any]:
        """
        배치 메시지 생성.

        JSON 형식으로 모든 타입별 값을 포함합니다.

        Args:
            data: 데이터 리스트

        Returns:
            JSON 직렬화 가능한 딕셔너리
        """
        # PLC ID는 첫 번째 데이터에서 추출
        plc_id = data[0].plc_id if data else 0

        return {
            "plc_id": plc_id,
            "timestamp": datetime.now().isoformat(timespec='milliseconds'),
            "count": len(data),
            "data": [item.to_dict() for item in data]
        }

    def _create_individual_message(self, item: ProcessedData) -> Dict[str, Any]:
        """
        개별 메시지 생성.

        Args:
            item: 단일 데이터

        Returns:
            JSON 직렬화 가능한 딕셔너리
        """
        return item.to_dict()

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """통계 조회."""
        stats = super().get_stats()
        stats.update({
            "mqtt_host": self._mqtt_config.host,
            "mqtt_port": self._mqtt_config.port,
            "mqtt_topic": self._mqtt_config.topic,
            "publish_mode": self._publish_mode,
            "use_aiomqtt": self._use_aiomqtt,
        })
        return stats
