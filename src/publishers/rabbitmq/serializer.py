"""
RabbitMQ 메시지 직렬화 유틸리티
================================

ProcessedData 배치를 JSON → 압축 → 암호화 하여 RabbitMQ 메시지로 변환합니다.

Pipeline:
    Serialize:  List[dict] → JSON bytes → compress → encrypt → bytes
    Deserialize: bytes → decrypt → decompress → JSON bytes → List[dict]

Compression:
    - zlib (기본): 빠르고 효율적, 소~중규모 JSON에 최적
    - gzip: zlib과 유사하나 헤더 포함
    - none: 압축 없음

Encryption:
    - Fernet (AES-128-CBC + HMAC): cryptography 패키지 사용
    - 키는 base64 인코딩된 32바이트 (Fernet.generate_key()로 생성)
"""

import json
import zlib
import gzip
from typing import Any, Dict, List, Optional


# Lazy import for encryption
try:
    from cryptography.fernet import Fernet
    FERNET_AVAILABLE = True
except ImportError:
    FERNET_AVAILABLE = False


class MessageSerializer:
    """
    RabbitMQ 메시지 직렬화/역직렬화.

    ProcessedData.to_dict() 리스트를 압축+암호화된 바이트로 변환합니다.

    Attributes:
        _compression: 압축 방식 ('zlib', 'gzip', 'none')
        _encryption_enabled: 암호화 활성화 여부
        _fernet: Fernet 암호화 인스턴스
    """

    def __init__(
        self,
        compression: str = "zlib",
        encryption_enabled: bool = False,
        encryption_key: str = "",
    ):
        """
        Args:
            compression: 압축 방식 ('zlib', 'gzip', 'none')
            encryption_enabled: 암호화 활성화 여부
            encryption_key: Fernet 암호화 키 (base64 인코딩)
        """
        self._compression = compression
        self._encryption_enabled = encryption_enabled
        self._fernet: Optional[Any] = None

        if encryption_enabled:
            if not FERNET_AVAILABLE:
                raise ImportError(
                    "cryptography required for encryption. "
                    "Install with: pip install cryptography"
                )
            if not encryption_key:
                raise ValueError("encryption_key is required when encryption is enabled")
            self._fernet = Fernet(encryption_key.encode())

    def serialize(self, data: List[Dict[str, Any]]) -> bytes:
        """
        데이터 직렬화: List[dict] → compressed+encrypted bytes.

        Args:
            data: ProcessedData.to_dict() 리스트

        Returns:
            직렬화된 바이트
        """
        # Step 1: JSON 인코딩
        json_bytes = json.dumps(
            data, ensure_ascii=False, separators=(',', ':')
        ).encode('utf-8')

        # Step 2: 압축
        compressed = self._compress(json_bytes)

        # Step 3: 암호화 (선택)
        if self._fernet:
            compressed = self._fernet.encrypt(compressed)

        return compressed

    def deserialize(self, data: bytes) -> List[Dict[str, Any]]:
        """
        데이터 역직렬화: compressed+encrypted bytes → List[dict].

        Args:
            data: 직렬화된 바이트

        Returns:
            ProcessedData dict 리스트
        """
        # Step 1: 복호화 (선택)
        if self._fernet:
            data = self._fernet.decrypt(data)

        # Step 2: 압축 해제
        decompressed = self._decompress(data)

        # Step 3: JSON 디코딩
        return json.loads(decompressed.decode('utf-8'))

    def _compress(self, data: bytes) -> bytes:
        """데이터 압축."""
        if self._compression == "zlib":
            return zlib.compress(data, level=6)
        elif self._compression == "gzip":
            return gzip.compress(data, compresslevel=6)
        return data

    def _decompress(self, data: bytes) -> bytes:
        """데이터 압축 해제."""
        if self._compression == "zlib":
            return zlib.decompress(data)
        elif self._compression == "gzip":
            return gzip.decompress(data)
        return data
