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

# How far a goal may sit from the ACTUAL belt speed. See clamp_goal().
MAX_GOAL_LEAD_KMH = 1.0


class Safety:
    def __init__(self, max_speed: float = 6.0, max_step: float = 0.5,
                 stale_after_s: float = 3.0,
                 pad_start_speed: float = PAD_START_SPEED_KMH,
                 max_goal_lead: float = MAX_GOAL_LEAD_KMH,
                 min_goal: float = MIN_GOAL_KMH):
        self.max_speed = max_speed
        self.max_step = max_step
        self.stale_after_s = stale_after_s
        self.pad_start_speed = pad_start_speed
        self.max_goal_lead = max_goal_lead
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
        """Bound a live goal against the ACTUAL belt speed.

        A goal is not a command. It is a standing intention the bridge walks the
        belt toward, one clamp_speed() step per status frame, so the belt still
        never changes by more than max_step at a time and max_speed is still the
        ceiling. What this adds is a bound on how far ahead of reality the
        intention may run.

        That bound is the point. The per-action clamp was written for "the worst
        case of a stuck key or a double click is one small increment", and a goal
        with no lead limit gives that back: a key that repeats walks the goal to
        the ceiling and the belt follows it there. Capping the lead means the
        worst an unattended input can do at any instant is max_goal_lead, and it
        cannot compound until the belt has actually caught up -- which takes real
        seconds at ~0.25 km/h/s, in front of someone who can see it happening.

        Symmetric on purpose. Slowing down is the safer direction, but a goal
        that can drop arbitrarily far still asks for a deceleration nobody
        braced for, and symmetry is easier to predict than a special case.
        """
        low = max(self.min_goal, current - self.max_goal_lead)
        high = min(self.max_speed, current + self.max_goal_lead)
        # A belt already slower than min_goal must not be dragged up to it.
        low = min(low, high)
        value = min(max(requested, low), high)
        return round(value, 1)

    def clamp_goal_absolute(self, requested: float) -> float:
        """Bound an EXPLICIT goal to the pad's own range, and nothing tighter.

        Deliberately without clamp_goal()'s max_goal_lead bound. Read that
        docstring first: the lead bound is aimed squarely at the REPEATING path
        -- a key that repeats, a double click -- where each press is worth one
        increment and nobody has stated a destination. An absolute value that a
        person typed and committed is the opposite of that. It is one
        deliberate act naming where they want to end up, and refusing it
        because the belt has not got there yet just makes them type it again.

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
