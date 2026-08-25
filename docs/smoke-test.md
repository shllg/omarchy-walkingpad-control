# Hardware smoke test

⚠ **Parts 3 and 4 MOVE THE BELT, and the pad starts at ~3.5 km/h no matter what
speed you set.** Stand clear of the pad before starting it. Do not run this while
anyone is on the belt.

## 0. A stopped pad is still live

⚠ The belt starts from a command, the remote, or auto mode — never on its own.
But the stop opcode (`F7 A2 04 00`) is a **toggle**: sent to a stopped belt, it
starts it. A stopped-but-awake pad in manual mode is a live machine. Stand clear
for every step below, and do not treat `belt: "stopped"` as "safe to step on".

Corollary for anyone scripting against the bridge: **never re-send a command on a
timer.** A retry loop fights the stop opcode's toggle behaviour and produces an
endless countdown-cancel-countdown cycle, beeping continuously. Send one command
per action and read the status stream for the result.

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

## 4. Optional open question — can set-speed 0 stop safely?

This procedure gathers evidence for an open question. It does not assume or
predict the answer. No new code or raw-frame tool is needed: the existing bridge
command `{"cmd":"speed","kmh":0}` sends exactly `f7 a2 01 00 a3 fd` when the
last-reported actual speed is either 0.5 km/h or 0.0 km/h. From 0.5 km/h the
per-action clamp reaches 0.0; from 0.0 km/h it stays at 0.0.

⚠ The bridge repeat-suppresses `speed` and `start` by command name AND requested
value; `stop` is explicitly exempt. Two *identical* requests less than 1.0 second
apart collide; two different speeds no longer do. (It keyed on the name alone
until 2026-08-25, which silently dropped genuine speed changes.) After every
command in both halves, check bridge stdout for
`{"t":"error","code":"repeat_suppressed",...}` before recording anything. If
that line appears, the command was never sent to the pad and says nothing about
the pad's behaviour. Wait at least 1.0 second, re-send that single command once,
then record the result. This is one command per action, not a retry loop.

### Half A — does it stop a running belt?

11. Satisfy §1: wake the pad, confirm `mode` reads `manual`, and close the phone
    app. Run `./bin/walkingpad-bridge` as in §2 if it is not already running,
    then wait for a current status line. Stand clear of the belt.
12. If the belt is not already running, start it with §3 steps 5–6. Wait for
    `belt` to read `"running"`.
13. Walk the reported speed down by 0.5 km/h per command. After each `speed`
    value settles, send one request 0.5 km/h below it, then wait a few seconds
    before the next action. After every request, check stdout before recording or
    continuing. If `repeat_suppressed` appears, follow the rule above; an
    unchanged speed is not evidence about the pad. Follow §0: never put these
    commands on a timer. Stop when the status reads `belt: "running"` and
    `speed: 0.5`.
14. Record the `belt` and `speed` values immediately before the test command.
15. Type `{"cmd":"speed","kmh":0}` **exactly once**, then check stdout as
    described above before recording its result.
16. Record the full sequence of `belt` and `speed` values afterward until the
    belt is at rest or clearly still moving. Record whether it winds down to 0.0
    like the existing stop opcode, or does something else.

### ⚠⚠ Half B — is it inert on a stopped belt?

⚠⚠ **This is the dangerous half.** If `F7 A2 01 00` is also a toggle, sending it
to a stopped belt will START it. The pad then starts at ~3.5 km/h regardless of
its stored speed, when nobody is expecting the ramp-up. Stand clear, with the
panel or remote within reach, before sending anything in this half.

17. Confirm the belt is stopped, the pad is awake, `mode` reads `manual`, and the
    phone app is closed. Do not continue from a moving belt.
18. Record the `belt` and `speed` values immediately before the test command.
19. Type `{"cmd":"speed","kmh":0}` **exactly once. Do not repeat it** unless
    stdout shows `repeat_suppressed`. Check stdout immediately. If that line
    appears, the test frame never reached the pad: wait at least 1.0 second and
    re-send this single test command once before recording any behaviour.
20. Record the full sequence of `belt` and `speed` values afterward for at least
    10 seconds, or through any complete startup sequence. Watch specifically for
    the 9/8/7 countdown states, which the bridge reports as `belt: "starting"`.
    Their appearance means the command toggled and started the belt, exactly like
    the existing toggle. If the belt stays stopped, record that sequence too.

If the belt starts, treat it exactly like an unwanted start from the existing
toggle: stand clear, let it run or use the panel or remote to stop it. Do not send
any further `speed` or `stop` command from the bridge to cancel it. That retry
pattern is already known to produce a countdown-cancel-countdown cycle (§0).

### Decision rule

Use this primitive in place of the toggle behind `Safety.may_stop()` only if Half
A stops the running belt **and** Half B leaves the stopped belt stopped. If either
half fails — it does not stop a running belt, or it starts a stopped one — the
current `Safety.may_stop()` gate stays exactly as it is, unmodified. This
procedure does not trigger a code change; any replacement requires a separate
decision after the evidence is recorded. A Half B result counts only if the
operator confirms that no `repeat_suppressed` line appeared for the specific
send whose outcome was recorded. Without that confirmation the run is
inconclusive and must be repeated, not treated as evidence either way.

## 4b. The two-part start — stand clear of the belt

⚠ This is the first procedure that validates code capable of putting the belt
under someone. Nobody stands on the pad for any step here.

Added 2026-08-25 with the bridge-side start sequencing (design spec §6 rule 6).
Fact 12 says the pad ramps to ~3.5 km/h on START regardless of its stored speed,
so a start that means 2.0 km/h has to send the speed AFTER the belt is moving.
The unit tests in `tests/test_start_sequence.py` prove the logic; only hardware
proves the ~2 s window is real and that the pad honours a speed sent inside it.

**Preconditions.** Pad awake, `mode` reads `manual` in the status stream — press
**M** on the panel if it reads `standby`, because nothing over BLE leaves it
(fact 13). Phone app closed. Belt clear. Run the bridge by hand, not through the
shell, so stdout is visible:

```
./bin/walkingpad-bridge
```

**Procedure.**

1. Confirm status frames are arriving and `belt` reads `stopped` or `idle`.
2. Send exactly one start with a speed well below the pad's own:
   `{"cmd":"start","kmh":2.0}`
3. Watch the belt state sequence without sending anything else. Expect the 3-2-1
   countdown (`belt` 9 → 8 → 7, fact 14), then `running`.
4. Record every status line for the next 20 seconds.

**What to record.**

- Did `speed` settle at **2.0**, or did it settle at ~3.5? Settling at 3.5 means
  the follow-up speed missed the window or the pad ignored it.
- How many seconds passed between the first `belt=running` frame and the speed
  beginning to fall away from the pad's own ramp? That is the real width of the
  window fact 12 describes; the bridge currently allows `START_SPEED_WINDOW_S`
  (8 s) for the belt to start at all, which is a different number.
- Did `{"t":"error","code":"start_unconfirmed",...}` appear? If so the belt never
  reported `running` within the window and no speed was sent.

**Then stop the belt from the panel, not over BLE**, and record whether the
status stream follows the transition. Use the BLE stop only in §3, where its
gating is the thing under test.

⚠ If the belt reaches ~3.5 km/h and stays there, that is the hazard §6 rule 6
exists to prevent, and it means the sequencing does NOT work on this firmware.
Record it and stop; do not add a retry, and do not widen the window by guesswork.
Fact 17 forbids re-sending on a timer, and a retry loop against the toggle is how
the countdown-cancel-countdown cycle was discovered in the first place.

## 4c. The panel, on hardware

Only after 4b. `omarchy plugin add` this repository, enable it, then confirm each
of these against the bar icon and popup:

1. Icon alone in the bar, no text. `nf-md-walk` when not running, `nf-md-run`
   while the belt moves, dimmed while the pad is asleep.
2. With the pad awake and idle, the popup shows the session stats AND the standby
   instruction line only when `mode` actually reads `standby` — not permanently.
   Getting this wrong was the defect that motivated the panel.
3. Right-click the bar icon with the belt stopped. Expect a refusal naming the
   panel and the remote, and expect the belt NOT to start (fact 16).
4. Press −0.5 four times quickly while the belt runs. Expect ONE speed change of
   one step, not four, and no `repeat_suppressed` for the distinct values.
5. Kill the bridge process by hand. Expect the widget to recover within a few
   seconds, and after four kills in a minute to show a hard error rather than
   respawning forever.

## 5. Read-only observations for open questions

Nothing in this section moves the belt or writes a motion command. It is safe to
run with the pad simply sitting there powered on. **Q4, Q5 and Q7 all remain
OPEN.** An observation here, including "not observed", does not close any of
them. Only a pattern confirmed across multiple sessions could do that; gathering
that pattern is future work, not this procedure's job.

### Q4 — OPEN: observe auto mode opportunistically

Mode byte 1 is confirmed as `"manual"`, and byte 2 is confirmed as
`"standby"`. Byte 0 decodes as `"auto"` but remains unobserved.

21. While running an earlier section, or while leaving the bridge connected and
    polling, watch the status stream's `mode` field.
22. If `mode` is ever anything other than `"manual"` or `"standby"`, record the
    full status line, including `belt`, `speed` and `mode`, and note when it
    happened. Otherwise record "not observed" for this session.

There is no documented way in this repository to force the pad into auto mode
from the panel or over BLE. Do not invent one or try undocumented panel button
combinations to provoke it. This is a passive observation only.

### Q5 — OPEN: capture raw status bytes [15]–[17]

Byte [16] remains undecoded; captures so far contain 0x02, 0x03 and 0x00. Bytes
[15] and [17] were 0x00 in every capture so far.

The bridge's JSON status stream exposes decoded fields, not these raw bytes.
`tools/probe-status.py` provides the required raw-frame view. It sends only the
read-only status query `F7 A2 00 00 A2 FD`, previously confirmed safe when sent
repeatedly with belt state and speed unchanged, and prints each full frame as
hex, including bytes [15]–[17].

23. Run `tools/probe-status.py` as a separate, standalone step. Only one BLE
    connection to the pad exists at a time, so do not run it while
    `./bin/walkingpad-bridge` is connected. Run it just before connecting the
    bridge, or after stopping the bridge with Ctrl-C and confirming the
    disconnection as in §3 step 10.
24. Capture a raw frame in whatever states the rest of this session reaches
    naturally: for example, once while awake-and-idle or in standby, and again if
    the belt happens to be running from an earlier step or a panel start. Stand
    clear if the belt is already moving. Record bytes [15], [16] and [17] from
    the raw hex for each capture.

Do not add panel or remote actions solely to reach more states than this session
already visits.

### Q7 — OPEN: time the untouched sleep boundary

25. After finishing whichever commands from §2–§5 you plan to send, stop sending
    operator commands and let the bridge keep polling. Note the wall-clock time.
26. Do not touch the panel or remote during the wait. Record when `link` first
    reports the pad unreachable as state `"lost"`, or when a subsequent scan
    reports `"absent"`. That is the sleep boundary for this observation.

AGENTS.md fact 3 already establishes that BLE cannot wake a sleeping pad. This
observation measures how long the idle, untouched pad remains reachable; it does
not re-test whether BLE can wake it.

## 6. Deferred to their own session

**Q3 and Q8 remain OPEN.** They are not part of this batch and do not ride along
with §4 or §5.

### Q3 — OPEN: child-lock interaction with remote commands

Nothing in this repository documents how to engage or disengage child lock on
this pad model. There is no confirmed button combination or confirmation
behaviour. Guessing at a trigger risks fumbling with an active belt under an
unverified control state.

The safety design in §0, AGENTS.md fact 16 and `Safety.may_stop()` directs the
operator to the panel or remote whenever the bridge refuses to send stop. If
child lock disables that fallback while the belt is moving, that interaction
must be understood before combining child lock with a moving belt, not discovered
by combining them.

Before any such test, establish a documented, confirmed way to engage and
disengage child lock at will. With the belt stationary, confirm that the panel or
remote's physical stop control remains available while child lock is engaged
before ever testing it against a moving belt. That requires its own dedicated
session; do not add it here.

### Q8 — OPEN: stale status after an abrupt link loss

Settling Q8 requires deliberately reproducing an ungraceful link loss, such as
SIGKILLing the bridge or otherwise severing the connection abruptly while the
belt is in a known state. It then requires reconnecting, timing how long the last
reported status stays frozen, and identifying the event that clears it.

That deliberately causes the failure mode warned against below: "Do not kill the
bridge with SIGKILL." It also breaks the link-health assumption that a recent
status frame is the only evidence of a live link. Both `Safety.may_stop()` and
§4 rely on that assumption. Layering this onto a test of an unverified stop
primitive would make a frozen readout ambiguous between a stale snapshot and an
effect of the new primitive.

Q8 needs a dedicated session with a simple, already-stationary belt. That session
must deliberately break the link, reconnect, time how long the last frame stays
frozen, and identify what specifically unfreezes it. Do not run it as part of
this session.

## Known traps when reading the output

- **`repeat_suppressed` means no frame reached the pad.** The bridge keys this
  check on command name AND value, so it fires only on a genuinely identical
  repeat within 1.0 second. Do not interpret the belt's unchanged state as
  protocol evidence; wait at least 1.0 second and re-send that single command
  once.
- **A stale snapshot can survive an abrupt link loss.** Seen once: after
  notifications stopped mid-session, the next connection served eight identical
  frames reading `belt=01, speed=3.5` while the belt was physically stopped. If
  the numbers are frozen *and* `elapsed` is not advancing, distrust them and
  reconnect. Do not conclude a command was ignored from reported speed alone.
- **Do not kill the bridge with SIGKILL.** BlueZ then keeps reporting a link that
  is already dead. The bridge recovers on its own — it adopts the stale link, sees
  no frames for 15 s, tears it down and rescans — but expect one slow cycle.
