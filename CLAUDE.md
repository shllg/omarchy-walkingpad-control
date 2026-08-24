# omarchy-walkingpad

An Omarchy bar plugin that controls a KingSmith WalkingPad over BLE, with no vendor
app, no account, and no network access. Destined for its own marketplace listing.

**Read `docs/design-spec.md` before changing anything.** `docs/protocol-evidence.md`
records what was measured on real hardware and why. Stage 1 (the bridge) is
complete — see `docs/plans/2026-08-24-bridge-stage1.md`. Stage 2 is the QML plugin.

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

5. **Only ONE BLE connection at a time.** The phone app and this plugin are mutually
   exclusive. "In use by another device" is a state to surface, not a fault to fix.

6. **`belt == 1` means running**, not "starting". An earlier guess said otherwise;
   the advancing step counter disproved it.

7. **Never write to `f000ffc0-0451-4000-b000-000000000000`.** It is an unauthenticated
   TI-style OTA service. A bad image bricks the control board.

8. **`belt == 4` is the awake-and-idle state**, and it is what the pad reports for
   most of the time it is reachable at all. Mapped as `"idle"`. `belt == 0` is
   documented upstream as "stopped" but has never been observed here — do not
   assume the two are interchangeable. Only `"running"` means the belt is moving;
   gate on that, never on the absence of some other value.

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

13. **In `mode == standby` the pad ignores every motion command**, and the BLE
    mode-switch opcode too. Only the panel's **M** button leaves standby. Tell the
    user that by name; do not retry.

14. **`belt` 9/8/7 is the 3-2-1 startup countdown**, not a fault. `belt == 0` is
    stopped, confirmed by observation.

15. **Byte [14] is the last BLE-commanded speed, not a target.** It reads 0.0 when
    the speed was set at the panel, while the belt runs. Never render it as a
    target. A stale frozen status snapshot can also survive an abrupt link loss —
    do not conclude a command failed from reported speed alone.

16. ⚠⚠ **UNRESOLVED: a STOP sent to a stopped belt may START it.** Seen twice,
    never tested deliberately. Until settled, "stop is always permitted" is
    PROVISIONAL. The belt does NOT auto-start on load — that claim was written
    here in error on 2026-08-24 and retracted; it starts only from a command, the
    remote, or auto mode. Never infer belt state from "we did not command it".

17. **Never auto-retry a command on a timer.** A retry loop fights the auto-start
    above and produces an endless countdown-cancel-countdown cycle, which is
    exactly how that behaviour was discovered. One command per user action.

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
- **Safety is not optional.** Stop is always permitted, including on a stale link.
  Everything else is refused without a recent status frame. Speed changes clamp to
  one step per command — a person may be standing on the belt.
- `bluetoothctl` hangs without `--timeout N` and a closed stdin; it still enters its
  REPL otherwise.

## Testing

`python -m pytest tests/ -v` runs the whole suite with **no hardware** — the codec
is tested against real captured frames in `fixtures/`, and the link watchdog
against a fake transport. `pytest` is a dev-only dependency (`python-pytest`); the
bridge itself does not need it.

Only `docs/smoke-test.md` moves a belt, and it requires the operator to be standing
clear. It also carries the open Q2 cold-start procedure.
