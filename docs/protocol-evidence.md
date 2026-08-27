# WalkingPad plugin — evidence log

## Device (measured 2026-08-24, this laptop)
- Advertised name `KS-BLC2`, addr `<pad-addr>`
- BLE SoC: BEKEN SAS, model `BK-BLE-1.0`, fw 6.1.2, sw 6.3.0, hw 1.0.0, serial `1.0.0.0-LE`
- Services: 180A (devinfo), 1801, 1800, **FE00 (WiLink)**, `f000ffc0-0451-4000-b000-…` (TI-style OAD/OTA)
  - FE01 [read, notify] — status stream
  - FE02 [write-without-response] — commands
- **FTMS 0x1826 ABSENT** → C2 is WiLink-only. FTMS libs are irrelevant for this unit.
- No pairing/bonding required; control is unauthenticated.
- OTA service exposed unauthenticated — DO NOT touch; document as a known device property.

## Runtime (measured)
- BlueZ 5.87, Realtek 0bda:2852, adapter <adapter-addr>
- Quickshell.Bluetooth: manages devices only (connect/disconnect/pair/forget/trusted/dbusPath).
  **ZERO GATT/characteristic/UUID surface.** No generic D-Bus module.
- Quickshell.Io provides Process, Socket, SocketServer, DataStreamParser, SplitParser.
- → seam: QML owns discovery/adoption/presence; external helper owns GATT only.

## Constraints
- ONE BLE connection at a time → phone app and helper are mutually exclusive.
- BarPanel is instantiated **once per screen** (3 here) → helper MUST be owned by a
  manifest `service` kind and looked up through `bar?.shell?.serviceFor(<id>)`,
  never by the widget. Otherwise 3 helpers, 1 slot.
- Marketplace disqualifiers (own vetting rule): binaries, sudoers/polkit, daemons.
  → source-only Python, child process of the shell, no systemd unit, no root.
- Plugin manifest v1: id/name/version/author/description/kinds/entryPoints,
  barWidget{displayName,description,category,allowMultiple}, keepLoaded.
- Marketplace registry: 1161 sources, **zero** treadmill/fitness/walkingpad plugins.

## Protocol (from ph4-walkingpad; TO BE VERIFIED against fw 6.1.2)
- Frame: `F7 … <crc> FD`, crc = sum(bytes[1:-2]) % 256
- ask status `F7 A2 00 00 A2 FD` | speed `F7 A2 01 <0.1km/h> <crc> FD`
- mode `F7 A2 02 <mode> <crc> FD` | start `F7 A2 04 01 <crc> FD`
- status frames head `F8 A2`: [2]=belt state [3]=speed [4]=mode [5:8]=time [8:11]=dist [11:14]=steps

## VERIFIED against fw 6.1.2 (2026-08-24, live, belt running)
- Discovery **by name prefix `KS-`** works — no hardcoded MAC needed. Also matched on FE00 in adv.
- Status frame is **20 bytes**, header `F8 A2`, terminator `FD`, CRC `sum(d[1:-2])%256` — **verified True**.
- Documented ph4 offsets hold EXACTLY:
      f8 a2 01 19 01 00 03 f0 00 00 45 00 05 22 00 00 02 00 1e fd
      [2]=01 belt  [3]=0x19=25 → 2.5 km/h  [4]=01 manual
      [5:8]=1008 s  [8:11]=0x45=69 → 0.69 km  [11:14]=0x0522=1314 steps
      [14:18]=00 00 02 00 (undocumented; [16]=02 constant)  [18]=crc  [19]=FD
- **belt=1 means RUNNING** (speed 2.5, steps advancing). My "starting" guess was wrong — remap.
- ⚠ **STATUS IS POLL-DRIVEN, NOT PUSH.** 4 s of silence with FE01 subscribed and zero frames;
  exactly one frame per `F7 A2 00 00 A2 FD` query. → helper MUST run its own ~1 Hz poll loop;
  a purely event-driven QML design would show a frozen bar.
- Query opcode is safe: sent 4×, belt state and speed unchanged.

## Safety (learned the hard way)
- The pad was IN USE while probing. A speed/stop write would have hit a walking human.
- → plugin MUST treat "belt running" as a guarded state; and any spike that writes
  commands requires the user to be OFF the belt. Confirm every time, never infer.

## WRITE PATH VERIFIED (2026-08-24, live, user on the belt)
- `F7 A2 01 <tenths> <crc> FD` works. 2.5 → 2.0 → 2.5, restore confirmed.
- ⚠ **THE PAD RAMPS, IT DOES NOT JUMP.** Measured ~0.25 km/h per second:
      down: 2.5 → 2.3 → 2.0   (~2 s)      up: 2.0 → 2.3 → 2.5   (~2 s)
- ⚠ **Reported speed is ACTUAL, not TARGET.** There is no ACK and no target field.
  Consequences for the UI:
    * service must track `commandedSpeed` separately from `currentSpeed`
    * during a ramp the bar must show `2.0→2.5`, else a click looks like it failed
    * rapid clicks MUST be debounced/coalesced into one target, or writes fight the ramp
- Command→first visible movement ≈ 1–1.5 s (poll granularity 1 s, so upper bound).
- Writing speed while belt already running in manual mode needs no START first.

## Q6 / Q2 attempt (2026-08-24) — BLOCKED, pad asleep
- Neither question closed. Recording what was learned anyway.

### NEW FINDING — the pad auto-sleeps and STOPS ADVERTISING
- After the user stepped off and the belt stopped, a 15 s discovery scan found
  **nothing** — no `KS-BLC2`, no `<pad-addr>`. Earlier scans found it in <2 s.
- → "device absent" is the pad's NORMAL RESTING STATE, not an error condition.
- → BLE cannot wake it. Waking needs physical interaction (panel/remote).
- → Consequences for the plugin:
    * the bar's dormant state is the COMMON case, and must look deliberate, not broken
    * `autoConnect` must be a low-frequency presence check, never a hot retry loop
    * a "cannot connect" toast on every failure would fire constantly — suppress it
    * §8's 30 s backoff + dormant glyph was the right call, now evidence-backed

### TOOLING LESSONS (not protocol facts)
- `bluetoothctl <cmd>` without `--timeout` hangs: it still enters its REPL and
  blocks on stdin. Always `bluetoothctl --timeout N` with stdin closed.
- ⚠ **`BleakClient(<address>)` requires the device to already be in BlueZ's cache.**
  A bare address with no prior scan fails with "device was not found" — which
  looks exactly like a contention failure but is not. The bridge MUST scan
  (`BleakScanner.find_device_by_filter`) before connecting, every time.
  This produced a false "Q6 = CONTEND" verdict that was wrong.

## Gio/BlueZ transport verified (2026-08-24, live, belt idle)
- Talking to BlueZ over `gi.repository.Gio` reaches the pad end to end: discovery,
  `Device1.Connect`, `ServicesResolved`, characteristic lookup, `StartNotify`,
  `WriteValue{type=command}`, `Disconnect`. Six polls produced six frames.
- **Q6 IS DISSOLVED, not answered.** The question assumed two competing stacks.
  bleak talks to BlueZ over this same D-Bus API, so there was never a second
  stack — using Gio directly just removes the wrapper. There is nothing to contend.
- ⚠ **A cached BlueZ device object is NOT evidence the pad is reachable.** BlueZ
  keeps `/org/bluez/hci0/dev_*` long after a device stops advertising (the paired
  WH-1000XM4 sits there permanently with `RSSI: None`). Discovery populates RSSI
  from live advertisements and drops it when they stop, so **RSSI presence is the
  reachability test**. Matching on name/UUID alone would make a sleeping pad look
  present and every connect attempt fail.

### NEW PROTOCOL FACT — belt == 4
      f8 a2 04 00 02 00 00 00 00 00 00 00 00 00 00 00 00 00 a8 fd
- Awake at the panel, nobody on the belt: `[2]=04`, speed 0.0, mode 02 standby,
  every counter zero. 20 bytes, CRC valid — a normal frame, not corruption.
- → **belt == 4 is the awake-and-idle state**, and it is the state the pad is in
  most of the time it is reachable at all. Mapped as `"idle"`; deliberately not
  "standby", which is already a MODE name.
- `belt == 0` is documented upstream as "stopped" but has **never been observed
  here**. Do not assume 0 and 4 are interchangeable.
- Only `belt == "running"` means the belt is moving. Gate on that, never on the
  absence of some other value, so an unmapped state fails safe.

## Bridge run against real hardware (2026-08-24)
- Full path works: scan → connect → resolve → StartNotify → 1 Hz poll → clean
  teardown. Nine polls, nine frames, one per query. **Fact 1 holds: nothing is
  ever volunteered.**

### ⚠ BlueZ `Connected` IS NOT PROOF OF A WORKING LINK
- After a process was SIGKILLed, BlueZ still reported `Connected=True` for a
  radio link that was already gone. `Device1.Connect` on it returned success,
  `ServicesResolved` was true, characteristics resolved, `StartNotify` succeeded
  and every `WriteValue` returned without error — **into nothing.** Zero frames.
- The write blocked ~2.5 s each time, which is the only outward sign at all.
- → the bridge cannot trust the `Connected` property. A **recent status frame is
  the only evidence of a live link**, which is the same reason the safety gate
  exists. Silence for 15 s now tears the link down and rescans.
- → seed that watchdog from the CONNECT time, never by crediting a fresh
  connection as though a frame had arrived — that would open the safety gate on
  a link that has never said anything.

### ⚠ Python signal handlers do not run under a GLib main loop
- `g_main_loop_run()` blocks in C, so Python never reaches its SIGINT handler,
  and a KeyboardInterrupt raised inside a GLib callback is swallowed rather than
  unwinding the loop. **Measured: Ctrl-C did nothing and the bridge polled on.**
- → register `GLib.unix_signal_add` sources for SIGINT and SIGTERM. Both then
  exit rc=0 and disconnect cleanly.
- → `GLib.unix_signal_add` also REPLACES Python's SIGINT handler, so no
  KeyboardInterrupt is raised anywhere afterwards.
- → SIGTERM matters as much as SIGINT: Quickshell terminates child processes,
  and an ungraceful kill leaves the phantom link described above.
- The `GLib.unix_signal_add` deprecation warning is not printed in practice:
  it is a DeprecationWarning raised from an imported module, which the default
  filter suppresses. The non-deprecated `GLibUnix.signal_add` needs a much newer
  gobject-introspection, so the compatible call is the right one to ship.

## Full command path measured (2026-08-24, live, operator clear of the belt)

### ✅ Q2 ANSWERED — an explicit START **is** required
- On a stopped belt in manual mode, `F7 A2 01 <tenths>` alone **does not start it.**
  Verified twice, ten seconds of polling each time: belt stayed `00`, speed `0.0`.
  The command is not lost — byte [14] updates — but the belt does not move.
- `F7 A2 04 01` (START) starts it. Sequence for the QML side is therefore
  **START first, then set the speed.**

### ⚠⚠ THE PAD STARTS AT ~3.5 km/h REGARDLESS OF THE SET SPEED
- With byte [14] holding 1.0, then again holding 3.0, START ramped the belt to
  **3.5 km/h** both times and held there. Reproduced a third time from the panel.
- → **a naive "start" button puts 3.5 km/h under the user's feet.** This is the
  single most important safety fact for the widget. The per-command 0.5 clamp does
  NOT cover it: the clamp governs speed changes, and this is the pad's own start.
- Ramp up measured ~0.65 km/h/s (0 → 3.5 in ~5 s); ramp down after STOP ~0.5
  km/h/s. Both faster than the 0.25 km/h/s measured earlier for a small change.
- There IS a usable window: after START the belt reports `belt=1, speed=0.0` for
  about two seconds before it accelerates. A speed command lands there.

### Belt states, completed
      9, 8, 7  = the panel's 3-2-1 startup countdown, ~1 s each  (decoded "unknown" before)
      1        = running
      0        = stopped  (CONFIRMED by observation, not just upstream docs)
      4        = awake and idle, reported while mode == standby
- Sequence observed: `00 → 09 → 08 → 07 → 01 → (ramp) → 01 → 00` after STOP.

### Byte [14] — the last BLE-commanded speed, NOT a target
- Echoes speed commands exactly: `0a`→1.0, `0f`→1.5, `1e`→3.0, `14`→2.0. Persists
  across disconnects. A running belt converges to it when a new command is sent
  (3.5 → 3.3 → 2.6 → 1.9 → 1.5, then held).
- ⚠ **It reads 0.0 when the speed was set at the panel** — every frame in the very
  first capture session shows [14]=00 while the belt ran at 2.5 km/h. And the pad
  ignores it at start. **Never render it as a target.** This corrects the earlier
  claim above that the protocol has no target field: it has this, and this is not
  a reliable one.
- [16] is not constant either: 0x02 in the first captures, 0x03 once started,
  0x00 in standby. [15] and [17] stayed 0x00 throughout.

### ⚠ standby ignores everything, and BLE cannot leave it
- With `mode == standby` (belt 4), the pad answers status queries normally and
  **silently ignores START, STOP and speed alike.** 40 s of commands, no change.
- `F7 A2 02 01` (mode → manual) **is also ignored.** BLE cannot leave standby.
- → only the panel's **M button** switches it to manual. The UI must say so by
  name rather than appearing broken.

### ⚠ a frozen stale snapshot can survive an abrupt link loss
- After notifications stopped mid-session, the next connection served eight
  consecutive byte-identical frames reading `belt=01, speed=3.5` **while the belt
  was physically stopped.** Counters were frozen with them.
- The operator confirmed every STOP had in fact worked, so this was a stale
  readout and NOT a failed stop. An earlier reading of this log said otherwise;
  it was wrong.
- → do not build a "did the stop take effect?" watchdog on reported speed. It
  would fire falsely on exactly this data.
- → open: how long the stale snapshot persists and what clears it. Seen once.

## ⚠⚠ CONFIRMED: `F7 A2 04 00` IS A TOGGLE — IT STARTS A STOPPED BELT (2026-08-25)

**This is the most important fact in this document.** Controlled test, operator
clear of the pad, hands off panel and remote:

      phase 1, 10 s, NOTHING sent   belt held at raw=0x00 stopped, mode manual
      phase 2, ONE frame sent       F7 A2 04 00
      the very next frame           raw=0x09  -> 08 -> 07 -> 01  (start countdown)
      then                          ramped to 3.5 km/h, nobody on the pad

The control phase rules out the pad starting on its own, and rules out the act of
connecting as the trigger. One frame, immediate countdown.

- On a **running** belt the same opcode stops it (wind-down measured repeatedly).
- On a **stopped** belt it starts it.
- So it is a run-state toggle, not a stop.

### This inverted the safety model
The rule "stop is always permitted, including on a stale link" was written into
the spec, AGENTS.md and the tests. It was the single most dangerous line in the
design: **without a recent frame we do not know which way the toggle goes, and
half the outcomes start a treadmill.** `Safety.may_stop()` now requires recent
evidence that the belt is actually moving, and refuses otherwise, telling the
user to use the panel or remote — which always work.

Stopping IS still permitted during the `starting` countdown. That is the moment
it matters most: the belt is about to move and nobody may be ready for it.

### Two earlier explanations, both wrong, both recorded rather than deleted
- *"The pad auto-starts when someone stands on it."* WRONG. The operator states
  plainly it does not; the belt starts from a command, the remote, or auto mode.
  This log recorded an inference as a measurement, which is exactly what it
  exists to prevent.
- *"STOP was being ignored."* WRONG. It was a stale frozen snapshot; the operator
  confirmed every stop had worked. Do not conclude a command failed from reported
  speed alone.

### Follow-up worth doing
**STILL OPEN AND UNANSWERED.** Test whether `F7 A2 01 00` (set speed 0) stops a
running belt and is inert on a stopped belt. The operator procedure is
[Hardware smoke test §4, “Optional open question — can set-speed 0 stop
safely?”](smoke-test.md#4-optional-open-question--can-set-speed-0-stop-safely).
No conclusion follows until an operator runs both halves of that hardware
procedure and records the observed state sequences. Its `repeat_suppressed`
caveat is a bridge stdout reading hazard caused by command-name suppression, not
a pad or protocol behaviour and not a new hardware fact.

### Why STOP is still exempt from repeat-suppression
- No ACK exists, so a dropped write is invisible, and a second press is the only
  recovery a person has. It is gated on evidence of motion, never throttled.

## Pad returns to standby on its own
- Observed 2026-08-25: a pad left connected and idle drops back to `standby`
  while still advertising and answering polls. Distinct from sleeping, where it
  stops advertising entirely. A connected, healthy-looking link can therefore be
  uncontrollable, and only the panel's M button restores manual mode.

## ⚠ The pad stops ANSWERING at 29.2 s. The link is healthy. (2026-08-27)

**This section replaces "Link cut off every ~29 s", written earlier the same day.
That heading was wrong and is retracted.** Nothing cuts the link. The radio stays
up, the pad keeps acknowledging every packet, and its application layer simply
stops replying. The morning entry reasoned from the bridge's own stdout, which
cannot tell those apart; the afternoon measured the radio with `btmon` and they
are not the same fault at all.

### What the HCI trace shows

A 180 s `btmon` capture over three connection cycles, 16:13–16:16, decoded with
`btmon -r`:

- **Our poll frame keeps going out during the silence.** `f7 a2 00 00 a2 fd` to
  FE02 (handle `0x000c`) every 2 s, exactly as the poll loop intends.
- **The pad accepts every one of them.** The controller raises `Number of
  Completed Packets` for each write, at 3–13 ms latency, with no retransmits and
  no errors. The peer is taking our packets off the air and acknowledging them.
- **The pad sends nothing back.** Not one peer-originated packet in an 18 s
  window — no notification, no ATT error, no L2CAP request.
- **`Disconnect Complete` reason is `0x16`, Connection Terminated By Local Host**,
  on all three cycles. That is our own D-4 watchdog at 15 s. It is the effect, not
  the cause, and reading it as "something disconnected us" inverts the causality.
- **Connection parameters are healthy:** interval 11.25 ms, latency 0, supervision
  timeout 6000 ms.

At 1 m distance with every packet acknowledged, **range and 2.4 GHz interference
are ruled out by measurement, not by argument.**

### The window is a timer, to a tenth of a second

Twelve consecutive connections, 16:25–16:34, measured from the first status reply
to the last:

| cycles | answered | belt |
|---|---|---|
| 12 of 12 | **29.1 – 29.2 s** | running in 10, stopped in 2 |

Twelve identical windows spanning a tenth of a second is a firmware timer. **Belt
state does not affect it**: the pad mutes while running exactly as it mutes while
idle. An earlier reading of this session claimed ~78 s while running; that came
from eyeballing filtered output and is superseded by the table above.

### It is still a condition, not a pad property

`bouts.jsonl` holds a bout from 2026-08-26 of `dur_s` 1988 with `n` 2000: one
unbroken link carrying ~1 Hz frames for 33 minutes, same pad, same firmware, same
code, and a bout closes on link loss so it cannot span one. Today every bout is
26–30 s, chopped by the mute.

So the pad answered indefinitely one day and mutes at 29.2 s the next, and
**nothing here explains why**. Design spec §11 open question 9 records it.
**No fact in `AGENTS.md` follows from this**, and it must not be treated as an
invariant — see the retraction at the top of this section for what happens when a
symptom is promoted to a cause too early.

### What it is not, each checked rather than assumed

- **Not belt state.** Measured on both sides, 12 cycles.
- **Not our commands.** Cycles that sent none behave identically.
- **Not the poll cadence.** ~29 s at both the 1.0 s and the 2.0 s poll interval.
- **Not adapter state.** A full `bluetoothctl power off` / `power on` changed
  nothing.
- **Not contention.** The plugin was disabled throughout; the bridge was the only
  client and the pad the only connected BLE device.
- **Not a host software change.** No kernel, `bluez` or `linux-firmware` upgrade
  since 2026-08-17, well before the 33-minute session above.
- **Not the radio.** The `btmon` evidence above.

`journalctl -k` logged `Bluetooth: hci0: ACL packet for unknown connection handle
16` at every reconnect — the controller delivering on a handle the host had
already torn down, which is a consequence of our teardown, not a cause.

### How it made the morning's §4b run inconclusive

One `{"cmd":"start","kmh":2.0}` at 14:40:00 produced two `belt: "starting"` frames
and then silence, because the start landed near the end of an answered window. The
watchdog tore down, the rescan reconnected, and the belt was already `running` at
3.5 km/h. `start_unconfirmed` fired correctly: `START_SPEED_WINDOW_S` (8 s) expired
with no `running` frame, so **no speed was ever sent**.

The belt therefore sat at the pad's own ~3.5 km/h — the outcome §4b names as the
hazard — **without the two-part start ever being exercised.** Reading that as "the
sequencing does not work on this firmware" would have been wrong, and it is the
reading the raw symptom invites.

The precondition this produced in smoke-test §4b was itself wrong on the first
attempt: "the link must hold for 60 unbroken seconds" can never pass against a
29.2 s window, so it would have blocked §4b permanently. The correct rule, and the
one now in the procedure, is **send the start inside the answered window** — in
practice within the first few seconds of a fresh connection.

### What behaved correctly under it, and is worth keeping

- **The D-4 watchdog recovered every time**, unaided, with no retry storm and no
  operator action, across more than twenty cycles.
- **`Safety.may_stop()` refused a stop on a stopped belt**, on hardware, with the
  `stop_unsafe` code. Fact 16's gate, live.
- **The residual model did exactly what it claims.** `last_pad` 65 s / 0.05 km /
  90 steps against `attributed` 29 s / 0.03 km / 43 steps — a residual of 36 s /
  0.02 km / 47 steps, precisely the walking lost to the mute windows. It detected
  and quantified lost frames, which accumulating our own totals from observed
  deltas could never have done.

### External corroboration — a different protocol, and NOT verified here

Read from the `walkingpad-controller` source (`mcdax/walkingpad-controller`),
`docs/ftms-protocol-reference.md`, and `ph4-walkingpad`. Recorded because it bears
on the start sequencing, not because it settles anything.

- **§2.6, from btsnoop captures of the vendor app:** `START_OR_RESUME` followed too
  quickly by `SET_TARGET_SPEED` *crashes the BLE link*, and the vendor app waits
  for the first non-zero speed in the notification stream before sending any
  target. That is the same shape as our two-part start, arrived at independently.
- **§2.7:** no keepalive or heartbeat frame appears in any of their four captures.
  **Nobody in the ecosystem has a keepalive**; detect-and-reconnect is the
  universal answer, which is what D-4 already does. Note this pad's mute is not
  something a keepalive would have prevented: it acknowledges our packets
  throughout, so the link never needed keeping alive.
- Their equivalent of `START_SPEED_WINDOW_S` allows **15 s**; ours allows 8 s.
  **Settled on hardware 2026-08-27: 8 s is enough.** Three §4b runs reached
  `belt: "running"` in 4.00, 4.03 and 4.00 s. Do not widen it.
- Their connect path retries 5 times at 3 s intervals, because the firmware "can be
  left in a bad state for several seconds after a previous abrupt disconnect".
- `ph4-walkingpad` enforces a `minimal_cmd_space` of 0.69 s between writes. Our
  1.0 s poll is already above that floor.

⚠ All of the above is **FTMS / 0x1826**. This C2 speaks WiLink FE00 and has no
FTMS service at all. Suggestive, not transferable, and not evidence about this pad.

### A power cycle does not clear it, and four more causes are eliminated (2026-08-27)

The pad was switched off at its own power switch and back on. The mute survived
it. Measured immediately afterwards, over 200 s and four connections:

| connection | answered for | then |
|---|---|---|
| 1 | never answered | rescan after 14.0 s |
| 2 | **29.04 s** | link lost at +46.1 s |
| 3 | **28.25 s** | link lost at +44.1 s |
| 4 | **28.32 s** | link lost at +44.1 s |

Same window as before the power cycle, so nothing the pad holds across a reboot
of its own control board is responsible. Whatever arms the timer is re-armed on
every fresh connection.

**Four causes eliminated, each by measurement rather than argument.**

1. **Our BLE code.** `git diff 8e08ce0..HEAD -- walkingpad/transport.py
   walkingpad/bridge.py` is a comment-only edit to one docstring. The session
   that answered indefinitely ran the same bytes as the sessions that mute.
2. **The host.** No reboot since 2026-08-26 01:58, which is before the good
   session. Same kernel throughout.
3. **BlueZ.** `bluetoothd` has been one continuous process for 39 h, spanning
   both the good session and every muting one.
4. **A second BLE client.** Two bridge processes were found running at once —
   the plugin's, spawned by quickshell, and a manual one. Contention is real and
   has its own consequences (below), but it is **not** the trigger: the store's
   19–20 s sample gaps begin at 14:40:02, and the plugin's bridge did not start
   until 16:36:53. A single-writer window shows the mute with no second client
   present at all.

That leaves the pad's own firmware, re-arming per connection, with no input from
anything on this machine that we have been able to vary.

**The gap fingerprint, and its one limit.** A mute leaves a hole of roughly
19–20 s between consecutive stored samples: the 15 s watchdog plus rescan and
reconnect. That makes past sessions classifiable from the store alone, with no
capture and no hardware.

⚠ It only sees mutes **during a walk**. Design §2.2 records samples only for
`starting`/`running` frames plus a short tail, so an idle pad writes nothing and
a long sample gap across a stop is ordinary. A gap is evidence of a mute only
when the belt was running on both sides of it. The bridge's own
`{"t":"link","state":"lost"}` carries `answered_s` for exactly this reason and is
the unambiguous record.

### Two bridges do not fail. They silently share the link. (2026-08-27)

Found while measuring the above: `omarchy plugin`'s installed copy is spawned by
quickshell and runs permanently, so any manually started bridge is a **second**
BLE client. AGENTS.md fact 5 says only one connection to the pad exists at a
time, and that remains true — but it is BlueZ that owns it, and two D-Bus clients
share that one link instead of the second being refused. Both poll it, and BlueZ
broadcasts every notification to both.

Consequences worth knowing before trusting any capture:

- Killing the plugin's bridge does not give exclusivity. quickshell respawned it
  in **3 s**, measured.
- A manual bridge run alongside the plugin's doubles the poll rate the pad sees.
- Frames arrive on two interleaved cadences, which reads as duplication.

### ⚠ The window is not always 29 s (2026-08-27, later the same evening)

"To a tenth of a second" above described twelve cycles that agreed. It does not
generalise. With `answered_s` now reported on every link loss, a run measured
**23.08 s** while a second bridge was also polling. The four post-power-cycle
connections gave 29.04, 28.25 and 28.32 s under the same contention, so the
second client is not a clean explanation either.

What survives: the pad answers for roughly half a minute and then goes quiet.
What does not: any claim that the number is fixed. Do not treat 29.2 s as a
constant to design against; read `answered_s` from the log instead.
