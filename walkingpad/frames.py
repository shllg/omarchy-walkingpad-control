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
#
# Measured 2026-08-24: belt == 4 is what an awake pad reports while nobody is on
# it -- speed 0.0, mode standby, all counters zero. It is named "idle" rather
# than "standby" so it cannot be confused with the MODE of the same name.
# 0 is "stopped", now confirmed directly: it is what the pad reports in manual
# mode with the belt still, and what it returns to after a STOP winds down.
# Measured 2026-08-24: 9, 8, 7 are the 3-2-1 startup countdown the panel shows
# after START. They last about a second each and decoded as "unknown" before.
_BELT = {0: "stopped", 1: "running", 4: "idle",
         9: "starting", 8: "starting", 7: "starting"}

# Only "running" means the belt is moving. Callers gate on that, never on the
# absence of a particular other value, so an unmapped state stays safe.
_MODE = {0: "auto", 1: "manual", 2: "standby"}


@dataclass(frozen=True)
class Status:
    belt: str
    speed_kmh: float
    set_speed_kmh: float
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
    """⚠ THIS OPCODE IS A TOGGLE. It stops a running belt and STARTS a stopped one.

    Confirmed 2026-08-25 with a controlled test: ten seconds of polling a stopped
    belt in manual mode with nothing sent (belt held at 0x00), then exactly one of
    these frames -- and the very next frame was 0x09, the start countdown, ramping
    to 3.5 km/h with nobody on the pad.

    This module is a pure codec and holds no policy, so it still encodes what is
    asked. Deciding whether sending it is SAFE belongs to Safety.may_stop(), which
    requires recent evidence that the belt is actually moving. Never call this
    without going through that gate.
    """
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
        # Byte [14] is the speed most recently commanded OVER BLE, in tenths of
        # a km/h: it echoed 0a -> 1.0, 0f -> 1.5, 1e -> 3.0 exactly, persists
        # across connections, and a running belt converges to it when a fresh
        # command is sent.
        #
        # It is NOT a general target, and must not be shown as one. It reads 0.0
        # when the speed was set at the panel instead -- every frame from the
        # first capture session shows 0.0 while the belt ran at 2.5 -- and the
        # pad IGNORES it when starting a stopped belt, starting at its own speed.
        set_speed_kmh=data[14] / 10.0,
        mode=_MODE.get(data[4], "unknown"),
        elapsed_s=int.from_bytes(data[5:8], "big"),
        # Measured: the distance counter is in 10 m units, so 69 -> 0.69 km.
        distance_km=int.from_bytes(data[8:11], "big") / 100.0,
        steps=int.from_bytes(data[11:14], "big"),
    )
    # Of bytes [14:18], [14] is the target speed above. [16] is NOT constant --
    # it was 0x02 in the first captures, 0x03 once started and 0x00 while in
    # standby, so it is some state byte. [15] and [17] stayed 0x00 throughout.
