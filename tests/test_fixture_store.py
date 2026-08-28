"""Checks for the synthetic store the QML harness renders against.

⚠ This is NOT a second set of fixtures for tests/test_stats_query.py, and it must
not become one. That suite writes its rows by hand on purpose, so a fault in
walkingpad/store.py cannot hide whether the reader honours the on-disk contract.
The generator here does the opposite by design -- it drives the real Store, so
what the harness renders is exactly what the bridge writes. Both are correct for
their own job; merging them would lose the independence of one or the realism of
the other.

What is worth pinning is that the generator keeps producing the SHAPES the
harness exists to put in front of the window. Charts §6 names them, and a fixture
that quietly stopped containing an unobserved bout would leave that path
unrendered while every test still passed.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = "bin/walkingpad-fixture-store"
STATS = "bin/walkingpad-stats"

# Pinned so the generated week lands on known dates. The generator defaults to
# "now" because a harness wants history that reaches today; a test wants the
# same bytes every run.
ANCHOR = "2026-08-27T20:00:00+00:00"


def generate(target, *arguments):
    return subprocess.run(
        [sys.executable, FIXTURE, str(target), "--anchor", ANCHOR, *arguments],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TZ": "UTC"},
    )


def run_stats(store_dir, *arguments):
    completed = subprocess.run(
        [sys.executable, STATS, *arguments, "--store", str(store_dir)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TZ": "UTC"},
    )
    return json.loads(completed.stdout)


def bouts(store_dir):
    path = Path(store_dir) / "bouts.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def day_of(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d")


def test_every_query_reads_the_generated_store_without_an_error(tmp_path):
    store = tmp_path / "walkingpad"
    assert generate(store).returncode == 0

    for arguments in (
        ("sessions",),
        ("totals", "--by", "day"),
        ("totals", "--by", "week"),
        ("profile",),
        ("residuals",),
    ):
        payload = run_stats(store, *arguments)
        assert "error" not in payload, (arguments, payload)


def test_the_generated_week_contains_the_shapes_the_harness_needs(tmp_path):
    store = tmp_path / "walkingpad"
    assert generate(store).returncode == 0
    rows = bouts(store)

    # Charts §6: a period whose only activity is an unobserved bout. Without one
    # in the fixture the window's "totals exist but nothing is drawable" path is
    # never rendered by anyone.
    unobserved = [row for row in rows if not row["observed"]]
    assert unobserved, "no unobserved bout to render"

    # An unobserved bout carries exact totals and no speed curve. Design §1.2 is
    # explicit that inventing one would be worse than the gap.
    for row in unobserved:
        assert row["dur_s"] > 0 and row["dist_km"] > 0
        assert "v_avg" not in row and "n" not in row

    observed = [row for row in rows if row["observed"]]
    assert observed, "no observed bout to draw a curve for"
    for row in observed:
        assert row["n"] > 0 and row["v_avg"] > 0

    # A day inside the populated week with nothing at all. Charts §2.4 has to
    # place an omitted bucket rather than a zero, which needs a real hole.
    days = {day_of(row["start"]) for row in rows}
    span = {day_of(min(row["start"] for row in rows) + offset * 86400)
            for offset in range(7)}
    assert span - days, "no empty day inside the generated week"

    # Two bouts close together: design §1.4 makes a stop and restart two observed
    # bouts in one session with no residual between them.
    starts = sorted(row["start"] for row in observed)
    assert any(0 < later - earlier < 3600
               for earlier, later in zip(starts, starts[1:]))


def test_counters_never_go_backwards_so_no_epoch_reset_is_invented(tmp_path):
    # The generator's Pad accumulates at full precision and quantises only when a
    # frame is built. An earlier version rounded every tick, which floored each
    # increment to zero and produced a week of walks with dist_km 0.00 -- flat,
    # not falling, but one sign change away from a reset design §1.4 would have
    # had to honour.
    store = tmp_path / "walkingpad"
    assert generate(store).returncode == 0

    samples = []
    for path in sorted((store / "samples").glob("*.jsonl")):
        samples.extend(json.loads(line)
                       for line in path.read_text(encoding="utf-8").splitlines()
                       if line.strip())
    assert samples

    for earlier, later in zip(samples, samples[1:]):
        for key in ("d", "s"):
            assert later[key] >= earlier[key], (key, earlier, later)
    assert samples[-1]["d"] > 0 and samples[-1]["s"] > 0

    assert run_stats(store, "residuals")["epoch"] == 0


def test_the_same_anchor_generates_the_same_store(tmp_path):
    first, second = tmp_path / "a" / "walkingpad", tmp_path / "b" / "walkingpad"
    assert generate(first).returncode == 0
    assert generate(second).returncode == 0
    assert bouts(first) == bouts(second)


def test_it_refuses_to_write_over_the_real_history(tmp_path):
    # ⚠ This test names the REAL store path, so a broken guard would destroy
    # someone's actual walking history the moment they ran the suite. Capture the
    # directory first and assert it is untouched, so the failure is a red test
    # rather than lost data.
    from walkingpad.store import DEFAULT_STORE_DIR

    real = Path(DEFAULT_STORE_DIR)
    before = sorted((path.name, path.stat().st_mtime_ns, path.stat().st_size)
                    for path in real.iterdir()) if real.is_dir() else None

    completed = generate(DEFAULT_STORE_DIR)
    assert completed.returncode != 0
    assert "refusing to write over the real history store" in completed.stderr

    after = sorted((path.name, path.stat().st_mtime_ns, path.stat().st_size)
                   for path in real.iterdir()) if real.is_dir() else None
    assert after == before


def test_it_refuses_a_directory_that_already_has_content(tmp_path):
    store = tmp_path / "walkingpad"
    store.mkdir()
    (store / "bouts.jsonl").write_text("{}\n", encoding="utf-8")

    completed = generate(store)
    assert completed.returncode != 0
    assert "pass --force" in completed.stderr

    assert generate(store, "--force").returncode == 0
