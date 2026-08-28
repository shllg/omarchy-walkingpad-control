import json
import os
import signal
import stat
import time

from walkingpad.bridge import Bridge, LINK_DEAD_AFTER_S
from walkingpad.frames import Status
from walkingpad.store import (
    DEFAULT_STORE_DIR,
    LINK_GAP_S,
    MAX_STATE_BYTES,
    Store,
    TAIL_S,
    resolve_store_path,
)


class FakeClock:
    def __init__(self, wall=1_700_000_000.0):
        self.wall = wall

    def __call__(self):
        return self.wall

    def advance(self, seconds):
        self.wall += seconds
        return self.wall


def make_status(belt="idle", speed_kmh=0.0, mode="manual",
                elapsed_s=0, distance_km=0.0, steps=0,
                set_speed_kmh=0.0):
    return Status(
        belt=belt,
        speed_kmh=speed_kmh,
        set_speed_kmh=set_speed_kmh,
        mode=mode,
        elapsed_s=elapsed_s,
        distance_km=distance_km,
        steps=steps,
    )


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def read_samples(root):
    rows = []
    for path in sorted((root / "samples").glob("*.jsonl")):
        rows.extend(read_jsonl(path))
    return rows


def numeric_values(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from numeric_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from numeric_values(item)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        yield value


def test_counters_continue_across_a_stop_as_two_bouts_in_one_epoch(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)

    statuses = [
        make_status("running", 2.0, elapsed_s=100,
                    distance_km=1.00, steps=100),
        make_status("running", 2.1, elapsed_s=101,
                    distance_km=1.01, steps=102),
        make_status("stopped", elapsed_s=101,
                    distance_km=1.01, steps=102),
        make_status("idle", elapsed_s=101,
                    distance_km=1.01, steps=102),
        make_status("running", 2.0, elapsed_s=102,
                    distance_km=1.02, steps=104),
        make_status("running", 2.1, elapsed_s=103,
                    distance_km=1.03, steps=106),
        make_status("stopped", elapsed_s=103,
                    distance_km=1.03, steps=106),
    ]

    for status in statuses:
        store.record(status, None, None)
        assert store.epoch == 0
        clock.advance(1)

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    assert len(bouts) == 2
    assert all(bout["observed"] is True for bout in bouts)
    assert list(bouts[0]) == [
        "e", "start", "end", "observed", "dur_s", "dist_km", "steps",
        "v_avg", "v_max", "n",
    ]
    # F1's design §1.2 invariant leaves the first positive delta after idle in
    # the residual because no bout was open when it arrived; the following frame
    # belongs to the newly opened second bout and is attributed normally.
    assert store.attributed == {
        "elapsed_s": 102, "dist_km": 1.02, "steps": 104,
    }
    assert store.last_pad == {
        "elapsed_s": 103, "dist_km": 1.03, "steps": 106,
    }


def test_closing_frame_adds_counter_deltas_but_not_a_speed_sample(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(
        make_status("running", 2.0, elapsed_s=100,
                    distance_km=1.00, steps=200),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("running", 2.1, elapsed_s=101,
                    distance_km=1.01, steps=202),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("stopped", 0.0, elapsed_s=103,
                    distance_km=1.03, steps=206),
        None,
        None,
    )

    bout = read_jsonl(tmp_path / "bouts.jsonl")[0]
    assert bout["n"] == 2
    assert bout["v_avg"] == round((2.0 + 2.1) / 2, 1)
    assert bout["v_max"] == 2.1
    assert (bout["dur_s"], bout["dist_km"], bout["steps"]) == (3, 0.03, 6)


def test_counter_reset_starts_one_new_epoch_without_negative_values(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(
        make_status("running", 2.0, elapsed_s=50,
                    distance_km=0.50, steps=80),
        None,
        None,
    )
    clock.advance(1)

    store.record(make_status("running"), None, None)

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    assert store.epoch == 1
    assert store.attributed == {"elapsed_s": 0, "dist_km": 0.0, "steps": 0}
    assert all(value >= 0 for value in numeric_values({
        "attributed": store.attributed,
        "last_pad": store.last_pad,
        "bouts": bouts,
    }))


def test_reconnect_writes_one_exact_unobserved_residual(tmp_path):
    clock = FakeClock()
    first_wall = clock.wall
    store = Store(tmp_path, now=clock)
    store.record(
        make_status(elapsed_s=100, distance_km=1.00, steps=200),
        None,
        None,
    )
    clock.advance(LINK_GAP_S + 1)

    store.record(
        make_status(elapsed_s=140, distance_km=1.08, steps=260),
        None,
        None,
    )

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    assert bouts == [{
        "e": 0,
        "start": first_wall,
        "end": clock.wall,
        "observed": False,
        "dur_s": 40,
        "dist_km": 0.08,
        "steps": 60,
    }]
    assert list(bouts[0]) == [
        "e", "start", "end", "observed", "dur_s", "dist_km", "steps",
    ]


def test_continuous_delta_without_an_open_bout_stays_in_residual(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(make_status(), None, None)
    clock.advance(LINK_GAP_S - 1)

    store.record(
        make_status(elapsed_s=10, distance_km=0.04, steps=20),
        None,
        None,
    )

    assert read_jsonl(tmp_path / "bouts.jsonl") == []
    assert store.attributed == {
        "elapsed_s": 0, "dist_km": 0.0, "steps": 0,
    }
    assert store.last_pad == {
        "elapsed_s": 10, "dist_km": 0.04, "steps": 20,
    }


def test_material_continuous_delta_without_an_open_bout_is_unobserved(tmp_path):
    clock = FakeClock()
    first_wall = clock.wall
    store = Store(tmp_path, now=clock)
    store.record(make_status(), None, None)
    clock.advance(LINK_GAP_S - 1)

    store.record(
        make_status(elapsed_s=30, distance_km=0.05, steps=60),
        None,
        None,
    )

    assert read_jsonl(tmp_path / "bouts.jsonl") == [{
        "e": 0,
        "start": first_wall,
        "end": clock.wall,
        "observed": False,
        "dur_s": 30,
        "dist_km": 0.05,
        "steps": 60,
    }]
    assert store.attributed == store.last_pad


def test_sub_threshold_link_gap_is_not_written_or_attributed(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(
        make_status(elapsed_s=100, distance_km=1.00, steps=200),
        None,
        None,
    )
    clock.advance(LINK_GAP_S + 1)

    store.record(
        make_status(elapsed_s=110, distance_km=1.04, steps=210),
        None,
        None,
    )

    assert read_jsonl(tmp_path / "bouts.jsonl") == []
    assert store.attributed == {
        "elapsed_s": 100, "dist_km": 1.0, "steps": 200,
    }
    assert store.last_pad == {
        "elapsed_s": 110, "dist_km": 1.04, "steps": 210,
    }


def test_bout_totals_equal_attributed_across_mixed_sequence(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)

    # Start with a non-zero first-frame baseline, then reset before attribution
    # so the current epoch can exercise every branch of design §1.4 exactly.
    store.record(
        make_status(elapsed_s=100, distance_km=1.0, steps=200),
        None,
        None,
    )
    clock.advance(1)
    store.record(make_status(), None, None)

    clock.advance(1)
    store.record(make_status("running", 2.0), None, None)
    clock.advance(1)
    store.record(
        make_status("running", 2.0, elapsed_s=10,
                    distance_km=0.04, steps=20),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("idle", elapsed_s=10, distance_km=0.04, steps=20),
        None,
        None,
    )

    # One sub-threshold link gap and one continuous no-bout delta both remain
    # visible in residual; neither is allowed to advance attribution by itself.
    clock.advance(LINK_GAP_S + 1)
    store.record(
        make_status("idle", elapsed_s=20, distance_km=0.08, steps=30),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("idle", elapsed_s=30, distance_km=0.12, steps=40),
        None,
        None,
    )

    clock.advance(LINK_GAP_S + 1)
    store.record(
        make_status("idle", elapsed_s=70, distance_km=0.20, steps=100),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("running", 2.0, elapsed_s=70,
                    distance_km=0.20, steps=100),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("running", 2.1, elapsed_s=75,
                    distance_km=0.22, steps=110),
        None,
        None,
    )
    clock.advance(1)
    store.record(
        make_status("idle", elapsed_s=75, distance_km=0.22, steps=110),
        None,
        None,
    )

    bouts = [
        bout for bout in read_jsonl(tmp_path / "bouts.jsonl")
        if bout["e"] == store.epoch
    ]
    bout_totals = {
        "elapsed_s": sum(bout["dur_s"] for bout in bouts),
        "dist_km": round(sum(bout["dist_km"] for bout in bouts), 2),
        "steps": sum(bout["steps"] for bout in bouts),
    }
    assert store.epoch == 1
    assert {bout["observed"] for bout in bouts} == {True, False}
    assert bout_totals == store.attributed
    assert store.attributed == {
        "elapsed_s": 55, "dist_km": 0.14, "steps": 90,
    }


def test_first_frame_adopts_high_counters_without_a_residual(tmp_path):
    store = Store(tmp_path, now=FakeClock())
    store.record(
        make_status(elapsed_s=50_000, distance_km=123.45, steps=80_000),
        None,
        None,
    )

    expected = {"elapsed_s": 50_000, "dist_km": 123.45, "steps": 80_000}
    assert store.attributed == expected
    assert store.last_pad == expected
    assert read_jsonl(tmp_path / "bouts.jsonl") == []


def test_restart_closes_an_open_bout_at_the_last_seen_frame(tmp_path):
    persisted_last_seen = 1_700_000_010.0
    state = {
        "epoch": 3,
        "last_pad": {"elapsed_s": 20, "dist_km": 0.04, "steps": 40},
        "attributed": {"elapsed_s": 20, "dist_km": 0.04, "steps": 40},
        "open_bout": {
            "e": 3,
            "start": 1_700_000_000.0,
            "last_t": persisted_last_seen,
            "dur_s": 20,
            "dist_km": 0.04,
            "steps": 40,
            "v_sum": 5.0,
            "v_max": 3.0,
            "n": 2,
        },
        "last_seen_wall": persisted_last_seen,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    store = Store(tmp_path, now=FakeClock(1_800_000_000.0))

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    assert len(bouts) == 1
    assert bouts[0]["observed"] is True
    assert bouts[0]["end"] == persisted_last_seen
    assert store.open_bout is None
    healed_state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert healed_state["open_bout"] is None
    assert list(healed_state) == [
        "epoch", "last_pad", "attributed", "open_bout", "last_seen_wall",
    ]


def test_link_loss_closes_a_bout_at_the_last_frame_not_now(tmp_path):
    class SilentTransport:
        def __init__(self):
            self.connected = True
            self.disconnects = 0

        def disconnect(self):
            self.connected = False
            self.disconnects += 1

    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(make_status("running", 2.0), None, None)
    clock.advance(1)
    store.record(
        make_status("running", 2.1, elapsed_s=1,
                    distance_km=0.01, steps=2),
        None,
        None,
    )
    last_frame_wall = clock.wall
    clock.advance(300)

    transport = SilentTransport()
    bridge = Bridge(transport=transport, store=store)
    emitted = []
    bridge.emit = emitted.append
    bridge._connected_at = time.monotonic() - LINK_DEAD_AFTER_S - 1
    bridge._service_link()

    bout = read_jsonl(tmp_path / "bouts.jsonl")[0]
    assert emitted[0]["state"] == "lost"
    assert transport.disconnects == 1
    assert bout["end"] == last_frame_wall
    assert bout["end"] != clock.wall
    assert store.open_bout is None
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["open_bout"] is None


def test_bout_append_immediately_clears_recovery_state(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(make_status("running", 2.0), None, None)
    clock.advance(1)
    store.record(
        make_status("running", 2.0, elapsed_s=1,
                    distance_km=0.01, steps=2),
        None,
        None,
    )

    append_bout = store._write_bout

    def crash_after_append(row, state_wall):
        append_bout(row, state_wall)
        raise RuntimeError("simulated crash after bout append")

    store._write_bout = crash_after_append
    clock.advance(1)
    store.record(
        make_status("idle", elapsed_s=1, distance_km=0.01, steps=2),
        None,
        None,
    )

    persisted = json.loads(
        (tmp_path / "state.json").read_text(encoding="utf-8")
    )
    assert persisted["open_bout"] is None
    Store(tmp_path, now=clock)
    assert len(read_jsonl(tmp_path / "bouts.jsonl")) == 1


def test_large_backward_wall_step_closes_and_rebaselines_bouts(tmp_path):
    clock = FakeClock(300.0)
    store = Store(tmp_path, now=clock)
    store.record(make_status("running", 2.0), None, None)
    clock.advance(1)
    store.record(
        make_status("running", 2.0, elapsed_s=1,
                    distance_km=0.01, steps=2),
        None,
        None,
    )

    clock.wall = 290.0
    store.record(
        make_status("running", 2.0, elapsed_s=1,
                    distance_km=0.01, steps=2),
        None,
        None,
    )
    assert store.open_bout["start"] == 290.0
    assert store.last_seen_wall == 290.0

    clock.advance(1)
    store.record(
        make_status("running", 2.0, elapsed_s=2,
                    distance_km=0.02, steps=4),
        None,
        None,
    )
    store.close()

    bouts = read_jsonl(tmp_path / "bouts.jsonl")
    assert [(bout["start"], bout["end"]) for bout in bouts] == [
        (300.0, 301.0),
        (290.0, 291.0),
    ]
    assert all(bout["end"] >= bout["start"] for bout in bouts)


def test_recovered_bout_clamps_end_to_its_start(tmp_path):
    state = {
        "epoch": 1,
        "last_pad": {"elapsed_s": 1, "dist_km": 0.01, "steps": 2},
        "attributed": {"elapsed_s": 1, "dist_km": 0.01, "steps": 2},
        "open_bout": {
            "e": 1,
            "start": 300.0,
            "last_t": 290.0,
            "dur_s": 1,
            "dist_km": 0.01,
            "steps": 2,
            "v_sum": 2.0,
            "v_max": 2.0,
            "n": 1,
        },
        "last_seen_wall": 290.0,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    Store(tmp_path, now=FakeClock(400.0))

    assert read_jsonl(tmp_path / "bouts.jsonl")[0]["end"] == 300.0


def test_first_running_frame_closed_immediately_writes_no_bout(tmp_path):
    store = Store(tmp_path, now=FakeClock())
    store.record(make_status("running", 2.0), None, None)

    store.close()

    assert read_jsonl(tmp_path / "bouts.jsonl") == []


def test_zero_sample_bout_with_totals_has_no_claimed_speed(tmp_path):
    state = {
        "epoch": 1,
        "last_pad": {"elapsed_s": 10, "dist_km": 0.04, "steps": 20},
        "attributed": {"elapsed_s": 10, "dist_km": 0.04, "steps": 20},
        "open_bout": {
            "e": 1,
            "start": 300.0,
            "last_t": 310.0,
            "dur_s": 10,
            "dist_km": 0.04,
            "steps": 20,
            "v_sum": 0.0,
            "v_max": 0.0,
            "n": 0,
        },
        "last_seen_wall": 310.0,
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")

    Store(tmp_path, now=FakeClock(400.0))

    bout = read_jsonl(tmp_path / "bouts.jsonl")[0]
    assert bout["observed"] is False
    assert not {"v_avg", "v_max", "n"} & bout.keys()


def test_starting_countdown_records_samples_without_opening_a_bout(tmp_path):
    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    store.record(
        make_status("starting", speed_kmh=0.04, elapsed_s=10,
                    distance_km=0.126, steps=20),
        2.54,
        3.06,
    )
    assert store.open_bout is None
    clock.advance(1)
    store.record(
        make_status("starting", speed_kmh=0.16, elapsed_s=10,
                    distance_km=0.126, steps=20),
        2.54,
        3.06,
    )

    rows = read_samples(tmp_path)
    assert len(rows) == 2
    assert [row["b"] for row in rows] == ["starting", "starting"]
    assert store.open_bout is None
    assert list(rows[0]) == ["t", "e", "b", "m", "v", "c", "g", "d", "s", "pe"]
    assert (rows[0]["v"], rows[0]["c"], rows[0]["g"], rows[0]["d"]) == (
        0.0, 2.5, 3.1, 0.13
    )


def test_write_failure_emits_once_and_disables_future_writes(tmp_path):
    clock = FakeClock()
    emitted = []
    store = Store(tmp_path, emit=emitted.append, now=clock)
    store.record(make_status("running"), None, None)
    samples_dir = tmp_path / "samples"
    os.chmod(samples_dir, 0o500)

    try:
        # Cross UTC dates so the next append must create a file in the now
        # unwritable samples directory rather than reopening the existing file.
        clock.advance(24 * 60 * 60)
        store.record(make_status("running"), None, None)
        store.record(make_status("running"), None, None)
    finally:
        # Removing a non-empty directory needs its write bit on POSIX, so restore
        # it for tmp_path cleanup after the failure has been observed.
        os.chmod(samples_dir, 0o700)

    assert len(emitted) == 1
    assert emitted[0]["t"] == "error"
    assert emitted[0]["code"] == "store_write_failed"


def test_tail_records_through_deadline_and_not_after_it(tmp_path):
    clock = FakeClock()
    first_wall = clock.wall
    store = Store(tmp_path, now=clock)
    store.record(make_status("running", 2.0), None, None)
    clock.advance(1)
    store.record(make_status("stopped"), None, None)
    clock.advance(2)
    store.record(make_status("idle"), None, None)
    clock.advance(TAIL_S - 2)
    store.record(make_status("idle"), None, None)
    clock.advance(0.1)
    store.record(make_status("idle"), None, None)

    assert [row["t"] for row in read_samples(tmp_path)] == [
        first_wall,
        first_wall + 1,
        first_wall + 3,
        first_wall + 1 + TAIL_S,
    ]


def test_idle_frame_advances_state_without_writing_a_sample(tmp_path):
    clock = FakeClock()
    counters = {"elapsed_s": 20, "distance_km": 0.04, "steps": 40}
    store = Store(tmp_path, now=clock)
    store.record(make_status("running", 2.0, **counters), None, None)
    clock.advance(1)
    store.record(make_status("stopped", **counters), None, None)
    sample_count = len(read_samples(tmp_path))
    clock.advance(TAIL_S + 1)

    store.record(make_status("idle", **counters), None, None)

    expected = {"elapsed_s": 20, "dist_km": 0.04, "steps": 40}
    assert store.last_pad == expected
    assert store.attributed == expected
    assert store.last_seen_wall == clock.wall
    assert len(read_samples(tmp_path)) == sample_count


def test_replay_without_an_explicit_store_cannot_use_the_real_default():
    assert resolve_store_path("synthetic.jsonl", None) is None
    assert resolve_store_path("synthetic.jsonl", "") is None
    assert resolve_store_path("synthetic.jsonl", "  \t") is None
    assert resolve_store_path("synthetic.jsonl", "/tmp/synthetic-store") == (
        "/tmp/synthetic-store"
    )
    assert resolve_store_path(None, None) == DEFAULT_STORE_DIR


def test_store_directories_are_private(tmp_path):
    root = tmp_path / "store"
    Store(root, now=FakeClock())

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "samples").stat().st_mode) == 0o700


def test_store_refuses_symlink_root_and_samples_without_chmodding_targets(tmp_path):
    redirected_root = tmp_path / "redirected-root"
    redirected_root.mkdir(mode=0o755)
    root_link = tmp_path / "store-link"
    root_link.symlink_to(redirected_root, target_is_directory=True)
    root_emitted = []

    root_store = Store(root_link, emit=root_emitted.append, now=FakeClock())

    assert root_store._disabled is True
    assert len(root_emitted) == 1
    assert "refusing symlink store root" in root_emitted[0]["msg"]
    assert stat.S_IMODE(redirected_root.stat().st_mode) == 0o755

    real_root = tmp_path / "real-store"
    real_root.mkdir()
    redirected_samples = tmp_path / "redirected-samples"
    redirected_samples.mkdir(mode=0o755)
    (real_root / "samples").symlink_to(
        redirected_samples, target_is_directory=True)
    samples_emitted = []

    samples_store = Store(
        real_root, emit=samples_emitted.append, now=FakeClock())

    assert samples_store._disabled is True
    assert len(samples_emitted) == 1
    assert "refusing symlink samples directory" in samples_emitted[0]["msg"]
    assert stat.S_IMODE(redirected_samples.stat().st_mode) == 0o755


# The store root is 0700 and _prepare_directory refuses a symlinked directory,
# so these leaves are only reachable by something that already sits inside the
# tree. They are covered anyway because the same open() that a symlink would
# redirect is the one that must not block: Store is constructed at
# bin/walkingpad-bridge:56, before bridge.run(), and plan §3.1 requires a
# filesystem problem to degrade to "no stats" rather than strand belt control.


def test_store_refuses_a_symlinked_state_file(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    decoy = tmp_path / "decoy.json"
    decoy.write_text(json.dumps({"epoch": 41}), encoding="utf-8")
    (root / "state.json").symlink_to(decoy)
    emitted = []

    store = Store(root, emit=emitted.append, now=FakeClock())

    assert store._disabled is True
    assert store.epoch == 0
    assert len(emitted) == 1
    assert emitted[0]["code"] == "store_write_failed"
    assert "refusing non-regular file" in emitted[0]["msg"]
    assert json.loads(decoy.read_text(encoding="utf-8")) == {"epoch": 41}


def test_store_does_not_block_on_a_fifo_state_file(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    os.mkfifo(root / "state.json", 0o600)
    emitted = []

    def bail(signum, frame):
        raise AssertionError(
            "Store blocked opening a FIFO at state.json. A blocked open() "
            "raises nothing, so the constructor's own error boundary cannot "
            "catch it and the bridge never reaches bridge.run()."
        )

    previous = signal.signal(signal.SIGALRM, bail)
    signal.alarm(5)
    try:
        store = Store(root, emit=emitted.append, now=FakeClock())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)

    assert store._disabled is True
    assert len(emitted) == 1
    assert "refusing non-regular file" in emitted[0]["msg"]


def test_oversized_state_file_starts_fresh_without_disabling(tmp_path):
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    # Valid JSON that an unbounded read would have parsed, so the assertion
    # below can only pass if the size bound rejected it before json saw it.
    (root / "state.json").write_text(
        json.dumps({"epoch": 41, "last_pad": {"steps": 5}})
        + " " * (MAX_STATE_BYTES + 1),
        encoding="utf-8",
    )
    emitted = []

    store = Store(root, emit=emitted.append, now=FakeClock())

    # Oversized is corruption, not tampering: the next atomic write heals it,
    # so it must not spend this module's single error emission.
    assert store._disabled is False
    assert emitted == []
    assert store.epoch == 0
    assert store.last_pad is None


def test_store_refuses_a_symlinked_bouts_file(tmp_path):
    root = tmp_path / "store"
    decoy = tmp_path / "decoy.jsonl"
    emitted = []
    clock = FakeClock()
    store = Store(root, emit=emitted.append, now=clock)
    (root / "bouts.jsonl").symlink_to(decoy)

    # A bout is only appended once the pad's own counters have advanced, so
    # two running frames and a stop are what reach _write_bout at all.
    store.record(
        make_status("running", 2.0, elapsed_s=100, distance_km=1.00, steps=200),
        None, None,
    )
    clock.advance(1)
    store.record(
        make_status("running", 2.1, elapsed_s=101, distance_km=1.01, steps=202),
        None, None,
    )
    clock.advance(1)
    store.record(
        make_status("stopped", 0.0, elapsed_s=103, distance_km=1.03, steps=206),
        None, None,
    )

    assert store._disabled is True
    assert not decoy.exists()
    assert len(emitted) == 1
    assert "refusing non-regular file" in emitted[0]["msg"]


def test_store_refuses_a_symlinked_sample_file(tmp_path):
    clock = FakeClock()
    root = tmp_path / "store"
    decoy = tmp_path / "decoy.jsonl"
    emitted = []
    store = Store(root, emit=emitted.append, now=clock)
    date = time.strftime("%Y-%m-%d", time.gmtime(clock.wall))
    (root / "samples" / f"{date}.jsonl").symlink_to(decoy)

    store.record(make_status("running", 2.0), None, None)

    assert store._disabled is True
    assert not decoy.exists()
    assert len(emitted) == 1
    assert "refusing non-regular file" in emitted[0]["msg"]


def test_state_rewrite_uses_an_unpredictable_temp_and_strands_none(tmp_path):
    root = tmp_path / "store"
    decoy = tmp_path / "decoy.json"
    store = Store(root, now=FakeClock())
    # The name _write_state used before mkstemp. Nothing may open it.
    (root / "state.json.tmp").symlink_to(decoy)

    store.record(make_status("running", 2.0), None, None)
    store.close()

    assert store._disabled is False
    assert not decoy.exists()
    assert (root / "state.json.tmp").is_symlink()
    # The planted symlink is the only .tmp entry, so no mkstemp temporary was
    # left behind either; _write_state runs for the life of the bridge.
    assert sorted(p.name for p in root.glob("*.tmp")) == ["state.json.tmp"]
    assert json.loads(
        (root / "state.json").read_text(encoding="utf-8")
    )["last_pad"] is not None


def test_unreadable_state_file_starts_fresh_without_disabling(tmp_path):
    if os.geteuid() == 0:
        # root ignores the mode bits, so the case under test cannot arise.
        return
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    state = root / "state.json"
    state.write_text(json.dumps({"epoch": 41}), encoding="utf-8")
    os.chmod(state, 0o000)
    emitted = []

    try:
        store = Store(root, emit=emitted.append, now=FakeClock())
    finally:
        os.chmod(state, 0o600)

    # Unreadable is not tampering. This was the behaviour before the leaf
    # checks existed and must survive them: os.replace can still heal the file
    # without read permission, so the store stays enabled and stays quiet.
    assert store._disabled is False
    assert emitted == []
    assert store.epoch == 0



def test_a_link_row_is_appended_with_the_shape_open_question_9_needs(tmp_path):
    # Design §2.5. answered_s alone cannot separate "replied steadily for 29 s"
    # from "replied twice, 29 s apart", and the ~29 s mute is a claim about the
    # first shape -- so the frame count and the connection's own length are part
    # of the row, not decoration.
    root = tmp_path / "store"
    clock = FakeClock()
    store = Store(root, now=clock)

    store.record_link(conn_s=44.1, answered_s=29.04, frames=29, why="silence")
    clock.advance(60)
    store.record_link(conn_s=15.2, answered_s=0.0, frames=0, why="dropped")

    rows = read_jsonl(root / "links.jsonl")
    assert rows == [
        {"t": 1_700_000_000.0, "conn_s": 44.1, "answered_s": 29.04,
         "frames": 29, "why": "silence"},
        {"t": 1_700_000_060.0, "conn_s": 15.2, "answered_s": 0.0,
         "frames": 0, "why": "dropped"},
    ]


def test_the_link_log_never_feeds_attribution(tmp_path):
    # ⚠ The load-bearing half of §2.5. Design §2.2 keeps history rebuildable
    # from samples alone, and every store written before links.jsonl existed
    # must keep reading identically -- so a link row must change no counter, no
    # bout and no sample. Asserting that by running the SAME frames with and
    # without one is what makes "diagnostic only" a property rather than a
    # promise: any future code that consulted these rows would diverge here.
    def run(with_link_rows):
        root = tmp_path / ("linked" if with_link_rows else "plain")
        clock = FakeClock()
        store = Store(root, now=clock)
        for steps in (100, 140, 180):
            if with_link_rows:
                store.record_link(conn_s=30.0, answered_s=29.0, frames=29,
                                  why="silence")
            store.record(
                make_status("running", 2.5, steps=steps,
                            distance_km=round(steps / 1300.0, 2),
                            elapsed_s=100 + steps),
                None, None)
            clock.advance(2)
        store.close()
        return root, store

    linked_root, linked = run(True)
    plain_root, plain = run(False)

    assert linked.attributed == plain.attributed
    assert linked.epoch == plain.epoch
    assert (read_jsonl(linked_root / "bouts.jsonl")
            == read_jsonl(plain_root / "bouts.jsonl"))
    assert (read_jsonl(next((linked_root / "samples").glob("*.jsonl")))
            == read_jsonl(next((plain_root / "samples").glob("*.jsonl"))))
    assert (json.loads((linked_root / "state.json").read_text(encoding="utf-8"))
            == json.loads((plain_root / "state.json").read_text(encoding="utf-8")))
    # And the rows really were written, so the comparison above is not vacuous.
    assert len(read_jsonl(linked_root / "links.jsonl")) == 3


def test_a_non_regular_link_log_degrades_the_store_once(tmp_path):
    # Same leaf hardening as every other predictable path in here: links.jsonl
    # is as guessable as bouts.jsonl, so it gets the same O_NOFOLLOW refusal and
    # the same design §3.1 answer -- degrade once, never reach belt control.
    root = tmp_path / "store"
    root.mkdir(mode=0o700)
    decoy = tmp_path / "decoy"
    decoy.write_text("untouched", encoding="utf-8")
    (root / "links.jsonl").symlink_to(decoy)
    emitted = []

    store = Store(root, emit=emitted.append, now=FakeClock())
    store.record_link(conn_s=44.1, answered_s=29.04, frames=29, why="silence")

    assert store._disabled is True
    assert [event["code"] for event in emitted] == ["store_write_failed"]
    assert "refusing non-regular file" in emitted[0]["msg"]
    assert decoy.read_text(encoding="utf-8") == "untouched"


def test_a_lost_link_reaches_links_jsonl_through_the_real_store(tmp_path):
    # The seam between the two units: everything else covering §2.5 either calls
    # Store.record_link directly or hands the Bridge a recording double, so a
    # keyword renamed on one side of that seam would pass both suites. This
    # drives the real watchdog into the real writer and then reads the file.
    class SilentTransport:
        connected = True

        def write(self, payload):
            pass

        def disconnect(self):
            self.connected = False

    clock = FakeClock()
    store = Store(tmp_path, now=clock)
    bridge = Bridge(transport=SilentTransport(), store=store)
    bridge.emit = lambda obj: None
    bridge._connection_open = True
    bridge._frames_this_connection = 29
    bridge._connected_at = time.monotonic() - LINK_DEAD_AFTER_S - 29.04
    bridge._last_status_at = time.monotonic() - LINK_DEAD_AFTER_S - 0.01

    bridge._service_link()

    rows = read_jsonl(tmp_path / "links.jsonl")
    assert len(rows) == 1
    assert rows[0]["why"] == "silence"
    assert rows[0]["frames"] == 29
    assert 29.0 <= rows[0]["answered_s"] <= 29.1
    assert rows[0]["t"] == clock.wall
