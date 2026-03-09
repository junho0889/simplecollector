"""
SX1303 HAL ctypes Wrapper
=========================

Semtech sx1302_hal (libloragw.so) Python 바인딩.
RAK5146 모듈의 SPI 통신을 추상화합니다.

HAL API Flow:
    lgw_board_setconf()  → 보드 설정 (SPI 경로, 클럭)
    lgw_rxrf_setconf()   → RF 채널 설정 (주파수, 라디오 타입)
    lgw_rxif_setconf()   → IF 채널 설정 (다채널 수신)
    lgw_start()          → 수신 시작
    lgw_receive()        → 패킷 수신 (non-blocking, 최대 16패킷)
    lgw_stop()           → 수신 종료

Requirements:
    - libloragw.so (ARM64, RPi용 크로스 빌드 필요)
    - SPI 활성화: sudo raspi-config → Interface → SPI

References:
    https://github.com/Lora-net/sx1302_hal
"""

import ctypes
import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional

logger = logging.getLogger('collector.collection')

# ============================================================================
# Constants
# ============================================================================

LGW_HAL_SUCCESS = 0
LGW_HAL_ERROR = -1
LGW_RF_CHAIN_NB = 2
LGW_IF_CHAIN_NB = 10
LGW_MULTI_NB = 8
LGW_PKT_MAX = 16            # lgw_receive() 최대 패킷 수
LGW_PAYLOAD_MAX = 256


class RadioType(IntEnum):
    """LoRa 라디오 칩 타입."""
    NONE = 0
    SX1255 = 1
    SX1257 = 2
    SX1272 = 3
    SX1276 = 4
    SX1250 = 5              # RAK5146 사용


class ComType(IntEnum):
    """통신 인터페이스 타입."""
    SPI = 0
    USB = 1


class CrcStatus(IntEnum):
    """CRC 상태."""
    UNDEFINED = 0x00
    NO_CRC = 0x01
    BAD_CRC = 0x11
    CRC_OK = 0x10


# ============================================================================
# ctypes Structures (loragw_hal.h 미러링)
# ============================================================================

class LgwConfBoard(ctypes.Structure):
    """보드 설정 (lgw_conf_board_s)."""
    _fields_ = [
        ("lorawan_public", ctypes.c_bool),
        ("clksrc", ctypes.c_uint8),
        ("full_duplex", ctypes.c_bool),
        ("com_type", ctypes.c_int),         # ComType enum
        ("com_path", ctypes.c_char * 64),
    ]


class LgwRssiTcomp(ctypes.Structure):
    """RSSI 온도 보상 계수."""
    _fields_ = [
        ("coeff_a", ctypes.c_float),
        ("coeff_b", ctypes.c_float),
        ("coeff_c", ctypes.c_float),
        ("coeff_d", ctypes.c_float),
        ("coeff_e", ctypes.c_float),
    ]


class LgwConfRxRf(ctypes.Structure):
    """RF 채널 설정 (lgw_conf_rxrf_s)."""
    _fields_ = [
        ("enable", ctypes.c_bool),
        ("freq_hz", ctypes.c_uint32),
        ("rssi_offset", ctypes.c_float),
        ("rssi_tcomp", LgwRssiTcomp),
        ("type", ctypes.c_int),             # RadioType enum
        ("tx_enable", ctypes.c_bool),
        ("single_input_mode", ctypes.c_bool),
    ]


class LgwConfRxIf(ctypes.Structure):
    """IF 채널 설정 (lgw_conf_rxif_s)."""
    _fields_ = [
        ("enable", ctypes.c_bool),
        ("rf_chain", ctypes.c_uint8),
        ("freq_hz", ctypes.c_int32),        # RF 중심 주파수 대비 오프셋
        ("datarate", ctypes.c_uint32),       # LoRa SF or FSK datarate
        ("bandwidth", ctypes.c_uint8),
        ("sync_word_size", ctypes.c_uint8),
        ("sync_word", ctypes.c_uint64),
        ("implicit_hdr", ctypes.c_bool),
        ("implicit_crc_en", ctypes.c_bool),
        ("implicit_payload_length", ctypes.c_uint8),
        ("implicit_coderate", ctypes.c_uint8),
    ]


class LgwConfDemod(ctypes.Structure):
    """복조 설정 (lgw_conf_demod_s)."""
    _fields_ = [
        ("multisf_datarate", ctypes.c_uint8),
    ]


class LgwPktRx(ctypes.Structure):
    """수신 패킷 구조체 (lgw_pkt_rx_s)."""
    _fields_ = [
        ("freq_hz", ctypes.c_uint32),
        ("freq_offset", ctypes.c_int32),
        ("if_chain", ctypes.c_uint8),
        ("status", ctypes.c_uint8),         # CrcStatus
        ("rf_chain", ctypes.c_uint8),
        ("modem_id", ctypes.c_uint8),
        ("modulation", ctypes.c_uint8),
        ("bandwidth", ctypes.c_uint8),
        ("datarate", ctypes.c_uint32),
        ("coderate", ctypes.c_uint8),
        ("rssic", ctypes.c_float),
        ("rssis", ctypes.c_float),
        ("snr", ctypes.c_float),
        ("snr_min", ctypes.c_float),
        ("snr_max", ctypes.c_float),
        ("crc", ctypes.c_uint16),
        ("size", ctypes.c_uint16),
        ("payload", ctypes.c_uint8 * LGW_PAYLOAD_MAX),
        ("ftime_received", ctypes.c_bool),
        ("ftime", ctypes.c_uint32),
        ("count_us", ctypes.c_uint32),
    ]


# ============================================================================
# Python Dataclass (사용하기 편한 형태)
# ============================================================================

@dataclass
class LoRaPacket:
    """수신된 LoRa 패킷 (Python 래핑)."""
    payload: bytes              # end device가 보낸 raw 데이터
    size: int                   # payload 길이
    freq_hz: int                # 수신 주파수 (Hz)
    rssi: float                 # 신호 세기 (dBm)
    snr: float                  # 신호 대 잡음비
    datarate: int               # Spreading Factor
    crc_ok: bool                # CRC 검증 결과
    count_us: int               # 내부 타임스탬프 (μs)
    rf_chain: int               # RF 체인 번호
    if_chain: int               # IF 체인 번호
    bandwidth: int              # 대역폭


# ============================================================================
# HAL Wrapper
# ============================================================================

class HalWrapper:
    """
    sx1302_hal (libloragw.so) Python 래퍼.

    SPI를 통해 RAK5146 칩을 제어합니다.
    HAL이 SPI 통신을 전부 추상화하므로, 직접 SPI를 다룰 필요 없습니다.

    Usage:
        hal = HalWrapper("/path/to/libloragw.so")
        hal.configure(spi_path="/dev/spidev0.0", freq_hz=923_300_000)
        hal.start()

        packets = hal.receive()
        for pkt in packets:
            print(pkt.payload, pkt.rssi, pkt.snr)

        hal.stop()
    """

    def __init__(self, lib_path: str = "libloragw.so"):
        self._lib: Optional[ctypes.CDLL] = None
        self._lib_path = lib_path
        self._started = False

    def load(self) -> bool:
        """HAL 라이브러리 로드."""
        try:
            if not os.path.exists(self._lib_path):
                logger.error(
                    f"[HAL] Library not found: {self._lib_path}"
                )
                return False

            self._lib = ctypes.CDLL(self._lib_path)
            version = self._lib.lgw_version_info
            version.restype = ctypes.c_char_p
            ver_str = version().decode('utf-8')
            logger.info(f"[HAL] Loaded libloragw {ver_str}")
            return True

        except OSError as e:
            logger.error(f"[HAL] Failed to load {self._lib_path}: {e}")
            return False

    def configure(
        self,
        spi_path: str = "/dev/spidev0.0",
        freq_hz: int = 923_300_000,
        radio_type: RadioType = RadioType.SX1250,
        lorawan_public: bool = False,
        clksrc: int = 0,
    ) -> bool:
        """
        RAK5146 설정.

        Args:
            spi_path: SPI 디바이스 경로
            freq_hz: 중심 주파수 (Hz) — KR920: 920.9~923.3 MHz
            radio_type: 라디오 칩 타입 (RAK5146 = SX1250)
            lorawan_public: LoRaWAN public 네트워크 여부 (raw LoRa = False)
            clksrc: 클럭 소스 RF 체인 (0 또는 1)
        """
        if not self._lib:
            logger.error("[HAL] Library not loaded")
            return False

        # 1. Board 설정
        board_conf = LgwConfBoard()
        board_conf.lorawan_public = lorawan_public
        board_conf.clksrc = clksrc
        board_conf.full_duplex = False
        board_conf.com_type = ComType.SPI
        board_conf.com_path = spi_path.encode('utf-8')

        ret = self._lib.lgw_board_setconf(ctypes.byref(board_conf))
        if ret != LGW_HAL_SUCCESS:
            logger.error("[HAL] lgw_board_setconf failed")
            return False

        # 2. RF 채널 설정 (2개 RF chain)
        for rf_chain in range(LGW_RF_CHAIN_NB):
            rf_conf = LgwConfRxRf()
            rf_conf.enable = True
            rf_conf.freq_hz = freq_hz
            rf_conf.rssi_offset = -215.4
            rf_conf.type = radio_type

            ret = self._lib.lgw_rxrf_setconf(rf_chain, ctypes.byref(rf_conf))
            if ret != LGW_HAL_SUCCESS:
                logger.error(f"[HAL] lgw_rxrf_setconf({rf_chain}) failed")
                return False

        # 3. Multi-SF 채널 설정 (8개 IF chain, SF7~SF12 동시 수신)
        # 채널 간격: 200kHz
        channel_offsets = [
            -300000, -100000, 100000, 300000,
            -300000, -100000, 100000, 300000,
        ]
        for i in range(LGW_MULTI_NB):
            if_conf = LgwConfRxIf()
            if_conf.enable = True
            if_conf.rf_chain = 0 if i < 4 else 1
            if_conf.freq_hz = channel_offsets[i]

            ret = self._lib.lgw_rxif_setconf(i, ctypes.byref(if_conf))
            if ret != LGW_HAL_SUCCESS:
                logger.error(f"[HAL] lgw_rxif_setconf({i}) failed")
                return False

        # 4. Demod 설정 (모든 SF 활성화)
        demod_conf = LgwConfDemod()
        demod_conf.multisf_datarate = 0xFF  # SF5~SF12 전부
        ret = self._lib.lgw_demod_setconf(ctypes.byref(demod_conf))
        if ret != LGW_HAL_SUCCESS:
            logger.error("[HAL] lgw_demod_setconf failed")
            return False

        logger.info(
            f"[HAL] Configured: SPI={spi_path}, "
            f"freq={freq_hz/1e6:.1f}MHz, radio={radio_type.name}"
        )
        return True

    def start(self) -> bool:
        """수신 시작."""
        if not self._lib:
            return False

        ret = self._lib.lgw_start()
        if ret != LGW_HAL_SUCCESS:
            logger.error("[HAL] lgw_start failed")
            return False

        self._started = True
        logger.info("[HAL] Concentrator started")
        return True

    def stop(self) -> bool:
        """수신 종료."""
        if not self._lib or not self._started:
            return False

        ret = self._lib.lgw_stop()
        self._started = False

        if ret != LGW_HAL_SUCCESS:
            logger.error("[HAL] lgw_stop failed")
            return False

        logger.info("[HAL] Concentrator stopped")
        return True

    def receive(self) -> List[LoRaPacket]:
        """
        수신된 패킷 조회 (non-blocking).

        Returns:
            수신된 LoRaPacket 리스트 (0~16개)
        """
        if not self._lib or not self._started:
            return []

        pkt_array = (LgwPktRx * LGW_PKT_MAX)()
        nb_pkt = self._lib.lgw_receive(LGW_PKT_MAX, pkt_array)

        if nb_pkt < 0:
            logger.error("[HAL] lgw_receive error")
            return []

        packets: List[LoRaPacket] = []
        for i in range(nb_pkt):
            raw = pkt_array[i]
            pkt = LoRaPacket(
                payload=bytes(raw.payload[:raw.size]),
                size=raw.size,
                freq_hz=raw.freq_hz,
                rssi=raw.rssic,
                snr=raw.snr,
                datarate=raw.datarate,
                crc_ok=(raw.status == CrcStatus.CRC_OK),
                count_us=raw.count_us,
                rf_chain=raw.rf_chain,
                if_chain=raw.if_chain,
                bandwidth=raw.bandwidth,
            )
            packets.append(pkt)

        if packets:
            logger.debug(f"[HAL] Received {len(packets)} packet(s)")

        return packets

    def get_temperature(self) -> Optional[float]:
        """SX1303 칩 온도 조회."""
        if not self._lib or not self._started:
            return None

        temp = ctypes.c_float()
        ret = self._lib.lgw_get_temperature(ctypes.byref(temp))
        if ret != LGW_HAL_SUCCESS:
            return None
        return temp.value

    @property
    def is_started(self) -> bool:
        return self._started
