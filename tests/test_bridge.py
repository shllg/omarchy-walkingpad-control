import json

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
