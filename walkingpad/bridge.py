#!/usr/bin/python3
"""
Orchestration: poll the pad, emit status, accept commands.

WHY THERE IS A POLL LOOP AT ALL
-------------------------------
Measured 2026-08-24: with FE01 subscribed, four seconds of silence produced zero
notifications, and a later run produced exactly one frame per query. Every frame
the pad has ever sent was a direct reply to a query. Delete this loop and the bar
freezes -- it will not look like a bug, it will look like the pad is idle.

WHY commanded_speed IS TRACKED SEPARATELY
-----------------------------------------
The speed in a status frame is the ACTUAL belt speed, and the pad ramps to a new
target at roughly 0.25 km/h per second. There is no ACK anywhere in this protocol.
If the UI echoed the frame alone, a click would appear to do nothing for two
seconds. Consumers get both numbers and can render the transition.

WHY A GLIB MAIN LOOP AND NOT ASYNCIO
------------------------------------
The transport is Gio, whose notifications are D-Bus signals dispatched by a GLib
main loop. Bridging that to asyncio needs asyncio-glib, which Omarchy does not
ship -- that would reintroduce exactly the manual-install problem Gio was chosen
to avoid. One loop, no bridge (ADR D-3).
"""
import json
import os
import signal
import sys
import time

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib

from walkingpad import frames
from walkingpad.safety import Safety
from walkingpad.transport import Transport

POLL_INTERVAL_S = 1.0          # while the belt is moving
IDLE_POLL_INTERVAL_S = 5.0     # after a minute stopped, keep the radio quiet
IDLE_AFTER_S = 60.0
RESCAN_BACKOFF_S = 30.0        # the pad SLEEPS; absence is normal, do not storm
LINK_DEAD_AFTER_S = 15.0       # silence this long means the link is gone, not quiet
MAX_CONSECUTIVE_BAD_FRAMES = 10  # spec §8


class Bridge:
    def __init__(self, safety: Safety | None = None, transport=None):
        self.safety = safety or Safety()
        # Built in run(), not here: constructing a Transport opens a system bus
        # connection, and nothing about command handling needs a radio. This is
        # what lets the whole command path be tested with no hardware present.
        self.transport = transport
        self.current_speed = 0.0
        # None until we actually command something. The bridge routinely attaches
        # to a belt already running -- started at the panel, or left running by an
        # earlier session -- and reporting 0.0 there reads as "commanded to stop"
        # while the belt does 3.5. We have no target until we set one; the wire
        # carries null and the UI decides how to render "unknown".
        self.commanded_speed = None
        self.belt = "unknown"
        self.mode = "unknown"
        self._last_status_at = 0.0
        self._connected_at = 0.0
        self._stopped_since = time.monotonic()
        self._bad_frames = 0
        self._stdin_buffer = b""
        self._loop = None
        self._running = True

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

        # Gate only what can move the belt. An unknown command has nothing to
        # refuse, and reporting "refusing to move the belt" for one would put a
        # wrong error code on the wire for Stage 2 to misread.
        if cmd not in ("speed", "start"):
            return None

        if not self.safety.may_move(self._last_status_age()):
            self.emit({"t": "error", "code": "stale_link",
                       "msg": "no recent status; refusing to move the belt"})
            return None

        # Measured 2026-08-24: in standby the pad answers status queries but
        # ignores every motion command, and the BLE mode-switch opcode is ignored
        # too -- only the panel's M button leaves standby. Refusing here with a
        # reason beats writing into the void and looking broken, and it gives the
        # bar something specific to tell the user to do.
        if self.mode == "standby":
            self.emit({"t": "error", "code": "standby",
                       "msg": "pad is in standby and ignores commands; "
                              "press M on the panel to switch it to manual"})
            return None

        if cmd == "speed":
            target = self.safety.clamp_speed(self.current_speed, float(obj.get("kmh", 0)))
            self.commanded_speed = target
            return frames.set_speed(target)
        if cmd == "start":
            return frames.start()
        return None

    # -- frames in ---------------------------------------------------------
    def on_notify(self, _sender, data) -> None:
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
        self.mode = status.mode
        # "running" is the only state that means the belt is moving; anything
        # else counts as stopped, so an unmapped state fails safe.
        if status.belt == "running":
            self._stopped_since = 0.0
        elif self._stopped_since == 0.0:
            self._stopped_since = time.monotonic()
        self.emit({
            "t": "status", "belt": status.belt,
            "speed": status.speed_kmh, "target": self.commanded_speed,
            # What the pad has stored from the last BLE speed command. Reads 0.0
            # if the speed was set at the panel, so it is diagnostic only --
            # "target" above is the number a UI should show.
            "pad_set_speed": status.set_speed_kmh,
            "mode": status.mode, "time": status.elapsed_s,
            "dist_km": status.distance_km, "steps": status.steps,
        })

    def _poll_interval(self) -> float:
        if self._stopped_since and (time.monotonic() - self._stopped_since) > IDLE_AFTER_S:
            return IDLE_POLL_INTERVAL_S
        return POLL_INTERVAL_S

    # -- stdin -------------------------------------------------------------
    def _on_stdin(self, fd, _condition, *_user_data) -> bool:
        chunk = os.read(fd, 4096)
        if not chunk:
            # The parent closed the pipe. Quit rather than spin on EOF.
            self._loop.quit()
            return False
        # Buffer and split rather than readline(): a partial line must never
        # block the main loop, because the poll loop and the staleness gate that
        # guards the belt both live in it.
        self._stdin_buffer += chunk
        while b"\n" in self._stdin_buffer:
            line, self._stdin_buffer = self._stdin_buffer.split(b"\n", 1)
            self._dispatch(line)
        return True

    def _dispatch(self, line: bytes) -> None:
        if not line.strip():
            return
        try:
            payload = self.handle_command(json.loads(line))
        except (ValueError, TypeError) as exc:
            self.emit({"t": "error", "code": "bad_command", "msg": str(exc)})
            return
        if payload is None:
            return
        if not self.transport.connected:
            # Said out loud rather than dropped: a stop that never reached the
            # pad must not look like a stop that did.
            self.emit({"t": "error", "code": "no_link",
                       "msg": "not connected to the pad"})
            return
        try:
            self.transport.write(payload)
        except Exception as exc:
            self.emit({"t": "link", "state": "lost", "msg": str(exc)})

    # -- link --------------------------------------------------------------
    def _service_link(self) -> float:
        """Do one unit of link work. Returns seconds until the next tick."""
        if not self.transport.connected:
            self.emit({"t": "link", "state": "scanning"})
            try:
                device = self.transport.find(should_continue=self._keep_running)
            except Exception as exc:
                self.emit({"t": "link", "state": "error", "msg": str(exc)})
                return RESCAN_BACKOFF_S
            if not self._running:
                # Shutting down mid-scan. Saying "absent" here would report a
                # sleeping pad that was never actually looked for.
                return RESCAN_BACKOFF_S
            if device is None:
                # Normal: the pad sleeps and stops advertising when idle, and
                # BLE cannot wake it. This is a resting state, not a fault, so
                # it gets a long backoff and no alarm.
                self.emit({"t": "link", "state": "absent"})
                return RESCAN_BACKOFF_S
            try:
                self.transport.connect(device)
                self.transport.subscribe(self.on_notify)
            except Exception as exc:
                # Only ONE BLE connection to the pad exists at a time, so the
                # phone app being attached lands here. Spec §8: surface it as a
                # state, do not retry into it.
                self.emit({"t": "link", "state": "unavailable", "msg": str(exc)})
                self.transport.disconnect()
                return RESCAN_BACKOFF_S
            self._connected_at = time.monotonic()
            self.emit({"t": "link", "state": "connected",
                       "addr": device.address, "name": device.name})

        # BlueZ saying "Connected" is NOT proof of a working link. Measured
        # 2026-08-24: after an ungraceful exit BlueZ kept Connected=True on a
        # radio link that was already gone, and every write disappeared into it
        # with no error. A recent status frame is the only real evidence, which
        # is the same reason the safety gate exists. Silence gets the link torn
        # down and rescanned rather than shown as live with frozen numbers.
        #
        # _connected_at, not _last_status_at, seeds the clock: crediting a fresh
        # connection as a status frame would open the safety gate on a link that
        # has never said anything.
        silent_for = time.monotonic() - max(self._connected_at, self._last_status_at)
        if silent_for > LINK_DEAD_AFTER_S:
            self.emit({"t": "link", "state": "lost",
                       "msg": "no status frame for %.0fs" % silent_for})
            self.transport.disconnect()
            return POLL_INTERVAL_S

        try:
            self.transport.write(frames.query())
        except Exception as exc:
            self.emit({"t": "link", "state": "lost", "msg": str(exc)})
            self.transport.disconnect()
            # One prompt reconnect attempt. If the pad has actually gone, find()
            # returns None and the long backoff takes over from there.
            return POLL_INTERVAL_S
        return self._poll_interval()

    def _on_signal(self, *_user_data) -> bool:
        # Dispatched by the main loop itself. A plain Python signal handler is
        # not enough: g_main_loop_run() blocks in C, so Python never reaches one,
        # and a KeyboardInterrupt raised inside a GLib callback is swallowed
        # rather than unwinding the loop. Measured 2026-08-24: without this,
        # Ctrl-C did nothing at all and the bridge polled straight through it.
        self._running = False
        self._loop.quit()
        return False

    def _keep_running(self) -> bool:
        return self._running

    def _tick(self) -> bool:
        try:
            delay = self._service_link()
        except KeyboardInterrupt:
            # A scan blocks the main loop for up to ten seconds, so a Ctrl-C
            # landing in there interrupts the sleep as a Python exception before
            # the signal source above ever gets a chance to run.
            self._loop.quit()
            return False
        # Rescheduled one shot at a time because the interval varies: 1 s while
        # walking, 5 s once idle, 30 s while the pad is asleep.
        GLib.timeout_add(int(delay * 1000), self._tick)
        return False

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:
        if self.transport is None:
            self.transport = Transport()
        self._loop = GLib.MainLoop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_HIGH, sig, self._on_signal)
        GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, sys.stdin.fileno(),
                              GLib.IOCondition.IN | GLib.IOCondition.HUP,
                              self._on_stdin, None)
        GLib.idle_add(self._tick)
        try:
            self._loop.run()
        finally:
            self.transport.disconnect()
