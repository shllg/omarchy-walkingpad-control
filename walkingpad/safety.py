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
