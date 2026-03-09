"""
BLE 디바이스 프로파일 레지스트리
"""

import logging
from typing import Dict, List, Optional

from .base import DeviceProfile

logger = logging.getLogger(__name__)


class ProfileRegistry:
    """디바이스 프로파일 레지스트리."""

    _profiles: Dict[str, DeviceProfile] = {}

    @classmethod
    def register(cls, profile: DeviceProfile) -> None:
        cls._profiles[profile.name] = profile
        logger.debug(f"[ProfileRegistry] Registered: {profile.name}")

    @classmethod
    def get(cls, name: str) -> Optional[DeviceProfile]:
        return cls._profiles.get(name)

    @classmethod
    def list_profiles(cls) -> List[str]:
        return list(cls._profiles.keys())


# Built-in 프로파일 자동 등록
from .posiot import PosiotProfile

ProfileRegistry.register(PosiotProfile())

__all__ = ['DeviceProfile', 'ProfileRegistry']
