"""
NeuroForge Collector Version Information
====================================

버전 정보의 단일 소스 (Single Source of Truth).
모든 모듈, 로그, metadata.json은 이 파일의 APP_VERSION을 참조합니다.
"""

import sys
import platform
from typing import Dict, List, Tuple

# =============================================================================
# Application Version
# =============================================================================
APP_NAME = "NeuroForge Collector"
APP_VERSION = "0.4.6"
APP_BUILD_DATE = "2026-06-10"

# =============================================================================
# Changelog
# =============================================================================
CHANGELOG: List[Dict[str, str]] = [
    {
        "version": "0.4.6",
        "date": "2026-06-10",
        "changes": (
            "fix: QA 감사 Critical 3건 (C-1/C-2/C-3) — "
            "(1) RabbitMQ publisher 재연결 태스크를 start()에서 상시 기동: "
            "운영 중 브로커 단절 1회로 영구 발행 중단 + 버퍼 drop_oldest 무음 "
            "손실되던 문제 해결 (collector v0.3.6과 동일 패턴), _do_connect 시 "
            "이전 연결 잔존 리소스 정리. "
            "(2) MC Protocol _receive_response 타임아웃/수신 오류 시 소켓 폐기 + "
            "state=ERROR: 지연 도착 응답이 다음 요청의 응답으로 파싱되어 "
            "엉뚱한 태그에 값이 기록되던 프레임 오정렬 오염 차단. "
            "(3) Modbus TCP Transaction ID 검증(stale 응답 폐기) + "
            "TCP/RTU-over-TCP 타임아웃 시 소켓 폐기 + RTU CRC 오류 시 폐기. "
            "+ High 2건: (H-1) BaseProcessor.process가 metadata['failed'] 검사 → "
            "수집 실패 시 전 태그 quality_code=0 행 생성(전 프로토콜, 통신이상과 "
            "무수집 구분 가능). (H-2) MC _extract_bool 비트 디바이스 분기 — "
            "비트주소 직접 조회로 word_addr 충돌(알람 오발생/미발생) 차단."
        ),
    },
    {
        "version": "0.4.5",
        "date": "2026-05-27",
        "changes": (
            "chore: multi-arch 이미지 (linux/amd64 + linux/arm64) — build_deploy.py "
            "가 buildx multi-platform + --push 로 NCR 에 manifest list 직접 push. "
            "ARM64 전용으로 빌드돼서 ubuntu(amd64) 환경에서 pull 시 fail 하던 이슈 해결. "
            "BLE collector 도 BUILDS 에 통합. 옛 단일 arch + --load 흐름은 --legacy-load 옵션으로 유지. "
            "런타임 코드 변경 없음. (이미지: edge-collector-mc, edge-collector-ble)"
        ),
    },
    {
        "version": "0.4.4",
        "date": "2026-05-26",
        "changes": (
            "chore: tar 출력 제거, NCR 단독 배포로 전환 — build_deploy.py 가 "
            "'--output type=docker,dest=<tar>' 대신 '--load' 를 사용해 로컬 docker daemon "
            "에 직접 이미지 로드. deploy/jem 등에 tar 파일 더 이상 생성하지 않음. "
            "이후 흐름: build_deploy.py → bash scripts/push-to-ncr.sh (NCR 푸시). "
            "런타임 변경 없음, 빌드 파이프라인 단순화 + 디스크 사용 감소."
        ),
    },
    {
        "version": "0.4.3",
        "date": "2026-05-26",
        "changes": (
            "feat: DB 로깅 — docker stdout 은 ERROR 만(기본 stdout_min_level), "
            "DEBUG/INFO/WARNING/ERROR 전부 neuroforge_logs.collector_log 에 비동기 배치 INSERT "
            "(DbLogHandler, asyncpg). LoggingConfig 에 stdout_min_level / db_enabled / "
            "db_min_level / db_batch_size / db_flush_interval_ms / db_queue_max / "
            "subsystem_levels 신규. DSN 우선순위 LOGS_DB_* → CONFIG_DB_* → DB_* 폴백. "
            "DB 다운 시 큐 누적 + 회복 자동 재개, 큐 가득 시 drop+stderr 1회 경고."
        ),
    },
    {
        "version": "0.4.2",
        "date": "2026-05-25",
        "changes": (
            "feat: catalog auto-publish 컨벤션 — 부팅 훅 publish_catalog()가 schema_meta + "
            "collector enum_meta(device_type/protocol_type/data_type 16종/memory 13종 등) 트랜잭션 UPSERT + "
            "catalog_publish_log에 변경 시에만 INSERT(hash 같고 30분 이내면 skip). "
            "실패 시 polling 블로킹(메타 없이 도는 위험 차단) — outer 재시도 루프로 처리. "
            "instance_id=COLLECTOR_KEY, IMAGE_TAG/GIT_SHA env 주입 가능."
        ),
    },
    {
        "version": "0.4.1",
        "date": "2026-05-25",
        "changes": (
            "feat: Cortex 메타 연동 — 부팅 시 neuroforge_config.schema_meta에 "
            "('collector', CONFIG_SCHEMA_VERSION) UPSERT (best-effort). config DB의 "
            "enum_meta/table_naming/constraint_meta(DDL)로 앱 enum·테이블 네이밍·운영 제약 노출."
        ),
    },
    {
        "version": "0.4.0",
        "date": "2026-05-22",
        "changes": (
            "feat: config를 DB(neuroforge_config 스키마)에서 로드하는 경로 추가 "
            "(CONFIG_SOURCE=db). YAML/CSV 대신 vw_collector/vw_device/vw_tag를 읽어 "
            "AppConfig+TagDefinition 구성 — DB 접속은 env(CONFIG_DB_*/DB_* 폴백), "
            "collector 식별은 COLLECTOR_KEY. DB 미접속 시 지수 백오프(최대 30초) "
            "영구 재시도(빈 config로 시작 안 함). PLC는 기존 _parse_tag_row 재사용, "
            "BLE는 load_ble_tags 동일 구성. 기본값 CONFIG_SOURCE=file로 기존 동작 불변."
        ),
    },
    {
        "version": "0.3.6",
        "date": "2026-05-04",
        "changes": (
            "fix: 운영 중 PLC 연결 끊김 시 영구 재연결 보장 — _reconnect_loop을 "
            "collector 수명 동안 항상 실행 (기존: 시작 시점 첫 실패에만 시작 → "
            "운영 중 끊긴 후 영원히 not_connected). 백오프 상한 5초→30초, "
            "재연결 실패 ERROR 'Cannot reconnect to host:port' (60초 throttle), "
            "복구 시 'Reconnected to host:port after N attempt(s)' INFO. "
            "LOSS 로그 throttle (그룹별 첫 실패 + 30초 주기), 복구 시 "
            "'Recovered group=X after N consecutive losses' INFO. "
            "MC Protocol writer NoneType race 가드 (lock 대기 중 transport "
            "정리되어 'NoneType.write' 에러 폭주하던 문제)."
        ),
    },
    {
        "version": "0.3.5",
        "date": "2026-04-22",
        "changes": (
            "fix: POSIOT BLE profile 이중 스케일링 버그 — profile이 이미 ÷100 "
            "변환한 값에 CSV decimals=2가 또 ÷100 적용되어 값이 100배 작게 "
            "저장되던 문제. posiot/posiot_v2 profile을 raw int 반환으로 통일 "
            "(CSV scale/offset/decimals가 단일 스케일링 경로). pressure 특수 "
            "공식도 CSV의 선형 scale/offset으로 이전."
        ),
    },
    {
        "version": "0.3.4",
        "date": "2026-04-22",
        "changes": (
            "BLE 예외 처리 보강 — cache_ttl/duplicate_filter_s 입력 검증 "
            "(음수/NaN/invalid → default), scanner detection_callback 전체 "
            "예외 흡수 + null 가드(device.address/.name), monotonic clock 사용 "
            "(시계 점프 내성), byte_offset 파싱 방어('1:'/'x:y'/'2:0' 등 말포먼드), "
            "BleMultiCollector 설정 오류 경고 (orphan device_id/mac, 태그 없는 "
            "디바이스). on_change 모드 적용은 collector_ble.yaml에서."
        ),
    },
    {
        "version": "0.3.3",
        "date": "2026-03-21",
        "changes": "BLE device_type 지원 + GitHub 미러 설정",
    },
    {
        "version": "0.3.2",
        "date": "2026-03-05",
        "changes": (
            "fix: 모든 비트 디바이스(M/X/Y) 워드 기반 읽기로 전환 — "
            "비트 읽기(0x0001)가 일부 PLC에서 항상 0 반환하는 문제 해결"
        ),
    },
    {
        "version": "0.3.1",
        "date": "2026-03-05",
        "changes": (
            "fix: L 디바이스 워드 기반 비트 읽기 주소 버그 수정 "
            "(워드 인덱스 대신 비트 주소를 PLC에 전송)"
        ),
    },
    {
        "version": "0.3.0",
        "date": "2026-02-27",
        "changes": (
            "MC/Modbus collector 부분 실패 허용 (이미 읽은 데이터 유지), "
            "재연결 지수 백오프 (1s~5s), "
            "Processor 재연결 시 on_change 캐시 초기화, "
            "Modbus coil/discrete 레지스터 + STRING 타입 파싱"
        ),
    },
    {
        "version": "0.2.3-beta",
        "date": "2026-02-09",
        "changes": "MC Protocol VERBOSE 로깅, apply_scaling NaN/Inf 검증",
    },
    {
        "version": "0.2.0-beta",
        "date": "2026-01-15",
        "changes": "태그 설정 확장, 마스터 동기화, 성능 최적화",
    },
    {
        "version": "0.1.0",
        "date": "2025-12-01",
        "changes": "초기 릴리스 (MC Protocol + RabbitMQ)",
    },
]

# =============================================================================
# Module Versions
# =============================================================================
MODULE_VERSIONS: Dict[str, str] = {
    "core": "0.3.1",           # config DB 로딩 + Cortex schema_meta 기록
    "collectors": "0.3.5",     # MC/Modbus 타임아웃 시 소켓 폐기 + Modbus TID 검증
    "processors": "0.3.1",     # 실패데이터 quality=0 라우팅 (H-1)
    "publishers": "0.3.1",     # 재연결 태스크 상시 기동 (운영 중 단절 복구)
    "pipeline": "0.2.0",       # 파이프라인 관리
    "services": "0.2.0",       # 부가 서비스 (Status API 등)
    "utils": "0.3.0",          # DbLogHandler 추가 (neuroforge_logs.collector_log 비동기 배치 INSERT)
}

# =============================================================================
# Protocol Support
# =============================================================================
SUPPORTED_PROTOCOLS: Dict[str, str] = {
    "mc_protocol": "0.3.5",    # 타임아웃 소켓 폐기 + _extract_bool 비트 디바이스 분기
    "modbus": "0.2.1",         # Transaction ID 검증 + 타임아웃/CRC 오류 시 소켓 폐기
}

# =============================================================================
# Publisher Support
# =============================================================================
SUPPORTED_PUBLISHERS: Dict[str, str] = {
    "rabbitmq": "0.2.0",       # RabbitMQ (aio_pika)
}


def get_version_string() -> str:
    """간단한 버전 문자열 반환."""
    return f"{APP_NAME} v{APP_VERSION}"


def get_full_version_info() -> Dict[str, any]:
    """전체 버전 정보 딕셔너리 반환."""
    return {
        "app_name": APP_NAME,
        "app_version": APP_VERSION,
        "build_date": APP_BUILD_DATE,
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "modules": MODULE_VERSIONS.copy(),
        "protocols": SUPPORTED_PROTOCOLS.copy(),
        "publishers": SUPPORTED_PUBLISHERS.copy(),
    }


def get_dependency_versions() -> Dict[str, str]:
    """주요 의존성 버전 반환."""
    deps = {}

    # aio_pika (RabbitMQ)
    try:
        import aio_pika
        deps["aio-pika"] = aio_pika.__version__
    except (ImportError, AttributeError):
        deps["aio-pika"] = "not installed"

    # PyYAML
    try:
        import yaml
        deps["pyyaml"] = yaml.__version__
    except (ImportError, AttributeError):
        deps["pyyaml"] = "not installed"

    return deps


def log_version_info(logger) -> None:
    """버전 정보를 로거에 출력."""
    logger.info(f"{APP_NAME} v{APP_VERSION} (build: {APP_BUILD_DATE})")
    logger.info(f"Python {platform.python_version()} on {platform.system()} {platform.machine()}")

    # 모듈 버전
    logger.info("Modules:")
    for module, version in MODULE_VERSIONS.items():
        logger.info(f"  - {module}: v{version}")

    # 프로토콜 지원
    logger.info("Supported Protocols:")
    for protocol, version in SUPPORTED_PROTOCOLS.items():
        logger.info(f"  - {protocol}: v{version}")

    # Publisher 지원
    logger.info("Supported Publishers:")
    for publisher, version in SUPPORTED_PUBLISHERS.items():
        logger.info(f"  - {publisher}: v{version}")

    # 의존성 버전
    deps = get_dependency_versions()
    logger.info("Dependencies:")
    for dep, version in deps.items():
        logger.info(f"  - {dep}: {version}")
