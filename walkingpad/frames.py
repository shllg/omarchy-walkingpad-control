#!/usr/bin/python3
"""
WiLink frame codec for KingSmith WalkingPad.

WHY THIS FILE EXISTS
--------------------
Everything the pad understands is a short framed byte string. This module is the
single place that knows the layout, and it is deliberately pure: no bleak, no I/O,
no async. That means the protocol can be tested against real captured bytes with
no hardware present, which is how fixtures/c2-fw6.1.2.jsonl is used.

Every constant below was MEASURED on a C2 running firmware 6.1.2 on 2026-08-24,
not taken from documentation. See docs/protocol-evidence.md.
"""
from dataclasses import dataclass

FRAME_START = 0xF7   # commands we send
FRAME_END   = 0xFD
STATUS_HEAD = (0xF8, 0xA2)   # frames the pad sends back
STATUS_LEN  = 20             # measured: always 20 bytes on fw 6.1.2

# Measured: belt == 1 while the belt is moving. An earlier guess of "starting"
# was wrong; the live step counter advancing at belt==1 disproved it.
_BELT = {0: "stopped", 1: "running"}
_MODE = {0: "auto", 1: "manual", 2: "standby"}


@dataclass(frozen=True)
class Status:
    belt: str
    speed_kmh: float
    mode: str
    elapsed_s: int
    distance_km: float
    steps: int


def checksum(body: bytes) -> int:
    """Sum of the body bytes, mod 256. Verified against every captured frame."""
    return sum(body) % 256


def build(*body: int) -> bytes:
    b = bytes(body)
    return bytes([FRAME_START]) + b + bytes([checksum(b), FRAME_END])


def query() -> bytes:
    """Ask for a status frame.

    This is REQUIRED on a timer. Measured: subscribing to FE01 and waiting
    produces nothing at all -- the pad answers queries and never volunteers.
    """
    return build(0xA2, 0x00, 0x00)


def set_speed(kmh: float) -> bytes:
    """Speed is carried in tenths of a km/h, so 2.5 km/h is 25."""
    return build(0xA2, 0x01, int(round(kmh * 10)))


def start() -> bytes:
    return build(0xA2, 0x04, 0x01)


def stop() -> bytes:
    return build(0xA2, 0x04, 0x00)


def parse_status(data: bytes) -> Status | None:
    """Decode a status frame, or return None if it is not a valid one.

    Returning None rather than raising is deliberate: BLE notifications are
    lossy and a malformed frame is an expected event, not an exception.
    """
    if len(data) < STATUS_LEN:
        return None
    if (data[0], data[1]) != STATUS_HEAD or data[-1] != FRAME_END:
        return None
    if checksum(data[1:-2]) != data[-2]:
        return None
    return Status(
        belt=_BELT.get(data[2], "unknown"),
        speed_kmh=data[3] / 10.0,
        mode=_MODE.get(data[4], "unknown"),
        elapsed_s=int.from_bytes(data[5:8], "big"),
        # Measured: the distance counter is in 10 m units, so 69 -> 0.69 km.
        distance_km=int.from_bytes(data[8:11], "big") / 100.0,
        steps=int.from_bytes(data[11:14], "big"),
    )
    # Bytes [14:18] are undecoded. [16] was constant 0x02 across every capture.
