"""
OPC UA Data Processor
=====================

OPC UA 데이터를 처리하고 스케일링을 적용합니다.

Features:
    - OPC UA 표준 데이터 타입 지원
    - 품질 코드 (Quality Code) 처리
    - 타입 변환 및 스케일링
    - 배열 데이터 처리

OPC UA Quality Codes:
    - Good (192): 정상
    - Uncertain (64): 불확실
    - Bad (0): 오류

Example:
    processor = OpcuaProcessor("opcua_processor")
    processor.register_tags_for_group("1sec", tags)
    processed = await processor.process(collected_data)
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, Union
import logging

from ...processors.base import BaseProcessor
from ...core.interfaces import CollectedData, ProcessedData, TagDefinition, DataType
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class OpcuaProcessor(BaseProcessor):
    """
    OPC UA 데이터 처리기.

    OPC UA 서버에서 수집된 데이터를 처리합니다.
    OPC UA는 이미 타입이 지정된 값을 반환하므로,
    주요 처리는 스케일링과 품질 코드 처리입니다.

    Attributes:
        _name: 처리기 이름
    """

    def __init__(self, name: str):
        """
        Args:
            name: 처리기 이름
        """
        super().__init__(name)

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        OPC UA 데이터 파싱.

        OPC UA 값은 이미 타입이 지정되어 있으므로
        메타데이터에서 직접 추출합니다.

        Args:
            data: 수집된 데이터 (metadata에 values 딕셔너리 포함)
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []

        # 값 데이터 추출: {tag_id: (value, quality_code)}
        values = data.metadata.get('values', {})

        for tag in tags:
            try:
                value_data = values.get(tag.tag_id)

                if value_data is None:
                    logger.warning(
                        f"[{self._name}] No value for tag {tag.tag_id} "
                        f"({tag.tag_name})"
                    )
                    continue

                # (value, quality_code) 튜플 처리
                if isinstance(value_data, tuple) and len(value_data) == 2:
                    raw_value, quality_code = value_data
                else:
                    # 단순 값인 경우
                    raw_value = value_data
                    quality_code = 192  # Good

                # 품질 코드 확인
                if quality_code == 0:  # Bad
                    logger.warning(
                        f"[{self._name}] Bad quality for tag {tag.tag_id}: "
                        f"quality_code={quality_code}"
                    )
                    # Bad 품질인 경우에도 값을 전달 (None으로)
                    results.append((tag, None))
                    continue

                # 타입 변환 (필요시)
                converted_value = self._convert_type(raw_value, tag.data_type)

                if converted_value is not None:
                    results.append((tag, converted_value))
                else:
                    logger.warning(
                        f"[{self._name}] Failed to convert value for tag "
                        f"{tag.tag_id} ({tag.tag_name}): {raw_value}"
                    )

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error processing tag {tag.tag_id}: {e}"
                )

        return results

    def _convert_type(
        self,
        value: Any,
        target_type: DataType
    ) -> Optional[Any]:
        """
        값을 목표 데이터 타입으로 변환.

        OPC UA는 대부분 올바른 타입을 반환하지만,
        일부 경우 변환이 필요할 수 있습니다.

        Args:
            value: 원본 값
            target_type: 목표 데이터 타입

        Returns:
            변환된 값, 실패 시 None
        """
        if value is None:
            return None

        try:
            if target_type == DataType.BOOL:
                return bool(value)

            elif target_type in (DataType.INT16, DataType.INT32):
                return int(value)

            elif target_type in (DataType.UINT16, DataType.UINT32):
                val = int(value)
                return val if val >= 0 else None

            elif target_type in (DataType.FLOAT32, DataType.FLOAT64):
                return float(value)

            elif target_type == DataType.STRING:
                return str(value)

            else:
                # 알 수 없는 타입은 그대로 반환
                return value

        except (ValueError, TypeError) as e:
            logger.debug(f"Type conversion failed: {value} -> {target_type}: {e}")
            return None

    async def process(self, data: CollectedData) -> List[ProcessedData]:
        """
        수집된 데이터 처리.

        품질 코드를 포함하여 처리합니다.

        Args:
            data: 수집된 원시 데이터

        Returns:
            처리된 데이터 리스트
        """
        group = data.collection_group
        tags = self._tags.get(group, [])

        if not tags:
            logger.warning(f"[{self._name}] No tags for group '{group}'")
            return []

        # 실패 데이터 처리
        if data.metadata.get('failed'):
            return self._create_failed_data(data, tags)

        # 값 데이터
        values = data.metadata.get('values', {})

        results: List[ProcessedData] = []
        collection_time = datetime.now()

        for tag in tags:
            try:
                value_data = values.get(tag.tag_id)

                if value_data is None:
                    # 값이 없으면 실패 처리
                    processed = self._create_processed_data(
                        tag=tag,
                        raw_value=None,
                        source_time=data.source_time,
                        collection_time=collection_time,
                        plc_id=data.plc_id,
                        quality_code=0,
                    )
                    results.append(processed)
                    continue

                # (value, quality_code) 튜플 처리
                if isinstance(value_data, tuple) and len(value_data) == 2:
                    raw_value, quality_code = value_data
                else:
                    raw_value = value_data
                    quality_code = 192  # Good

                # 타입 변환
                converted_value = self._convert_type(raw_value, tag.data_type)

                # 스케일링 적용
                if converted_value is not None:
                    scaled_value = tag.apply_scaling(converted_value)
                else:
                    scaled_value = None
                    quality_code = 0

                # ProcessedData 생성
                processed = self._create_processed_data(
                    tag=tag,
                    raw_value=scaled_value,
                    source_time=data.source_time,
                    collection_time=collection_time,
                    plc_id=data.plc_id,
                    quality_code=quality_code,
                )
                results.append(processed)

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error creating processed data for "
                    f"tag {tag.tag_id}: {e}"
                )
                # 오류 시 quality_code=0으로 생성
                processed = self._create_processed_data(
                    tag=tag,
                    raw_value=None,
                    source_time=data.source_time,
                    collection_time=collection_time,
                    plc_id=data.plc_id,
                    quality_code=0,
                )
                results.append(processed)

        return results

    def _create_failed_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[ProcessedData]:
        """
        실패 데이터 생성 (모든 태그 quality_code=0).

        Args:
            data: 수집된 데이터 (failed=True)
            tags: 태그 리스트

        Returns:
            실패 ProcessedData 리스트
        """
        collection_time = datetime.now()
        results = []

        for tag in tags:
            processed = self._create_processed_data(
                tag=tag,
                raw_value=None,
                source_time=data.source_time,
                collection_time=collection_time,
                plc_id=data.plc_id,
                quality_code=0,
            )
            results.append(processed)

        return results


class OpcuaArrayProcessor(OpcuaProcessor):
    """
    OPC UA 배열 데이터 처리기.

    배열 타입의 OPC UA 변수를 처리합니다.

    Example:
        # 온도 센서 배열 (10개)
        tag = TagDefinition(
            tag_id=1,
            tag_name="Temperature_Array",
            address="ns=2;s=TemperatureArray",
            data_type=DataType.FLOAT32,
        )

        # 배열의 각 요소가 개별 ProcessedData로 생성됨
    """

    def __init__(
        self,
        name: str,
        expand_arrays: bool = True,
        max_array_length: int = 100,
    ):
        """
        Args:
            name: 처리기 이름
            expand_arrays: 배열을 개별 요소로 확장할지 여부
            max_array_length: 처리할 최대 배열 길이
        """
        super().__init__(name)
        self._expand_arrays = expand_arrays
        self._max_array_length = max_array_length

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        배열 데이터 포함 파싱.

        Args:
            data: 수집된 데이터
            tags: 태그 리스트

        Returns:
            (태그, 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []
        values = data.metadata.get('values', {})

        for tag in tags:
            try:
                value_data = values.get(tag.tag_id)
                if value_data is None:
                    continue

                # (value, quality_code) 튜플 처리
                if isinstance(value_data, tuple) and len(value_data) == 2:
                    raw_value, quality_code = value_data
                else:
                    raw_value = value_data
                    quality_code = 192

                # 배열 데이터 처리
                if isinstance(raw_value, (list, tuple)) and self._expand_arrays:
                    # 배열 길이 제한
                    array_data = raw_value[:self._max_array_length]

                    for idx, element in enumerate(array_data):
                        # 배열 요소용 가상 태그 생성
                        array_tag = TagDefinition(
                            tag_id=tag.tag_id * 1000 + idx,  # 고유 ID 생성
                            tag_name=f"{tag.tag_name}[{idx}]",
                            address=f"{tag.address}[{idx}]",
                            data_type=tag.data_type,
                            scale=tag.scale,
                            offset=tag.offset,
                            unit=tag.unit,
                            description=f"{tag.description} - Element {idx}",
                        )
                        results.append((array_tag, element))
                else:
                    # 스칼라 값 또는 배열 확장 안함
                    results.append((tag, raw_value))

            except Exception as e:
                logger.error(
                    f"[{self._name}] Error parsing array tag {tag.tag_id}: {e}"
                )

        return results
