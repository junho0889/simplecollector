"""
설정 관리 모듈
==============

YAML 및 CSV 파일에서 설정을 로드하고 관리합니다.

Configuration Structure:
    config/
    ├── collector.yaml    # 메인 설정 파일 (접속정보, 로그, 버퍼 등)
    └── tags.csv          # 태그 정의 파일

YAML 설정 구조:
    - collector: 수집기 설정 (프로토콜, 접속정보, 수집주기)
    - publisher: 발행기 설정 (RabbitMQ 접속정보)
    - buffer: 버퍼 설정 (크기, 배치, 임계값)
    - logging: 로그 설정 (레벨, 파일경로)

CSV 태그 구조:
    tag_id, tag_name, address, data_type, scale, offset, unit, description, collection_group

Environment Variables:
    설정 값은 환경변수로 오버라이드 가능합니다.
    예: ${DB_HOST} → 환경변수 DB_HOST 값으로 대체

Usage:
    # 설정 로드
    config = ConfigLoader.load("config/collector.yaml")

    # 태그 로드
    tags = ConfigLoader.load_tags("config/tags.csv")

    # 환경변수 치환
    rmq_host = config.publisher.rabbitmq.host  # 환경변수 적용됨
"""

import csv
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

import yaml

from .interfaces import TagDefinition, DataType

logger = logging.getLogger(__name__)


# ============================================================================
# 설정 데이터 클래스
# ============================================================================

@dataclass
class LoggingConfig:
    """
    로깅 설정.

    Attributes:
        level: 기본 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        collection_level: 수집 로그 레벨
        publish_level: 송신 로그 레벨
        file_path: 로그 파일 경로 (None이면 콘솔만)
        max_size_mb: 로그 파일 최대 크기 (MB)
        backup_count: 백업 로그 파일 수
        format: 로그 포맷

        # 압축 및 보관 설정
        compress_enabled: 압축 활성화 여부
        compress_after_days: N일 후 압축 (기본 7일)
        archive_after_months: N개월 후 아카이브 (기본 1개월)
        max_archive_count: 최대 아카이브 파일 수 (초과 시 오래된 것 삭제)
        archive_path: 아카이브 저장 경로

        # JSON 로깅 (Kibana/ELK 연동)
        json_enabled: JSON 로깅 활성화
        json_file_path: JSON 로그 파일 경로
        ecs_enabled: Elastic Common Schema 호환 여부

        # 상세 에러 로깅
        error_detail_enabled: 에러 상세 로깅 활성화
        include_traceback: 스택 트레이스 포함
        include_context: 컨텍스트 정보 포함
    """
    level: str = "INFO"
    collection_level: str = "INFO"
    publish_level: str = "INFO"
    file_path: Optional[str] = None
    max_size_mb: int = 10
    backup_count: int = 5
    format: str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    # 압축 및 보관 설정
    compress_enabled: bool = True
    compress_after_days: int = 7
    archive_after_months: int = 1
    max_archive_count: int = 12  # 12개월치 보관
    archive_path: Optional[str] = "logs/archive"

    # JSON 로깅 (Kibana/ELK 연동)
    json_enabled: bool = False
    json_file_path: Optional[str] = None
    ecs_enabled: bool = True  # Elastic Common Schema

    # 상세 에러 로깅
    error_detail_enabled: bool = True
    include_traceback: bool = True
    include_context: bool = True


@dataclass
class BufferConfig:
    """
    버퍼 설정.

    Attributes:
        max_size: 버퍼 최대 크기
        batch_size: 배치 처리 크기
        threshold_ratio: 임계값 비율 (0.0~1.0)
        drop_oldest: 오버플로우 시 오래된 데이터 삭제 여부
        persist_on_shutdown: 종료 시 데이터 영속화 여부
        persist_path: 영속화 파일 경로
    """
    max_size: int = 10000
    batch_size: int = 100
    threshold_ratio: float = 0.8
    drop_oldest: bool = True
    persist_on_shutdown: bool = True
    persist_path: str = "data/buffer.pkl"


@dataclass
class CollectionGroup:
    """
    수집 그룹 설정.

    같은 주기로 수집되는 태그들의 그룹입니다.

    Attributes:
        name: 그룹 이름 (예: "1sec", "1min")
        interval_ms: 수집 주기 (밀리초)
        timeout_ms: 수집 타임아웃 (밀리초)
        retry_count: 재시도 횟수
        retry_delay_ms: 재시도 간격 (밀리초)
        mode: 수집 모드
            - "polling": 기본 모드, 모든 데이터를 주기적으로 전달
            - "on_change": 값이 변경된 태그만 전달 (알람/이벤트용)
        deadband: 변화 감지 임계값 (on_change 모드에서 사용)
            - 숫자형: 이전값과 차이가 deadband 초과 시 변경으로 감지
            - 0.0: 모든 변화 감지 (기본값)
        deadband_type: deadband 타입
            - "absolute": 절대값 비교 (기본값)
            - "percent": 백분율 비교
    """
    name: str
    interval_ms: int = 1000
    timeout_ms: int = 5000
    retry_count: int = 3
    retry_delay_ms: int = 1000
    mode: str = "polling"  # "polling" | "on_change"
    deadband: float = 0.0
    deadband_type: str = "absolute"  # "absolute" | "percent"


@dataclass
class ProtocolConfig:
    """
    프로토콜 설정 (Modbus, FENET, MC Protocol 등).

    Attributes:
        type: 프로토콜 유형 (modbus, fenet, mcprotocol)
        host: 대상 호스트
        port: 대상 포트
        unit_id: 유닛 ID (Modbus slave ID 등)
        timeout_ms: 연결/요청 타임아웃 (밀리초)
        reconnect_interval_ms: 재연결 간격 (밀리초)
        extra: 프로토콜별 추가 설정
    """
    type: str
    host: str
    port: int
    unit_id: int = 1
    timeout_ms: int = 5000
    reconnect_interval_ms: int = 10000
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BleDeviceEntry:
    """
    BLE 디바이스 엔트리 (devices CSV 한 행).

    Attributes:
        ble_id: BLE 센서 고유 ID (내부적으로 plc_id에 매핑)
        mac_address: BLE MAC 주소
        device_profile: 프로파일 이름 (posiot, posiot_v2 등)
        device_name_filter: BLE LocalName 필터 (선택)
        description: 설명
    """
    ble_id: int
    mac_address: str
    device_profile: str = "posiot"
    device_name_filter: str = ""
    description: str = ""


@dataclass
class CollectorConfig:
    """
    수집기 설정.

    Attributes:
        plc_id: PLC 식별자 (1~10)
        name: 수집기 이름
        enabled: 활성화 여부
        protocol: 프로토콜 설정
        collection_groups: 수집 그룹 리스트
        tags_file: 태그 정의 CSV 파일 경로
        devices_file: BLE 디바이스 목록 CSV 경로 (멀티디바이스 모드, 레거시)
        mode: BLE 수집 모드 ("hardcoded": 프로파일 기반, "flexible": CSV byte_offset 기반)
    """
    plc_id: int = 0
    ble_id: Optional[int] = None  # BLE 전용 디바이스 ID (설정 시 plc_id 대신 사용)
    name: str = "collector"
    description: str = ""
    site: str = ""
    area: str = ""
    line: str = ""
    enabled: bool = True
    protocol: Optional[ProtocolConfig] = None
    collection_groups: List[CollectionGroup] = field(default_factory=list)
    tags_file: str = "config/tags.csv"
    devices_file: Optional[str] = None

    @property
    def device_id(self) -> int:
        """디바이스 식별자 (ble_id 우선, 없으면 plc_id)."""
        return self.ble_id if self.ble_id is not None else self.plc_id

    @property
    def device_type(self) -> str:
        """디바이스 타입 (ble_id 설정 시 'ble', 아니면 'plc')."""
        return "ble" if self.ble_id is not None else "plc"

    @property
    def device_id_key(self) -> str:
        """메시지 키 이름 ('ble_id' 또는 'plc_id')."""
        return "ble_id" if self.ble_id is not None else "plc_id"


@dataclass
class RabbitMQConfig:
    """
    RabbitMQ 발행기 설정.

    Attributes:
        enabled: 활성화 여부
        host: RabbitMQ 호스트
        port: RabbitMQ AMQP 포트
        virtual_host: 가상 호스트
        username: 사용자명
        password: 비밀번호
        exchange_name: 토픽 교환기 이름
        exchange_type: 교환기 타입 (topic)
        routing_key_prefix: 라우팅 키 접두사 (routing_key: "{prefix}.{plc_id}.data")
        compression: 압축 방식 ('none', 'zlib', 'gzip')
        encryption_enabled: 암호화 활성화
        encryption_key: Fernet 암호화 키 (base64)
        heartbeat: AMQP heartbeat 간격 (초)
        connection_timeout: 연결 타임아웃 (초)
        delivery_mode: 메시지 전달 모드 (1=transient, 2=persistent)
    """
    enabled: bool = False
    host: str = "localhost"
    port: int = 5672
    virtual_host: str = "/"
    username: str = "guest"
    password: str = "guest"
    exchange_name: str = "plc.data"
    exchange_type: str = "topic"
    routing_key_prefix: str = "plc"
    compression: str = "zlib"
    encryption_enabled: bool = False
    encryption_key: str = ""
    heartbeat: int = 60
    connection_timeout: int = 10
    delivery_mode: int = 2


@dataclass
class PublisherConfig:
    """
    발행기 설정.

    Attributes:
        rabbitmq: RabbitMQ 설정
        publish_interval_ms: 발행 주기 (밀리초)
        max_retries: 최대 재시도 횟수
        retry_delay_ms: 재시도 간격 (밀리초)
    """
    rabbitmq: RabbitMQConfig = field(default_factory=RabbitMQConfig)
    publish_interval_ms: int = 1000
    max_retries: int = 3
    retry_delay_ms: int = 5000


@dataclass
class AppConfig:
    """
    애플리케이션 전체 설정.

    Attributes:
        collector: 수집기 설정
        publisher: 발행기 설정
        buffer: 버퍼 설정
        logging: 로깅 설정
    """
    collector: CollectorConfig
    publisher: PublisherConfig = field(default_factory=PublisherConfig)
    buffer: BufferConfig = field(default_factory=BufferConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


# ============================================================================
# 설정 로더
# ============================================================================

class ConfigLoader:
    """
    설정 파일 로더.

    YAML 설정 파일과 CSV 태그 파일을 로드합니다.
    환경변수 치환을 지원합니다.

    Environment Variable Substitution:
        YAML 파일 내에서 ${VAR_NAME} 또는 ${VAR_NAME:default} 형식으로
        환경변수를 참조할 수 있습니다.

        예:
            host: ${DB_HOST:localhost}
            password: ${DB_PASSWORD}

    Example:
        # YAML 설정 로드
        config = ConfigLoader.load("config/collector.yaml")

        # 태그 로드
        tags = ConfigLoader.load_tags("config/tags.csv")

        # 환경변수 치환 확인
        print(config.publisher.rabbitmq.host)
    """

    # 환경변수 패턴: ${VAR_NAME} 또는 ${VAR_NAME:default_value}
    ENV_PATTERN = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')

    @classmethod
    def load(cls, config_path: Union[str, Path]) -> AppConfig:
        """
        YAML 설정 파일 로드.

        Args:
            config_path: 설정 파일 경로

        Returns:
            AppConfig 객체

        Raises:
            FileNotFoundError: 파일이 존재하지 않을 때
            ValueError: 설정 파싱 오류
        """
        config_path = Path(config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        logger.info(f"Loading config from: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            raw_config = yaml.safe_load(f)

        # 환경변수 치환
        config_dict = cls._substitute_env_vars(raw_config)

        # AppConfig 객체로 변환
        return cls._parse_config(config_dict)

    @classmethod
    def load_tags(cls, tags_path: Union[str, Path]) -> List[TagDefinition]:
        """
        CSV 태그 정의 파일 로드.

        CSV 컬럼:
            tag_id, tag_name, address, data_type, scale, offset, unit, description, collection_group

        Args:
            tags_path: 태그 파일 경로

        Returns:
            TagDefinition 리스트

        Raises:
            FileNotFoundError: 파일이 존재하지 않을 때
            ValueError: CSV 파싱 오류
        """
        tags_path = Path(tags_path)
        if not tags_path.exists():
            raise FileNotFoundError(f"Tags file not found: {tags_path}")

        logger.info(f"Loading tags from: {tags_path}")

        tags: List[TagDefinition] = []

        with open(tags_path, 'r', encoding='utf-8-sig') as f:
            # '#'으로 시작하는 주석 행 제거
            lines = [line for line in f if not line.strip().startswith('#')]
            reader = csv.DictReader(lines)

            for row_num, row in enumerate(reader, start=2):  # 헤더가 1번째 줄
                try:
                    tag = cls._parse_tag_row(row)
                    tags.append(tag)
                except Exception as e:
                    logger.warning(f"Failed to parse tag at row {row_num}: {e}")
                    continue

        logger.info(f"Loaded {len(tags)} tags")
        return tags

    @classmethod
    def load_devices(cls, devices_path: Union[str, Path]) -> List['BleDeviceEntry']:
        """
        BLE 디바이스 목록 CSV 로드.

        CSV 컬럼:
            ble_id, mac_address, device_profile, device_name_filter, description

        Args:
            devices_path: 디바이스 CSV 경로

        Returns:
            BleDeviceEntry 리스트
        """
        devices_path = Path(devices_path)
        if not devices_path.exists():
            raise FileNotFoundError(f"Devices file not found: {devices_path}")

        logger.info(f"Loading BLE devices from: {devices_path}")

        devices: List[BleDeviceEntry] = []

        with open(devices_path, 'r', encoding='utf-8-sig') as f:
            lines = [line for line in f if not line.strip().startswith('#')]
            reader = csv.DictReader(lines)

            for row_num, row in enumerate(reader, start=2):
                try:
                    device = BleDeviceEntry(
                        ble_id=int(row['ble_id']),
                        mac_address=row['mac_address'].strip().upper(),
                        device_profile=row.get('device_profile', 'posiot').strip(),
                        device_name_filter=row.get('device_name_filter', '').strip(),
                        description=row.get('description', '').strip(),
                    )
                    devices.append(device)
                except Exception as e:
                    logger.warning(f"Failed to parse device at row {row_num}: {e}")
                    continue

        logger.info(f"Loaded {len(devices)} BLE devices")
        return devices

    @classmethod
    def _substitute_env_vars(cls, config: Any) -> Any:
        """
        설정 값에서 환경변수 치환.

        ${VAR_NAME} → 환경변수 값
        ${VAR_NAME:default} → 환경변수 값 또는 기본값

        Args:
            config: 원본 설정 (dict, list, str)

        Returns:
            환경변수가 치환된 설정
        """
        if isinstance(config, dict):
            return {
                key: cls._substitute_env_vars(value)
                for key, value in config.items()
            }
        elif isinstance(config, list):
            return [cls._substitute_env_vars(item) for item in config]
        elif isinstance(config, str):
            return cls._substitute_string(config)
        else:
            return config

    @classmethod
    def _substitute_string(cls, value: str) -> str:
        """
        문자열 내 환경변수 치환.

        Args:
            value: 원본 문자열

        Returns:
            환경변수가 치환된 문자열
        """
        def replace_env(match):
            var_name = match.group(1)
            default_value = match.group(2)
            env_value = os.environ.get(var_name)

            if env_value is not None:
                return env_value
            elif default_value is not None:
                return default_value
            else:
                logger.warning(f"Environment variable not found: {var_name}")
                return match.group(0)  # 원본 유지

        return cls.ENV_PATTERN.sub(replace_env, value)

    @classmethod
    def _parse_config(cls, config_dict: Dict[str, Any]) -> AppConfig:
        """
        딕셔너리를 AppConfig 객체로 변환.

        Args:
            config_dict: 설정 딕셔너리

        Returns:
            AppConfig 객체
        """
        # Collector 설정
        collector_dict = config_dict.get('collector', {})
        protocol_dict = collector_dict.get('protocol', {})
        groups_list = collector_dict.get('collection_groups', [])

        protocol = ProtocolConfig(
            type=protocol_dict.get('type', 'modbus'),
            host=protocol_dict.get('host', 'localhost'),
            port=int(protocol_dict.get('port', 502)),
            unit_id=int(protocol_dict.get('unit_id', 1)),
            timeout_ms=int(protocol_dict.get('timeout_ms', 5000)),
            reconnect_interval_ms=int(protocol_dict.get('reconnect_interval_ms', 10000)),
            extra=protocol_dict.get('extra', {}),
        ) if protocol_dict else None

        collection_groups = [
            CollectionGroup(
                name=g.get('name', 'default'),
                interval_ms=int(g.get('interval_ms', 1000)),
                timeout_ms=int(g.get('timeout_ms', 5000)),
                retry_count=int(g.get('retry_count', 3)),
                retry_delay_ms=int(g.get('retry_delay_ms', 1000)),
                mode=g.get('mode', 'polling'),
                deadband=float(g.get('deadband', 0.0)),
                deadband_type=g.get('deadband_type', 'absolute'),
            )
            for g in groups_list
        ]

        # ble_id가 있으면 BLE 모드, 없으면 plc_id 사용
        ble_id_raw = collector_dict.get('ble_id')
        ble_id = int(ble_id_raw) if ble_id_raw is not None else None

        collector = CollectorConfig(
            plc_id=int(collector_dict.get('plc_id', 0)),
            ble_id=ble_id,
            name=collector_dict.get('name', 'collector'),
            description=collector_dict.get('description', ''),
            site=collector_dict.get('site', ''),
            area=collector_dict.get('area', ''),
            line=collector_dict.get('line', ''),
            enabled=collector_dict.get('enabled', True),
            protocol=protocol,
            collection_groups=collection_groups,
            tags_file=collector_dict.get('tags_file', 'config/tags.csv'),
            devices_file=collector_dict.get('devices_file'),
        )

        # Publisher 설정
        publisher_dict = config_dict.get('publisher', {})

        rabbitmq_dict = publisher_dict.get('rabbitmq', {})
        rabbitmq = RabbitMQConfig(
            enabled=rabbitmq_dict.get('enabled', False),
            host=rabbitmq_dict.get('host', 'localhost'),
            port=int(rabbitmq_dict.get('port', 5672)),
            virtual_host=rabbitmq_dict.get('virtual_host', '/'),
            username=rabbitmq_dict.get('username', 'guest'),
            password=rabbitmq_dict.get('password', 'guest'),
            exchange_name=rabbitmq_dict.get('exchange_name', 'plc.data'),
            exchange_type=rabbitmq_dict.get('exchange_type', 'topic'),
            routing_key_prefix=rabbitmq_dict.get('routing_key_prefix', 'plc'),
            compression=rabbitmq_dict.get('compression', 'zlib'),
            encryption_enabled=rabbitmq_dict.get('encryption_enabled', False),
            encryption_key=rabbitmq_dict.get('encryption_key', ''),
            heartbeat=int(rabbitmq_dict.get('heartbeat', 60)),
            connection_timeout=int(rabbitmq_dict.get('connection_timeout', 10)),
            delivery_mode=int(rabbitmq_dict.get('delivery_mode', 2)),
        )

        publisher = PublisherConfig(
            rabbitmq=rabbitmq,
            publish_interval_ms=int(publisher_dict.get('publish_interval_ms', 1000)),
            max_retries=int(publisher_dict.get('max_retries', 3)),
            retry_delay_ms=int(publisher_dict.get('retry_delay_ms', 5000)),
        )

        # Buffer 설정
        buffer_dict = config_dict.get('buffer', {})
        buffer = BufferConfig(
            max_size=int(buffer_dict.get('max_size', 10000)),
            batch_size=int(buffer_dict.get('batch_size', 100)),
            threshold_ratio=float(buffer_dict.get('threshold_ratio', 0.8)),
            drop_oldest=buffer_dict.get('drop_oldest', True),
            persist_on_shutdown=buffer_dict.get('persist_on_shutdown', True),
            persist_path=buffer_dict.get('persist_path', 'data/buffer.pkl'),
        )

        # Logging 설정
        logging_dict = config_dict.get('logging', {})
        logging_config = LoggingConfig(
            level=logging_dict.get('level', 'INFO'),
            collection_level=logging_dict.get('collection_level', 'INFO'),
            publish_level=logging_dict.get('publish_level', 'INFO'),
            file_path=logging_dict.get('file_path'),
            max_size_mb=int(logging_dict.get('max_size_mb', 10)),
            backup_count=int(logging_dict.get('backup_count', 5)),
            format=logging_dict.get(
                'format',
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            ),
            # 압축 및 보관 설정
            compress_enabled=logging_dict.get('compress_enabled', True),
            compress_after_days=int(logging_dict.get('compress_after_days', 7)),
            archive_after_months=int(logging_dict.get('archive_after_months', 1)),
            max_archive_count=int(logging_dict.get('max_archive_count', 12)),
            archive_path=logging_dict.get('archive_path', 'logs/archive'),
            # JSON 로깅 (Kibana/ELK 연동)
            json_enabled=logging_dict.get('json_enabled', False),
            json_file_path=logging_dict.get('json_file_path'),
            ecs_enabled=logging_dict.get('ecs_enabled', True),
            # 상세 에러 로깅
            error_detail_enabled=logging_dict.get('error_detail_enabled', True),
            include_traceback=logging_dict.get('include_traceback', True),
            include_context=logging_dict.get('include_context', True),
        )

        return AppConfig(
            collector=collector,
            publisher=publisher,
            buffer=buffer,
            logging=logging_config,
        )

    @classmethod
    def _parse_tag_row(cls, row: Dict[str, str]) -> TagDefinition:
        """
        CSV 행을 TagDefinition으로 변환.

        새 포맷 (memory/address 분리, data_size 포함):
            tag_id,tag_name,memory,address,data_size,data_type,collection_group,
            scale,offset,decimals,word_length,format,unit,description

            data_size: 16(WORD), 32(DWORD), 1(BIT)
            data_type: UDEC(unsigned), DEC(signed), FLOAT, STRING, BIT 등

        구 포맷 (하위 호환):
            tag_id,tag_name,address,data_type,collection_group,scale,offset,description

        Args:
            row: CSV 행 딕셔너리

        Returns:
            TagDefinition 객체
        """
        # Optional 정수 파싱 헬퍼
        def parse_optional_int(value: str) -> Optional[int]:
            if value and value.strip():
                try:
                    return int(value.strip())
                except ValueError:
                    return None
            return None

        # Optional float 파싱 헬퍼
        def parse_optional_float(value: str, default: float = 1.0) -> float:
            if value and value.strip():
                try:
                    return float(value.strip())
                except ValueError:
                    return default
            return default

        # Optional bool 파싱 헬퍼
        def parse_optional_bool(value: str) -> bool:
            if value and value.strip().lower() in ('true', '1', 'yes'):
                return True
            return False

        # data_size 파싱 (기본값 16 = WORD)
        data_size = parse_optional_int(row.get('data_size', '')) or 16

        # 원본 데이터 타입 문자열
        raw_type = row.get('data_type', 'float32').strip().upper()

        # 데이터 타입 결정: data_size + raw_type 조합
        data_type = cls._resolve_data_type(raw_type, data_size)

        # memory/address 분리 형식 지원
        memory = row.get('memory', '').strip().upper()
        address_num = row.get('address', '').strip()
        word_length = parse_optional_int(row.get('word_length', ''))

        # 새 포맷: memory와 address 컬럼이 분리됨
        if memory and address_num:
            # word_length가 있으면 주소에 :length 추가 (STRING 타입용)
            if word_length and data_type == DataType.STRING:
                full_address = f"{memory}{address_num}:{word_length}"
            else:
                full_address = f"{memory}{address_num}"
        else:
            # 구 포맷: address 컬럼에 전체 주소 (예: D900:10)
            full_address = row.get('address', '')

        return TagDefinition(
            tag_id=int(row['tag_id']),
            tag_name=row['tag_name'],
            address=full_address,
            data_type=data_type,
            data_size=data_size,
            raw_type=raw_type,
            scale=parse_optional_float(row.get('scale', ''), 1.0),
            offset=parse_optional_float(row.get('offset', ''), 0.0),
            unit=row.get('unit', ''),
            description=row.get('description', ''),
            collection_group=row.get('collection_group', 'default'),
            # 확장 필드
            memory=memory,
            decimals=parse_optional_int(row.get('decimals', '')),
            string_length=parse_optional_int(row.get('string_length', '')),
            word_length=word_length,
            format=row.get('format', ''),
            bool_true_value=parse_optional_int(row.get('bool_true_value', '')),
            bool_false_value=parse_optional_int(row.get('bool_false_value', '')),
            bool_invert=parse_optional_bool(row.get('bool_invert', '')),
            # BLE 확장 필드
            mac_address=row.get('mac_address', '').strip().upper(),
            device_name_filter=row.get('device_name', '').strip(),
            ble_mode=row.get('mode', '').strip().lower(),
            byte_offset=row.get('byte_offset', '').strip() or None,
        )

    @classmethod
    def _resolve_data_type(cls, raw_type: str, data_size: int) -> DataType:
        """
        raw_type과 data_size를 기반으로 DataType 결정.

        MC Protocol CSV 형식:
            - UDEC + 16 → UINT16 (WORD)
            - UDEC + 32 → UINT32 (DWORD)
            - DEC + 16 → INT16
            - DEC + 32 → INT32
            - FLOAT + 32 → FLOAT32
            - STRING → STRING
            - BIT → BOOL

        Args:
            raw_type: 원본 타입 문자열 (UDEC, DEC, FLOAT 등)
            data_size: 비트 크기 (1, 16, 32)

        Returns:
            DataType 열거형
        """
        raw_type = raw_type.upper()

        # 직접 매핑되는 타입들
        direct_map = {
            # 비트 타입
            'BOOL': DataType.BOOL,
            'BIT': DataType.BOOL,
            # 문자열
            'STRING': DataType.STRING,
            # 부동소수점 (직접 지정)
            'FLOAT32': DataType.FLOAT32,
            'FLOAT64': DataType.FLOAT64,
            'REAL': DataType.FLOAT32,
            'LREAL': DataType.FLOAT64,
            # 명시적 정수 타입
            'INT8': DataType.INT8,
            'INT16': DataType.INT16,
            'INT32': DataType.INT32,
            'INT64': DataType.INT64,
            'UINT8': DataType.UINT8,
            'UINT16': DataType.UINT16,
            'UINT32': DataType.UINT32,
            'UINT64': DataType.UINT64,
            # 바이트/워드 타입
            'BYTE': DataType.BYTE,
            'WORD': DataType.WORD,
            'DWORD': DataType.DWORD,
            'LWORD': DataType.LWORD,
        }

        if raw_type in direct_map:
            return direct_map[raw_type]

        # UDEC (Unsigned Decimal) - data_size에 따라 결정
        if raw_type == 'UDEC':
            if data_size == 32:
                return DataType.UINT32
            elif data_size == 64:
                return DataType.UINT64
            else:  # 16 또는 기본값
                return DataType.UINT16

        # DEC (Signed Decimal) - data_size에 따라 결정
        if raw_type == 'DEC':
            if data_size == 32:
                return DataType.INT32
            elif data_size == 64:
                return DataType.INT64
            else:  # 16 또는 기본값
                return DataType.INT16

        # FLOAT - data_size에 따라 결정
        if raw_type == 'FLOAT':
            if data_size == 64:
                return DataType.FLOAT64
            else:  # 32 또는 기본값
                return DataType.FLOAT32

        # 알 수 없는 타입은 FLOAT32로 기본값
        logger.warning(f"Unknown data type '{raw_type}', defaulting to FLOAT32")
        return DataType.FLOAT32

    @classmethod
    def validate_config(cls, config: AppConfig) -> List[str]:
        """
        설정 유효성 검사.

        Args:
            config: 검사할 설정

        Returns:
            오류 메시지 리스트 (빈 리스트면 유효)
        """
        errors: List[str] = []

        # Collector 검사
        # BLE 멀티디바이스 또는 devices_file 설정 시 plc_id 검사 스킵
        is_ble = (config.collector.protocol and
                  config.collector.protocol.type.lower() == 'ble')
        if not config.collector.devices_file and not is_ble:
            if config.collector.plc_id < 1 or config.collector.plc_id > 100:
                errors.append("PLC ID must be between 1 and 100")
        # BLE에서 ble_id 검증
        if is_ble and config.collector.ble_id is not None:
            if config.collector.ble_id < 0 or config.collector.ble_id > 100:
                errors.append("BLE ID must be between 0 and 100")

        if not config.collector.collection_groups:
            errors.append("At least one collection group is required")

        for group in config.collector.collection_groups:
            if group.interval_ms < 10:
                errors.append(
                    f"Collection interval too small for group '{group.name}': "
                    f"{group.interval_ms}ms (min: 10ms)"
                )

        # Publisher 검사 (disabled면 로그만 출력 - 허용)

        # Buffer 검사
        if config.buffer.batch_size > config.buffer.max_size:
            errors.append("Batch size cannot exceed buffer max size")

        if not 0.0 <= config.buffer.threshold_ratio <= 1.0:
            errors.append("Threshold ratio must be between 0.0 and 1.0")

        return errors
