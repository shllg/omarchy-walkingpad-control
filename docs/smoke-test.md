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

Only one BLE connection to the pad exists at a time, so close the phone app
first. ⚠ That exclusivity does NOT extend to this machine: BlueZ owns the link
and shares it between local clients, so the plugin's own bridge and a bridge you
start by hand both connect and both poll. Disable the plugin rather than
assuming one of them wins — see §4b.

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

⚠ **Disable the plugin first** (`omarchy plugin disable io.github.shllg.walkingpad`)
so the shell's own bridge stops. Re-enable it afterwards; `disable` removes the
bar placement, so back up `~/.config/omarchy/shell.json` before and restore it
after.

⚠ **Disable it — do not kill the process.** Two bridges do not fail: BlueZ owns
the pad's one link and shares it between local clients, so both connect, both
poll, and frames arrive on two interleaved cadences that read as duplication
(fact 5, measured 2026-08-27). Killing the plugin's bridge does not give you
exclusivity either — quickshell respawned it in **3 s**, measured. Contention is
not harmless to what you are measuring here: one `answered_s` came in at 23.08 s
while a second bridge was also polling, against 28–29 s otherwise.

⚠⚠ **Gate: send the start inside the answered window.** This pad has been
measured stopping its replies 29.2 s after a connection while still acknowledging
every packet on the radio (`docs/protocol-evidence.md`, "The pad stops ANSWERING
at 29.2 s"). Wait for `{"t":"link","state":"connected"}` and its first status
frame, then send the start **immediately** — within a few seconds, not thirty.

A start sent late lands in a pad that has gone mute, the belt never reports
`running` inside `START_SPEED_WINDOW_S`, `start_unconfirmed` fires, and no speed
is ever sent. The belt then settles at the pad's own ~3.5 km/h, which is exactly
what this procedure's failure branch describes — **produced by something that has
nothing to do with the start sequencing.** A start into a muted pad is
indistinguishable, at the belt, from a start whose speed the pad ignored.

⚠ An earlier version of this gate demanded "60 unbroken seconds of link" before
starting. That can never pass against a 29.2 s window, so it would have blocked
§4b permanently. It is retracted; the rule above replaces it.

If the belt never reports `running`, this run is **inconclusive — not a failure**.
Record it as inconclusive and check when the start landed relative to the
connection. Do not widen `START_SPEED_WINDOW_S` to make it pass.

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

**Runs so far.**

- **2026-08-27 16:22, 16:26 and 16:33 — ✅ PASSED, three times. §4b IS SIGNED
  OFF.** Each start was sent on the first status frame of a fresh connection, per
  the gate above.

  | run | command sent | first `running` | countdown | settled at |
  |---|---|---|---|---|
  | 1 | 16:22:19.853 | 16:22:23.870 | 4.00 s | **2.0** |
  | 2 (restart, §4e) | 16:26:26.860 | 16:26:30.889 | 4.03 s | **2.0** |
  | 3 (after §4d) | 16:33:49.755 | 16:33:53.750 | 4.00 s | **2.0** |

  The belt settled at the commanded 2.0 km/h every time, never at the pad's own
  ~3.5. Run 1 caught the pad already at 2.5 km/h 70 ms after its first `running`
  frame, climbing toward 3.5, and the follow-up pulled it back to 2.0 — fact 12's
  window is real, narrow, and handled. `pad_set_speed` flipped to the commanded
  value on the same frame the follow-up landed, which is the byte [14] evidence
  that the speed was accepted.

  **`START_SPEED_WINDOW_S = 8.0` is confirmed adequate**: 4.0 s of countdown, with
  the same value three times. The 15 s used by `walkingpad-controller` is not
  needed here. Do not widen it.

  Run 3 was the §4d closing re-run and is the evidence that a goal does not
  disturb the start: `goal` stayed `null` across the whole sequence.

- **2026-08-27 14:40 — INCONCLUSIVE, superseded by the runs above.** The start
  landed at the end of an answered window, `start_unconfirmed` fired, no speed was
  sent, and the belt settled at ~3.5 km/h with the sequencing never exercised.
  This is what the gate above now prevents.

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
   while the belt moves.

   ⚠ **Dimming tracks the BRIDGE’s link, not the pad’s sleep.**
   `Model.barDimmed()` is `linkState !== "connected"`, so while open question 9
   stands the icon dims and returns roughly every 48 s with the pad awake and a
   metre away. **Expect that flap.** It is neither a fault nor a rendering
   defect, and it is not evidence about sleep. This check’s original wording
   ("dimmed while the pad is asleep") assumed a mute that does not exist, and
   was rewritten on 2026-08-28 rather than the code being changed to match it.
2. With the pad awake and idle, the popup shows the session stats AND the standby
   instruction line only when `mode` actually reads `standby` — not permanently.
   Getting this wrong was the defect that motivated the panel.
3. Right-click the bar icon with the belt stopped **and the popup CLOSED**.
   Expect the belt NOT to start (fact 16), and expect all three of these:
   1. the bar icon takes the bar’s attention colour for about ten seconds;
   2. hovering it within that window shows the state line plus
      `Stop refused — use the panel or remote`;
   3. opening the popup within that window still shows the full refusal, naming
      the panel and the remote — it must not be wiped by the act of opening.

   Then wait past ten seconds and open the popup again. Expect the instruction
   line to be gone: a refusal is a press that failed, not a standing condition.

   ⚠ **This is the step the check was missing**, and it is why it exists. On
   2026-08-28 the belt did not start, correctly, and the operator reported
   "right click does not do anything" — an accurate description of what there
   was to see, because the refusal rendered only inside a popup nobody had
   open. It stopped being cosmetic the same day: during §4d the operator pressed
   Stop **while standing on a moving belt**, saw no reaction, and stepped off.
   Fact 18 exempts stop from throttling so that a second press is the recovery,
   and that only works if the first press visibly failed.
4. Press −0.5 four times quickly while the belt runs, starting from a known
   goal. Expect:
   - the goal to fall by **the full 2.0 km/h** — four presses of a button
     labelled −0.5 mean −2.0, floored at `minGoal` (0.5 km/h);
   - **every goal to sit on a multiple of the step**, never 3.1 or 2.1. Run this
     once more from a belt that is still ramping out of a start, with no goal
     set: that is the state that produced the off-grid defect below, and an
     on-grid start cannot reproduce it;
   - **one** `goal` command on the wire, not four. The 250 ms `goalDebounce`
     sends the settled value once;
   - no `repeat_suppressed`. Only one command is sent, and consecutive goals
     carry distinct values in any case;
   - the belt to lag behind the goal and ramp toward it at ~0.25 km/h per second
     (fact 2). The readout catching up slowly is the pad, not the plugin.

   ⚠ Rewritten 2026-08-28. This check used to ask for "ONE speed change of one
   step", which describes the one-shot `setSpeed` path. The buttons drive
   `setGoal()` and have since the goal servo landed, and `8182722` then removed
   the lead bound that made three presses from 2.0 km/h reach 3.0 instead of
   3.5. The old expectation would now fail against correct behaviour.
5. Kill the bridge process by hand. Expect the widget to recover within a few
   seconds, and after four kills in a minute to show a hard error rather than
   respawning forever.
6. **The popup chart, during and after a real walk.** Charts §1 defines this
   chart and charts §6 defers its only check to this file; this is that check.
   It needs a moving belt, so run it in the same session as check 4.

   ⚠ **Restart the shell BEFORE the walk, never during it.** The chart's only
   data source is the in-memory ring buffer in `WalkingpadService.qml`. It
   touches no file and survives nothing, so `omarchy restart shell` empties it
   and kills the bridge — a restart mid-walk leaves an empty chart that reads
   exactly like a rendering fault. A QML edit does not reach the running bar
   without that restart either, so do it first, then walk.

   **6a — while walking.** Walk at least three minutes, then stand off the belt
   for a full minute with the belt stopped, then walk again, so the run
   contains one real gap. Expect:

   - **bars within the first minute**, one per wall-clock minute, newest right;
   - **no bar at all for the minute you did not walk** — not a zero-height bar,
     not a flat baseline segment. That gap is the information the chart exists
     for, and it is why the axis is real time rather than sample index;
   - **the in-progress minute rendered distinctly** from the completed ones, and
     no longer distinct once the next minute opens;
   - bars that track **steps, not distance**: they vary minute to minute at a
     steady speed rather than sitting at one height.

   **6b — after stopping, belt stopped and standing clear.** The ring buffer
   keeps the walk's samples, so the chart still has data to check against.

   Cross-check the JS arithmetic against the Python. This is the point of the
   check: charts §6 records that the two implementations diverged on first
   write — 118 steps against 120 for the same minute — and pytest pins them only
   at the boundaries it can reach. Read the same minutes back:

       bin/walkingpad-stats profile --since <YYYY-MM-DDTHH:MM> \
                                    --until <YYYY-MM-DDTHH:MM> --bucket 60

   Each `buckets[].t` is a wall-clock minute and `buckets[].steps` is that
   minute's total. Expect **the same minutes present and the same minutes
   absent**, and the step totals to match the bars.
   ⚠ `--bucket` takes whole seconds or `auto`, never `1min`, and these
   subcommands have no `--help` despite what the plans say.

   Then the keyboard zones (charts §5). Arrow up from the controls into the
   chart. Expect the cursor to seed at the **newest** bucket, horizontal arrows
   to move within the chart, and leaving the zone to clear the cursor.

   ⚠⚠ **With the cursor in the chart zone, press Enter. Nothing must happen to
   the belt.** `activateCursor()` is gated so Enter can never fire a belt
   command from a non-control zone, and charts §5 marks that gate
   safety-relevant. Run this press with the belt stopped and yourself clear of
   it: if the gate has been relaxed, the command that fires is whichever control
   sits at that index, and fact 16 makes a stray stop a start.

**Runs so far.**

- **2026-08-27 — one observation, §4c otherwise still unsigned.** Pressing Start
  on a stopped belt was refused with `stale_link` — then worded "no recent status;
  refusing to move the belt" — and a later press succeeded. Confirmed by the
  operator. ⚠ That wording was replaced on 2026-08-28: the refusal now says what
  was seen and what to do, because this is the refusal a user meets most often.

  **This is the 29.2 s mute reaching the user**, not a widget defect. Neither the
  button (`WalkingpadPanel.qml:121`, no `enabled` condition) nor the service
  (`WalkingpadService.qml:128`, always sends) gates a start, and `Safety.may_move`
  is nothing but `last_status_age_s <= 3.0`. With the pad answering 29.2 s of each
  ~48 s cycle, roughly **one press in three lands in a dead window.**

  ⚠ The operator's impression was that pressing Stop first is what enabled the
  Start. It is not: a stop on a stopped belt is refused by `may_stop` and clears
  nothing that gates a start. The few seconds it took were what mattered, because
  a fresh connection landed. Do not build a "press stop first" workaround on this;
  the cause is open question 9.

- **2026-08-28 — checks 1, 2, 3 and half of 5 PASSED. Check 4 needs rewriting.**

  1. **PASS.** Icon alone, no text, walk glyph.
     ⚠ **But it dims while the pad is wide awake.** `Model.barDimmed()` is
     `linkState !== "connected"`, and question 9's ~29 s mute makes the link
     flap on a ~48 s cycle, so the icon dims and returns roughly every 48 s with
     the pad a metre away and fully reachable. The rendering is correct; this
     check's wording ("dimmed while the pad is asleep") assumed a mute that does
     not exist. Rewrite the check, not the code.
  2. **PASS.** With the pad genuinely in standby, the popup showed the stats AND
     the standby line together, which is the designed behaviour
     (`WalkingpadPanel.qml:479-483`). `Model.instruction()` gates that string on
     `mode === "standby"` alone, so its presence proves the mode rather than a
     stuck line. The original defect is not present.
  3. **PASS**, and it exposed a gap this check does not currently cover.
     Right-click on a stopped belt was refused and the belt did not start.
     ⚠ **With the popup CLOSED, a refused right-click is completely invisible.**
     The handler fires (`WalkingpadPanel.qml:347`, and `WidgetButton.qml:97`
     accepts the right button), the stop is sent, and the bridge refuses it —
     but `root.instruction` renders inside the popup, and the tooltip carries
     `linkState`/`beltState`/`speed`, never the error. The operator reported
     "right click does not do anything", which was an accurate description of
     what a user sees.

     This is worse than cosmetic in one specific case: someone walking who
     right-clicks to stop, and whose stop is refused as `stale_link` — about one
     press in three right now. The belt keeps moving and nothing says the stop
     failed. Fact 18 exempts stop from throttling precisely so a second press is
     the recovery, and that only works if the user knows the first one failed.
     ⚠ It stopped being hypothetical the same day: during §4d the operator
     pressed Stop **while standing on a moving belt**, saw no reaction, and
     stepped off. **This check must gain a "where does the user see it" step.**
  4. **NOT RUN — the expected result is stale.** It asks for one speed change of
     one step from four fast presses, which describes the one-shot `setSpeed`
     path. The buttons drive `setGoal()` (§4d) and have since the goal servo
     landed; `setSpeed`'s own comment says "the buttons use setGoal()". Rewrite
     against what the goal does before running it.
  5. **Half PASS.** One kill, widget recovered in seconds. The four-kills-in-a-
     minute half is still unrun.

  **What changed after that run, before the next one.** Checks 1, 3 and 4 above
  were rewritten on 2026-08-28 against what the code does. Check 3 also gained
  the three "where does the user see it" sub-steps, and the gap it found was
  closed rather than only documented: a live refusal now colours the bar icon
  for ten seconds, names itself in the tooltip, and survives the popup being
  opened to read it. So checks 1, 2 and 3 must all be **re-run** — 1 and 3
  against new expectations, and 2 because it has never been run against this
  build.

- **2026-08-28, second run — check 3 PASSED in full, and found one defect.**
  Run against the build that closed the invisibility gap, after
  `omarchy restart shell`. Belt stopped, popup closed, one right-click:

  1. **The belt stayed stopped.** Confirmed independently from the store — zero
     `running` frames after the stop, so the fact 16 toggle was gated as designed.
  2. **The bar icon changed colour for the dwell.** ⚠ It came up **red** — see
     the defect below.
  3. **The tooltip carried the refusal**, reading `Stop refused — use…` on its
     second line.
  4. **Opening the popup kept the full refusal.** This is the half that used to
     fail: `onOpenedChanged` cleared the error unconditionally, so opening the
     popup to find out what happened wiped the explanation on the way in.

  Also confirmed the same session, though not a §4c check: **right-click stop on
  a MOVING belt works.** 18 accepted stops are in the day's samples, the last at
  `1787931183` showing `v: 0.2` decelerating to `0.0`.

  ⚠ **Defect found and fixed the same day: the bar icon painted every refusal in
  the alarm colour.** `WidgetButton` defaults `activeColor` to `bar.urgent`, and
  `Commons/Color.qml:76` defines `Color.bar.active` as
  `pick("bar.active", root.urgent)` — a fallback to the alarm hue for any theme
  that does not define `bar.active`. Neither theme on the author's machine does.

  This is the **same defect the popup was corrected for on 2026-08-25**, when a
  stop refused on a stopped belt came back in alarm red, reproduced on a new
  surface. It got the same answer: `Model.isAdvisory()` now splits the bar icon
  too, so an expected refusal gets `accent` and only a genuinely broken plugin
  gets `urgent`. ✅ **Confirmed on hardware the same day**, after a second
  `omarchy restart shell`: a stop refused on a stopped belt now comes up in the
  accent role, and the rest of check 3 behaves as it did. **Check 3 is signed
  off in full.**

  ⚠⚠ **Second defect, found the same day by pressing + rather than by running a
  check: the goal came off the step grid, reading 3.1.** Reported by the
  operator as "Multi ++ pressed → 3.1 → WTF why?".

  **Root cause.** `speedBy()` seeded the goal from `state.currentSpeed` when no
  goal was set yet — the belt's ACTUAL speed. Fact 2 is the trap: reported speed
  is actual rather than target and the pad ramps ~0.25 km/h per second, so the
  base was wherever the ramp stood at the instant of the press, at the pad's 0.1
  resolution. `base + delta` then carried that offset through every later press.

  **Caught in the store, not by eye:**

      t=…853.14  v: 2.0  c: 2.1  g: 2.1   pe: 2   ← 2 s after start, ramping
      t=…855.14  v: 2.1  c: 2.6  g: 3.1           ← two more presses

  `2.1 = 1.6 + 0.5`, and 1.6 km/h is where the ramp was. Every goal set that day
  on a settled belt was clean: 3.5, 1.5, 2.0, 2.5, 5.0.

  **Fixed** by moving to the next grid multiple strictly in the pressed
  direction. On the grid — every press after the first — that is exactly
  `base + delta`, so `8182722`'s "N presses mean N steps" is untouched. Off the
  grid, the first press moves LESS than one step rather than more, which is the
  safe direction with someone on the belt.

  ⚠ **This arithmetic has no automated test.** It is QML-side JavaScript, and
  ADR D-1 puts testable logic in Python precisely because QML cannot be reached
  by pytest. It was verified by hand against a table of bases including the
  clamp edges. Check 4 is the only gate on it, which is why the check above now
  requires a run from a ramping belt.

  ✅ **Check 5's second half PASSED, 2026-08-28.** Four SIGTERMs to the bridge
  at t+6, t+24, t+37 and t+49 s — all inside 43 s, with gaps of 18, 13 and 12 s,
  each well under the 60 s `respawnWindow` that resets the counter:

      kill 1  respawned      kill 3  respawned
      kill 2  respawned      kill 4  NOT respawned, and still down at t+68

  Three respawns then a hard stop, matching `WalkingpadService.qml:483`. The
  belt was stopped throughout.

  ⚠ **Trap for whoever automates this next: do not find the bridge with
  `pgrep python3` or by matching `comm`.** Python sets the process name from the
  script, so `comm` is the truncated `walkingpad-brid`, never `python3`. And
  `pgrep -f walkingpad-bridge` matches the *searching shell's own command line*
  as well, so a `| head -1` can return your own PID and kill your script instead
  of the bridge. Both mistakes were made here, and the second one produced a
  confident but wrong "the bridge has given up" reading while it was running
  normally. Match on `/proc/*/cmdline` beginning `/usr/bin/python3`.

  **Still unrun from this section:** checks 4 and 6, which both need a running
  belt and are meant to ride in one session. Checks 1 and 2 keep their PASS from
  the first run — check 1's rewrite changed only its wording about dimming, and
  nothing since has touched `Model.instruction()`'s standby gate.

## 4d. The live goal — stand clear of the belt

⚠ The goal is the first thing in this bridge that writes a motion command with
nobody pressing anything. Design spec §6 rule 12 lists the three bounds it rests
on; this section is where they stop being an argument.

⚠⚠ **Step 2 was REPLACED on 2026-08-28.** The lead bound it used to verify was
removed that day, so the old step now tests for behaviour that is gone. The
2026-08-27 run below is kept as the historical record and is NOT re-runnable as
written — see design spec §6 rule 12.

**Preconditions** as §4b: awake, `mode == manual`, phone app closed, belt clear.
Belt running at a known speed, started per §4b.

**Procedure and what to record.**

1. **It converges.** With the belt steady at 2.0, press `+0.5` once. Expect the
   panel to show `Goal 2.5` immediately and the belt to reach 2.5 within ~2 s.
2. **The label's promise holds.** With the belt steady at a known speed, press
   `+0.5` three times as fast as you can. The goal must land on exactly
   **start + 1.5** — the button says +0.5, so three presses are worth +1.5 and
   nothing may quietly cut them. Record the number the panel settles on and the
   speed the belt reaches.

   ⚠ Replaces the lead-bound check of 2026-08-27, which required the goal to
   stop at actual + 1.0. That bound was removed on 2026-08-28 after three
   presses from 2.0 km/h delivered +0.8 on this pad. What still bounds the belt
   is bound 1: no single write may exceed one `max_step` from the ACTUAL speed,
   so watch the `target` values as it climbs and record any step above 0.5.
   A step above 0.5 is a stop-the-test failure.
3. **It reverses.** While the belt is still climbing, press `-0.5` twice. The
   goal must move down from where it was — one intention, not a queue. Record
   whether the belt turns around without first finishing the old climb.
4. **It stops on arrival.** Let it reach the goal. Record whether the bridge goes
   quiet: the `Goal` row should disappear and no further speed frames should go
   out. If speed frames keep flowing at a reached goal, that is bound 4 failing
   and it is the one that would hammer the pad once a second.
5. **Stop cancels it.** Set a goal 1.0 above actual, then press Stop while it is
   still converging. The belt must stop and stay stopped. ⚠ If it stops and then
   moves again, the goal survived a stop — halt the test and report it. That is
   the single worst failure this feature can have.
6. **A refused stop is not red.** With the belt stopped, press Stop. The message
   should appear in the accent colour, not the urgent one, and should be gone
   when you close and reopen the panel.

**Then re-run §4b once** to confirm the goal has not disturbed the two-part
start: a goal must never write in the same status frame a start's speed lands.

**Runs so far.**

- **2026-08-27 16:29–16:33 — ✅ STEPS 1 TO 5 PASSED, bridge-side. Step 6 is still
  open**, because it is a colour check that belongs with §4c.

  1. **It converges.** Belt steady at 2.0, goal 2.5 set at 16:29:44.863. One write
     at 16:29:45.921, belt 2.0 → 2.3 → 2.5 by 16:29:47.877 — **2.0 s**, as
     specified.
  2. **The cap holds.** ⚠ SUPERSEDED 2026-08-28 — the bound this verified was
     removed; the result below is history, not a requirement.
     Five goals sent back to back (3.0, 3.5, 4.0, 4.5, 5.0)
     with the belt actually at 2.5. The goal settled at **3.5 — actual + 1.0**,
     not 5.0. Bound 1 holds. The servo then walked the belt one clamped step per
     status frame: `target` 3.0 → 3.2 → 3.5, no step above 0.5.
  3. **It reverses.** Goal 4.5 with the belt climbing 3.5 → 3.7 → 4.2; two `-0.5`
     presses set the goal to **3.5**, coalesced to the last intention rather than
     queued as 4.0 then 3.5. The belt turned around at 4.2 and came back down
     without finishing the old climb.
  4. **It stops on arrival.** `goal` went `null` on the arrival frame and **no
     further speed frame was written for 20 s** while the belt held 2.5. Bound 4
     holds; the servo does not hammer the pad at a reached goal.
  5. **Stop cancels it.** ⚠ The critical one. Goal 4.5, belt converging through
     3.7 with `target` 4.2, stop sent at 16:32:35.443. On the very next frame
     `goal` was `null` and `target` 0.0; the belt coasted 4.1 → 2.5 → 0.7 → 0.0
     and **stayed stopped for 37 s and across a full reconnect.** The goal did not
     survive the stop.

  The closing §4b re-run passed as run 3 in §4b above, with `goal` `null`
  throughout: no goal write collided with the frame the start's speed landed on.

  Also observed, confirming fact 15: `pad_set_speed` stayed at 4.2 while the belt
  was stopped, because byte [14] is the last BLE-commanded speed and never a
  target.

- **2026-08-28 15:14–15:26 — ✅ ALL SIX STEPS PASSED, on the rewritten step 2.**
  First run against the goal with `max_goal_lead` removed.

  ⚠⚠ **Procedure deviation, recorded because the sign-off is worthless if it is
  not truthful: the operator was ON THE BELT for these runs.** Every §4d step
  says to stand clear. The step counter advancing through the run is the
  evidence (632 steps by 15:23). The results below stand — arguably it is the
  harder test — but nobody should read this record as a clear-belt run, and the
  next one should be one.

  1. **It converges.** Belt at 2.0, goal 2.5 at 15:19:50, belt at 2.5 by
     15:19:52.
  2. **The label's promise holds.** Three `+0.5` presses from a settled 2.0
     produced `g=3.5` exactly, and the belt reached 3.5:
     `2.0→c2.5 · 2.2→c2.7 · 2.3→c2.8 · 2.8→c3.3 · 3.0→c3.5`, arriving 15:15:07
     with `g` cleared. **Every write exactly 0.5 above the actual speed** — five
     writes, no step over one clamp. Under the old lead bound the same three
     presses gave 3.0 and a belt that stopped at 2.8.
  3. **It reverses.** Four `-0.5` presses took the goal 3.5 → **1.5** (−2.0,
     exact). One `+0.5` while the belt was still descending moved it 1.5 → **2.0**
     — one intention moved, not a queue — and the belt settled there at 15:15:34.
  4. **It stops on arrival.** `g` went `null` on the arrival frame every time,
     with no further speed frame written.
  5. **Stop cancels it.** ⚠ The critical one. Goal 5.0 with the belt converging
     through 2.5 → 2.8 (`c` 3.0 then 3.3); stop at 15:23:53.490 cleared `g` to
     `null` and `c` to 0.0 **on the same frame**. The belt wound down 3.1 → 0.6 →
     0.0 and stayed stopped. The goal did not survive the stop.
  6. **A refused stop is not red.** Refused on a stopped belt with "no recent
     evidence the belt is moving", rendered in the **accent** colour, and gone
     after closing and reopening the panel. Step 6 was open since 2026-08-27;
     it is now closed.

  The closing §4b re-run passed at 15:25:43: `starting` with `c=2.0`, `running`
  at v=0.0, settled at exactly 2.0, `g=None` on every frame. No goal write
  collided with the frame the start's speed landed on.

  ⚠ **A defect was found and fixed during this run, and the fix is UNVERIFIED on
  hardware.** With the lead bound gone, every press during one of question 9's
  mutes added another step to the panel's optimistic local goal while the bridge
  refused each send on the stale link. At 15:23:45 the popup read
  `Goal 5.0 km/h` and *"the pad has gone quiet, so nothing was sent"* at the same
  time, with `g=None` on every frame — a goal that existed nowhere but in the
  widget. The whole accumulated intention then landed at once when the link
  returned. `WalkingpadService.qml` now clears the pending goal on `stale_link`
  and `standby`, the two codes the bridge refuses on before adopting anything.
  **Re-run steps 2 and 3 across a mute to confirm.**

  Also observed, and NOT explained: at 15:26:18 the belt was stopped while
  `commanded_speed` still read 2.0, so this bridge never sent a stop — a sent
  stop drives it to 0.0, as it did at 15:23:53. The operator had pressed Stop
  with no effect and then stepped off the pad. An unloaded auto-stop is the
  obvious candidate and it is UNMEASURED; see `docs/protocol-evidence.md`.

## 4e. Counters across a stop — stand clear of the belt

Run this first on hardware for Stage 3. The store's attribution model rests on
the pad's cumulative `time`, `dist_km` and `steps` counters surviving a stop.
That behaviour is only an operator report from 2026-08-25 (stats design §1.1),
not a captured measurement. It is weaker evidence than the measured facts in
AGENTS.md. This procedure upgrades the report to a measured fact, or refutes it.

⚠ This procedure moves the belt. Stand clear of the pad before starting it, and
do not let anyone stand on the belt during any step here.

**Preconditions.** Pad awake, `mode` reads `manual` in the status stream — press
**M** on the panel if it does not. Phone app closed. Belt clear. Run the bridge
by hand so stdout is visible; a normal real-pad run writes samples to the default
store without any additional flag:

```
./bin/walkingpad-bridge
```

**Procedure.**

1. Type `{"cmd":"start","kmh":2.0}` to use the two-part start from §4b at a
   modest speed. Confirm `belt` reads `"running"`.
2. Walk for one minute. Immediately before the next step, record the `time`,
   `dist_km` and `steps` fields from the status stream.
3. Type `{"cmd":"stop"}`. The current running frame is why
   `Safety.may_stop()` permits this: fact 16 gates stop because its opcode is a
   toggle, while fact 18 leaves stop unthrottled so a second deliberate press is
   never swallowed. Wait twenty seconds with the belt stopped.
4. Start the belt again with the two-part start from §4b, then walk for one more
   minute.
5. Record `time`, `dist_km` and `steps` as soon as `belt` reads `"running"`
   after the restart, then record all three again at the end of the second
   walking minute.

**What to record.**

- Record all three counter snapshots: immediately before the stop, immediately
  after the restart, and at the end of the second walking minute.
- If `time`, `dist_km` and `steps` continue climbing after the restart from where
  they were before the stop, rather than dropping toward zero, that confirms the
  counters survive a stop. The attribution model in the stats design is sound as
  designed.
- If any counter drops back toward zero after the restart, every stop is an epoch
  reset in that model. The `residual` concept becomes meaningless as designed,
  and the attribution model needs rework before it can be trusted. This is a real
  possible outcome: record it honestly rather than explaining it away.

**Runs so far.**

- **2026-08-27 16:25–16:27 — ✅ ANSWERED. THE COUNTERS SURVIVE A STOP AND CONTINUE.**

  | moment | `time` | `dist_km` | `steps` |
  |---|---|---|---|
  | immediately before the stop | 169 | 0.09 | 188 |
  | belt stopped, across a reconnect | 171 | 0.09 | 188 |
  | first `running` frame after restart | 171 | 0.09 | 188 |
  | one minute later | 290 | 0.15 | 321 |

  They resumed from where they were and kept climbing. They did **not** drop
  toward zero. **The attribution model in stats design §1.1 is sound as designed**,
  and `residual = last_pad − attributed` remains meaningful across a stop. The
  2026-08-25 operator report is now a measured fact.

  The stop was sent over BLE, not the panel, so this run also exercised
  `Safety.may_stop()` in the permitting direction on a live running frame: belt
  ramped 2.0 → 1.1 → 0.4 → 0.2 → 0.0. Note `belt` reads `stopped` while `speed`
  is still coasting at 0.4 — the state flips before the belt physically halts.

- **2026-08-27 earlier — half answered, superseded.** `time` 65, `dist_km` 0.05,
  `steps` 90 held across a panel stop and three reconnects, but the belt was never
  restarted.

## 4f. The sleep boundary's counter half

Section 5's Q7 steps 25–26 time the first half of the sleep boundary: when the
pad stops being reachable at all. This procedure observes what happens to the
pad's cumulative counters across that same boundary.

Nothing here moves the belt or writes a motion command. You can run it with the
pad simply sitting there powered on; it does not carry the standing-clear
requirement of the movement procedures. The sleep timeout is unmeasured, so be
patient and note every wall-clock time carefully.

AGENTS.md fact 3 establishes that BLE cannot wake a sleeping pad. Wake it
physically at the panel or with the remote. This procedure cannot be scripted or
automated for that reason.

**Procedure.**

1. With the bridge running and connected, stop sending commands. Note the
   wall-clock time.
2. Let the bridge keep polling. Wait for `{"t":"link","state":"lost"}` or for
   a subsequent scan to report `"absent"`. Note the wall-clock time and keep the
   last status line from before the loss.
3. Leave the pad alone for a while longer; do not touch it. Then wake it
   physically with any panel interaction that would normally wake it, or with
   the remote. Note the wall-clock time.
4. Let the bridge reconnect; it rescans on its own. Record the first status
   frame's `time`, `dist_km` and `steps`.

**What to record.**

- Record the three wall-clock timestamps from steps 1–3, how long the pad stayed
  reachable before sleep, and how long it remained asleep before the physical
  wake.
- Record the last pre-sleep and first reconnected `time`, `dist_km` and `steps`
  values. Say whether the reconnected counters are near zero, indicating a
  reset, or continue from where they were before sleep.
- This observation bounds but cannot close the under-count in the stats design
  §10. An unobserved reset under-counts, and no heuristic can fix it. Treating a
  long wall-clock gap as "probably a sleep" would silently invent numbers, which
  is worse than under-counting a case the totals already mark as unobserved. This
  procedure can only narrow the estimate of how long a reset takes; it cannot
  eliminate the gap.

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

23. Run `tools/probe-status.py` as a separate, standalone step, with **no other
    BLE client attached to the pad**. ⚠ Corrected 2026-08-28: this step used to
    say only "do not run it while `./bin/walkingpad-bridge` is connected", which
    misses the bridge that matters. The installed plugin's bridge is spawned by
    quickshell and runs permanently, and BlueZ shares the pad's one link between
    local clients rather than refusing the second (fact 5) — so following the old
    wording gave two clients, two interleaved poll cadences, and a raw capture
    you could not trust. Disable the plugin as §4b describes, stop any manual
    bridge with Ctrl-C, and confirm the disconnection as in §3 step 10 before
    running the probe.
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
- **A hardware smoke test writes real stats.** Every session against the real pad
  leaves recorded bouts in `${XDG_STATE_HOME:-$HOME/.local/state}/walkingpad/`,
  mixed with real walking history. `bin/walkingpad-bridge --replay` deliberately
  skips that store unless `--store PATH` is also passed, so a replay session with
  no hardware cannot pollute the real record.
