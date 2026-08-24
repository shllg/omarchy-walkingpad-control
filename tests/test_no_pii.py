import re
import subprocess

# tests/test_no_pii.py
#
# On 2026-08-24 this laptop's BlueZ adapter address and the pad's address were
# committed in README.md and docs/, pushed public, and had to be scrubbed from
# all 21 commits with git-filter-repo. The force-push did NOT retract them: the
# pre-rewrite SHAs still answered 200 on GitHub afterwards. That is why the
# guard is a test and not a note — the cost of noticing late is a support
# ticket, not an amend. Real values live in the gitignored docs/local-device.md.

ADDRESS = re.compile(rb"\b([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")


def _repo_root():
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, check=True, text=True,
    )
    return out.stdout.strip()


def _tracked_files(root):
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root, capture_output=True, check=True,
    ).stdout
    return [p.decode() for p in out.split(b"\0") if p]


def test_no_device_address_in_tracked_files():
    root = _repo_root()
    offenders = []
    for rel in _tracked_files(root):
        try:
            with open(f"{root}/{rel}", "rb") as fh:
                body = fh.read()
        except OSError:
            continue
        for match in ADDRESS.finditer(body):
            # Report a prefix only; the point is to locate it, not reprint it.
            shown = match.group(0).decode()[:5]
            offenders.append(f"{rel}: {shown}...")

    assert not offenders, (
        "Real device address found in tracked content. Use the <pad-addr> / "
        "<adapter-addr> placeholders and keep the real value in the untracked "
        "docs/local-device.md:\n  " + "\n  ".join(offenders)
    )
