# Charts redesign — popup, analysis window, rendering (Stage 3.1) — decisions

**Goal:** make the popup carry one chart that tells you something its numbers
cannot, restructure the analysis window so period and session never compete, and
replace the chart rendering with primitives that look like the rest of the shell.

**Supersedes** `docs/plans/2026-08-26-stats-and-analysis.md` §4, §5.2 and §6.3 —
the storage model, the attribution rules and the query tool's existing contract in
§1–§3 are untouched and still govern.

**Status:** implemented 2026-08-26. Reduced on 2026-08-28 to the decisions its
section numbers are cited by and the work still open; what was built is in the
code. **Section numbers are frozen** — 15 of them are cited by name from comments
in the QML, `WalkingpadModel.js` and `bin/walkingpad-stats`, so a renumber
orphans those citations. That includes the numbered items in §0.

---

## 0. What the shipped version got wrong

Recorded so the fixes are not re-litigated later.

1. **Cumulative rows carried no information.** Distance and steps only rise, and
   each terminated at a number printed in text beside it.
2. **Speed-over-time was in the wrong place.** The popup's large readout already
   states the current speed, and the history is not actionable mid-walk.
3. **Period and session selection competed.** Both were on screen, both drove the
   same chart, and nothing said which had won.
4. **A navigation control was put in the belt-control row.** Start, Stop, −, +
   and Set act on the treadmill; Stats navigates. The sixth cell shrank every
   real control to ~52 px against ~55 px of content, in a component with neither
   `elide` nor `clip`. The width was the symptom; the category error was the cause.
5. **Canvas was the wrong primitive for bars.** Hand-stroked rectangles look
   muddy next to the rest of the shell.

---

## 1. The popup chart

**One chart: steps per minute, on a real wall-clock axis.** 60 fixed one-minute
buckets aligned to wall-clock minutes, newest at the right — the axis is always
the last hour, matching the ring buffer's own bound.

- **A minute with no walking draws no bar.** The gap is the information: it says
  you were at the desk and not moving. That is why the axis must be real time and
  not sample index.
- **The in-progress minute renders distinctly.** A bucket still filling would
  otherwise read as a slowdown.
- **Steps, not distance.** The pad quantises distance to 10 m, so a minute at
  2.5 km/h advances roughly four quanta and the bars would be visibly steppy.
  Steps is an integer counter with real resolution.

**Data source is the existing in-memory ring buffer, and the popup performs no
file I/O.** That constraint is unchanged and load-bearing. The buffer only records
while the belt is `starting` or `running` plus a 5 s tail, so a pause produces a
gap in sample timestamps — which is exactly what the empty buckets are derived
from. No extra state is needed.

---

## 2. The analysis window

A left rail and a right pane, restructured around one rule: **exactly one thing is
selected at a time.**

### 2.1 Layout

- Period tabs scope the rail; the session list below shows the sessions inside the
  selected period.
- **Default is Today, combined**, with no session selected.
- Selecting a session swaps the right pane; selecting a period clears the session.
- **The header always names what is displayed.** That is what removes the
  ambiguity; the layout alone would not.

### 2.2 Period mode — the default

Three charts: steps per bucket, speed over time, and time at speed.

⚠ **Period mode never loads raw samples.** All three read the bucketed `profile`
object from §4. The speed curve in this mode is therefore a line through
per-bucket averages, not a per-second trace — a day holds up to 86 400 samples,
and pulling them into QML would put exactly the unbounded work on the UI thread
that the previous spec's §3.2 exists to prevent. The per-second trace is what
session mode is for.

### 2.3 Session mode — one walk

Speed, cadence and steps-per-minute for a single walk.

**Commanded and goal overlay the speed area rather than getting their own rows.**
Fact 2: reported speed is ACTUAL and the pad ramps ~0.25 km/h per second with no
ACK anywhere, so the gap between commanded and actual *is* that ramp. Splitting
them into separate rows hides the one thing worth seeing.

Written values sit above the charts in both modes, with the unobserved share
stated separately. **`observed: false` marks provenance, not accuracy:** the
residual makes the totals exact; what is missing is samples and placement.

### 2.4 Bucket selection

Deterministic, from a fixed ladder: 1 min, 5 min, 15 min, 1 h, 6 h, 1 day, 1 week.
Pick the smallest rung whose bar count is at most 120, targeting roughly 60.

**The axis spans the data's actual extent within the period** — first activity to
last — not the full calendar span. A day with two walks between 09:00 and 14:00
draws five hours of axis, not twenty-four hours of mostly nothing.

### 2.5 Out of scope

Cumulative distance and cumulative steps as chart rows, in either mode: they rise
monotonically to a number printed above them. Also still out, unchanged from the
previous spec: calendar heatmap, goals, export, cross-session comparison.

---

## 3. Rendering

### 3.1 Bars are Rectangles, not Canvas

A bar chart is rectangles. A `Repeater` of `Rectangle`s gives crisp edges at any
DPI, takes theme colours directly, animates for free, and needs no repaint
plumbing or manual antialiasing. It also removes code.

### 3.2 Curves use `QtQuick.Shapes`

**Not a new dependency:** it ships with Qt 6 and the shell already imports it
(`Ui/SpeedTestOverlay.qml`, `Ui/BorderOverlay.qml`,
`plugins/background/Background.qml`), so the no-third-party-runtime-dependency
constraint holds.

### 3.3 Aesthetic rules

- No box around the plot. A baseline, and at most two faint horizontal guides.
- Axis labels outside the plot, small and muted, never overlaying data.
- Real padding. The shipped charts crowded their own edges.
- Corner radius from `Style.cornerRadius`, which mirrors Hyprland's `rounding`
  and is `0` on the current theme. Never a literal.

### 3.4 Sane stretching

- Bars have a **maximum width**; the group left-aligns rather than stretching.
  Ten bars must look like ten bars, not ten billboards.
- For time-series charts the plot width is proportional to the data's actual share
  of the period. A six-minute session inside a day view occupies six minutes of
  width.

### 3.5 Component split

`WalkingpadChartStack.qml` was deleted — 857 lines doing four row types,
background bands and cursor handling at once. Rewriting it as one monolith would
have repeated the mistake. It became `WalkingpadBarChart.qml` (bucketed bars,
Rectangles only, used by the popup and both window modes) and
`WalkingpadTimeChart.qml` (continuous series, `QtQuick.Shapes`, window only).

**The rules that are load-bearing, and are why the split is shaped this way:**

- They own **no colour** and import no palette. `fg`, `accent` and `muted` arrive
  as properties. This is forced, not stylistic: the panel loader injects `service`
  but not `bar` (`shell.qml:629-637`), so the window cannot use the bar palette
  while the popup already does. A component importing either would be unusable in
  one of its two homes. Sizes arrive as properties too.
- `WalkingpadTimeChart` keeps **one** `MouseArea` spanning the whole chart. Per-row
  mouse areas produce N cursors that must then be kept in sync; that bug is avoided
  by construction, not by discipline.
- Neither owns its cursor index. They render what they are given and emit
  `cursorMoved`.
- Degenerate input renders an empty frame and never throws: empty data, one point,
  zero range, a null entry, non-monotonic time.
- Lines break across a gap larger than the sampling cadence and across an epoch
  change. A line drawn across an unobserved interval is a line that did not
  happen, and stats §10's rule stands: inventing numbers is worse than showing a
  gap.

---

## 4. The query tool's `profile` subcommand

The period pane needs steps per bucket across a whole day. **Scanning a day of
samples is unbounded work, and that must not land in the bridge or on the UI
thread**, so the bucketing happens in Python — one pass over the sample files in
range, one JSON object out. `--bucket auto` applies §2.4's ladder server-side and
reports what it chose. Run `walkingpad-stats profile --help` for the flags.

**A bucket with no samples is omitted, not emitted as zero.** The consumer draws
nothing there, and the distinction between "no data" and "measured zero" survives
into the UI.

Session mode keeps using `session --start`, which already returns samples.

---

## 5. Popup controls

**Stats leaves the control row** and becomes a small action at the right-hand end
of the `CONTROL` section header, where a secondary navigation action belongs
(§0.4). The control row returns to five buttons at their original width.

**Keyboard navigation is three zones:** `stats` header → `chart` → `controls`.
Vertical arrows move between zones, horizontal within one. Entering the chart zone
seeds the cursor at the newest bucket; leaving it clears the cursor. The zone is
unreachable when there is no data.

⚠ `activateCursor()` stays gated so Enter can never fire a belt command from a
non-control zone. **That gate is safety-relevant and must not be relaxed.**

---

## 6. Testing

- **Bucketing arithmetic lives in Python precisely so pytest can reach it**
  (ADR D-1). The popup's own 60-bucket derivation is the one copy in JS, and it
  must agree with the Python at bucket boundaries; the convention is stated in
  both.
  ⚠ They diverged on first implementation — 118 steps against 120 for the same
  minute, because the query tool dropped the sample opening the interval that
  crosses into the first bucket. Two tests now pin the agreement, including across
  a UTC day-file boundary, which is where every "Today" query lands.
- **QML has no unit harness.** `bin/walkingpad-fixture-store` generates a week of
  history by driving the real `walkingpad.store.Store`, and `bin/walkingpad-harness`
  renders either entry point against it or against real history.
  ⚠ `--offscreen` verifies only that the QML parses and that every type resolves.
  Both entry points are built on `PanelWindow`, which needs the Wayland backend;
  offscreen it fails to instantiate and nothing inside ever runs, so data and
  bindings need a real display.
- `bin/check-repo` must stay green, including the real plugin validator and the
  QML lint. ⚠ That lint was hollow until 2026-08-26: `/usr/bin/qmllint` is the
  Qt 5 binary and exits 255 silently on Qt 6 QML, and `-I $OMARCHY_PATH/shell`
  does not resolve `qs.*`. See the skills for the correct invocation.

**Still open, and still by hand:** the popup chart during a real walk. It needs a
moving belt, so it belongs with `docs/smoke-test.md` rather than here.

---

## 7. Constraints that do not move

- Nothing here writes to the pad. If an implementation step needs a command frame,
  the step is wrong.
- The poll loop, `Safety`, the start sequence and the goal servo are untouched.
- The rest — no hardcoded colour or size, no new runtime dependency, no committed
  symlink, no real device address in tracked content — is `AGENTS.md`'s
  "Hard constraints", which governs and is not restated here.
