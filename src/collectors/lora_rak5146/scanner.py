"""
LoRa Scanner 싱글톤
===================

RAK5146 HAL 기반 공유 LoRa 패킷 수신기.
여러 LoRaCollector 인스턴스가 하나의 스캐너를 공유합니다.

Features:
    - HAL 기반 연속 수신 루프 (lgw_receive non-blocking 폴링)
    - 디바이스별 최신 패킷 캐시 (device_id 기반)
    - 참조 카운팅 (acquire/release) — 자동 시작/종료
    - 캐시 TTL 관리 (만료된 데이터 자동 정리)
    - 수신 오류 시 자동 재시작

Architecture:
    BLE Scanner와 동일한 패턴:
    - BLE: Bleak → advertisement callback → cache
    - LoRa: HAL lgw_receive() → polling loop → cache

Usage:
    scanner = LoRaScanner.get_instance()
    await scanner.acquire(lib_path="/app/lib/libloragw.so", spi_path="/dev/spidev0.0")

    entry = scanner.get_latest(device_id=1)
    if entry:
        print(entry.payload, entry.rssi)

    await scanner.release()
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

from .hal_wrapper import HalWrapper, LoRaPacket

logger = logging.getLogger('collector.collection')


@dataclass
class LoRaPacketEntry:
    """캐시된 LoRa 패킷 데이터."""
    device_id: int                  # end device ID (payload 첫 바이트에서 추출)
    payload: bytes                  # raw 페이로드 (device_id 이후 데이터 바이트)
    full_payload: bytes             # 전체 페이로드 (device_id 포함)
    rssi: float                     # 신호 세기 (dBm)
    snr: float                      # 신호 대 잡음비
    freq_hz: int                    # 수신 주파수
    datarate: int                   # Spreading Factor
    timestamp: datetime             # 수신 시각


class LoRaScanner:
    """
    공유 LoRa 패킷 수신기 싱글톤.

    RAK5146 HAL을 사용하여 LoRa 패킷을 수신하고,
    device_id별로 최신 패킷을 캐시합니다.

    Packet Format (end device → gateway):
        [0]     device_id (uint8)   — end device 식별자 (1~255)
        [1:]    payload             — 센서 데이터 바이트

    Lifecycle:
        - 첫 번째 acquire() 시 HAL 초기화 + 수신 시작
        - 마지막 release() 시 HAL 종료
    """

    _instance: Optional['LoRaScanner'] = None
    _lock: Optional[asyncio.Lock] = None

    @classmethod
    def get_instance(cls) -> 'LoRaScanner':
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
        self._hal: Optional[HalWrapper] = None
        self._pkt_cache: Dict[int, LoRaPacketEntry] = {}
        self._ref_count: int = 0
        self._is_running: bool = False
        self._receive_task: Optional[asyncio.Task] = None

        # 설정 (첫 acquire에서 세팅)
        self._cache_ttl: float = 30.0
        self._poll_interval: float = 0.01   # 10ms — lgw_receive() 폴링 주기

        # 통계
        self._total_received: int = 0
        self._total_crc_error: int = 0

    async def _get_lock(self) -> asyncio.Lock:
        """asyncio.Lock lazy init."""
        if LoRaScanner._lock is None:
            LoRaScanner._lock = asyncio.Lock()
        return LoRaScanner._lock

    async def acquire(
        self,
        lib_path: str = "libloragw.so",
        spi_path: str = "/dev/spidev0.0",
        freq_hz: int = 923_300_000,
        cache_ttl: float = 30.0,
        poll_interval: float = 0.01,
        com_path: Optional[str] = None,
        com_type: Optional[str] = None,
    ) -> None:
        """
        스캐너 참조 획득. 첫 호출 시 HAL 초기화 + 수신 시작.

        Args:
            lib_path: libloragw.so 파일 경로
            spi_path: SPI 디바이스 경로 (하위 호환용)
            freq_hz: 중심 주파수 (Hz)
            cache_ttl: 패킷 캐시 만료 시간 (초)
            poll_interval: lgw_receive() 폴링 간격 (초)
            com_path: 디바이스 경로 (USB: /dev/ttyACMx, SPI: /dev/spidev0.0)
            com_type: 통신 타입 문자열 ("usb" 또는 "spi", None이면 자동 감지)
        """
        actual_path = com_path or spi_path
        lock = await self._get_lock()
        async with lock:
            self._ref_count += 1
            logger.info(
                f"[LoRaScanner] Acquired (ref_count={self._ref_count})"
            )

            if self._ref_count == 1:
                self._cache_ttl = cache_ttl
                self._poll_interval = poll_interval
                await self._start(lib_path, actual_path, freq_hz, com_type)
            else:
                if cache_ttl != self._cache_ttl:
                    logger.warning(
                        f"[LoRaScanner] cache_ttl conflict: "
                        f"existing={self._cache_ttl}, requested={cache_ttl}"
                    )

    async def release(self) -> None:
        """스캐너 참조 해제. 마지막 해제 시 HAL 종료."""
        lock = await self._get_lock()
        async with lock:
            self._ref_count = max(0, self._ref_count - 1)
            logger.info(
                f"[LoRaScanner] Released (ref_count={self._ref_count})"
            )

            if self._ref_count == 0:
                await self._stop()

    def get_latest(self, device_id: int) -> Optional[LoRaPacketEntry]:
        """
        device_id의 최신 패킷 데이터 조회.

        TTL 초과 시 None 반환.
        """
        entry = self._pkt_cache.get(device_id)
        if entry is None:
            return None

        age = (datetime.now() - entry.timestamp).total_seconds()
        if age > self._cache_ttl:
            return None

        return entry

    def is_device_available(self, device_id: int) -> bool:
        """디바이스가 TTL 내에 패킷을 보냈는지 확인."""
        return self.get_latest(device_id) is not None

    def get_stats(self) -> Dict[str, Any]:
        """수신 통계."""
        return {
            'total_received': self._total_received,
            'total_crc_error': self._total_crc_error,
            'cached_devices': len(self._pkt_cache),
            'is_running': self._is_running,
        }

    # =========================================================================
    # 내부 메서드
    # =========================================================================

    async def _start(
        self,
        lib_path: str,
        com_path: str,
        freq_hz: int,
        com_type: Optional[str] = None,
    ) -> None:
        """HAL 초기화 + 수신 루프 시작."""
        if self._is_running:
            return

        self._hal = HalWrapper(lib_path)

        if not self._hal.load():
            raise RuntimeError("HAL library load failed")

        # com_type 문자열 → ComType enum 변환
        from .hal_wrapper import ComType as HalComType
        hal_com_type = None
        if com_type:
            hal_com_type = HalComType.USB if com_type.lower() == "usb" else HalComType.SPI

        if not self._hal.configure(com_path=com_path, freq_hz=freq_hz, com_type=hal_com_type):
            raise RuntimeError("HAL configure failed")

        if not self._hal.start():
            raise RuntimeError("HAL start failed")

        self._is_running = True
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("[LoRaScanner] Started")

    async def _stop(self) -> None:
        """HAL 종료 + 수신 루프 정지."""
        self._is_running = False

        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        if self._hal:
            self._hal.stop()
            self._hal = None

        self._pkt_cache.clear()
        logger.info(
            f"[LoRaScanner] Stopped "
            f"(total_rx={self._total_received}, crc_err={self._total_crc_error})"
        )

    async def _receive_loop(self) -> None:
        """
        패킷 수신 루프.

        lgw_receive()는 non-blocking이므로, 짧은 간격으로 폴링합니다.
        수신된 패킷은 device_id별로 캐시에 저장합니다.
        """
        backoff = 0.01
        max_backoff = 5.0
        cleanup_counter = 0
        poll_count = 0
        log_interval = 300  # 300회(~30초)마다 상태 로그

        logger.info("[LoRaScanner] Receive loop started, polling lgw_receive()...")

        while self._is_running:
            try:
                if not self._hal:
                    break

                # non-blocking 수신
                packets = self._hal.receive()
                poll_count += 1

                for pkt in packets:
                    self._process_packet(pkt)

                # 패킷이 있으면 backoff 리셋
                if packets:
                    backoff = self._poll_interval
                else:
                    # 패킷 없으면 약간 대기
                    backoff = min(backoff * 1.1, 0.1)

                await asyncio.sleep(backoff)

                # 주기적 상태 로그 (패킷 없을 때도 살아있음 확인)
                cleanup_counter += 1
                if cleanup_counter >= log_interval:
                    logger.debug(
                        f"[LoRaScanner] POLL_STATUS | polls={poll_count} "
                        f"total_rx={self._total_received} "
                        f"crc_err={self._total_crc_error} "
                        f"cached={len(self._pkt_cache)} "
                        f"backoff={backoff:.3f}s"
                    )
                    self._cleanup_expired_cache()
                    cleanup_counter = 0

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(
                    f"[LoRaScanner] Receive loop error: {e}, "
                    f"retry in {max_backoff:.1f}s"
                )
                await asyncio.sleep(max_backoff)

    def _process_packet(self, pkt: LoRaPacket) -> None:
        """수신 패킷 처리 → 캐시 저장."""
        self._total_received += 1
        now = datetime.now()

        # CRC 체크
        if not pkt.crc_ok:
            self._total_crc_error += 1
            raw_hex = pkt.payload[:pkt.size].hex() if pkt.size > 0 else ""
            logger.warning(
                f"[LoRaScanner] CRC_FAIL | size={pkt.size}B "
                f"freq={pkt.freq_hz} SF={pkt.datarate} "
                f"RSSI={pkt.rssi:.1f} SNR={pkt.snr:.1f} "
                f"raw={raw_hex}"
            )
            return

        # 페이로드 길이 확인 (최소 2바이트: device_id + 데이터)
        if pkt.size < 2:
            logger.warning(
                f"[LoRaScanner] PKT_SHORT | size={pkt.size}B "
                f"raw={pkt.payload[:pkt.size].hex()}"
            )
            return

        # device_id 추출 (첫 바이트)
        device_id = pkt.payload[0]
        data_payload = pkt.payload[1:]
        raw_hex = pkt.payload[:pkt.size].hex()

        entry = LoRaPacketEntry(
            device_id=device_id,
            payload=data_payload,
            full_payload=pkt.payload,
            rssi=pkt.rssi,
            snr=pkt.snr,
            freq_hz=pkt.freq_hz,
            datarate=pkt.datarate,
            timestamp=now,
        )

        self._pkt_cache[device_id] = entry

        logger.info(
            f"[LoRaScanner] RX | dev={device_id} size={pkt.size}B "
            f"RSSI={pkt.rssi:.1f} SNR={pkt.snr:.1f} "
            f"freq={pkt.freq_hz} SF={pkt.datarate} "
            f"time={now.strftime('%H:%M:%S.%f')[:-3]} "
            f"raw={raw_hex}"
        )

    def _cleanup_expired_cache(self) -> None:
        """만료된 캐시 엔트리 정리."""
        now = datetime.now()
        expired = [
            dev_id for dev_id, entry in self._pkt_cache.items()
            if (now - entry.timestamp).total_seconds() > self._cache_ttl * 2
        ]
        for dev_id in expired:
            del self._pkt_cache[dev_id]

        if expired:
            logger.debug(
                f"[LoRaScanner] Cleaned {len(expired)} expired entries"
            )
