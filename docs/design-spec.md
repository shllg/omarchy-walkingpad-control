# omarchy-walkingpad — design spec

Status: draft for review · 2026-08-24
Target: standalone Omarchy plugin, own git repo, marketplace submission.

`docs/plugin-evidence.md` records the loader, manifest, installation and
marketplace implementation details this spec relies on.

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
  │  WalkingpadPanel.qml    × N monitors   (pure view, no state)│
  │        bar icon + popup  ◄─ IpcHandler ◄──────────────┼─ qs ipc call
  │            │ reads / writes commands                        │
  │  WalkingpadService.qml  service (one per plugin id)         │
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
**single** BLE connection slot. The manifest therefore declares the `service`
kind through `kinds: ["service", "bar-widget"]`; for every enabled service-kind
plugin, the shell's service loader creates exactly one service object per plugin
id. The manifest also sets `keepLoaded: true`, matching the first-party precedent,
but that flag does not control service mounting. Each widget reaches the service
through `bar?.shell?.serviceFor("io.github.shllg.walkingpad")` and remains a
stateless view.

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
`{"cmd":"speed","kmh":3.0}` · `{"cmd":"start"}` · `{"cmd":"start","kmh":2.0}` ·
`{"cmd":"stop"}` · `{"cmd":"goal","kmh":3.0}` ·
`{"cmd":"goal","kmh":3.0,"absolute":true}`

`goal` takes an OPTIONAL `absolute`. Without it the value is bounded against the
actual belt speed by `max_goal_lead` — the stepping path, driven by −step/+step.
With it the value is bounded by the pad's range alone, which is what the Set
editor commits. Both converge identically, one clamped step per status frame;
see §6 rule 12.

`start` takes an OPTIONAL `kmh`. With it, the bridge runs the two-part start of
§6 rule 6 — issue `start`, then send that speed once a status frame reports the
belt running. Without it, a bare `start` sends nothing further and the pad goes
to its own ~3.5 km/h.

That is the complete implemented command vocabulary. Any other `cmd` value is
silently ignored, including `mode`, `connect` and `disconnect`. This is not an
error or refusal path: `docs/smoke-test.md` §2 step 4 deliberately sends an
unknown command and requires no output at all.

- **`mode`:** will not be implemented on current evidence. In `standby` the pad
  ignores every motion command and the BLE mode-switch opcode; only the panel's
  **M** button leaves standby (§5.6). The one situation where this command would
  matter is therefore the situation measured not to work over BLE. §11 Q4 also
  records `mode == 0` (`auto`) as still unobserved. This command stays out unless
  hardware evidence later shows the mode-switch opcode producing an observed
  mode transition outside standby; that question remains unresolved.
- **`connect`:** absent from the current interface. The bridge presently owns the
  link end to end: it scans before connecting, connects, treats only a recent
  status frame as proof of life through its 15 s watchdog, and rescans without a
  command. However, a targeted `connect <addr>` capability is an **OPEN
  DEPENDENCY** of §5.4's adoption design. That section stores the address selected
  by the user, while the bridge has no stdin path for a target address and its
  discovery only matches advertised FE00, falling back to a `KS-` name prefix.
  Stage 2 must either implement this capability or design around the gap; it is
  unresolved, not a decided deletion.
- **`disconnect`:** absent because graceful release already follows the bridge
  process lifecycle. SIGTERM is handled through a `GLib.unix_signal_add` source
  because plain Python signal handlers do not run under the GLib main loop, and
  `run()` tears the link down in its `finally` block. No stdin command is needed
  for that release path.

stdout → one JSON object per line:
```json
{"t":"status","belt":"running","speed":2.5,"target":2.5,"pad_set_speed":0.0,
 "mode":"manual","time":1014,"dist_km":0.70,"steps":1321}
{"t":"link","state":"connected","addr":"<pad-addr>","name":"KS-BLC2"}
{"t":"error","code":"no_link","msg":"not connected to the pad"}
{"t":"error","code":"start_unconfirmed","msg":"the belt did not start within 8s; no speed was sent"}
```

`pad_set_speed` is what the pad has stored from the last BLE speed command; it
reads 0.0 when the speed was most recently set at the panel (AGENTS.md fact 15).
It is diagnostic only — `target` is the number a UI should render.

Line-delimited JSON is chosen because `Quickshell.Io.SplitParser` consumes it directly.

### 4.2 Poll loop

1 Hz while connected, per §2.1(a). Backs off to 0.2 Hz when the belt has been
stopped for >60 s, to keep the radio quiet. Every status frame is emitted, so the
service never has to poll the bridge.

### 4.3 Protocol adapter seam

The implemented bridge is a flat `walkingpad/` package. `frames.py` owns the pure
WiLink frame codec. `transport.py` owns BLE/D-Bus plumbing and knows nothing of
WalkingPad semantics. `safety.py` gates motion commands. `bridge.py` orchestrates
the poll loop and command dispatch.

FTMS, the standard `0x1826` service, is **deferred**. As recorded in
`docs/plans/2026-08-24-bridge-stage1.md` under "Deferred to later stages", the
author's C2 has no `0x1826`, so nobody involved can test an FTMS path. Building
one now would ship untested code claiming support. Per D-1's existing principle,
untestable code must be clearly marked as such rather than shipped silently.

A future adapter seam remains an option if someone with FTMS-capable hardware can
test a second protocol implementation. Its shape and module layout are
unspecified until that hardware exists.

### 4.4 Standalone testability

The bridge runs without Quickshell:
```
walkingpad-bridge --replay fixtures/c2-fw6.1.2.jsonl    # no hardware needed
```
`--replay` feeds captured frames so the QML layer and CI can run pad-free.

`--once` is deliberately not provided. A one-shot process would contend with the
long-running bridge for the pad's single BLE connection slot. It would also have
to scan before connecting, which is slow and often finds nothing because the pad
normally sleeps and stops advertising entirely. One-off external commands instead
use §5.1's `IpcHandler` (target `walkingpad`), which exposes `speedUp`, `speedDown`,
`stop`, `start`, `toggle` and `status`; for example,
`qs ipc call walkingpad speedUp` uses the existing service and bridge.

## 5. The QML plugin

### 5.1 `WalkingpadService.qml` (service entry point)

Declared through `kinds: ["service", "bar-widget"]` and loaded once per enabled
plugin id by the shell's service loader (§12 D-5). The manifest separately sets
`keepLoaded: true`, matching the first-party precedent; that flag does not control
service mounting. Widgets retrieve it through
`bar?.shell?.serviceFor("io.github.shllg.walkingpad")`. It owns the `Process`,
parses stdout via `SplitParser`, and exposes:

Optional configuration reaches this service only when a widget forwards its
injected `settings`, per D-5; forwarded settings never define bridge safety
limits.

```
readonly property string  linkState      // scanning | connected | absent | unavailable | lost | error
readonly property string  beltState      // stopped | starting | running | idle | unknown
readonly property real    currentSpeed   // from frames — ACTUAL
readonly property var     commandedSpeed // what the BRIDGE last commanded; null until something is
readonly property var     pendingSpeed   // local intent, only while §5.2's debounce is in flight
readonly property var     targetSpeed    // pendingSpeed, else commandedSpeed — what a UI shows
readonly property bool    ramping        // currentSpeed has not reached targetSpeed
readonly property bool    running        // beltState === "running", and nothing else (fact 8)
readonly property int     steps
readonly property real    distanceKm
readonly property int     elapsedSec
```

`commandedSpeed` is nullable on purpose: the bridge routinely adopts a belt it
did not start, and reporting 0.0 there reads as a commanded stop.

The service also owns the command channel — `start()`, `stop()`, `setSpeed()`,
`speedUp()`, `speedDown()` — writing one JSON line per action to the bridge's
stdin. It re-clamps nothing: every command is gated again in Python, which is the
copy with tests. `send()` clears the last error, so a refusal stays on screen
until the user's next action rather than being wiped by the next status frame
1 s later.

**Assignment order in `handleStatus` is load-bearing.** Each property signals its
own change, so a handler watching one reads whatever the others hold at that
instant. `beltState` is assigned LAST because it is the property consumers watch
to decide the belt is moving; `lastErrorMessage` is assigned before
`lastErrorCode` for the same reason. Verified under Quickshell: before this,
`onBeltStateChanged` observed `belt=running` beside the previous frame's
`speed=0, mode=unknown`.

**Bridge respawn.** `onExited` restarts the bridge up to 3 times a minute, then
holds a hard error state. This is not rule 11's forbidden retry — a fresh bridge
sends no motion command, it scans, and any half-finished start died with the old
process. The cap is so a bridge that cannot run fails visibly.

The `IpcHandler` (target `walkingpad`) lives in the panel rather than here,
because `qs.Ui`'s `Panel` base already claims that target for `open`/`close`/
`toggle` and only one handler per target is permitted. See §5.3.

### 5.2 Input coalescing — required, not an optimisation

Because of §2.1(b), rapid clicks must not become rapid writes.

- Clicks mutate `pendingSpeed` immediately and locally.
- A 250 ms debounce timer sends **one** speed command for the settled value.
- While `ramping`, the panel labels the target "Heading for" so the input is
  visibly acknowledged even though the pad has not reached it.
- Each step is measured from the **actual** belt speed, not from the accumulated
  local value. The pad clamps every command to one step from where the belt
  really is, so accumulating clicks locally would display a target the belt was
  never going to reach.

⚠ The bridge's repeat-suppression signature includes the requested speed, not
just the command name. It keyed on the name alone until 2026-08-25, which made
two DIFFERENT speeds inside the 1 s window collide, with the second dropped in
silence — indistinguishable, from the bar, from a speed the pad ignored. That is
exactly the reading that would talk someone into weakening a safety gate.
Allowing distinct values through adds no hazard, because each is still clamped to
one step from the actual speed, which moves only ~0.25 km/h per second: a burst
of clicks still resolves to the same number and still collapses to one write.

### 5.3 `WalkingpadPanel.qml` (bar-widget entry point)

Stateless view. Built on `qs.Ui`'s `Panel`, which is the shape every first-party
popup uses: ONE bar-widget entry point whose root owns both the bar button and
the popup. `omarchy.power`, `omarchy.bluetooth` and `omarchy.audio` are all
`kinds: ["bar-widget"]` with `entryPoints.barWidget` pointing at a `Panel`; there
is no separate "panel" kind for this. `manageIpc: false`, so the panel owns the
single `IpcHandler` on target `walkingpad`, exposing `open`/`close`/`toggle` plus
`start`, `stop`, `speedUp`, `speedDown`, `status` — Hyprland keybinds are then
one-liners (`qs ipc call walkingpad speedUp`) with no extra binary.

**The bar shows one icon and no text.** A `BarIconButton` at `Style.bar.iconSlot`,
its glyph carrying the state: `nf-md-run` (U+F070E) while the belt is running,
`nf-md-walk` (U+F0583) otherwise, dimmed whenever the link is not connected. Fact
3 makes dimming the right register for a sleeping pad — that is its normal
resting state, not a fault, so it never takes the urgent role. Glyphs are written
as `String.fromCodePoint`, because these all sit in the Supplementary Private Use
Area where a stray re-encoding truncates them into a different, valid-looking
character. An earlier revision rendered a full status sentence in the bar; at
`Style.font.body` that measured ~512 px with no cap and no elide, and it changed
width every 30 s while the pad slept.

| Interaction | Action | Rationale |
|---|---|---|
| Left click | open the panel | never starts the belt (§6.1) |
| Right click | **STOP** | panic action, always available, one gesture (§6.2) |

The popup carries a hero (glyph · title · state · actual speed, with the pad's
ceiling stated beneath as a small `MAX n.n`), an instruction line, the session
stats, and a `CONTROL` row of Start / Stop / −step / +step / Set with keyboard
navigation via `PanelKeyCatcher`.

⚠ An earlier revision drew a filled bar of `currentSpeed / maxSpeed` beneath the
hero. It was removed on 2026-08-25: a filled track is the idiom for progress
toward a TARGET, and spending it on a fixed ceiling made the pad's limit read as
something the belt was heading for. The bar also carried the ramp pulse, which
§2.1(b) makes load-bearing — there is no ACK anywhere, so the pulse was the only
"your command landed" signal. That signal survives in the `Ramping` state label;
anything reintroducing a filled bar here should mean an actual goal by it.

**Setting an exact speed is disclosed, not permanent.** The `Set` action reveals
a `qs.Ui` `TextField` seeded with the current target, and hides it again on
commit, on Escape, on any other command, when the link drops, and when the panel
closes. It exists because −step/+step measure from the actual belt speed (§5.2),
which the pad reports in tenths and hunts around: from 2.8 km/h the only
reachable values are 2.3 and 3.3, so a round number is unreachable from an odd
tenth by stepping alone. Stepping stays purely relative; this is the way onto an
exact value.

Two details are not incidental:

- While the editor is open the `PanelKeyCatcher` above it is `blocked`. That is
  the component's own documented contract for an inline editor, and the same
  thing the first-party wifi passphrase prompt does. `Keys.priority` is
  `BeforeItem`, so without it the panel swallows Escape, Enter and every arrow
  before the field ever sees them.
- The field's validator spans the pad's WHOLE range, not the live goal window.
  This is also what the editor commits against: an absolute goal is bounded by
  the pad's range alone, never by the stepping window (§6 rule 12). The caption
  underneath states that range, and if a typed value would still be cut — only
  by the ceiling or the floor now — it says so before the commit rather than
  afterwards: `99.0 applies as 6.0`.

**Buttons stay pressable and the bridge does the refusing.** The panel never
re-implements `Safety.may_move` or `may_stop`; it renders `stale_link`,
`standby`, `stop_unsafe` and `no_link` verbatim. One authority for safety, and no
second copy in untestable QML to drift from it.

**The instruction line is separate from the state label, not a replacement for
it.** The pad reports `belt=idle` and `mode=standby` in the SAME frame — the
captured `f8a204000200…` shows both, and `frames.py` records that belt 4 is what
an awake pad reports with nobody on it. An earlier widget tested `mode` before
`beltState` and so displayed "In standby — press M on the panel" for essentially
the whole time the pad was reachable, hiding every statistic behind it.

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

The stored `address` remains gated on §4.1's open targeted-connect dependency;
D-5 neither closes that gap nor introduces a state file.

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

### 5.7 Stats, session charts and the analysis window

**DESIGNED 2026-08-26 — see `docs/plans/2026-08-26-stats-and-analysis.md` (Stage 3).**
That document is authoritative for the store, the attribution model, the chart
component and the overlay window. This section records only what it resolved.

All four open questions this section held are answered there:

- **Which figures the bar shows.** The popup keeps its existing Distance/Steps/Time
  pairs and gains a compact four-row chart stack; the bar strip itself is unchanged.
- **Whether a sparkline is worth it, and how it renders with no accent contrast.**
  Yes, as a linked chart stack rather than a lone sparkline. It owns no colour at
  all: it takes `fg`/`accent`/`muted` as properties, because the panel loader injects
  `service` but not `bar` (`shell.qml:629-637`), so a component importing either
  palette would be unusable in one of its two homes. §5.5 holds in both.
- **Whether totals are accumulated locally.** Yes, and the state file D-5 deferred
  now exists — see ADR D-6. It lives outside the plugin directory, under
  `${XDG_STATE_HOME:-$HOME/.local/state}/walkingpad/` at mode `0700`.
- **What the dormant state shows.** Last session's chart and totals, read from the
  store, rather than an empty frame. Absence is the normal resting state (§2.1) and
  must not read as a failed connection.

The load-bearing idea, and the reason a naive implementation gets this wrong: the
pad's counters are cumulative and survive a stop, so **the pad's counter is the
authority for an epoch and our samples attribute portions of it**. The unattributed
remainder is walking we did not observe, computed as `last_pad − attributed` rather
than inferred. Accumulating our own totals from frame deltas instead would drift
below the pad by exactly the unobserved amount and never detect it.

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
  had reached. Stop is also refused without recent evidence of motion because its
  opcode is a toggle that can start a stopped belt. The panel and remote remain
  the safe fallback; whether an abrupt physical stop should replace the taper is
  still unresolved.
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
  (service lifetime and enablement). Trigger 1 does not have this problem: a
  manual taper is short enough to live wherever the session lives.

Config sketch, if built: `taperSec` · `taperStep` · `goalUnit` · `dailyResetHour`.

## 6. Safety model

The pad accepts unauthenticated commands and may have a person standing on it.

1. **Left click never starts the belt.** Starting is an explicit menu action or a
   deliberate keybind. Discovered the hard way: the author was mid-walk during
   protocol probing.
2. **Right click always means stop**, at every level of the UI.
3. **Per-action speed delta is clamped** (default 0.5 km/h, configurable). One
   gesture cannot take a walker from 2.5 to 6.0 km/h.

   ⚠ Revised 2026-08-25, after hardware use. Clamping each press against the
   ACTUAL belt speed made the buttons dead for ~2 s per step: the belt ramps at
   ~0.25 km/h/s, so a second press inside that window resolved to the same
   number and was dropped as a repeat. The fix is a **live goal** (rule 12), not
   a bigger per-command clamp. Every write is still one `max_step` step; what
   changed is that the *intention* may now sit ahead of the belt.

12. **The live goal, and the four bounds it rests on.** `{"cmd":"goal","kmh":X}`
    sets a standing intention. `Bridge._service_goal()` walks the belt toward it,
    at most one write per status frame, and is **the first autonomous writer in
    this design** -- every other write here follows a press. §5.8 requires that
    to be justified rather than assumed, so:

    1. `Safety.clamp_goal()` bounds the goal to `max_goal_lead` (1.0 km/h) either
       side of the ACTUAL speed. This is what replaces "one gesture, one step".
       The original rationale was *"the worst case of a stuck key or a double
       click is one small increment"*; an uncapped goal hands that back, because
       a repeating key walks the goal to the ceiling and the belt follows it
       there. With the cap, a key that repeats forever still cannot move the goal
       more than 1.0 km/h ahead of reality, and cannot compound until the belt
       has caught up -- which takes real seconds, in front of someone who can see
       it happening.
    2. Every write is still `clamp_speed()`, one `max_step` step. Loosening the
       lead does not loosen this; `tests/test_goal.py` asserts it by widening
       `max_goal_lead` and checking the write is still one step.
    3. Nothing is written unless a fresh frame says `belt == running`, the mode
       is not standby, and `may_move()` passes.
    4. It stops on arrival and never repeats a value. A belt that is not moving
       toward the goal -- someone braced against it, a pad ignoring us -- ends
       the convergence rather than being hammered once a second.

    ⚠ Bound 1 governs the STEPPING path only. `{"cmd":"goal","kmh":X,"absolute":
    true}` -- a value the user typed into the Set editor and committed -- is
    bounded by the pad's own range and nothing tighter
    (`Safety.clamp_goal_absolute`). The lead bound is aimed at the repeating
    path, where each press is worth one increment and nobody has named a
    destination; an absolute value is one deliberate act that does name one, and
    cutting it to where the belt currently sits only makes the user type it
    again. Revised on 2026-08-25 after the bound turned a typed 3.5 into 1.0 on
    a STOPPED belt -- a state where the goal is not serviced at all, so the
    bound was protecting nothing and refusing everything.

    Bounds 2, 3 and 4 are what make that safe, and they apply identically to
    both paths: `max_speed` is still the ceiling, `min_goal` still the floor, and
    the belt is still walked there one `clamp_speed()` step per status frame. So
    an absolute goal changes how far ahead the intention may SIT, never how fast
    the belt may MOVE. `tests/test_goal.py` §5 re-proves the one-step-per-frame
    property against a distant absolute goal rather than assuming it carries
    over.

    `stop`, `start` and an explicit one-shot `speed` all cancel a goal, and a
    goal never writes in the same frame a start's own speed lands.

    A goal is floored at `min_goal` (0.5 km/h) and never reaches 0.0: whether a
    set-speed of zero stops the belt is an OPEN hardware question
    (`docs/smoke-test.md` §4), and the stop opcode is a toggle, so normal use
    must not wander into probing it by accident.

    ⚠ Still unproven on hardware. `docs/smoke-test.md` §4d is the gate.
4. **No blind writes.** If no status frame has arrived within 3 s, the service
   refuses to issue motion commands and shows a stale-link state. ⚠ **This now
   includes stop** — see rule 10.
5. Absolute speed is clamped to a configurable `maxSpeed` (default 6.0 km/h).
6. ⚠ **Starting is the biggest hazard, and the clamp does not cover it.** Measured
   2026-08-24: the pad ramps to **~3.5 km/h on START regardless of the speed it has
   stored**, and ignores a speed command issued while the belt is stopped. The
   per-action clamp governs *changes*; this is the pad's own start behaviour and
   sits outside it.

   Therefore starting is a two-part action, and **the bridge** owns both parts:
   `{"cmd":"start","kmh":X}` issues `start`, arms `X`, and sends it from
   `on_notify` as soon as a status frame reports `belt=running`. There is a ~2 s
   window at `belt=running, speed=0.0` before the pad accelerates, which is where
   the speed command must land. A UI that offers a bare "start" and trusts the
   displayed speed setting is putting 3.5 km/h under someone's feet.

   ⚠ Earlier revisions of this section put the sequencing in the QML service.
   That was moved on 2026-08-25: QML cannot be unit-tested here, and this is the
   most dangerous logic in the project. In Python it sits beside `Safety` and is
   covered by `tests/test_start_sequence.py`. Three properties matter, and each
   has a test:

   - The follow-up speed is capped at `Safety.pad_start_speed` (3.5), never run
     through `clamp_speed()`. The step clamp measures a change against the
     CURRENT speed, which is 0.0 here, so it would clamp every start to 0.5. The
     cap makes the safety property provable instead of argued: a start can be
     slowed down and has no way to be sped up.
   - It is armed only when the belt is not already running, fires at most once,
     and is cancelled by `stop` or by any explicit `speed`.
   - It expires after `START_SPEED_WINDOW_S`. Without expiry a start that never
     took would fire whenever the belt next moved — possibly much later, when
     someone started it by hand at the panel.

   This is not the timer retry rule 11 forbids: nothing is re-sent, and the
   trigger is an observed state change rather than a clock.
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

The bar injects a per-layout-entry `settings` object into a widget that exposes
that property. The service loader does not inject settings into services. Stage 2
must therefore choose where the service reads configuration and how the widget
hands changes across; that source is an open design question, not a platform
facility to assume.

D-5 (§12) resolves that asymmetry and defines the safety boundary of the chosen
settings channel.

The intended optional values remain:
`address` · `maxSpeed` (6.0) · `speedStep` (0.5) · `units` (kmh|mph) ·
`autoConnect` (true) · `notifyOnConnect` (true) · `idleDisconnectSec` (0 = never)

## 8. Error handling

| Condition | Behaviour |
|---|---|
| Pad not in range | `linkState = absent`, dormant glyph, no retry storm (30 s backoff) |
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
  manifest.json          id: io.github.shllg.walkingpad
                         kinds: [service, bar-widget], keepLoaded: true
                         barWidget.allowMultiple: false
  WalkingpadWidget.qml
  WalkingpadService.qml  service entry point, one object per plugin id
  bin/walkingpad-bridge
  walkingpad/{frames,transport,safety,bridge}.py
  fixtures/c2-fw6.1.2.jsonl
  README.md  LICENSE (MIT)
```

The intended id is `io.github.shllg.walkingpad`. A bare `walkingpad` is legal,
but it squats a generic name in a namespace where ids are permanent.

Marketplace submission currently goes through the issue-form template in
`HANCORE-linux/omarchy-plugin-marketplace`, not a direct `registry.json` PR. The
public repository must have `manifest.json`, README and licence at its root. This
flow is pre-release infrastructure and must be checked again immediately before
submission (`docs/plugin-evidence.md`).

## 11. Open questions

1. ✅ **ANSWERED 2026-08-25: the `service` kind supplies the singleton.** Declare
   `kinds: ["service", "bar-widget"]`; the service loader mounts the service once
   per enabled plugin id. A top-level `plugins[]` entry is sufficient enablement
   under `findEntryLocation()`, independently of any bar widget, and each widget
   retrieves the service through `bar?.shell?.serviceFor(<id>)`. The manifest also
   declares `keepLoaded: true`, matching the first-party media service, but that
   flag is not the mounting mechanism. The first-party media service is the
   working precedent. Verified in `docs/plugin-evidence.md`.
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
   so BLE cannot reach it at all. **Second half, opened 2026-08-26:** how long
   until it RESETS its cumulative counters, and whether sleep is the trigger. An
   idle pad has been seen reporting every counter at zero, but the transition was
   never observed. `docs/smoke-test.md` §4f is the procedure. This bounds, but
   cannot close, the under-count in `docs/plans/2026-08-26-stats-and-analysis.md` §10.

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

### D-5 — Widget forwarding carries preferences, never safety policy (2026-08-25)

**Decision.** Optional user preferences reach the service through the widget's
injected `settings` property. That channel carries preferences only; it is never
the source of a safety invariant or required startup state.

**Why forwarding is needed.** Verified against the installed Omarchy shell
(`4.0.0.alpha`): the bar injects `bar`, `moduleName` and `settings` into matching
widget properties, both when the item becomes active and when its settings change
(`/usr/share/omarchy/shell/plugins/bar/Bar.qml:1744-1753`). It also patches a live
widget's `settings` when `shell.json` changes
(`/usr/share/omarchy/shell/plugins/bar/Bar.qml:376-386`). The service loader
injects `omarchyPath`, `shell`, `manifest`, `barWidgetRegistry` and
`pluginRegistry`, but no `settings`
(`/usr/share/omarchy/shell/shell.qml:283-313`).

`manifest.json` therefore declares §7's optional `address`, `maxSpeed`,
`speedStep`, `units`, `autoConnect`, `notifyOnConnect` and `idleDisconnectSec`
under `barWidget.defaults` and `barWidget.schema`. The working MIT-licensed
`eduardodallecort.weather-radar` precedent uses those manifest surfaces
(`~/.config/omarchy/plugins/eduardodallecort.weather-radar/manifest.json:23-49`):
plain defaults supply the values, while schema objects supply the key, type,
label, `defaultValue` and description that the settings UI renders. Its widget
then calls a `syncService()`-style handoff whenever settings change and again when
the service reference becomes available
(`~/.config/omarchy/plugins/eduardodallecort.weather-radar/BarWidget.qml:39-45,84-85`).
Multiple bar instances may repeat the same assignment; they read the same
`shell.json` entry, so the writes are equivalent.

The service starts with `settings: ({})`, and a widget is created before the bar
injects its real values. Verified in the same precedent: `setting(name, fallback)`
uses the fallback only for `undefined` or `null`, while `settingsReady` treats the
initial empty object as "not known yet," not as a chosen value
(`~/.config/omarchy/plugins/eduardodallecort.weather-radar/Service.qml:36-53`).
→ The WalkingPad service must apply the same guard. An empty or not-yet-populated
object never means that a feature is disabled or that a fallback was selected.

**The safety boundary is load-bearing.** Verified against the installed shell:
`_syncServices()` mounts every enabled service-kind plugin with a service entry
point (`/usr/share/omarchy/shell/shell.qml:323-344`). For a third-party plugin,
`isEnabled()` delegates to `findEntryLocation()`, which considers the selected
bar, every bar layout and the top-level `plugins[]` array; that array is
explicitly the enablement path for panels, overlays and services
(`/usr/share/omarchy/shell/services/PluginRegistry.qml:108-138,206-221`). A
top-level plugin entry can therefore keep this service polling and holding the
pad's one BLE connection while zero widgets exist anywhere. In that state no bar
item exists to receive or forward settings, so a forwarded value is permanently
absent for the service's entire lifetime, not merely delayed during startup.

`keepLoaded` is not this mechanism: `_syncServices()` does not reference it. Its
separate use keeps a panel loader active while its window is hidden
(`/usr/share/omarchy/shell/shell.qml:625`).

→ **Nothing safety-relevant may depend on a forwarded setting.** In particular,
the forwarded `maxSpeed` and `speedStep` names are not enforcement inputs. They
must never set, relax or replace the bridge's `max_speed`, `max_step` or
`stale_after_s`. `Safety.clamp_speed()` owns the per-action and absolute speed
clamps, while `may_move()` and `may_stop()` own the staleness gate because a
person may be standing on the belt
(`walkingpad/safety.py:5-16,21-39,54-56`). The bridge constructs it from
process-launch flags `--max-speed` and `--max-step`, defaulting to 6.0 and 0.5;
`stale_after_s` has no flag and retains the class default of 3.0
(`bin/walkingpad-bridge:11-20`, `walkingpad/safety.py:21-26`). Those limits can
change only through the bridge's own launch interface, owned by its process
launcher, never by mapping a value that arrives—or never arrives—over the widget
channel. A safety limit that can silently fail to arrive is worse than one that
cannot be changed from the bar at all.

**No state file yet.** The only setting the service could need before any widget
exists is §5.4's stored pad address. The bridge has no target-address input,
however; §4.1 keeps that as an **OPEN DEPENDENCY**. Its current discovery by the
advertised FE00 service, falling back to a `KS-` name prefix, works without a
stored address. A stored address is meaningless until the bridge has somewhere
to receive it.

→ Defer a state file until §5.4's adoption design or §5.7's daily-totals design
actually forces the question. This decision closes neither open question. There
is a mechanism precedent if one does: the weather-radar service reads a file
directly with `FileView`, independently of settings injection
(`~/.config/omarchy/plugins/eduardodallecort.weather-radar/Service.qml:70-90`).
That example reads shared state owned by the first-party weather widget, not the
plugin's own private state, so it is a mechanism to study rather than code to
copy.

### D-6 — The state file exists, outside the plugin directory (2026-08-26)

**Decision.** The plugin owns a private store at
`${XDG_STATE_HOME:-$HOME/.local/state}/walkingpad/`, created at mode `0700`. The
bridge writes it; a separate short-lived process reads it. This **supersedes D-5's
"No state file yet" clause and only that clause.** D-5's safety boundary — that the
widget settings channel carries preferences and never a safety invariant — is
untouched and still governs.

**What forced it.** D-5 deferred the file until §5.4's adoption design or §5.7's
daily-totals design actually required it. §5.7 now does
(`docs/plans/2026-08-26-stats-and-analysis.md`). §5.4 is still unforced: the bridge
still has no target-address input, so a stored pad address would still have nowhere
to go. This decision does not close §5.4.

**Why outside the plugin directory, and why `0700`.** Two independent reasons, either
sufficient:

1. This repository IS the installed plugin, and the shell watches that directory with
   `inotifywait -m -r -e close_write,create,delete,move` and no `--exclude`. A 1 Hz
   append inside it would look exactly like the plugin changing and fire a full
   `reloadPlugins()` every second — the same trap that forced
   `sys.dont_write_bytecode = True` in `bin/walkingpad-bridge`.
2. It is personal health data: a record of when its owner is at their desk and
   moving. `0700` is the floor, and no part of it may ever reach a tracked file.

**Why the bridge writes but does not read.** The poll loop is safety-critical — a
status frame is the only proof of a live link (D-4), and `IDLE_POLL_INTERVAL_S` is
already tuned against `Safety.stale_after_s`. Writing is bounded, append-only work.
Reading is not: aggregating a year of samples is unbounded, and doing it in the
process that must answer within the staleness gate would trade a safety property for
a reporting feature. Aggregation therefore lives in `bin/walkingpad-stats`, a
short-lived child spawned by the UI — consistent with §3.2's "child process, not a
daemon", and with D-1, since the gap-grouping and residual maths stay in Python where
pytest reaches them. Parsing in QML was rejected for the same unbounded-work reason,
on the UI thread instead.

A store write that fails is emitted once as an error and then suppressed. A full disk
degrades to "no stats" and must never stop the belt controls working.

**Correction to D-5 on `keepLoaded`.** D-5 recorded that `_syncServices()` does not
reference `keepLoaded`, and that is still true — but it was read at the time as
meaning the flag was inert for this plugin, which held only while the manifest
declared `service` and `bar-widget`. The panel loader *does* read it
(`shell.qml:625`). Adding the `overlay` kind for the analysis window therefore gives
the flag a real effect it never had: the window mounts permanently instead of on
first open. That is the wanted behaviour — the window holds no BLE resource, and
`summon` can then deliver its payload immediately through `deliverIfLoaded` rather
than racing an asynchronous `Loader`.

**One further consequence of the `overlay` kind, verified before adopting it.**
`shell.isBarWidgetPanelPlugin()` returns false for a plugin holding `bar-widget` and
an overlay/panel/menu kind (`shell.qml:426-438`), so `shell.summon/hide/toggle` for
this plugin id re-routes to the panel loader. `omarchy.menu` is the first-party
precedent. The documented keybinding path is unaffected: the popup's IPC is
`IpcHandler { target: "walkingpad" }` (`WalkingpadPanel.qml:189`), a Quickshell IPC
target independent of the shell's plugin routing.
