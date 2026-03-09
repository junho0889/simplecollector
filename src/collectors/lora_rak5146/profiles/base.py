"""
LoRa 디바이스 프로파일 기본 클래스
==================================

LoRa 센서 유형별 payload 바이트 레이아웃을 정의하는 추상 클래스.
각 센서 유형마다 DeviceProfile을 상속받아 parse() 메서드를 구현합니다.

BLE 프로파일과의 차이:
    - BLE: parse(company_id, data) — company_id가 별도 파라미터
    - LoRa: parse(data) — 전체 payload가 하나의 바이트 배열

Example:
    class MySensorProfile(DeviceProfile):
        @property
        def name(self) -> str:
            return "my_sensor"

        def parse(self, data: bytes) -> Dict[str, Any]:
            return {"temperature": struct.unpack_from('<h', data, 0)[0] / 100.0}
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List


class DeviceProfile(ABC):
    """
    LoRa 디바이스 프로파일 추상 클래스.

    end device payload의 바이트 레이아웃을 정의하고 파싱합니다.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """프로파일 이름 (YAML extra.device_profile 값과 일치)."""

    @abstractmethod
    def parse(self, data: bytes) -> Dict[str, Any]:
        """
        Raw payload 파싱.

        Args:
            data: end device에서 보낸 raw 바이트 배열 (device_id 제외)

        Returns:
            {필드이름: 파싱된 값} — 스케일링 완료된 최종값
        """

    @abstractmethod
    def get_field_names(self) -> List[str]:
        """사용 가능한 필드 이름 목록."""
