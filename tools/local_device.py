#!/usr/bin/python3
"""
Machine-local device addresses, read at runtime from a file git never sees.

WHY THIS EXISTS
---------------
On 2026-08-24 this laptop's adapter address and the pad's address were committed
in README.md and docs/, pushed public, and had to be scrubbed from all 21 commits
with git-filter-repo. The force-push did NOT retract them -- the pre-rewrite SHAs
still answered 200 afterwards. A hardcoded address in a tracked file is exactly
how that happened, so nothing tracked holds one: tools read the value at runtime
instead, and tests/test_no_pii.py fails the build the moment one reappears.

The bridge itself never needs an address. Discovery matches the FE00 service UUID
first and falls back to the KS- name prefix, and a hardcoded MAC stays forbidden
(CLAUDE.md, hard constraints). This is only for hand-run diagnostics, which is
why it lives under tools/ rather than in the walkingpad package.
"""
import os
import re

LOCAL_DEVICE_FILE = os.path.join("docs", "local-device.md")

# The gitignored file is prose, not config: it explains the placeholders and then
# gives each real value on a bullet line keyed by the placeholder it replaces.
# Keying off the placeholder rather than line order means the file stays readable
# and can be reordered without breaking this.
_PLACEHOLDERS = {"pad": "<pad-addr>", "adapter": "<adapter-addr>"}
_ADDRESS = re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b")

_ENV_VARS = {"pad": "WALKINGPAD_PAD_ADDR", "adapter": "WALKINGPAD_ADAPTER_ADDR"}


class MissingAddress(Exception):
    """Raised with instructions rather than a bare KeyError, because the usual
    cause is a fresh clone where the untracked file simply does not exist yet."""


def _repo_root() -> str:
    # tools/ sits at the repo root. Derived from __file__ rather than shelling
    # out to git so this still works from a copied checkout.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _explain(kind: str, path: str) -> str:
    return (
        "no %s address available.\n"
        "  Set %s, or create %s containing a line like:\n"
        "      - `%s` — <what it is>: `XX:XX:XX:XX:XX:XX`\n"
        "  That file is gitignored and must stay that way -- see tests/test_no_pii.py."
        % (kind, _ENV_VARS[kind], path, _PLACEHOLDERS[kind])
    )


def _lookup(kind: str) -> str:
    override = os.environ.get(_ENV_VARS[kind])
    if override:
        return override.strip()

    path = os.path.join(_repo_root(), LOCAL_DEVICE_FILE)
    try:
        with open(path, "r") as handle:
            lines = handle.readlines()
    except OSError:
        raise MissingAddress(_explain(kind, path))

    for line in lines:
        if _PLACEHOLDERS[kind] in line:
            found = _ADDRESS.search(line)
            if found:
                return found.group(0)
    raise MissingAddress(_explain(kind, path))


def pad_address() -> str:
    """The WalkingPad's BLE address, for hand-run diagnostics only."""
    return _lookup("pad")


def adapter_address() -> str:
    """This machine's BlueZ adapter address."""
    return _lookup("adapter")
