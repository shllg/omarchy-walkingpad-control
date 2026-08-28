"""Bounded persistence for decoded WalkingPad status frames.

The bridge's poll loop is safety-critical (AGENTS.md facts 1 and 9), so record()
does only bounded appends and one throttled atomic state rewrite. Aggregation and
history reads belong to the separate query process described by design §3.1.

The attribution invariant is load-bearing: after design §1.4's first-frame
baseline, ``attributed`` advances if and only if the same delta is added to some
bout. A delta too small or too uncertain to place stays in ``last_pad -
attributed``; advancing both counters without a bout would make real walking
vanish from both history and the residual.
"""
import errno
import json
import os
import stat
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_STORE_DIR = os.path.join(
    os.environ.get("XDG_STATE_HOME")
    or os.path.join(os.path.expanduser("~"), ".local", "state"),
    "walkingpad",
)
TAIL_S = 5.0
MIN_RESIDUAL_ELAPSED_S = 30
MIN_RESIDUAL_DIST_KM = 0.05
STATE_WRITE_INTERVAL_S = 10.0

# store.py has no BLE knowledge (design §3.1), and record() sees only freshly
# decoded frames, never a "link reconnected" event. While attached, the bridge
# polls every 1-2 s; a larger wall-clock gap therefore means the link disappeared
# and returned. These values mirror the bridge's measured cadence and watchdog,
# but remain local so this arithmetic-only module does not import BLE policy.
MAX_POLL_INTERVAL_S = 2.0
LINK_GAP_S = 15.0

# state.json holds five small scalars plus one open bout -- a few hundred bytes
# in practice. The cap exists so a corrupt or pre-planted file cannot turn the
# constructor's read into the unbounded work plan §3.1 forbids the writer.
MAX_STATE_BYTES = 1 << 20

# _prepare_directory refuses a symlinked store root and samples directory, but
# that says nothing about the predictable leaf names inside them. O_NOFOLLOW
# refuses a symlink at the final component; O_NONBLOCK keeps a FIFO from
# blocking the call before the S_ISREG check below can reject it. Regular files
# ignore O_NONBLOCK on Linux, so the flag costs nothing once the check passes.
_APPEND_FLAGS = (
    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
)
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK


class StoreLeafError(OSError):
    """A predictable path inside the store is not the regular file it must be.

    Redirecting an append into another user's file, and handing the constructor
    a FIFO it would block on forever, both need the answer plan §3.1 gives a
    full disk: degrade the whole store once and leave belt control alone. The
    blocking case is why this cannot be left to the caller's try/except -- a
    blocked open() raises nothing, and Store is built before bridge.run().
    """


def _open_leaf(path, flags):
    """Open a leaf inside the store, refusing anything but a regular file."""
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        # ELOOP is O_NOFOLLOW meeting a symlink; ENXIO is O_WRONLY|O_NONBLOCK
        # meeting a FIFO with no reader. Both mean the leaf was replaced.
        if exc.errno in (errno.ELOOP, errno.ENXIO):
            raise StoreLeafError(
                f"refusing non-regular file: {path}"
            ) from exc
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreLeafError(f"refusing non-regular file: {path}")
    except BaseException:
        os.close(fd)
        raise
    return fd


class Store:
    def __init__(self, path, emit=None, now=None):
        self._emit = emit
        self._now = now or time.time
        self._disabled = False
        self._closed = False

        self.epoch = 0
        self.last_pad = None
        self.attributed = {"elapsed_s": 0, "dist_km": 0.0, "steps": 0}
        self.open_bout = None
        self.last_seen_wall = None

        self._was_active = False
        self._tail_deadline = None
        self._last_state_write = None

        self._root = Path(path)
        self._samples_dir = self._root / "samples"
        self._bouts_path = self._root / "bouts.jsonl"
        self._state_path = self._root / "state.json"
        self._links_path = self._root / "links.jsonl"

        # Design §3.1 requires a full disk or permission error to degrade to
        # "no stats", never a bridge crash that takes belt controls with it. The
        # construction path is covered by that rule too, so every filesystem
        # operation here shares record()'s fail-closed error boundary.
        try:
            self._prepare_directory(self._root, "store root")
            # Create this eagerly so each 1 Hz sample append stays bounded and
            # never performs a directory-existence check (design §3.1).
            self._prepare_directory(self._samples_dir, "samples directory")
            self._load_state()
        except Exception as exc:
            self._fail(exc)

    def _prepare_directory(self, path, label):
        # Design §2.1 treats this tree as private health data. Following a
        # pre-planted symlink would redirect those writes and chmod its target;
        # refuse both before and after mkdir, then let design §3.1 degrade the
        # whole store once instead of touching a path we do not own.
        if path.is_symlink():
            raise OSError(f"refusing symlink {label}: {path}")
        path.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            raise OSError(f"refusing symlink {label}: {path}")
        # mkdir(mode=...) is filtered through umask. chmod afterwards is what
        # guarantees 0700 for personal health data (design §2.1).
        os.chmod(path, 0o700)

    def _load_state(self):
        try:
            fd = _open_leaf(self._state_path, _READ_FLAGS)
        except StoreLeafError:
            # Tampering, not corruption: escape to the constructor's _fail.
            raise
        except OSError:
            # A missing file is the normal first run, and anything else
            # unreadable is corruption the next atomic write heals, exactly as
            # before. Opening and catching beats an exists() check, which left
            # a window for the path to change underneath the open.
            return
        try:
            with os.fdopen(fd, "rb") as fh:
                # One byte past the cap separates "exactly at the limit" from
                # "truncated by it" without reading whatever follows.
                raw = fh.read(MAX_STATE_BYTES + 1)
        except OSError:
            return
        if len(raw) > MAX_STATE_BYTES:
            # Too large to be state this module wrote. Treated as corrupt
            # rather than parsed, so json never sees the unbounded input.
            return
        try:
            data = json.loads(raw)
        except ValueError:
            # A corrupt state file is distinct from the write failure named by
            # this module's only error code. Starting fresh lets the next
            # successful atomic write heal it without a misleading emission.
            return

        self.epoch = data.get("epoch", 0)
        self.last_pad = data.get("last_pad", None)
        self.attributed = data.get(
            "attributed", {"elapsed_s": 0, "dist_km": 0.0, "steps": 0}
        )
        self.open_bout = data.get("open_bout", None)
        self.last_seen_wall = data.get("last_seen_wall", None)

        if self.open_bout is not None and self.last_seen_wall is not None:
            # Design §1.4: bouts never span a bridge lifetime. Close at the
            # final frame the old process actually saw, never at startup time.
            self._finish_bout(end=self.last_seen_wall)
            self._write_state(self.last_seen_wall, force=True)

    def record(self, status, commanded_speed, goal_speed):
        if self._disabled:
            return
        try:
            self._record_inner(status, commanded_speed, goal_speed)
        except Exception as exc:
            self._fail(exc)

    def record_link(self, conn_s, answered_s, frames, why):
        """Append one row for a BLE connection that has just ended.

        Design spec open question 9: this pad answers status queries for around
        29 s after a connection and then goes quiet while its radio keeps
        acknowledging every packet, yet one earlier session answered for 1988 s
        on the same pad, firmware and code. Nothing distinguishes the two from
        the outside, and the trigger is still unknown.

        `answered_s` was added to the bridge's `link/lost` event to make that
        visible, but it only ever reached stdout, and the installed plugin's
        service reads `state` and `msg` and drops the rest. So the one mode that
        could accumulate hundreds of connections -- ordinary daily use -- was
        recording nothing at all. This file is where that accumulates.

        ⚠ ONE ROW PER ENDED CONNECTION, never one per link transition. Fact 3
        makes a sleeping pad the normal resting state, and the bridge re-emits
        `absent` on every rescan for as long as that lasts; a row per transition
        would grow without bound while precisely nothing was happening. A row is
        written only when a connection that actually existed has ended, so an
        absent pad writes nothing.

        ⚠ NOTHING MAY READ THIS BACK INTO ATTRIBUTION. Design §2.2 keeps history
        rebuildable from samples alone, and every store written before this file
        existed must keep reading identically -- so LINK_GAP_S stays inferred
        from the wall-clock gap between samples, exactly as the note above it
        says, and is not replaced by these rows. This is diagnostic evidence for
        one open question, not an input to the attribution model.
        """
        if self._disabled:
            return
        try:
            self._write_link(conn_s, answered_s, frames, why)
        except Exception as exc:
            self._fail(exc)

    def _fail(self, exc):
        self._disabled = True
        if self._emit is not None:
            try:
                self._emit({
                    "t": "error",
                    "code": "store_write_failed",
                    "msg": str(exc),
                })
            except Exception:
                # Even a broken output path must not propagate into belt control.
                pass

    def _record_inner(self, status, commanded_speed, goal_speed):
        wall = self._now()
        # The pad quantises distance to 10 m. Round once at the input boundary so
        # reset checks, attribution, samples and persisted state use one value.
        dist_km = round(status.distance_km, 2)
        pad_now = {
            "elapsed_s": status.elapsed_s,
            "dist_km": dist_km,
            "steps": status.steps,
        }
        running = status.belt == "running"
        force_write = False

        if self.last_pad is None:
            # Design §1.4 "First-ever frame": the preceding uncertainty window
            # is unbounded, so assigning its counters to any bout would fabricate
            # history. Adopt them as the baseline and write no residual.
            self.attributed = dict(pad_now)
            self.last_pad = dict(pad_now)
            self.last_seen_wall = wall
            if running:
                self.open_bout = self._new_bout(wall)
            force_write = True
        else:
            # Design §1.4 "Epoch reset detection": ANY lower counter starts a
            # new epoch. Requiring all three could turn a partial reset into a
            # bogus giant residual; this direction loses at most one boundary.
            is_reset = (
                pad_now["elapsed_s"] < self.last_pad["elapsed_s"]
                or pad_now["dist_km"] < self.last_pad["dist_km"]
                or pad_now["steps"] < self.last_pad["steps"]
            )
            if is_reset:
                if self.open_bout is not None:
                    # Inference from design §1.4, not an explicit close trigger:
                    # leaving a bout open across a reset would bleed the next
                    # negative delta into it, violating the no-negative rule. End
                    # it against the OLD final frame before adopting the reset.
                    self._finish_bout(end=self.last_seen_wall)
                self.epoch += 1
                self.attributed = {
                    "elapsed_s": 0,
                    "dist_km": 0.0,
                    "steps": 0,
                }
                self.last_pad = dict(pad_now)
                self.last_seen_wall = wall
                if running:
                    self.open_bout = self._new_bout(wall)
                force_write = True
            elif wall < self.last_seen_wall - MAX_POLL_INTERVAL_S:
                # Wall time is persisted because bouts must be placeable in real
                # time, but design §1.4 never permits one to run backwards. A
                # step larger than the bridge's slowest poll is a discontinuity,
                # not elapsed time: close on the last trustworthy timestamp and
                # re-baseline without fabricating a negative uncertainty window.
                if self.open_bout is not None:
                    self._finish_bout(end=self.last_seen_wall)
                self.last_pad = dict(pad_now)
                self.last_seen_wall = wall
                if running:
                    self.open_bout = self._new_bout(wall)
                force_write = True
            elif wall - self.last_seen_wall > LINK_GAP_S:
                # Design §1.4: no observed bout spans a lost link. The gap's
                # exact counter delta is attributed separately, with its wall
                # times marking uncertainty rather than a claimed walking time.
                if self.open_bout is not None:
                    self._finish_bout(end=self.last_seen_wall)
                delta = {key: pad_now[key] - self.last_pad[key]
                         for key in pad_now}
                previous_wall = self.last_seen_wall
                self.last_pad = dict(pad_now)
                self.last_seen_wall = wall
                if (delta["elapsed_s"] >= MIN_RESIDUAL_ELAPSED_S
                        or delta["dist_km"] >= MIN_RESIDUAL_DIST_KM):
                    self._add_attributed(delta)
                    self._write_bout({
                        "e": self.epoch,
                        "start": previous_wall,
                        "end": wall,
                        "observed": False,
                        "dur_s": delta["elapsed_s"],
                        "dist_km": round(delta["dist_km"], 2),
                        "steps": delta["steps"],
                    }, state_wall=wall)
                # Design §1.2 defines the residual as the exact home for small
                # amounts we could not place. Sub-threshold deltas therefore do
                # not advance attribution: doing so without a bout would erase
                # them from both sides of the central self-check.
                if running:
                    self.open_bout = self._new_bout(wall)
                force_write = True
            else:
                # Design §1.4 continuous attribution assigns the delta by the
                # CURRENT frame's belt state. That makes stop→start form two
                # observed bouts without inventing a residual between them.
                delta = {key: pad_now[key] - self.last_pad[key]
                         for key in pad_now}
                positive_delta = any(value > 0 for value in delta.values())
                if self.open_bout is None and positive_delta:
                    # Design §1.2 permits attribution only when the same delta
                    # has a bout. A counter jump with no open observed bout has
                    # unknown placement even inside LINK_GAP_S, so apply §1.4's
                    # residual threshold instead of silently calling it observed.
                    previous_wall = self.last_seen_wall
                    self.last_pad = dict(pad_now)
                    self.last_seen_wall = wall
                    if (delta["elapsed_s"] >= MIN_RESIDUAL_ELAPSED_S
                            or delta["dist_km"] >= MIN_RESIDUAL_DIST_KM):
                        self._add_attributed(delta)
                        self._write_bout({
                            "e": self.epoch,
                            "start": previous_wall,
                            "end": wall,
                            "observed": False,
                            "dur_s": delta["elapsed_s"],
                            "dist_km": round(delta["dist_km"], 2),
                            "steps": delta["steps"],
                        }, state_wall=wall)
                    if running:
                        self.open_bout = self._new_bout(wall)
                        force_write = True
                else:
                    if running and self.open_bout is None:
                        self.open_bout = self._new_bout(wall)
                        force_write = True
                    if self.open_bout is not None:
                        self._accumulate_bout_counters(delta, wall)
                        self._add_attributed(delta)
                    self.last_pad = dict(pad_now)
                    self.last_seen_wall = wall
                    if not running and self.open_bout is not None:
                        self._finish_bout(end=wall)
                        force_write = True

        # Design §1.3 defines a bout as RUNNING frames, so only those frames
        # contribute speed samples. Counter deltas are separate by design §1.4:
        # the non-running close frame may still carry real pad-owned walking
        # totals, but fact 2 makes its near-zero speed outside the bout and would
        # otherwise corrupt almost every normal walk's v_avg.
        if running and self.open_bout is not None:
            self._accumulate_bout_speed(status)

        # Design §2.2: idle frames contain no sample signal, but the transition
        # OUT of starting/running and the next TAIL_S seconds are recorded so the
        # stored curve captures both ends of the ramp.
        active = status.belt in ("starting", "running")
        if active:
            self._tail_deadline = None
            recordable = True
        else:
            if self._was_active:
                self._tail_deadline = wall + TAIL_S
            recordable = (
                self._tail_deadline is not None
                and wall <= self._tail_deadline
            )
            if not recordable:
                self._tail_deadline = None
        self._was_active = active
        if recordable:
            self._write_sample(
                wall, status, dist_km, commanded_speed, goal_speed
            )

        # State is deliberately last: samples preserve the evidence needed to
        # rebuild attribution, and no filesystem work may precede motion service.
        self._write_state(wall, force=force_write)

    def _new_bout(self, wall):
        return {
            "e": self.epoch,
            "start": wall,
            "last_t": wall,
            "dur_s": 0,
            "dist_km": 0.0,
            "steps": 0,
            "v_sum": 0.0,
            "v_max": 0.0,
            "n": 0,
        }

    def _accumulate_bout_counters(self, delta, wall):
        bout = self.open_bout
        bout["last_t"] = wall
        bout["dur_s"] += delta["elapsed_s"]
        bout["dist_km"] = round(bout["dist_km"] + delta["dist_km"], 2)
        bout["steps"] += delta["steps"]

    def _accumulate_bout_speed(self, status):
        bout = self.open_bout
        speed = round(status.speed_kmh, 1)
        bout["v_sum"] += speed
        bout["v_max"] = max(bout["v_max"], speed)
        bout["n"] += 1

    def _add_attributed(self, delta):
        self.attributed = {
            "elapsed_s": self.attributed["elapsed_s"] + delta["elapsed_s"],
            "dist_km": round(
                self.attributed["dist_km"] + delta["dist_km"], 2
            ),
            "steps": self.attributed["steps"] + delta["steps"],
        }

    def _finish_bout(self, end):
        bout = self.open_bout
        self.open_bout = None
        # Design §1.4 places bouts on wall time, so even a clock correction or
        # stale recovered state may never produce an end before its start.
        end = max(end, bout["start"])
        has_totals = (
            bout["dur_s"] > 0
            or bout["dist_km"] > 0
            or bout["steps"] > 0
        )
        # A single running snapshot with no pad-owned counter delta is still an
        # empty shell, even though design §1.3 now correctly counts its speed as
        # a running sample. Emptiness is a totals property, not an n proxy.
        if not has_totals:
            return
        if bout["n"] == 0:
            # Design §2.3 permits v_avg/v_max/n only when samples exist. An
            # open bout with counters but no samples can arise from recovered
            # older state; preserve its exact totals as unobserved instead of
            # claiming a speed that was never measured or breaking attribution.
            row = {
                "e": bout["e"],
                "start": bout["start"],
                "end": end,
                "observed": False,
                "dur_s": bout["dur_s"],
                "dist_km": bout["dist_km"],
                "steps": bout["steps"],
            }
        else:
            row = {
                "e": bout["e"],
                "start": bout["start"],
                "end": end,
                "observed": True,
                "dur_s": bout["dur_s"],
                "dist_km": bout["dist_km"],
                "steps": bout["steps"],
                "v_avg": round(bout["v_sum"] / bout["n"], 1),
                "v_max": round(bout["v_max"], 1),
                "n": bout["n"],
            }
        self._write_bout(row, state_wall=end)

    def _write_bout(self, row, state_wall):
        # Append + flush is the bounded durability point from design §3.1.
        # fsync per 1 Hz row would add wear without preserving unique evidence.
        with os.fdopen(
            _open_leaf(self._bouts_path, _APPEND_FLAGS), "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()
        # This forced state rewrite starts immediately after every bout append,
        # so restart recovery sees the cleared open_bout and cannot normally
        # append it twice. It narrows the two-file tear window to the state
        # rewrite's os.replace; it cannot make the pair atomic. Design §2.2 keeps
        # raw cumulative samples precisely so attribution remains rebuildable if
        # a crash ever lands in that remaining window.
        self._write_state(state_wall, force=True)

    def _write_sample(self, wall, status, dist_km,
                      commanded_speed, goal_speed):
        row = {
            "t": wall,
            "e": self.epoch,
            "b": status.belt,
            "m": status.mode,
            "v": round(status.speed_kmh, 1),
            "c": (
                round(commanded_speed, 1)
                if commanded_speed is not None else None
            ),
            "g": round(goal_speed, 1) if goal_speed is not None else None,
            "d": dist_km,
            "s": status.steps,
            "pe": status.elapsed_s,
        }
        date = datetime.fromtimestamp(
            wall, tz=timezone.utc
        ).strftime("%Y-%m-%d")
        path = self._samples_dir / f"{date}.jsonl"
        with os.fdopen(
            _open_leaf(path, _APPEND_FLAGS), "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()

    def _write_link(self, conn_s, answered_s, frames, why):
        # Around 90 bytes, and bounded by how often a connection ends: with the
        # mute that is roughly one per ~48 s cycle while the pad is awake, and
        # nothing at all while it sleeps. Same append-and-flush durability as
        # every other row here (design §3.1).
        row = {
            "t": self._now(),
            "conn_s": conn_s,
            "answered_s": answered_s,
            "frames": frames,
            "why": why,
        }
        with os.fdopen(
            _open_leaf(self._links_path, _APPEND_FLAGS), "a", encoding="utf-8"
        ) as fh:
            fh.write(json.dumps(row) + "\n")
            fh.flush()

    def _write_state(self, wall, force):
        if self.last_pad is None:
            # Nothing has established a counter baseline, so there is no state
            # that a later bridge lifetime could safely resume.
            return
        if (not force
                and self._last_state_write is not None
                and wall - self._last_state_write < STATE_WRITE_INTERVAL_S):
            return
        data = {
            "epoch": self.epoch,
            "last_pad": self.last_pad,
            "attributed": self.attributed,
            "open_bout": self.open_bout,
            "last_seen_wall": self.last_seen_wall,
        }
        # mkstemp creates an unpredictable name with O_EXCL and mode 0600 and
        # hands back the open descriptor, so there is no window in which the
        # path exists unopened for a symlink to occupy. The fixed
        # "state.json.tmp" this replaced was predictable and followed.
        fd, tmp = tempfile.mkstemp(
            dir=self._root, prefix="state.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
                fh.flush()
            # rename replaces a symlink at the destination rather than writing
            # through it, so the state path heals itself on the next write.
            os.replace(tmp, self._state_path)
        except BaseException:
            # A failed rewrite must not strand a temp file. This runs up to
            # every STATE_WRITE_INTERVAL_S for the life of the bridge.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._last_state_write = wall

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._disabled:
            return
        try:
            if self.open_bout is not None and self.last_seen_wall is not None:
                # Design §1.4: shutdown closes at the last observed frame,
                # never at an arbitrary later wall-clock time.
                self._finish_bout(end=self.last_seen_wall)
            self._write_state(
                self.last_seen_wall
                if self.last_seen_wall is not None else 0.0,
                force=True,
            )
        except Exception as exc:
            self._fail(exc)

    def close_open_bout(self):
        if self._disabled:
            return
        try:
            if self.open_bout is not None and self.last_seen_wall is not None:
                # Design §1.4: link loss ends the bout at the last frame the
                # store actually observed, never at a later wall-clock "now".
                self._finish_bout(end=self.last_seen_wall)
            self._write_state(
                self.last_seen_wall
                if self.last_seen_wall is not None else 0.0,
                force=True,
            )
        except Exception as exc:
            # Design §3.1: stats failure degrades once and never reaches the
            # safety-critical poll loop or any belt-control path.
            self._fail(exc)


def resolve_store_path(replay, store_arg):
    """Return the CLI store path, or None when replay should stay synthetic.

    A replay fixture with no explicit --store must never write synthetic bouts
    into a person's real health record (design wiring contract §3).
    """
    # An empty argv value is semantically no override. Treating it as the real
    # default during replay would violate the synthetic-data boundary above.
    if store_arg is not None and not store_arg.strip():
        store_arg = None
    if replay is not None and store_arg is None:
        return None
    return store_arg or DEFAULT_STORE_DIR
