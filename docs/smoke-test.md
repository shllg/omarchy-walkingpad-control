# Hardware smoke test

⚠ **Steps 5 and later MOVE THE BELT. Stand clear of the pad before starting them.**
Do not run this while anyone is on the belt.

## Before you start

The pad sleeps and stops advertising when idle, and **BLE cannot wake it**. Wake it
at the panel first. If the bridge reports `{"t":"link","state":"absent"}` forever,
the pad is asleep — that is the normal resting state, not a fault.

Only one BLE connection to the pad exists at a time. Close the phone app first.

## Read-only checks

1. Wake the pad at its panel.
2. Run `./bin/walkingpad-bridge`.
3. Expect within ~10 s:

       {"t": "link", "state": "scanning"}
       {"t": "link", "state": "connected", "addr": "…", "name": "KS-BLC2"}
       {"t": "status", "belt": "idle", "speed": 0.0, …}

   then one `status` line per second. An untouched pad reports `belt: "idle"`,
   `mode: "standby"` and zero counters. Step on it and confirm `steps` and `time`
   advance and `belt` becomes `"running"`.
4. Type `{"cmd":"explode"}`. Expect **no output at all** — an unknown command is
   ignored silently, not reported as a refusal.

## Movement checks — stand clear

5. Type `{"cmd":"speed","kmh":1.0}`. Expect the belt to reach 1.0 km/h over a few
   seconds, passing through intermediate values. **This ramp is expected, not a
   fault**: the pad accelerates at ~0.25 km/h per second and reports its ACTUAL
   speed, never the target. Compare `speed` against `target` in the status lines.
6. Type `{"cmd":"speed","kmh":6.0}`. Expect it to clamp to **1.5**, not jump to 6.0.
   One step per command is the whole point — a person may be standing on the belt.
7. Type `{"cmd":"stop"}`. Expect the belt to wind down to 0.0.
8. Press Ctrl-C. Confirm the process exits and the pad disconnects. Confirm with:

       bluetoothctl --timeout 3 info <pad-addr> </dev/null | grep Connected

   ⚠ If the bridge is ever killed with `SIGKILL`, BlueZ can keep reporting
   `Connected: yes` for a link that is already dead. The bridge recovers on its
   own — it adopts the stale link, sees no status frames for 15 s, tears it down
   and rescans — but expect one slow cycle when that happens.

## Still open: Q2, cold start

Unanswered, and this is the cheapest place to answer it. On a **stopped but awake**
belt, with nobody on the pad, send `{"cmd":"speed","kmh":1.0}` **without** sending
`{"cmd":"start"}` first.

- If the belt starts, no explicit start is needed and Q2 is closed.
- If nothing happens, send `{"cmd":"start"}` and then the speed command.

Record the answer in `docs/protocol-evidence.md`. It decides whether the QML side
has to issue `start` before its first speed change, or can simply set a speed.
