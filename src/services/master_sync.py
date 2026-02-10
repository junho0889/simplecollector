"""
마스터 테이블 동기화 서비스
===========================

수집기 시작 시 로컬 설정(YAML/CSV)을 데이터베이스 마스터 테이블과 동기화합니다.
전체 교체 방식 (DELETE + INSERT) 사용.

대상 테이블:
    - {schema}.plc_master: PLC 연결 정보
    - {schema}.tag_master: 태그 정의
    - {schema}.event_master: 이벤트/알람 정의
    - {schema}.quality_master: 데이터 품질 코드
    - {schema}.collection_group_master: 수집 그룹 정의

Features:
    - 전체 교체 방식 (해당 PLC 데이터 DELETE 후 INSERT)
    - CSV 파일에서 이벤트 마스터 로드
    - 기본 데이터 품질 코드 제공
    - 그룹별 테이블 매핑 지원

Usage:
    sync_service = MasterSyncService(db_config, schema='master')
    if await sync_service.connect():
        events = load_events_from_csv('config/events.csv')
        results = await sync_service.sync_all(
            collector_config, tags, events
        )
        await sync_service.disconnect()
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False

from ..core.interfaces import TagDefinition, DataType
from ..core.config import CollectorConfig, DatabaseConfig

logger = logging.getLogger(__name__)


class MasterSyncService:
    """
    마스터 테이블 동기화 서비스.

    수집기 설정을 데이터베이스 마스터 테이블과 동기화합니다.

    Tables:
        - {schema}.plc_master: PLC 메타데이터
        - {schema}.tag_master: 태그 정의

    Attributes:
        _db_config: 데이터베이스 설정
        _schema: 대상 스키마 이름
        _pool: 커넥션 풀
    """

    def __init__(
        self,
        db_config: DatabaseConfig,
        schema: str = 'test',
    ):
        """
        Args:
            db_config: 데이터베이스 설정
            schema: 마스터 테이블 스키마 (기본: 'test')
        """
        self._db_config = db_config
        self._schema = schema
        self._pool: Optional[asyncpg.Pool] = None

        self._dsn = (
            f"postgresql://{db_config.user}:{db_config.password}"
            f"@{db_config.host}:{db_config.port}/{db_config.database}"
        )

    async def connect(self) -> bool:
        """
        데이터베이스 연결.

        Returns:
            연결 성공 여부
        """
        if not ASYNCPG_AVAILABLE:
            logger.warning("asyncpg not available, master sync disabled")
            return False

        try:
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=1,
                max_size=3,
                command_timeout=30,
            )
            logger.info(f"MasterSyncService connected to {self._db_config.host}")
            return True
        except Exception as e:
            logger.error(f"MasterSyncService connection failed: {e}")
            return False

    async def disconnect(self) -> None:
        """데이터베이스 연결 해제."""
        if self._pool:
            await self._pool.close()
            self._pool = None
            logger.info("MasterSyncService disconnected")

    async def sync_plc(
        self,
        collector_config: CollectorConfig,
        site: str = '',
        line: str = '',
        vendor: str = '',
    ) -> bool:
        """
        PLC 정보를 plc_master 테이블과 동기화.

        UPSERT: plc_id 기준으로 INSERT 또는 UPDATE.

        Args:
            collector_config: 수집기 설정
            site: 사이트명
            line: 라인명
            vendor: 제조사명

        Returns:
            동기화 성공 여부
        """
        if not self._pool:
            return False

        protocol = collector_config.protocol
        protocol_name = protocol.type if protocol else 'unknown'

        # 연결 설정을 JSON으로 저장
        conn_config: Dict[str, Any] = {}
        if protocol:
            conn_config = {
                'host': protocol.host,
                'port': protocol.port,
                'unit_id': protocol.unit_id,
                'timeout_ms': protocol.timeout_ms,
            }
            if protocol.extra:
                conn_config.update(protocol.extra)

        upsert_sql = f"""
            INSERT INTO {self._schema}.plc_master (
                plc_id, name, site, line, vendor, protocol,
                timezone, is_active, conn_config
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (plc_id) DO UPDATE SET
                name = EXCLUDED.name,
                site = EXCLUDED.site,
                line = EXCLUDED.line,
                vendor = EXCLUDED.vendor,
                protocol = EXCLUDED.protocol,
                conn_config = EXCLUDED.conn_config,
                is_active = EXCLUDED.is_active
        """

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    upsert_sql,
                    collector_config.plc_id,
                    collector_config.name,
                    site,
                    line,
                    vendor,
                    protocol_name,
                    'Asia/Seoul',
                    collector_config.enabled,
                    json.dumps(conn_config),
                )
            logger.info(
                f"Synced PLC {collector_config.plc_id} ({collector_config.name}) "
                f"to {self._schema}.plc_master"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to sync PLC master: {e}")
            return False

    async def sync_tags(
        self,
        plc_id: int,
        tags: List[TagDefinition],
    ) -> int:
        """
        태그 정의를 tag_master 테이블과 동기화.

        UPSERT: (plc_id, address) 기준으로 INSERT 또는 UPDATE.

        Args:
            plc_id: PLC ID
            tags: 태그 정의 리스트

        Returns:
            동기화된 태그 수
        """
        if not self._pool or not tags:
            return 0

        upsert_sql = f"""
            INSERT INTO {self._schema}.tag_master (
                tag_id, plc_id, tag_name, address, address_meta, data_type, byte_size,
                collection_group, scale, "offset", decimals, string_length,
                word_length, bool_true_value, bool_false_value, bool_invert,
                unit, description, is_active, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17, $18, $19, $20
            )
            ON CONFLICT (plc_id, address) DO UPDATE SET
                tag_id = EXCLUDED.tag_id,
                tag_name = EXCLUDED.tag_name,
                address_meta = EXCLUDED.address_meta,
                data_type = EXCLUDED.data_type,
                byte_size = EXCLUDED.byte_size,
                collection_group = EXCLUDED.collection_group,
                scale = EXCLUDED.scale,
                "offset" = EXCLUDED."offset",
                decimals = EXCLUDED.decimals,
                string_length = EXCLUDED.string_length,
                word_length = EXCLUDED.word_length,
                bool_true_value = EXCLUDED.bool_true_value,
                bool_false_value = EXCLUDED.bool_false_value,
                bool_invert = EXCLUDED.bool_invert,
                unit = EXCLUDED.unit,
                description = EXCLUDED.description,
                is_active = EXCLUDED.is_active,
                updated_at = EXCLUDED.updated_at
        """

        synced_count = 0
        now = datetime.now()

        try:
            async with self._pool.acquire() as conn:
                for tag in tags:
                    try:
                        # byte_size 계산
                        byte_size = self._calculate_byte_size(tag)

                        # address_meta 준비
                        address_meta: Dict[str, Any] = {}
                        if tag.memory:
                            address_meta['memory'] = tag.memory
                        if tag.format:
                            address_meta['format'] = tag.format

                        await conn.execute(
                            upsert_sql,
                            tag.tag_id,
                            plc_id,
                            tag.tag_name,
                            tag.address,
                            json.dumps(address_meta),
                            tag.data_type.value,
                            byte_size,
                            tag.collection_group,
                            tag.scale,
                            tag.offset,
                            tag.decimals,
                            tag.string_length,
                            tag.word_length,
                            tag.bool_true_value,
                            tag.bool_false_value,
                            tag.bool_invert,
                            tag.unit,
                            tag.description,
                            True,  # is_active
                            now,
                        )
                        synced_count += 1

                    except Exception as e:
                        logger.warning(
                            f"Failed to sync tag {tag.tag_id} ({tag.tag_name}): {e}"
                        )
                        continue

            logger.info(
                f"Synced {synced_count}/{len(tags)} tags for PLC {plc_id} "
                f"to {self._schema}.tag_master"
            )

        except Exception as e:
            logger.error(f"Failed to sync tags: {e}")

        return synced_count

    def _calculate_byte_size(self, tag: TagDefinition) -> int:
        """
        태그의 바이트 크기 계산.

        Args:
            tag: 태그 정의

        Returns:
            바이트 크기
        """
        # word_length가 설정된 경우
        if tag.word_length:
            if tag.data_type == DataType.STRING:
                return tag.word_length * 2  # 워드당 2바이트
            return tag.word_length * 2

        # string_length가 설정된 경우
        if tag.data_type == DataType.STRING and tag.string_length:
            return tag.string_length

        # effective_byte_size 사용
        if hasattr(tag, 'effective_byte_size'):
            return tag.effective_byte_size

        # 기본값: 데이터 타입의 바이트 크기
        return tag.data_type.byte_size

    async def ensure_tables_exist(self) -> bool:
        """
        마스터 테이블 존재 여부 확인.

        Returns:
            테이블이 존재하면 True
        """
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                # plc_master 테이블 확인
                plc_exists = await conn.fetchval(f"""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = $1 AND table_name = 'plc_master'
                    )
                """, self._schema)

                # tag_master 테이블 확인
                tag_exists = await conn.fetchval(f"""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = $1 AND table_name = 'tag_master'
                    )
                """, self._schema)

                if not plc_exists:
                    logger.warning(
                        f"Table {self._schema}.plc_master does not exist"
                    )
                if not tag_exists:
                    logger.warning(
                        f"Table {self._schema}.tag_master does not exist"
                    )

                return plc_exists and tag_exists

        except Exception as e:
            logger.error(f"Failed to check tables: {e}")
            return False

    # =========================================================================
    # 전체 교체 방식 동기화 메서드 (DELETE + INSERT)
    # =========================================================================

    async def sync_all(
        self,
        collector_config: CollectorConfig,
        tags: List[TagDefinition],
        events: Optional[List[Dict[str, Any]]] = None,
        quality_codes: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        모든 마스터 테이블 전체 동기화.

        전체 교체 방식: 해당 PLC 데이터 DELETE 후 INSERT.

        Args:
            collector_config: 수집기 설정
            tags: 태그 정의 리스트
            events: 이벤트 정의 리스트 (Optional)
            quality_codes: 데이터 품질 코드 리스트 (Optional)

        Returns:
            동기화 결과 딕셔너리
        """
        results = {
            'success': True,
            'plc_master': {'count': 0, 'success': False},
            'tag_master': {'count': 0, 'success': False},
            'event_master': {'count': 0, 'success': False},
            'quality_master': {'count': 0, 'success': False},
            'collection_group_master': {'count': 0, 'success': False},
        }

        if not self._pool:
            results['success'] = False
            results['error'] = 'Not connected'
            return results

        plc_id = collector_config.plc_id

        # 1. PLC 마스터 동기화
        try:
            await self._sync_plc_replace(collector_config)
            results['plc_master'] = {'count': 1, 'success': True}
        except Exception as e:
            logger.error(f"PLC master sync failed: {e}")
            results['plc_master'] = {'count': 0, 'success': False, 'error': str(e)}
            results['success'] = False

        # 2. 태그 마스터 동기화
        try:
            count = await self._sync_tags_replace(plc_id, tags)
            results['tag_master'] = {'count': count, 'success': True}
        except Exception as e:
            logger.error(f"Tag master sync failed: {e}")
            results['tag_master'] = {'count': 0, 'success': False, 'error': str(e)}
            results['success'] = False

        # 3. 컬렉션 그룹 마스터 동기화
        if collector_config.collection_groups:
            try:
                count = await self._sync_groups_replace(
                    plc_id, collector_config.collection_groups
                )
                results['collection_group_master'] = {'count': count, 'success': True}
            except Exception as e:
                logger.error(f"Collection group master sync failed: {e}")
                results['collection_group_master'] = {'count': 0, 'success': False, 'error': str(e)}

        # 4. 이벤트 마스터 동기화 (Optional)
        if events:
            try:
                count = await self._sync_events_replace(plc_id, events)
                results['event_master'] = {'count': count, 'success': True}
            except Exception as e:
                logger.error(f"Event master sync failed: {e}")
                results['event_master'] = {'count': 0, 'success': False, 'error': str(e)}

        # 5. 데이터 품질 마스터 동기화 (Optional)
        if quality_codes:
            try:
                count = await self._sync_quality_replace(quality_codes)
                results['quality_master'] = {'count': count, 'success': True}
            except Exception as e:
                logger.error(f"Quality master sync failed: {e}")
                results['quality_master'] = {'count': 0, 'success': False, 'error': str(e)}

        # 결과 요약 로깅
        success_tables = sum(
            1 for k, v in results.items()
            if isinstance(v, dict) and v.get('success')
        )
        logger.info(
            f"Master sync completed: {success_tables} tables synced for PLC {plc_id}"
        )

        return results

    async def _sync_plc_replace(self, collector_config: CollectorConfig) -> None:
        """PLC 마스터 전체 교체."""
        protocol = collector_config.protocol
        protocol_name = protocol.type if protocol else 'unknown'
        host = protocol.host if protocol else None
        port = protocol.port if protocol else None

        async with self._pool.acquire() as conn:
            # 해당 PLC 삭제
            await conn.execute(
                f"DELETE FROM {self._schema}.plc_master WHERE plc_id = $1",
                collector_config.plc_id
            )

            # 새로 삽입
            await conn.execute(f"""
                INSERT INTO {self._schema}.plc_master
                (plc_id, plc_name, protocol_type, host, port, description, collect_yn, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            """,
                collector_config.plc_id,
                collector_config.name,
                protocol_name,
                host,
                port,
                f"Auto-synced at {datetime.now().isoformat()}",
                'Y' if collector_config.enabled else 'N',
            )

        logger.info(f"Synced plc_master: PLC {collector_config.plc_id}")

    async def _sync_tags_replace(
        self,
        plc_id: int,
        tags: List[TagDefinition],
    ) -> int:
        """태그 마스터 전체 교체."""
        async with self._pool.acquire() as conn:
            # 해당 PLC 태그 전체 삭제
            await conn.execute(
                f"DELETE FROM {self._schema}.tag_master WHERE plc_id = $1",
                plc_id
            )

            # 벌크 INSERT
            count = 0
            for tag in tags:
                try:
                    # memory와 address 분리
                    memory = getattr(tag, 'memory', None)
                    address = self._extract_address_number(tag.address)

                    await conn.execute(f"""
                        INSERT INTO {self._schema}.tag_master
                        (plc_id, tag_id, tag_name, memory, address, data_type,
                         scale, offset_value, decimals, word_length, format,
                         unit, description, collection_group, collect_yn, updated_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, NOW())
                    """,
                        plc_id,
                        tag.tag_id,
                        tag.tag_name,
                        memory,
                        address,
                        tag.data_type.value if hasattr(tag.data_type, 'value') else str(tag.data_type),
                        tag.scale,
                        tag.offset,
                        getattr(tag, 'decimals', None),
                        getattr(tag, 'word_length', None),
                        getattr(tag, 'format', None),
                        tag.unit,
                        tag.description,
                        tag.collection_group,
                        'Y',
                    )
                    count += 1
                except Exception as e:
                    logger.warning(f"Failed to insert tag {tag.tag_id}: {e}")
                    continue

        logger.info(f"Synced tag_master: {count} tags for PLC {plc_id}")
        return count

    async def _sync_groups_replace(
        self,
        plc_id: int,
        groups: List[Any],
    ) -> int:
        """컬렉션 그룹 마스터 전체 교체."""
        async with self._pool.acquire() as conn:
            # 해당 PLC 그룹 삭제
            await conn.execute(
                f"DELETE FROM {self._schema}.collection_group_master WHERE plc_id = $1",
                plc_id
            )

            # 벌크 INSERT
            count = 0
            for group in groups:
                group_type = getattr(group, 'type', 'periodic')
                target_table = getattr(group, 'table', f'plc_data_{group.name}')

                await conn.execute(f"""
                    INSERT INTO {self._schema}.collection_group_master
                    (plc_id, group_name, group_type, interval_ms, target_table, enabled)
                    VALUES ($1, $2, $3, $4, $5, $6)
                """,
                    plc_id,
                    group.name,
                    group_type,
                    group.interval_ms,
                    target_table,
                    True,
                )
                count += 1

        logger.info(f"Synced collection_group_master: {count} groups for PLC {plc_id}")
        return count

    async def _sync_events_replace(
        self,
        plc_id: int,
        events: List[Dict[str, Any]],
    ) -> int:
        """이벤트 마스터 전체 교체."""
        async with self._pool.acquire() as conn:
            # 해당 PLC 이벤트 삭제
            await conn.execute(
                f"DELETE FROM {self._schema}.event_master WHERE plc_id = $1",
                plc_id
            )

            # 벌크 INSERT
            count = 0
            for event in events:
                # plc_id 필터링 (해당 PLC 이벤트만)
                if event.get('plc_id') != plc_id:
                    continue

                await conn.execute(f"""
                    INSERT INTO {self._schema}.event_master
                    (event_id, plc_id, memory, address, event_type, severity,
                     event_name, message_on, message_off, auto_reset,
                     collection_group, enabled, updated_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW())
                """,
                    event['event_id'],
                    event['plc_id'],
                    event['memory'],
                    event['address'],
                    event['event_type'],
                    event.get('severity', 2),
                    event['event_name'],
                    event.get('message_on'),
                    event.get('message_off'),
                    event.get('auto_reset', False),
                    event.get('collection_group', 'event'),
                    event.get('enabled', True),
                )
                count += 1

        logger.info(f"Synced event_master: {count} events for PLC {plc_id}")
        return count

    async def _sync_quality_replace(
        self,
        quality_codes: List[Dict[str, Any]],
    ) -> int:
        """데이터 품질 마스터 전체 교체."""
        async with self._pool.acquire() as conn:
            # 전체 삭제
            await conn.execute(f"TRUNCATE {self._schema}.quality_master")

            # 벌크 INSERT
            count = 0
            for qc in quality_codes:
                await conn.execute(f"""
                    INSERT INTO {self._schema}.quality_master
                    (quality_code, quality_name, description)
                    VALUES ($1, $2, $3)
                """,
                    qc['quality_code'],
                    qc['quality_name'],
                    qc.get('description'),
                )
                count += 1

        logger.info(f"Synced quality_master: {count} codes")
        return count

    def _extract_address_number(self, address: str) -> int:
        """주소 문자열에서 숫자 추출."""
        if not address:
            return 0

        # 숫자 부분 추출
        num_str = ""
        for char in address:
            if char.isdigit():
                num_str += char
            elif num_str:  # 숫자 이후 문자 (예: ':')
                break

        try:
            return int(num_str)
        except ValueError:
            return 0


# =============================================================================
# CSV 파일 로더
# =============================================================================

def load_events_from_csv(file_path: str) -> List[Dict[str, Any]]:
    """
    이벤트 마스터 CSV 파일 로드.

    Args:
        file_path: CSV 파일 경로

    Returns:
        이벤트 딕셔너리 리스트
    """
    import csv
    from pathlib import Path

    events = []
    path = Path(file_path)

    if not path.exists():
        logger.warning(f"Event file not found: {file_path}")
        return events

    try:
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # 주석 행 건너뛰기
                if not row.get('event_id') or str(row['event_id']).startswith('#'):
                    continue

                event = {
                    'event_id': int(row['event_id']),
                    'plc_id': int(row['plc_id']),
                    'memory': row['memory'],
                    'address': int(row['address']),
                    'event_type': row['event_type'],
                    'severity': int(row.get('severity', 2)),
                    'event_name': row['event_name'],
                    'message_on': row.get('message_on'),
                    'message_off': row.get('message_off'),
                    'auto_reset': row.get('auto_reset', '').lower() == 'true',
                    'collection_group': row.get('collection_group', 'event'),
                    'enabled': row.get('enabled', 'true').lower() == 'true',
                }
                events.append(event)

        logger.info(f"Loaded {len(events)} events from {file_path}")

    except Exception as e:
        logger.error(f"Failed to load events from CSV: {e}")

    return events


# 기본 데이터 품질 코드
DEFAULT_QUALITY_CODES = [
    {'quality_code': 0, 'quality_name': 'BAD', 'description': '통신 이상 또는 데이터 없음'},
    {'quality_code': 1, 'quality_name': 'GOOD', 'description': '정상 데이터'},
    {'quality_code': 2, 'quality_name': 'UNCERTAIN', 'description': '불확실한 데이터'},
    {'quality_code': 3, 'quality_name': 'TIMEOUT', 'description': '타임아웃'},
    {'quality_code': 4, 'quality_name': 'ERROR', 'description': '에러 발생'},
    {'quality_code': 5, 'quality_name': 'MANUAL', 'description': '수동 입력값'},
    {'quality_code': 6, 'quality_name': 'SIMULATED', 'description': '시뮬레이션 값'},
]
