# tests/test_goal.py
"""The live goal, which is the first autonomous writer in this bridge.

Everything else here sends a frame because a person just pressed something.
This walks the belt toward a standing intention on its own, so the tests below
are mostly about what it REFUSES to do.

The safety argument rests on three independent bounds, one test each:
  1. every write is still one clamp_speed() step from the ACTUAL speed
  2. nothing is written unless a fresh frame says the belt is running
  3. it stops on arrival, and never repeats a value

⚠ There was a fourth: a stepped goal could not sit more than max_goal_lead from
the actual speed. It was removed on 2026-08-28 because it silently truncated
presses the "+0.5" label had already promised -- see Safety.clamp_goal(). The
three above are now the whole argument, and they apply identically to the
stepped and absolute paths.
"""
from walkingpad import frames
from walkingpad.bridge import Bridge
from walkingpad.safety import MIN_GOAL_KMH, Safety

from tests.test_start_sequence import RecordingTransport, running_at

COUNTDOWN = bytes.fromhex("f8a209000100006000000500006f1e000300a1fd")
STOPPED = bytes.fromhex("f8a200000100007300000600008314000300b6fd")
IDLE_STANDBY = bytes.fromhex("f8a204000200000000000000000000000000a8fd")


def make_bridge(speed=2.0):
    t = RecordingTransport()
    b = Bridge(transport=t)
    b._last_status_age = lambda: 0.0
    b.emit = lambda obj: None
    b.belt, b.mode, b.current_speed = "running", "manual", speed
    return b, t


def feed(bridge, frame):
    bridge.on_notify(None, frame)


# -- 1. the goal is bounded by the pad, and by nothing else -----------------

def test_a_goal_may_run_to_the_pads_ceiling():
    # Was capped at 2.0 + 1.0 before 2026-08-28.
    b, _ = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 6.0})
    assert b.goal_speed == 6.0


def test_a_goal_may_run_down_to_the_floor():
    # Was capped at 4.0 - 1.0 before 2026-08-28.
    b, _ = make_bridge(speed=4.0)
    b.handle_command({"cmd": "goal", "kmh": 1.0})
    assert b.goal_speed == 1.0


def test_n_presses_of_one_step_are_worth_n_steps():
    # The promise the button's own label makes, and the reason the lead bound
    # went. Measured on hardware 2026-08-28: three presses of "+0.5" from
    # 2.0 km/h produced a goal of 3.0 and a belt that settled at 2.8. The panel
    # accumulates the presses; this pins that the bridge honours the total
    # rather than cutting it back to where the belt happens to be.
    b, _ = make_bridge(speed=2.0)
    step = b.safety.max_step
    for n in (1, 2, 3):
        b.handle_command({"cmd": "goal", "kmh": 2.0 + n * step})
        assert b.goal_speed == 2.0 + n * step


def test_a_goal_never_reaches_zero():
    # Whether set-speed 0 stops the belt is an OPEN hardware question and the
    # stop opcode is a toggle. Normal use must not wander into probing it.
    b, _ = make_bridge(speed=1.0)
    b.handle_command({"cmd": "goal", "kmh": 0.0})
    assert b.goal_speed >= MIN_GOAL_KMH


def test_max_speed_still_bounds_a_goal():
    b, _ = make_bridge(speed=5.8)
    b.safety.max_speed = 6.0
    b.handle_command({"cmd": "goal", "kmh": 6.0})
    assert b.goal_speed == 6.0


def test_a_repeating_key_can_now_walk_the_goal_to_the_ceiling():
    # ⚠ This is the hazard the removed lead bound used to hold off, pinned here
    # deliberately rather than left to be discovered later. Before 2026-08-28
    # twenty presses resolved to 3.0 and could not compound until the belt had
    # caught up; now they reach the pad's ceiling.
    #
    # It is recorded as a test, not a regression: the decision that day was that
    # the "+0.5" label's promise outranks the bound. What still holds the belt is
    # clamp_speed() -- every write is one step from the ACTUAL speed, so the belt
    # cannot jump even with the intention sitting at 6.0.
    b, _ = make_bridge(speed=2.0)
    for _ in range(20):
        b.handle_command({"cmd": "goal", "kmh": 6.0})
    assert b.goal_speed == 6.0


# -- 2. a goal writes nothing by itself -------------------------------------

def test_setting_a_goal_writes_no_frame():
    # One writer only. A burst of goal updates must not become a burst of
    # writes, however fast the buttons are pressed.
    b, t = make_bridge()
    for kmh in (2.5, 3.0, 2.5, 3.0, 2.5):
        assert b.handle_command({"cmd": "goal", "kmh": kmh}) is None
    assert t.writes == []


def test_a_repeated_goal_is_not_reported_as_suppressed():
    # It writes nothing, so there is no burst to throttle -- and a red refusal
    # under a button that worked is worse than useless.
    b, _ = make_bridge()
    emitted = []
    b.emit = emitted.append
    b.handle_command({"cmd": "goal", "kmh": 2.5})
    b.handle_command({"cmd": "goal", "kmh": 2.5})
    assert emitted == []


# -- 3. convergence, one clamped step per frame -----------------------------

def test_the_belt_is_walked_toward_the_goal_one_step_per_frame():
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    feed(b, running_at(2.0))
    assert t.writes == [frames.set_speed(2.5)]
    feed(b, running_at(2.5))
    assert t.writes == [frames.set_speed(2.5), frames.set_speed(3.0)]


def test_no_single_write_ever_exceeds_one_step():
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 6.0})
    feed(b, running_at(2.0))
    assert t.writes == [frames.set_speed(2.5)]


def test_convergence_stops_on_arrival():
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 2.5})
    feed(b, running_at(2.0))
    feed(b, running_at(2.5))
    assert b.goal_speed is None
    assert t.writes == [frames.set_speed(2.5)]


def test_a_stalled_belt_is_not_hammered_with_the_same_value():
    # If the belt is not moving toward the goal, saying it again louder is not
    # going to help, and fact 17 is explicit about re-sending on a clock.
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    for _ in range(10):
        feed(b, running_at(2.0))
    assert t.writes == [frames.set_speed(2.5)]


def test_a_goal_can_be_reversed_mid_flight():
    # The reason this is a goal and not a queue: pressing down after up has to
    # move the same intention, not schedule a second one.
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    b.handle_command({"cmd": "goal", "kmh": 1.5})
    assert b.goal_speed == 1.5
    feed(b, running_at(2.0))
    assert t.writes == [frames.set_speed(1.5)]


# -- 4. what it refuses to do -----------------------------------------------

def test_nothing_is_written_while_the_belt_is_not_running():
    # Fact 11: a speed command never starts a stopped belt. Fact 8: "running"
    # is the only state that means it is moving.
    for frame in (STOPPED, COUNTDOWN):
        b, t = make_bridge(speed=0.0)
        b.handle_command({"cmd": "goal", "kmh": 2.0})
        feed(b, frame)
        assert t.writes == []


def test_nothing_is_written_in_standby():
    b, t = make_bridge(speed=0.0)
    b.handle_command({"cmd": "goal", "kmh": 2.0})
    feed(b, IDLE_STANDBY)
    assert t.writes == []


def test_nothing_is_written_on_a_stale_link():
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    b._last_status_age = lambda: 99.0
    feed(b, running_at(2.0))
    assert t.writes == []


def test_stop_cancels_a_goal():
    # The worst bug this file could have: a stop, then the goal quietly putting
    # the belt back into motion on the next frame.
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    assert b.handle_command({"cmd": "stop"}) == frames.stop()
    assert b.goal_speed is None
    feed(b, running_at(2.0))
    assert t.writes == []


def test_start_cancels_a_goal():
    b, _ = make_bridge(speed=0.0)
    b.belt = "stopped"
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    b.handle_command({"cmd": "start", "kmh": 2.0})
    assert b.goal_speed is None


def test_an_explicit_speed_cancels_a_goal():
    b, _ = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    b.handle_command({"cmd": "speed", "kmh": 2.5})
    assert b.goal_speed is None


def test_a_goal_waits_for_a_pending_start_to_land():
    # Two writers inside fact 12's window would fight over the belt.
    b, t = make_bridge(speed=0.0)
    b.belt = "stopped"
    b.handle_command({"cmd": "start", "kmh": 2.0})
    b.goal_speed = 3.0                     # as if set during the countdown
    feed(b, running_at(0.0))
    assert t.writes == [frames.set_speed(2.0)]      # the start's speed, not the goal's


def test_the_goal_is_reported_on_the_wire():
    import json
    b, _ = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    emitted = []
    b.emit = Bridge.emit.__get__(b)        # undo make_bridge's silencing
    b._write = emitted.append
    feed(b, running_at(2.0))
    assert json.loads(emitted[0])["goal"] == 3.0


def test_a_landing_start_and_a_goal_never_write_in_the_same_frame():
    # Otherwise the goal overrides the speed the start had just placed, on the
    # very frame fact 12's window was there to catch.
    b, t = make_bridge(speed=0.0)
    b.belt = "stopped"
    b.handle_command({"cmd": "start", "kmh": 2.0})
    b.goal_speed = 3.0
    feed(b, running_at(0.0))
    assert t.writes == [frames.set_speed(2.0)]


# -- 5. an explicit value the user typed ------------------------------------

def test_an_absolute_goal_ignores_the_lead_bound():
    # The whole point. Reported from hardware 2026-08-25: on a stopped belt the
    # lead bound turned a typed 3.5 into 1.0, on a path where the goal is not
    # serviced at all and the bound was protecting nothing.
    b, _ = make_bridge(speed=0.0)
    b.handle_command({"cmd": "goal", "kmh": 3.5, "absolute": True})
    assert b.goal_speed == 3.5


def test_an_absolute_goal_still_stops_at_max_speed():
    b, _ = make_bridge(speed=2.0)
    b.safety.max_speed = 6.0
    b.handle_command({"cmd": "goal", "kmh": 99.0, "absolute": True})
    assert b.goal_speed == 6.0


def test_an_absolute_goal_still_never_reaches_zero():
    # Whether set-speed 0 stops the belt is still an OPEN hardware question and
    # the stop opcode is still a toggle. This path must not probe it either.
    b, _ = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 0.0, "absolute": True})
    assert b.goal_speed >= MIN_GOAL_KMH


def test_a_distant_absolute_goal_still_moves_the_belt_one_step_per_frame():
    # THE safety property. Dropping the lead bound changes how far ahead the
    # intention may sit, not how fast the belt may move -- if this ever fails,
    # a typed 6.0 becomes a single continuous acceleration under someone.
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 6.0, "absolute": True})
    feed(b, running_at(2.0))
    assert t.writes == [frames.set_speed(2.5)]
    feed(b, running_at(2.5))
    assert t.writes == [frames.set_speed(2.5), frames.set_speed(3.0)]


def test_stop_cancels_an_absolute_goal_too():
    b, t = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 6.0, "absolute": True})
    assert b.handle_command({"cmd": "stop"}) == frames.stop()
    assert b.goal_speed is None
    feed(b, running_at(2.0))
    assert t.writes == []


def test_an_absolute_goal_is_still_refused_while_the_belt_is_not_running():
    b, t = make_bridge(speed=0.0)
    b.handle_command({"cmd": "goal", "kmh": 5.0, "absolute": True})
    feed(b, STOPPED)
    assert t.writes == []


def test_both_goal_paths_bound_identically_now():
    # The `absolute` flag used to be the only way past the lead bound. With that
    # bound gone the two paths agree, and the flag survives only because
    # collapsing them is a separate change. If they ever diverge again, the
    # stepping path is the one that must not be the tighter of the two.
    stepped, _ = make_bridge(speed=2.0)
    stepped.handle_command({"cmd": "goal", "kmh": 6.0})
    absolute, _ = make_bridge(speed=2.0)
    absolute.handle_command({"cmd": "goal", "kmh": 6.0, "absolute": True})
    assert stepped.goal_speed == absolute.goal_speed == 6.0


def test_a_goal_is_not_declared_reached_while_a_step_is_still_missing():
    # Measured 2026-08-28: with the tolerance at half a step (0.25), a goal of
    # 3.0 was declared reached and discarded with the belt at 2.8, so the user's
    # third press vanished. The pad reports speed to 0.1; arrival means arrival.
    b, _ = make_bridge(speed=2.0)
    b.handle_command({"cmd": "goal", "kmh": 3.0})
    feed(b, running_at(2.5))
    feed(b, running_at(2.8))
    assert b.goal_speed == 3.0
    feed(b, running_at(3.0))
    assert b.goal_speed is None
