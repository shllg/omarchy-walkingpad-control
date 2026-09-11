"""Check-mode safety guards are testable without opening a display."""

from pathlib import Path
import json
import os
import subprocess

import pytest


HARNESS = Path(__file__).resolve().parents[1] / "bin" / "walkingpad-harness"
CHECKER = Path(__file__).resolve().parent / "ui" / "check.py"


@pytest.mark.parametrize("argument", ["--offscreen", "--real-store"])
def test_check_mode_refuses_non_behavioral_or_private_inputs(argument):
    result = subprocess.run([str(HARNESS), "--check", argument],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "--check cannot be combined with " + argument in result.stderr


def test_check_mode_refuses_an_external_store(tmp_path):
    result = subprocess.run([str(HARNESS), "--check", "--store", str(tmp_path)],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "--check always creates its own synthetic store" in result.stderr


def run_checker(tmp_path, state, log_line=""):
    # The code under test supervises Quickshell. A disposable process implements
    # only its IPC response and log channels, so failure handling needs no GUI.
    qs = tmp_path / "qs"
    qs.write_text(
        "#!/usr/bin/python3\n"
        "import sys,time\n"
        "if 'ipc' in sys.argv:\n"
        "    print(" + repr(json.dumps({"ok": True, "value": state})) + ")\n"
        "    raise SystemExit(0)\n"
        "print(" + repr(log_line) + ", flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    qs.chmod(0o700)
    return subprocess.run(
        ["/usr/bin/python3", str(CHECKER), str(tmp_path / "harness.qml"),
         "panel", "--timeout", "0.3"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, timeout=5, check=False,
    )


def test_checker_timeout_is_a_failure(tmp_path):
    result = run_checker(tmp_path, {"ready": False})
    assert result.returncode == 1
    assert "timed out" in result.stderr


def test_checker_component_error_is_a_failure_before_the_deadline(tmp_path):
    result = run_checker(tmp_path, {"ready": False},
                         'WARN scene: fixture.qml: Cannot assign to non-existent property "onEndPressed"')
    assert result.returncode == 1
    assert "unexpected QML errors" in result.stderr
    assert "timed out" not in result.stderr


def test_checker_assertion_is_a_failure(tmp_path):
    result = run_checker(tmp_path, {"ready": True, "opened": True,
                                   "focus": "popup", "commands": [{"cmd": "unexpected"}]})
    assert result.returncode == 1
    assert "opening the popup sent a command" in result.stderr
