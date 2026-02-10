"""
OPC UA Client Collector
=======================

OPC UA 프로토콜을 통한 데이터 수집기 구현.

Features:
    - OPC UA 표준 준수 (IEC 62541)
    - 다양한 보안 정책 지원 (None, Basic256, Basic256Sha256)
    - 인증서 기반 보안 채널
    - 사용자 인증 (Anonymous, Username/Password, Certificate)
    - 노드 브라우징 및 구독
    - 자동 재연결

Security Policies:
    - None: 암호화 없음 (테스트/개발 환경용)
    - Basic256: AES-256 암호화, RSA-SHA1 서명 (레거시)
    - Basic256Sha256: AES-256 암호화, RSA-SHA256 서명 (권장)
    - Aes128Sha256RsaOaep: AES-128 암호화, RSA-SHA256 서명
    - Aes256Sha256RsaPss: AES-256 암호화, RSA-PSS 서명 (최신)

Security Modes:
    - None: 보안 없음
    - Sign: 서명만 (무결성 검증)
    - SignAndEncrypt: 서명 + 암호화 (기밀성 + 무결성)

User Authentication:
    - Anonymous: 인증 없음
    - Username/Password: 사용자명/비밀번호 인증
    - Certificate: X.509 인증서 기반 인증

Dependencies:
    - asyncua 라이브러리 (pip install asyncua)
    - cryptography 라이브러리 (인증서 생성)

Example:
    collector = OpcuaCollector(
        plc_id=1,
        name="OPC_UA_Server",
        config=config,
        event_bus=event_bus,
    )

    await collector.start()
"""

import asyncio
import struct
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import logging

from asyncua import Client, ua
from asyncua.common.node import Node
from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256
from asyncua.ua import UaStatusCodeError

from ...collectors.base import BaseCollector
from ...core.interfaces import CollectedData, TagDefinition, DataType, ConnectionState
from ...core.config import CollectorConfig
from ...core.events import EventBus
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_collection_logger()


class SecurityPolicy:
    """OPC UA 보안 정책 상수."""
    NONE = "None"
    BASIC256 = "Basic256"
    BASIC256_SHA256 = "Basic256Sha256"
    AES128_SHA256_RSAOAEP = "Aes128Sha256RsaOaep"
    AES256_SHA256_RSAPSS = "Aes256Sha256RsaPss"


class SecurityMode:
    """OPC UA 보안 모드 상수."""
    NONE = "None"
    SIGN = "Sign"
    SIGN_AND_ENCRYPT = "SignAndEncrypt"


class AuthenticationType:
    """OPC UA 인증 타입 상수."""
    ANONYMOUS = "Anonymous"
    USERNAME = "Username"
    CERTIFICATE = "Certificate"


class OpcuaCollector(BaseCollector):
    """
    OPC UA 데이터 수집기.

    asyncua 라이브러리를 사용하여 OPC UA 통신을 수행합니다.
    다양한 보안 정책과 인증 방식을 지원합니다.

    Configuration Example (YAML):
        protocol:
          type: opcua
          host: "192.168.1.100"
          port: 4840
          timeout_ms: 5000
          extra:
            # 보안 설정
            security_policy: "Basic256Sha256"   # None, Basic256, Basic256Sha256
            security_mode: "SignAndEncrypt"      # None, Sign, SignAndEncrypt

            # 인증 설정
            authentication: "Username"           # Anonymous, Username, Certificate
            username: "admin"                    # Username 인증 시
            password: "${OPC_PASSWORD}"          # 환경변수 참조 가능

            # 인증서 설정 (보안 정책 사용 시 필수)
            certificate_path: "/path/to/client_cert.pem"
            private_key_path: "/path/to/client_key.pem"
            server_certificate_path: "/path/to/server_cert.pem"  # 선택

            # 고급 설정
            application_uri: "urn:my:application"
            namespace_uri: "http://example.com/ns"
            session_timeout_ms: 30000
            subscription_interval_ms: 1000

    Attributes:
        _client: OPC UA 클라이언트
        _subscription: 구독 객체
        _node_cache: 노드 ID 캐시 (성능 최적화)
        _consecutive_failures: 연속 실패 횟수
    """

    # 데이터 타입별 OPC UA 타입 매핑
    UA_TYPE_MAP = {
        DataType.BOOL: ua.VariantType.Boolean,
        DataType.INT16: ua.VariantType.Int16,
        DataType.UINT16: ua.VariantType.UInt16,
        DataType.INT32: ua.VariantType.Int32,
        DataType.UINT32: ua.VariantType.UInt32,
        DataType.FLOAT32: ua.VariantType.Float,
        DataType.FLOAT64: ua.VariantType.Double,
        DataType.STRING: ua.VariantType.String,
    }

    # 보안 정책 URI 매핑
    SECURITY_POLICY_URI = {
        SecurityPolicy.NONE: "http://opcfoundation.org/UA/SecurityPolicy#None",
        SecurityPolicy.BASIC256: "http://opcfoundation.org/UA/SecurityPolicy#Basic256",
        SecurityPolicy.BASIC256_SHA256: "http://opcfoundation.org/UA/SecurityPolicy#Basic256Sha256",
        SecurityPolicy.AES128_SHA256_RSAOAEP: "http://opcfoundation.org/UA/SecurityPolicy#Aes128_Sha256_RsaOaep",
        SecurityPolicy.AES256_SHA256_RSAPSS: "http://opcfoundation.org/UA/SecurityPolicy#Aes256_Sha256_RsaPss",
    }

    def __init__(
        self,
        plc_id: int,
        name: str,
        config: CollectorConfig,
        event_bus: Optional[EventBus] = None,
    ):
        """
        Args:
            plc_id: PLC 고유 식별자
            name: 수집기 이름
            config: 수집기 설정
            event_bus: 이벤트 버스
        """
        super().__init__(plc_id, name, config, event_bus)

        self._client: Optional[Client] = None
        self._subscription = None
        self._node_cache: Dict[str, Node] = {}
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5
        self._connection_lock = asyncio.Lock()

        # 프로토콜 설정 추출
        if self._protocol_config:
            self._host = self._protocol_config.host
            self._port = self._protocol_config.port
            self._timeout = self._protocol_config.timeout_ms / 1000.0

            # extra에서 OPC UA 고급 설정 추출
            extra = self._protocol_config.extra or {}

            # 보안 설정
            self._security_policy = extra.get('security_policy', SecurityPolicy.NONE)
            self._security_mode = extra.get('security_mode', SecurityMode.NONE)

            # 인증 설정
            self._authentication = extra.get('authentication', AuthenticationType.ANONYMOUS)
            self._username = extra.get('username', '')
            self._password = extra.get('password', '')

            # 인증서 경로
            self._certificate_path = extra.get('certificate_path', '')
            self._private_key_path = extra.get('private_key_path', '')
            self._server_certificate_path = extra.get('server_certificate_path', '')

            # 고급 설정
            self._application_uri = extra.get(
                'application_uri',
                f"urn:simpleCollector:client:{name}"
            )
            self._namespace_uri = extra.get('namespace_uri', '')
            self._session_timeout = extra.get('session_timeout_ms', 30000)
            self._subscription_interval = extra.get('subscription_interval_ms', 1000)
        else:
            # 기본값
            self._host = "127.0.0.1"
            self._port = 4840
            self._timeout = 5.0
            self._security_policy = SecurityPolicy.NONE
            self._security_mode = SecurityMode.NONE
            self._authentication = AuthenticationType.ANONYMOUS
            self._username = ''
            self._password = ''
            self._certificate_path = ''
            self._private_key_path = ''
            self._server_certificate_path = ''
            self._application_uri = f"urn:simpleCollector:client:{name}"
            self._namespace_uri = ''
            self._session_timeout = 30000
            self._subscription_interval = 1000

        # Endpoint URL 생성
        self._endpoint_url = f"opc.tcp://{self._host}:{self._port}"

    def register_tags(self, group: str, tags: List[TagDefinition]) -> None:
        """
        태그 등록.

        OPC UA 노드 ID 형식을 사용합니다.
        예: "ns=2;s=Temperature", "ns=2;i=1001"

        Args:
            group: 수집 그룹명
            tags: 태그 정의 리스트
        """
        super().register_tags(group, tags)

        logger.info(
            f"[{self._name}] Registered {len(tags)} tags for group '{group}'"
        )

    # =========================================================================
    # Security & Certificate Management
    # =========================================================================

    def _validate_security_config(self) -> Tuple[bool, str]:
        """
        보안 설정 유효성 검증.

        Returns:
            (유효 여부, 오류 메시지) 튜플
        """
        # 보안 정책이 None이 아닌 경우 인증서 필요
        if self._security_policy != SecurityPolicy.NONE:
            if not self._certificate_path:
                return False, "Certificate path required for security policy"
            if not self._private_key_path:
                return False, "Private key path required for security policy"

            # 파일 존재 확인
            if not Path(self._certificate_path).exists():
                return False, f"Certificate file not found: {self._certificate_path}"
            if not Path(self._private_key_path).exists():
                return False, f"Private key file not found: {self._private_key_path}"

        # Certificate 인증 시 추가 검증
        if self._authentication == AuthenticationType.CERTIFICATE:
            if not self._certificate_path or not self._private_key_path:
                return False, "Certificate authentication requires certificate and private key"

        # Username 인증 시 사용자명/비밀번호 필요
        if self._authentication == AuthenticationType.USERNAME:
            if not self._username:
                return False, "Username required for Username authentication"

        return True, ""

    async def _setup_security(self) -> bool:
        """
        보안 채널 설정.

        Returns:
            설정 성공 여부
        """
        if self._security_policy == SecurityPolicy.NONE:
            logger.info(f"[{self._name}] No security policy configured")
            return True

        try:
            # 보안 정책 URI 가져오기
            policy_uri = self.SECURITY_POLICY_URI.get(self._security_policy)
            if not policy_uri:
                logger.error(f"[{self._name}] Unknown security policy: {self._security_policy}")
                return False

            # 보안 모드 매핑
            mode_map = {
                SecurityMode.NONE: ua.MessageSecurityMode.None_,
                SecurityMode.SIGN: ua.MessageSecurityMode.Sign,
                SecurityMode.SIGN_AND_ENCRYPT: ua.MessageSecurityMode.SignAndEncrypt,
            }
            security_mode = mode_map.get(
                self._security_mode,
                ua.MessageSecurityMode.SignAndEncrypt
            )

            # 인증서 및 개인키 로드
            cert_path = Path(self._certificate_path)
            key_path = Path(self._private_key_path)

            # 서버 인증서 (선택)
            server_cert = None
            if self._server_certificate_path:
                server_cert_path = Path(self._server_certificate_path)
                if server_cert_path.exists():
                    server_cert = server_cert_path.read_bytes()

            # 보안 설정 적용
            await self._client.set_security(
                policy=policy_uri,
                certificate=str(cert_path),
                private_key=str(key_path),
                server_certificate=server_cert,
                mode=security_mode,
            )

            logger.info(
                f"[{self._name}] Security configured: "
                f"policy={self._security_policy}, mode={self._security_mode}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Security setup failed: {e}")
            return False

    async def _setup_authentication(self) -> bool:
        """
        사용자 인증 설정.

        Returns:
            설정 성공 여부
        """
        try:
            if self._authentication == AuthenticationType.ANONYMOUS:
                logger.info(f"[{self._name}] Using anonymous authentication")
                return True

            elif self._authentication == AuthenticationType.USERNAME:
                self._client.set_user(self._username)
                self._client.set_password(self._password)
                logger.info(
                    f"[{self._name}] Using username authentication: {self._username}"
                )
                return True

            elif self._authentication == AuthenticationType.CERTIFICATE:
                # 인증서 기반 인증은 보안 설정에서 처리됨
                logger.info(f"[{self._name}] Using certificate authentication")
                return True

            else:
                logger.warning(
                    f"[{self._name}] Unknown authentication type: {self._authentication}"
                )
                return False

        except Exception as e:
            logger.error(f"[{self._name}] Authentication setup failed: {e}")
            return False

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        OPC UA 연결.

        Returns:
            연결 성공 여부
        """
        async with self._connection_lock:
            try:
                # 보안 설정 검증
                valid, error_msg = self._validate_security_config()
                if not valid:
                    logger.error(f"[{self._name}] Invalid security config: {error_msg}")
                    return False

                # 기존 연결 정리
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception as e:
                        logger.warning(f"[{self._name}] Error during disconnect: {e}")
                    self._client = None

                # 새 클라이언트 생성
                self._client = Client(
                    url=self._endpoint_url,
                    timeout=self._timeout,
                )

                # Application URI 설정
                self._client.application_uri = self._application_uri

                # 보안 설정
                if not await self._setup_security():
                    return False

                # 인증 설정
                if not await self._setup_authentication():
                    return False

                # 연결 시도
                await self._client.connect()

                # 연결 성공
                self._consecutive_failures = 0
                self._node_cache.clear()  # 노드 캐시 초기화

                logger.info(
                    f"[{self._name}] Connected to OPC UA server: {self._endpoint_url}"
                )

                # 서버 정보 로깅
                await self._log_server_info()

                return True

            except UaStatusCodeError as e:
                logger.error(
                    f"[{self._name}] OPC UA status error: {e.code.name} - {e}"
                )
                return False
            except Exception as e:
                logger.error(f"[{self._name}] Connection error: {e}")
                return False

    async def _log_server_info(self) -> None:
        """서버 정보 로깅."""
        try:
            # 서버 상태 노드 읽기
            server_node = self._client.nodes.server
            server_status = await server_node.read_value()

            logger.info(
                f"[{self._name}] Server status: {server_status}"
            )

            # 네임스페이스 배열 읽기
            ns_array = await self._client.get_namespace_array()
            logger.debug(f"[{self._name}] Namespace array: {ns_array}")

        except Exception as e:
            logger.debug(f"[{self._name}] Could not read server info: {e}")

    async def _do_disconnect(self) -> None:
        """연결 해제."""
        async with self._connection_lock:
            # 구독 해제
            if self._subscription:
                try:
                    await self._subscription.delete()
                except Exception as e:
                    logger.debug(f"[{self._name}] Subscription delete error: {e}")
                self._subscription = None

            # 클라이언트 연결 해제
            if self._client:
                try:
                    await self._client.disconnect()
                except Exception as e:
                    logger.warning(f"[{self._name}] Disconnect error: {e}")
                finally:
                    self._client = None
                    self._node_cache.clear()

    async def _do_health_check(self) -> bool:
        """
        연결 상태 확인.

        서버 상태 노드를 읽어 연결 상태를 확인합니다.

        Returns:
            연결 정상 여부
        """
        if not self._client:
            return False

        try:
            # 서버 상태 노드 읽기
            server_state = self._client.nodes.server_state
            state = await asyncio.wait_for(
                server_state.read_value(),
                timeout=2.0
            )
            return state is not None
        except Exception as e:
            logger.warning(f"[{self._name}] Health check failed: {e}")
            return False

    async def _ensure_connection(self) -> bool:
        """
        연결 상태 확인 및 필요시 재연결.

        Returns:
            연결 사용 가능 여부
        """
        if self._client:
            try:
                # 간단한 연결 테스트
                await self._client.check_connection()
                return True
            except Exception as e:
                logger.verbose(f"[{self._name}] Connection test failed: {e}")

        logger.warning(f"[{self._name}] Connection lost, attempting reconnect...")
        self._state = ConnectionState.RECONNECTING

        success = await self._do_connect()
        if success:
            self._state = ConnectionState.CONNECTED
        else:
            self._state = ConnectionState.ERROR

        return success

    # =========================================================================
    # Node Operations
    # =========================================================================

    async def _get_node(self, node_id: str) -> Optional[Node]:
        """
        노드 ID로 노드 객체 획득 (캐싱).

        Args:
            node_id: 노드 ID 문자열 (예: "ns=2;s=Temperature")

        Returns:
            노드 객체, 실패 시 None
        """
        if node_id in self._node_cache:
            return self._node_cache[node_id]

        try:
            node = self._client.get_node(node_id)
            self._node_cache[node_id] = node
            return node
        except Exception as e:
            logger.error(f"[{self._name}] Failed to get node {node_id}: {e}")
            return None

    async def _read_node_value(self, node: Node) -> Tuple[Any, int]:
        """
        노드 값 읽기.

        Args:
            node: 노드 객체

        Returns:
            (값, 품질코드) 튜플
        """
        try:
            data_value = await node.read_data_value()

            # 품질 코드 확인
            status_code = data_value.StatusCode
            if status_code.is_good():
                quality_code = 192  # GOOD
            elif status_code.is_uncertain():
                quality_code = 64   # UNCERTAIN
            else:
                quality_code = 0    # BAD
                logger.warning(
                    f"[{self._name}] Bad quality for node {node}: {status_code}"
                )

            return data_value.Value.Value, quality_code

        except UaStatusCodeError as e:
            logger.error(f"[{self._name}] Read error for node {node}: {e.code.name}")
            return None, 0
        except Exception as e:
            logger.error(f"[{self._name}] Read error for node {node}: {e}")
            return None, 0

    async def _read_multiple_nodes(
        self,
        nodes: List[Node]
    ) -> List[Tuple[Any, int]]:
        """
        여러 노드 일괄 읽기 (최적화).

        Args:
            nodes: 노드 객체 리스트

        Returns:
            [(값, 품질코드), ...] 리스트
        """
        try:
            # asyncua의 read_values 사용 (일괄 읽기)
            data_values = await self._client.read_values(nodes)

            results = []
            for dv in data_values:
                if dv.StatusCode.is_good():
                    results.append((dv.Value.Value, 192))
                elif dv.StatusCode.is_uncertain():
                    results.append((dv.Value.Value, 64))
                else:
                    results.append((None, 0))

            return results

        except Exception as e:
            logger.error(f"[{self._name}] Batch read error: {e}")
            # 모두 실패로 처리
            return [(None, 0)] * len(nodes)

    # =========================================================================
    # Browsing (노드 탐색)
    # =========================================================================

    async def browse_nodes(
        self,
        start_node_id: str = "i=85",  # Objects 폴더
        max_depth: int = 3
    ) -> List[Dict[str, Any]]:
        """
        노드 트리 탐색.

        Args:
            start_node_id: 시작 노드 ID (기본: Objects 폴더)
            max_depth: 최대 탐색 깊이

        Returns:
            노드 정보 리스트
        """
        if not self._client:
            logger.error(f"[{self._name}] Not connected, cannot browse")
            return []

        try:
            start_node = self._client.get_node(start_node_id)
            return await self._browse_recursive(start_node, 0, max_depth)
        except Exception as e:
            logger.error(f"[{self._name}] Browse error: {e}")
            return []

    async def _browse_recursive(
        self,
        node: Node,
        depth: int,
        max_depth: int
    ) -> List[Dict[str, Any]]:
        """
        재귀적 노드 탐색.

        Args:
            node: 현재 노드
            depth: 현재 깊이
            max_depth: 최대 깊이

        Returns:
            노드 정보 리스트
        """
        if depth >= max_depth:
            return []

        results = []
        try:
            browse_name = await node.read_browse_name()
            node_class = await node.read_node_class()

            node_info = {
                'node_id': node.nodeid.to_string(),
                'browse_name': browse_name.Name,
                'node_class': node_class.name,
                'depth': depth,
            }

            # Variable 노드인 경우 값 읽기 시도
            if node_class == ua.NodeClass.Variable:
                try:
                    value = await node.read_value()
                    data_type = await node.read_data_type()
                    node_info['value'] = str(value)
                    node_info['data_type'] = data_type.to_string()
                except Exception as e:
                    logger.verbose(f"[{self._name}] Could not read node value: {e}")

            results.append(node_info)

            # 자식 노드 탐색
            children = await node.get_children()
            for child in children:
                child_results = await self._browse_recursive(child, depth + 1, max_depth)
                results.extend(child_results)

        except Exception as e:
            logger.debug(f"[{self._name}] Browse node error: {e}")

        return results

    # =========================================================================
    # Data Collection
    # =========================================================================

    async def _do_collect(self, group: str) -> Optional[CollectedData]:
        """
        OPC UA 데이터 수집.

        Args:
            group: 수집 그룹명

        Returns:
            수집된 데이터
        """
        tags = self._tags.get(group, [])
        if not tags:
            logger.warning(f"[{self._name}] No tags for group '{group}'")
            return None

        # 연결 확인
        if not await self._ensure_connection():
            self._consecutive_failures += 1
            return None

        source_time = datetime.now()
        values: Dict[int, Tuple[Any, int]] = {}  # tag_id -> (value, quality_code)

        try:
            # 노드 객체 리스트 생성
            nodes: List[Node] = []
            tag_indices: List[int] = []  # 노드 인덱스 -> 태그 인덱스 매핑

            for i, tag in enumerate(tags):
                node = await self._get_node(tag.address)
                if node:
                    nodes.append(node)
                    tag_indices.append(i)
                else:
                    # 노드 획득 실패 시 해당 태그는 실패로 처리
                    values[tag.tag_id] = (None, 0)

            # 일괄 읽기
            if nodes:
                read_results = await self._read_multiple_nodes(nodes)

                for idx, (value, quality) in zip(tag_indices, read_results):
                    tag = tags[idx]
                    values[tag.tag_id] = (value, quality)

            # 성공
            self._consecutive_failures = 0

            return CollectedData(
                source_time=source_time,
                collection_time=datetime.now(),
                plc_id=self._plc_id,
                raw_data=b'',
                collection_group=group,
                metadata={
                    'values': values,
                    'tags': tags,
                    'protocol': 'opcua',
                },
            )

        except UaStatusCodeError as e:
            logger.error(f"[{self._name}] OPC UA error: {e.code.name}")
            self._consecutive_failures += 1
            if self._consecutive_failures >= self._max_consecutive_failures:
                self._state = ConnectionState.ERROR
            return None

        except Exception as e:
            logger.error(f"[{self._name}] Collection error: {e}")
            self._consecutive_failures += 1
            return None

    # =========================================================================
    # Subscription (구독 기반 수집)
    # =========================================================================

    async def create_subscription(
        self,
        tags: List[TagDefinition],
        callback: Optional[callable] = None
    ) -> bool:
        """
        데이터 변경 구독 생성.

        폴링 대신 서버에서 값 변경 시 알림을 받습니다.

        Args:
            tags: 구독할 태그 리스트
            callback: 데이터 변경 콜백 (None이면 기본 핸들러 사용)

        Returns:
            구독 생성 성공 여부
        """
        if not self._client:
            logger.error(f"[{self._name}] Not connected, cannot create subscription")
            return False

        try:
            # 기존 구독 해제
            if self._subscription:
                await self._subscription.delete()

            # 구독 핸들러
            handler = callback or self._default_subscription_handler

            # 구독 생성
            self._subscription = await self._client.create_subscription(
                period=self._subscription_interval,
                handler=handler,
            )

            # 노드 모니터링 아이템 추가
            nodes = []
            for tag in tags:
                node = await self._get_node(tag.address)
                if node:
                    nodes.append(node)

            if nodes:
                await self._subscription.subscribe_data_change(nodes)
                logger.info(
                    f"[{self._name}] Subscription created for {len(nodes)} nodes"
                )
                return True
            else:
                logger.warning(f"[{self._name}] No valid nodes for subscription")
                return False

        except Exception as e:
            logger.error(f"[{self._name}] Subscription creation failed: {e}")
            return False

    def _default_subscription_handler(self, node: Node, value: Any, data: Any):
        """
        기본 구독 핸들러.

        Args:
            node: 변경된 노드
            value: 새 값
            data: 추가 데이터
        """
        logger.debug(
            f"[{self._name}] Data change: {node.nodeid} = {value}"
        )

    # =========================================================================
    # Certificate Generation Utility
    # =========================================================================

    @staticmethod
    def generate_self_signed_certificate(
        output_dir: str,
        common_name: str = "SimpleCollector OPC UA Client",
        organization: str = "NEUROSENSE",
        country: str = "KR",
        validity_days: int = 365 * 5,
    ) -> Tuple[str, str]:
        """
        자체 서명 인증서 생성 (개발/테스트용).

        Args:
            output_dir: 출력 디렉토리
            common_name: 인증서 CN
            organization: 조직명
            country: 국가 코드
            validity_days: 유효 기간 (일)

        Returns:
            (인증서 경로, 개인키 경로) 튜플
        """
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        from datetime import timedelta

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # RSA 키 쌍 생성
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        # 인증서 생성
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, country),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, organization),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(private_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=validity_days))
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None),
                critical=True,
            )
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.UniformResourceIdentifier(
                        f"urn:simpleCollector:client:{common_name.replace(' ', '_')}"
                    ),
                ]),
                critical=False,
            )
            .sign(private_key, hashes.SHA256())
        )

        # 파일 저장
        cert_path = output_path / "client_cert.pem"
        key_path = output_path / "client_key.pem"

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        with open(key_path, "wb") as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))

        return str(cert_path), str(key_path)

    # =========================================================================
    # Discovery (서버 탐색)
    # =========================================================================

    @staticmethod
    async def discover_servers(discovery_url: str) -> List[Dict[str, Any]]:
        """
        OPC UA 서버 탐색.

        Args:
            discovery_url: Discovery 서버 URL

        Returns:
            발견된 서버 정보 리스트
        """
        try:
            client = Client(url=discovery_url)
            servers = await client.connect_and_find_servers()

            result = []
            for server in servers:
                result.append({
                    'application_uri': server.ApplicationUri,
                    'product_uri': server.ProductUri,
                    'application_name': server.ApplicationName.Text,
                    'application_type': server.ApplicationType.name,
                    'discovery_urls': list(server.DiscoveryUrls or []),
                })

            return result

        except Exception as e:
            logger.error(f"Discovery failed: {e}")
            return []

    @staticmethod
    async def get_endpoints(server_url: str) -> List[Dict[str, Any]]:
        """
        서버 엔드포인트 조회.

        Args:
            server_url: 서버 URL

        Returns:
            엔드포인트 정보 리스트
        """
        try:
            client = Client(url=server_url)
            endpoints = await client.connect_and_get_server_endpoints()

            result = []
            for ep in endpoints:
                result.append({
                    'endpoint_url': ep.EndpointUrl,
                    'security_policy_uri': ep.SecurityPolicyUri,
                    'security_mode': ep.SecurityMode.name,
                    'transport_profile_uri': ep.TransportProfileUri,
                    'security_level': ep.SecurityLevel,
                })

            return result

        except Exception as e:
            logger.error(f"Get endpoints failed: {e}")
            return []
