"""
인터페이스 정의 모듈
====================

데이터 수집기의 핵심 추상 인터페이스를 정의합니다.
모든 프로토콜 모듈(Modbus, FENET, MC Protocol 등)과
퍼블리셔 모듈(DB, MQTT 등)은 이 인터페이스를 구현해야 합니다.

Architecture:
    ┌──────────────┐     Event      ┌──────────────┐     Buffer     ┌──────────────┐
    │  Collector   │ ────────────▶  │  Processor   │ ────────────▶  │  Publisher   │
    │ (Raw Conn)   │   DATA_READY   │  (Parsing)   │   BUFFER_PUT   │  (DB/MQTT)   │
    └──────────────┘                └──────────────┘                └──────────────┘

Usage:
    각 프로토콜 구현체는 ICollector를 상속받아 구현합니다.

    class ModbusCollector(ICollector):
        async def connect(self) -> bool:
            # Modbus 연결 로직
            pass

        async def collect(self) -> CollectedData:
            # 데이터 수집 로직
            pass
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union
import asyncio


class ConnectionState(Enum):
    """연결 상태를 나타내는 열거형."""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class DataType(Enum):
    """
    수집 데이터 타입.

    PLC에서 읽어올 데이터의 원본 타입을 지정합니다.
    """
    # 비트 타입
    BOOL = "bool"

    # 정수 타입 (부호 있음)
    INT8 = "int8"
    INT16 = "int16"
    INT32 = "int32"
    INT64 = "int64"

    # 정수 타입 (부호 없음)
    UINT8 = "uint8"
    UINT16 = "uint16"
    UINT32 = "uint32"
    UINT64 = "uint64"

    # 부동소수점 타입
    FLOAT32 = "float32"
    FLOAT64 = "float64"

    # 바이트/문자열 타입
    BYTE = "byte"           # Raw 바이트 (1~N 바이트)
    WORD = "word"           # 2바이트 (16비트)
    DWORD = "dword"         # 4바이트 (32비트)
    LWORD = "lword"         # 8바이트 (64비트)
    STRING = "string"

    @property
    def byte_size(self) -> int:
        """데이터 타입의 바이트 크기."""
        size_map = {
            DataType.BOOL: 1,
            DataType.INT8: 1, DataType.UINT8: 1, DataType.BYTE: 1,
            DataType.INT16: 2, DataType.UINT16: 2, DataType.WORD: 2,
            DataType.INT32: 4, DataType.UINT32: 4, DataType.DWORD: 4, DataType.FLOAT32: 4,
            DataType.INT64: 8, DataType.UINT64: 8, DataType.LWORD: 8, DataType.FLOAT64: 8,
            DataType.STRING: 1,  # 가변
        }
        return size_map.get(self, 2)

    @property
    def db_column(self) -> str:
        """해당 데이터 타입이 저장될 DB 컬럼명."""
        column_map = {
            DataType.BOOL: "v_bool",
            DataType.INT8: "v_byte", DataType.UINT8: "v_byte", DataType.BYTE: "v_byte",
            DataType.INT16: "v_int", DataType.UINT16: "v_int", DataType.WORD: "v_int",
            DataType.INT32: "v_int", DataType.UINT32: "v_int", DataType.DWORD: "v_int",
            DataType.INT64: "v_bigint", DataType.UINT64: "v_bigint", DataType.LWORD: "v_bigint",
            DataType.FLOAT32: "v_float", DataType.FLOAT64: "v_float",
            DataType.STRING: "v_text",
        }
        return column_map.get(self, "v_float")


@dataclass
class TagDefinition:
    """
    태그 정의 데이터 클래스.

    CSV 파일에서 로드되는 태그 메타데이터를 담습니다.

    Attributes:
        tag_id: 태그 고유 식별자
        tag_name: 태그 이름 (사람이 읽기 쉬운 형태)
        address: 프로토콜별 주소 (예: "D900:10", "M0")
        data_type: 데이터 타입 (읽기 타입)
        data_size: 데이터 비트 크기 (1=BIT, 16=WORD, 32=DWORD)
        raw_type: 원본 데이터 타입 문자열 (UDEC, DEC, FLOAT 등)
        scale: 스케일 팩터 (raw 값에 곱할 값)
        offset: 오프셋 (스케일 적용 후 더할 값)
        unit: 단위 (예: "°C", "kW")
        description: 태그 설명
        collection_group: 수집 그룹 (같은 주기로 수집할 태그 그룹핑)
        memory: 메모리 영역 (D, M, W, X, Y 등) - CSV 분리용
        decimals: 소수점 자릿수 (float 표시용)
        string_length: 문자열 최대 길이
        word_length: 읽을 워드 수 (string, multi-word용)
        format: 출력 변환 포맷 (예: "float32" - int를 float로 변환)
        bool_true_value: bool TRUE 임계값
        bool_false_value: bool FALSE 임계값
        bool_invert: bool 값 반전 여부
    """
    tag_id: int
    tag_name: str
    address: str
    data_type: DataType = DataType.FLOAT32
    data_size: int = 16  # 비트 크기: 1, 16, 32
    raw_type: str = ""   # UDEC, DEC, FLOAT, STRING 등 원본 타입
    scale: float = 1.0
    offset: float = 0.0
    unit: str = ""
    description: str = ""
    collection_group: str = "default"
    # 확장 필드
    memory: str = ""
    decimals: Optional[int] = None
    string_length: Optional[int] = None
    word_length: Optional[int] = None
    format: str = ""
    bool_true_value: Optional[int] = None
    bool_false_value: Optional[int] = None
    bool_invert: bool = False

    @property
    def output_type(self) -> DataType:
        """
        최종 출력 데이터 타입 결정.

        scale != 1.0 또는 format이 float 계열이면 FLOAT32 반환.
        decimals가 설정되어 있으면 FLOAT32 반환.

        Returns:
            출력 데이터 타입
        """
        # STRING은 그대로
        if self.data_type == DataType.STRING:
            return DataType.STRING

        # BOOL은 그대로
        if self.data_type == DataType.BOOL:
            return DataType.BOOL

        # format이 float 계열이면 FLOAT
        if self.format and self.format.lower() in ('float32', 'float64', 'float', 'real'):
            return DataType.FLOAT32 if 'float32' in self.format.lower() else DataType.FLOAT64

        # scale이 1.0이 아니거나 decimals가 설정되면 FLOAT
        if self.scale != 1.0 or self.decimals is not None:
            return DataType.FLOAT32

        # 원본 타입 유지
        return self.data_type

    @property
    def is_signed(self) -> bool:
        """부호 있는 정수인지 여부."""
        return self.raw_type.upper() in ('DEC', 'INT', 'INT16', 'INT32', 'INT64')

    def apply_scaling(self, raw_value: Any) -> Any:
        """
        Raw 값에 스케일링 적용.

        Args:
            raw_value: 원시 수집 값

        Returns:
            스케일링이 적용된 값: (raw_value * scale) + offset
            STRING 타입은 스케일링 없이 그대로 반환
            BOOL 타입은 커스텀 임계값과 반전 적용
            decimals가 설정된 경우 소수점 반올림 적용
        """
        if raw_value is None:
            return None

        # STRING 타입은 스케일링 없이 반환
        if self.data_type == DataType.STRING:
            return raw_value

        # BOOL 타입은 커스텀 변환 적용
        if self.data_type == DataType.BOOL:
            bool_val = self._convert_to_bool(raw_value)
            return not bool_val if self.bool_invert else bool_val

        # 문자열이 들어온 경우 그대로 반환
        if isinstance(raw_value, str):
            return raw_value

        # 스케일링: (raw * scale) + offset
        scaled = (raw_value * self.scale) + self.offset

        # decimals 적용: 소수점 반올림
        if self.decimals is not None:
            scaled = round(scaled, self.decimals)

        return scaled

    def _convert_to_bool(self, raw_value: Any) -> bool:
        """
        Raw 값을 bool로 변환.

        커스텀 임계값이 설정된 경우 해당 값 사용.

        Args:
            raw_value: 원시 값

        Returns:
            변환된 bool 값
        """
        if isinstance(raw_value, bool):
            return raw_value

        try:
            numeric_val = int(raw_value) if raw_value is not None else 0
        except (ValueError, TypeError):
            return False

        # 커스텀 임계값 사용
        if self.bool_true_value is not None:
            return numeric_val >= self.bool_true_value
        if self.bool_false_value is not None:
            return numeric_val > self.bool_false_value

        return bool(numeric_val)

    @property
    def effective_byte_size(self) -> int:
        """
        실제 바이트 크기 계산.

        word_length나 string_length가 설정된 경우 해당 값 사용.

        Returns:
            바이트 크기
        """
        if self.word_length and self.data_type in (DataType.STRING, DataType.WORD):
            return self.word_length * 2

        if self.data_type == DataType.STRING and self.string_length:
            return self.string_length

        return self.data_type.byte_size


@dataclass
class CollectedData:
    """
    수집된 Raw 데이터 클래스.

    Collector에서 수집 완료 후 Processor로 전달되는 데이터 구조입니다.

    Attributes:
        source_time: 데이터 소스(PLC)에서의 타임스탬프
        collection_time: 수집기에서 데이터를 받은 시간
        plc_id: PLC 식별자
        raw_data: 프로토콜별 원시 데이터 (바이트 또는 값 리스트)
        collection_group: 수집 그룹명
        metadata: 추가 메타데이터 (프로토콜별 정보)
    """
    source_time: datetime
    collection_time: datetime
    plc_id: int
    raw_data: bytes
    collection_group: str = "default"
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessedData:
    """
    처리된 데이터 클래스.

    Processor에서 파싱 완료 후 Publisher로 전달되는 데이터 구조입니다.
    DB 스키마와 1:1 매핑됩니다.

    Attributes:
        source_time: 데이터 소스(PLC)에서의 타임스탬프
        server_time: 서버(수집기)에서 처리된 시간
        plc_id: PLC 식별자 (1~100)
        tag_id: 태그 식별자
        data_type: 원본 데이터 타입
        v_bool: Boolean 값
        v_byte: Raw 바이트 값 (항상 저장)
        v_int: 정수 값 (INT16~INT32, UINT16~UINT32)
        v_bigint: 큰 정수 값 (INT64, UINT64)
        v_float: 실수 값 (FLOAT32, FLOAT64)
        v_text: 문자열 값
        quality_code: 데이터 품질 코드 (1=정상, 0=통신이상)

    DB Schema:
        CREATE TABLE plc_data_integrated (
            source_time     TIMESTAMPTZ       NOT NULL,
            server_time     TIMESTAMPTZ       NOT NULL DEFAULT NOW(),
            plc_id          SMALLINT          NOT NULL,
            tag_id          INTEGER           NOT NULL,
            v_bool          BOOLEAN,
            v_byte          SMALLINT,         -- Raw 바이트 (항상 저장)
            v_int           INTEGER,
            v_bigint        BIGINT,
            v_float         DOUBLE PRECISION,
            v_text          TEXT,
            quality_code    SMALLINT DEFAULT 1
        );
    """
    source_time: datetime
    server_time: datetime
    plc_id: int
    tag_id: int
    data_type: DataType = DataType.FLOAT32

    # 타입별 값 컬럼
    v_bool: Optional[bool] = None
    v_byte: Optional[int] = None          # Raw 바이트 값 (0~255 또는 바이트 배열의 정수 표현)
    v_int: Optional[int] = None           # INT16~INT32, UINT16~UINT32
    v_bigint: Optional[int] = None        # INT64, UINT64, LWORD
    v_float: Optional[float] = None       # FLOAT32, FLOAT64
    v_text: Optional[str] = None          # STRING

    quality_code: int = 1                  # 1=정상, 0=통신이상

    # 메타데이터 (DB 직접 컬럼 아님, 라우팅용)
    collection_group: str = "default"      # 수집 그룹 (fast, alm, log 등)

    @property
    def value(self) -> Any:
        """주요 값 반환 (하위 호환성)."""
        if self.v_bool is not None:
            return self.v_bool
        if self.v_float is not None:
            return self.v_float
        if self.v_bigint is not None:
            return self.v_bigint
        if self.v_int is not None:
            return self.v_int
        if self.v_text is not None:
            return self.v_text
        return self.v_byte

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환 (JSON/MQTT 직렬화용)."""
        result = {
            "source_time": self.source_time.isoformat(timespec='milliseconds'),
            "server_time": self.server_time.isoformat(timespec='milliseconds'),
            "plc_id": self.plc_id,
            "tag_id": self.tag_id,
            "data_type": self.data_type.value,
            "quality": self.quality_code,
        }

        # 값 추가 (None이 아닌 것만)
        if self.v_bool is not None:
            result["v_bool"] = self.v_bool
        if self.v_byte is not None:
            result["v_byte"] = self.v_byte
        if self.v_int is not None:
            result["v_int"] = self.v_int
        if self.v_bigint is not None:
            result["v_bigint"] = self.v_bigint
        if self.v_float is not None:
            result["v_float"] = round(self.v_float, 6) if isinstance(self.v_float, float) else self.v_float
        if self.v_text is not None:
            result["v_text"] = self.v_text

        # 단일 value 필드 (대표값)
        result["value"] = self.value

        # 수집 그룹 (publisher에서 테이블 라우팅용)
        if self.collection_group != "default":
            result["collection_group"] = self.collection_group

        return result

    def to_tuple(self) -> tuple:
        """튜플로 변환 (DB INSERT용)."""
        return (
            self.source_time,
            self.server_time,
            self.plc_id,
            self.tag_id,
            self.v_bool,
            self.v_byte,
            self.v_int,
            self.v_bigint,
            self.v_float,
            self.v_text,
            self.quality_code,
        )


class ICollector(ABC):
    """
    데이터 수집기 인터페이스 (Abstract Base Class).

    모든 프로토콜 수집기(Modbus, FENET, MC Protocol 등)는
    이 인터페이스를 구현해야 합니다.

    책임:
        - 프로토콜별 연결 관리
        - 설정된 주기에 따른 데이터 수집
        - 수집 완료 시 이벤트 발생

    Thread Model:
        - 각 Collector는 독립적인 asyncio Task로 실행
        - 하나의 연결에서 여러 수집 그룹(다른 주기)을 관리

    Example:
        class ModbusCollector(ICollector):
            async def connect(self) -> bool:
                self._client = ModbusTcpClient(self.host, self.port)
                return self._client.connect()

            async def collect(self) -> CollectedData:
                result = self._client.read_holding_registers(...)
                return CollectedData(
                    source_time=datetime.now(),
                    collection_time=datetime.now(),
                    plc_id=self.plc_id,
                    raw_data=result.registers,
                )
    """

    def __init__(self, plc_id: int, name: str):
        """
        Args:
            plc_id: PLC 고유 식별자
            name: 수집기 이름 (로깅용)
        """
        self._plc_id = plc_id
        self._name = name
        self._state = ConnectionState.DISCONNECTED
        self._is_running = False
        self._tags: Dict[str, List[TagDefinition]] = {}  # group -> tags

    @property
    def plc_id(self) -> int:
        """PLC 식별자."""
        return self._plc_id

    @property
    def name(self) -> str:
        """수집기 이름."""
        return self._name

    @property
    def state(self) -> ConnectionState:
        """현재 연결 상태."""
        return self._state

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._is_running

    def register_tags(self, group: str, tags: List[TagDefinition]) -> None:
        """
        수집 그룹에 태그 등록.

        같은 주기로 수집할 태그들을 그룹으로 묶습니다.

        Args:
            group: 수집 그룹명 (예: "1sec", "1min")
            tags: 해당 그룹에 속하는 태그 정의 리스트
        """
        self._tags[group] = tags

    @abstractmethod
    async def connect(self) -> bool:
        """
        대상 장비에 연결.

        Returns:
            연결 성공 여부

        Raises:
            ConnectionError: 연결 실패 시
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """연결 해제."""
        pass

    @abstractmethod
    async def collect(self, group: str) -> Optional[CollectedData]:
        """
        지정된 그룹의 데이터 수집.

        Args:
            group: 수집 그룹명

        Returns:
            수집된 Raw 데이터, 실패 시 None
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결이 정상이면 True
        """
        pass

    async def start(self) -> None:
        """수집기 시작."""
        self._is_running = True
        self._state = ConnectionState.CONNECTING

    async def stop(self) -> None:
        """수집기 중지."""
        self._is_running = False
        await self.disconnect()
        self._state = ConnectionState.DISCONNECTED


class IProcessor(ABC):
    """
    데이터 처리기 인터페이스 (Abstract Base Class).

    Collector로부터 받은 Raw 데이터를 파싱하고
    스케일링을 적용하여 ProcessedData로 변환합니다.

    책임:
        - Raw 데이터 파싱 (바이트 → 값)
        - 데이터 타입 변환
        - 스케일링 적용
        - 버퍼에 데이터 적재

    Thread Model:
        - 이벤트 기반으로 동작 (DATA_COLLECTED 이벤트 수신)
        - 처리 완료 시 버퍼에 데이터 추가

    Example:
        class ModbusProcessor(IProcessor):
            def process(self, data: CollectedData) -> List[ProcessedData]:
                results = []
                for tag in self._tags:
                    value = self._parse_register(data.raw_data, tag)
                    results.append(ProcessedData(
                        source_time=data.source_time,
                        server_time=datetime.now(),
                        plc_id=data.plc_id,
                        tag_id=tag.tag_id,
                        value=tag.apply_scaling(value),
                    ))
                return results
    """

    def __init__(self, name: str):
        """
        Args:
            name: 처리기 이름 (로깅용)
        """
        self._name = name
        self._is_running = False
        self._tags: Dict[int, TagDefinition] = {}  # tag_id -> TagDefinition

    @property
    def name(self) -> str:
        """처리기 이름."""
        return self._name

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._is_running

    def register_tag(self, tag: TagDefinition) -> None:
        """태그 정의 등록."""
        self._tags[tag.tag_id] = tag

    def register_tags(self, tags: List[TagDefinition]) -> None:
        """태그 정의 일괄 등록."""
        for tag in tags:
            self._tags[tag.tag_id] = tag

    @abstractmethod
    async def process(self, data: CollectedData) -> List[ProcessedData]:
        """
        Raw 데이터 처리.

        Args:
            data: Collector로부터 받은 Raw 데이터

        Returns:
            처리된 데이터 리스트 (태그별 1개씩)
        """
        pass

    async def start(self) -> None:
        """처리기 시작."""
        self._is_running = True

    async def stop(self) -> None:
        """처리기 중지."""
        self._is_running = False


class IPublisher(ABC):
    """
    데이터 발행기 인터페이스 (Abstract Base Class).

    버퍼에서 데이터를 가져와 외부 시스템(DB, MQTT 등)으로 전송합니다.

    책임:
        - 버퍼 모니터링
        - 배치 단위 데이터 전송
        - 재시도 로직 처리
        - 전송 실패 데이터 보존

    Thread Model:
        - 독립적인 asyncio Task로 실행
        - 설정된 간격 또는 버퍼 임계값 도달 시 전송

    Example:
        class RabbitMQPublisher(IPublisher):
            async def publish(self, data: List[ProcessedData]) -> bool:
                message = aio_pika.Message(body=serialized_data)
                await self._exchange.publish(message, routing_key)
                return True
    """

    def __init__(self, name: str):
        """
        Args:
            name: 발행기 이름 (로깅용)
        """
        self._name = name
        self._is_running = False
        self._is_connected = False

    @property
    def name(self) -> str:
        """발행기 이름."""
        return self._name

    @property
    def is_running(self) -> bool:
        """실행 중 여부."""
        return self._is_running

    @property
    def is_connected(self) -> bool:
        """연결 상태."""
        return self._is_connected

    @abstractmethod
    async def connect(self) -> bool:
        """
        대상 시스템에 연결.

        Returns:
            연결 성공 여부
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """연결 해제."""
        pass

    @abstractmethod
    async def publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 발행.

        Args:
            data: 발행할 처리된 데이터 리스트

        Returns:
            발행 성공 여부
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결이 정상이면 True
        """
        pass

    async def start(self) -> None:
        """발행기 시작."""
        self._is_running = True

    async def stop(self) -> None:
        """발행기 중지."""
        self._is_running = False
        await self.disconnect()
