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


def run_stats(store_dir, *arguments):
    return subprocess.run(
        [sys.executable, STATS, *arguments, "--store", str(store_dir)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "TZ": "UTC"},
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
    ]

    for result in results:
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(result.stdout.strip().splitlines()) == 1
        assert isinstance(json.loads(result.stdout), dict)
