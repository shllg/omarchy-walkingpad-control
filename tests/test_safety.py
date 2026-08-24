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


def test_may_stop_requires_evidence_the_belt_is_moving():
    # The stop opcode is a toggle: it starts a stopped belt. Sending it without
    # evidence of motion is sending a start.
    s = Safety()
    assert s.may_stop("running", 2.5, 0.0)
    assert s.may_stop("starting", 0.0, 0.0)      # cancel the countdown
    assert s.may_stop("unknown", 1.2, 0.0)       # speed alone is evidence enough
    assert not s.may_stop("stopped", 0.0, 0.0)
    assert not s.may_stop("idle", 0.0, 0.0)


def test_may_stop_refuses_on_a_stale_link():
    s = Safety()
    assert not s.may_stop("running", 2.5, 99.0)
