# Charts redesign — popup, analysis window, rendering (Stage 3.1) — design spec

**Goal:** make the popup carry one chart that tells you something its numbers
cannot, restructure the analysis window so period and session never compete, and
replace the chart rendering with primitives that look like the rest of the shell.

**Supersedes** `docs/plans/2026-08-26-stats-and-analysis.md` §4, §5.2 and §6.3 —
the storage model, the attribution rules and the query tool's existing contract in
§1–§3 are untouched and still govern.

**Status:** design approved 2026-08-26 after review of the shipped Stage 3 UI on
hardware. Nothing here writes to the pad; this remains a read-side concern end to
end.

---

## 0. What the shipped version got wrong

Recorded so the fixes are not re-litigated later.

1. **Cumulative rows carried no information.** Distance and steps only ever rise,
   and each terminated at a number printed in text directly beside it. Two of the
   four popup rows were redundant by construction.
2. **Speed-over-time was in the wrong place.** In a bar popup the large readout
   already states the current speed, and the history is not actionable mid-walk.
   It belongs where someone is studying a walk, not glancing at one.
3. **Period and session selection competed.** Both were on screen, both drove the
   same chart, and nothing said which had won.
4. **A navigation control was put in the belt-control row.** Start, Stop, −, + and
   Set act on the treadmill; Stats navigates. Adding a sixth cell shrank every
   real control from ~63 px to ~52 px against ~55 px of content, in a component
   with neither `elide` nor `clip`. The width was the symptom; the category error
   was the cause.
5. **Canvas was the wrong primitive for bars.** Hand-stroked rectangles look
   muddy next to the rest of the shell.

---

## 1. The popup chart

**One chart: steps per minute, on a real wall-clock axis.**

- **60 fixed one-minute buckets**, aligned to wall-clock minutes, newest at the
  right. The axis is always the last hour, matching the ring buffer's own bound.
- **A minute with no walking draws no bar.** The gap is the information: it says
  you were at the desk and not moving. This is why the axis must be real time and
  not sample index.
- **The in-progress minute renders distinctly** (reduced opacity). A bucket that
  is still filling would otherwise read as a slowdown.
- Steps, not distance: the pad quantises distance to 10 m, so a minute at
  2.5 km/h advances roughly four quanta and the bars would be visibly steppy.
  Steps is an integer counter with real resolution.
- Bar height scales to the tallest bucket in view, with the peak value labelled.
  No y-axis furniture beyond that.

**Data source is the existing in-memory ring buffer.** Steps per bucket is the
difference in the cumulative `s` field across bucket edges. The popup performs no
file I/O — that constraint is unchanged and load-bearing.

The buffer only records while the belt is `starting` or `running` plus a 5 s tail,
so a pause produces a gap in sample timestamps, which is exactly what the empty
buckets are derived from. No extra state is needed.

**Written values are unchanged.** They were right.

---

## 2. The analysis window

### 2.1 Layout

A left rail and a right pane. Exactly one thing is selected at a time.

```
┌────────────────────────────────────────────────┐
│ WalkingPad · History                      [×]  │
├──────────────┬─────────────────────────────────┤
│ ● TODAY      │  Today — all sessions combined  │
│   Week       │                                 │
│   Month      │  2.41 km   4812 steps   38:20   │
│   All time   │  avg 2.5 km/h                   │
│              │  + 12 min unobserved            │
│ SESSIONS     │                                 │
│  09:04 18min │  …charts…                       │
│  11:20 12min │                                 │
└──────────────┴─────────────────────────────────┘
```

- Period tabs scope the rail; the session list below shows the sessions inside the
  selected period.
- **Default is Today, combined.** No session selected.
- Selecting a session swaps the right pane and the header reads
  `Session · 09:04 – 09:22`. Selecting a period clears the session.
- The header always names what is displayed. That is what removes the ambiguity;
  the layout alone would not.

### 2.2 Period mode — the default

| Chart | What it answers | Data |
|---|---|---|
| Steps per bucket (bars, real time axis) | When you moved, and how much | `profile.buckets[].steps` |
| Speed over time (area, gaps preserved) | Which speeds you actually used | `profile.buckets[].v_avg` |
| Time at speed (horizontal bars) | Whether you held a speed or drifted | `profile.speed_histogram` |

⚠ **Period mode never loads raw samples.** All three charts read the bucketed
`profile` object from §4. The speed area in this mode is therefore a curve through
per-bucket averages, not a per-second trace — a day holds up to 86 400 samples and
pulling them into QML would put exactly the unbounded work on the UI thread that
§3.2 of the previous spec exists to prevent. The per-second trace is what session
mode is for.

### 2.3 Session mode — one walk

| Chart | What it answers |
|---|---|
| Speed (filled area, commanded and goal as thin overlays) | The ramp. Fact 2: reported speed is ACTUAL and the pad ramps ~0.25 km/h per second with no ACK anywhere — the gap between commanded and actual *is* that ramp, and splitting them into separate rows would hide the one thing worth seeing |
| Cadence (smoothed line) | Effort, independent of belt speed. Derived, never reported |
| Steps per minute (bars) | Same visual language as the popup, so the two read as one system |

Written values sit above the charts in both modes, with the unobserved share
stated separately. `observed: false` marks provenance, not accuracy: the residual
makes the totals exact; what is missing is samples and placement.

### 2.4 Bucket selection

Deterministic, from a fixed ladder: 1 min, 5 min, 15 min, 1 h, 6 h, 1 day, 1 week.
Pick the smallest rung whose resulting bar count is at most 120, targeting roughly
60. The axis spans the data's actual extent within the period — first activity to
last — not the full calendar span. A day with two walks between 09:00 and 14:00
draws five hours of axis, not twenty-four hours of mostly nothing.

### 2.5 Removed from v1

Cumulative distance and cumulative steps as chart rows, in both modes. They rise
monotonically to a number printed above them. Also still out of scope, unchanged
from the previous spec: calendar heatmap, goals, export, cross-session comparison.

---

## 3. Rendering

### 3.1 Bars are Rectangles, not Canvas

A bar chart is rectangles. A `Repeater` of `Rectangle`s gives crisp edges at any
DPI, takes theme colours directly, animates for free, and needs no repaint
plumbing or manual antialiasing. This is the single largest visual improvement
available and it also removes code.

### 3.2 Curves use `QtQuick.Shapes`

Verified present at `/usr/lib/qt6/qml/QtQuick/Shapes` and already used by
first-party Omarchy UI — `Ui/SpeedTestOverlay.qml`, `Ui/BorderOverlay.qml`,
`plugins/background/Background.qml`. **Not a new dependency:** it ships with Qt 6
and the shell already imports it, so the no-third-party-runtime-dependency
constraint holds.

Antialiased vector paths with real joins and caps, and a gradient fill for the
speed area.

### 3.3 Aesthetic rules

- No box around the plot. A baseline, and at most two faint horizontal guides.
- Axis labels outside the plot, small and muted, never overlaying data.
- Real padding. The shipped charts crowd their own edges.
- Corner radius from `Style.cornerRadius`, which mirrors Hyprland's `rounding`
  and is `0` on the current theme. Never a literal.

### 3.4 Sane stretching

- Bars have a **maximum width**; the group left-aligns rather than stretching.
  Ten bars must look like ten bars, not ten billboards.
- For time-series charts the plot width is proportional to the data's actual share
  of the period. A six-minute session inside a day view occupies six minutes of
  width.

### 3.5 Component split

`WalkingpadChartStack.qml` is deleted. It is 857 lines doing four row types,
background bands and cursor handling at once, and rewriting it as one monolith
would repeat the mistake.

| Component | Job |
|---|---|
| `WalkingpadBarChart.qml` | Bucketed bars on a time axis. Rectangles only. Used by the popup and both window modes |
| `WalkingpadTimeChart.qml` | Continuous series on a time axis — speed area with overlays, cadence line. `QtQuick.Shapes`. Window only |

**Both keep the rules that were already load-bearing:**

- They own **no colour** and import no palette. `fg`, `accent` and `muted` arrive
  as properties. This is forced, not stylistic: the panel loader injects `service`
  but not `bar` (`shell.qml:629-637`), so the window cannot use the bar palette
  while the popup already does. A component importing either would be unusable in
  one of its two homes.
- Sizes arrive as properties too, and callers pass `Style.space(...)` values.
- `WalkingpadTimeChart` keeps **one** `MouseArea` spanning the whole chart, mapping
  `mouseX` to a sample index. Per-row mouse areas produce N cursors that must then
  be kept in sync; that bug is avoided by construction, not by discipline.
- Neither owns its cursor index. They render what they are given and emit
  `cursorMoved`.
- Degenerate input renders an empty frame and never throws: empty data, one point,
  zero range, a null entry, non-monotonic time.
- Lines break across a gap larger than the sampling cadence and across an epoch
  change. A line drawn across an unobserved interval is a line that did not
  happen, and §10's rule stands: inventing numbers is worse than showing a gap.

---

## 4. The query tool gains one subcommand

The period pane needs steps per bucket across a whole day. Scanning a day of
samples is unbounded work, and §3.2 of the previous spec keeps unbounded work out
of both the bridge and the UI thread. So the bucketing happens in Python.

```
walkingpad-stats profile [--since ISO] [--until ISO] [--bucket auto|SECONDS]
                         [--gap MIN] [--store PATH]
```

One JSON object, one pass over the sample files in range:

```json
{"bucket_s": 300,
 "start": 1756137600.0,
 "end": 1756155600.0,
 "buckets": [{"t": 1756137600.0, "steps": 412, "dist_km": 0.31,
              "dur_s": 300, "v_avg": 2.5}],
 "speed_histogram": [{"kmh": 2.5, "dur_s": 700}],
 "totals": {"dur_s": 1183, "dist_km": 0.82, "steps": 1603, "v_avg": 2.5,
            "unobserved_s": 0, "unobserved_dist_km": 0.0,
            "unobserved_steps": 0}}
```

A bucket with no samples is **omitted**, not emitted as zero. The consumer draws
nothing there, and the distinction between "no data" and "measured zero" survives
into the UI.

`--bucket auto` applies §2.4's ladder server-side, and `bucket_s` reports what was
chosen. Session mode keeps using the existing `session --start`, which already
returns samples.

Existing subcommands are unchanged.

---

## 5. Popup controls

**Stats leaves the control row** and becomes a small action at the right-hand end
of the `CONTROL` section header, where a secondary navigation action belongs. The
control row returns to five buttons at their original width and the overflow is
gone — not clipped, not hidden behind a wider popup.

**Keyboard navigation becomes three zones**, extending the existing two-zone model:
`stats` header → `chart` → `controls`. Vertical arrows move between zones,
horizontal moves within one. Entering the chart zone seeds the cursor at the
newest bucket; leaving it clears the cursor. The chart zone is unreachable when
there is no data. `activateCursor()` stays gated so Enter can never fire a belt
command from a non-control zone — that gate is safety-relevant and must not be
relaxed.

`blocked: root.editing` continues to hand keys to the Set editor while it is open.

---

## 6. Testing

- **Python:** the `profile` subcommand gets the same treatment as the others —
  bucket ladder selection at boundaries, omitted-vs-zero buckets, an empty store,
  a period with no samples, malformed rows skipped and counted, and exactly one
  JSON object on stdout.
- **Bucketing arithmetic** is in Python precisely so pytest can reach it (ADR D-1).
  The popup's own 60-bucket derivation is the one copy in JS; it must agree with
  the Python at bucket boundaries, and the boundary convention is stated in both.
- **QML has no unit harness.** Verify by hand: the popup chart during a walk and
  while idle, the window in both modes, a period containing an unobserved bout, a
  session with no drawable samples, and the three-zone keyboard path including
  that Enter does nothing outside the control zone.
- `bin/check-repo` must stay green, including the real plugin validator.

---

## 7. Constraints that do not move

- Nothing here writes to the pad. If an implementation step needs a command frame,
  the step is wrong.
- The poll loop, `Safety`, the start sequence and the goal servo are untouched.
- No hardcoded colour, size or radius anywhere, not even as a fallback.
- No new runtime dependency. `QtQuick.Shapes` is part of Qt 6 and already used by
  the shell.
- No committed symlink; one makes the plugin uninstallable for every user.
- No real device address in tracked content.
