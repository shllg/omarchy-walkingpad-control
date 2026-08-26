# Stats, session charts and analysis window (Stage 3) — design spec

**Goal:** Record every value the pad reports, durably and at full resolution, in a
form that survives the pad's own counter resets; draw the current session in the bar
popup; and add a screen-centred window for analysing the history.

**Spec:** this file. `docs/design-spec.md` remains canonical for the bridge, the
safety model and the plugin contract; §5.7 there is replaced by a pointer to this
document, and a new ADR D-6 supersedes D-5's "no state file yet" clause.

**Status:** design approved 2026-08-26. No code written. Implementation plan follows
separately.

---

## 0. What is new, and what is deliberately unchanged

New: a persistent store, an attribution model, a query tool, a chart component, and
an overlay window.

Unchanged, and not to be touched by this work: the poll loop, `Safety`, the start
sequence, the goal servo, and every measured fact in `AGENTS.md`. **Nothing in this
document may write to the pad.** Stats are a read-side concern end to end. If an
implementation step finds itself needing a new command frame, the step is wrong.

---

## 1. The attribution model

This is the core of the design. Everything else is plumbing around it.

### 1.1 The problem

The pad's `elapsed_s`, `distance_km` and `steps` are **cumulative counters it owns**,
not per-session values. Measured properties:

- `elapsed_s` counts **running time**, not awake time. Verified against
  `fixtures/c2-fw6.1.2.jsonl`: 1008 s at 2.5 km/h works out to 0.70 km and the frame
  reports 0.69 km, so the counter advances only while the belt moves.
- The counters **survive a stop**. Operator observation, 2026-08-25: walk, stop for
  twenty seconds, start again, and the counters continue rather than restarting.
  ⚠ **Reported, not yet captured.** `docs/smoke-test.md` gains a step to record it
  (§8.3 below). Until then it is an operator report, which is weaker evidence than
  everything else in `AGENTS.md`.
- The counters **do reset**. Measured 2026-08-24: an awake, idle pad
  (`belt == 4`, `mode == standby`) reported every counter at zero
  (`docs/protocol-evidence.md`, "NEW PROTOCOL FACT — belt == 4"). The trigger is
  unmeasured; sleep is the presumed cause.

So the pad's numbers are neither per-session nor monotonic forever, and the bridge is
not watching most of the time.

### 1.2 The model

**The pad's cumulative counter is the authority for a counter epoch. Our samples
attribute portions of it to specific bouts and timestamps. The unattributed remainder
is walking we did not observe.**

```
residual  =  last_pad  −  attributed
```

Both terms are numbers we hold, so "what did we record and what did we miss" is
arithmetic, never a guess or an inference from `elapsed_s` alone.

Rejected alternative: accumulating our own totals from observed frame deltas. That
drifts below the pad by exactly the unobserved amount and can never detect that it
has done so.

Two properties fall out, and both are reasons to prefer this model:

1. **It is a continuous self-check.** Divergence between `last_pad` and `attributed`
   while the link was believed healthy means frames were lost — a poll gap, a dropped
   notification, a stale snapshot. Nothing else in the plugin would notice that.
2. **It gives distance and steps, not only duration.** Inference from `elapsed_s`
   would have given duration alone.

### 1.3 Definitions

- **Epoch** — a maximal span over which the pad's cumulative counters are
  non-decreasing. Identified by a synthetic integer, persisted, incremented on reset
  detection. Never derived from anything the pad sends; the pad has no epoch concept.
- **Bout** — a contiguous run of frames reporting `belt == "running"`. The atomic
  stored fact. Fact 8: only `"running"` means the belt is moving, so the 3-2-1
  countdown (`belt` 9/8/7, fact 14) is **not** part of a bout — a bout begins when
  the belt actually moves, consistent with `_service_pending_start`.
- **Session** — bouts separated by less than `gap_minutes` (default 5). **Not
  stored.** Computed at read time by the query tool, so retuning the gap re-groups
  history instead of rewriting it.

### 1.4 Rules

**Epoch reset detection.** If any of `elapsed_s`, `distance_km`, `steps` is lower
than the persisted `last_pad` value, the epoch has reset: increment the epoch id,
set `attributed` to zero, set `last_pad` to the new frame. Testing *any* counter
rather than all three fails toward starting a new epoch, which loses at most one
epoch boundary; the alternative fails toward computing an enormous bogus residual.

**Attribution while the link is up.** On each frame, `delta = frame − last_pad`.
Positive deltas are added to `attributed` and to the open bout. A missed poll simply
produces a larger delta on the next frame, which is still attributed correctly.

**Attribution across a link loss.** On the first frame of a *new* link session, the
delta since the persisted `last_pad` is **not** attributed to any bout. It becomes a
residual. If it exceeds `MIN_RESIDUAL` (30 s of `elapsed_s`, or 50 m — below that it
is counter quantisation and rounding), write an unobserved bout spanning
`[last_seen_wall, now]` and add it to `attributed`.

**First-ever frame.** On a fresh store there is no `last_seen_wall`, so the
uncertainty window is unbounded — the pad's counters could be minutes or weeks old.
Adopt them as the baseline (`attributed = last_pad`) and write **no** residual bout.
This is the one case where unobserved walking is deliberately discarded, because
attributing it to any time window would be a fabrication.

**Bouts never span a bridge lifetime.** On startup, an `open_bout` found in
`state.json` is closed at the persisted `last_seen_wall`. A crash mid-walk therefore
fragments one walk into two bouts, and the session gap rule re-merges them. This
keeps bout semantics trivial and makes crash recovery require no repair logic.

**Bout close.** A bout closes on the first frame reporting `belt != "running"`, on
link loss (end time = last frame's wall clock, not now), or on clean shutdown.

---

## 2. Storage

### 2.1 Location

`${XDG_STATE_HOME:-$HOME/.local/state}/walkingpad/`

Created with mode `0700`. **This is personal health data** — a record of when its
owner is at their desk and moving — and it is the first thing this plugin has ever
written to disk. It must never be created inside the plugin directory, which is a
git clone that ships to users and which the shell watches with `inotifywait`
(see the `sys.dont_write_bytecode` note in `bin/walkingpad-bridge`).

Override with `--store PATH` on the bridge, which is how tests point it at `tmp_path`.

### 2.2 `samples/YYYY-MM-DD.jsonl`

One row per frame while recording. Short keys, roughly 90 bytes per row.

```json
{"t":1756137600.1,"e":7,"b":"running","m":"manual","v":2.5,"c":2.5,"g":3.0,
 "d":0.69,"s":1314,"pe":1008}
```

| key | meaning |
|---|---|
| `t` | wall clock, epoch seconds, float |
| `e` | epoch id |
| `b` | belt state as decoded by `frames.py` |
| `m` | mode |
| `v` | actual belt speed, km/h |
| `c` | commanded speed, or `null` |
| `g` | standing goal, or `null` |
| `d` | pad cumulative distance, km |
| `s` | pad cumulative steps |
| `pe` | pad cumulative `elapsed_s` |

`d`, `s` and `pe` are the pad's **cumulative** values, stored raw rather than as
deltas. Deliberate: the residual stays recomputable from the sample file itself, so a
bug in today's attribution logic is repairable tomorrow from evidence that was never
thrown away. It also means a lost or stale `state.json` can be rebuilt by replaying
the samples.

**Recording window.** Record while `belt` is `"starting"` or `"running"`, and for
`TAIL_S = 5` seconds after it leaves those states, so both ends of the ramp are
captured. Idle frames are never written: they are all zeros and carry no signal.

Daily files bound any single read and make the file layout self-describing. **Nothing
is ever pruned** — decided 2026-08-26. Growth is roughly 200 KB per walking hour,
about 70 MB per year at an hour a day. No retention setting exists, so there is no
retention code and no way to lose a curve by misconfiguration.

### 2.3 `bouts.jsonl`

Append-only, one row per closed bout, roughly 1000 rows per year.

```json
{"e":7,"start":1756137600.1,"end":1756138283.4,"observed":true,
 "dur_s":683,"dist_km":0.47,"steps":903,"v_avg":2.48,"v_max":3.0,"n":683}
```

An unobserved bout carries `"observed":false`, exact totals from the residual, and
`start`/`end` bounding the **uncertainty window** rather than claiming a precise
time. It has no `v_avg`, `v_max` or `n`, because no samples exist for it.

The flag marks **provenance, not accuracy**. The residual makes the totals exact.
What is missing is samples (no curve can be drawn) and placement (we know the window,
not where inside it). The analysis window needs the flag to decide whether a chart
row is drawable.

### 2.4 `state.json`

Rewritten atomically (`tmp` + `os.replace`).

```json
{"epoch":7,
 "last_pad":   {"elapsed_s":1008,"dist_km":0.69,"steps":1314},
 "attributed": {"elapsed_s":900, "dist_km":0.61,"steps":1180},
 "open_bout":null,
 "last_seen_wall":1756138283.4}
```

`last_pad − attributed` is the residual, persisted across bridge restarts. This is
the file D-5 deferred; ADR D-6 (§9) records why it now exists.

`last_pad`, `attributed` and `last_seen_wall` advance on **every decoded frame**,
including idle ones — the store sees every frame the bridge does, and only §2.2
decides which of them also produce a sample row.

Written on bout open, bout close and epoch change, and otherwise at most once every
10 s while frames are arriving, plus once on clean shutdown. A crash between writes costs up to
10 s of `attributed`, which surfaces as a spurious ≤10 s residual — and §2.2 makes
even that repairable from the samples.

---

## 3. Three processes, three jobs

| Component | Job | Never does |
|---|---|---|
| `walkingpad/store.py` (new), called from `bridge.py` | Writes samples, bouts and state | Reads, aggregates, or blocks |
| `bin/walkingpad-stats` (new) | Answers queries with JSON on stdout | Touches the pad or BlueZ |
| `WalkingpadService.qml` ring buffer | Feeds the live popup chart | Any file I/O |

### 3.1 The writer

`store.py` exposes a small class, constructed once by `Bridge` and called from
`on_notify` **after** the status emit, so the UI is never delayed by disk. It holds
no BLE knowledge and takes a decoded `Status` plus the bridge's `commanded_speed` and
`goal_speed`.

Appends use a plain `open(..., "a")`, `write`, `flush` — no `fsync` per row, which at
1 Hz would be pointless wear for data that §2.2 can reconstruct.

⚠ **The poll loop is safety-critical** (facts 1 and 9; `Safety.stale_after_s` is
3.0 s and `IDLE_POLL_INTERVAL_S` is already tuned against it in `bridge.py`). The
writer must therefore never read a file, never aggregate, never scan a directory, and
never do unbounded work. Any exception it raises is caught, emitted once as
`{"t":"error","code":"store_write_failed"}`, and then suppressed — a full disk must
degrade to "no stats" and must never stop the belt controls working.

### 3.2 The query tool

`bin/walkingpad-stats`, system python, stdlib only, same shebang and
`sys.dont_write_bytecode` guard as `bin/walkingpad-bridge`. Short-lived: the analysis
window spawns it via `Process`, reads one JSON object from stdout, and it exits.

```
walkingpad-stats sessions [--since ISO] [--until ISO] [--gap MIN]
walkingpad-stats session  --start EPOCH [--gap MIN]      # one session + its samples
walkingpad-stats totals   --by day|week|month|all
walkingpad-stats residuals
```

All accept `--store PATH`. All emit exactly one JSON object.

Why a separate process rather than extending the bridge: aggregating a year of
samples is unbounded work, and the bridge is the process that must answer a status
query every second to keep the safety gate open. Why not parse in QML: the same
unbounded work would land on the UI thread, and the gap-grouping and residual maths
belong in Python where pytest can reach them (ADR D-1). This also matches §3.2 of the
design spec — short-lived child processes, no daemon.

---

## 4. `WalkingpadChartStack.qml`

One reusable component, instantiated twice. Rows share one x-axis and **one cursor
index**, which is what makes the crosshair linked: a single `MouseArea` spans the
whole stack, maps `mouseX` to a sample index, and every row redraws against that one
property. Per-row mouse areas would produce N independent cursors that have to be
kept in sync, which is the bug this avoids by construction.

Rows draw on `Canvas`.

### 4.1 Rows

| Row | Kind | Notes |
|---|---|---|
| Speed | filled area | commanded speed and goal as thin overlay lines |
| Cadence | line | derived; see below |
| Distance | step line | cumulative |
| Steps | step line | cumulative |

**Speed carries the overlays rather than getting its own rows.** Same unit, and the
gap between commanded and actual *is* the ~0.25 km/h per second ramp (fact 2).
Splitting them into separate rows would hide the one thing that chart exists to show.

**Belt state and mode render as a background band** across all rows, not as a row.
A pause or a standby stretch is then shaded once instead of plotted four times.

**Cadence is derived, not reported.** `Δsteps / Δt × 60`, smoothed over a 10 s
rolling window. The window is required, not cosmetic: at roughly 1.3 steps/s with
integer steps at 1 Hz, the raw quotient jumps by ±40 spm between adjacent samples.

**Distance renders as a step line, deliberately.** The pad quantises distance to
10 m, so at 2.5 km/h it advances once every ~14 s. Interpolating it into a smooth
line would draw a precision the data does not have.

### 4.2 Colour

The component **owns no colour** and imports neither palette. It takes `fg`, `accent`
and `muted` as properties, and layers with opacity. Callers supply them:

- popup → `bar.foreground` and friends, as the popup does today
- window → `Color.menu.text`, `Color.menu.selectedText`, `Color.menu.border`

This is forced, not stylistic: the panel loader injects `service` but **not** `bar`
(`shell.qml:629-637`), so the window cannot use the bar palette, while the popup
already does. A component that imported either would be unusable in the other. §5.5
of the design spec is satisfied in both.

---

## 5. The popup

`WalkingpadPanel.qml` gains:

1. **A ring buffer of samples** in `WalkingpadService.qml`, appended in
   `handleStatus`, capped at 3600 entries (one hour at 1 Hz). No file access.
   "Current session" is the tail of that buffer since the last gap longer than
   `gap_minutes`, computed in JS — cheap on a bounded buffer.
2. **A compact `WalkingpadChartStack`**, roughly 24 px per row, below the hero and
   above the existing Distance/Steps/Time pairs. Its cursor is driven by hover *and*
   by the existing arrow-key navigation, since this panel is keyboard-first
   (`PanelKeyCatcher`, `moveCursor`).
3. **An "Analysis" action** in the existing `actions` array, so cursor navigation
   reaches it. It calls `bar.shell.summon("io.github.shllg.walkingpad")` and then
   `root.close()`.

Height is `fittedContentHeight(column.implicitHeight)`, so the panel grows to fit.
Four rows at 24 px plus an axis is roughly 110 px added to a `Style.space(340)`-wide
column. If that reads as too tall on hardware, the fix is fewer rows in the popup —
not a second chart implementation.

---

## 6. The analysis window

### 6.1 Mechanism

A new overlay-kind entry point, `WalkingpadWindow.qml`. Precedent, verified in the
installed shell and in an installed third-party plugin:

- `hypr-whichkey` declares `kinds: ["overlay"]` with `entryPoints.overlay`, and its
  overview mode is a full-screen `PanelWindow` on `WlrLayer.Overlay` with
  `keyboardFocus: WlrKeyboardFocus.Exclusive`, a scrim, and content
  `anchors.centerIn: parent`.
- The panel loader mounts it and injects `omarchyPath`, `shell`, `manifest`,
  `barWidgetRegistry`, `pluginRegistry` and `service` (`shell.qml:623-651`). The root
  is a plain `Item` exposing `opened`, `open(payloadJson)` and `close()`.

Manifest changes:

```json
"kinds": ["service", "bar-widget", "overlay"],
"entryPoints": {
  "service":   "WalkingpadService.qml",
  "barWidget": "WalkingpadPanel.qml",
  "overlay":   "WalkingpadWindow.qml"
}
```

### 6.2 Two consequences of adding the kind — both verified, neither cosmetic

⚠ **`shell.summon/hide/toggle` re-routes for this plugin id.**
`shell.isBarWidgetPanelPlugin()` returns false for any plugin holding `bar-widget`
*and* an overlay/panel/menu kind, because "plugins that are also panel/overlay/menu
kinds are owned by the panel loader" (`shell.qml:426-438`). `omarchy.menu` is the
first-party precedent for the combination, so it is supported.

This does **not** affect the documented keybinding path: the popup's IPC is
`IpcHandler { target: "walkingpad" }` (`WalkingpadPanel.qml:189`), a Quickshell IPC
target independent of the shell's plugin routing. `qs ipc call walkingpad toggle`
keeps working. Only `omarchy-shell shell toggle io.github.shllg.walkingpad` changes
meaning, and nothing documented uses it. **Verify both paths by hand before merging.**

⚠ **`keepLoaded: true` stops being inert.** D-5 recorded that `_syncServices()` never
reads it, which was correct — with only `service` and `bar-widget` kinds it did
nothing. The panel loader *does* read it (`shell.qml:625`), so adding the overlay
kind makes the window mount permanently rather than on first open. Keep it: the
window holds no BLE resource, mounting is cheap QML, `summon` then delivers the
payload immediately via `deliverIfLoaded`, and the alternative is an async Loader race
on every open. D-6 records the correction.

### 6.3 Content (v1)

- **Period selector** — day / week / month / all, driving a totals strip.
- **Totals strip** — distance, steps, time, average speed for the selected period,
  with the unobserved share shown separately rather than folded in silently.
- **Session list** — grouped by day, each row showing its bouts, with unobserved
  bouts visibly distinguished.
- **Chart stack** — the full four rows for the selected session, tall, with the
  linked crosshair and range selection.

Explicitly out of scope for v1: calendar heatmap, goals or targets, export, and any
comparison across sessions.

---

## 7. Configuration

`gap_minutes` (default 5) is the only user-facing knob, exposed through
`manifest.barWidget.defaults`/`schema` like the existing preferences.

Per D-5 that channel carries **preferences only, never safety policy**, and it may
never arrive at all when no bar widget exists. Both conditions hold here: the gap
rule is applied at read time, affects presentation only, and has a working default.

Both readers apply it independently: the window passes it to `walkingpad-stats` as
`--gap`, and the popup applies it in JS to its own ring buffer (§5). The bridge never
sees it, because nothing it does depends on where a session boundary falls.

---

## 8. Testing

### 8.1 Unit — `tests/test_store.py`

Feed decoded `Status` objects to `store.py` directly, against `tmp_path`:

- counters continue across a stop → two bouts, one epoch, no residual
- counters reset to zero → epoch increments, `attributed` resets, no negative delta
- link loss then reconnect with advanced counters → one unobserved bout with exact
  totals and a `[last_seen_wall, now]` window
- residual below `MIN_RESIDUAL` → no bout written
- first-ever frame with high counters → baseline adopted, no bout written
- restart with an `open_bout` → closed at `last_seen_wall`, not at now
- `belt` 9/8/7 → samples recorded, bout not yet opened
- write failure (unwritable store) → one error emitted, bridge keeps running

### 8.2 Unit — `tests/test_stats_query.py`

Gap grouping at boundaries (exactly at the gap, either side of it), totals by period,
sessions containing unobserved bouts, and an empty store.

### 8.3 Integration and hardware

- **Replay:** tests generate a synthetic fixture into `tmp_path` and run it through
  `--replay`, asserting the written files. Generated rather than tracked because
  these cases — a counter reset in particular — have never been captured on hardware,
  and a tracked fixture would imply otherwise. Generating in `tmp_path` also keeps
  `tests/test_no_pii.py` out of the picture entirely.
- **`docs/smoke-test.md` gains §4e**, the first thing to run on hardware: walk one
  minute, stop twenty seconds, walk one minute, and record whether the counters
  continue. This upgrades §1.1's operator report to a measured fact, or refutes it.
  It moves the belt, so it inherits the existing standing-clear requirement.
- **§4f:** leave the pad idle until it sleeps, wake it, and record whether the
  counters reset and how long it took. This is open question Q7's counter half.
- **QML:** no unit harness exists. Verify by hand — the popup chart under a theme
  with no accent contrast (§5.5), the window opening from the popup button, and both
  IPC paths from §6.2.

---

## 9. Changes to `docs/design-spec.md`

- **§5.7** replaced by a pointer to this document. Its four open questions are all
  answered here: which figures the bar shows, whether a sparkline is worth it, whether
  totals are accumulated locally (yes, and the state file now exists), and what the
  dormant state shows.
- **New ADR D-6 — the state file exists (2026-08-26).** Supersedes D-5's "no state
  file yet" clause and only that clause; D-5's safety boundary is untouched and still
  governs §7 above. Records: what forced it (this design), where it lives, why
  `0700`, why the bridge writes it and a separate process reads it, and the correction
  that `keepLoaded` is inert only in the absence of a panel/overlay kind.
- **§11 open questions:** Q7 gains the counter-reset half, pointing at smoke-test §4f.

---

## 10. Known limitation, recorded rather than papered over

**An unobserved reset under-counts, and no heuristic can fix it.** If the pad resets
its counters entirely inside a gap and then accumulates past our last-seen value —
we last saw 1008 s, the pad slept and reset, then 1200 s was walked — we read 1200,
judge it the same epoch, and attribute a 192 s residual instead of 1200 s. The
walking before the reset is unrecoverable.

Nothing in the frame distinguishes this case. Byte `[16]` varies with state
(0x02, 0x03, 0x00) and is still undecoded; `[15]` and `[17]` were 0x00 in every
capture. A wall-clock heuristic ("a long gap probably means a sleep") would silently
invent numbers, which is worse than under-counting a case the totals strip already
shows as unobserved.

Smoke-test §4f may narrow this by establishing how long the pad takes to reset. It
cannot close it.
