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

# Measured 2026-08-24: the pad ramps to roughly this on START whatever speed it
# has stored, and ignores a speed command sent while it is stopped.
PAD_START_SPEED_KMH = 3.5

# A goal is never allowed to reach 0.0. Whether a set-speed of zero stops the
# belt is an OPEN hardware question (docs/smoke-test.md §4) and the stop opcode
# is a toggle, so normal use must not wander into probing it by accident.
MIN_GOAL_KMH = 0.5


class Safety:
    def __init__(self, max_speed: float = 6.0, max_step: float = 0.5,
                 stale_after_s: float = 3.0,
                 pad_start_speed: float = PAD_START_SPEED_KMH,
                 min_goal: float = MIN_GOAL_KMH):
        self.max_speed = max_speed
        self.max_step = max_step
        self.stale_after_s = stale_after_s
        self.pad_start_speed = pad_start_speed
        self.min_goal = min_goal

    def clamp_speed(self, current: float, requested: float) -> float:
        low = current - self.max_step
        high = current + self.max_step
        value = min(max(requested, low), high)
        value = min(value, self.max_speed)
        value = max(value, 0.0)
        return round(value, 1)

    def clamp_start_speed(self, requested: float) -> float:
        """Speed to command in the window just after START.

        Deliberately NOT clamp_speed(). That one governs a CHANGE to a belt
        already moving, and measures the change against the current speed. In
        the start window the current speed is 0.0, so routing a start through it
        would clamp every start to 0.5 km/h -- never what the caller asked for.

        The real hazard here is the pad's own behaviour: measured 2026-08-24, it
        ramps to ~3.5 km/h on START regardless of the speed it has stored, and
        ignores a speed command issued while the belt is stopped. The belt is
        going to 3.5 whatever we do. The only useful thing to send in that
        window is a LOWER number, so the cap is the pad's own start speed. That
        makes the property provable rather than argued: this can slow a start
        down, and has no way to speed one up.
        """
        value = min(requested, self.pad_start_speed, self.max_speed)
        return round(max(value, 0.0), 1)

    def clamp_goal(self, current: float, requested: float) -> float:
        """Bound a goal to the pad's own range, and nothing tighter.

        A goal is not a command. It is a standing intention the bridge walks the
        belt toward, one clamp_speed() step per status frame, so the belt still
        never changes by more than max_step at a time and max_speed is still the
        ceiling. Those are the bounds that matter with someone standing on the
        belt, and nothing here touches them.

        ⚠ This ALSO used to cap how far ahead of the ACTUAL speed the intention
        could sit -- max_goal_lead, 1.0 km/h. REMOVED by decision on 2026-08-28,
        because that bound broke a promise the UI makes out loud: the button is
        labelled "+0.5", so three presses must mean +1.5. Measured on hardware
        that day, three presses from 2.0 km/h produced a goal of 3.0 and a belt
        that settled at 2.8 -- the cap ate the third press and the arrival
        tolerance in bridge.py ate the remainder. A control that does not do what
        its own label says is its own kind of hazard.

        What that gives up, stated plainly so nobody reinstates the cap as a bug
        fix: a key that repeats now walks the goal to max_speed and the servo
        follows it there, one step per frame, with nobody pressing anything. The
        old bound held the worst instantaneous case to 1.0 km/h. What remains is
        the per-write clamp_speed() step, the "belt must be running" gate and the
        staleness gate, all in _service_goal().

        `current` is no longer read. The signature is kept because bridge.py
        still routes the stepped and absolute paths separately; collapsing the
        two is a follow-up, not this change.
        """
        value = min(max(requested, self.min_goal), self.max_speed)
        return round(value, 1)

    def clamp_goal_absolute(self, requested: float) -> float:
        """Bound an EXPLICIT goal to the pad's own range, and nothing tighter.

        ⚠ As of 2026-08-28 this is identical to clamp_goal(), which lost its
        max_goal_lead bound the same day. It is kept as a separate name because
        bridge.py still distinguishes the two paths through the `absolute` flag;
        collapsing them is a flagged follow-up. Until then, read clamp_goal()'s
        docstring for why the lead bound went.

        What this does NOT relax:
          - max_speed is still the ceiling.
          - min_goal is still the floor, so a goal still never reaches 0.0 and
            normal use never probes whether set-speed-0 stops the belt.
          - the belt is still walked there one clamp_speed() step per status
            frame, so it never changes by more than max_step at a time and
            never moves faster than the pad's own ~0.25 km/h/s ramp.

        So this changes how far ahead the standing intention may SIT, not how
        fast the belt may move. Requested on 2026-08-25 after the lead bound
        turned a typed 3.5 into 1.0 on a stopped belt, where the goal is not
        serviced at all and the bound was protecting nothing.
        """
        value = min(max(requested, self.min_goal), self.max_speed)
        return round(value, 1)

    def may_move(self, last_status_age_s: float) -> bool:
        return last_status_age_s <= self.stale_after_s

    def may_stop(self, belt: str, speed_kmh: float, last_status_age_s: float) -> bool:
        """Whether it is safe to SEND the stop opcode.

        ⚠ This gate exists because the stop opcode is a TOGGLE: measured
        2026-08-25, sending it to a stopped belt STARTS it, countdown and all.
        An earlier version of this file said stop was always permitted including
        on a stale link. That was exactly backwards -- with no recent frame we do
        not know which way the toggle will go, and half the outcomes start a
        treadmill nobody asked to start.

        So stopping requires recent evidence that the belt is actually moving.
        When that evidence is missing the honest answer is to refuse and say so:
        the panel and the remote still stop the belt, and telling the user to use
        them beats firing a command that may start it.
        """
        if last_status_age_s > self.stale_after_s:
            return False
        return belt in ("running", "starting") or speed_kmh > 0
