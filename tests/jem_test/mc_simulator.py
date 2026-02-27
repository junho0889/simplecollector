#!/usr/bin/env python3
"""
MC Protocol Simulator for JEM Test Environment

Serves 10 PLCs on ports 5001-5010, each with its own D registers and L/M bit memory.
Loads test_data.csv and tag definitions from config/ to populate PLC registers
with reverse-scaled raw values. Data frames cycle every ~1 second.

Usage:
    python mc_simulator.py [--config-dir CONFIG_DIR] [--data-file DATA_FILE]
                           [--base-port BASE_PORT] [--cycle-interval SECONDS]
                           [--host HOST]

Designed to run standalone with no external dependencies beyond Python stdlib.
"""

import asyncio
import csv
import glob
import logging
import os
import random
import signal
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mc_simulator")


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------
@dataclass
class TagInfo:
    """Tag definition loaded from CSV."""
    tag_id: int
    tag_name: str
    memory: str       # D, L, M
    address: int
    data_type: str    # uint16, int16, uint32, int32, float32, float64, bool, string
    collection_group: str  # plc_data, alm, log
    scale: float = 1.0
    offset: float = 0.0
    decimals: int = 0
    word_length: int = 0


@dataclass
class PLCMemory:
    """Simulated PLC memory for one PLC."""
    plc_id: int
    name: str
    d_regs: Dict[int, int] = field(default_factory=dict)  # D register: addr -> uint16
    l_bits: Dict[int, bool] = field(default_factory=dict)  # L device: addr -> bool
    m_bits: Dict[int, bool] = field(default_factory=dict)  # M device: addr -> bool
    x_bits: Dict[int, bool] = field(default_factory=dict)  # X device: addr -> bool
    y_bits: Dict[int, bool] = field(default_factory=dict)  # Y device: addr -> bool

    # Tag definitions for this PLC
    tags_by_id: Dict[int, TagInfo] = field(default_factory=dict)
    alm_addresses: List[Tuple[str, int]] = field(default_factory=list)  # (memory, address)

    def get_d_reg(self, addr: int) -> int:
        """Get D register value (default 0)."""
        return self.d_regs.get(addr, 0)

    def get_bit(self, device: str, addr: int) -> bool:
        """Get bit device value."""
        if device == "L":
            return self.l_bits.get(addr, False)
        elif device == "M":
            return self.m_bits.get(addr, False)
        elif device == "X":
            return self.x_bits.get(addr, False)
        elif device == "Y":
            return self.y_bits.get(addr, False)
        return False


@dataclass
class DataRow:
    """One row from test_data.csv."""
    timestamp: str
    plc_id: int
    tag_id: int
    v_bool: Optional[bool]
    v_int: Optional[int]
    v_bigint: Optional[int]
    v_float: Optional[float]
    v_text: Optional[str]
    quality_code: int


# ---------------------------------------------------------------------------
# Reverse Scaling
# ---------------------------------------------------------------------------
def reverse_scale(
    final_value: float,
    data_type: str,
    scale: float,
    offset: float,
    decimals: int,
) -> float:
    """
    Convert a scaled (final) value back to the raw PLC register value.

    Forward scaling in the collector:
        scaled = (raw * scale) + offset
        if decimals > 0:
            if float type: scaled = round(scaled, decimals)
            else (int type): scaled = round(raw / (10^decimals), decimals)

    Reverse:
        For float types with decimals: raw is unchanged (round is lossy but we keep as-is)
        For int types with decimals: raw_scaled = final_value * (10^decimals)
        Then: raw = (raw_scaled - offset) / scale
    """
    if decimals and decimals > 0:
        if data_type in ("float32", "float64"):
            raw_scaled = final_value  # float types: just had round()
        else:
            raw_scaled = final_value * (10 ** decimals)  # reverse: / 10^d -> * 10^d
    else:
        raw_scaled = final_value

    # Reverse scale/offset: raw = (scaled - offset) / scale
    if scale != 0:
        raw = (raw_scaled - offset) / scale
    else:
        raw = raw_scaled - offset

    return raw


# ---------------------------------------------------------------------------
# Register Writing
# ---------------------------------------------------------------------------
def write_uint32(d_regs: Dict[int, int], addr: int, value: int) -> None:
    """Write uint32 as 2 words (little-endian word order, Mitsubishi default)."""
    value = int(value) & 0xFFFFFFFF
    d_regs[addr] = value & 0xFFFF          # low word
    d_regs[addr + 1] = (value >> 16) & 0xFFFF  # high word


def write_int32(d_regs: Dict[int, int], addr: int, value: int) -> None:
    """Write int32 as 2 words."""
    value = int(value)
    # Convert to unsigned 32-bit representation
    if value < 0:
        value = value + 0x100000000
    write_uint32(d_regs, addr, value)


def write_float32(d_regs: Dict[int, int], addr: int, value: float) -> None:
    """Write float32 as 2 words (little-endian)."""
    bytes_ = struct.pack("<f", float(value))
    w1, w2 = struct.unpack("<HH", bytes_)
    d_regs[addr] = w1      # low word
    d_regs[addr + 1] = w2  # high word


def write_int16(d_regs: Dict[int, int], addr: int, value: int) -> None:
    """Write int16 as 1 word."""
    value = int(value)
    d_regs[addr] = struct.unpack("<H", struct.pack("<h", value))[0]


def write_uint16(d_regs: Dict[int, int], addr: int, value: int) -> None:
    """Write uint16 as 1 word."""
    d_regs[addr] = int(value) & 0xFFFF


def write_string(d_regs: Dict[int, int], addr: int, text: str, word_length: int = 0) -> None:
    """Write string to D registers (LE: low byte = first char)."""
    if word_length <= 0:
        word_length = (len(text) + 1) // 2  # ceil(len/2)
    for i in range(0, word_length * 2, 2):
        low = ord(text[i]) if i < len(text) else 0
        high = ord(text[i + 1]) if i + 1 < len(text) else 0
        d_regs[addr + i // 2] = low | (high << 8)


def write_register(
    d_regs: Dict[int, int],
    addr: int,
    raw_value: float,
    data_type: str,
    word_length: int = 0,
    text_value: Optional[str] = None,
) -> None:
    """Write a value to D registers according to its data type."""
    if data_type == "string":
        if text_value is not None:
            write_string(d_regs, addr, text_value, word_length)
        return

    if data_type == "uint32":
        write_uint32(d_regs, addr, int(round(raw_value)))
    elif data_type == "int32":
        write_int32(d_regs, addr, int(round(raw_value)))
    elif data_type == "float32":
        write_float32(d_regs, addr, raw_value)
    elif data_type == "float64":
        # float64 -> 4 words
        bytes_ = struct.pack("<d", float(raw_value))
        words = struct.unpack("<HHHH", bytes_)
        for i, w in enumerate(words):
            d_regs[addr + i] = w
    elif data_type == "int16":
        write_int16(d_regs, addr, int(round(raw_value)))
    elif data_type == "uint16":
        write_uint16(d_regs, addr, int(round(raw_value)))


# ---------------------------------------------------------------------------
# CSV Loading
# ---------------------------------------------------------------------------
def load_tags_csv(filepath: str) -> List[TagInfo]:
    """Load tag definitions from a CSV file."""
    tags = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader)

        # Normalize header names (strip whitespace)
        header = [h.strip() for h in header]

        for row in reader:
            if not row or not row[0].strip():
                continue
            try:
                tag_id = int(row[0].strip())
                tag_name = row[1].strip()
                memory = row[2].strip()
                address = int(row[3].strip())
                data_type = row[4].strip()
                collection_group = row[5].strip() if len(row) > 5 else ""
                scale = float(row[6]) if len(row) > 6 and row[6].strip() else 1.0
                offset = float(row[7]) if len(row) > 7 and row[7].strip() else 0.0
                decimals = int(row[8]) if len(row) > 8 and row[8].strip() else 0
                word_length = int(row[9]) if len(row) > 9 and row[9].strip() else 0

                tags.append(TagInfo(
                    tag_id=tag_id,
                    tag_name=tag_name,
                    memory=memory,
                    address=address,
                    data_type=data_type,
                    collection_group=collection_group,
                    scale=scale,
                    offset=offset,
                    decimals=decimals,
                    word_length=word_length,
                ))
            except (ValueError, IndexError) as e:
                logger.warning(f"Skipping malformed row in {filepath}: {row} ({e})")

    return tags


def load_test_data(filepath: str) -> List[DataRow]:
    """Load test_data.csv rows."""
    rows = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 9:
                logger.warning(f"test_data.csv line {line_num}: not enough columns")
                continue
            try:
                timestamp = parts[0].strip()
                plc_id = int(parts[1].strip())
                tag_id = int(parts[2].strip())

                v_bool = None
                v_int = None
                v_bigint = None
                v_float = None
                v_text = None

                if parts[3].strip():
                    v_bool = parts[3].strip().lower() in ("true", "1")
                if parts[4].strip():
                    v_int = int(parts[4].strip())
                if parts[5].strip():
                    v_bigint = int(parts[5].strip())
                if parts[6].strip():
                    v_float = float(parts[6].strip())
                if parts[7].strip():
                    v_text = parts[7].strip()

                quality_code = int(parts[8].strip()) if parts[8].strip() else 1

                rows.append(DataRow(
                    timestamp=timestamp,
                    plc_id=plc_id,
                    tag_id=tag_id,
                    v_bool=v_bool,
                    v_int=v_int,
                    v_bigint=v_bigint,
                    v_float=v_float,
                    v_text=v_text,
                    quality_code=quality_code,
                ))
            except (ValueError, IndexError) as e:
                logger.warning(f"test_data.csv line {line_num}: parse error ({e})")

    return rows


def group_by_timestamp(rows: List[DataRow]) -> List[Tuple[str, List[DataRow]]]:
    """Group data rows by timestamp, preserving order."""
    groups: List[Tuple[str, List[DataRow]]] = []
    current_ts = None
    current_group: List[DataRow] = []

    for row in rows:
        if row.timestamp != current_ts:
            if current_group:
                groups.append((current_ts, current_group))
            current_ts = row.timestamp
            current_group = [row]
        else:
            current_group.append(row)

    if current_group:
        groups.append((current_ts, current_group))

    return groups


def get_value_from_row(row: DataRow) -> Tuple[Optional[float], Optional[str]]:
    """Extract the numeric value or text from a data row."""
    # Priority: v_bigint > v_int > v_float > v_bool
    text_val = row.v_text
    num_val = None

    if row.v_bigint is not None:
        num_val = float(row.v_bigint)
    elif row.v_int is not None:
        num_val = float(row.v_int)
    elif row.v_float is not None:
        num_val = row.v_float
    elif row.v_bool is not None:
        num_val = 1.0 if row.v_bool else 0.0

    return num_val, text_val


# ---------------------------------------------------------------------------
# PLC Configuration Loading
# ---------------------------------------------------------------------------
# The mapping from plc_id to tags CSV is determined by reading collector YAMLs.
# But to keep this simulator self-contained (no pyyaml), we define the mapping
# based on the known JEM setup. The mapping is also discoverable from the
# file naming convention and the collector configs.

# PLC ID -> tags CSV file basename mapping (from collector_plcN.yaml configs)
PLC_TAGS_MAPPING = {
    1: "tags_04_PLC-A.csv",
    2: "tags_02_PLC-B.csv",
    3: "tags_05_PLC-C.csv",
    4: "tags_10_PLC-D.csv",
    5: "tags_01_PLC-EFG.csv",
    6: "tags_06_PLC-H.csv",
    7: "tags_07_PLC-I.csv",
    8: "tags_03_PLC-J.csv",
    9: "tags_08_PLC-K.csv",
    10: "tags_09_PLC-L.csv",
}

PLC_NAMES = {
    1: "PLC-A",
    2: "PLC-B",
    3: "PLC-C",
    4: "PLC-D",
    5: "PLC-EFG",
    6: "PLC-H",
    7: "PLC-I",
    8: "PLC-J",
    9: "PLC-K",
    10: "PLC-L",
}


def build_plc_memories(config_dir: str) -> Dict[int, PLCMemory]:
    """Build PLCMemory instances for all 10 PLCs from tag CSV files."""
    memories: Dict[int, PLCMemory] = {}

    for plc_id in range(1, 11):
        csv_file = PLC_TAGS_MAPPING.get(plc_id)
        if not csv_file:
            continue

        filepath = os.path.join(config_dir, csv_file)
        if not os.path.exists(filepath):
            logger.warning(f"Tags CSV not found for PLC {plc_id}: {filepath}")
            continue

        tags = load_tags_csv(filepath)
        plc_mem = PLCMemory(
            plc_id=plc_id,
            name=PLC_NAMES.get(plc_id, f"PLC-{plc_id}"),
        )

        alm_addresses: List[Tuple[str, int]] = []
        for tag in tags:
            plc_mem.tags_by_id[tag.tag_id] = tag
            if tag.collection_group == "alm" and tag.data_type == "bool":
                alm_addresses.append((tag.memory, tag.address))

        plc_mem.alm_addresses = alm_addresses
        memories[plc_id] = plc_mem

    return memories


def initialize_alarm_bits(memories: Dict[int, PLCMemory], on_ratio: float = 0.05) -> None:
    """Initialize alarm bits with a random percentage ON."""
    for plc_id, plc_mem in memories.items():
        on_count = 0
        for memory, addr in plc_mem.alm_addresses:
            is_on = random.random() < on_ratio
            if memory == "L":
                plc_mem.l_bits[addr] = is_on
            elif memory == "M":
                plc_mem.m_bits[addr] = is_on
            if is_on:
                on_count += 1

        total = len(plc_mem.alm_addresses)
        logger.info(
            f"  PLC {plc_id} ({plc_mem.name}): {on_count}/{total} alarm bits ON "
            f"({on_count/total*100:.1f}%)" if total > 0 else
            f"  PLC {plc_id} ({plc_mem.name}): no alarm bits defined"
        )


def apply_data_frame(
    memories: Dict[int, PLCMemory],
    frame_rows: List[DataRow],
) -> None:
    """Apply one data frame (a timestamp's rows) to PLC registers."""
    for row in frame_rows:
        plc_mem = memories.get(row.plc_id)
        if plc_mem is None:
            continue

        tag = plc_mem.tags_by_id.get(row.tag_id)
        if tag is None:
            continue

        num_val, text_val = get_value_from_row(row)

        if tag.data_type == "string":
            if text_val is not None and tag.memory == "D":
                write_string(plc_mem.d_regs, tag.address, text_val, tag.word_length)
            continue

        if num_val is None:
            continue

        if tag.memory == "D":
            # Reverse scale the final value back to raw PLC register value
            raw = reverse_scale(num_val, tag.data_type, tag.scale, tag.offset, tag.decimals)
            write_register(
                plc_mem.d_regs,
                tag.address,
                raw,
                tag.data_type,
                tag.word_length,
            )
        elif tag.memory in ("L", "M"):
            # Bit device
            bit_val = bool(num_val)
            if tag.memory == "L":
                plc_mem.l_bits[tag.address] = bit_val
            else:
                plc_mem.m_bits[tag.address] = bit_val


# ---------------------------------------------------------------------------
# MC Protocol Binary 3E Frame Handler
# ---------------------------------------------------------------------------

# Device code mapping (MC Protocol binary)
DEVICE_CODES = {
    0xA8: "D",   # D register (word device)
    0x92: "L",   # L latch relay (bit device)
    0x90: "M",   # M internal relay (bit device)
    0x9C: "X",   # X input (bit device)
    0x9D: "Y",   # Y output (bit device)
    0xB4: "W",   # W link register (word device)
    0xB0: "R",   # R file register (word device)
}

# Bit devices (need bit-to-word packing)
BIT_DEVICES = {"L", "M", "X", "Y"}
# Word devices
WORD_DEVICES = {"D", "W", "R"}


def pack_bits_to_words(bits: Dict[int, bool], start_addr: int, count: int) -> bytes:
    """
    Pack bit device values into words (16 bits per word).
    count is in bit units. Return ceil(count/16) words as bytes.
    """
    num_words = (count + 15) // 16
    result = bytearray()
    for word_idx in range(num_words):
        word_val = 0
        for bit_idx in range(16):
            bit_addr = start_addr + word_idx * 16 + bit_idx
            if bit_addr < start_addr + count:
                if bits.get(bit_addr, False):
                    word_val |= (1 << bit_idx)
        result.extend(struct.pack("<H", word_val))
    return bytes(result)


def handle_batch_read(plc_mem: PLCMemory, data: bytes) -> bytes:
    """
    Handle MC Protocol batch read command (0x0401).

    Request data format (after command+subcommand):
        start_address: 3 bytes LE (first 3 bytes)
        device_code: 1 byte
        count: 2 bytes LE (number of points)

    For word devices: count = number of words to read
    For bit devices: count = number of bits, response packs into words
    """
    if len(data) < 6:
        logger.warning("Batch read: insufficient data")
        return build_error_response(0xC059)  # parameter error

    # Parse start address (3 bytes LE) + device code (1 byte)
    start_addr = data[0] | (data[1] << 8) | (data[2] << 16)
    device_code = data[3]
    count = struct.unpack_from("<H", data, 4)[0]

    device_name = DEVICE_CODES.get(device_code, f"0x{device_code:02X}")

    if device_name in BIT_DEVICES:
        # Bit device read -> pack bits into words
        if device_name == "L":
            bits = plc_mem.l_bits
        elif device_name == "M":
            bits = plc_mem.m_bits
        elif device_name == "X":
            bits = plc_mem.x_bits
        else:
            bits = plc_mem.y_bits
        response_data = pack_bits_to_words(bits, start_addr, count)

    elif device_name in WORD_DEVICES:
        # Word device read
        result = bytearray()
        for i in range(count):
            addr = start_addr + i
            if device_name == "D":
                word = plc_mem.get_d_reg(addr)
            else:
                word = 0  # W, R devices default to 0
            result.extend(struct.pack("<H", word & 0xFFFF))
        response_data = bytes(result)
    else:
        logger.warning(f"Unknown device code: 0x{device_code:02X}")
        return build_error_response(0xC061)  # device specification error

    return build_success_response(response_data)


def build_success_response(data: bytes) -> bytes:
    """Build MC Protocol 3E binary success response frame."""
    # Subheader: 0xD000 (2 bytes, big-endian as per MC protocol spec)
    subheader = b"\xD0\x00"
    # Network number: 0x00
    network_no = b"\x00"
    # PC number: 0xFF
    pc_no = b"\xFF"
    # Unit I/O: 0xFF03 (2 bytes LE)
    unit_io = struct.pack("<H", 0x03FF)
    # Unit station: 0x00
    unit_station = b"\x00"
    # Data length: 2 bytes (end code) + data
    data_length = struct.pack("<H", 2 + len(data))
    # End code: 0x0000 (success)
    end_code = struct.pack("<H", 0x0000)

    return subheader + network_no + pc_no + unit_io + unit_station + data_length + end_code + data


def build_error_response(error_code: int) -> bytes:
    """Build MC Protocol 3E binary error response frame."""
    subheader = b"\xD0\x00"
    network_no = b"\x00"
    pc_no = b"\xFF"
    unit_io = struct.pack("<H", 0x03FF)
    unit_station = b"\x00"
    data_length = struct.pack("<H", 2)  # just end code
    end_code = struct.pack("<H", error_code)

    return subheader + network_no + pc_no + unit_io + unit_station + data_length + end_code


def parse_mc_request(data: bytes) -> Optional[Tuple[int, int, bytes]]:
    """
    Parse MC Protocol 3E binary request frame.

    Returns (command, subcommand, request_data) or None on error.

    Frame structure:
        [0-1]   Subheader: 0x5000
        [2]     Network number
        [3]     PC number
        [4-5]   Unit I/O (2 bytes LE)
        [6]     Unit station
        [7-8]   Data length (2 bytes LE) - includes monitoring timer + command onwards
        [9-10]  Monitoring timer (2 bytes LE)
        [11-12] Command (2 bytes LE)
        [13-14] Subcommand (2 bytes LE)
        [15+]   Request data
    """
    if len(data) < 15:
        return None

    # Verify subheader (0x5000 - note: big-endian in the frame)
    if data[0] != 0x50 or data[1] != 0x00:
        logger.warning(f"Invalid subheader: 0x{data[0]:02X}{data[1]:02X}")
        return None

    # Data length
    data_length = struct.unpack_from("<H", data, 7)[0]

    # Command and subcommand
    command = struct.unpack_from("<H", data, 11)[0]
    subcommand = struct.unpack_from("<H", data, 13)[0]

    # Request data starts at offset 15
    request_data = data[15:]

    return command, subcommand, request_data


# ---------------------------------------------------------------------------
# TCP Connection Handler
# ---------------------------------------------------------------------------
class MCConnectionHandler:
    """Handles a single TCP connection for MC Protocol."""

    def __init__(self, plc_mem: PLCMemory, reader: asyncio.StreamReader,
                 writer: asyncio.StreamWriter):
        self.plc_mem = plc_mem
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")

    async def handle(self) -> None:
        """Process incoming MC Protocol requests."""
        logger.debug(f"PLC {self.plc_mem.plc_id}: Connection from {self.peer}")
        try:
            while True:
                # Read the MC Protocol frame header (9 bytes minimum to get data length)
                header = await self.reader.readexactly(9)

                # Parse data length from header
                data_length = struct.unpack_from("<H", header, 7)[0]

                # Read the rest of the frame
                rest = await self.reader.readexactly(data_length)

                # Combine into full frame
                full_frame = header + rest

                # Parse the request
                parsed = parse_mc_request(full_frame)
                if parsed is None:
                    logger.warning(
                        f"PLC {self.plc_mem.plc_id}: Invalid request from {self.peer}"
                    )
                    response = build_error_response(0xC059)
                    self.writer.write(response)
                    await self.writer.drain()
                    continue

                command, subcommand, request_data = parsed

                if command == 0x0401:
                    # Batch read
                    response = handle_batch_read(self.plc_mem, request_data)
                elif command == 0x1401:
                    # Batch write (just acknowledge, we ignore writes in the simulator)
                    response = build_success_response(b"")
                else:
                    logger.warning(
                        f"PLC {self.plc_mem.plc_id}: Unsupported command 0x{command:04X}"
                    )
                    response = build_error_response(0xC059)

                self.writer.write(response)
                await self.writer.drain()

        except asyncio.IncompleteReadError:
            logger.debug(f"PLC {self.plc_mem.plc_id}: Connection closed by {self.peer}")
        except ConnectionResetError:
            logger.debug(f"PLC {self.plc_mem.plc_id}: Connection reset by {self.peer}")
        except Exception as e:
            logger.error(f"PLC {self.plc_mem.plc_id}: Error handling {self.peer}: {e}")
        finally:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Simulator Core
# ---------------------------------------------------------------------------
class MCSimulator:
    """
    MC Protocol Simulator serving 10 PLCs.

    Each PLC listens on its own TCP port and serves MC Protocol binary 3E frames.
    Data from test_data.csv cycles through the PLC registers every cycle_interval seconds.
    """

    def __init__(
        self,
        config_dir: str,
        data_file: str,
        base_port: int = 5001,
        cycle_interval: float = 1.0,
        host: str = "0.0.0.0",
    ):
        self.config_dir = config_dir
        self.data_file = data_file
        self.base_port = base_port
        self.cycle_interval = cycle_interval
        self.host = host

        self.memories: Dict[int, PLCMemory] = {}
        self.data_frames: List[Tuple[str, List[DataRow]]] = []
        self.frame_index: int = 0
        self.servers: List[asyncio.AbstractServer] = []
        self.running: bool = False
        self._connection_counts: Dict[int, int] = defaultdict(int)

    def load(self) -> None:
        """Load configuration and test data."""
        logger.info("=" * 70)
        logger.info("MC Protocol Simulator - JEM Test Environment")
        logger.info("=" * 70)

        # Load PLC memories from tag CSVs
        logger.info(f"Loading tag definitions from: {self.config_dir}")
        self.memories = build_plc_memories(self.config_dir)

        if not self.memories:
            logger.error("No PLC memories loaded! Check config directory.")
            sys.exit(1)

        for plc_id in sorted(self.memories.keys()):
            plc_mem = self.memories[plc_id]
            plc_data_tags = sum(
                1 for t in plc_mem.tags_by_id.values()
                if t.collection_group == "plc_data"
            )
            alm_tags = len(plc_mem.alm_addresses)
            logger.info(
                f"  PLC {plc_id:2d} ({plc_mem.name:>8s}): "
                f"{plc_data_tags} data tags, {alm_tags} alarm tags"
            )

        # Initialize alarm bits with ~5% random ON
        logger.info("")
        logger.info("Initializing alarm bits (5% random ON):")
        initialize_alarm_bits(self.memories, on_ratio=0.05)

        # Load test data
        logger.info("")
        logger.info(f"Loading test data from: {self.data_file}")
        rows = load_test_data(self.data_file)
        logger.info(f"  Total rows: {len(rows)}")

        # Group by timestamp
        self.data_frames = group_by_timestamp(rows)
        logger.info(f"  Unique timestamps (frames): {len(self.data_frames)}")

        # Count frames per PLC
        plc_frame_counts: Dict[int, int] = defaultdict(int)
        for ts, frame_rows in self.data_frames:
            plc_ids_in_frame = set(r.plc_id for r in frame_rows)
            for pid in plc_ids_in_frame:
                plc_frame_counts[pid] += 1
        for plc_id in sorted(plc_frame_counts.keys()):
            logger.info(f"  PLC {plc_id:2d}: {plc_frame_counts[plc_id]} frames")

        # Apply the first frame to initialize registers
        if self.data_frames:
            _, first_rows = self.data_frames[0]
            apply_data_frame(self.memories, first_rows)
            self.frame_index = 1
            logger.info("")
            logger.info("Applied initial data frame to PLC registers.")

        logger.info("")

    async def start(self) -> None:
        """Start all TCP servers and the data cycling task."""
        self.running = True

        # Start TCP servers for each PLC
        logger.info("Starting MC Protocol TCP servers:")
        for plc_id in range(1, 11):
            port = self.base_port + plc_id - 1
            plc_mem = self.memories.get(plc_id)

            if plc_mem is None:
                logger.warning(f"  Port {port} -> PLC {plc_id}: SKIPPED (no config)")
                continue

            server = await asyncio.start_server(
                lambda r, w, pm=plc_mem: self._handle_connection(pm, r, w),
                self.host,
                port,
            )
            self.servers.append(server)
            logger.info(f"  Port {port} -> PLC {plc_id:2d} ({plc_mem.name})")

        logger.info("")
        logger.info(
            f"All servers started. Data cycling every {self.cycle_interval}s "
            f"({len(self.data_frames)} frames, loop mode)."
        )
        logger.info("=" * 70)
        logger.info("")

        # Start data cycling task
        asyncio.create_task(self._data_cycle_loop())

    async def _handle_connection(
        self,
        plc_mem: PLCMemory,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a new TCP connection."""
        self._connection_counts[plc_mem.plc_id] += 1
        handler = MCConnectionHandler(plc_mem, reader, writer)
        await handler.handle()
        self._connection_counts[plc_mem.plc_id] -= 1

    async def _data_cycle_loop(self) -> None:
        """Cycle through data frames at the configured interval."""
        loop_count = 0
        while self.running:
            await asyncio.sleep(self.cycle_interval)

            if not self.data_frames:
                continue

            # Get next frame
            ts, frame_rows = self.data_frames[self.frame_index]

            # Apply frame to PLC memories
            apply_data_frame(self.memories, frame_rows)

            loop_count += 1
            if loop_count % 60 == 0:  # Log every 60 cycles (~1 minute)
                active_conns = sum(self._connection_counts.values())
                logger.info(
                    f"Data cycle #{loop_count}: frame {self.frame_index + 1}/"
                    f"{len(self.data_frames)}, "
                    f"active connections: {active_conns}"
                )

            # Advance to next frame (loop back to start)
            self.frame_index = (self.frame_index + 1) % len(self.data_frames)

    async def stop(self) -> None:
        """Stop all servers gracefully."""
        logger.info("Shutting down MC Protocol Simulator...")
        self.running = False

        for server in self.servers:
            server.close()
            await server.wait_closed()

        logger.info("All servers stopped.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def find_config_dir() -> str:
    """Find the config directory relative to this script."""
    # Try paths relative to this script's location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "config"),
        os.path.join(script_dir, "..", "..", "config"),
        "/app/config",  # Docker path
        "config",  # Current directory
    ]
    for path in candidates:
        if os.path.isdir(path):
            # Check if tags CSV files exist here
            if glob.glob(os.path.join(path, "tags_*.csv")):
                return os.path.abspath(path)
    return os.path.join(script_dir, "config")


def find_data_file() -> str:
    """Find the test_data.csv file."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(script_dir, "test_data.csv"),
        os.path.join(script_dir, "..", "..", "test_data.csv"),
        "/app/test_data.csv",  # Docker path
        "test_data.csv",  # Current directory
    ]
    for path in candidates:
        if os.path.isfile(path):
            return os.path.abspath(path)
    return os.path.join(script_dir, "test_data.csv")


async def main() -> None:
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="MC Protocol Simulator for JEM Test Environment"
    )
    parser.add_argument(
        "--config-dir", "-c",
        default=None,
        help="Directory containing tags_*.csv files (default: auto-detect)",
    )
    parser.add_argument(
        "--data-file", "-d",
        default=None,
        help="Path to test_data.csv (default: auto-detect)",
    )
    parser.add_argument(
        "--base-port", "-p",
        type=int,
        default=5001,
        help="Base port for PLC 1 (default: 5001, PLCs use 5001-5010)",
    )
    parser.add_argument(
        "--cycle-interval", "-i",
        type=float,
        default=1.0,
        help="Data cycle interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind servers to (default: 0.0.0.0)",
    )

    args = parser.parse_args()

    config_dir = args.config_dir or find_config_dir()
    data_file = args.data_file or find_data_file()

    # Create simulator
    simulator = MCSimulator(
        config_dir=config_dir,
        data_file=data_file,
        base_port=args.base_port,
        cycle_interval=args.cycle_interval,
        host=args.host,
    )

    # Load configuration and data
    simulator.load()

    # Setup signal handlers for graceful shutdown
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Received shutdown signal")
        stop_event.set()

    # Register signal handlers (Unix only; on Windows use alternative)
    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    else:
        # On Windows, signal.signal works for SIGINT (Ctrl+C)
        original_sigint = signal.getsignal(signal.SIGINT)

        def _win_handler(signum, frame):
            stop_event.set()

        signal.signal(signal.SIGINT, _win_handler)

    # Start servers
    await simulator.start()

    # Wait for shutdown signal
    await stop_event.wait()

    # Graceful shutdown
    await simulator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
