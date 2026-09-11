#!/usr/bin/python3
"""Drive the real QML through harness IPC; all assertions stay outside the UI."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from walkingpad.frames import Status
from walkingpad.store import Store


class CheckFailure(RuntimeError):
    pass


class ComponentFailure(CheckFailure):
    pass


def require(condition, message, state=None):
    if not condition:
        suffix = "" if state is None else ": " + json.dumps(state, sort_keys=True)
        raise CheckFailure(message + suffix)


class Driver:
    def __init__(self, config, process, deadline, log_path):
        self.config = config
        self.process = process
        self.deadline = deadline
        self.log_path = log_path
        self.log_size = 0

    def check_component_errors(self):
        size = self.log_path.stat().st_size
        if size == self.log_size:
            return
        self.log_size = size
        errors = [line for line in self.log_path.read_text().splitlines()
                  if QML_ERROR.search(line)]
        if errors:
            raise ComponentFailure("unexpected QML errors: " + "\n".join(errors))

    def call(self, action, payload=None):
        self.check_component_errors()
        require(self.process.poll() is None, "Quickshell exited before checks finished")
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "behavioral checks timed out")
        response = subprocess.run(
            ["qs", "-p", str(self.config), "ipc", "call", "walkingpadHarness",
             "dispatch", action, json.dumps(payload)],
            capture_output=True, text=True, timeout=min(5, remaining), check=False,
        )
        if response.returncode:
            raise CheckFailure("harness IPC failed: " + (response.stderr or response.stdout).strip())
        try:
            result = json.loads(response.stdout)
        except ValueError as error:
            raise CheckFailure("harness returned invalid JSON: " + response.stdout.strip()) from error
        require(result.get("ok"), result.get("error", "harness action failed"))
        return result.get("value")

    def wait(self, predicate, description, startup=False):
        last = None
        while time.monotonic() < self.deadline:
            try:
                last = self.call("inspect")
                if predicate(last):
                    return last
            except CheckFailure as error:
                if isinstance(error, ComponentFailure) or not startup or self.process.poll() is not None:
                    raise
            time.sleep(0.05)
        raise CheckFailure("timed out waiting for " + description + ": " + repr(last))

    def settled(self):
        return self.wait(lambda s: not s["loading"], "history queries to finish")


def check_panel(driver):
    initial = driver.wait(lambda s: s["ready"] and s["opened"] and s["focus"] != "",
                          "popup and keyboard focus", startup=True)
    require(initial["commands"] == [], "opening the popup sent a command", initial)

    driver.call("click", "Set")
    driver.wait(lambda s: s["editing"] and s["editorFocused"], "speed editor focus")
    driver.call("type", "3.0")
    driver.call("click", "Apply speed")
    applied = driver.wait(lambda s: not s["editing"], "Apply to close the editor")
    expected = [{"cmd": "goal", "kmh": 3}]
    require(applied["commands"] == expected, "Apply must send exactly one goal", applied)
    print("PASS: Apply sends exactly one goal", flush=True)

    driver.call("click", "Set")
    driver.wait(lambda s: s["editing"] and s["editorFocused"], "reopened editor focus")
    driver.call("type", "4.0")
    driver.call("key", "Escape")
    cancelled = driver.wait(lambda s: not s["editing"], "Escape to cancel the editor")
    require(cancelled["opened"], "Escape from the editor closed the popup", cancelled)
    require(cancelled["commands"] == expected, "Escape committed a command", cancelled)
    print("PASS: Escape cancels without commanding the pad", flush=True)

    driver.call("controlCursor", 0)
    driver.call("key", "Up")
    driver.call("key", "Up")
    chart = driver.call("inspect")
    require(chart["cursorZone"] == "chart" and chart["chartBuckets"] > 0,
            "keyboard did not enter the populated chart", chart)
    driver.call("key", "Return")
    driver.call("key", "Enter")
    driver.call("key", "Space")
    driver.call("key", "Left")
    left = driver.call("inspect")
    require(left["chartCursorIndex"] == chart["chartCursorIndex"] - 1,
            "Left did not browse chart buckets", left)
    driver.call("key", "Right")
    chart = driver.call("inspect")
    require(chart["commands"] == expected,
            "Enter in the chart invoked the retained control cursor", chart)
    require(chart["cursorZone"] == "chart", "Enter moved the chart cursor zone", chart)
    print("PASS: chart Enter never commands the pad", flush=True)


def append_fixture_walk(driver):
    # The launcher creates this exact private path. Never accept an externally
    # supplied store or a path from an IPC response for write-side fixtures.
    path = driver.config.parent / "state" / "walkingpad"
    require(path.is_dir() and not path.is_symlink(), "synthetic store is missing")
    require(Path(os.environ["XDG_STATE_HOME"]) == path.parent, "fixture environment changed")
    clock = [0.0]
    errors = []
    store = Store(path, emit=errors.append, now=lambda: clock[0])
    require(store.last_seen_wall is not None and store.last_pad is not None,
            "fixture store has no final frame")
    clock[0] = store.last_seen_wall + 1
    before = dict(store.last_pad)
    for second in range(61):
        store.record(Status(
            belt="running" if second < 60 else "stopped", speed_kmh=2.5 if second < 60 else 0,
            set_speed_kmh=0, mode="manual", elapsed_s=before["elapsed_s"] + second,
            distance_km=round(before["dist_km"] + second / 1200, 2),
            steps=before["steps"] + second,
        ), None, None)
        clock[0] += 1
    store.close()
    require(not errors, "fixture append failed", errors)


def check_refresh(driver):
    state = driver.settled()
    require(state["refreshAvailable"], "history has no refresh control/API", state)
    require(state["sessionCount"] > 1, "synthetic history needs multiple sessions", state)
    # Make both Flickables overflow on a large output, so preservation cannot
    # pass by comparing an unscrollable viewport's zero with zero.
    driver.call("compact")
    driver.call("click", "sessionRow_1")
    state = driver.wait(lambda s: s["detailLoaded"] and not s["loading"], "selected session")
    driver.call("scroll", 40)
    driver.call("scrollSessions", 40)
    before = driver.call("inspect")
    require(before["sessionScroll"] > 0 and before["paneScroll"] > 0,
            "refresh check needs nonzero session and chart scroll offsets", before)
    driver.call("click", "refreshHistoryButton")
    refreshed = driver.settled()
    require(refreshed["epoch"] == before["epoch"] + 1, "Refresh did not run exactly once", refreshed)
    for key in ("period", "selectedSessionStart", "sessionScroll", "paneScroll"):
        require(refreshed[key] == before[key], "Refresh changed " + key, refreshed)

    driver.call("click", "All time")
    driver.settled()
    driver.call("scroll", 40)
    before = driver.call("inspect")
    require(before["paneScroll"] > 0, "period refresh check needs a nonzero chart scroll offset", before)
    driver.call("key", "F5")
    refreshed = driver.settled()
    require(refreshed["paneScroll"] == before["paneScroll"], "period refresh reset chart scroll", refreshed)

    # Extend the most recent (initially reconstructed) session through the real
    # Store, within the grouping gap. Its identity is unchanged, but rail,
    # detail and period samples must all come back with the new measured walk.
    driver.call("scrollSessions", 0)
    driver.call("click", "sessionRow_0")
    driver.wait(lambda s: s["detailLoaded"] and not s["loading"], "latest session")
    driver.call("focus", "sessionRow_1")
    before = driver.call("inspect")
    append_fixture_walk(driver)
    driver.call("key", "F5")
    refreshed = driver.settled()
    require(refreshed["epoch"] == before["epoch"] + 1, "F5 did not refresh from session focus", refreshed)
    require(refreshed["selectedSessionStart"] == before["selectedSessionStart"],
            "F5 cleared the selected session", refreshed)
    require(refreshed["focus"] == "sessionRow_1", "F5 moved focus to the selected session", refreshed)
    require(refreshed["sessionSteps"][0] > before["sessionSteps"][0], "F5 did not reread the session rail", refreshed)
    require(refreshed["detailSteps"] > before["detailSteps"], "F5 did not reread the selected detail", refreshed)
    require(refreshed["profileSteps"] > before["profileSteps"], "F5 did not reread period totals", refreshed)
    require(refreshed["detailSampleCount"] > before["detailSampleCount"], "F5 did not reread chart samples", refreshed)
    require(refreshed["commands"] == [], "history refresh commanded the pad", refreshed)
    print("PASS: Refresh and F5 preserve context and reread changed fixture data", flush=True)

    driver.call("holdVerify")
    driver.call("key", "F5")
    pending = driver.wait(lambda s: s["verifyRunning"], "held integrity query")
    driver.call("key", "F5")
    queued = driver.call("inspect")
    require(queued["verifyQueued"] and queued["epoch"] > pending["verifyEpoch"],
            "F5 did not queue an integrity rerun while the old query was active", queued)
    driver.call("releaseVerify")
    refreshed = driver.settled()
    require(refreshed["verifyEpoch"] == refreshed["epoch"] and not refreshed["verifySentinel"],
            "latest integrity result never replaced the stale query", refreshed)
    require(refreshed["commands"] == [], "coalesced refresh commanded the pad", refreshed)
    print("PASS: F5 during an active integrity query eventually accepts a fresh result", flush=True)


def check_navigation(driver):
    driver.call("click", "All time")
    before = driver.settled()
    require(before["sessionCount"] > 2, "navigation needs three session rows", before)
    driver.call("focus", "sessionRow_0")
    for key, expected in (("Up", 0), ("Down", 1), ("End", before["sessionCount"] - 1),
                          ("Down", before["sessionCount"] - 1), ("Home", 0)):
        driver.call("key", key)
        state = driver.call("inspect")
        require(state["focus"] == "sessionRow_" + str(expected),
                key + " did not focus the expected session", state)
        require(state["selectedSessionStart"] is None and not state["detailLoaded"],
                key + " selected a session while browsing", state)
        require(state["epoch"] == before["epoch"], key + " refreshed history while browsing", state)
        require(state["focusedRowVisible"], key + " left the focused row outside the scroll viewport", state)
    driver.call("key", "Down")
    selected_start = driver.call("inspect")["sessionStarts"][1]
    driver.call("key", "Return")
    selected = driver.wait(lambda s: s["detailLoaded"] and not s["loading"], "Enter session selection")
    require(selected["selectedSessionStart"] == selected_start, "Enter selected the wrong session", selected)
    driver.call("key", "Down")
    driver.call("key", "Space")
    selected = driver.wait(lambda s: s["detailLoaded"] and not s["loading"], "Space session selection")
    require(selected["selectedSessionStart"] == selected["sessionStarts"][2],
            "Space selected the wrong session", selected)
    require(selected["commands"] == [], "session navigation commanded the pad", selected)
    print("PASS: Up/Down/Home/End focus and scroll; Enter/Space select", flush=True)


def check_notices(driver):
    # Feed the ordinary response handlers. This isolates presentation states
    # without changing source or inventing a second query implementation.
    for payload, fragments in (
        ({"missing_steps": 0, "missing_dur_s": 120, "days_mismatched": 1}, ("2", "missing")),
        ({"days_unchecked": 2}, ("2", "check")),
        ({"profileSkipped": 3}, ("3", "could not be read", "incomplete")),
        ({"sessionsSkipped": 4}, ("4", "could not be read", "incomplete")),
        ({"detailSkipped": 5}, ("5", "could not be read", "incomplete")),
        ({"skipped_rows": 6}, ("6", "could not be read", "incomplete")),
        ({"profileSkipped": 3, "sessionsSkipped": 3, "skipped_rows": 3},
         ("at least 3", "could not be read")),
    ):
        driver.call("notice", payload)
        state = driver.call("inspect")
        note = state["integrityNote"].lower()
        require(all(fragment in note for fragment in fragments),
                "completeness notice omitted a state", {"input": payload, "note": note})
        require(state["noticeVisible"], "completeness notice is not visible in the UI", state)
        require(not (payload.get("missing_dur_s") and "0 steps" in note),
                "duration-only mismatch claimed zero steps were missing", state)
        require(not (payload.get("days_unchecked") and "--apply" in note),
                "unchecked history offered a repair it cannot justify", state)
    driver.call("notice", {})
    require(driver.call("inspect")["integrityNote"] == "", "healthy history retained an integrity warning")
    print("PASS: duration-only, unchecked, skipped, and overlapping completeness notices", flush=True)


def check_window(driver, group):
    driver.wait(lambda s: s["ready"] and s["opened"] and s["focus"] != "",
                "history window and keyboard focus", startup=True)
    driver.call("click", "All time")
    driver.settled()
    if group in ("all", "refresh"):
        check_refresh(driver)
    if group in ("all", "navigation"):
        check_navigation(driver)
    if group == "notices":
        driver.call("click", "sessionRow_0")
        driver.wait(lambda s: s["detailLoaded"] and not s["loading"], "session for detail notice")
    if group in ("all", "notices"):
        check_notices(driver)


QML_ERROR = re.compile(
    r"(?:ReferenceError|TypeError|SyntaxError|RangeError):|"
    r"(?:Unable|Cannot) (?:to )?assign|Binding loop|is not a type|"
    r"is not installed|Failed to load|Error loading|harness: FAIL"
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("target", choices=("window", "panel"))
    parser.add_argument("--group", choices=("all", "refresh", "navigation", "notices"), default="all")
    parser.add_argument("--timeout", type=float, default=40)
    args = parser.parse_args()
    log_path = args.config.parent / "checks.log"
    failure = None
    with log_path.open("w+") as log:
        process = subprocess.Popen(["qs", "-p", str(args.config)], stdout=log, stderr=subprocess.STDOUT)
        try:
            driver = Driver(args.config, process, time.monotonic() + args.timeout, log_path)
            if args.target == "panel":
                check_panel(driver)
            else:
                check_window(driver, args.group)
        except (CheckFailure, subprocess.TimeoutExpired, OSError) as error:
            failure = str(error)
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.flush()
            log.seek(0)
            output = log.read()
    errors = [line for line in output.splitlines() if QML_ERROR.search(line)]
    if errors and failure is None:
        failure = "unexpected QML errors: " + "\n".join(errors)
    if failure is not None:
        print("harness: FAIL — " + failure, file=sys.stderr)
        print(output, file=sys.stderr)
        return 1
    print("harness: PASS — " + args.target + " behavioral checks", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
