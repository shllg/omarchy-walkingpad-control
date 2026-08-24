# Hardware smoke test

⚠ **Part 3 MOVES THE BELT, and the pad starts at ~3.5 km/h no matter what speed
you set.** Stand clear of the pad before starting it. Do not run this while anyone
is on the belt.

## 1. Prerequisites

The pad sleeps and stops advertising when idle, and **BLE cannot wake it.** Wake it
at the panel. `{"t":"link","state":"absent"}` forever means it is asleep — that is
the normal resting state, not a fault.

**The pad must be in manual mode, not standby.** In standby it answers status
queries but silently ignores START, STOP and speed alike, and the BLE mode-switch
command is ignored too. **Only the panel's M button leaves standby.** The bridge
now refuses motion commands in that state and says so:

    {"t":"error","code":"standby","msg":"pad is in standby and ignores commands; …"}

Only one BLE connection to the pad exists at a time, so close the phone app first.

## 2. Read-only checks

1. Wake the pad, press **M** so `mode` reads `manual`.
2. Run `./bin/walkingpad-bridge`.
3. Expect within ~10 s:

       {"t": "link", "state": "scanning"}
       {"t": "link", "state": "connected", "addr": "…", "name": "KS-BLC2"}
       {"t": "status", "belt": "stopped", "speed": 0.0, "target": null, …}

   then one `status` line per second. `target` is `null` until you command
   something — the bridge does not invent one. A stopped belt in manual mode
   reports `belt: "stopped"`; an untouched pad in standby reports `belt: "idle"`.
4. Type `{"cmd":"explode"}`. Expect **no output at all** — an unknown command is
   ignored silently, not reported as a refusal.

## 3. Movement checks — stand clear of the belt

5. Type `{"cmd":"speed","kmh":1.0}` on the stopped belt. **Expect nothing to
   happen.** The belt does not move and `pad_set_speed` becomes 0.5 (the command
   is clamped to one step from 0.0). A speed command alone never starts a stopped
   belt — this is the answer to Q2.
6. Type `{"cmd":"start"}`. Expect `belt` to run `"starting"` for about three
   seconds — that is the panel's 3-2-1 countdown — then `"running"`, and the speed
   to ramp to **~3.5 km/h**, *not* to the speed you set in step 5. The pad ignores
   its stored speed when starting. This is the hazard the widget must design
   around, not a bug.
7. Type `{"cmd":"speed","kmh":2.5}` **repeatedly**, a few seconds apart, and watch
   `speed` come down one 0.5 step per command: 3.5 → 3.0 → 2.5. One step per
   command is the whole point — a person may be standing on the belt. Reaching a
   target takes several commands, which is the service's job in Stage 2.
8. Type `{"cmd":"speed","kmh":6.0}`. Expect `target` to move one step above the
   current speed, **never to 6.0.**
9. Type `{"cmd":"stop"}`. Expect the belt to wind down to 0.0 over ~5 s and `belt`
   to return to `"stopped"`.
10. Press Ctrl-C. Confirm the process exits and the pad disconnects:

        bluetoothctl --timeout 3 info <pad-addr> </dev/null | grep Connected

## Known traps when reading the output

- **A stale snapshot can survive an abrupt link loss.** Seen once: after
  notifications stopped mid-session, the next connection served eight identical
  frames reading `belt=01, speed=3.5` while the belt was physically stopped. If
  the numbers are frozen *and* `elapsed` is not advancing, distrust them and
  reconnect. Do not conclude a command was ignored from reported speed alone.
- **Do not kill the bridge with SIGKILL.** BlueZ then keeps reporting a link that
  is already dead. The bridge recovers on its own — it adopts the stale link, sees
  no frames for 15 s, tears it down and rescans — but expect one slow cycle.
