"""
기본 처리기 모듈
================

모든 데이터 처리기의 기본 클래스를 제공합니다.
Raw 데이터를 파싱하고 스케일링을 적용하여 버퍼에 저장합니다.

Architecture:
    BaseProcessor는 이벤트 기반으로 동작합니다:

    1. DATA_COLLECTED 이벤트 수신
    2. Raw 데이터 파싱 (프로토콜별)
    3. 스케일링 적용
    4. 버퍼에 데이터 저장
    5. DATA_PROCESSED 이벤트 발생

    ┌──────────────┐   EVENT    ┌──────────────┐
    │  Collector   │ ─────────▶ │  Processor   │
    └──────────────┘            │              │
                                │  parse()     │
                                │  scale()     │
                                │     │        │
                                │     ▼        │
                                │  ┌────────┐  │
                                │  │ Buffer │  │
                                │  └────────┘  │
                                └──────────────┘

Extension:
    프로토콜별 구현체는 BaseProcessor를 상속받아 _parse_raw_data를 구현합니다.

    class ModbusProcessor(BaseProcessor):
        async def _parse_raw_data(
            self,
            data: CollectedData,
            tags: List[TagDefinition]
        ) -> List[Tuple[TagDefinition, Any]]:
            # Modbus 레지스터 파싱 로직
            pass

Example:
    processor = MyProcessor("processor1")
    processor.register_tags(tags)
    processor.set_buffer(buffer)
    processor.set_event_bus(event_bus)

    await processor.start()
"""

import asyncio
from abc import abstractmethod
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging

from ..core.interfaces import (
    IProcessor,
    CollectedData,
    ProcessedData,
    TagDefinition,
    DataType,
)
from ..core.buffer import DataBuffer
from ..core.events import EventBus, Event, EventType
from ..utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


# =============================================================================
# 출력 타입 매핑 (룩업 테이블로 if-elif 체인 최적화)
# =============================================================================

# format 문자열 → 출력 타입 매핑
_FORMAT_TO_OUTPUT_TYPE: Dict[str, str] = {
    'float32': 'float', 'float64': 'float', 'real': 'float',
    'lreal': 'float', 'float': 'float',
    'int32': 'int', 'int16': 'int', 'int': 'int',
    'word': 'int', 'dword': 'int',
    'int64': 'bigint', 'bigint': 'bigint', 'lword': 'bigint',
    'string': 'text', 'text': 'text',
    'bool': 'bool', 'boolean': 'bool',
}

# DataType enum → 출력 타입 매핑
# 주의: UINT32는 PostgreSQL INTEGER 범위(~21억)를 초과할 수 있으므로 bigint 사용
_DATATYPE_TO_OUTPUT_TYPE: Dict[DataType, str] = {
    DataType.BOOL: 'bool',
    DataType.INT8: 'byte', DataType.UINT8: 'byte', DataType.BYTE: 'byte',
    DataType.INT16: 'int', DataType.UINT16: 'int', DataType.WORD: 'int',
    DataType.INT32: 'int', DataType.DWORD: 'int',
    DataType.UINT32: 'bigint',  # UINT32 범위(0~4294967295)가 int32 범위 초과
    DataType.INT64: 'bigint', DataType.UINT64: 'bigint', DataType.LWORD: 'bigint',
    DataType.FLOAT32: 'float', DataType.FLOAT64: 'float',
    DataType.STRING: 'text',
}


class BaseProcessor(IProcessor):
    """
    기본 데이터 처리기 클래스.

    Collector로부터 Raw 데이터를 받아 파싱하고 버퍼에 저장합니다.

    Features:
        - 이벤트 기반 동작 (DATA_COLLECTED 구독)
        - 스케일링 자동 적용
        - 버퍼 자동 저장
        - 처리 통계
        - on_change 모드: 값 변경 시에만 전달 (알람/이벤트용)

    Attributes:
        name: 처리기 이름
        buffer: 데이터 버퍼
        event_bus: 이벤트 버스

    Abstract Methods (구현 필요):
        _parse_raw_data: 프로토콜별 파싱 로직
    """

    def __init__(self, name: str):
        """
        Args:
            name: 처리기 이름 (로깅용)
        """
        super().__init__(name)

        self._buffer: Optional[DataBuffer] = None
        self._event_bus: Optional[EventBus] = None
        self._event_unsubscribe: Optional[Callable[[], None]] = None

        # 그룹별 태그 관리
        self._tags_by_group: Dict[str, List[TagDefinition]] = {}

        # 최적화: 태그별 출력 타입 캐시 (tag_id → output_type)
        self._output_type_cache: Dict[int, str] = {}

        # on_change 모드용 값 캐시 (tag_id → 이전 값)
        self._value_cache: Dict[int, Any] = {}

        # 통계
        self._total_processed = 0
        self._total_errors = 0
        self._total_changes = 0  # on_change 모드에서 감지된 변경 수
        self._last_process_time: Optional[datetime] = None

    def set_buffer(self, buffer: DataBuffer) -> None:
        """
        데이터 버퍼 설정.

        Args:
            buffer: 처리된 데이터를 저장할 버퍼
        """
        self._buffer = buffer

    def set_event_bus(self, event_bus: EventBus) -> None:
        """
        이벤트 버스 설정.

        Args:
            event_bus: 이벤트 통신용 버스
        """
        self._event_bus = event_bus

    def register_tags_for_group(
        self,
        group: str,
        tags: List[TagDefinition]
    ) -> None:
        """
        그룹별 태그 등록.

        태그 등록 시 출력 타입을 미리 계산하여 캐시합니다 (처리 시 연산 최소화).

        Args:
            group: 수집 그룹명
            tags: 해당 그룹의 태그 리스트
        """
        self._tags_by_group[group] = tags

        # 전체 태그 맵에도 등록 + 출력 타입 캐싱
        for tag in tags:
            self._tags[tag.tag_id] = tag
            # 출력 타입 미리 계산하여 캐시
            self._output_type_cache[tag.tag_id] = self._compute_output_type(tag)

        logger.debug(
            f"[{self._name}] Registered {len(tags)} tags for group '{group}'"
        )

    # =========================================================================
    # Public Methods (IProcessor 구현)
    # =========================================================================

    async def process(self, data: CollectedData) -> List[ProcessedData]:
        """
        Raw 데이터 처리 (최적화됨).

        최적화 포인트:
        - 리스트 사전 할당 (동적 확장 최소화)
        - 캐시된 출력 타입 사용
        - 로컬 변수로 속성 접근 최소화

        Args:
            data: Collector로부터 받은 Raw 데이터

        Returns:
            처리된 데이터 리스트 (태그별 1개씩)
        """
        # 그룹에 해당하는 태그 조회
        tags = self._tags_by_group.get(data.collection_group)
        if not tags:
            logger.warning(
                f"[{self._name}] No tags found for group '{data.collection_group}'"
            )
            return []

        server_time = datetime.now()
        source_time = data.source_time
        plc_id = data.plc_id

        # 로컬 변수로 속성 접근 최적화
        output_type_cache = self._output_type_cache
        create_processed = self._create_processed_data_fast

        try:
            # 프로토콜별 파싱
            parsed_values = await self._parse_raw_data(data, tags)

            # 결과 리스트 사전 할당
            results: List[ProcessedData] = []
            results_append = results.append  # 메서드 참조 캐싱
            error_count = 0

            # 배치 처리
            for tag, raw_value in parsed_values:
                try:
                    # 스케일링 적용
                    scaled_value = tag.apply_scaling(raw_value) if raw_value is not None else None

                    # 캐시된 출력 타입 사용
                    output_type = output_type_cache.get(tag.tag_id, 'float')

                    # 최적화된 ProcessedData 생성
                    processed = create_processed(
                        source_time, server_time, plc_id,
                        tag, raw_value, scaled_value, output_type
                    )
                    results_append(processed)

                except Exception as e:
                    logger.error(f"[{self._name}] Tag {tag.tag_id} error: {e}")
                    error_count += 1

            # 통계 업데이트
            self._total_processed += len(results)
            self._total_errors += error_count
            self._last_process_time = server_time

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"[{self._name}] Processed {len(results)} values "
                    f"from group '{data.collection_group}'"
                )

            return results

        except Exception as e:
            logger.exception(f"[{self._name}] Processing error: {e}")
            self._total_errors += 1
            return []

    def _create_processed_data_fast(
        self,
        source_time: datetime,
        server_time: datetime,
        plc_id: int,
        tag: TagDefinition,
        raw_value: Any,
        scaled_value: Any,
        output_type: str,
    ) -> ProcessedData:
        """
        최적화된 ProcessedData 생성.

        출력 타입이 이미 결정되어 전달되므로 추가 연산 없음.

        Args:
            source_time: 소스 타임스탬프
            server_time: 서버 타임스탬프
            plc_id: PLC ID
            tag: 태그 정의
            raw_value: 원시 값
            scaled_value: 스케일링 적용된 값
            output_type: 미리 계산된 출력 타입

        Returns:
            ProcessedData 인스턴스
        """
        # Raw 바이트 값 계산
        v_byte = self._calculate_raw_byte(raw_value, tag.data_type)
        quality_code = 1 if raw_value is not None else 0

        # 타입별 값 초기화 (None)
        v_bool = v_int = v_bigint = v_float = v_text = None

        # 값이 있을 때만 타입별 컬럼에 저장
        if scaled_value is not None:
            if output_type == 'float':
                v_float = float(scaled_value)
            elif output_type == 'int':
                v_int = int(scaled_value)
            elif output_type == 'bigint':
                v_bigint = int(scaled_value)
            elif output_type == 'bool':
                v_bool = bool(scaled_value)
            elif output_type == 'text':
                v_text = str(scaled_value)
            elif output_type == 'byte':
                v_byte = int(scaled_value) & 0xFF
            else:
                v_float = float(scaled_value)

        return ProcessedData(
            source_time=source_time,
            server_time=server_time,
            plc_id=plc_id,
            tag_id=tag.tag_id,
            data_type=tag.data_type,
            v_bool=v_bool,
            v_byte=v_byte,
            v_int=v_int,
            v_bigint=v_bigint,
            v_float=v_float,
            v_text=v_text,
            quality_code=quality_code,
        )

    def _create_processed_data(
        self,
        source_time: datetime,
        server_time: datetime,
        plc_id: int,
        tag: TagDefinition,
        raw_value: Any,
        scaled_value: Any,
    ) -> ProcessedData:
        """
        데이터 타입에 따라 적절한 값 컬럼에 데이터 저장.

        (호환성을 위해 유지, 내부적으로 _create_processed_data_fast 사용)
        """
        output_type = self._output_type_cache.get(
            tag.tag_id,
            self._compute_output_type(tag)
        )
        return self._create_processed_data_fast(
            source_time, server_time, plc_id,
            tag, raw_value, scaled_value, output_type
        )

    def _compute_output_type(self, tag: TagDefinition) -> str:
        """
        태그의 출력 타입 계산 (등록 시 1회만 호출).

        scale != 1.0 또는 decimals가 설정되면 float 출력.
        format 필드가 있으면 해당 형식 사용.
        그 외에는 데이터 타입 기반 매핑.

        Args:
            tag: 태그 정의

        Returns:
            출력 타입 문자열 ('bool', 'byte', 'int', 'bigint', 'float', 'text')
        """
        # format 필드가 있으면 룩업 테이블에서 검색
        if tag.format:
            output_type = _FORMAT_TO_OUTPUT_TYPE.get(tag.format.lower())
            if output_type:
                return output_type

        # scale이 1.0이 아니거나 decimals가 설정되면 float 출력
        # (정수 데이터도 스케일링하면 소수점 결과가 나올 수 있음)
        if tag.scale != 1.0 or tag.decimals is not None:
            return 'float'

        # 데이터 타입 기반 매핑 (룩업 테이블)
        return _DATATYPE_TO_OUTPUT_TYPE.get(tag.data_type, 'float')

    def _get_effective_output_type(self, tag: TagDefinition) -> str:
        """
        실제 출력 타입 결정.

        (호환성을 위해 유지, 캐시 또는 계산 사용)
        """
        cached = self._output_type_cache.get(tag.tag_id)
        if cached:
            return cached
        return self._compute_output_type(tag)

    def _calculate_raw_byte(self, raw_value: Any, data_type: DataType) -> Optional[int]:
        """
        Raw 바이트 값 계산.

        원시 데이터의 첫 번째 바이트 또는 전체 바이트를 정수로 변환합니다.

        Args:
            raw_value: 원시 값
            data_type: 데이터 타입

        Returns:
            0~255 범위의 바이트 값 또는 None
        """
        if raw_value is None:
            return None

        try:
            if data_type == DataType.BOOL:
                return 1 if raw_value else 0
            elif isinstance(raw_value, bool):
                return 1 if raw_value else 0
            elif isinstance(raw_value, int):
                return raw_value & 0xFF  # 하위 바이트
            elif isinstance(raw_value, float):
                return int(raw_value) & 0xFF
            elif isinstance(raw_value, bytes):
                return raw_value[0] if raw_value else None
            else:
                return int(raw_value) & 0xFF
        except (ValueError, TypeError, IndexError):
            return None

    # =========================================================================
    # Change Detection (on_change 모드)
    # =========================================================================

    def _is_value_changed(
        self,
        tag_id: int,
        new_value: Any,
        deadband: float = 0.0,
        deadband_type: str = "absolute"
    ) -> bool:
        """
        값 변경 여부 판단 (on_change 모드용).

        Args:
            tag_id: 태그 ID
            new_value: 새로운 값
            deadband: 변화 감지 임계값
            deadband_type: "absolute" (절대값) 또는 "percent" (백분율)

        Returns:
            값이 변경되었으면 True
        """
        # 캐시에 이전 값이 없으면 첫 수집 → 변경으로 간주
        if tag_id not in self._value_cache:
            self._value_cache[tag_id] = new_value
            return True

        old_value = self._value_cache[tag_id]

        # None 처리
        if old_value is None and new_value is None:
            return False
        if old_value is None or new_value is None:
            self._value_cache[tag_id] = new_value
            return True

        # 타입별 비교
        try:
            # bool 타입
            if isinstance(new_value, bool) or isinstance(old_value, bool):
                changed = bool(old_value) != bool(new_value)
                if changed:
                    self._value_cache[tag_id] = new_value
                return changed

            # 문자열 타입
            if isinstance(new_value, str) or isinstance(old_value, str):
                changed = str(old_value) != str(new_value)
                if changed:
                    self._value_cache[tag_id] = new_value
                return changed

            # 숫자 타입 (deadband 적용)
            old_num = float(old_value)
            new_num = float(new_value)

            if deadband <= 0:
                # deadband가 0이면 모든 변화 감지
                changed = old_num != new_num
            elif deadband_type == "percent":
                # 백분율 비교
                if old_num == 0:
                    changed = new_num != 0
                else:
                    percent_change = abs((new_num - old_num) / old_num) * 100
                    changed = percent_change > deadband
            else:
                # 절대값 비교 (기본)
                changed = abs(new_num - old_num) > deadband

            if changed:
                self._value_cache[tag_id] = new_value

            return changed

        except (ValueError, TypeError):
            # 비교 불가 → 문자열 비교
            changed = str(old_value) != str(new_value)
            if changed:
                self._value_cache[tag_id] = new_value
            return changed

    def _filter_changed_data(
        self,
        processed_list: List[ProcessedData],
        deadband: float = 0.0,
        deadband_type: str = "absolute"
    ) -> List[ProcessedData]:
        """
        변경된 데이터만 필터링 (on_change 모드용).

        Args:
            processed_list: 처리된 데이터 리스트
            deadband: 변화 감지 임계값
            deadband_type: "absolute" 또는 "percent"

        Returns:
            변경된 데이터만 포함된 리스트
        """
        changed_list: List[ProcessedData] = []

        for data in processed_list:
            # 대표값 추출
            value = data.value

            if self._is_value_changed(data.tag_id, value, deadband, deadband_type):
                changed_list.append(data)

        return changed_list

    async def start(self) -> None:
        """
        처리기 시작.

        이벤트 버스에 구독하고 DATA_COLLECTED 이벤트를 수신합니다.
        """
        await super().start()

        # 이벤트 구독
        if self._event_bus:
            self._event_unsubscribe = self._event_bus.subscribe(
                EventType.DATA_COLLECTED,
                self._on_data_collected
            )

        logger.info(f"[{self._name}] Processor started")

    async def stop(self) -> None:
        """
        처리기 중지.

        이벤트 구독을 해제합니다.
        """
        # 이벤트 구독 해제
        if self._event_unsubscribe:
            self._event_unsubscribe()
            self._event_unsubscribe = None

        await super().stop()
        logger.info(f"[{self._name}] Processor stopped")

    # =========================================================================
    # Event Handler
    # =========================================================================

    async def _on_data_collected(self, event: Event) -> None:
        """
        DATA_COLLECTED 이벤트 핸들러.

        on_change 모드일 경우 변경된 데이터만 버퍼에 저장합니다.

        Args:
            event: 수집 완료 이벤트
        """
        if not isinstance(event.data, CollectedData):
            logger.warning(
                f"[{self._name}] Invalid event data type: {type(event.data)}"
            )
            return

        collected_data: CollectedData = event.data

        # 데이터 처리
        processed_list = await self.process(collected_data)

        if not processed_list:
            return

        # on_change 모드 확인 및 처리
        mode = collected_data.metadata.get("mode", "polling")

        if mode == "on_change":
            deadband = collected_data.metadata.get("deadband", 0.0)
            deadband_type = collected_data.metadata.get("deadband_type", "absolute")

            # 변경된 데이터만 필터링
            original_count = len(processed_list)
            processed_list = self._filter_changed_data(
                processed_list, deadband, deadband_type
            )
            changed_count = len(processed_list)

            # 변경 통계 업데이트
            self._total_changes += changed_count

            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    f"[{self._name}] on_change mode: {changed_count}/{original_count} "
                    f"changed in group '{collected_data.collection_group}'"
                )

            # 변경된 데이터가 없으면 종료
            if not processed_list:
                return

        # 버퍼에 저장
        if self._buffer:
            count = await self._buffer.put_many(processed_list)
            logger.debug(
                f"[{self._name}] Stored {count} records to buffer"
            )

        # 처리 완료 이벤트 발생
        if self._event_bus:
            await self._event_bus.emit(Event(
                event_type=EventType.DATA_PROCESSED,
                source=self._name,
                data={
                    "count": len(processed_list),
                    "plc_id": collected_data.plc_id,
                    "group": collected_data.collection_group,
                    "mode": mode,
                },
            ))

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """
        처리기 통계 조회.

        Returns:
            통계 딕셔너리
        """
        return {
            "name": self._name,
            "is_running": self._is_running,
            "total_processed": self._total_processed,
            "total_errors": self._total_errors,
            "total_changes": self._total_changes,  # on_change 모드 변경 감지 수
            "cached_values": len(self._value_cache),  # 캐시된 값 수
            "last_process_time": (
                self._last_process_time.isoformat()
                if self._last_process_time else None
            ),
            "registered_tags": len(self._tags),
            "registered_groups": list(self._tags_by_group.keys()),
        }

    # =========================================================================
    # Abstract Methods (구현 필요)
    # =========================================================================

    @abstractmethod
    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        Raw 데이터 파싱 (프로토콜별 구현 필요).

        Args:
            data: 수집된 Raw 데이터
            tags: 파싱할 태그 정의 리스트

        Returns:
            (태그, 파싱된 값) 튜플 리스트
        """
        pass


class GenericProcessor(BaseProcessor):
    """
    범용 데이터 처리기.

    Raw 데이터가 이미 파싱된 형태(딕셔너리)로 전달되는 경우 사용합니다.
    테스트나 간단한 프로토콜에 적합합니다.

    Expected Data Format:
        CollectedData.metadata = {
            "values": {
                tag_id: raw_value,
                ...
            }
        }
    """

    async def _parse_raw_data(
        self,
        data: CollectedData,
        tags: List[TagDefinition]
    ) -> List[Tuple[TagDefinition, Any]]:
        """
        메타데이터에서 값 추출.

        Args:
            data: 수집된 데이터 (metadata에 values 딕셔너리 포함)
            tags: 태그 정의 리스트

        Returns:
            (태그, 값) 튜플 리스트
        """
        results: List[Tuple[TagDefinition, Any]] = []

        values = data.metadata.get("values", {})

        for tag in tags:
            if tag.tag_id in values:
                results.append((tag, values[tag.tag_id]))
            elif tag.tag_name in values:
                results.append((tag, values[tag.tag_name]))

        return results
