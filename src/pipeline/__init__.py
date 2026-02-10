"""
Pipeline 모듈
=============

데이터 파이프라인 관리를 제공합니다.

파이프라인 구조:
    Collector → Processor → Buffer → Publisher

주요 클래스:
    - Pipeline: 단일 파이프라인 (1 PLC = 1 Pipeline)
    - PipelineManager: 다중 파이프라인 관리

Usage:
    from src.pipeline import Pipeline, PipelineManager

    # 단일 파이프라인
    pipeline = Pipeline(config, collector, processor, publisher)
    await pipeline.start()

    # 매니저 사용
    manager = PipelineManager()
    manager.add_pipeline(pipeline)
    await manager.start_all()
"""

from .manager import Pipeline, PipelineManager

__all__ = [
    "Pipeline",
    "PipelineManager",
]
