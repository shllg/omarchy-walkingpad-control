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
  `pragma Singleton` service, never by the widget. Otherwise 3 helpers, 1 slot.
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

## ⚠⚠ UNRESOLVED: a STOP sent to a stopped belt may START it (2026-08-24)

**Status: hypothesis, decisive test pending. Do not build on either answer yet.**

Two observations, both of which are a STOP arriving at a belt that was already
stopped, followed by the pad running its 3-2-1 start countdown:

1. A script re-sending STOP several times a second put the pad in a loop:
   countdown, beep, cancel, double beep, countdown again, for as long as it ran.
2. The bridge connected to a belt reporting `stopped` at `time=476`. Exactly one
   STOP was sent (two more were suppressed). Four frames later: `starting`, then
   `running` at 1.5 -> 1.7 km/h.

The counter-case is consistent rather than contradictory: one STOP to a RUNNING
belt wound it down to 0.0 and it stayed there — but no further STOP followed.

### Two explanations were considered and BOTH were withdrawn
- *"`A2 04 00` is a toggle"* — plausible, still unconfirmed. See below.
- *"The pad auto-starts when it detects someone standing on it"* — **WRONG.** It
  was briefly written into this log and the spec on 2026-08-24 and is retracted
  here. The operator states plainly that the belt does not start on load: it
  starts only from a command, the remote, or auto mode. This log recorded an
  inference as a measurement, which is exactly what it exists to prevent.

### The test that settles it
Belt confirmed stopped, nobody on the pad, nobody touching panel or remote. Send
exactly ONE `F7 A2 04 00` and watch. If a countdown follows, the hypothesis holds
and the safety model below has to change. One further STOP then stops it.

### Until it is settled
Treat "send stop to a belt not known to be moving" as **unsafe**, because one of
the two live possibilities is that it starts the belt. This directly contradicts
the standing rule that stop is always permitted, so the rule is provisional until
this is resolved.

### Why STOP is exempt from repeat-suppression (unaffected by the above)
- There is no ACK anywhere in this protocol, so a dropped write is invisible.
- A person pressing stop a second time because nothing happened is the ONLY
  recovery available to them. Swallowing that retry leaves them on a moving belt.
