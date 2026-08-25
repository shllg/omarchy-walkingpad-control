# WalkingPad Bridge (Stage 1) Implementation Plan

> Work through this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

> ## STATUS: COMPLETE (2026-08-24)
>
> All five tasks are implemented, committed and verified against a WalkingPad C2
> on firmware 6.1.2. 36 tests pass with no hardware. **The plan text below is kept
> as written for the record; where it disagrees with the code, the code is right.**
> Five deviations, each forced by evidence rather than preference:
>
> 1. **Task 2 was rewritten onto `gi.repository.Gio` instead of bleak** (ADR D-3).
>    A plugin manifest has no dependency field and no install hook, so bleak would
>    have meant a manual `pacman -S` before the plugin worked at all. This also
>    dissolved open question Q6.
> 2. **Task 4 runs a GLib main loop, not asyncio**, which follows from 1: Gio
>    delivers notifications as D-Bus signals, and bridging to asyncio needs
>    `asyncio-glib`, which Omarchy does not ship. `handle_command`, `emit`,
>    `on_notify` and `_poll_interval` are unchanged; `run()` and stdin reading are
>    not.
> 3. **`belt == 4` was added to the codec.** Measured live: it is the awake-and-idle
>    state, so the plan's map decoded the pad's most common awake state as
>    `"unknown"`.
> 4. **A link watchdog was added** (ADR D-4). BlueZ reported `Connected=True` on a
>    radio link that was already gone and swallowed every write. Fifteen seconds of
>    silence now tears the link down and rescans.
> 5. **SIGINT/SIGTERM are GLib signal sources**, and the staleness gate applies only
>    to `speed` and `start`. Without the first, Ctrl-C did nothing; without the
>    second, an unknown command answered "refusing to move the belt".
>
> Test count is 36, not the 25 predicted here — the extra 11 cover transport
> predicates, link adoption and the watchdog.

**Goal:** Build `walkingpad-bridge`, a standalone Python process that owns the BLE link to a KingSmith WalkingPad and exposes it as line-delimited JSON on stdio.

**Architecture:** Three focused modules behind a thin entry point. `frames.py` is a pure, hardware-free codec for the WiLink protocol (this is where all the measured protocol knowledge lives). `transport.py` wraps bleak and knows nothing about WalkingPad semantics. `bridge.py` orchestrates: a 1 Hz poll loop, stdin commands, stdout status. No QML in this stage — the deliverable runs in a terminal.

**Tech Stack:** System Python (`/usr/bin/python3`) and `gi.repository.Gio` talking
to BlueZ over D-Bus — no third-party runtime dependency. `pytest` for development
only. No mise-managed runtimes. *(Revised from bleak; see ADR D-3.)*

**Spec:** `docs/design-spec.md` (see especially §2 verified facts, §4 the bridge, §6 safety, §12 ADRs D-1/D-2)

## Global Constraints

- Shebang is exactly `#!/usr/bin/python3`. Never a mise runtime (ADR D-2).
- Dependencies: none at runtime. `python-gobject` and `bluez` are already in
  `omarchy-base.packages`. Never pip. *(Revised; see ADR D-3.)*
- **Always scan before connecting.** Connecting to a device BlueZ has not seen
  recently fails with "device was not found", which is indistinguishable from a
  contention failure. The one exception is a link BlueZ is already holding, which
  is adopted rather than rediscovered — a connected pad does not advertise.
- **Status is poll-driven, not push.** Subscribing to FE01 and waiting yields nothing.
  Every frame is a reply to a query. The poll loop is load-bearing, not an optimisation.
- **Reported speed is ACTUAL, not target.** The pad ramps at ~0.25 km/h/s and never ACKs.
- Never write to the OTA service `f000ffc0-0451-4000-b000-000000000000` (§3.3).
- Plain constructs only: no metaprogramming, no clever async beyond the poll loop.
  Comments explain *why* and cite the measured fact they depend on (ADR D-1).
- Absent device is the NORMAL resting state — the pad sleeps and stops advertising.
  Never treat it as an error; never retry-storm.

---

### Task 1: Frame codec (pure, no hardware)

**Files:**
- Create: `walkingpad/__init__.py` (empty)
- Create: `walkingpad/frames.py`
- Create: `tests/test_frames.py`
- Create: `fixtures/c2-fw6.1.2.jsonl`

**Interfaces:**
- Consumes: nothing.
- Produces: `checksum(body: bytes) -> int`, `build(*body: int) -> bytes`,
  `query() -> bytes`, `set_speed(kmh: float) -> bytes`, `start() -> bytes`,
  `stop() -> bytes`, `Status` dataclass with fields
  `belt: str, speed_kmh: float, mode: str, elapsed_s: int, distance_km: float, steps: int`,
  and `parse_status(data: bytes) -> Status | None`.

- [ ] **Step 1: Save the real captured frames as fixtures**

Create `fixtures/c2-fw6.1.2.jsonl` with these four frames, captured live from the
author's C2 on firmware 6.1.2 while the belt was running at 2.5 km/h:

```
{"hex":"f8a20119010003f0000045000522000002001efd","note":"2.5kmh t=1008s d=0.69km s=1314"}
{"hex":"f8a20119010003f30000460005260000020026fd","note":"2.5kmh t=1011s d=0.70km s=1318"}
{"hex":"f8a20119010003f5000046000528000002002afd","note":"2.5kmh t=1013s d=0.70km s=1320"}
{"hex":"f8a20119010003f6000046000529000002002cfd","note":"2.5kmh t=1014s d=0.70km s=1321"}
```

Strip the spaces when loading — they are only present to keep this document readable.

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_frames.py
import json, pathlib
import pytest
from walkingpad import frames

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "c2-fw6.1.2.jsonl"

def load_frames():
    out = []
    for line in FIXTURES.read_text().splitlines():
        if line.strip():
            out.append(bytes.fromhex(json.loads(line)["hex"].replace(" ", "")))
    return out

def test_checksum_is_sum_of_body_mod_256():
    assert frames.checksum(bytes([0xA2, 0x00, 0x00])) == 0xA2

def test_query_frame_is_exact():
    assert frames.query() == bytes.fromhex("f7a20000a2fd")

def test_start_and_stop_frames_are_exact():
    assert frames.start() == bytes.fromhex("f7a20401a7fd")
    assert frames.stop()  == bytes.fromhex("f7a20400a6fd")

@pytest.mark.parametrize("kmh,expected", [
    (2.0, "f7a20114b7fd"),
    (2.5, "f7a20119bcfd"),
])
def test_set_speed_frames_are_exact(kmh, expected):
    assert frames.set_speed(kmh) == bytes.fromhex(expected)

def test_parses_real_captured_status():
    s = frames.parse_status(load_frames()[0])
    assert s.belt == "running"
    assert s.speed_kmh == 2.5
    assert s.mode == "manual"
    assert s.elapsed_s == 1008
    assert s.distance_km == 0.69
    assert s.steps == 1314

def test_every_captured_frame_parses_and_has_valid_crc():
    for raw in load_frames():
        assert frames.parse_status(raw) is not None

def test_rejects_frame_with_bad_crc():
    raw = bytearray(load_frames()[0])
    raw[-2] ^= 0xFF
    assert frames.parse_status(bytes(raw)) is None

def test_rejects_truncated_frame():
    assert frames.parse_status(bytes.fromhex("f8a20119")) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd ~/src/omarchy-walkingpad && python -m pytest tests/test_frames.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkingpad'`

- [ ] **Step 4: Implement the codec**

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_frames.py -v`
Expected: PASS, 9 tests

- [ ] **Step 6: Commit**

```bash
git add walkingpad/ tests/ fixtures/
git commit -m "feat(frames): WiLink codec verified against captured C2 frames"
```

---

### Task 2: BLE transport

> ⚠ **SUPERSEDED.** Implemented on `gi.repository.Gio`, not bleak — see ADR D-3 and
> `walkingpad/transport.py`. The interface below still holds except that the methods
> are synchronous, `find()` takes a `should_continue` callable, `matches_walkingpad`
> takes a BlueZ property dict, and `find_connected` / `find_advertising` /
> `is_in_range` were added. The rationale in the step text stands.

**Files:**
- Create: `walkingpad/transport.py`
- Create: `tests/test_transport.py`

**Interfaces:**
- Consumes: nothing from Task 1 (transport is protocol-agnostic by design).
- Produces: `SERVICE_FE00`, `CHAR_NOTIFY`, `CHAR_WRITE` constants; and
  `class Transport` with `async def find(timeout: float = 10.0) -> object | None`,
  `async def connect(device) -> None`, `async def write(payload: bytes) -> None`,
  `async def subscribe(callback) -> None`, `async def disconnect() -> None`,
  and property `connected: bool`.

- [ ] **Step 1: Write the failing test**

Transport touches real radios, so the test covers only the part that can be
verified without hardware: that `find` filters on the right criteria.

```python
# tests/test_transport.py
from walkingpad.transport import matches_walkingpad, SERVICE_FE00

class FakeDevice:
    def __init__(self, name): self.name = name

class FakeAdv:
    def __init__(self, uuids=None): self.service_uuids = uuids or []

def test_matches_on_advertised_service_uuid():
    assert matches_walkingpad(FakeDevice("anything"), FakeAdv([SERVICE_FE00]))

def test_matches_on_ks_name_prefix():
    assert matches_walkingpad(FakeDevice("KS-BLC2"), FakeAdv())

def test_service_uuid_match_is_case_insensitive():
    assert matches_walkingpad(FakeDevice("x"), FakeAdv([SERVICE_FE00.upper()]))

def test_ignores_unrelated_devices():
    assert not matches_walkingpad(FakeDevice("Glove80"), FakeAdv())
    assert not matches_walkingpad(FakeDevice(None), FakeAdv())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_transport.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkingpad.transport'`

- [ ] **Step 3: Implement the transport**

```python
#!/usr/bin/python3
"""
BLE plumbing. Knows nothing about WalkingPad semantics -- it moves bytes.

WHY THE SCAN IS NOT OPTIONAL
----------------------------
BleakClient(<address>) requires the device to already be in BlueZ's cache. With
no prior scan it fails with "device was not found", which looks exactly like a
connection being refused by another client. That false signal cost real debugging
time on 2026-08-24, so connecting ALWAYS goes through find() first.
"""
from bleak import BleakClient, BleakScanner

SERVICE_FE00 = "0000fe00-0000-1000-8000-00805f9b34fb"
CHAR_NOTIFY  = "0000fe01-0000-1000-8000-00805f9b34fb"
CHAR_WRITE   = "0000fe02-0000-1000-8000-00805f9b34fb"

# The pad exposes a TI-style OTA service. We never touch it -- a bad image
# bricks the control board. Named here only so nobody "adds support" later.
FORBIDDEN_OTA_SERVICE = "f000ffc0-0451-4000-b000-000000000000"


def matches_walkingpad(device, adv) -> bool:
    """Prefer the advertised service UUID; fall back to the vendor name prefix.

    Matching on FE00 identifies a CAPABILITY, so it also catches models whose
    advertised name does not follow the KS- convention.
    """
    uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
    if SERVICE_FE00 in uuids:
        return True
    name = getattr(device, "name", None) or ""
    return name.upper().startswith("KS-")


class Transport:
    def __init__(self):
        self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    async def find(self, timeout: float = 10.0):
        """Return a device handle, or None. None is NORMAL: the pad sleeps and
        stops advertising entirely when idle."""
        return await BleakScanner.find_device_by_filter(matches_walkingpad, timeout=timeout)

    async def connect(self, device) -> None:
        self._client = BleakClient(device, timeout=30.0)
        await self._client.connect()

    async def subscribe(self, callback) -> None:
        await self._client.start_notify(CHAR_NOTIFY, callback)

    async def write(self, payload: bytes) -> None:
        # write-without-response: the characteristic offers no ACK path at all,
        # so confirmation only ever arrives as a later status frame.
        await self._client.write_gatt_char(CHAR_WRITE, payload, response=False)

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_transport.py -v`
Expected: PASS, 4 tests

- [ ] **Step 5: Commit**

```bash
git add walkingpad/transport.py tests/test_transport.py
git commit -m "feat(transport): BLE plumbing with mandatory scan-before-connect"
```

---

### Task 3: Safety policy

**Files:**
- Create: `walkingpad/safety.py`
- Create: `tests/test_safety.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `class Safety` constructed as
  `Safety(max_speed: float = 6.0, max_step: float = 0.5, stale_after_s: float = 3.0)`,
  with `clamp_speed(current: float, requested: float) -> float` and
  `may_move(last_status_age_s: float) -> bool`.

Written before the bridge so the bridge cannot be built without it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_safety.py
from walkingpad.safety import Safety

def test_clamps_upward_jump_to_one_step():
    assert Safety().clamp_speed(current=2.5, requested=6.0) == 3.0

def test_clamps_downward_jump_to_one_step():
    assert Safety().clamp_speed(current=2.5, requested=0.5) == 2.0

def test_allows_small_change_unchanged():
    assert Safety().clamp_speed(current=2.5, requested=2.7) == 2.7

def test_never_exceeds_max_speed():
    assert Safety(max_speed=3.0).clamp_speed(current=2.9, requested=3.4) == 3.0

def test_never_goes_negative():
    assert Safety().clamp_speed(current=0.2, requested=-5.0) == 0.0

def test_refuses_to_move_on_stale_link():
    assert Safety().may_move(last_status_age_s=0.5) is True
    assert Safety().may_move(last_status_age_s=9.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_safety.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkingpad.safety'`

- [ ] **Step 3: Implement**

```python
#!/usr/bin/python3
"""
Safety policy, kept separate so it is impossible to bypass by accident.

WHY A PER-ACTION CLAMP
----------------------
The pad ramps at roughly 0.25 km/h per second and there may be a person standing
on the belt. An unclamped 2.5 -> 6.0 command is a continuous fourteen-second
acceleration under someone's feet. Clamping to one step per action means the
worst case of a stuck key or a double click is one small increment.

WHY A STALENESS GATE
--------------------
There is no ACK anywhere in this protocol, so the ONLY evidence that we are
talking to a live pad is a recent status frame. If none has arrived, we do not
know what state the belt is in, and issuing motion commands blind is not
acceptable. Stopping is exempt -- see bridge.py.
"""


class Safety:
    def __init__(self, max_speed: float = 6.0, max_step: float = 0.5,
                 stale_after_s: float = 3.0):
        self.max_speed = max_speed
        self.max_step = max_step
        self.stale_after_s = stale_after_s

    def clamp_speed(self, current: float, requested: float) -> float:
        low = current - self.max_step
        high = current + self.max_step
        value = min(max(requested, low), high)
        value = min(value, self.max_speed)
        value = max(value, 0.0)
        return round(value, 1)

    def may_move(self, last_status_age_s: float) -> bool:
        return last_status_age_s <= self.stale_after_s
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_safety.py -v`
Expected: PASS, 6 tests

- [ ] **Step 5: Commit**

```bash
git add walkingpad/safety.py tests/test_safety.py
git commit -m "feat(safety): per-action speed clamp and stale-link gate"
```

---

### Task 4: Bridge orchestration

**Files:**
- Create: `walkingpad/bridge.py`
- Create: `bin/walkingpad-bridge`
- Create: `tests/test_bridge.py`

**Interfaces:**
- Consumes: `frames` (Task 1), `Transport` (Task 2), `Safety` (Task 3).
- Produces: `class Bridge` with `async def run() -> None`,
  `def handle_command(obj: dict) -> bytes | None`, and
  `def emit(obj: dict) -> None`.

- [ ] **Step 1: Write the failing test**

Command translation is pure and testable; the loop is not, so only translation
is covered here. `emit` is injected so tests can capture output.

```python
# tests/test_bridge.py
from walkingpad.bridge import Bridge
from walkingpad import frames

def make_bridge():
    b = Bridge()
    b._last_status_age = lambda: 0.0          # link is fresh
    b.current_speed = 2.5
    return b

def test_speed_command_is_clamped_then_encoded():
    b = make_bridge()
    assert b.handle_command({"cmd": "speed", "kmh": 6.0}) == frames.set_speed(3.0)

def test_stop_command_encodes_stop():
    assert make_bridge().handle_command({"cmd": "stop"}) == frames.stop()

def test_stop_is_allowed_even_on_a_stale_link():
    b = make_bridge()
    b._last_status_age = lambda: 99.0
    assert b.handle_command({"cmd": "stop"}) == frames.stop()

def test_speed_is_refused_on_a_stale_link():
    b = make_bridge()
    b._last_status_age = lambda: 99.0
    assert b.handle_command({"cmd": "speed", "kmh": 3.0}) is None

def test_unknown_command_is_ignored():
    assert make_bridge().handle_command({"cmd": "explode"}) is None

def test_emit_writes_one_json_line():
    lines = []
    b = make_bridge()
    b._write = lines.append
    b.emit({"t": "status", "speed": 2.5})
    assert lines == ['{"t": "status", "speed": 2.5}\n']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'walkingpad.bridge'`

- [ ] **Step 3: Implement the bridge**

```python
#!/usr/bin/python3
"""
Orchestration: poll the pad, emit status, accept commands.

WHY THERE IS A POLL LOOP AT ALL
-------------------------------
Measured 2026-08-24: with FE01 subscribed, four seconds of silence produced zero
notifications. Every frame the pad has ever sent was a direct reply to a query.
Delete this loop and the bar freezes -- it will not look like a bug, it will look
like the pad is idle.

WHY commanded_speed IS TRACKED SEPARATELY
-----------------------------------------
The speed in a status frame is the ACTUAL belt speed, and the pad ramps to a new
target over roughly two seconds. If the UI echoed the frame alone, a click would
appear to do nothing for two seconds. Consumers get both numbers and can render
the transition.
"""
import asyncio, json, sys, time

from walkingpad import frames
from walkingpad.safety import Safety
from walkingpad.transport import Transport

POLL_INTERVAL_S = 1.0          # while the belt is moving
IDLE_POLL_INTERVAL_S = 5.0     # after a minute stopped, keep the radio quiet
IDLE_AFTER_S = 60.0
RESCAN_BACKOFF_S = 30.0        # the pad SLEEPS; absence is normal, do not storm
MAX_CONSECUTIVE_BAD_FRAMES = 10  # spec §8


class Bridge:
    def __init__(self, safety: Safety | None = None):
        self.safety = safety or Safety()
        self.transport = Transport()
        self.current_speed = 0.0
        self.commanded_speed = 0.0
        self.belt = "unknown"
        self._last_status_at = 0.0
        self._stopped_since = time.monotonic()
        self._bad_frames = 0

    # -- output ------------------------------------------------------------
    def _write(self, line: str) -> None:
        sys.stdout.write(line)
        sys.stdout.flush()

    def emit(self, obj: dict) -> None:
        self._write(json.dumps(obj) + "\n")

    # -- input -------------------------------------------------------------
    def _last_status_age(self) -> float:
        if self._last_status_at == 0.0:
            return float("inf")
        return time.monotonic() - self._last_status_at

    def handle_command(self, obj: dict) -> bytes | None:
        cmd = obj.get("cmd")

        # Stop is exempt from every gate. If we are unsure of the state, the
        # safe action is still to stop, never to refuse.
        if cmd == "stop":
            self.commanded_speed = 0.0
            return frames.stop()

        if not self.safety.may_move(self._last_status_age()):
            self.emit({"t": "error", "code": "stale_link",
                       "msg": "no recent status; refusing to move the belt"})
            return None

        if cmd == "speed":
            target = self.safety.clamp_speed(self.current_speed, float(obj.get("kmh", 0)))
            self.commanded_speed = target
            return frames.set_speed(target)
        if cmd == "start":
            return frames.start()
        return None

    # -- frames in ---------------------------------------------------------
    def on_notify(self, _sender, data: bytearray) -> None:
        status = frames.parse_status(bytes(data))
        if status is None:
            # Spec §8: a lone bad frame is expected on BLE and is dropped, but a
            # run of them means the link is unhealthy rather than merely lossy.
            self._bad_frames += 1
            if self._bad_frames >= MAX_CONSECUTIVE_BAD_FRAMES:
                self.emit({"t": "error", "code": "link_unhealthy",
                           "msg": f"{self._bad_frames} consecutive undecodable frames"})
                self._bad_frames = 0
            return
        self._bad_frames = 0
        self._last_status_at = time.monotonic()
        self.current_speed = status.speed_kmh
        self.belt = status.belt
        if status.belt == "running":
            self._stopped_since = 0.0
        elif self._stopped_since == 0.0:
            self._stopped_since = time.monotonic()
        self.emit({
            "t": "status", "belt": status.belt,
            "speed": status.speed_kmh, "target": self.commanded_speed,
            "mode": status.mode, "time": status.elapsed_s,
            "dist_km": status.distance_km, "steps": status.steps,
        })

    def _poll_interval(self) -> float:
        if self._stopped_since and (time.monotonic() - self._stopped_since) > IDLE_AFTER_S:
            return IDLE_POLL_INTERVAL_S
        return POLL_INTERVAL_S

    # -- main loop ---------------------------------------------------------
    async def _read_stdin(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                return
            try:
                payload = self.handle_command(json.loads(line))
            except (ValueError, TypeError) as exc:
                self.emit({"t": "error", "code": "bad_command", "msg": str(exc)})
                continue
            if payload is not None and self.transport.connected:
                await self.transport.write(payload)

    async def run(self) -> None:
        asyncio.ensure_future(self._read_stdin())
        while True:
            if not self.transport.connected:
                self.emit({"t": "link", "state": "scanning"})
                device = await self.transport.find()
                if device is None:
                    # Normal: the pad sleeps and stops advertising when idle.
                    self.emit({"t": "link", "state": "absent"})
                    await asyncio.sleep(RESCAN_BACKOFF_S)
                    continue
                await self.transport.connect(device)
                await self.transport.subscribe(self.on_notify)
                self.emit({"t": "link", "state": "connected",
                           "addr": device.address, "name": device.name})
            try:
                await self.transport.write(frames.query())
            except Exception as exc:
                self.emit({"t": "link", "state": "lost", "msg": str(exc)})
                await self.transport.disconnect()
                continue
            await asyncio.sleep(self._poll_interval())
```

- [ ] **Step 4: Create the entry point**

```python
#!/usr/bin/python3
"""Entry point for the WalkingPad bridge. See walkingpad/bridge.py."""
import argparse, asyncio, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from walkingpad.bridge import Bridge
from walkingpad.safety import Safety

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="WalkingPad BLE bridge (stdio JSON)")
    ap.add_argument("--max-speed", type=float, default=6.0,
                    help="absolute ceiling in km/h (spec §7)")
    ap.add_argument("--max-step", type=float, default=0.5,
                    help="largest speed change a single command may make")
    args = ap.parse_args()
    safety = Safety(max_speed=args.max_speed, max_step=args.max_step)
    try:
        asyncio.run(Bridge(safety=safety).run())
    except KeyboardInterrupt:
        pass
```

Then: `chmod +x bin/walkingpad-bridge`

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/ -v`
Expected: PASS, 25 tests total across all four files

- [ ] **Step 6: Commit**

```bash
git add walkingpad/bridge.py bin/walkingpad-bridge tests/test_bridge.py
git commit -m "feat(bridge): poll loop, stdio JSON protocol, safety gating"
```

---

### Task 5: Hardware smoke test and README

**Files:**
- Create: `README.md`
- Create: `docs/smoke-test.md`

**Interfaces:**
- Consumes: the finished `bin/walkingpad-bridge`.
- Produces: no code.

- [ ] **Step 1: Write the smoke-test checklist**

Create `docs/smoke-test.md` containing exactly this procedure:

```markdown
# Hardware smoke test

⚠ Steps 4 and later MOVE THE BELT. Stand clear of the pad before starting them.

1. Wake the pad at its panel. It sleeps and stops advertising when idle.
2. `./bin/walkingpad-bridge`
3. Expect within ~10 s: `{"t":"link","state":"connected",...}` then one
   `{"t":"status",...}` per second. Confirm `steps` and `time` advance.
4. Type: `{"cmd":"speed","kmh":1.0}` — expect the belt to reach 1.0 km/h over ~4 s,
   passing through intermediate values. This ramp is expected, not a fault.
5. Type: `{"cmd":"speed","kmh":6.0}` — expect it to clamp to 1.5, NOT jump to 6.0.
6. Type: `{"cmd":"stop"}` — expect the belt to wind down to 0.0.
7. Ctrl-C. Confirm the process exits and the pad disconnects.
```

- [ ] **Step 2: Write the README**

`README.md` must state: what it is, that it needs `pacman -S python-bleak`, the
stdio JSON protocol with one example line in each direction, the single-connection
limit (the phone app and the bridge cannot both be connected), that the pad sleeps
and absence is normal, and that the OTA service is deliberately untouched.

- [ ] **Step 3: Run the full test suite one last time**

Run: `python -m pytest tests/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add README.md docs/smoke-test.md
git commit -m "docs: README and hardware smoke-test checklist"
```

---

## Deferred to later stages

- FTMS adapter (§4.3). The author's C2 has no `0x1826`, so it cannot be tested
  by anyone involved. Building it now would ship untested code claiming support.
- `--replay` mode (§4.4). Needed to develop QML without hardware; belongs with
  Stage 2 when there is a consumer for it.
- QML service, bar widget, adoption UI, IpcHandler — all Stage 2.
- Open question Q2 (cold start) stays open — it needs an awake pad AND an operator
  standing clear, because answering it moves a belt. The procedure is in
  `docs/smoke-test.md`. It may still change Task 4's `start` handling.
- Q6 (BlueZ/bleak cooperation) is **dissolved, not answered**: dropping bleak for
  Gio means there is no second BLE stack to contend with.
