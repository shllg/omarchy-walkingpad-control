# WalkingPad Control

An Omarchy bar plugin that controls a KingSmith WalkingPad over BLE, with no vendor
app, no account, and no network access. Destined for its own marketplace listing.

**Read `docs/design-spec.md` before changing anything.** `docs/protocol-evidence.md`
records what was measured on real hardware and why.

Stages 1 to 3.1 are all shipped: the Python bridge, the QML bar icon and popup
panel, the durable history store with its query tool, and the analysis window.
The plans under `docs/plans/` were reduced on 2026-08-26 to the decisions their
section numbers are cited by and the work that is still open — they are not a
progress tracker, and completed steps are not kept there.

✅ **The two-part start (§4b) and the live goal (§4d) were signed off against
hardware on 2026-08-27**, with the measurements in `docs/smoke-test.md`. §4c, the
panel on hardware, is still unsigned: checks 4 and 6 remain, and both need an
operator on a moving belt rather than only at the popup. That file is the live
tracker for every open hardware question.

## Non-obvious facts. Do not re-derive these, and do not "fix" them.

Every one was MEASURED on a WalkingPad C2, firmware 6.1.2, on 2026-08-24. Each has
already caused, or nearly caused, a wrong design decision.

1. **Status is poll-driven, not push.** With FE01 subscribed, four seconds of silence
   produced zero notifications. Every frame the pad has ever sent was a reply to a
   `F7 A2 00 00 A2 FD` query. The poll loop is load-bearing. Removing it as
   "redundant with notifications" produces a permanently frozen UI that looks like an
   idle pad rather than a bug.

2. **Reported speed is ACTUAL, not target, and the pad ramps ~0.25 km/h per second.**
   There is no ACK anywhere in the protocol. `commanded_speed` is tracked separately
   on purpose. Echoing only the frame makes every click look like it failed for two
   seconds.

3. **The pad sleeps and stops advertising entirely.** Absence is the NORMAL resting
   state, not an error. BLE cannot wake it — that needs the panel or the remote.
   Never retry-storm, never toast on failure to find it.

4. **Always scan before connecting.** `BleakClient(<address>)` needs the device
   already in BlueZ's cache; without a scan it fails with "device was not found",
   which is indistinguishable from a contention failure. This produced a confidently
   wrong conclusion once already. Use `BleakScanner.find_device_by_filter`.

5. **One BLE connection at a time — and BlueZ owns it.** The phone app and this
   plugin are mutually exclusive: a client on another machine is refused, and
   "in use by another device" is a state to surface, not a fault to fix.
   ⚠ **A second client on THIS host is not refused.** Measured 2026-08-27: two
   local D-Bus clients share the one link, both poll it, and BlueZ broadcasts
   every notification to both. So two bridges do not fail loudly — they double
   the poll rate the pad sees and interleave two cadences into what looks like
   duplication. That is why the service singleton matters, and it is a stronger
   reason than "the second one would be refused", which is what this fact used
   to say. `docs/protocol-evidence.md`, "Two bridges do not fail. They silently
   share the link."

6. **`belt == 1` means running**, not "starting". An earlier guess said otherwise;
   the advancing step counter disproved it.

7. **Never write to `f000ffc0-0451-4000-b000-000000000000`.** It is an unauthenticated
   TI-style OTA service. A bad image bricks the control board.

8. **`belt == 4` is the awake-and-idle state**, and it is what the pad reports for
   most of the time it is reachable at all. Mapped as `"idle"`. `belt == 0` is
   `"stopped"`, confirmed by observation — see fact 14. Only `"running"` means
   the belt is moving; gate on that, never on the absence of some other value.

9. **BlueZ's `Connected` property is NOT proof of a working link.** After an
   ungraceful kill it kept reporting `Connected=True` for a radio link that was
   already gone: `Connect` succeeded, services resolved, `StartNotify` succeeded
   and every write returned without error, into nothing. A recent status frame is
   the only real evidence — hence the 15 s watchdog. Corollary: **a connected pad
   does not advertise**, so a stranded link can never be rediscovered, which is
   why `find()` adopts an existing one.

10. **Python signal handlers do not run under a GLib main loop.**
    `g_main_loop_run()` blocks in C, so Ctrl-C did nothing at all and the bridge
    polled straight through it. Use `GLib.unix_signal_add` sources for SIGINT and
    SIGTERM. Note it also replaces Python's SIGINT handler, so no
    `KeyboardInterrupt` is raised anywhere afterwards.

11. **START is required; setting a speed never starts a stopped belt.** The speed
    command is stored (byte [14]) and the belt stays put. Answered on hardware.

12. **The pad starts at ~3.5 km/h regardless of its stored speed**, and ignores
    byte [14] when starting. The 0.5 km/h per-command clamp does NOT cover this —
    it governs changes, not the pad's own start. Starting is two steps: `start`,
    then the speed, inside the ~2 s window at `belt=running, speed=0.0`.
    ✅ Confirmed end to end on 2026-08-27, three runs (smoke-test §4b): 4.00 s of
    3-2-1 countdown, the follow-up speed landing 70 ms after the first `running`
    frame with the pad already at 2.5 km/h and climbing, and the belt settling at
    the commanded 2.0. `START_SPEED_WINDOW_S = 8.0` is measured adequate.

13. **In `mode == standby` the pad ignores every motion command**, and the BLE
    mode-switch opcode too. Only the panel's **M** button leaves standby. Tell the
    user that by name; do not retry.

14. **`belt` 9/8/7 is the 3-2-1 startup countdown**, not a fault. `belt == 0` is
    stopped, confirmed by observation.

15. **Byte [14] is the last BLE-commanded speed, not a target.** It reads 0.0 when
    the speed was set at the panel, while the belt runs. Never render it as a
    target. A stale frozen status snapshot can also survive an abrupt link loss —
    do not conclude a command failed from reported speed alone.

16. ⚠⚠ **`F7 A2 04 00` IS A TOGGLE: it STARTS a stopped belt.** Confirmed by
    controlled test on 2026-08-25. Stopping is therefore gated on recent evidence
    the belt is moving (`Safety.may_stop`); without it, refuse and point the user
    at the panel. "Stop is always permitted on a stale link" was written here in
    error and is retracted — it was the most dangerous line in the design. The
    belt does NOT auto-start on load; that claim was also wrong and is retracted.

17. **Never auto-retry a command on a timer.** A retry loop fights the stop
    opcode's own toggle behaviour and produces an endless countdown-cancel-countdown
    cycle, which is exactly how that behaviour was discovered. One command per
    user action.

18. **`stop` is never throttled**, unlike `speed` and `start`. There is no ACK, so
    a dropped write is invisible and a second press is the user's only recovery.
    Swallowing it leaves someone on a moving belt; a spammed stop is only noisy.

## Hard constraints

- **System python only** (`#!/usr/bin/python3`) and **no third-party runtime
  dependency at all.** BLE goes through `gi.repository.Gio` to BlueZ; both
  `python-gobject` and `bluez` are in `omarchy-base.packages`. A plugin manifest
  has no dependency field and no install hook, so any other package would become a
  manual `pacman -S` the user must run before the plugin works. Never pip. Ruby,
  node and bun here are mise-managed, and a mise config wipe would unmount the
  bridge. See ADRs D-2 and D-3.
- **The QML widget owns no colour.** Build on `qs.Commons` (Color, Style) and
  `qs.Ui`; never hardcode a colour, size or radius, not even as a fallback. Omarchy
  themes swap live and copying a value at construction breaks that. See §5.5.
- **No hardcoded MAC.** Discovery matches the advertised FE00 service UUID first,
  falling back to a `KS-` name prefix.
- **No real device address in tracked content, ever.** Docs use the `<pad-addr>`
  and `<adapter-addr>` placeholders; the real values live in
  `docs/local-device.md`, which is gitignored. This laptop's adapter address was
  committed and pushed public on 2026-08-24, then scrubbed from all 21 commits
  with `git-filter-repo`. A force-push does NOT retract it — the pre-rewrite
  SHAs still answered 200 on GitHub afterwards, so the cleanup needed a support
  request on top. `tests/test_no_pii.py` guards this; it fails the suite on any
  MAC-shaped string in a tracked file. Treat that failure as a stop, not a lint.
- **Plain constructs only.** No metaprogramming, no clever async beyond the poll loop.
  Comments explain *why* and cite the measured fact they rest on (ADR D-1).
- **Safety is not optional.** As fact 16 records, stop is gated on recent evidence
  that the belt is moving; without it, refuse and point the user at the panel.
  Every motion command requires a recent status frame. Speed changes clamp to one
  step per command — a person may be standing on the belt.
- `bluetoothctl` hangs without `--timeout N` and a closed stdin; it still enters its
  REPL otherwise.

## Testing

`python -m pytest tests/ -v` runs the whole suite with **no hardware** — the codec
is tested against real captured frames in `fixtures/`, the link watchdog against
a fake transport, and the two-part start (the most dangerous logic here) against
a recording transport in `tests/test_start_sequence.py`. That sequencing lives in
Python precisely so it can be tested; do not move it into QML, which cannot be.

The QML can also be exercised without hardware. `bin/walkingpad-bridge --replay
fixtures/c2-fw6.1.2.jsonl` plays captured frames through the transport seam, and
**`bin/walkingpad-harness` renders either entry point** against a synthetic store
that `bin/walkingpad-fixture-store` generates by driving the real `Store`, so the
fixture is what the bridge actually writes rather than hand-rolled rows.
⚠ Its `--offscreen` mode proves only that the QML parses and that types resolve:
`PanelWindow` needs the Wayland backend, so offscreen the entry point fails to
instantiate and nothing inside it runs. Data and bindings need a real display. `pytest` is a dev-only dependency (`python-pytest`); the
bridge itself does not need it.

⚠ **A QML edit does NOT reach the running bar until the shell is restarted.**
`omarchy-launch-shell` starts Quickshell with `QS_DISABLE_FILE_WATCHER=1`, and
its own comment says so: *"Quickshell's own reloading is off; Omarchy restarts
the shell deliberately."* So even with the plugin directory symlinked to a
checkout, the bar keeps running whatever QML it loaded at startup. Run
`omarchy restart shell`. The **bridge** is different — it is a child process, so
killing it is enough to pick up a Python change, and the service respawns it in
seconds. Measured 2026-08-28, after a QML fix was tested against a shell that had
been running since two days before it was written and the result was read as a
failure of the fix.

Only `docs/smoke-test.md` moves a belt, and it requires the operator to be standing
clear. It is also the live tracker for every open hardware question.

## Plugin boundary

This repository is the installed plugin. `omarchy plugin add` clones it into the
user's plugins directory, so everything committed here ships to users and is
scanned before installation. No symlink may be committed: one tracked symlink
makes the validator reject the whole plugin for every user. Local symlinks that
are never committed cannot reach anyone, which is why the runtime skill trees
below are gitignored rather than banned.

No new runtime dependency. The manifest has no dependency field and the install
path has no hook (ADR D-3); deliberately, the bridge imports only the standard
library and `gi.repository` Gio/GLib. `bin/walkingpad-bridge` stays executable with
its `#!/usr/bin/python3` shebang.

The plugin runtime has no daemon, systemd unit, sudo or polkit use, installer,
bundled binary, or network access. No personal or machine-specific content belongs
in tracked files.

## Repository skills

The skills themselves are regular files under `.agents/skills/`, which is the
only tracked copy. `.claude/skills/` and `.codex/skills/` are symlink trees
pointing at them so each runtime discovers them where it looks; both are
gitignored, because a committed symlink would make the plugin uninstallable.
After cloning, run `bin/link-skills` once to recreate the links and activate this
repository's git hooks; later checkouts and merges refresh the links automatically.

- `walkingpad-work` — implement changes within the measured safety boundary.
- `omarchy-plugin` — apply the verified plugin, manifest and loader contract.
- `walkingpad-review` — review correctness without editing.
- `walkingpad-security-review` — review the repository-specific threat model.
- `walkingpad-next` — rank the next useful work by evidence and hardware needs.
