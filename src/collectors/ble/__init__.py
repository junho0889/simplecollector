"""
BLE Collector Module
====================

BLE (Bluetooth Low Energy) advertisement 기반 데이터 수집 모듈.
Bleak 라이브러리를 사용하여 Windows/Linux(RPi) 크로스 플랫폼 지원.

Components:
    - BleCollector: BaseCollector 구현 (디바이스당 1개)
    - BleProcessor: BaseProcessor 구현 (DeviceProfile 기반 파싱)
    - BleScanner: 공유 싱글톤 스캐너
    - DeviceProfile: 센서별 바이트 파싱 프로파일

Requirements:
    pip install bleak
"""

from .collector import BleCollector
from .processor import BleProcessor

__all__ = ['BleCollector', 'BleProcessor']
