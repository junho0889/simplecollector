"""
로깅 시스템 모듈
================

수집 로그와 송신 로그를 분리하여 관리합니다.
각 로거는 독립적인 로그 레벨을 가집니다.

Logger Hierarchy:
    collector                 # 루트 로거
    ├── collector.collection  # 수집 로그
    ├── collector.publish     # 송신 로그
    ├── collector.system      # 시스템 로그
    ├── collector.loss        # 데이터 손실 로그
    └── collector.error       # 상세 에러 로그

Features:
    - 수집/송신 로그 분리
    - 각 로거별 독립 로그 레벨
    - 파일 로테이션 지원
    - JSON 포맷 (Kibana/ELK 연동)
    - ECS (Elastic Common Schema) 호환
    - 개월 단위 압축 아카이브
    - 아카이브 파일 수 관리
    - 상세 에러 로깅 (컨텍스트, 스택트레이스)
    - 컬러 콘솔 출력

Usage:
    from src.utils.logging import setup_logging, LoggerFactory, log_error

    # 로깅 초기화
    setup_logging(logging_config)

    # 로거 가져오기
    collection_logger = LoggerFactory.get_collection_logger()
    publish_logger = LoggerFactory.get_publish_logger()

    # 상세 에러 로깅
    log_error(e, context={"plc_id": 1, "tag": "Temperature"})
"""

import gzip
import logging
import logging.handlers
import os
import shutil
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
import json

from ..core.config import LoggingConfig


# ============================================================================
# VERBOSE 로그 레벨 추가 (DEBUG보다 낮은 레벨)
# ============================================================================
VERBOSE = 5
logging.addLevelName(VERBOSE, "VERBOSE")


def verbose(self, message, *args, **kwargs):
    """VERBOSE 레벨 로깅 메서드."""
    if self.isEnabledFor(VERBOSE):
        self._log(VERBOSE, message, args, **kwargs)


# Logger 클래스에 verbose 메서드 추가
logging.Logger.verbose = verbose


def get_log_level(level_name: str, default: int = logging.INFO) -> int:
    """
    로그 레벨 이름을 레벨 값으로 변환.

    VERBOSE 레벨을 포함하여 처리합니다.

    Args:
        level_name: 레벨 이름 (VERBOSE, DEBUG, INFO, WARNING, ERROR, CRITICAL)
        default: 인식 못하는 경우 기본값

    Returns:
        로그 레벨 정수값
    """
    level_name_upper = level_name.upper()
    if level_name_upper == "VERBOSE":
        return VERBOSE
    return getattr(logging, level_name_upper, default)


# ============================================================================
# ECS (Elastic Common Schema) 호환 JSON 포매터
# ============================================================================

class ECSJsonFormatter(logging.Formatter):
    """
    Elastic Common Schema (ECS) 호환 JSON 포매터.

    Kibana, Elasticsearch, Logstash 등과 연동할 때 사용합니다.
    https://www.elastic.co/guide/en/ecs/current/index.html

    ECS 필드 매핑:
        - @timestamp: 이벤트 발생 시간
        - log.level: 로그 레벨
        - log.logger: 로거 이름
        - message: 로그 메시지
        - error.*: 에러 정보
        - host.*: 호스트 정보
        - service.*: 서비스 정보
        - labels.*: 커스텀 라벨
    """

    def __init__(
        self,
        service_name: str = "simple-collector",
        service_version: str = "",
        environment: str = "production",
    ):
        if not service_version:
            from ..version import APP_VERSION
            service_version = APP_VERSION
        super().__init__()
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment
        self._hostname = os.environ.get("HOSTNAME", os.environ.get("COMPUTERNAME", "unknown"))

    def format(self, record: logging.LogRecord) -> str:
        """ECS 형식으로 레코드 포맷팅."""
        # 기본 ECS 구조
        log_data: Dict[str, Any] = {
            "@timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
            "log": {
                "level": record.levelname.lower(),
                "logger": record.name,
                "origin": {
                    "file": {
                        "name": record.filename,
                        "line": record.lineno,
                    },
                    "function": record.funcName,
                },
            },
            "message": record.getMessage(),
            "host": {
                "hostname": self._hostname,
            },
            "service": {
                "name": self.service_name,
                "version": self.service_version,
                "environment": self.environment,
            },
            "process": {
                "pid": record.process,
                "thread": {
                    "id": record.thread,
                    "name": record.threadName,
                },
            },
        }

        # 에러 정보 (ECS error.* 필드)
        if record.exc_info:
            exc_type, exc_value, exc_tb = record.exc_info
            log_data["error"] = {
                "type": exc_type.__name__ if exc_type else "Unknown",
                "message": str(exc_value) if exc_value else "",
                "stack_trace": self.formatException(record.exc_info),
            }

        # 추가 필드를 labels로 매핑
        labels: Dict[str, Any] = {}
        event: Dict[str, Any] = {}

        for key, value in record.__dict__.items():
            if key.startswith("_"):
                continue
            if key in [
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message", "asctime", "taskName",
            ]:
                continue

            # 특수 필드 매핑
            if key == "plc_id":
                labels["plc_id"] = value
            elif key == "tag_id":
                labels["tag_id"] = value
            elif key == "tag_name":
                labels["tag_name"] = value
            elif key == "group":
                labels["collection_group"] = value
            elif key == "duration_ms":
                event["duration"] = int(value * 1_000_000)  # nanoseconds
            elif key == "records_count":
                labels["records_count"] = value
            elif key == "quality_code":
                labels["quality_code"] = value
            elif key == "error_code":
                labels["error_code"] = value
            else:
                labels[key] = value

        if labels:
            log_data["labels"] = labels
        if event:
            log_data["event"] = event

        return json.dumps(log_data, ensure_ascii=False, default=str)


class SimpleJsonFormatter(logging.Formatter):
    """
    간단한 JSON 포매터.

    ECS 없이 기본 JSON 형식으로 로그를 출력합니다.
    """

    def format(self, record: logging.LogRecord) -> str:
        """레코드를 JSON으로 포맷팅."""
        log_data: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": record.process,
            "thread_id": record.thread,
        }

        # 예외 정보
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": self.formatException(record.exc_info),
            }

        # 추가 필드
        for key, value in record.__dict__.items():
            if key.startswith("_"):
                continue
            if key not in [
                "name", "msg", "args", "created", "filename", "funcName",
                "levelname", "levelno", "lineno", "module", "msecs",
                "pathname", "process", "processName", "relativeCreated",
                "stack_info", "exc_info", "exc_text", "thread", "threadName",
                "message", "asctime", "taskName",
            ]:
                log_data[key] = value

        return json.dumps(log_data, ensure_ascii=False, default=str)


# ============================================================================
# 컬러 포매터
# ============================================================================

class ColoredFormatter(logging.Formatter):
    """
    컬러 콘솔 출력 포매터.

    로그 레벨에 따라 다른 색상을 적용합니다.
    """
    COLORS = {
        'VERBOSE': '\033[90m',     # Gray (dim)
        'DEBUG': '\033[36m',       # Cyan
        'INFO': '\033[32m',        # Green
        'WARNING': '\033[33m',     # Yellow
        'ERROR': '\033[31m',       # Red
        'CRITICAL': '\033[35;1m',  # Magenta Bold
    }
    RESET = '\033[0m'

    def format(self, record: logging.LogRecord) -> str:
        """레코드 포맷팅."""
        original_levelname = record.levelname
        color = self.COLORS.get(record.levelname, '')
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        result = super().format(record)
        record.levelname = original_levelname
        return result


# ============================================================================
# 압축 지원 로테이팅 핸들러
# ============================================================================

class CompressingRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """
    압축을 지원하는 로테이팅 파일 핸들러.

    로테이션 시 이전 파일을 gzip 압축합니다.
    """

    def __init__(
        self,
        filename: str,
        mode: str = "a",
        maxBytes: int = 0,
        backupCount: int = 0,
        encoding: Optional[str] = None,
        compress: bool = True,
    ):
        self.compress = compress
        super().__init__(filename, mode, maxBytes, backupCount, encoding)

    def doRollover(self) -> None:
        """로테이션 수행 및 압축."""
        if self.stream:
            self.stream.close()
            self.stream = None

        if self.backupCount > 0:
            # 기존 백업 파일 이동
            for i in range(self.backupCount - 1, 0, -1):
                sfn = self.rotation_filename(f"{self.baseFilename}.{i}")
                dfn = self.rotation_filename(f"{self.baseFilename}.{i + 1}")

                if self.compress:
                    sfn_gz = f"{sfn}.gz"
                    dfn_gz = f"{dfn}.gz"
                    if os.path.exists(sfn_gz):
                        if os.path.exists(dfn_gz):
                            os.remove(dfn_gz)
                        os.rename(sfn_gz, dfn_gz)
                else:
                    if os.path.exists(sfn):
                        if os.path.exists(dfn):
                            os.remove(dfn)
                        os.rename(sfn, dfn)

            dfn = self.rotation_filename(f"{self.baseFilename}.1")
            if os.path.exists(dfn):
                os.remove(dfn)
            if os.path.exists(self.baseFilename):
                if self.compress:
                    # 압축하여 저장
                    dfn_gz = f"{dfn}.gz"
                    if os.path.exists(dfn_gz):
                        os.remove(dfn_gz)
                    with open(self.baseFilename, "rb") as f_in:
                        with gzip.open(dfn_gz, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)
                    os.remove(self.baseFilename)
                else:
                    os.rename(self.baseFilename, dfn)

        if not self.delay:
            self.stream = self._open()


# ============================================================================
# 월별 아카이브 관리자
# ============================================================================

class MonthlyArchiveHandler:
    """
    월별 아카이브 관리자.

    - 지정된 일수 후 로그 파일 압축
    - 월별로 아카이브 생성
    - 최대 아카이브 수 초과 시 오래된 것 삭제
    """

    def __init__(
        self,
        log_dir: str,
        archive_dir: str,
        compress_after_days: int = 7,
        max_archives: int = 12,
    ):
        self.log_dir = Path(log_dir)
        self.archive_dir = Path(archive_dir)
        self.compress_after_days = compress_after_days
        self.max_archives = max_archives
        self._lock = threading.Lock()

        self.archive_dir.mkdir(parents=True, exist_ok=True)

    def archive_old_logs(self) -> List[str]:
        """
        오래된 로그 파일을 아카이브.

        Returns:
            아카이브된 파일 목록
        """
        archived = []
        cutoff_date = datetime.now() - timedelta(days=self.compress_after_days)

        with self._lock:
            # 압축되지 않은 백업 로그 파일 검색
            for log_file in self.log_dir.glob("*.log.*"):
                if log_file.suffix == ".gz":
                    continue

                # 파일 수정 시간 확인
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime < cutoff_date:
                    # 압축
                    gz_path = str(log_file) + ".gz"
                    with open(log_file, "rb") as f_in:
                        with gzip.open(gz_path, "wb") as f_out:
                            shutil.copyfileobj(f_in, f_out)

                    log_file.unlink()
                    archived.append(str(log_file))

            # 오래된 아카이브 정리
            self._cleanup_old_archives()

        return archived

    def create_monthly_archive(self) -> Optional[str]:
        """
        월별 통합 아카이브 생성.

        Returns:
            생성된 아카이브 경로 또는 None
        """
        import tarfile

        now = datetime.now()
        last_month = now.replace(day=1) - timedelta(days=1)
        archive_name = f"logs_{last_month.strftime('%Y%m')}.tar.gz"
        archive_path = self.archive_dir / archive_name

        if archive_path.exists():
            return None

        with self._lock:
            # 지난달 로그 파일 수집
            files_to_archive = []
            for log_file in self.log_dir.glob("*.gz"):
                mtime = datetime.fromtimestamp(log_file.stat().st_mtime)
                if mtime.year == last_month.year and mtime.month == last_month.month:
                    files_to_archive.append(log_file)

            if not files_to_archive:
                return None

            # tar.gz 생성
            with tarfile.open(archive_path, "w:gz") as tar:
                for f in files_to_archive:
                    tar.add(f, arcname=f.name)

            # 원본 삭제
            for f in files_to_archive:
                f.unlink()

            # 오래된 아카이브 정리
            self._cleanup_old_archives()

            return str(archive_path)

    def _cleanup_old_archives(self) -> List[str]:
        """
        최대 개수 초과 아카이브 삭제.

        Returns:
            삭제된 파일 목록
        """
        deleted = []
        archives = sorted(
            self.archive_dir.glob("logs_*.tar.gz"),
            key=lambda f: f.stat().st_mtime,
        )

        while len(archives) > self.max_archives:
            oldest = archives.pop(0)
            oldest.unlink()
            deleted.append(str(oldest))

        return deleted


# ============================================================================
# 로거 팩토리
# ============================================================================

class LoggerFactory:
    """
    로거 팩토리 클래스.

    애플리케이션 전체에서 일관된 로거 인스턴스를 제공합니다.

    Logger Types:
        - collection: 데이터 수집 관련 로그
        - publish: 데이터 송신 관련 로그
        - system: 시스템 이벤트 로그
        - loss: 데이터 손실 로그
        - error: 상세 에러 로그
    """

    ROOT_LOGGER_NAME = "collector"
    _initialized = False
    _config: Optional[LoggingConfig] = None
    _archive_handler: Optional[MonthlyArchiveHandler] = None

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """일반 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.{name}")

    @classmethod
    def get_collection_logger(cls) -> logging.Logger:
        """수집 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.collection")

    @classmethod
    def get_publish_logger(cls) -> logging.Logger:
        """송신 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.publish")

    @classmethod
    def get_system_logger(cls) -> logging.Logger:
        """시스템 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.system")

    @classmethod
    def get_loss_logger(cls) -> logging.Logger:
        """데이터 손실 전용 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.loss")

    @classmethod
    def get_error_logger(cls) -> logging.Logger:
        """상세 에러 로거 가져오기."""
        return logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.error")

    @classmethod
    def set_level(cls, logger_name: str, level: str) -> None:
        """로거 레벨 동적 변경."""
        logger = logging.getLogger(f"{cls.ROOT_LOGGER_NAME}.{logger_name}")
        logger.setLevel(get_log_level(level))

    @classmethod
    def get_archive_handler(cls) -> Optional[MonthlyArchiveHandler]:
        """아카이브 핸들러 가져오기."""
        return cls._archive_handler


# ============================================================================
# 상세 에러 로깅 헬퍼
# ============================================================================

def log_error(
    exception: Exception,
    message: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
    level: int = logging.ERROR,
) -> None:
    """
    상세 에러 로깅.

    에러 발생 시 컨텍스트, 스택 트레이스를 포함한 상세 정보를 기록합니다.

    Args:
        exception: 발생한 예외
        message: 추가 메시지 (선택)
        context: 컨텍스트 정보 (plc_id, tag_name 등)
        logger: 사용할 로거 (기본: error 로거)
        level: 로그 레벨 (기본: ERROR)

    Example:
        try:
            result = await collector.read_tags()
        except Exception as e:
            log_error(e, "Failed to read tags", {
                "plc_id": 1,
                "group": "fast",
                "tag_count": 100,
            })
    """
    if logger is None:
        logger = LoggerFactory.get_error_logger()

    # 에러 메시지 구성
    error_type = type(exception).__name__
    error_msg = str(exception)
    full_message = message or f"{error_type}: {error_msg}"

    # extra 필드 구성
    extra: Dict[str, Any] = {
        "error_type": error_type,
        "error_message": error_msg,
        "error_code": getattr(exception, "errno", None) or getattr(exception, "code", None),
    }

    if context:
        extra.update(context)

    # 로깅
    logger.log(level, full_message, exc_info=True, extra=extra)


def log_warning(
    message: str,
    context: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    경고 로깅 헬퍼.

    Args:
        message: 경고 메시지
        context: 컨텍스트 정보
        logger: 사용할 로거
    """
    if logger is None:
        logger = LoggerFactory.get_system_logger()

    extra = context or {}
    logger.warning(message, extra=extra)


def log_data_loss(
    reason: str,
    lost_count: int,
    context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    데이터 손실 로깅.

    Args:
        reason: 손실 원인
        lost_count: 손실 데이터 수
        context: 추가 컨텍스트
    """
    logger = LoggerFactory.get_loss_logger()

    extra: Dict[str, Any] = {
        "loss_reason": reason,
        "lost_count": lost_count,
    }
    if context:
        extra.update(context)

    logger.warning(
        f"Data loss: {reason} - {lost_count} records lost",
        extra=extra,
    )


# ============================================================================
# 로깅 초기화
# ============================================================================

def setup_logging(config: LoggingConfig) -> None:
    """
    로깅 시스템 초기화.

    Args:
        config: 로깅 설정
    """
    # 루트 로거 설정
    root_logger = logging.getLogger(LoggerFactory.ROOT_LOGGER_NAME)
    root_logger.setLevel(get_log_level(config.level))
    root_logger.handlers.clear()

    # 콘솔 핸들러
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(VERBOSE)  # VERBOSE 레벨까지 출력 가능

    if sys.stdout.isatty():
        console_formatter = ColoredFormatter(config.format)
    else:
        console_formatter = logging.Formatter(config.format)

    console_handler.setFormatter(console_formatter)
    root_logger.addHandler(console_handler)

    # 3rd-party 라이브러리 로거 레벨 제어 (DEBUG 시 내부 로그 폭주 방지)
    noisy_loggers = [
        "aio_pika", "aiormq", "asyncpg",
        "aio_pika.robust_connection", "aio_pika.connection",
        "aio_pika.channel", "aio_pika.queue",
        "aiormq.connection", "bleak",
    ]
    app_level = get_log_level(config.level)
    lib_level = logging.WARNING if app_level <= logging.DEBUG else app_level
    for lib_name in noisy_loggers:
        logging.getLogger(lib_name).setLevel(lib_level)

    # 파일 핸들러
    if config.file_path:
        log_path = Path(config.file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 압축 지원 로테이팅 핸들러
        file_handler = CompressingRotatingFileHandler(
            filename=str(log_path),
            maxBytes=config.max_size_mb * 1024 * 1024,
            backupCount=config.backup_count,
            encoding="utf-8",
            compress=config.compress_enabled,
        )
        file_handler.setLevel(VERBOSE)  # VERBOSE 레벨까지 기록 가능
        file_handler.setFormatter(logging.Formatter(config.format))
        root_logger.addHandler(file_handler)

        # 아카이브 핸들러 설정
        if config.archive_path:
            LoggerFactory._archive_handler = MonthlyArchiveHandler(
                log_dir=str(log_path.parent),
                archive_dir=config.archive_path,
                compress_after_days=config.compress_after_days,
                max_archives=config.max_archive_count,
            )

    # JSON 로깅 (Kibana/ELK 연동)
    if config.json_enabled and config.json_file_path:
        json_path = Path(config.json_file_path)
        json_path.parent.mkdir(parents=True, exist_ok=True)

        json_handler = CompressingRotatingFileHandler(
            filename=str(json_path),
            maxBytes=config.max_size_mb * 1024 * 1024,
            backupCount=config.backup_count,
            encoding="utf-8",
            compress=config.compress_enabled,
        )
        json_handler.setLevel(VERBOSE)  # VERBOSE 레벨까지 기록 가능

        if config.ecs_enabled:
            json_handler.setFormatter(ECSJsonFormatter(
                service_name="simple-collector",
                environment=os.environ.get("ENVIRONMENT", "production"),
            ))
        else:
            json_handler.setFormatter(SimpleJsonFormatter())

        root_logger.addHandler(json_handler)

    # 하위 로거 레벨 설정
    _setup_child_loggers(config)

    # 손실 로그 별도 파일
    if config.file_path:
        _setup_loss_logger(config)

    # 에러 로그 별도 파일
    if config.error_detail_enabled and config.file_path:
        _setup_error_logger(config)

    LoggerFactory._initialized = True
    LoggerFactory._config = config

    system_logger = LoggerFactory.get_system_logger()
    system_logger.info(
        f"Logging initialized - "
        f"Level: {config.level}, "
        f"Collection: {config.collection_level}, "
        f"Publish: {config.publish_level}, "
        f"JSON: {config.json_enabled}, "
        f"Compress: {config.compress_enabled}"
    )


def _setup_child_loggers(config: LoggingConfig) -> None:
    """하위 로거 레벨 설정."""
    loggers = {
        "collection": config.collection_level,
        "publish": config.publish_level,
        "system": config.level,
        "loss": "WARNING",
        "error": "ERROR" if config.error_detail_enabled else "CRITICAL",
    }

    for name, level in loggers.items():
        logger = logging.getLogger(f"{LoggerFactory.ROOT_LOGGER_NAME}.{name}")
        logger.setLevel(get_log_level(level))


def _setup_loss_logger(config: LoggingConfig) -> None:
    """손실 로그 별도 파일 설정."""
    loss_logger = LoggerFactory.get_loss_logger()
    loss_log_path = Path(config.file_path).parent / "loss.log"

    loss_handler = CompressingRotatingFileHandler(
        filename=str(loss_log_path),
        maxBytes=config.max_size_mb * 1024 * 1024,
        backupCount=config.backup_count,
        encoding="utf-8",
        compress=config.compress_enabled,
    )
    loss_handler.setLevel(logging.WARNING)

    # JSON 형식으로 손실 기록 (분석 용이)
    if config.json_enabled:
        loss_handler.setFormatter(SimpleJsonFormatter())
    else:
        loss_handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        ))

    loss_logger.addHandler(loss_handler)


def _setup_error_logger(config: LoggingConfig) -> None:
    """에러 로그 별도 파일 설정."""
    error_logger = LoggerFactory.get_error_logger()
    error_log_path = Path(config.file_path).parent / "error.log"

    error_handler = CompressingRotatingFileHandler(
        filename=str(error_log_path),
        maxBytes=config.max_size_mb * 1024 * 1024,
        backupCount=config.backup_count,
        encoding="utf-8",
        compress=config.compress_enabled,
    )
    error_handler.setLevel(logging.ERROR)

    # 상세 에러 포맷
    if config.json_enabled:
        error_handler.setFormatter(ECSJsonFormatter() if config.ecs_enabled else SimpleJsonFormatter())
    else:
        error_format = (
            "=" * 80 + "\n"
            "%(asctime)s | %(levelname)s | %(name)s\n"
            "Message: %(message)s\n"
            "Location: %(module)s.%(funcName)s:%(lineno)d\n"
        )
        error_handler.setFormatter(logging.Formatter(error_format))

    error_logger.addHandler(error_handler)


# ============================================================================
# 로그 컨텍스트
# ============================================================================

class LogContext:
    """
    로그 컨텍스트 매니저.

    특정 작업에 대한 로그 컨텍스트를 추가합니다.

    Example:
        with LogContext(plc_id=1, group="fast"):
            logger.info("Processing data")
            # JSON: {"labels": {"plc_id": 1, "group": "fast"}, ...}
    """

    def __init__(self, **kwargs):
        self._context = kwargs
        self._old_factory = None

    def __enter__(self):
        self._old_factory = logging.getLogRecordFactory()
        context = self._context

        def record_factory(*args, **kwargs):
            record = self._old_factory(*args, **kwargs)
            for key, value in context.items():
                setattr(record, key, value)
            return record

        logging.setLogRecordFactory(record_factory)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        logging.setLogRecordFactory(self._old_factory)
        return False


# ============================================================================
# 아카이브 스케줄러
# ============================================================================

def run_archive_task() -> Dict[str, Any]:
    """
    아카이브 작업 실행.

    Returns:
        작업 결과 정보
    """
    handler = LoggerFactory.get_archive_handler()
    if not handler:
        return {"status": "skipped", "reason": "Archive handler not configured"}

    result = {
        "status": "completed",
        "archived_files": [],
        "monthly_archive": None,
        "deleted_archives": [],
    }

    try:
        # 오래된 로그 압축
        archived = handler.archive_old_logs()
        result["archived_files"] = archived

        # 월별 아카이브 생성
        monthly = handler.create_monthly_archive()
        result["monthly_archive"] = monthly

        LoggerFactory.get_system_logger().info(
            f"Archive task completed: {len(archived)} files archived"
        )

    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        log_error(e, "Archive task failed")

    return result


# ============================================================================
# JSON 로깅 별도 설정 (하위 호환)
# ============================================================================

def setup_json_logging(config: LoggingConfig, json_file_path: str) -> None:
    """
    JSON 형식 로깅 추가 설정.

    기존 로깅에 JSON 파일 출력을 추가합니다.
    로그 분석 시스템과 연동할 때 사용합니다.

    Args:
        config: 로깅 설정
        json_file_path: JSON 로그 파일 경로
    """
    root_logger = logging.getLogger(LoggerFactory.ROOT_LOGGER_NAME)

    json_path = Path(json_file_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    json_handler = CompressingRotatingFileHandler(
        filename=str(json_path),
        maxBytes=config.max_size_mb * 1024 * 1024,
        backupCount=config.backup_count,
        encoding="utf-8",
        compress=config.compress_enabled,
    )
    json_handler.setLevel(logging.DEBUG)

    if getattr(config, 'ecs_enabled', True):
        json_handler.setFormatter(ECSJsonFormatter())
    else:
        json_handler.setFormatter(SimpleJsonFormatter())

    root_logger.addHandler(json_handler)

    logging.getLogger(f"{LoggerFactory.ROOT_LOGGER_NAME}.system").info(
        f"JSON logging enabled: {json_file_path}"
    )
