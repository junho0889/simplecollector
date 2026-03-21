"""
BLE Scanner 싱글톤
==================

Bleak 기반 공유 BLE 스캐너.
여러 BleCollector 인스턴스가 하나의 스캐너를 공유합니다.

Features:
    - 콜백 모드 연속 스캔 (advertisement 실시간 수신)
    - MAC별 최신 advertisement 캐시
    - 참조 카운팅 (acquire/release) — 자동 시작/종료
    - 캐시 TTL 관리 (만료된 데이터 자동 정리)
    - 스캐너 오류 시 자동 재시작

Usage:
    scanner = BleScanner.get_instance()
    await scanner.acquire(cache_ttl=30.0)

    entry = scanner.get_latest("AA:BB:CC:DD:EE:FF")
    if entry:
        print(entry.manufacturer_data)

    await scanner.release()
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger('collector.collection')


@dataclass
class AdvertisementEntry:
    """캐시된 BLE advertisement 데이터."""
    mac_address: str
    device_name: Optional[str]
    rssi: int
    manufacturer_data: Dict[int, bytes]  # {company_id: raw_bytes}
    timestamp: datetime


class BleScanner:
    """
    공유 BLE 스캐너 싱글톤.

    하나의 BLE 어댑터로 모든 BLE 디바이스의 advertisement를 수집합니다.
    여러 BleCollector 인스턴스가 공유합니다.

    Lifecycle:
        - 첫 번째 acquire() 시 스캔 자동 시작
        - 마지막 release() 시 스캔 자동 종료
    """

    _instance: Optional['BleScanner'] = None
    _lock: Optional[asyncio.Lock] = None

    @classmethod
    def get_instance(cls) -> 'BleScanner':
        """싱글톤 인스턴스 반환."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """테스트용: 싱글톤 리셋."""
        cls._instance = None
        cls._lock = None

    def __init__(self):
        self._adv_cache: Dict[str, AdvertisementEntry] = {}
        self._ref_count: int = 0
        self._is_running: bool = False
        self._scan_task: Optional[asyncio.Task] = None
        self._scanner: Any = None  # BleakScanner (lazy import)

        # 설정 (첫 acquire에서 세팅)
        self._cache_ttl: float = 30.0
        self._duplicate_filter_s: float = 4.0

        # 중복 필터
        self._last_seen: Dict[str, float] = {}

    async def _get_lock(self) -> asyncio.Lock:
        """asyncio.Lock lazy init (이벤트 루프 내에서)."""
        if BleScanner._lock is None:
            BleScanner._lock = asyncio.Lock()
        return BleScanner._lock

    async def acquire(
        self,
        cache_ttl: float = 30.0,
        duplicate_filter_s: float = 4.0,
    ) -> None:
        """
        스캐너 참조 획득. 첫 호출 시 스캔 시작.

        Args:
            cache_ttl: advertisement 캐시 만료 시간 (초)
            duplicate_filter_s: 같은 디바이스 중복 필터 시간 (초)
        """
        lock = await self._get_lock()
        async with lock:
            self._ref_count += 1
            logger.info(
                f"[BleScanner] Acquired (ref_count={self._ref_count})"
            )

            if self._ref_count == 1:
                # 첫 acquire — 설정 적용 및 시작
                self._cache_ttl = cache_ttl
                self._duplicate_filter_s = duplicate_filter_s
                await self._start_scanning()
            else:
                if cache_ttl != self._cache_ttl:
                    logger.warning(
                        f"[BleScanner] cache_ttl conflict: "
                        f"existing={self._cache_ttl}, requested={cache_ttl}"
                    )

    async def release(self) -> None:
        """스캐너 참조 해제. 마지막 해제 시 스캔 종료."""
        lock = await self._get_lock()
        async with lock:
            self._ref_count = max(0, self._ref_count - 1)
            logger.info(
                f"[BleScanner] Released (ref_count={self._ref_count})"
            )

            if self._ref_count == 0:
                await self._stop_scanning()

    def get_latest(self, mac_address: str) -> Optional[AdvertisementEntry]:
        """
        MAC 주소의 최신 advertisement 데이터 조회.

        TTL 초과 시 None 반환.
        """
        mac = mac_address.upper()
        entry = self._adv_cache.get(mac)

        if entry is None:
            return None

        # TTL 확인
        age = (datetime.now() - entry.timestamp).total_seconds()
        if age > self._cache_ttl:
            return None

        return entry

    def is_device_available(self, mac_address: str) -> bool:
        """디바이스가 TTL 내에 보였는지 확인."""
        return self.get_latest(mac_address.upper()) is not None

    # =========================================================================
    # 내부 메서드
    # =========================================================================

    async def _start_scanning(self) -> None:
        """Bleak 스캐너 시작."""
        if self._is_running:
            return

        try:
            from bleak import BleakScanner as _BleakScanner

            self._is_running = True
            self._scan_task = asyncio.create_task(
                self._scan_loop(_BleakScanner)
            )
            logger.info("[BleScanner] Scanning started")

        except ImportError:
            logger.error(
                "[BleScanner] bleak 패키지가 설치되지 않았습니다. "
                "pip install bleak"
            )
            self._is_running = False
            raise

    async def _stop_scanning(self) -> None:
        """스캐너 종료."""
        self._is_running = False

        if self._scan_task and not self._scan_task.done():
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass

        if self._scanner:
            try:
                await self._scanner.stop()
            except Exception as e:
                logger.warning(f"[BleScanner] Error stopping scanner: {e}")
            self._scanner = None

        self._adv_cache.clear()
        self._last_seen.clear()
        logger.info("[BleScanner] Scanning stopped")

    async def _scan_loop(self, scanner_cls: type) -> None:
        """
        연속 스캔 루프.

        콜백 모드로 advertisement를 실시간 수신합니다.
        오류 발생 시 자동 재시작 (backoff).
        """
        backoff = 1.0
        max_backoff = 10.0

        while self._is_running:
            try:
                self._scanner = scanner_cls(
                    detection_callback=self._detection_callback,
                )
                await self._scanner.start()
                backoff = 1.0  # 성공 시 backoff 리셋
                logger.info("[BleScanner] Bleak scanner started")

                # 스캔 유지 + 주기적 캐시 정리
                while self._is_running:
                    await asyncio.sleep(5.0)
                    self._cleanup_expired_cache()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[BleScanner] Scan error: {e}, "
                    f"retry in {backoff:.1f}s"
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
            finally:
                if self._scanner:
                    try:
                        await self._scanner.stop()
                    except Exception as e:
                        logger.debug(f"[BleScanner] Cleanup stop error: {e}")
                    self._scanner = None

    def _detection_callback(self, device: Any, adv_data: Any) -> None:
        """
        Bleak detection callback.

        BLE advertisement 수신 시 호출됩니다.
        중복 필터 적용 후 캐시를 갱신합니다.
        """
        import time

        mac = device.address.upper()
        now = time.time()

        # 중복 필터
        if mac in self._last_seen:
            if now - self._last_seen[mac] < self._duplicate_filter_s:
                return

        self._last_seen[mac] = now

        # manufacturer_data 변환 (Bleak은 dict[int, bytes] 반환)
        mfr_data: Dict[int, bytes] = {}
        if hasattr(adv_data, 'manufacturer_data') and adv_data.manufacturer_data:
            mfr_data = dict(adv_data.manufacturer_data)

        entry = AdvertisementEntry(
            mac_address=mac,
            device_name=device.name,
            rssi=adv_data.rssi if hasattr(adv_data, 'rssi') else 0,
            manufacturer_data=mfr_data,
            timestamp=datetime.now(),
        )

        self._adv_cache[mac] = entry

        logger.debug(
            f"[BleScanner] {mac} ({device.name}) "
            f"RSSI={entry.rssi} mfr_keys={list(mfr_data.keys())}"
        )

    def _cleanup_expired_cache(self) -> None:
        """만료된 캐시 엔트리 정리."""
        now = datetime.now()
        expired = [
            mac for mac, entry in self._adv_cache.items()
            if (now - entry.timestamp).total_seconds() > self._cache_ttl * 2
        ]
        for mac in expired:
            del self._adv_cache[mac]
            self._last_seen.pop(mac, None)

        if expired:
            logger.debug(f"[BleScanner] Cleaned {len(expired)} expired entries")
