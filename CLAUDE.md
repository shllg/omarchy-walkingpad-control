# omarchy-walkingpad

An Omarchy bar plugin that controls a KingSmith WalkingPad over BLE, with no vendor
app, no account, and no network access. Destined for its own marketplace listing.

**Read `docs/design-spec.md` before changing anything.** `docs/protocol-evidence.md`
records what was measured on real hardware and why. Current work is
`docs/plans/2026-08-24-bridge-stage1.md`.

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

## Hard constraints

- **System python only** (`#!/usr/bin/python3`) with `pacman -S python-bleak`.
  Ruby, node and bun on this machine are mise-managed, and a mise config wipe would
  unmount the bridge. See ADR D-2.
- **No hardcoded MAC.** Discovery matches the advertised FE00 service UUID first,
  falling back to a `KS-` name prefix.
- **Plain constructs only.** No metaprogramming, no clever async beyond the poll loop.
  Comments explain *why* and cite the measured fact they rest on (ADR D-1).
- **Safety is not optional.** Stop is always permitted, including on a stale link.
  Everything else is refused without a recent status frame. Speed changes clamp to
  one step per command — a person may be standing on the belt.
- `bluetoothctl` hangs without `--timeout N` and a closed stdin; it still enters its
  REPL otherwise.

## Testing

Tasks 1–3 of the current plan need **no hardware** — the frame codec is tested
against four real captured frames in `fixtures/`. Only the smoke test moves a belt,
and it requires the operator to be standing clear.
