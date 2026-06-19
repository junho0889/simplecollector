"""
MQTT Processor
==============

MQTT payload를 파싱하여 태그별 raw 값으로 변환.
스케일링/타입라우팅/on_change/실패데이터는 BaseProcessor가 처리하므로,
여기서는 `_parse_raw_data(data, tags) -> [(tag, raw_value)]` 만 구현한다.

태그 매핑:
    tag.address = "토픽" 또는 "토픽#json_path"
        "factory/line1/data#temperature"  → JSON payload의 temperature
        "factory/line1/data#$.sensor.temp" → 중첩 경로 ($ 접두/점 표기 지원)
        "factory/line1/status"             → payload 전체(스칼라)

payload 해석:
    json_path 있음 → json.loads 후 경로 추출 (같은 토픽은 사이클 내 1회만 파싱)
    json_path 없음 → bytes decode → 숫자(int/float) 시도, 안되면 문자열
    최종 값은 BaseProcessor.apply_scaling이 data_type 기준으로 변환/스케일.
"""

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from ...core.interfaces import CollectedData, TagDefinition
from ...processors.base import BaseProcessor

logger = logging.getLogger('collector.collection')


class MqttProcessor(BaseProcessor):
    """MQTT payload 처리기."""

    def __init__(self, name: str):
        super().__init__(name)

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition],
    ) -> List[Tuple[TagDefinition, Any]]:
        payloads: Dict[str, bytes] = data.metadata.get('topic_payloads', {})
        if not payloads:
            return []

        # 같은 토픽 JSON 중복 파싱 방지 (사이클 내 캐시)
        json_cache: Dict[str, Any] = {}
        results: List[Tuple[TagDefinition, Any]] = []

        for tag in tags:
            topic, path = self._split_address(tag.address)
            raw = payloads.get(topic)
            if raw is None:
                continue
            value = self._extract(raw, path, topic, json_cache)
            if value is not None:
                results.append((tag, value))

        return results

    @staticmethod
    def _split_address(address: str) -> Tuple[str, str]:
        """ "토픽#json_path" → (토픽, json_path).  '#' 없으면 path="". """
        addr = (address or '').strip()
        if '#' in addr:
            topic, _, path = addr.partition('#')
            return topic.strip(), path.strip()
        return addr, ''

    def _extract(
        self,
        raw: bytes,
        path: str,
        topic: str,
        json_cache: Dict[str, Any],
    ) -> Optional[Any]:
        if path:
            doc = json_cache.get(topic)
            if doc is None:
                try:
                    doc = json.loads(raw.decode('utf-8', errors='replace'))
                    json_cache[topic] = doc
                except (ValueError, UnicodeDecodeError) as e:
                    logger.debug(f"[{self._name}] JSON parse fail topic={topic}: {e}")
                    return None
            return self._json_path(doc, path)

        # 스칼라 payload
        try:
            s = raw.decode('utf-8', errors='replace').strip()
        except Exception:
            return None
        return self._coerce(s)

    @staticmethod
    def _json_path(doc: Any, path: str) -> Optional[Any]:
        """점 표기 경로 추출. '$.a.b' / 'a.b' / 'a.0.b'(리스트 인덱스) 지원."""
        cur = doc
        for key in path.lstrip('$').lstrip('.').split('.'):
            if not key:
                continue
            if isinstance(cur, dict):
                cur = cur.get(key)
            elif isinstance(cur, list):
                try:
                    cur = cur[int(key)]
                except (ValueError, IndexError):
                    return None
            else:
                return None
            if cur is None:
                return None
        return cur

    @staticmethod
    def _coerce(s: str) -> Optional[Any]:
        """스칼라 문자열 → 숫자(int→float) 시도, 안되면 문자열 그대로.
        bool/문자열 변환은 tag.data_type 기준으로 apply_scaling이 처리."""
        if s == '':
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s
