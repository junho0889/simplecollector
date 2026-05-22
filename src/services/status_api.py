# =============================================================================
# NeuroForge Collector - Status API Service
# =============================================================================
# Copyright (c) 2024-2026 NEUROSENSE Inc. All Rights Reserved.
# =============================================================================
"""
수집기 상태 API 서비스

CollectorHub Agent가 수집기 상태를 조회할 수 있도록 경량 HTTP API 제공.

Usage:
    # 환경변수로 활성화
    STATUS_API_ENABLED=true
    STATUS_API_PORT=8090

    # 또는 코드에서 직접 사용
    from src.services.status_api import StatusAPIService

    api = StatusAPIService(pipeline_manager, port=8090)
    await api.start()
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from aiohttp import web

logger = logging.getLogger(__name__)


class StatusAPIService:
    """
    수집기 상태 API 서비스.

    경량 HTTP 서버로 수집기 상태 정보 제공.

    Endpoints:
        GET /health          - 헬스 체크
        GET /status          - 상세 상태
        GET /stats           - 통계 정보
        GET /config          - 현재 설정 (읽기 전용)
    """

    def __init__(
        self,
        pipeline_manager=None,
        host: str = "0.0.0.0",
        port: int = 8090,
    ):
        """
        Args:
            pipeline_manager: PipelineManager 인스턴스
            host: 바인드 호스트
            port: 바인드 포트
        """
        self._pipeline_manager = pipeline_manager
        self._host = host
        self._port = port
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._start_time = time.time()
        self._is_running = False

    async def start(self) -> None:
        """API 서버 시작"""
        if self._is_running:
            return

        self._app = web.Application()
        self._setup_routes()

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        self._site = web.TCPSite(self._runner, self._host, self._port)
        await self._site.start()

        self._is_running = True
        logger.info(f"Status API started on http://{self._host}:{self._port}")

    async def stop(self) -> None:
        """API 서버 중지"""
        if not self._is_running:
            return

        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()

        self._is_running = False
        logger.info("Status API stopped")

    def _setup_routes(self) -> None:
        """라우트 설정"""
        self._app.router.add_get("/", self._handle_root)
        self._app.router.add_get("/health", self._handle_health)
        self._app.router.add_get("/status", self._handle_status)
        self._app.router.add_get("/stats", self._handle_stats)
        self._app.router.add_get("/config", self._handle_config)

    async def _handle_root(self, request: web.Request) -> web.Response:
        """루트 엔드포인트"""
        return web.json_response({
            "name": "NeuroForge Collector",
            "version": "1.0.0",
            "status": "running",
            "api_version": "v1",
        })

    async def _handle_health(self, request: web.Request) -> web.Response:
        """헬스 체크"""
        uptime = time.time() - self._start_time

        # 파이프라인 상태 확인
        is_healthy = True
        pipelines_running = 0
        pipelines_total = 0

        if self._pipeline_manager:
            stats = self._pipeline_manager.get_all_stats()
            pipelines_total = len(stats)
            pipelines_running = sum(
                1 for s in stats.values()
                if s.get("status") == "running"
            )
            is_healthy = pipelines_running > 0

        return web.json_response({
            "status": "healthy" if is_healthy else "unhealthy",
            "uptime": round(uptime, 2),
            "timestamp": datetime.now().isoformat(),
            "pipelines": {
                "total": pipelines_total,
                "running": pipelines_running,
            },
        })

    async def _handle_status(self, request: web.Request) -> web.Response:
        """상세 상태"""
        uptime = time.time() - self._start_time

        status = {
            "name": "NeuroForge Collector",
            "version": "1.0.0",
            "uptime": round(uptime, 2),
            "timestamp": datetime.now().isoformat(),
            "pipelines": [],
        }

        if self._pipeline_manager:
            stats = self._pipeline_manager.get_all_stats()
            for name, pipeline_stats in stats.items():
                status["pipelines"].append({
                    "name": name,
                    "status": pipeline_stats.get("status", "unknown"),
                    "plc_id": pipeline_stats.get("plc_id"),
                    "protocol": pipeline_stats.get("protocol"),
                    "is_connected": pipeline_stats.get("collector", {}).get("is_connected", False),
                    "total_collected": pipeline_stats.get("collector", {}).get("total_collected", 0),
                    "total_processed": pipeline_stats.get("processor", {}).get("total_processed", 0),
                    "total_published": pipeline_stats.get("publisher", {}).get("total_published", 0),
                    "buffer_size": pipeline_stats.get("buffer", {}).get("current_size", 0),
                })

        return web.json_response(status)

    async def _handle_stats(self, request: web.Request) -> web.Response:
        """통계 정보"""
        stats = {
            "timestamp": datetime.now().isoformat(),
            "pipelines": {},
        }

        if self._pipeline_manager:
            all_stats = self._pipeline_manager.get_all_stats()
            for name, pipeline_stats in all_stats.items():
                stats["pipelines"][name] = {
                    "collector": pipeline_stats.get("collector", {}),
                    "processor": pipeline_stats.get("processor", {}),
                    "buffer": pipeline_stats.get("buffer", {}),
                    "publisher": pipeline_stats.get("publisher", {}),
                }

        return web.json_response(stats)

    async def _handle_config(self, request: web.Request) -> web.Response:
        """현재 설정 (읽기 전용, 민감 정보 제외)"""
        config = {
            "timestamp": datetime.now().isoformat(),
            "pipelines": [],
        }

        if self._pipeline_manager:
            stats = self._pipeline_manager.get_all_stats()
            for name, pipeline_stats in stats.items():
                config["pipelines"].append({
                    "name": name,
                    "plc_id": pipeline_stats.get("plc_id"),
                    "protocol": pipeline_stats.get("protocol"),
                    "groups": pipeline_stats.get("groups", []),
                })

        return web.json_response(config)

    @property
    def is_running(self) -> bool:
        """실행 중 여부"""
        return self._is_running
