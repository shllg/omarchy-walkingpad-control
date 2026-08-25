# tests/test_start_sequence.py
"""The two-part start, which is the most dangerous thing in the bridge.

AGENTS.md fact 12: the pad ramps to ~3.5 km/h on START regardless of the speed
it has stored, and ignores a speed command issued while the belt is stopped. So
a start that means "walk at 2.0" has to send the speed AFTER the belt is moving,
inside the short window at belt=running, speed=0.0. Getting that wrong puts
3.5 km/h under someone's feet, which is why it lives here in Python where it can
be tested rather than in QML where it cannot.
"""
import time

from walkingpad import frames
from walkingpad.bridge import Bridge, START_SPEED_WINDOW_S
from walkingpad.safety import PAD_START_SPEED_KMH

# Real captured frames. There is no capture of belt=running at speed 0.0 -- the
# window is measured but was never recorded -- so RUNNING_0 below is derived
# from the running frame with only its speed byte changed.
COUNTDOWN = bytes.fromhex("f8a209000100006000000500006f1e000300a1fd")   # belt 9
RUNNING_35 = bytes.fromhex("f8a201230100006500000500007414000300bcfd")  # 3.5
STOPPED = bytes.fromhex("f8a200000100007300000600008314000300b6fd")


def running_at(kmh: float) -> bytes:
    """A captured running frame with its speed byte rewritten."""
    body = bytearray(RUNNING_35[1:-2])
    body[2] = int(round(kmh * 10))
    return (bytes([frames.STATUS_HEAD[0]]) + bytes(body)
            + bytes([frames.checksum(body), frames.FRAME_END]))


RUNNING_0 = running_at(0.0)


class RecordingTransport:
    """Records writes without answering them -- the pad ACKs nothing anyway."""

    def __init__(self):
        self.connected = True
        self.writes = []

    def find(self, should_continue=None):
        return None

    def connect(self, device):
        pass

    def subscribe(self, callback):
        pass

    def write(self, payload):
        self.writes.append(payload)

    def disconnect(self):
        self.connected = False


def make_bridge():
    t = RecordingTransport()
    b = Bridge(transport=t)
    b._last_status_age = lambda: 0.0   # link is fresh
    b.emit = lambda obj: None
    b.belt, b.mode, b.current_speed = "stopped", "manual", 0.0
    return b, t


def feed(bridge, frame):
    bridge.on_notify(None, frame)


# -- the happy path ---------------------------------------------------------

def test_the_frame_used_for_the_start_window_decodes_as_running_at_zero():
    # Guards the derivation above: if the checksum or layout were wrong the
    # whole suite would pass against a frame the parser rejects.
    status = frames.parse_status(RUNNING_0)
    assert status is not None
    assert (status.belt, status.speed_kmh) == ("running", 0.0)


def test_start_sends_only_start_at_first():
    b, t = make_bridge()
    assert b.handle_command({"cmd": "start", "kmh": 2.0}) == frames.start()
    assert t.writes == []          # nothing extra went out yet


def test_the_speed_lands_once_the_belt_is_running():
    b, t = make_bridge()
    b._send(b.handle_command({"cmd": "start", "kmh": 2.0}))
    feed(b, COUNTDOWN)             # 3-2-1, fact 14 -- not yet
    assert t.writes == [frames.start()]
    feed(b, RUNNING_0)             # the window opens
    assert t.writes == [frames.start(), frames.set_speed(2.0)]


def test_the_speed_is_sent_exactly_once():
    # Fact 17: never re-send. A second running frame must add nothing.
    b, t = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 2.0})
    feed(b, RUNNING_0)
    feed(b, RUNNING_0)
    feed(b, RUNNING_35)
    assert t.writes == [frames.set_speed(2.0)]


def test_target_is_reported_from_the_moment_start_is_pressed():
    b, _ = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 2.0})
    assert b.commanded_speed == 2.0


# -- the cap ----------------------------------------------------------------

def test_a_start_speed_below_the_pads_own_is_passed_through():
    b, t = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 2.0})
    feed(b, RUNNING_0)
    assert t.writes == [frames.set_speed(2.0)]


def test_a_start_speed_above_the_pads_own_is_capped_not_obeyed():
    # The cap is what makes this provably safe: a start can be slowed down and
    # has no way to be sped up past what the pad does on its own.
    b, t = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 6.0})
    feed(b, RUNNING_0)
    assert t.writes == [frames.set_speed(PAD_START_SPEED_KMH)]


def test_the_step_clamp_is_not_applied_to_a_start():
    # Running 2.0 through clamp_speed() against the observed 0.0 would yield
    # 0.5 and make every start useless. Documented here so nobody "fixes" it.
    b, _ = make_bridge()
    assert b.safety.clamp_speed(0.0, 2.0) == 0.5
    assert b.safety.clamp_start_speed(2.0) == 2.0


def test_max_speed_still_bounds_a_start():
    b, t = make_bridge()
    b.safety.max_speed = 1.5
    b.handle_command({"cmd": "start", "kmh": 6.0})
    feed(b, RUNNING_0)
    assert t.writes == [frames.set_speed(1.5)]


# -- disarming --------------------------------------------------------------

def test_a_bare_start_sends_no_speed_at_all():
    b, t = make_bridge()
    assert b.handle_command({"cmd": "start"}) == frames.start()
    feed(b, RUNNING_0)
    assert t.writes == []


def test_stop_cancels_a_pending_start():
    # The worst possible bug in this file would be a stop followed by the speed
    # command a start had armed.
    b, t = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 2.0})
    b.belt, b.current_speed = "starting", 0.0
    assert b.handle_command({"cmd": "stop"}) == frames.stop()
    feed(b, RUNNING_0)
    assert t.writes == []
    assert b._pending_start is None


def test_an_explicit_speed_supersedes_a_pending_start():
    b, t = make_bridge()
    b.handle_command({"cmd": "start", "kmh": 2.0})
    b.handle_command({"cmd": "speed", "kmh": 0.5})
    feed(b, RUNNING_0)
    assert t.writes == []


def test_a_start_that_never_takes_expires_instead_of_lingering():
    # Otherwise the speed would fire whenever the belt next moved -- possibly an
    # hour later, when someone started it at the panel by hand.
    b, t = make_bridge()
    emitted = []
    b.emit = emitted.append
    b.handle_command({"cmd": "start", "kmh": 2.0})
    b._pending_start = (2.0, time.monotonic() - START_SPEED_WINDOW_S - 1)
    feed(b, STOPPED)
    assert [e["code"] for e in emitted if e["t"] == "error"] == ["start_unconfirmed"]
    feed(b, RUNNING_0)
    assert t.writes == []
    assert b.commanded_speed is None


def test_a_start_on_an_already_running_belt_arms_nothing():
    # The current frame is already "running", so a follow-up would apply a speed
    # change that never passed the per-action step clamp.
    b, t = make_bridge()
    b.belt, b.current_speed = "running", 2.5
    assert b.handle_command({"cmd": "start", "kmh": 6.0}) == frames.start()
    assert b._pending_start is None
    feed(b, RUNNING_35)
    assert t.writes == []


# -- the existing gates still apply -----------------------------------------

def test_a_refused_start_arms_nothing_in_standby():
    b, t = make_bridge()
    b.mode = "standby"
    assert b.handle_command({"cmd": "start", "kmh": 2.0}) is None
    assert b._pending_start is None
    b.mode = "manual"
    feed(b, RUNNING_0)
    assert t.writes == []


def test_a_refused_start_arms_nothing_on_a_stale_link():
    b, t = make_bridge()
    b._last_status_age = lambda: 99.0
    assert b.handle_command({"cmd": "start", "kmh": 2.0}) is None
    assert b._pending_start is None
