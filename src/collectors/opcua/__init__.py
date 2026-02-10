"""
OPC UA Client Collector Module
==============================

OPC UA 서버에서 데이터를 수집하는 클라이언트 모듈입니다.

Features:
    - OPC UA 표준 준수
    - 다양한 보안 정책 지원 (None, Basic256, Basic256Sha256)
    - 인증서 기반 보안 채널
    - 사용자 인증 (Anonymous, Username/Password, Certificate)
    - 노드 브라우징 및 구독

Security Policies:
    - None: 암호화 없음 (테스트용)
    - Basic256: AES-256 암호화, RSA-SHA1 서명
    - Basic256Sha256: AES-256 암호화, RSA-SHA256 서명 (권장)

Dependencies:
    - asyncua 라이브러리

Example:
    collector = OpcuaCollector(
        plc_id=1,
        name="OPC_UA_Server",
        config=config,
        event_bus=event_bus,
    )

    await collector.start()
"""

from .collector import (
    OpcuaCollector,
    SecurityPolicy,
    SecurityMode,
    AuthenticationType,
)
from .processor import OpcuaProcessor, OpcuaArrayProcessor

__all__ = [
    "OpcuaCollector",
    "OpcuaProcessor",
    "OpcuaArrayProcessor",
    "SecurityPolicy",
    "SecurityMode",
    "AuthenticationType",
]
