"""Black-box checks for the reporting CLI contract in CONTRACT.md §4.

Sessions are reconstructed at read time, and unobserved bouts carry exact totals
without pretending that a speed curve was recorded. Those distinctions are easy
to erase with an innocent grouping or aggregation change, so this suite exercises
the installed command surface rather than importing its implementation.

Fixtures are written by hand because walkingpad/store.py is an independently built
sibling unit. A failure there must not hide whether this reader honors the on-disk
contract, and this reader must never depend on that module merely to start.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
STATS = "bin/walkingpad-stats"


def epoch(year, month, day, hour=0, minute=0, second=0):
    return datetime(
        year, month, day, hour, minute, second,
        tzinfo=timezone.utc,
    ).timestamp()


def run_stats(store_dir, *arguments, timeout=None):
    # timeout stays None by default so ordinary cases keep waiting as long as
    # they need; the cases that assert "does not block" pass one explicitly.
    return subprocess.run(
        [sys.executable, STATS, *arguments, "--store", str(store_dir)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TZ": "UTC"},
        timeout=timeout,
    )


def result_json(result, expected_code=0):
    assert result.returncode == expected_code, result.stdout + result.stderr
    assert result.stderr == ""

    def reject_nonstandard_number(value):
        raise ValueError(f"non-standard JSON number: {value}")

    return json.loads(
        result.stdout,
        parse_constant=reject_nonstandard_number,
    )


def append_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload) + "\n")


def write_bout(store_dir, payload):
    append_json(store_dir / "bouts.jsonl", payload)
    return payload


def write_observed_bout(
        store_dir, *, start, end, dur_s, dist_km, steps,
        v_avg=2.0, v_max=2.5, n=10, e=1):
    return write_bout(store_dir, {
        "e": e,
        "start": float(start),
        "end": float(end),
        "observed": True,
        "dur_s": dur_s,
        "dist_km": dist_km,
        "steps": steps,
        "v_avg": v_avg,
        "v_max": v_max,
        "n": n,
    })


def write_unobserved_bout(
        store_dir, *, start, end, dur_s, dist_km, steps, e=1):
    return write_bout(store_dir, {
        "e": e,
        "start": float(start),
        "end": float(end),
        "observed": False,
        "dur_s": dur_s,
        "dist_km": dist_km,
        "steps": steps,
    })


def write_sample(
        store_dir, date_string, *, t, e=1, b="running", m="manual",
        v=2.0, c=2.0, g=None, d=0.1, s=10, pe=10):
    payload = {
        "t": float(t),
        "e": e,
        "b": b,
        "m": m,
        "v": v,
        "c": c,
        "g": g,
        "d": d,
        "s": s,
        "pe": pe,
    }
    append_json(store_dir / "samples" / f"{date_string}.jsonl", payload)
    return payload


def write_state(
        store_dir, *, epoch_id, last_pad, attributed,
        last_seen_wall=None):
    payload = {
        "epoch": epoch_id,
        "last_pad": last_pad,
        "attributed": attributed,
        "open_bout": None,
        "last_seen_wall": last_seen_wall,
    }
    path = store_dir / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def zero_residuals(state_status):
    return {
        "state_status": state_status,
        "epoch": 0,
        "last_pad": {"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        "attributed": {"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        "residual": {"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        "last_seen_wall": None,
    }


def test_empty_store_returns_normal_empty_shapes_for_each_query(tmp_path):
    sessions = result_json(run_stats(tmp_path, "sessions"))
    totals = result_json(run_stats(tmp_path, "totals", "--by", "day"))
    residuals = result_json(run_stats(tmp_path, "residuals"))

    assert sessions == {
        "gap_minutes": 5.0, "sessions": [], "skipped_rows": 0,
    }
    assert totals == {"by": "day", "buckets": [], "skipped_rows": 0}
    assert residuals == zero_residuals("not_found")


def test_gap_rejects_negative_and_non_finite_values_as_json_errors(tmp_path):
    for value in ("nan", "1e309", "-1"):
        result = run_stats(tmp_path, "sessions", "--gap", value)
        payload = result_json(result, expected_code=1)

        assert payload.keys() == {"error"}
        assert "finite and greater than or equal to 0" in payload["error"]
        assert len(result.stdout.strip().splitlines()) == 1


def test_gap_exactly_at_the_boundary_starts_a_new_session(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    with (tmp_path / "bouts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write('{"torn"\n')
    write_observed_bout(
        tmp_path, start=start + 360, end=start + 420,
        dur_s=60, dist_km=0.04, steps=80)

    payload = result_json(run_stats(tmp_path, "sessions", "--gap", "5"))

    assert len(payload["sessions"]) == 2


def test_sessions_skip_wrong_shaped_bouts_and_report_the_count(tmp_path):
    start = epoch(2026, 8, 26, 8)
    good = write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    missing_key = dict(good)
    missing_key.pop("steps")
    write_bout(tmp_path, missing_key)
    wrong_type = dict(good)
    wrong_type["steps"] = "eighty"
    write_bout(tmp_path, wrong_type)
    write_bout(tmp_path, ["not", "an", "object"])

    payload = result_json(run_stats(tmp_path, "sessions"))

    assert payload["skipped_rows"] == 3
    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["bouts"] == [good]


def test_gap_just_under_the_boundary_merges_sessions(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    write_observed_bout(
        tmp_path, start=start + 359, end=start + 419,
        dur_s=60, dist_km=0.04, steps=80)

    payload = result_json(run_stats(tmp_path, "sessions", "--gap", "5"))

    assert len(payload["sessions"]) == 1
    assert len(payload["sessions"][0]["bouts"]) == 2


def test_gap_just_over_the_boundary_starts_a_new_session(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    write_observed_bout(
        tmp_path, start=start + 361, end=start + 421,
        dur_s=60, dist_km=0.04, steps=80)

    payload = result_json(run_stats(tmp_path, "sessions", "--gap", "5"))

    assert len(payload["sessions"]) == 2


def test_nested_bouts_group_against_and_report_the_running_maximum_end(tmp_path):
    start = epoch(2026, 8, 26, 8)
    bouts = [
        write_observed_bout(
            tmp_path, start=start, end=start + 1000,
            dur_s=1000, dist_km=0.7, steps=1400),
        write_observed_bout(
            tmp_path, start=start + 100, end=start + 200,
            dur_s=100, dist_km=0.07, steps=140),
        write_observed_bout(
            tmp_path, start=start + 1100, end=start + 1150,
            dur_s=50, dist_km=0.04, steps=80),
        write_observed_bout(
            tmp_path, start=start + 1120, end=start + 1130,
            dur_s=10, dist_km=0.01, steps=20),
    ]

    payload = result_json(run_stats(tmp_path, "sessions", "--gap", "5"))

    assert len(payload["sessions"]) == 1
    assert payload["sessions"][0]["bouts"] == bouts
    assert payload["sessions"][0]["end"] == start + 1150


def test_totals_by_day_use_local_calendar_days_and_correct_sums(tmp_path):
    day_one = epoch(2026, 8, 26, 9)
    day_two = epoch(2026, 8, 27, 11)
    write_observed_bout(
        tmp_path, start=day_one, end=day_one + 60,
        dur_s=60, dist_km=0.47, steps=100,
        v_avg=2.0, v_max=2.5, n=10)
    write_observed_bout(
        tmp_path, start=day_one + 3600, end=day_one + 3720,
        dur_s=120, dist_km=0.13, steps=50,
        v_avg=4.0, v_max=4.5, n=30)
    write_observed_bout(
        tmp_path, start=day_two, end=day_two + 90,
        dur_s=90, dist_km=0.25, steps=75,
        v_avg=3.0, v_max=3.5, n=20)

    payload = result_json(run_stats(tmp_path, "totals", "--by", "day"))

    assert payload == {
        "by": "day",
        "skipped_rows": 0,
        "buckets": [
            {
                "key": "2026-08-26",
                "start": day_one,
                "end": day_one + 3720,
                "dur_s": 180,
                "dist_km": 0.6,
                "steps": 150,
                "v_avg": 3.5,
                "unobserved_s": 0,
                "unobserved_dist_km": 0.0,
                "unobserved_steps": 0,
            },
            {
                "key": "2026-08-27",
                "start": day_two,
                "end": day_two + 90,
                "dur_s": 90,
                "dist_km": 0.25,
                "steps": 75,
                "v_avg": 3.0,
                "unobserved_s": 0,
                "unobserved_dist_km": 0.0,
                "unobserved_steps": 0,
            },
        ],
    }


def test_totals_by_week_split_at_monday_and_use_monday_keys(tmp_path):
    sunday = epoch(2026, 8, 30, 12)
    monday = epoch(2026, 8, 31, 12)
    write_observed_bout(
        tmp_path, start=sunday, end=sunday + 60,
        dur_s=60, dist_km=0.1, steps=100)
    write_observed_bout(
        tmp_path, start=monday, end=monday + 120,
        dur_s=120, dist_km=0.2, steps=200)

    payload = result_json(run_stats(tmp_path, "totals", "--by", "week"))

    assert [bucket["key"] for bucket in payload["buckets"]] == [
        "2026-08-24", "2026-08-31"]
    assert [bucket["dur_s"] for bucket in payload["buckets"]] == [60, 120]
    assert [bucket["dist_km"] for bucket in payload["buckets"]] == [0.1, 0.2]


def test_totals_by_month_split_bouts_in_different_months(tmp_path):
    august = epoch(2026, 8, 31, 23)
    september = epoch(2026, 9, 1, 1)
    write_observed_bout(
        tmp_path, start=august, end=august + 60,
        dur_s=60, dist_km=0.1, steps=100)
    write_observed_bout(
        tmp_path, start=september, end=september + 120,
        dur_s=120, dist_km=0.2, steps=200)

    payload = result_json(run_stats(tmp_path, "totals", "--by", "month"))

    assert [bucket["key"] for bucket in payload["buckets"]] == [
        "2026-08", "2026-09"]
    assert [bucket["steps"] for bucket in payload["buckets"]] == [100, 200]


def test_totals_by_all_collapse_every_bout_into_one_grand_total(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.47, steps=100,
        v_avg=2.0, v_max=2.5, n=10)
    write_observed_bout(
        tmp_path, start=start + 86400, end=start + 86520,
        dur_s=120, dist_km=0.13, steps=50,
        v_avg=4.0, v_max=4.5, n=30)
    write_unobserved_bout(
        tmp_path, start=start + 172800, end=start + 172890,
        dur_s=90, dist_km=0.2, steps=75)

    payload = result_json(run_stats(tmp_path, "totals", "--by", "all"))

    assert payload == {
        "by": "all",
        "skipped_rows": 0,
        "buckets": [{
            "key": "all",
            "start": start,
            "end": start + 172890,
            "dur_s": 270,
            "dist_km": 0.8,
            "steps": 225,
            "v_avg": 3.5,
            "unobserved_s": 90,
            "unobserved_dist_km": 0.2,
            "unobserved_steps": 75,
        }],
    }


def test_mixed_session_includes_unobserved_totals_and_discloses_share(tmp_path):
    start = epoch(2026, 8, 26, 8)
    observed = write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.47, steps=100,
        v_avg=2.3, v_max=3.0, n=20)
    unobserved = write_unobserved_bout(
        tmp_path, start=start + 120, end=start + 150,
        dur_s=30, dist_km=0.13, steps=25)

    payload = result_json(run_stats(tmp_path, "sessions"))
    session = payload["sessions"][0]

    assert session["dur_s"] == 90
    assert session["dist_km"] == 0.6
    assert session["steps"] == 125
    assert session["unobserved_s"] == 30
    assert session["unobserved_dist_km"] == 0.13
    assert session["unobserved_steps"] == 25
    assert session["observed"] is False
    assert session["v_avg"] == 2.3
    assert session["v_max"] == 3.0
    assert session["bouts"] == [observed, unobserved]


def test_entirely_unobserved_session_has_no_sample_derived_speeds(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_unobserved_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.1, steps=100)
    write_unobserved_bout(
        tmp_path, start=start + 120, end=start + 180,
        dur_s=60, dist_km=0.2, steps=200)

    payload = result_json(run_stats(tmp_path, "sessions"))
    session = payload["sessions"][0]

    assert session["v_avg"] is None
    assert session["v_max"] is None
    assert session["observed"] is False


def test_sessions_filter_bouts_before_reconstructing_session_membership(tmp_path):
    starts = [epoch(2026, 8, 26, 8, minute) for minute in (0, 2, 4, 6)]
    bouts = [
        write_observed_bout(
            tmp_path, start=start, end=start + 30,
            dur_s=30, dist_km=0.02, steps=40)
        for start in starts
    ]
    unfiltered = result_json(run_stats(tmp_path, "sessions"))

    filtered = result_json(run_stats(
        tmp_path, "sessions",
        "--since", "2026-08-26T08:02:00",
        "--until", "2026-08-26T08:04:00"))

    assert len(unfiltered["sessions"]) == 1
    assert unfiltered["sessions"][0]["bouts"] == bouts
    assert len(filtered["sessions"]) == 1
    assert filtered["sessions"][0]["bouts"] == bouts[1:3]
    assert filtered["sessions"][0]["start"] == starts[1]


def test_totals_filters_bouts_before_calendar_aggregation(tmp_path):
    before = epoch(2026, 8, 25, 12)
    first = epoch(2026, 8, 26)
    second = epoch(2026, 8, 26, 12)
    boundary = epoch(2026, 8, 27)
    after = epoch(2026, 8, 27, 12)
    for start, duration, distance, steps in (
            (before, 10, 0.1, 10),
            (first, 20, 0.2, 20),
            (second, 30, 0.3, 30),
            (boundary, 40, 0.4, 40),
            (after, 50, 0.5, 50)):
        write_observed_bout(
            tmp_path, start=start, end=start + duration,
            dur_s=duration, dist_km=distance, steps=steps)

    payload = result_json(run_stats(
        tmp_path, "totals", "--by", "day",
        "--since", "2026-08-26", "--until", "2026-08-27"))

    assert [bucket["key"] for bucket in payload["buckets"]] == [
        "2026-08-26", "2026-08-27"]
    assert payload["buckets"][0]["dur_s"] == 50
    assert payload["buckets"][0]["dist_km"] == 0.5
    assert payload["buckets"][0]["steps"] == 50
    assert payload["buckets"][1]["dur_s"] == 40
    assert payload["buckets"][1]["dist_km"] == 0.4
    assert payload["buckets"][1]["steps"] == 40


def test_residuals_compute_last_pad_minus_attributed_with_rounding(tmp_path):
    last_seen = epoch(2026, 8, 26, 12)
    state = write_state(
        tmp_path,
        epoch_id=7,
        last_pad={"elapsed_s": 1000, "dist_km": 1.0, "steps": 1300},
        attributed={"elapsed_s": 700, "dist_km": 0.67, "steps": 900},
        last_seen_wall=last_seen,
    )

    payload = result_json(run_stats(tmp_path, "residuals"))

    assert payload == {
        "state_status": "ok",
        "epoch": 7,
        "last_pad": state["last_pad"],
        "attributed": state["attributed"],
        "residual": {"elapsed_s": 300, "dist_km": 0.33, "steps": 400},
        "last_seen_wall": last_seen,
    }


def test_residuals_missing_state_reports_that_no_state_exists(tmp_path):
    result = run_stats(tmp_path, "residuals")

    assert result_json(result) == zero_residuals("not_found")


def test_residuals_malformed_state_reports_unreadable_not_real_zeros(tmp_path):
    (tmp_path / "state.json").write_text('{"epoch": 7', encoding="utf-8")

    result = run_stats(tmp_path, "residuals")

    assert result_json(result) == zero_residuals("unreadable")


def test_residuals_real_zeros_are_distinct_from_missing_state(tmp_path):
    write_state(
        tmp_path,
        epoch_id=0,
        last_pad={"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        attributed={"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
    )

    payload = result_json(run_stats(tmp_path, "residuals"))

    assert payload == zero_residuals("ok")


def test_session_detail_returns_only_samples_inside_its_utc_date_window(tmp_path):
    start = epoch(2026, 8, 26, 23, 59)
    second_start = epoch(2026, 8, 27, 0, 0, 30)
    end = epoch(2026, 8, 27, 0, 2)
    write_observed_bout(
        tmp_path, start=start, end=start + 30,
        dur_s=30, dist_km=0.02, steps=40)
    write_observed_bout(
        tmp_path, start=second_start, end=end,
        dur_s=90, dist_km=0.06, steps=120)

    write_sample(tmp_path, "2026-08-26", t=start - 1)
    first = write_sample(tmp_path, "2026-08-26", t=start, s=1, pe=1)
    sample_path = tmp_path / "samples" / "2026-08-26.jsonl"
    with sample_path.open("a", encoding="utf-8") as stream:
        stream.write('{"torn"\n')
    middle = write_sample(
        tmp_path, "2026-08-27", t=second_start, s=2, pe=2)
    last = write_sample(tmp_path, "2026-08-27", t=end, s=3, pe=3)
    write_sample(tmp_path, "2026-08-27", t=end + 1, s=4, pe=4)
    append_json(tmp_path / "samples" / "1999-01-01.jsonl", {})

    payload = result_json(run_stats(
        tmp_path, "session", "--start", str(start)))

    assert payload["gap_minutes"] == 5.0
    assert payload["session"]["start"] == start
    assert payload["session"]["end"] == end
    assert payload["samples"] == [first, middle, last]
    assert payload["skipped_rows"] == 1


def test_session_skips_wrong_shaped_samples_and_reports_the_count(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    good = write_sample(tmp_path, "2026-08-26", t=start + 30)
    missing_key = dict(good)
    missing_key.pop("s")
    append_json(tmp_path / "samples" / "2026-08-26.jsonl", missing_key)
    wrong_type = dict(good)
    wrong_type["v"] = "fast"
    append_json(tmp_path / "samples" / "2026-08-26.jsonl", wrong_type)
    append_json(tmp_path / "samples" / "2026-08-26.jsonl", 42)

    payload = result_json(run_stats(
        tmp_path, "session", "--start", str(start)))

    assert payload["samples"] == [good]
    assert payload["skipped_rows"] == 3


def test_session_detail_rejects_a_start_that_matches_nothing(tmp_path):
    start = epoch(2026, 8, 26, 8)
    missing = start + 1
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)

    result = run_stats(tmp_path, "session", "--start", str(missing))
    payload = result_json(result, expected_code=1)

    assert payload.keys() == {"error"}
    assert str(missing) in payload["error"]


def test_session_detail_rejects_implausible_daily_file_ranges(tmp_path):
    start = epoch(2026, 1, 1)
    write_observed_bout(
        tmp_path,
        start=start,
        end=start + 31 * 24 * 60 * 60,
        dur_s=60,
        dist_km=0.04,
        steps=80,
    )

    result = run_stats(tmp_path, "session", "--start", str(start))
    payload = result_json(result, expected_code=1)

    assert payload.keys() == {"error"}
    assert "maximum is 31" in payload["error"]


def test_every_subcommand_emits_exactly_one_json_object_line(tmp_path):
    start = epoch(2026, 8, 26, 8)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    write_sample(tmp_path, "2026-08-26", t=start + 30)
    write_state(
        tmp_path,
        epoch_id=1,
        last_pad={"elapsed_s": 60, "dist_km": 0.04, "steps": 80},
        attributed={"elapsed_s": 60, "dist_km": 0.04, "steps": 80},
        last_seen_wall=start + 60,
    )

    results = [
        run_stats(tmp_path, "sessions"),
        run_stats(tmp_path, "session", "--start", str(start)),
        run_stats(tmp_path, "totals", "--by", "all"),
        run_stats(tmp_path, "residuals"),
        run_stats(tmp_path, "profile"),
    ]

    for result in results:
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(result.stdout.strip().splitlines()) == 1
        assert isinstance(json.loads(result.stdout), dict)


# ---------------------------------------------------------------- profile
#
# Charts §4 moved bucketing into Python so pytest can reach it (ADR D-1). The
# distinctions that are easy to erase here are the same two the storage model
# rests on: an omitted bucket is not a zero one, and time nobody observed is not
# time at zero speed.


def write_walk(store_dir, date_string, *, start, seconds, e=1,
               v=2.5, steps_per_second=2, metres_per_second=0.7,
               step_seconds=1):
    """Append one second-by-second running walk and return its samples.

    Cumulative counters are what the store actually records (Stage 3 §2.2), so
    the fixture advances them rather than writing per-sample deltas.
    """
    written = []
    for index in range(seconds + 1):
        offset = index * step_seconds
        written.append(write_sample(
            store_dir, date_string,
            t=start + offset,
            e=e,
            b="running",
            v=v,
            c=v,
            d=round(offset * metres_per_second / 1000.0, 2),
            s=offset * steps_per_second,
            pe=offset,
        ))
    return written


def test_profile_empty_store_reports_no_extent_and_no_buckets(tmp_path):
    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload == {
        "bucket_s": 60,
        "start": None,
        "end": None,
        "buckets": [],
        "speed_histogram": [],
        "totals": {
            "dur_s": 0, "dist_km": 0.0, "steps": 0, "v_avg": None,
            "unobserved_s": 0, "unobserved_dist_km": 0.0,
            "unobserved_steps": 0,
        },
        "skipped_rows": 0,
    }


def test_profile_buckets_steps_by_the_delta_across_bucket_edges(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=180)

    payload = result_json(run_stats(tmp_path, "profile", "--bucket", "60"))
    buckets = payload["buckets"]

    assert [bucket["t"] for bucket in buckets] == [
        start, start + 60, start + 120, start + 180]
    # Each delta is charged to the bucket of the LATER sample, so the period's
    # first sample opens no interval and its bucket carries only the 59 that
    # follow it. The trailing bucket holds the single sample past the third
    # minute, and therefore the one delta that crossed into it.
    assert [bucket["steps"] for bucket in buckets] == [118, 120, 120, 2]
    assert [bucket["dur_s"] for bucket in buckets] == [59, 60, 60, 1]
    assert [bucket["v_avg"] for bucket in buckets] == [2.5, 2.5, 2.5, 2.5]


def test_profile_omits_buckets_with_no_samples_but_keeps_measured_zero(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=59)
    # Stage 3 §2.2 records a 5 s tail after the belt leaves starting/running, so
    # the minute after a walk really does contain samples whose counters do not
    # move. That is a measured zero, not an absence.
    write_sample(tmp_path, "2026-08-26", t=start + 60, b="idle", v=0.0,
                 c=None, d=0.04, s=118, pe=59)
    # Ten minutes at the desk, then a second walk. The pad's own counters carry
    # on from where the first walk left them.
    resumed = start + 660
    for index in range(61):
        write_sample(
            tmp_path, "2026-08-26",
            t=resumed + index, e=1, b="running", v=2.5, c=2.5,
            d=round((59 + index) * 0.7 / 1000.0, 2),
            s=(59 + index) * 2, pe=59 + index)

    payload = result_json(run_stats(tmp_path, "profile", "--bucket", "60"))
    drawn = {bucket["t"]: bucket for bucket in payload["buckets"]}

    assert set(drawn) == {start, start + 60, resumed, resumed + 60}
    # The silent minutes are absent entirely -- that gap is the information.
    assert start + 300 not in drawn
    assert drawn[start + 60]["steps"] == 0
    assert drawn[start + 60]["dur_s"] == 0
    # No running frame landed there either, so it has no measured speed at all
    # rather than a fabricated 0.0 km/h.
    assert drawn[start + 60]["v_avg"] is None


def test_profile_never_bridges_a_hole_in_the_sample_record(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_sample(tmp_path, "2026-08-26", t=start, b="running",
                 v=2.5, d=0.0, s=0, pe=0)
    # Twenty minutes later the counters have advanced a long way. Nothing
    # observed that interval, so no bucket may claim it.
    resumed = start + 1200
    write_sample(tmp_path, "2026-08-26", t=resumed, b="running",
                 v=2.5, d=0.84, s=2400, pe=1200)

    payload = result_json(run_stats(tmp_path, "profile", "--bucket", "60"))
    drawn = {bucket["t"]: bucket for bucket in payload["buckets"]}

    assert set(drawn) == {start, resumed}
    assert drawn[resumed]["steps"] == 0
    assert drawn[resumed]["dur_s"] == 0
    assert payload["speed_histogram"] == []


def test_profile_speed_histogram_counts_running_seconds_at_each_speed(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=60, v=2.5)
    faster = start + 61
    for index in range(31):
        write_sample(
            tmp_path, "2026-08-26",
            t=faster + index, e=1, b="running", v=3.5, c=3.5,
            d=round((61 + index) * 0.7 / 1000.0, 2),
            s=(61 + index) * 2, pe=61 + index)

    payload = result_json(run_stats(tmp_path, "profile"))

    # The interval carries the speed of the frame that OPENED it, so the single
    # 2.5 -> 3.5 step second belongs to 2.5.
    assert payload["speed_histogram"] == [
        {"kmh": 2.5, "dur_s": 61}, {"kmh": 3.5, "dur_s": 30}]


def test_profile_never_joins_counter_deltas_across_an_epoch_reset(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_sample(tmp_path, "2026-08-26", t=start, e=1, b="running",
                 v=2.5, d=0.7, s=2000, pe=1000)
    write_sample(tmp_path, "2026-08-26", t=start + 1, e=2, b="running",
                 v=2.5, d=0.0, s=0, pe=0)
    write_sample(tmp_path, "2026-08-26", t=start + 2, e=2, b="running",
                 v=2.5, d=0.0, s=2, pe=1)

    payload = result_json(run_stats(tmp_path, "profile", "--bucket", "60"))

    assert len(payload["buckets"]) == 1
    # Only the delta inside epoch 2 survives. The reset itself contributes
    # nothing rather than a negative, and the pre-reset counters are not carried
    # across into a coordinate system that does not contain them.
    assert payload["buckets"][0]["steps"] == 2
    assert payload["buckets"][0]["dur_s"] == 1


def test_profile_bucket_ladder_picks_the_smallest_rung_that_fits(tmp_path):
    start = epoch(2026, 8, 26, 0)
    # 120 one-minute buckets exactly, which is the cap rather than one past it.
    write_bout(tmp_path, {
        "e": 1, "start": float(start), "end": float(start + 119 * 60),
        "observed": False, "dur_s": 60, "dist_km": 0.04, "steps": 80,
    })

    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload["bucket_s"] == 60


def test_profile_bucket_ladder_steps_up_one_bucket_past_the_cap(tmp_path):
    start = epoch(2026, 8, 26, 0)
    write_bout(tmp_path, {
        "e": 1, "start": float(start), "end": float(start + 120 * 60),
        "observed": False, "dur_s": 60, "dist_km": 0.04, "steps": 80,
    })

    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload["bucket_s"] == 300


def test_profile_axis_spans_the_data_not_the_calendar_period(tmp_path):
    first = epoch(2026, 8, 26, 9)
    last = epoch(2026, 8, 26, 14)
    write_observed_bout(
        tmp_path, start=first, end=first + 600,
        dur_s=600, dist_km=0.4, steps=800)
    write_observed_bout(
        tmp_path, start=last, end=last + 600,
        dur_s=600, dist_km=0.4, steps=800)

    payload = result_json(run_stats(
        tmp_path, "profile",
        "--since", "2026-08-26", "--until", "2026-08-27"))

    assert payload["start"] == first
    assert payload["end"] == last + 600


def test_profile_totals_disclose_the_unobserved_share_separately(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_observed_bout(
        tmp_path, start=start, end=start + 600,
        dur_s=600, dist_km=0.40, steps=800, v_avg=2.5, v_max=2.6, n=600)
    write_unobserved_bout(
        tmp_path, start=start + 1200, end=start + 1900,
        dur_s=700, dist_km=0.48, steps=960)

    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload["totals"] == {
        "dur_s": 1300, "dist_km": 0.88, "steps": 1760, "v_avg": 2.5,
        "unobserved_s": 700, "unobserved_dist_km": 0.48,
        "unobserved_steps": 960,
    }
    # The unobserved bout has exact totals and no samples, so it widens the axis
    # and draws no bucket. Charts §2.3: provenance, not accuracy.
    assert payload["buckets"] == []
    assert payload["end"] == start + 1900


def test_profile_period_with_bouts_but_no_samples_is_not_an_error(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_observed_bout(
        tmp_path, start=start, end=start + 600,
        dur_s=600, dist_km=0.4, steps=800)

    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload["buckets"] == []
    assert payload["speed_histogram"] == []
    assert payload["totals"]["steps"] == 800


def test_profile_skips_malformed_rows_in_both_files_and_counts_them(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    write_bout(tmp_path, ["not", "an", "object"])
    write_walk(tmp_path, "2026-08-26", start=start, seconds=5)
    with (tmp_path / "samples" / "2026-08-26.jsonl").open(
            "a", encoding="utf-8") as stream:
        stream.write('{"t": 1, "e"\n')
    write_sample(tmp_path, "2026-08-26", t=start + 6, s="many")

    payload = result_json(run_stats(tmp_path, "profile"))

    assert payload["skipped_rows"] == 3
    assert payload["buckets"][0]["steps"] == 10


def test_profile_ignores_files_outside_the_requested_range(tmp_path):
    inside = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=inside, seconds=60)
    write_walk(tmp_path, "2026-08-20", start=epoch(2026, 8, 20, 9), seconds=60)

    payload = result_json(run_stats(
        tmp_path, "profile", "--since", "2026-08-26"))

    assert payload["start"] == inside
    assert all(bucket["t"] >= inside for bucket in payload["buckets"])


def test_profile_bucket_rejects_values_that_are_not_auto_or_positive(tmp_path):
    for value in ("0", "-60", "5.5", "minute"):
        result = run_stats(tmp_path, "profile", "--bucket", value)
        payload = result_json(result, expected_code=1)

        assert payload.keys() == {"error"}
        assert "'auto'" in payload["error"]
        assert len(result.stdout.strip().splitlines()) == 1


def test_profile_accepts_gap_without_changing_what_it_reports(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=60)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=120)

    default_gap = result_json(run_stats(tmp_path, "profile"))
    wide_gap = result_json(run_stats(tmp_path, "profile", "--gap", "45"))

    assert default_gap == wide_gap


def test_profile_coarsening_a_bucket_matches_accumulating_it_directly(tmp_path):
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=900)
    # A later bout widens the axis past the 120-bucket cap, so `auto` reaches the
    # 300 s rung by coarsening the 60 s pass rather than accumulating it directly.
    write_unobserved_bout(
        tmp_path, start=start + 10800, end=start + 11400,
        dur_s=600, dist_km=0.4, steps=800)

    explicit = result_json(run_stats(tmp_path, "profile", "--bucket", "300"))
    coarsened = result_json(run_stats(tmp_path, "profile"))

    assert coarsened["bucket_s"] == 300
    assert coarsened["buckets"] == explicit["buckets"]


def test_non_finite_numbers_cost_their_own_row_and_nothing_more(tmp_path):
    # json.loads accepts the Infinity and NaN tokens, so "is a number" is not
    # enough: one hand-edited row used to reach emit()'s allow_nan=False and
    # fail the whole period with "Out of range float values are not JSON
    # compliant". A torn row costs itself, exactly as §2.2/§2.3 require.
    start = epoch(2026, 8, 26, 9)
    write_observed_bout(
        tmp_path, start=start, end=start + 60,
        dur_s=60, dist_km=0.04, steps=80)
    with (tmp_path / "bouts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            '{"e":1,"start":%r,"end":%r,"observed":true,"dur_s":60,'
            '"dist_km":Infinity,"steps":80,"v_avg":2.5,"v_max":2.6,"n":60}\n'
            % (start, start + 60))
    write_sample(tmp_path, "2026-08-26", t=start + 30)
    append_json(tmp_path / "samples" / "2026-08-26.jsonl", {
        "t": start + 31, "e": 1, "b": "running", "m": "manual",
        "v": float("nan"), "c": None, "g": None, "d": 0.1, "s": 10, "pe": 10,
    })

    for arguments in (("sessions",), ("totals", "--by", "all"), ("profile",)):
        payload = result_json(run_stats(tmp_path, *arguments))

        assert "error" not in payload, arguments
        assert payload["skipped_rows"] >= 1, arguments


def test_profile_counts_the_delta_that_crosses_into_the_first_bucket(tmp_path):
    # Charts §6 requires this reader and WalkingpadModel.js to agree at a bucket
    # boundary, and the JS keeps the sample just before the range so the first
    # bucket gets the interval that crossed into it. Dropping it here cost that
    # bucket one poll: the same minute read 118 against the JS reader's 120.
    start = epoch(2026, 8, 26, 9)
    write_walk(tmp_path, "2026-08-26", start=start, seconds=120)

    payload = result_json(run_stats(
        tmp_path, "profile", "--bucket", "60",
        "--since", "2026-08-26T09:01:00", "--until", "2026-08-26T09:03:00"))
    drawn = {bucket["t"]: bucket for bucket in payload["buckets"]}

    assert drawn[start + 60]["steps"] == 120
    # The out-of-range sample opens the interval but must not widen the axis or
    # create a bucket of its own.
    assert start not in drawn
    assert payload["start"] == start + 60


def test_profile_reaches_into_the_previous_day_file_for_that_delta(tmp_path):
    # The crossing sample sits in the PREVIOUS UTC day's file whenever a period
    # begins at midnight, which is every "Today" query the window makes.
    midnight = epoch(2026, 8, 26, 0)
    write_sample(tmp_path, "2026-08-25", t=midnight - 1, b="running",
                 v=2.5, d=0.70, s=2000, pe=1000)
    write_sample(tmp_path, "2026-08-26", t=midnight, b="running",
                 v=2.5, d=0.70, s=2002, pe=1001)
    write_sample(tmp_path, "2026-08-26", t=midnight + 1, b="running",
                 v=2.5, d=0.70, s=2004, pe=1002)

    payload = result_json(run_stats(
        tmp_path, "profile", "--bucket", "60", "--since", "2026-08-26"))
    drawn = {bucket["t"]: bucket for bucket in payload["buckets"]}

    assert set(drawn) == {midnight}
    # Two deltas: the one crossing midnight, and the one wholly inside the day.
    assert drawn[midnight]["steps"] == 4
    assert drawn[midnight]["dur_s"] == 2


def test_profile_still_will_not_join_across_a_hole_at_the_range_edge(tmp_path):
    # Reaching back for the crossing sample must not become a licence to join
    # across an absence. The cadence guard still governs.
    midnight = epoch(2026, 8, 26, 0)
    write_sample(tmp_path, "2026-08-25", t=midnight - 600, b="running",
                 v=2.5, d=0.70, s=2000, pe=1000)
    write_sample(tmp_path, "2026-08-26", t=midnight, b="running",
                 v=2.5, d=0.98, s=2800, pe=1400)

    payload = result_json(run_stats(
        tmp_path, "profile", "--bucket", "60", "--since", "2026-08-26"))

    assert payload["buckets"] == [
        {"t": midnight, "steps": 0, "dist_km": 0.0, "dur_s": 0, "v_avg": 2.5}]


def test_profile_buckets_align_to_the_unix_epoch(tmp_path):
    # The convention WalkingpadModel.js repeats for the popup: a bucket starts at
    # floor(t / bucket_s) * bucket_s, so a sample 30 s into a minute lands in the
    # bucket that minute began, never in one anchored on the sample itself.
    start = epoch(2026, 8, 26, 9) + 30
    write_walk(tmp_path, "2026-08-26", start=start, seconds=5)

    payload = result_json(run_stats(tmp_path, "profile", "--bucket", "60"))

    assert payload["buckets"][0]["t"] == epoch(2026, 8, 26, 9)


# This reader opens the same predictable names the bridge writes. A FIFO here
# hangs the analysis window's query rather than belt control, but it hangs just
# as completely, and a symlink redirects the read to a file nobody asked for.


def test_residuals_refuse_a_symlinked_state_file(tmp_path):
    decoy = tmp_path / "decoy.json"
    decoy.write_text(
        json.dumps({
            "epoch": 41,
            "last_pad": {"elapsed_s": 9, "dist_km": 9.0, "steps": 9},
            "attributed": {"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        }),
        encoding="utf-8",
    )
    (tmp_path / "state.json").symlink_to(decoy)

    result = run_stats(tmp_path, "residuals")

    # Valid state the old code would have parsed and reported as real walking.
    assert result_json(result) == zero_residuals("unreadable")


def test_residuals_do_not_block_on_a_fifo_state_file(tmp_path):
    os.mkfifo(tmp_path / "state.json", 0o600)

    result = run_stats(tmp_path, "residuals", timeout=30)

    assert result_json(result) == zero_residuals("unreadable")


def test_residuals_reject_an_oversized_state_file(tmp_path):
    # Valid JSON, so only the size bound can reject it.
    (tmp_path / "state.json").write_text(
        json.dumps({
            "epoch": 1,
            "last_pad": {"elapsed_s": 1, "dist_km": 1.0, "steps": 1},
            "attributed": {"elapsed_s": 0, "dist_km": 0.0, "steps": 0},
        }) + " " * ((1 << 20) + 1),
        encoding="utf-8",
    )

    result = run_stats(tmp_path, "residuals")

    assert result_json(result) == zero_residuals("unreadable")


def test_a_symlinked_bouts_file_is_counted_as_skipped_not_read(tmp_path):
    decoy = tmp_path / "decoy.jsonl"
    append_json(decoy, {
        "e": 1, "start": epoch(2026, 3, 1, 8), "end": epoch(2026, 3, 1, 9),
        "observed": True, "dur_s": 3600, "dist_km": 4.0, "steps": 6000,
        "v_avg": 4.0, "v_max": 4.5, "n": 3600,
    })
    (tmp_path / "bouts.jsonl").symlink_to(decoy)

    payload = result_json(run_stats(tmp_path, "sessions"))

    # A whole refused file costs the file, and says so through the same counter
    # a torn row uses, so "empty" stays distinguishable from "unreadable".
    assert payload["sessions"] == []
    assert payload["skipped_rows"] == 1


def test_sessions_do_not_block_on_a_fifo_bouts_file(tmp_path):
    os.mkfifo(tmp_path / "bouts.jsonl", 0o600)

    payload = result_json(run_stats(tmp_path, "sessions", timeout=30))

    assert payload["sessions"] == []
    assert payload["skipped_rows"] == 1



def write_link(store_dir, *, t, conn_s, answered_s, frames, why):
    payload = {"t": float(t), "conn_s": conn_s, "answered_s": answered_s,
               "frames": frames, "why": why}
    append_json(store_dir / "links.jsonl", payload)
    return payload


def test_links_on_a_store_with_no_link_log_is_empty_not_an_error(tmp_path):
    # links.jsonl only appears once a connection has ended, so every store
    # written before design §2.5 existed has none. That must read as "nothing
    # recorded", never as a failed query -- and never as a JSON NaN, which is
    # what an average over zero connections would otherwise produce.
    payload = result_json(run_stats(tmp_path, "links"))
    assert payload["n"] == 0
    assert payload["answered_s"] is None
    assert payload["conn_s"] is None
    assert payload["why"] == {}
    assert payload["longest"] == []


def test_links_reports_the_distribution_open_question_9_turns_on(tmp_path):
    # The question is whether the ~29 s window is a constant. One session
    # answered for 1988 s and every session since has muted near 29 s, so an
    # average over the two regimes describes neither -- the spread and the
    # longest rows verbatim are the answer, which is why both are reported.
    for index in range(9):
        write_link(tmp_path, t=1000 + index, conn_s=44.0, answered_s=29.0,
                   frames=29, why="silence")
    write_link(tmp_path, t=2000, conn_s=2003.0, answered_s=1988.0,
               frames=1900, why="shutdown")

    payload = result_json(run_stats(tmp_path, "links"))

    assert payload["n"] == 10
    assert payload["answered_s"]["min"] == 29.0
    assert payload["answered_s"]["p50"] == 29.0
    assert payload["answered_s"]["p90"] == 29.0
    assert payload["answered_s"]["max"] == 1988.0
    assert payload["why"] == {"silence": 9, "shutdown": 1}
    assert payload["first_t"] == 1000.0
    assert payload["last_t"] == 2000.0
    assert payload["skipped_rows"] == 0


def test_links_separates_a_connection_that_never_answered(tmp_path):
    # A pad that never replied at all is a different failure from a short
    # window, and averaging the two together hides both. Fact 9's corollary
    # makes the first shape real: a stranded link can be adopted and then say
    # nothing whatsoever.
    write_link(tmp_path, t=1000, conn_s=15.1, answered_s=0.0, frames=0,
               why="silence")
    write_link(tmp_path, t=1100, conn_s=44.0, answered_s=29.0, frames=29,
               why="silence")

    payload = result_json(run_stats(tmp_path, "links"))
    assert payload["never_answered"] == 1
    assert payload["answered_s"]["min"] == 0.0


def test_links_returns_the_longest_windows_verbatim(tmp_path):
    for index, answered in enumerate((5.0, 60.0, 29.0, 1988.0)):
        write_link(tmp_path, t=1000 + index, conn_s=answered + 15.0,
                   answered_s=answered, frames=int(answered), why="silence")

    payload = result_json(run_stats(tmp_path, "links", "--top", "2"))

    assert [row["answered_s"] for row in payload["longest"]] == [1988.0, 60.0]
    # Whole rows, not just the number: the frame count against conn_s is what
    # separates "answered steadily" from "answered twice, far apart".
    assert payload["longest"][0]["frames"] == 1988
    assert payload["longest"][0]["conn_s"] == 2003.0


def test_links_filters_by_since_and_until(tmp_path):
    write_link(tmp_path, t=epoch(2026, 8, 26, 12), conn_s=2003.0,
               answered_s=1988.0, frames=1900, why="shutdown")
    write_link(tmp_path, t=epoch(2026, 8, 27, 12), conn_s=44.0,
               answered_s=29.0, frames=29, why="silence")

    payload = result_json(
        run_stats(tmp_path, "links", "--since", "2026-08-27T00:00:00"))
    assert payload["n"] == 1
    assert payload["answered_s"]["max"] == 29.0


def test_links_costs_a_torn_row_only_itself(tmp_path):
    # Stage 3 §2.2/§2.3 never prune these durable files, so a hand-edited or
    # half-written row must not cost every later query.
    write_link(tmp_path, t=1000, conn_s=44.0, answered_s=29.0, frames=29,
               why="silence")
    (tmp_path / "links.jsonl").open("a", encoding="utf-8").write(
        '{"t":1001,"conn_s":44.0,"answered_s":"twenty-nine"}\n')
    write_link(tmp_path, t=1002, conn_s=44.0, answered_s=30.0, frames=30,
               why="silence")

    payload = result_json(run_stats(tmp_path, "links"))
    assert payload["n"] == 2
    assert payload["skipped_rows"] == 1


def test_links_rejects_a_top_of_zero(tmp_path):
    payload = result_json(run_stats(tmp_path, "links", "--top", "0"),
                          expected_code=1)
    assert "error" in payload
