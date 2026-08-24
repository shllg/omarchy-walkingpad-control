# omarchy-walkingpad — design spec

Status: draft for review · 2026-08-24
Target: standalone Omarchy plugin, own git repo, marketplace submission.

---

## 1. Purpose

Control a KingSmith WalkingPad from the Omarchy bar over BLE, with no vendor app,
no account and no network access of any kind.

Non-goals, explicitly:
- No cloud, no telemetry, no phone-home. Ever.
- No firmware/OTA features (see §3.3).
- Not a fitness tracker. Session history beyond the pad's own counters is out of scope for v1.

## 2. Verified device facts

All measured on the author's C2 (fw 6.1.2), 2026-08-24. Cited because the design
depends on them and two contradicted the public documentation.

| Fact | Value |
|---|---|
| Advertised name | `KS-BLC2` |
| BLE SoC | BEKEN `BK-BLE-1.0`, fw 6.1.2 |
| Service | `0000fe00` (KingSmith "WiLink") |
| Notify char | `0000fe01` |
| Write char | `0000fe02`, **write-without-response** |
| FTMS `0x1826` | **absent** on C2 |
| Pairing | none required; control is unauthenticated |

Frame format — `F7 <body…> <crc> FD`, `crc = sum(body) % 256`.

| Command | Bytes |
|---|---|
| status query | `F7 A2 00 00 A2 FD` |
| set speed | `F7 A2 01 <tenths_kmh> <crc> FD` |
| set mode | `F7 A2 02 <mode> <crc> FD` |
| start belt | `F7 A2 04 01 <crc> FD` |

Status frame — 20 bytes, header `F8 A2`:

```
[2] belt (1 = RUNNING)   [3] speed, tenths km/h   [4] mode (1 = manual)
[5:8] time s   [8:11] distance, 10 m units   [11:14] steps
[14:18] undocumented ([16] constant 0x02)   [18] crc   [19] FD
```

### 2.1 Two measured facts that drive the whole design

**(a) Status is poll-driven, not push.** Subscribing to FE01 and waiting yields
nothing. Every frame is a direct reply to a status query — one query, one frame.
A conventional event-driven QML implementation renders a permanently frozen bar.
→ *The bridge must run its own poll loop (§4.2).*

**(b) The pad ramps; reported speed is ACTUAL, not TARGET.** Measured ~0.25 km/h/s:
a 2.5→2.0 command passes through 2.3 over ~2 s. There is no ACK and no target field
anywhere in the protocol.
→ *The service must track commanded and current speed separately, render transitions,
and coalesce rapid input (§5.2). Naively echoing the frame makes every click look
like it failed.*

## 3. Architecture

Three components, one process tree, no systemd unit, no root, no compiled binary.

```
  ┌─ Quickshell (the shell process) ───────────────────────────┐
  │                                                             │
  │  WalkingpadWidget.qml   × N monitors   (pure view, no state)│
  │            │ reads                                          │
  │  WalkingpadService.qml  pragma Singleton  ◄── IpcHandler ◄──┼── qs ipc call
  │            │ owns exactly one                               │
  │         Process ──stdio, JSON lines──┐                      │
  └──────────────────────────────────────┼──────────────────────┘
                                         ▼
                            walkingpad-bridge  (Python + bleak)
                                         │ GATT
                                         ▼
                                   FE01 / FE02
```

### 3.1 Why the singleton is mandatory, not stylistic

`BarPanel` is instantiated **once per screen**. The author's machine has three.
If the widget spawned the bridge, three bridges would contend for the pad's
**single** BLE connection slot. The bridge is therefore owned by a
`pragma Singleton` service and the widget is a stateless view.

### 3.2 Why the bridge is a child process, not a daemon

Lifecycle is free: it dies with the shell, needs no unit file, no socket cleanup,
no PID file. It also keeps the plugin clear of the marketplace disqualifiers
(binaries, sudoers/polkit, daemons).

### 3.3 Deliberately not implemented

The pad exposes an unauthenticated TI-style OTA service
(`f000ffc0-0451-4000-b000-…`, chars `ffc1`/`ffc2`). The plugin must never write to
it. A bad image bricks the control board, and shipping a firmware-write primitive
in a bar widget is indefensible. Documented in the README as a device property.

## 4. The bridge

`bin/walkingpad-bridge` — Python 3.10+, `bleak`. A pure protocol codec with no
knowledge of Quickshell.

### 4.1 Interface

stdin ← one JSON object per line:
`{"cmd":"speed","kmh":3.0}` · `{"cmd":"start"}` · `{"cmd":"stop"}` ·
`{"cmd":"mode","mode":"manual"}` · `{"cmd":"connect","addr":"…"}` · `{"cmd":"disconnect"}`

stdout → one JSON object per line:
```json
{"t":"status","belt":"running","speed":2.5,"target":2.5,"mode":"manual",
 "time":1014,"dist_km":0.70,"steps":1321,"ts":1756049000.1}
{"t":"link","state":"connected","addr":"04:57:…","name":"KS-BLC2"}
{"t":"error","code":"not_found","msg":"no pad in range"}
```

Line-delimited JSON is chosen because `Quickshell.Io.SplitParser` consumes it directly.

### 4.2 Poll loop

1 Hz while connected, per §2.1(a). Backs off to 0.2 Hz when the belt has been
stopped for >60 s, to keep the radio quiet. Every status frame is emitted, so the
service never has to poll the bridge.

### 4.3 Protocol adapter seam

`protocols/wilink.py` implements the verified C2 protocol.
`protocols/ftms.py` is a stub for standard `0x1826` machines.
Selection is by service discovery: FE00 → WiLink, 0x1826 → FTMS.

This exists because the plugin will be installed on models neither the author nor
any reviewer can test. Untestable code is clearly marked as such; FTMS ships
disabled behind a flag until someone with the hardware confirms it.

### 4.4 Standalone testability

The bridge runs without Quickshell:
```
echo '{"cmd":"speed","kmh":2.0}' | walkingpad-bridge --once
walkingpad-bridge --replay fixtures/c2-fw6.1.2.jsonl    # no hardware needed
```
`--replay` feeds captured frames so the QML layer and CI can run pad-free.

## 5. The QML plugin

### 5.1 `WalkingpadService.qml` (pragma Singleton)

Owns the `Process`, parses stdout via `SplitParser`, and exposes:

```
readonly property string  linkState      // idle | scanning | connecting | connected | error
readonly property string  beltState      // stopped | running | unknown
readonly property real    currentSpeed   // from frames — ACTUAL
readonly property real    commandedSpeed // what we last asked for
readonly property bool    ramping        // currentSpeed !== commandedSpeed
readonly property int     steps
readonly property real    distanceKm
readonly property int     elapsedSec
```

Plus an `IpcHandler` (target `walkingpad`) exposing `speedUp`, `speedDown`,
`stop`, `start`, `toggle`, `status` — so Hyprland keybinds are one-liners
(`qs ipc call walkingpad speedUp`) with no extra binary.

### 5.2 Input coalescing — required, not an optimisation

Because of §2.1(b), rapid clicks must not become rapid writes.

- Clicks mutate `commandedSpeed` immediately and locally.
- A 250 ms debounce timer sends **one** speed command for the settled value.
- While `ramping`, the bar renders `2.0→2.5` so the input is visibly acknowledged
  even though the pad has not reached the target.

### 5.3 `WalkingpadWidget.qml` (bar-widget entry point)

Stateless view. Renders glyph + speed, or step count when stopped.

| Interaction | Action | Rationale |
|---|---|---|
| Left click | open the plugin menu | never starts the belt (§6) |
| Right click | **STOP** | panic action, always available, one gesture |
| Scroll | ±0.1 km/h, debounced | fine control without a menu |

### 5.4 Discovery and adoption

Uses `Quickshell.Bluetooth`, which supplies device management (`connect`,
`disconnect`, `pair`, `forget`, `trusted`, `dbusPath`) but **no GATT** — hence the
split with the bridge.

- **New device:** menu → "Find walking pad" → `adapter.discovering = true`, list
  candidates matching FE00 in the advertisement (preferred) or a `KS-` name prefix,
  user picks one, address stored in plugin config, `trusted = true`.
- **Known device afterwards:** on shell start and on `device.connected` transitions,
  if a stored address is present and in range, connect automatically and emit a
  notification ("Walking pad ready"). No MAC is ever hardcoded.
- Matching on the advertised **service UUID** rather than the name is preferred
  because it identifies capability rather than a vendor naming convention.

## 6. Safety model

The pad accepts unauthenticated commands and may have a person standing on it.

1. **Left click never starts the belt.** Starting is an explicit menu action or a
   deliberate keybind. Discovered the hard way: the author was mid-walk during
   protocol probing.
2. **Right click always means stop**, at every level of the UI.
3. **Per-action speed delta is clamped** (default 0.5 km/h, configurable). One
   gesture cannot take a walker from 2.5 to 6.0 km/h.
4. **No blind writes.** If no status frame has arrived within 3 s, the service
   refuses to issue motion commands and shows a stale-link state.
5. Absolute speed is clamped to a configurable `maxSpeed` (default 6.0 km/h).

## 7. Configuration

Plugin config (Omarchy plugin settings), all optional:
`address` · `maxSpeed` (6.0) · `speedStep` (0.5) · `units` (kmh|mph) ·
`autoConnect` (true) · `notifyOnConnect` (true) · `idleDisconnectSec` (0 = never)

## 8. Error handling

| Condition | Behaviour |
|---|---|
| Pad not in range | `linkState = idle`, dormant glyph, no retry storm (30 s backoff) |
| Connection lost mid-walk | notify, retry 3× with backoff, keep last known stats visible but greyed |
| Slot held by phone app | surface "in use by another device" — this is the single-connection limit, not a fault |
| Bridge crashes | service restarts it, max 3× per minute, then reports a hard error |
| CRC mismatch | drop the frame, count it; >10 consecutive → treat as link failure |

## 9. Testing

- **Bridge:** unit tests over frame encode/decode against captured fixtures. CRC,
  the 20-byte layout, and the ramp semantics all have real recorded data.
- **Replay:** `--replay` drives the full QML stack with no hardware, so the widget
  is developable and CI-testable away from the pad.
- **Hardware smoke test:** documented manual checklist, explicitly requiring the
  operator to be **off the belt** for anything that writes.

## 10. Distribution

```
omarchy-walkingpad/
  manifest.json          id: walkingpad, kinds: [bar-widget], allowMultiple: false
  WalkingpadWidget.qml
  WalkingpadService.qml  pragma Singleton
  bin/walkingpad-bridge
  protocols/{wilink,ftms}.py
  fixtures/c2-fw6.1.2.jsonl
  README.md  LICENSE (MIT)
```

Marketplace submission is a PR adding the repo to `registry.json` in
`HANCORE-linux/omarchy-plugin-marketplace`. Checked 2026-08-24: 1161 sources,
**zero** treadmill or fitness plugins — this is the first.

## 11. Open questions

1. Does `keepLoaded: true` apply to `bar-widget` kind? The service must stay alive
   even if no bar shows the widget. Verify against the loader.
2. Cold start: `walkingpad-controller` reports some models need START before
   accepting a speed. Untested here — the belt was already running.
3. Child-lock interaction with remote commands: unknown.
4. Mode semantics (`0`/`1`/`2`) beyond manual = 1: only manual observed.
5. Bytes `[14:18]` of the status frame remain undecoded.
6. Whether BlueZ-level connect (via Quickshell) and bleak's connect cooperate or
   contend. Expected to cooperate — bleak is a BlueZ D-Bus client — but unverified.
   Attempted 2026-08-24; blocked because the pad had gone to sleep.
7. How long until the pad sleeps, and whether anything short of physical
   interaction wakes it. Measured: once asleep it stops advertising entirely,
   so BLE cannot reach it at all.

---

## 12. Decisions

### D-1 — All protocol logic lives in the Python bridge (2026-08-24)

**Decision.** The bridge owns transport *and* protocol. The QML service renders
what it is told and holds no WalkingPad semantics.

**Alternative rejected.** A ~120-line Python "dumb pipe" exchanging hex strings,
with frame building, CRC, decode, ramp handling and the FTMS adapter implemented
in JavaScript inside the QML service. That would have put the evolving logic in a
language the author uses more (TypeScript/Bun) and made it unit-testable with
`bun test`.

**Why the chosen option won.** One obvious place for anything protocol-related,
fewer moving parts, and no hex marshalling across a process boundary. The adapter
seam (§4.3) stays entirely inside one component.

**Consequence to manage.** The code most worth reading is now in the author's
least-used language. Mitigation is explicit and binding on the implementation:

- single-file where practical, plain constructs only — no metaprogramming, no
  clever async beyond the poll loop, no comprehension golf
- comments explain *why*, in the style of `nightlight-mode` in the os-configs repo,
  which opens with a 40-line "WHY THIS EXISTS" header
- every measured fact from §2.1 is cited inline at the code that depends on it, so
  a future reader cannot "simplify" the poll loop or the ramp handling without
  meeting the evidence first

### D-2 — Python is system python, never mise (2026-08-24)

`/usr/bin/python3` plus `pacman -S python-bleak` (`extra/python-bleak 3.0.2-1`).

Ruby, Node and Bun on this machine are all mise-managed. The os-configs CLAUDE.md
documents a real, recurring failure in which concurrent `mise use -g` wipes the
global config and unmounts every binary under a version pin. A bar widget must not
inherit that failure mode. The bridge shebang is `#!/usr/bin/python3` and the
plugin declares `python-bleak` as a system dependency.

This also matches the platform: Omarchy's own 422 scripts are 417 bash and 5
python3, with zero ruby and zero node.
