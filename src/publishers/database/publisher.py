"""
Database Publisher
==================

TimescaleDB/PostgreSQL로 데이터를 저장합니다.

Features:
    - 비동기 커넥션 풀 (asyncpg)
    - 배치 INSERT (COPY 프로토콜 사용)
    - 자동 재연결
    - 트랜잭션 관리
    - 연결 풀 모니터링

Performance:
    - COPY 프로토콜: 대량 INSERT 최적화
    - 커넥션 풀: 연결 재사용
    - Prepared Statement: 쿼리 캐싱

Connection Stability:
    - 풀 연결 상태 모니터링
    - 연결 실패 시 자동 재연결
    - 트랜잭션 롤백 처리

Example:
    publisher = DatabasePublisher(
        name="db_publisher",
        db_config=db_config,
        publisher_config=publisher_config,
    )

    await publisher.start()
"""

import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

try:
    import asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    ASYNCPG_AVAILABLE = False

from ...publishers.base import BasePublisher
from ...core.interfaces import ProcessedData
from ...core.config import DatabaseConfig, PublisherConfig
from ...utils.logging import LoggerFactory

logger = LoggerFactory.get_publish_logger()


class DatabasePublisher(BasePublisher):
    """
    데이터베이스 발행기.

    asyncpg를 사용하여 TimescaleDB/PostgreSQL에 데이터를 저장합니다.

    Insert Methods:
        - copy: COPY 프로토콜 (가장 빠름, 기본값)
        - batch: executemany (중간)
        - single: 개별 INSERT (가장 느림)

    Attributes:
        db_config: 데이터베이스 설정
        _pool: 커넥션 풀
        _insert_method: INSERT 방식
    """

    # INSERT 쿼리 템플릿 (새 스키마)
    INSERT_SQL = """
        INSERT INTO {table} (
            source_time, server_time, plc_id, tag_id,
            v_bool, v_byte, v_int, v_bigint, v_float, v_text, quality_code
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
    """

    # 컬럼 정의
    COLUMNS = [
        'source_time', 'server_time', 'plc_id', 'tag_id',
        'v_bool', 'v_byte', 'v_int', 'v_bigint', 'v_float', 'v_text', 'quality_code'
    ]

    def __init__(
        self,
        name: str,
        db_config: DatabaseConfig,
        publisher_config: PublisherConfig,
        insert_method: str = 'copy',
    ):
        """
        Args:
            name: 발행기 이름
            db_config: 데이터베이스 설정
            publisher_config: 발행기 설정
            insert_method: INSERT 방식 ('copy', 'batch', 'single')
        """
        super().__init__(name, publisher_config)

        if not ASYNCPG_AVAILABLE:
            raise ImportError("asyncpg is required. Install with: pip install asyncpg")

        self._db_config = db_config
        self._insert_method = insert_method
        self._pool: Optional[asyncpg.Pool] = None

        # 테이블명 파싱 (schema.table 형식 지원)
        if '.' in db_config.table_name:
            self._schema_name, self._table_name = db_config.table_name.split('.', 1)
        else:
            self._schema_name = None
            self._table_name = db_config.table_name

        # DSN 생성
        self._dsn = (
            f"postgresql://{db_config.user}:{db_config.password}"
            f"@{db_config.host}:{db_config.port}/{db_config.database}"
        )

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def _do_connect(self) -> bool:
        """
        데이터베이스 연결 (커넥션 풀 생성).

        Returns:
            연결 성공 여부
        """
        try:
            # 기존 풀 정리
            if self._pool:
                await self._pool.close()

            # 커넥션 풀 생성
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=2,
                max_size=self._db_config.pool_size,
                command_timeout=30,
                max_inactive_connection_lifetime=300,
            )

            # 연결 테스트
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")

            logger.info(
                f"[{self._name}] Connected to database "
                f"{self._db_config.host}:{self._db_config.port}/{self._db_config.database}"
            )
            return True

        except Exception as e:
            logger.error(f"[{self._name}] Database connection error: {e}")
            return False

    async def _do_disconnect(self) -> None:
        """연결 해제 (커넥션 풀 종료)."""
        if self._pool:
            try:
                await self._pool.close()
            except Exception as e:
                logger.warning(f"[{self._name}] Disconnect error: {e}")
            finally:
                self._pool = None

    async def _do_health_check(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            연결 정상 여부
        """
        if not self._pool:
            return False

        try:
            async with self._pool.acquire() as conn:
                await conn.execute("SELECT 1")
            return True
        except Exception as e:
            logger.error(f"[{self._name}] Health check failed: {e}")
            return False

    # =========================================================================
    # Data Publishing
    # =========================================================================

    async def _do_publish(self, data: List[ProcessedData]) -> bool:
        """
        데이터 저장.

        Args:
            data: 저장할 데이터 리스트

        Returns:
            저장 성공 여부
        """
        if not data:
            return True

        if not self._pool:
            logger.error(f"[{self._name}] Database pool not initialized")
            return False

        try:
            if self._insert_method == 'copy':
                return await self._publish_copy(data)
            elif self._insert_method == 'batch':
                return await self._publish_batch(data)
            else:
                return await self._publish_single(data)
        except Exception as e:
            logger.error(f"[{self._name}] Publish error: {e}")
            return False

    async def _publish_copy(self, data: List[ProcessedData]) -> bool:
        """
        COPY 프로토콜로 저장 (가장 빠름).

        Args:
            data: 저장할 데이터 리스트

        Returns:
            저장 성공 여부
        """
        # 데이터를 튜플 리스트로 변환 (새 스키마)
        records = [item.to_tuple() for item in data]

        try:
            async with self._pool.acquire() as conn:
                # COPY 프로토콜 사용
                result = await conn.copy_records_to_table(
                    table_name=self._table_name,
                    records=records,
                    columns=self.COLUMNS,
                    schema_name=self._schema_name,
                )

            logger.debug(f"[{self._name}] Inserted {len(data)} records via COPY")
            return True

        except asyncpg.PostgresError as e:
            logger.error(f"[{self._name}] Database error: {e}")
            return False

    async def _publish_batch(self, data: List[ProcessedData]) -> bool:
        """
        배치 INSERT (executemany).

        Args:
            data: 저장할 데이터 리스트

        Returns:
            저장 성공 여부
        """
        records = [item.to_tuple() for item in data]
        full_table = f"{self._schema_name}.{self._table_name}" if self._schema_name else self._table_name
        sql = self.INSERT_SQL.format(table=full_table)

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(sql, records)

            logger.debug(f"[{self._name}] Inserted {len(data)} records via batch")
            return True

        except asyncpg.PostgresError as e:
            logger.error(f"[{self._name}] Database error: {e}")
            return False

    async def _publish_single(self, data: List[ProcessedData]) -> bool:
        """
        개별 INSERT.

        Args:
            data: 저장할 데이터 리스트

        Returns:
            저장 성공 여부
        """
        full_table = f"{self._schema_name}.{self._table_name}" if self._schema_name else self._table_name
        sql = self.INSERT_SQL.format(table=full_table)
        success_count = 0

        try:
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    for item in data:
                        await conn.execute(sql, *item.to_tuple())
                        success_count += 1

            logger.debug(f"[{self._name}] Inserted {success_count} records")
            return True

        except asyncpg.PostgresError as e:
            logger.error(f"[{self._name}] Database error: {e}")
            return False

    # =========================================================================
    # Statistics
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """통계 조회."""
        stats = super().get_stats()

        pool_stats = {}
        if self._pool:
            pool_stats = {
                "pool_size": self._pool.get_size(),
                "pool_free": self._pool.get_idle_size(),
                "pool_used": self._pool.get_size() - self._pool.get_idle_size(),
            }

        stats.update({
            "db_host": self._db_config.host,
            "db_port": self._db_config.port,
            "db_name": self._db_config.database,
            "table_name": self._table_name,
            "insert_method": self._insert_method,
            **pool_stats,
        })
        return stats
