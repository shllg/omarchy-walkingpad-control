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
                            walkingpad-bridge  (system Python + Gio)
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

`bin/walkingpad-bridge` — system Python, talking to BlueZ over D-Bus through
`gi.repository.Gio`. No third-party runtime dependency (ADR D-3). A pure protocol
codec with no knowledge of Quickshell.

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

### 5.5 Theming — the plugin owns no colour

**Hard constraint.** Omarchy's themes are a large part of why anyone uses it, and
a widget that ignores them looks broken next to the built-ins. The widget must
therefore not define a single colour, radius, font size or spacing of its own.

- `import qs.Commons` for the `Color`, `Style`, `Util` and `Border` singletons.
  Surface roles come from `Color`; every dimension comes from `Style`.
- `import qs.Ui` and build on the pre-themed components rather than styling
  primitives by hand.
- **Never hardcode a colour, not even a fallback.** No `#rrggbb`, no named colour,
  no `Qt.rgba` literal.
- Theme switches are live: `Color.qml` reassigns its whole values object, so
  bindings re-evaluate on their own. Copying a colour into a local property at
  construction time breaks that and is the one easy way to get this wrong.
- The shipped `weather-radar` plugin is the working precedent to read first.

Where the design needs a colour that carries meaning rather than decoration —
belt running, link absent, refusal — it takes it from a semantic role (`accent`,
`urgent`, `muted`), never from a chosen hue.

### 5.6 Standby is a state the user must be told how to leave

Measured: with `mode == standby` the pad answers status queries but silently
ignores START, STOP and speed alike, and the BLE mode-switch command is ignored
as well. **Nothing the plugin can send leaves standby** — it takes the panel's M
button.

So this is not an error to log, it is an instruction to display. The widget must
say what it sees and what to do about it, naming the button:

> Pad found, but it is in standby — press **M** on the panel to control it.

The bridge already refuses motion commands in this state and emits
`{"t":"error","code":"standby"}` with that wording, so the widget renders the
reason rather than deriving it. Distinguish it clearly from `absent` (asleep,
nothing to be done from here) and from `unavailable` (the phone app holds the
single BLE connection).

### 5.7 Stats and visuals (Stage 2 scope, noted)

Beyond the minimum controls, the bar widget is to show session stats and some
visual treatment of them. Not yet designed; recorded here so it is not lost.

The bridge already emits everything needed per poll: `speed`, `target`, `belt`,
`mode`, `time`, `dist_km`, `steps`. Open design questions:

- Which figure earns the bar's scarce horizontal space when the belt is running,
  and which move into the menu.
- Whether a sparkline or progress arc is worth it in a bar-height strip, and how
  it renders under a theme that has no accent contrast to spare.
- Whether daily totals are accumulated locally. The pad's own counters reset, and
  persisting anything means owning a state file, which is a new concern the bridge
  currently does not have.
- What the dormant state shows. It is the common case (§2.1) and must read as
  deliberate rather than as a failed connection.

All of it inherits §5.5: stats are rendered with `qs.Commons` and `qs.Ui`, with no
colour of the plugin's own.

### 5.8 Taper-to-stop, manual and goal-driven (deferred, noted)

Not Stage 2. Recorded now because it changes the safety model, and must not be
retrofitted casually.

**The problem.** Stopping the belt abruptly leaves the walker dizzy. The desk in
front of them never moved, so the sudden end of the leg motion is a change the
balance system has to absorb all at once. Ending a walk should be a ramp, not a
switch.

**The primitive: taper to stop.** Walk the speed down across a window, then issue
an explicit `stop` from the pad's lowest speed. The window is configurable,
defaulting short enough to read as the end of the walk rather than a slow fade — a
first guess is 60 s. Everything below is a way of triggering this one primitive.

**Trigger 1 — manual, from the UI.** A "wind down" action alongside stop: the
walker asks for the taper when they want it, with no goal armed and nothing to
configure in advance. This is the smaller half of the feature and probably ships
first, because it needs no counters, no persistence and no prediction — only the
taper itself.

⚠ It must be a *separate* action, never a replacement for stop. §6.2 makes right
click mean stop at every level of the UI, and that gesture has to stay one
unambiguous thing that halts the belt now. A UI where the stop affordance
sometimes takes 60 s to stop the belt is a worse design than no taper at all.

**Trigger 2 — a goal, armed before or during a walk.**

- `stop at 5000 steps`
- `stop in 10 minutes`
- `stop at 30 minutes total` (cumulative, not this session)
- the distance equivalents of each

As the goal approaches, the goal fires the taper instead of a bare `stop`.

**Why this is not a small feature.** Every write in the design today is
user-initiated. Trigger 2 is the first thing that commands the belt on its own,
and it does so while someone is standing on it. That inverts §6 rather than
extending it, so the questions below precede any implementation.

Open questions:

- **The taper must never be a surprise.** A goal that fires unnoticed is a speed
  change under someone's feet. The bar has to show an armed goal and its
  countdown, and the taper needs a visible "slowing down" state of its own. This
  question is why trigger 1 is the easy one: the walker asked for it, so it is
  never a surprise.
- **Whether a taper in progress is interruptible, and by what.** At minimum stop
  must cut it short. Whether a speed change cancels it or rebases it is undecided.
- **What happens when the link drops mid-taper.** §6.4 refuses motion commands on
  a stale link, so the taper simply stalls, leaving the belt at whatever speed it
  had reached. Stop is always permitted even on a stale link — but falling back to
  an abrupt stop is precisely what this feature exists to prevent. Neither branch
  is obviously right.
- **Which counter the goal is measured against.** The pad's counters reset on
  their own, so a goal must be a delta from when it was armed, and a mid-walk
  reset has to be detected rather than read as the goal receding.
- **`total` forces persistence.** A cumulative goal needs state that outlives the
  process — the state file §5.7 already flags as a new concern — plus a definition
  of "a day".
- **Overshoot or undershoot.** A taper cannot both begin before the goal and reach
  the goal at a standstill. The simplest honest rule is that the window is
  expressed in the goal's own unit (the last 500 steps, the last 2 minutes) and
  the number lands where it lands.
- **Taper rate versus the pad's own ramp.** Reported speed is actual and the pad
  glides ~0.25 km/h per second (fact 2), so a taper is a schedule of setpoints,
  not continuous control. Whether it reuses `speedStep` or needs a gentler rate of
  its own is untested.
- **The pad's minimum speed is unmeasured.** The last rung before `stop` depends
  on it.
- **A manual override should cancel a goal.** If the walker changes speed at the
  panel, byte [14] is not evidence (fact 15). The taper must work from actual
  speed, and decide what human intervention means.
- **Where the timer lives.** A goal that dies when the bar reloads is worse than
  no goal, because the user is relying on it. That argues for the bridge, which
  owns the poll loop and the only guaranteed clock — but D-1 scopes the bridge to
  protocol, and a goal is policy. Unresolved, and entangled with open question 1
  (`keepLoaded`). Trigger 1 does not have this problem: a manual taper is short
  enough to live wherever the session lives.

Config sketch, if built: `taperSec` · `taperStep` · `goalUnit` · `dailyResetHour`.

## 6. Safety model

The pad accepts unauthenticated commands and may have a person standing on it.

1. **Left click never starts the belt.** Starting is an explicit menu action or a
   deliberate keybind. Discovered the hard way: the author was mid-walk during
   protocol probing.
2. **Right click always means stop**, at every level of the UI.
3. **Per-action speed delta is clamped** (default 0.5 km/h, configurable). One
   gesture cannot take a walker from 2.5 to 6.0 km/h.
4. **No blind writes.** If no status frame has arrived within 3 s, the service
   refuses to issue motion commands and shows a stale-link state. ⚠ **This now
   includes stop** — see rule 10.
5. Absolute speed is clamped to a configurable `maxSpeed` (default 6.0 km/h).
6. ⚠ **Starting is the biggest hazard, and the clamp does not cover it.** Measured
   2026-08-24: the pad ramps to **~3.5 km/h on START regardless of the speed it has
   stored**, and ignores a speed command issued while the belt is stopped. The
   per-action clamp governs *changes*; this is the pad's own start behaviour and
   sits outside it.

   Therefore starting is a two-part action, and the service owns both parts: issue
   `start`, then issue the intended speed as soon as `belt` becomes `running`.
   There is a ~2 s window at `belt=running, speed=0.0` before the pad accelerates,
   which is where the speed command must land. A UI that offers a bare "start" and
   trusts the displayed speed setting is putting 3.5 km/h under someone's feet.
7. **A stopped belt cannot be started by setting a speed** — the command is stored
   and ignored. Only `start` starts it (open question 2, now answered).
8. ⚠⚠ **The stop opcode is a TOGGLE — it starts a stopped belt.** Confirmed
   2026-08-25 by controlled test (`docs/protocol-evidence.md`). Never infer belt
   state from "we did not command it"; the status stream is the only truth.
10. ⚠⚠ **Stopping is gated on evidence of motion.** `Safety.may_stop()` requires a
   recent frame showing the belt `running` or `starting`, or a non-zero speed. An
   earlier version of this spec said stop was always permitted including on a
   stale link; that was exactly backwards and is retracted. When the evidence is
   missing the UI must say so and point at the panel and remote, which always
   work. Stopping during the `starting` countdown IS permitted — that is when it
   matters most.
11. **Never auto-retry a command on a timer.** A retry loop produced an endless
   countdown-cancel-countdown cycle on real hardware. Send once per user action
   and let the status stream report the result. §5.2's coalescing is a safety
   requirement for this reason, not an optimisation.
9. **`stop` is exempt from every throttle**, including the repeat-suppression that
   applies to `speed` and `start`. With no ACK in the protocol a dropped write is
   invisible, and a second press is a person's only recovery; swallowing it leaves
   them on a moving belt. A spammed stop is merely noisy.

Every rule above assumes the command came from a person. §5.8 sketches a
taper-to-stop that a *goal* could fire on its own — the first autonomous writer
in the design, aimed at a belt that by definition has someone on it. It is
deferred, and this section has to be revisited before any of it is built.

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
  walkingpad/{frames,transport,safety,bridge}.py
  fixtures/c2-fw6.1.2.jsonl
  README.md  LICENSE (MIT)
```

Marketplace submission is a PR adding the repo to `registry.json` in
`HANCORE-linux/omarchy-plugin-marketplace`. Checked 2026-08-24: 1161 sources,
**zero** treadmill or fitness plugins — this is the first.

## 11. Open questions

1. Does `keepLoaded: true` apply to `bar-widget` kind? The service must stay alive
   even if no bar shows the widget. Verify against the loader.
2. ✅ **ANSWERED 2026-08-24: yes, START is required.** A speed command on a
   stopped belt updates the pad's stored speed and does not move the belt.
   Verified twice. §5.1 must issue `start` first, then set the speed — and must
   do so promptly, because the pad starts at ~3.5 km/h on its own (§6.6).
3. Child-lock interaction with remote commands: unknown.
4. Mode semantics: manual = 1 and standby = 2 are both confirmed. `0` (auto) is
   still unobserved. ⚠ In standby the pad answers queries but ignores every motion
   command, and **the BLE mode-switch opcode is ignored too** — only the panel's M
   button leaves standby (§5.6).
5. Byte `[14]` is decoded: the speed most recently commanded over BLE, which
   reads 0.0 when the speed was set at the panel, so it is not a usable target.
   `[16]` varies with state (0x02, 0x03, 0x00) and is still undecoded; `[15]` and
   `[17]` were 0x00 in every capture.
8. How long a stale status snapshot can survive an abrupt link loss, and what
   clears it. Observed once: eight identical frames reporting a running belt that
   was physically stopped.
6. ~~Whether BlueZ-level connect and bleak's connect cooperate or contend.~~
   **DISSOLVED, not answered** (2026-08-24). The question presupposed two BLE
   stacks. Dropping bleak for Gio (ADR D-3) means the bridge is a BlueZ D-Bus
   client like every other client on the desktop, so there is no second stack to
   contend with. Verified live end to end.
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

`/usr/bin/python3`, with no third-party package at all (see ADR D-3 for how the
dependency was removed entirely).

Ruby, Node and Bun on this machine are all mise-managed. The os-configs AGENTS.md
documents a real, recurring failure in which concurrent `mise use -g` wipes the
global config and unmounts every binary under a version pin. A bar widget must not
inherit that failure mode. The bridge shebang is `#!/usr/bin/python3` and the
plugin has no system dependency to declare.

This also matches the platform: Omarchy's own 422 scripts are 417 bash and 5
python3, with zero ruby and zero node.

### D-3 — Gio/BlueZ, not bleak (2026-08-24)

**A plugin cannot install a dependency.** The manifest schema the shell enforces
has `schemaVersion`, `id`, `name`, `version`, `kinds`, `entryPoints` and a
`barWidget` block — and no dependency field. `omarchy plugin add` clones,
validates, moves, rescans and enables; it never invokes a package manager, and
there is no build, prepare or postinstall hook anywhere in the plugin lifecycle.

So a bleak dependency is not a packaging detail. It is a manual
`sudo pacman -S python-bleak` that every user must run before the plugin works at
all, and a plugin that silently does nothing until an undocumented command is run
is not the seamless install this is meant to be.

`python-gobject` and `bluez` are both in `omarchy-base.packages`, so a Gio
transport installs with zero extra steps. bleak is itself a BlueZ D-Bus client,
so this removes a wrapper rather than replacing a stack — which is also what
dissolves open question 6.

Consequences, accepted:

- The bridge runs a **GLib main loop, not asyncio.** Gio delivers notifications
  as D-Bus signals dispatched by a GLib loop, and bridging that to asyncio needs
  `asyncio-glib`, which Omarchy does not ship — reintroducing exactly the
  manual-install problem this decision removes.
- `find()` and `connect()` block that loop. They run only while disconnected,
  when there is no live link to service and no belt under our control to stop.
  The poll and command paths, which do answer for a belt, never block.
- More lines of transport code than bleak's API would need. That is the price of
  the dependency, and it is paid once.

Alternatives weighed and rejected: **Node** is not in Omarchy at all, and on this
machine node/npm/bun all resolve under mise — precisely D-2's failure mode. **Go**
and **Rust** are not guaranteed present, so with no build step each would mean
committing a prebuilt binary per release and architecture; Rust additionally trips
the marketplace's `cargo-git-unpinned` automatic failure. **C++** has a guaranteed
compiler, but QtBluetooth (`qt6-connectivity`) is not installed and is not a
Quickshell dependency, so it would be another manual install, and raw BlueZ D-Bus
in C++ is more code than Python for no gain. Measured: importing gi and connecting
the system bus takes ~80 ms at ~30 MiB RSS. At 1 Hz over BLE, language performance
is not a differentiator here — dependency-freedom is.

### D-4 — A recent status frame is the only proof of a live link (2026-08-24)

Measured: after an ungraceful kill, BlueZ reported `Connected=True` for a radio
link that was already gone. `Connect` succeeded, services resolved, `StartNotify`
succeeded and every write returned without error — into nothing.

So link state is never inferred from BlueZ's `Connected` property. Fifteen seconds
without a status frame tears the link down and rescans. This is the same principle
the safety gate already rests on (§6): there is no ACK anywhere in this protocol,
so a recent frame is the only evidence that a pad is listening.

The watchdog clock is seeded from the **connect** time and never by crediting a
fresh connection as though a frame had arrived — that would open the safety gate
on a link that has never said anything.

Two corollaries the QML side inherits:

- **A connected pad does not advertise.** A link outlives the process that made
  it, so an ungraceful exit strands the pad: discovery can never see it again.
  The bridge adopts an existing BlueZ link instead of rediscovering it.
- **Terminate the bridge with SIGTERM and let it exit.** SIGKILL leaves the
  phantom link above. The bridge handles SIGTERM and SIGINT through GLib signal
  sources and disconnects cleanly; a plain Python signal handler does not run at
  all under a GLib main loop.
