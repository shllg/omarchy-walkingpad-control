import json

# tests/test_bridge.py
import time

from walkingpad.bridge import Bridge, IDLE_POLL_INTERVAL_S, MIN_REPEAT_INTERVAL_S
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


def test_stop_is_refused_on_a_stale_link():
    # This test asserted the OPPOSITE until 2026-08-25, when a controlled test
    # showed the stop opcode is a toggle that starts a stopped belt. With no
    # recent frame we cannot tell which way it will go, and half the outcomes
    # start a treadmill nobody asked to start.
    b = make_bridge()
    b._last_status_age = lambda: 99.0
    emitted = []
    b.emit = emitted.append
    assert b.handle_command({"cmd": "stop"}) is None
    assert [e["code"] for e in emitted] == ["stop_unsafe"]


def test_stop_is_refused_when_the_belt_is_already_stopped():
    b = make_bridge()
    b.belt, b.current_speed = "stopped", 0.0
    b.emit = lambda o: None
    assert b.handle_command({"cmd": "stop"}) is None


def test_stop_is_allowed_while_the_belt_is_running():
    b = make_bridge()
    b.belt, b.current_speed = "running", 2.5
    assert b.handle_command({"cmd": "stop"}) == frames.stop()


def test_stop_is_allowed_during_the_startup_countdown():
    # Cancelling a countdown is the one moment stopping matters most: the belt
    # is about to move and nobody may be ready for it.
    b = make_bridge()
    b.belt, b.current_speed = "starting", 0.0
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


def test_unknown_command_does_not_report_a_stale_link():
    # An unknown command has no belt to refuse. Emitting "stale_link" for one
    # puts a wrong error code on the wire; caught during live hardware testing.
    b = make_bridge()
    b._last_status_age = lambda: 99.0
    emitted = []
    b.emit = emitted.append
    assert b.handle_command({"cmd": "explode"}) is None
    assert emitted == []


class FakeTransport:
    """Enough of a Transport to exercise the link watchdog with no radio."""

    def __init__(self):
        self.connected = True
        self.writes = []
        self.disconnects = 0

    def find(self, should_continue=None):
        return None

    def connect(self, device):
        pass

    def subscribe(self, callback):
        pass

    def write(self, payload):
        self.writes.append(payload)

    def disconnect(self):
        self.disconnects += 1
        self.connected = False


def test_a_silent_link_is_torn_down_not_shown_as_live():
    # Measured: BlueZ reported Connected=True on a radio link that was already
    # gone, and writes vanished into it without error. Silence is the only
    # symptom, so silence has to be what triggers the teardown.
    import time as _time
    t = FakeTransport()
    b = Bridge(transport=t)
    b.emit = lambda obj: None
    b._connected_at = _time.monotonic() - 60.0
    b._service_link()
    assert t.disconnects == 1
    assert t.writes == []


def test_a_speaking_link_is_kept_and_polled():
    import time as _time
    t = FakeTransport()
    b = Bridge(transport=t)
    b.emit = lambda obj: None
    b._connected_at = _time.monotonic() - 60.0
    b._last_status_at = _time.monotonic()
    b._service_link()
    assert t.disconnects == 0
    assert t.writes == [frames.query()]


def test_a_fresh_connection_is_not_immediately_declared_dead():
    import time as _time
    t = FakeTransport()
    b = Bridge(transport=t)
    b.emit = lambda obj: None
    b._connected_at = _time.monotonic()
    b._service_link()
    assert t.disconnects == 0


def test_motion_is_refused_while_the_pad_is_in_standby():
    # Measured: in standby the pad answers status queries but silently ignores
    # every motion command, so sending one would look like a broken plugin.
    for cmd in ({"cmd": "speed", "kmh": 3.0}, {"cmd": "start"}):
        b = make_bridge()
        b.mode = "standby"
        emitted = []
        b.emit = emitted.append
        assert b.handle_command(cmd) is None
        assert [e["code"] for e in emitted] == ["standby"]


def test_stop_is_still_allowed_in_standby():
    b = make_bridge()
    b.mode = "standby"
    assert b.handle_command({"cmd": "stop"}) == frames.stop()


def test_target_is_unknown_until_something_is_commanded():
    # Seen live: the bridge attached to a belt already running at 3.5 and
    # reported target 0.0, which reads as a commanded stop. We had commanded
    # nothing, so the honest answer is null rather than a number we made up.
    b = Bridge()
    assert b.commanded_speed is None
    emitted = []
    b._write = emitted.append
    b.on_notify(None, bytes.fromhex("f8a201140100006900000500007a14000300b7fd"))
    assert json.loads(emitted[0])["target"] is None


def test_target_is_reported_once_a_speed_is_commanded():
    b = make_bridge()
    b.handle_command({"cmd": "speed", "kmh": 2.5})
    emitted = []
    b._write = emitted.append
    b.on_notify(None, bytes.fromhex("f8a201140100006900000500007a14000300b7fd"))
    assert json.loads(emitted[0])["target"] == 2.5


def test_a_repeated_speed_command_is_dropped():
    b = make_bridge()
    emitted = []
    b.emit = emitted.append
    assert b.handle_command({"cmd": "speed", "kmh": 2.5}) == frames.set_speed(2.5)
    assert b.handle_command({"cmd": "speed", "kmh": 2.5}) is None
    assert [e["code"] for e in emitted] == ["repeat_suppressed"]


def test_repeated_stops_are_never_throttled():
    # There is no ACK in this protocol, so a lost write is invisible and a
    # second press is the only recovery a person has. Swallowing it would leave
    # someone on a moving belt. Spamming stop only cancels start attempts.
    b = make_bridge()
    emitted = []
    b.emit = emitted.append
    for _ in range(5):
        assert b.handle_command({"cmd": "stop"}) == frames.stop()
    assert emitted == []


def test_stop_is_not_throttled_by_a_preceding_speed_command():
    b = make_bridge()
    b.emit = lambda o: None
    b.handle_command({"cmd": "speed", "kmh": 2.5})
    assert b.handle_command({"cmd": "stop"}) == frames.stop()


def test_a_repeat_is_allowed_again_after_the_window():
    b = make_bridge()
    b.emit = lambda o: None
    assert b.handle_command({"cmd": "start"}) == frames.start()
    b._last_command = ("start", time.monotonic() - MIN_REPEAT_INTERVAL_S - 0.1)
    assert b.handle_command({"cmd": "start"}) == frames.start()


def test_different_commands_are_not_suppressed():
    b = make_bridge()
    b.emit = lambda o: None
    assert b.handle_command({"cmd": "start"}) == frames.start()
    assert b.handle_command({"cmd": "speed", "kmh": 2.5}) == frames.set_speed(2.5)


def test_two_different_speeds_are_not_suppressed_as_a_repeat():
    # Keying suppression on the command NAME alone made these collide, and the
    # second was dropped in silence -- from the bar, indistinguishable from a
    # speed command the pad ignored.
    b = make_bridge()
    emitted = []
    b.emit = emitted.append
    assert b.handle_command({"cmd": "speed", "kmh": 2.5}) == frames.set_speed(2.5)
    assert b.handle_command({"cmd": "speed", "kmh": 3.0}) == frames.set_speed(3.0)
    assert emitted == []


def test_a_burst_of_identical_speeds_still_collapses_to_one_write():
    # The clamp is what makes the suppression still bite: the belt ramps at
    # ~0.25 km/h per second, so mashing a step button resolves to the same
    # requested number until the belt has actually moved.
    b = make_bridge()
    emitted = []
    b.emit = emitted.append
    assert b.handle_command({"cmd": "speed", "kmh": 3.0}) == frames.set_speed(3.0)
    assert b.handle_command({"cmd": "speed", "kmh": 3.0}) is None
    assert [e["code"] for e in emitted] == ["repeat_suppressed"]


def test_the_idle_poll_never_outlasts_the_staleness_gate():
    # Measured on hardware 2026-08-25: with a 5.0 s idle poll against a 3.0 s
    # gate, 40% of button presses came back `stale_link` and the belt did not
    # move. It reads as a flaky plugin, and the obvious fix from that symptom is
    # to loosen the gate -- which is a safety gate. Assert the relationship
    # instead, so the next person tuning the radio has to see it.
    from walkingpad.bridge import IDLE_POLL_INTERVAL_S
    from walkingpad.safety import Safety
    assert IDLE_POLL_INTERVAL_S < Safety().stale_after_s


def test_a_pending_start_keeps_the_poll_fast():
    # The start window is seconds wide and only visible in a status frame, so
    # backing off during it would let the pad reach its own 3.5 km/h unseen.
    import time as _time
    from walkingpad.bridge import POLL_INTERVAL_S
    b = make_bridge()
    b._stopped_since = _time.monotonic() - 600
    assert b._poll_interval() == IDLE_POLL_INTERVAL_S
    b.handle_command({"cmd": "start", "kmh": 2.0})
    assert b._poll_interval() == POLL_INTERVAL_S
