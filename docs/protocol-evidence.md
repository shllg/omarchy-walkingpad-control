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
