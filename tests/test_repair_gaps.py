"""The repair must reconstruct exactly what the fixed store writes.

bin/walkingpad-repair-gaps is a second reader of the link-gap rule, and a second
implementation of a rule is a place for the two to drift apart. So this suite
never asserts against hand-written rows: it drives the real Store through a mute
gap, deletes the unobserved bouts that the old floors would have dropped, and
requires the repair to put the file back exactly as the writer left it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from walkingpad.frames import Status
from walkingpad.store import Store


REPO_ROOT = Path(__file__).resolve().parents[1]
REPAIR = "bin/walkingpad-repair-gaps"
STEPS_PER_KM = 1300


class FakeClock:
    def __init__(self, wall=1_700_000_000.0):
        self.wall = wall

    def __call__(self):
        return self.wall

    def advance(self, seconds):
        self.wall += seconds
        return self.wall


class Pad:
    """The three cumulative counters, quantised only where a frame reports them.

    Accumulating at full precision matters: one second at 2.5 km/h is 0.7 m and
    0.9 of a step, so rounding each tick before adding it floors every increment
    to zero and the walk generates no counters at all.
    """

    def __init__(self):
        self._elapsed_s = 0.0
        self._dist_km = 0.0
        self._steps = 0.0

    def walk(self, seconds, speed_kmh=2.5):
        distance = speed_kmh / 3600.0 * seconds
        self._elapsed_s += seconds
        self._dist_km += distance
        self._steps += distance * STEPS_PER_KM

    def status(self, belt, speed_kmh=2.5):
        return Status(
            belt=belt,
            speed_kmh=speed_kmh,
            set_speed_kmh=0.0,
            mode="manual",
            elapsed_s=int(self._elapsed_s),
            distance_km=round(self._dist_km, 2),
            steps=int(self._steps),
        )


def read_jsonl(path):
    if not Path(path).exists():
        return []
    return [json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()]


def write_jsonl(path, rows):
    Path(path).write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def run_repair(store_dir, *arguments):
    result = subprocess.run(
        [sys.executable, REPAIR, "--store", str(store_dir), *arguments],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={**os.environ, "TZ": "UTC"}, timeout=60,
    )
    return json.loads(result.stdout), result.returncode


def walk_through_one_mute(root, mute_s=20.3):
    """One walk split by a mute, the shape open question 9 produces on hardware.

    The pad answers for ~29 s, goes quiet while the belt keeps running, and the
    bridge reconnects about 20 s later having missed every frame in between.
    """
    clock = FakeClock()
    store = Store(root, now=clock)
    pad = Pad()
    for _ in range(29):
        pad.walk(1)
        store.record(pad.status("running"), None, None)
        clock.advance(1)

    # The mute: the belt runs on, and not one frame reaches the store.
    pad.walk(mute_s)
    clock.advance(mute_s)

    # The bridge polls the instant it reconnects, so the first frame back
    # reports exactly the mute's own delta and nothing else.
    for _ in range(29):
        store.record(pad.status("running"), None, None)
        clock.advance(1)
        pad.walk(1)
    store.record(pad.status("stopped", 0.0), None, None)
    store.close()
    return store


def test_the_fixed_store_writes_the_mute_gap_as_an_unobserved_bout(tmp_path):
    walk_through_one_mute(tmp_path)

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    unobserved = [bout for bout in bouts if bout["observed"] is False]
    assert len(unobserved) == 1
    # The pad's own counters say the belt ran for the whole mute; nothing here
    # is inferred from the wall clock.
    assert unobserved[0]["dur_s"] == 20
    assert unobserved[0]["steps"] > 0


def test_repair_restores_exactly_the_rows_the_old_floors_dropped(tmp_path):
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")
    # What the store used to leave behind: the observed runs, and no trace at
    # all of the walking between them.
    write_jsonl(tmp_path / "bouts.jsonl",
                [bout for bout in written if bout["observed"]])

    payload, code = run_repair(tmp_path, "--apply")

    assert code == 0
    assert payload["applied"] is True
    assert payload["recovered_bouts"] == 1
    assert read_jsonl(tmp_path / "bouts.jsonl") == written


def test_repair_is_idempotent(tmp_path):
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")

    payload, code = run_repair(tmp_path, "--apply")

    assert code == 0
    assert payload["recovered_bouts"] == 0
    assert payload["applied"] is False
    assert read_jsonl(tmp_path / "bouts.jsonl") == written


def test_repair_backs_the_old_file_up_before_rewriting_it(tmp_path):
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")
    observed_only = [bout for bout in written if bout["observed"]]
    write_jsonl(tmp_path / "bouts.jsonl", observed_only)

    payload, _ = run_repair(tmp_path, "--apply")

    assert read_jsonl(payload["backup"]) == observed_only
    assert os.stat(payload["backup"]).st_mode & 0o777 == 0o600


def test_repair_writes_nothing_without_apply(tmp_path):
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")
    observed_only = [bout for bout in written if bout["observed"]]
    write_jsonl(tmp_path / "bouts.jsonl", observed_only)

    payload, code = run_repair(tmp_path)

    assert code == 0
    assert payload["applied"] is False
    assert payload["recovered_bouts"] == 1
    assert read_jsonl(tmp_path / "bouts.jsonl") == observed_only


def test_repair_refuses_while_a_walk_is_open(tmp_path):
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")
    observed_only = [bout for bout in written if bout["observed"]]
    write_jsonl(tmp_path / "bouts.jsonl", observed_only)
    # An open bout means the bridge is mid-walk: it will append when the belt
    # stops, and it is still attributing counters this tool wants to place.
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    state["open_bout"] = {"e": 0, "start": 1_700_000_000.0}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    payload, code = run_repair(tmp_path, "--apply")

    assert code == 1
    assert "a walk is open" in payload["error"]
    assert read_jsonl(tmp_path / "bouts.jsonl") == observed_only


def test_repair_leaves_a_store_with_no_holes_alone(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    pad = Pad()
    for _ in range(30):
        pad.walk(1)
        store.record(pad.status("running"), None, None)
        clock.advance(1)
    store.record(pad.status("stopped", 0.0), None, None)
    store.close()
    written = read_jsonl(tmp_path / "bouts.jsonl")

    payload, code = run_repair(tmp_path, "--apply")

    assert code == 0
    assert payload["recovered_bouts"] == 0
    assert read_jsonl(tmp_path / "bouts.jsonl") == written


def test_repair_skips_a_hole_an_existing_bout_already_covers(tmp_path):
    """The case that would have double-counted on the first real store.

    state.json is rewritten at most every STATE_WRITE_INTERVAL_S, so a bridge
    restart recovers a last_seen_wall up to 10 s behind the last sample. The
    bout the store then writes for the hole starts EARLIER than the sample
    stream says, and matching endpoints would not find it. Measured on
    2026-08-28: a 51-step bout covering a hole this scan reads as 39 steps.
    """
    walk_through_one_mute(tmp_path)
    written = read_jsonl(tmp_path / "bouts.jsonl")
    covering = []
    for bout in written:
        if bout["observed"]:
            covering.append(bout)
            continue
        # Same hole, but recorded from a stale wall clock and so wider.
        covering.append({**bout,
                         "start": bout["start"] - 9.0,
                         "dur_s": bout["dur_s"] + 9,
                         "steps": bout["steps"] + 12})
    write_jsonl(tmp_path / "bouts.jsonl", covering)

    payload, code = run_repair(tmp_path, "--apply")

    assert code == 0
    assert payload["recovered_bouts"] == 0
    assert payload["holes_already_covered"] == 1
    assert read_jsonl(tmp_path / "bouts.jsonl") == covering
