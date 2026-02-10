"""
Simple Collector - Industrial Data Collection Framework
========================================================

Copyright (c) 2024-2026 NEUROSENSE Inc. All Rights Reserved.

PLC, 센서 등의 산업용 데이터를 수집하여 TimescaleDB에 저장하는 프레임워크.

주요 컴포넌트:
    - Collector: 프로토콜별 데이터 수집 (Modbus, MC Protocol 등)
    - Processor: 수집된 Raw 데이터 파싱 및 변환
    - Publisher: 처리된 데이터를 DB/MQTT 등으로 전송

Version History:
    - 0.2.0-beta: 태그 설정 확장, 마스터 동기화, 최적화
    - 0.1.0: 초기 릴리스
"""

__version__ = "0.2.0-beta"
__author__ = "NEUROSENSE Inc."
__copyright__ = "Copyright (c) 2024-2026 NEUROSENSE Inc. All Rights Reserved."
__license__ = "Proprietary"
