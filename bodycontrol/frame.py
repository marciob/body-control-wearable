"""WHOOP 5.0 / MG wire framing (the "puffin" envelope).

Frame layout, all integers little-endian:
  [0]     0xAA            start of frame
  [1]     0x01            format
  [2:4]   decl_len u16    = len(inner) + 4
  [4:6]   header          = 00 01
  [6:8]   crc16           CRC16-Modbus over bytes [0:6]
  [8:]    inner           = type u8, seq u8, cmd u8, payload...
  tail    crc32 u32       zlib CRC-32 over inner
  total length = decl_len + 8

Only the CRC32 protects the payload. Reject every frame whose CRC32 fails.
Facts from community reverse engineering; see README, "Sources".
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from enum import IntEnum

SOF = 0xAA
FORMAT = 0x01
HEADER = b"\x00\x01"
INNER_OFF = 8
MAX_FRAME = 4096

# The fixed 16-byte session opener. A type-35 COMMAND, cmd 145, payload 0x01.
CLIENT_HELLO = bytes.fromhex("aa0108000001e67123019101363e5c8d")
CLIENT_HELLO_CMD = 145


class PacketType(IntEnum):
    COMMAND = 35
    COMMAND_RESPONSE = 36
    PUFFIN_COMMAND = 37
    PUFFIN_COMMAND_RESPONSE = 38  # alias of COMMAND_RESPONSE on 5.0
    REALTIME_DATA = 40
    REALTIME_RAW_DATA = 43
    HISTORICAL_DATA = 47
    EVENT = 48
    METADATA = 49
    CONSOLE_LOGS = 50
    REALTIME_IMU = 51
    HISTORICAL_IMU = 52
    RELATIVE_PUFFIN_EVENTS = 53
    PUFFIN_EVENTS = 54
    PUFFIN_METADATA = 56  # alias of METADATA on 5.0


class Command(IntEnum):
    """Read-mostly subset. The 5.0 accepts the 4.0 command numbers."""

    TOGGLE_REALTIME_HR = 3
    REPORT_VERSION_INFO = 7
    SET_CLOCK = 10
    GET_CLOCK = 11
    SEND_HISTORICAL_DATA = 22
    HISTORICAL_DATA_RESULT = 23  # the per-chunk ack; advances the strap's trim cursor
    GET_BATTERY_LEVEL = 26
    GET_DATA_RANGE = 34
    GET_HELLO = 35


class MetaType(IntEnum):
    HISTORY_START = 1
    HISTORY_END = 2
    HISTORY_COMPLETE = 3


META_TYPE_OFF = 10
META_TRIM_OFF = 21
END_DATA_LEN = 8


def crc16_modbus(data: bytes) -> int:
    """CRC16-Modbus: poly 0xA001 (reflected), init 0xFFFF, no final xor."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def build_command(cmd: int, seq: int = 0, payload: bytes = b"\x00", type_: int = PacketType.COMMAND) -> bytes:
    """Build one command frame ready to write to the command characteristic."""
    inner = bytes([type_ & 0xFF, seq & 0xFF, cmd & 0xFF]) + bytes(payload)
    # The strap rejects an inner record that is not 4-byte aligned (NOOP pad4, #48).
    inner += bytes((4 - len(inner) % 4) % 4)
    decl = len(inner) + 4
    head = bytes([SOF, FORMAT, decl & 0xFF, (decl >> 8) & 0xFF]) + HEADER
    c16 = crc16_modbus(head)
    frame = head + c16.to_bytes(2, "little") + inner + crc32(inner).to_bytes(4, "little")
    return frame


def verify_frame(frame: bytes) -> bool:
    """True when length, CRC16 header and CRC32 trailer all match."""
    if len(frame) < 12 or frame[0] != SOF:
        return False
    decl = frame[2] | (frame[3] << 8)
    total = decl + 8
    if total != len(frame):
        return False
    if crc16_modbus(frame[0:6]) != (frame[6] | (frame[7] << 8)):
        return False
    inner = frame[INNER_OFF : total - 4]
    return crc32(inner) == int.from_bytes(frame[total - 4 : total], "little")


@dataclass(frozen=True)
class Frame:
    raw: bytes
    type: int
    seq: int
    cmd: int
    payload: bytes
    crc_ok: bool

    @property
    def type_name(self) -> str:
        try:
            return PacketType(self.type).name
        except ValueError:
            return f"TYPE_{self.type}"


def parse_frame(frame: bytes) -> Frame | None:
    """Split a complete frame into its fields. Return None if it is too short."""
    if len(frame) < INNER_OFF + 3 + 4 or frame[0] != SOF:
        return None
    decl = frame[2] | (frame[3] << 8)
    total = decl + 8
    inner_end = min(total, len(frame)) - 4
    inner = frame[INNER_OFF:inner_end]
    if len(inner) < 3:
        return None
    return Frame(
        raw=bytes(frame),
        type=inner[0],
        seq=inner[1],
        cmd=inner[2],
        payload=bytes(inner[3:]),
        crc_ok=verify_frame(frame),
    )


def history_end_data(frame: bytes) -> bytes | None:
    """Return the 8-byte trim token of a CRC-valid METADATA HISTORY_END frame, else None."""
    if len(frame) < META_TRIM_OFF + END_DATA_LEN:
        return None
    if frame[INNER_OFF] not in (PacketType.METADATA, PacketType.PUFFIN_METADATA):
        return None
    if frame[META_TYPE_OFF] != MetaType.HISTORY_END:
        return None
    if not verify_frame(frame):
        return None
    return bytes(frame[META_TRIM_OFF : META_TRIM_OFF + END_DATA_LEN])


def build_history_ack(end_data: bytes, seq: int = 0) -> bytes:
    """HISTORICAL_DATA_RESULT(23) with payload 0x01 + the 8-byte trim token.

    WARNING: the strap trims the acknowledged chunk from its own store.
    Write it only after the chunk's records are saved on disk.
    """
    if len(end_data) != END_DATA_LEN:
        raise ValueError("end_data must be 8 bytes")
    return build_command(Command.HISTORICAL_DATA_RESULT, seq=seq, payload=b"\x01" + bytes(end_data))


class Reassembler:
    """Join BLE notifications into complete frames. Use one per notify characteristic."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        self.buf.extend(data)
        out: list[bytes] = []
        while True:
            sof = self.buf.find(SOF)
            if sof == -1:
                self.buf.clear()
                break
            if sof > 0:
                del self.buf[:sof]
            if len(self.buf) < 4:
                break
            total = (self.buf[2] | (self.buf[3] << 8)) + 8
            if total < 12 or total > MAX_FRAME:
                del self.buf[:1]
                continue
            if len(self.buf) < total:
                break
            out.append(bytes(self.buf[:total]))
            del self.buf[:total]
        return out
