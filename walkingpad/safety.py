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
