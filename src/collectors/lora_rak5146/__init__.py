"""
LoRa RAK5146 Collector Module
==============================

RAK5146 (SX1303) 기반 LoRa 패킷 수집 모듈.
SPI를 통해 HAL(libloragw.so)로 LoRa 패킷을 수신합니다.

Components:
    - LoRaRak5146Collector: BaseCollector 구현 (디바이스당 1개)
    - LoRaRak5146Processor: BaseProcessor 구현 (DeviceProfile 기반 파싱)
    - LoRaScanner: 공유 싱글톤 수신기 (HAL 래핑)
    - DeviceProfile: 센서별 바이트 파싱 프로파일

Requirements:
    - libloragw.so (ARM64, sx1302_hal 빌드)
    - SPI 활성화 (RPi: sudo raspi-config → Interface → SPI)
"""

from .collector import LoRaRak5146Collector
from .processor import LoRaRak5146Processor

__all__ = ['LoRaRak5146Collector', 'LoRaRak5146Processor']
