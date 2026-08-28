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
# ⚠ MUST stay below Safety.stale_after_s, and tests/test_bridge.py asserts it.
#
# This was 5.0 against a 3.0 s gate, which meant that once the pad had been idle
# a minute, a status frame was older than the gate allows for 2 of every 5
# seconds. Measured on hardware 2026-08-25: 40% of button presses came back
# `stale_link` and the belt did not move. It reads as a flaky plugin, and the
# obvious "fix" from that symptom is to loosen the staleness gate -- which is a
# safety gate. Keeping the radio quiet must never cost more than the gate allows.
IDLE_POLL_INTERVAL_S = 2.0
IDLE_AFTER_S = 60.0
RESCAN_BACKOFF_S = 30.0        # the pad SLEEPS; absence is normal, do not storm
LINK_DEAD_AFTER_S = 15.0       # silence this long means the link is gone, not quiet
MIN_REPEAT_INTERVAL_S = 1.0    # a repeated command inside this window is dropped
START_SPEED_WINDOW_S = 8.0     # how long a start may wait for the belt to move
MAX_CONSECUTIVE_BAD_FRAMES = 10  # spec §8


class Bridge:
    def __init__(self, safety: Safety | None = None, transport=None, store=None):
        self.safety = safety or Safety()
        # Built in run(), not here: constructing a Transport opens a system bus
        # connection, and nothing about command handling needs a radio. This is
        # what lets the whole command path be tested with no hardware present.
        self.transport = transport
        self.store = store
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
        self._last_command = None
        # (speed, armed_at) while a start is waiting for the belt to move, so
        # the intended speed can be sent inside the window fact 12 describes.
        self._pending_start = None
        # A standing intention the belt is walked toward, one step per status
        # frame. None means nobody has asked for anything.
        self.goal_speed = None
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

        # An unknown command has nothing to refuse, and reporting "refusing to
        # move the belt" for one would put a wrong error code on the wire for
        # Stage 2 to misread.
        if cmd not in ("stop", "speed", "start", "goal"):
            return None

        # ⚠ STOP IS DELIBERATELY EXEMPT FROM THIS.
        #
        # Measured 2026-08-24: a script re-sending STOP several times a second
        # put the pad in a loop of countdown, beep, cancel, countdown. The cause
        # was the retry loop fighting the stop opcode's own toggle behaviour
        # (see below), not the pad restarting itself -- so throttling speed and
        # start is enough.
        #
        # Stop must never be throttled. There is no ACK in this protocol, so a
        # lost write is invisible, and a person pressing stop a second time
        # because nothing happened is the ONLY recovery there is. Swallowing
        # that retry leaves someone on a moving belt. A spammed stop merely
        # cancels start attempts; a swallowed one does not stop the belt. The
        # first failure is far worse than the second.
        # The signature includes the requested speed, so "identical" means what
        # the message says. Keying on the command NAME alone made two DIFFERENT
        # speeds inside the window collide, and the second was dropped in
        # silence -- indistinguishable, from the bar, from a speed command the
        # pad ignored. That is exactly the reading that would talk someone into
        # weakening a safety gate.
        #
        # Allowing distinct values back through adds no hazard: every speed is
        # still clamped to one step from the ACTUAL belt speed, which moves only
        # ~0.25 km/h per second. So a burst of clicks still resolves to the same
        # clamped number and still collapses to one write -- the suppression
        # keeps working on real bursts, and stops firing on real changes.
        #
        # `goal` is exempt too, for a different reason: it writes nothing to the
        # pad, so there is no burst to throttle. Throttling it would only put a
        # red refusal under a button that was working correctly.
        now = time.monotonic()
        signature = (cmd, obj.get("kmh"))
        if (cmd not in ("stop", "goal")
                and self._last_command is not None
                and self._last_command[0] == signature
                and now - self._last_command[1] < MIN_REPEAT_INTERVAL_S):
            self.emit({"t": "error", "code": "repeat_suppressed",
                       "msg": "identical '%s' within %.1fs dropped; "
                              "stop is never throttled"
                              % (cmd, MIN_REPEAT_INTERVAL_S)})
            return None
        self._last_command = (signature, now)

        # ⚠ Stop is NOT unconditional, because the stop opcode is a toggle that
        # STARTS a stopped belt (measured 2026-08-25; see frames.stop). Firing it
        # without evidence of motion is firing a start.
        if cmd == "stop":
            if not self.safety.may_stop(self.belt, self.current_speed,
                                        self._last_status_age()):
                self.emit({"t": "error", "code": "stop_unsafe",
                           "msg": "no recent evidence the belt is moving; sending "
                                  "stop could start it. Use the panel or remote."})
                return None
            # A stop must never be chased by a speed command that a start armed
            # or a goal was still converging toward.
            self._pending_start = None
            self.goal_speed = None
            self.commanded_speed = 0.0
            return frames.stop()

        if not self.safety.may_move(self._last_status_age()):
            # Design §5.6's rule, applied to the refusal a user actually meets
            # most often: say what was seen and what to do about it, rather than
            # only what was refused. Open question 9 measured this pad answering
            # for ~29 s of each ~48 s cycle, so roughly one press in three lands
            # while it is quiet -- on a pad that is awake, healthy and a metre
            # away. "No recent status" is true and tells the user nothing.
            #
            # ⚠ This is a MESSAGE change, not a gate change. §5.3 keeps the
            # buttons pressable and leaves refusing to the bridge; making the
            # panel disable them would be a second copy of Safety.may_move in
            # QML, which is the one thing that section forbids.
            self.emit({"t": "error", "code": "stale_link",
                       "msg": "the pad has gone quiet, so nothing was sent. If it "
                              "is awake the link re-establishes itself within "
                              "about 20 s -- try again then.",
                       # The age is what the gate actually refused on. It costs
                       # one key and makes question 9 legible in a plain log.
                       "status_age_s": round(self._last_status_age(), 2)})
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
            # An explicit one-shot speed supersedes both standing intentions.
            self._pending_start = None
            self.goal_speed = None
            target = self.safety.clamp_speed(self.current_speed, float(obj.get("kmh", 0)))
            self.commanded_speed = target
            return frames.set_speed(target)
        if cmd == "start":
            # Fact 12: the pad ramps to ~3.5 km/h on START whatever speed it has
            # stored, and IGNORES a speed command while the belt is stopped. So
            # the intended speed cannot be sent with the start -- it has to land
            # after the belt is actually moving, in the ~2 s window at
            # belt=running, speed=0.0. Arm it here; _service_pending_start fires
            # it off the status stream.
            #
            # This is NOT the timer retry fact 17 forbids. It is the second half
            # of ONE user action, triggered by an observed state change rather
            # than a clock, sent at most once, and discarded if the belt never
            # starts. Nothing is ever re-sent.
            #
            # Arming is skipped when the belt is already running, because then
            # the first "running" frame is the current one and the follow-up
            # would apply a speed change that never passed the per-action step
            # clamp. A start on a moving belt is just a start.
            self.goal_speed = None
            if "kmh" in obj and self.belt != "running":
                self.commanded_speed = self.safety.clamp_start_speed(float(obj["kmh"]))
                self._pending_start = (self.commanded_speed, now)
            return frames.start()

        if cmd == "goal":
            # Deliberately writes NOTHING here. A goal is a standing intention,
            # not a command, and _service_goal() is the single place that turns
            # it into frames -- one clamped step per status frame. Keeping one
            # writer means a burst of goal updates cannot become a burst of
            # writes, however fast the buttons are pressed.
            # "absolute" marks a value the user typed and committed, which is
            # bounded by the pad's range alone. Without it this is the stepping
            # path, where the lead bound still applies. See clamp_goal_absolute.
            requested = float(obj.get("kmh", 0))
            if obj.get("absolute"):
                self.goal_speed = self.safety.clamp_goal_absolute(requested)
            else:
                self.goal_speed = self.safety.clamp_goal(
                    self.current_speed, requested)
            return None
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
            "goal": self.goal_speed,
            # What the pad has stored from the last BLE speed command. Reads 0.0
            # if the speed was set at the panel, so it is diagnostic only --
            # "target" above is the number a UI should show.
            "pad_set_speed": status.set_speed_kmh,
            "mode": status.mode, "time": status.elapsed_s,
            "dist_km": status.distance_km, "steps": status.steps,
        })
        # After the status goes out, so a UI sees the frame that justified the
        # command before it sees the command's own effect.
        # At most ONE motion write per status frame. The start's own speed and
        # the goal's next step are both legitimate writers, and on the frame
        # where a start lands they would otherwise both fire -- the goal
        # immediately overriding the speed the start had just placed.
        if not self._service_pending_start(status):
            self._service_goal(status)
        # Store I/O is deliberately last, after status output and both possible
        # motion writers. A disk stall must never consume fact 12's ~2 s start
        # window or delay the safety-critical goal service on this frame.
        if self.store is not None:
            self.store.record(status, self.commanded_speed, self.goal_speed)

    def _service_pending_start(self, status) -> bool:
        """Send a start's intended speed once the belt is actually moving.

        Returns True if it wrote a frame, so the caller can keep the goal off
        the wire for this status frame.

        The window is short (fact 12) and this is the only place that can see it
        open, because the pad announces the transition in a status frame and
        acknowledges nothing.
        """
        if self._pending_start is None:
            return False
        speed, armed_at = self._pending_start

        # Expiry is checked BEFORE the belt state, so a start that never took
        # -- a lost write, a pad that refused -- disarms instead of lingering.
        # Without this, a stale pending would fire whenever the belt next moved,
        # which could be an hour later when someone starts it at the panel: a
        # speed command nobody asked for, from an action they had forgotten.
        if time.monotonic() - armed_at > START_SPEED_WINDOW_S:
            self._pending_start = None
            self.commanded_speed = None
            self.emit({"t": "error", "code": "start_unconfirmed",
                       "msg": "the belt did not start within %.0fs; "
                              "no speed was sent" % START_SPEED_WINDOW_S})
            return False

        # Anything else is the 3-2-1 countdown (fact 14) still running. Wait.
        if status.belt != "running":
            return False

        self._pending_start = None
        return self._send(frames.set_speed(speed))

    def _service_goal(self, status) -> None:
        """Walk the belt one step toward the standing goal, at most once a frame.

        ⚠ This is the first thing in the bridge that writes a motion command
        without a person having just pressed something, so every guard below is
        load-bearing. It is bounded in four independent ways: the goal itself
        cannot sit more than max_goal_lead from the actual speed (clamp_goal),
        each write is still one clamp_speed() step, nothing is sent unless a
        fresh frame says the belt is running, and it stops the moment the goal
        is reached.

        It is not the auto-retry fact 17 forbids. That rule is about re-sending
        the SAME command on a clock, which is how the countdown-cancel-countdown
        cycle was found. Every write here is a new, strictly closer value, and
        the guard below refuses to repeat one.
        """
        if self.goal_speed is None:
            return

        # A start owns the belt until its own speed has landed (fact 12's
        # window); two writers inside that window would fight over it.
        if self._pending_start is not None:
            return

        # Fact 11: a speed command never starts a stopped belt, and fact 8 says
        # "running" is the only state that means it is moving. Converging
        # against anything else would be writing into a belt nobody is on.
        if status.belt != "running":
            return

        # Fact 13: in standby the pad ignores motion commands entirely.
        if self.mode == "standby":
            return

        if not self.safety.may_move(self._last_status_age()):
            return

        if abs(status.speed_kmh - self.goal_speed) < self.safety.max_step / 2:
            self.goal_speed = None
            return

        step = self.safety.clamp_speed(status.speed_kmh, self.goal_speed)

        # Refuse to say the same thing twice. If the belt is not moving toward
        # the goal -- someone braced against it, the pad ignoring us -- this
        # stops rather than hammering the same value once a second.
        if self.commanded_speed is not None and step == self.commanded_speed:
            return

        self.commanded_speed = step
        self._send(frames.set_speed(step))

    def _poll_interval(self) -> float:
        # A pending start is waiting for a window measured in seconds (fact 12),
        # and it can only be seen in a status frame. Backing off here would let
        # the pad reach its own 3.5 km/h between two polls and the follow-up
        # speed would land after the moment it was meant for -- so poll fast
        # from the moment start is sent until the belt is actually moving.
        if self._pending_start is not None:
            return POLL_INTERVAL_S
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
        self._send(payload)

    def _send(self, payload: bytes) -> bool:
        """Write one frame, reporting rather than swallowing a failure."""
        if not self.transport.connected:
            # Said out loud rather than dropped: a stop that never reached the
            # pad must not look like a stop that did.
            self.emit({"t": "error", "code": "no_link",
                       "msg": "not connected to the pad"})
            return False
        try:
            self.transport.write(payload)
        except Exception as exc:
            self.emit({"t": "link", "state": "lost", "msg": str(exc)})
            self.transport.disconnect()
            self._close_store_bout_after_link_loss()
            return False
        return True

    def _close_store_bout_after_link_loss(self) -> None:
        # Design §1.4 closes an observed bout on explicit link loss at its last
        # frame. Every caller emits and tears the radio down first, so bounded
        # stats I/O cannot delay either the visible state or link teardown; the
        # Store method owns design §3.1's catch-once-then-disable boundary.
        if self.store is not None:
            self.store.close_open_bout()

    # -- link --------------------------------------------------------------
    def _answered_s(self) -> float:
        """How long the pad answered on THIS connection before going quiet.

        Design spec open question 9: this pad has been measured replying for
        ~29 s after a connection and then falling silent while its radio still
        acknowledges every packet we send. Nothing else in the output separates
        that from an ordinary link loss, so every reconnect looks the same in a
        log and the condition can only be caught with a `btmon` capture and root.
        Reporting the window on the loss itself makes it visible in any bridge
        log, with no capture and no hardware.

        0.0 means the pad never answered at all on this connection --
        `_last_status_at` then still belongs to an earlier one, or to no frame
        at all, and either way this connection has no window to report.
        """
        return round(max(0.0, self._last_status_at - self._connected_at), 2)

    def _service_link(self) -> float:
        """Do one unit of link work. Returns seconds until the next tick."""
        if not self.transport.connected:
            # BlueZ drops the link on its own and Connected goes False without
            # any path here having torn it down. Measured 2026-08-27: rescanning
            # straight from this branch left the FE01 notification subscription
            # registered, and the next subscribe() overwrote its id -- orphaning
            # it for the life of the process, because only disconnect() ever
            # unsubscribes. Each orphan makes on_notify run once more per status
            # frame, and on_notify drives the pending start and the goal servo,
            # so "at most ONE motion write per status frame" quietly stops
            # holding. Tearing down first is idempotent when there is nothing to
            # tear down, and the mute makes these drops frequent.
            self.transport.disconnect()
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
                       "msg": "no status frame for %.0fs" % silent_for,
                       "answered_s": self._answered_s()})
            self.transport.disconnect()
            self._close_store_bout_after_link_loss()
            return POLL_INTERVAL_S

        try:
            self.transport.write(frames.query())
        except Exception as exc:
            self.emit({"t": "link", "state": "lost", "msg": str(exc),
                       "answered_s": self._answered_s()})
            self.transport.disconnect()
            self._close_store_bout_after_link_loss()
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
            if self.store is not None:
                self.store.close()
            self.transport.disconnect()
