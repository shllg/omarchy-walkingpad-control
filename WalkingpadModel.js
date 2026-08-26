.pragma library

// Pure presentation and chart-series logic shared by WalkingpadPanel.qml and
// WalkingpadWindow.qml. No colours and no sizes live here -- those come from
// qs.Commons at the call site (spec §5.5). Neither do the chart COMPONENTS
// derive anything: charts §3.5 says they render what they are given, so every
// derivation lives here, in one place, for both hosts.

// Nerd Font glyphs, by codepoint rather than as literals: these all sit in the
// Supplementary Private Use Area, where a stray editor or transport re-encoding
// silently truncates them into a different, valid-looking character. Every one
// was checked against JetBrainsMono Nerd Font with fc-list :charset=.
//
// The control row carries no glyphs. Measured against this theme: a glyph plus
// Style.spacing.controlGap costs 16.4 px per cell, which put the widest control
// at 71.4 px inside a 63.2 px slot even at five buttons. Only ICON_WALK,
// ICON_RUN and ICON_STATS have room to be drawn, so only those exist.
var ICON_WALK = String.fromCodePoint(0xF0583)    // nf-md-walk
var ICON_RUN = String.fromCodePoint(0xF070E)     // nf-md-run
var ICON_STATS = String.fromCodePoint(0xF0128)   // nf-md-chart_bar

// Fact 8: "running" is the ONLY state that means the belt is moving, so the
// running glyph is gated on it directly rather than on the absence of anything.
function barIcon(linkState, beltState) {
  if (linkState === "connected" && beltState === "running") return ICON_RUN
  return ICON_WALK
}

// Fact 3: the pad sleeps and stops advertising, and that is its NORMAL resting
// state rather than a fault. Dimming says "nothing to do here" without
// borrowing the urgent role for a pad that is simply asleep.
function barDimmed(linkState) {
  return linkState !== "connected"
}

function stateLabel(linkState, beltState, hasStatus, ramping) {
  if (linkState === "scanning") return "Looking for pad"
  if (linkState === "absent") return "Asleep"
  if (linkState === "unavailable") return "In use elsewhere"
  if (linkState === "lost") return "Connection lost"
  if (linkState === "error") return "Bridge error"
  if (linkState !== "connected") return "Unknown"
  if (!hasStatus) return "Connecting"

  if (beltState === "running") return ramping ? "Ramping" : "Running"
  if (beltState === "starting") return "Starting"
  // Fact 8: belt == 4 is awake-and-idle, and is what the pad reports for most
  // of the time it is reachable at all.
  if (beltState === "idle") return "Ready"
  if (beltState === "stopped") return "Stopped"
  return "Unknown"
}

// The line that tells someone what to DO. Returned separately from the state
// label because these coexist: the pad reports belt=idle and mode=standby in
// the same frame, so an earlier version that returned one OR the other showed
// "In standby" for essentially the whole time the pad was reachable and hid
// every statistic behind it.
function instruction(linkState, mode, errorCode, errorMessage) {
  // A refusal the user just provoked outranks the ambient state.
  if (errorCode !== "" && errorCode !== "standby") return errorMessage

  // Fact 13: in standby the pad ignores every motion command AND the BLE
  // mode-switch opcode. Only the panel's M button leaves it, so name that
  // button rather than reporting a failure.
  if (mode === "standby") return "In standby — press M on the panel to control it."

  // Fact 3: BLE cannot wake the pad.
  if (linkState === "absent")
    return "Asleep. Wake it at the panel or the remote — Bluetooth cannot."
  // Fact 5: exactly one BLE connection at a time.
  if (linkState === "unavailable")
    return "Another device holds the pad's one Bluetooth connection."
  if (linkState === "lost" || linkState === "error") return errorMessage
  return ""
}

// A refusal is not a fault. Most of what the bridge says "no" to is expected
// and the user can act on it: the belt is not known to be moving, the link went
// quiet for a moment, the pad is in standby. Painting those in the urgent role
// says something is broken when nothing is -- reported from hardware
// 2026-08-25, where a stop refused on a stopped belt came back in alarm red.
//
// Reserve `urgent` for the cases where the plugin itself is not working, and
// give the rest the accent role: attention, not failure. No hue is chosen here;
// both come from the theme (spec §5.5).
var ADVISORY_CODES = [
  "stop_unsafe",
  "stale_link",
  "standby",
  "repeat_suppressed",
  "start_unconfirmed"
]

function isAdvisory(errorCode) {
  return ADVISORY_CODES.indexOf(errorCode) !== -1
}

// True when the pad is reachable but will ignore anything we send, so the panel
// can explain instead of offering controls that quietly do nothing.
function inert(linkState, mode) {
  return linkState !== "connected" || mode === "standby"
}

function elapsedText(totalSeconds) {
  var total = Math.max(0, Number(totalSeconds))
  var hours = Math.floor(total / 3600)
  var minutes = Math.floor((total % 3600) / 60)
  var seconds = Math.floor(total % 60)
  var mm = minutes < 10 ? "0" + minutes : String(minutes)
  var ss = seconds < 10 ? "0" + seconds : String(seconds)
  if (hours > 0) return hours + ":" + mm + ":" + ss
  return minutes + ":" + ss
}

function speedText(kmh) {
  return Number(kmh).toFixed(1)
}

// Fact 15: byte [14] is the last BLE-commanded speed, never a target, and reads
// 0.0 when the speed was set at the panel. It is diagnostic only, so what the
// panel shows as the target is what WE commanded, or an em dash when we have
// commanded nothing -- the bridge attaches to belts it did not start.
function targetText(target) {
  if (target === null || target === undefined) return "—"
  return Number(target).toFixed(1) + " km/h"
}

function tooltip(linkState, beltState, hasStatus, ramping, speed) {
  var label = stateLabel(linkState, beltState, hasStatus, ramping)
  if (linkState !== "connected" || !hasStatus) return "WalkingPad · " + label
  return "WalkingPad · " + label + " · " + speedText(speed) + " km/h"
}

// ----------------------------------------------------------------- charts
//
// Charts §1: the popup carries ONE chart, steps per minute on a real wall-clock
// axis -- 60 fixed one-minute buckets, newest at the right. Steps rather than
// distance because the pad quantises distance to 10 m, so a minute at 2.5 km/h
// advances only about four quanta and the bars would be visibly steppy; steps is
// an integer counter with real resolution.
//
// A minute with no walking produces NO bucket. That gap is the information: it
// says you were at the desk and not moving, which is why the axis has to be real
// time rather than sample index.
//
// Charts §6: this is the one copy of the bucket arithmetic in JavaScript, and it
// must agree with bin/walkingpad-stats at a boundary. Both state the same
// convention -- a bucket starts at floor(t / bucket) * bucket, aligned to the
// Unix epoch, which for a one-minute bucket is a wall-clock minute everywhere,
// because civil offsets are whole minutes.
var POPUP_BUCKET_S = 60
var POPUP_BUCKET_COUNT = 60

// The bridge polls once a second while the belt moves and every two seconds
// while it is idle, and the stale gate is 3.0 s. A wider separation between
// adjacent samples is a hole in the record rather than an interval anything
// observed, so no counter delta may be charged across it -- doing so would
// invent a placement for walking nobody saw. bin/walkingpad-stats mirrors this
// bound, and for the same reason.
var MAX_SAMPLE_INTERVAL_S = 3.0

function isFiniteNumber(value) {
  return typeof value === "number" && isFinite(value)
}

function bucketStart(timestamp, bucketSeconds) {
  return Math.floor(timestamp / bucketSeconds) * bucketSeconds
}

function usableSample(sample) {
  return sample !== null && sample !== undefined && isFiniteNumber(sample.t)
}

// The one implementation. `rangeEnd` is exclusive, matching a bucket covering
// [t, t + bucketSeconds), and a bucket outside the range is dropped rather than
// clamped into a neighbour it did not happen in.
function stepBucketsInRange(samples, rangeStart, rangeEnd, bucketSeconds) {
  var source = Array.isArray(samples) ? samples : []
  var totals = ({})
  var previous = null

  for (var i = 0; i < source.length; ++i) {
    var sample = source[i]
    if (!usableSample(sample)) continue

    if (sample.t >= rangeStart && sample.t < rangeEnd) {
      var key = bucketStart(sample.t, bucketSeconds)
      // A bucket exists as soon as a sample lands in it, even one carrying no
      // delta. That keeps a measured zero -- the belt was recorded here and did
      // not advance -- distinct from a minute nothing observed at all.
      if (totals[key] === undefined) totals[key] = 0
      totals[key] += stepDelta(previous, sample)
    }
    // Kept even for an out-of-range sample, so the first bucket on the axis
    // still gets the delta that crossed into it.
    previous = sample
  }

  var keys = Object.keys(totals)
  keys.sort(function(left, right) { return Number(left) - Number(right) })
  var buckets = []
  for (var k = 0; k < keys.length; ++k) {
    buckets.push({ t: Number(keys[k]), value: totals[keys[k]] })
  }
  return buckets
}

// Returns { buckets, rangeStart, rangeEnd, partialBucketStart }. The caller
// hands the whole ring buffer over; the hour bound is applied here so the axis
// stays the last hour even when the buffer holds older samples from a pause.
function stepBuckets(samples, nowSeconds) {
  var currentBucket = bucketStart(nowSeconds, POPUP_BUCKET_S)
  var rangeStart = currentBucket - (POPUP_BUCKET_COUNT - 1) * POPUP_BUCKET_S
  var rangeEnd = currentBucket + POPUP_BUCKET_S

  return {
    buckets: stepBucketsInRange(samples, rangeStart, rangeEnd, POPUP_BUCKET_S),
    rangeStart: rangeStart,
    rangeEnd: rangeEnd,
    // Charts §1: the in-progress minute renders distinctly, because a bucket
    // that is still filling would otherwise read as a slowdown.
    partialBucketStart: currentBucket
  }
}

function stepDelta(previous, sample) {
  if (!usableSample(previous)) return 0
  // Stage 3 §1.3 makes the epoch a maximal non-decreasing counter span, so a
  // delta across that boundary joins two unrelated coordinate systems.
  if (previous.e !== sample.e) return 0
  var elapsed = sample.t - previous.t
  if (!isFiniteNumber(elapsed) || !(elapsed > 0)
      || elapsed > MAX_SAMPLE_INTERVAL_S) return 0
  var steps = sample.s - previous.s
  // Inside one epoch the counter only rises, so a decrease is a malformed pair.
  if (!isFiniteNumber(steps) || steps < 0) return 0
  return steps
}

// Cadence is DERIVED, never reported: Δsteps / Δt × 60, smoothed over a trailing
// ten-second window. The window is required rather than cosmetic. At roughly
// 1.3 steps per second with an integer counter arriving near 1 Hz, the raw
// one-sample quotient jumps about 40 steps/minute from rounding alone, which
// would be an invented cadence.
//
// Returns one entry per input sample, `v` null where no honest value exists, so
// the caller's cursor index still addresses its own array.
var CADENCE_WINDOW_S = 10.0

function cadenceSeries(samples) {
  var source = Array.isArray(samples) ? samples : []
  var derived = []

  for (var i = 0; i < source.length; ++i) {
    var current = source[i]
    if (!usableSample(current) || !isFiniteNumber(current.s)) {
      derived.push(null)
      continue
    }

    var earliest = i
    for (var j = i - 1; j >= 0; --j) {
      var candidate = source[j]
      if (!usableSample(candidate) || !isFiniteNumber(candidate.s)) break
      var later = source[j + 1]
      // Stage 3 §1.3 makes an epoch change a counter reset and §2.3 makes a time
      // gap absent evidence, so neither boundary may enter a smoothing window.
      if (candidate.e !== later.e) break
      var step = later.t - candidate.t
      if (!isFiniteNumber(step) || !(step > 0)
          || step > MAX_SAMPLE_INTERVAL_S) break
      // Guard each adjacent counter delta as well as the final quotient. A
      // decrease without an epoch change would recreate exactly the reset spike
      // the epoch boundary exists to stop.
      var adjacent = later.s - candidate.s
      if (!isFiniteNumber(adjacent) || adjacent < 0) break
      if (current.t - candidate.t > CADENCE_WINDOW_S) break
      earliest = j
    }

    if (earliest === i) {
      derived.push({ t: current.t, v: null, e: current.e })
      continue
    }

    var elapsed = current.t - source[earliest].t
    var steps = current.s - source[earliest].s
    var cadence = (isFiniteNumber(elapsed) && elapsed > 0
      && isFiniteNumber(steps) && steps >= 0)
      ? steps / elapsed * 60.0 : null
    derived.push({
      t: current.t,
      v: isFiniteNumber(cadence) ? cadence : null,
      e: current.e
    })
  }
  return derived
}

// Pull one nullable field out of a sample series as a chart series. Commanded
// and goal speeds are nullable by design: a missing command is missing evidence,
// not a zero, and must break its overlay rather than dive through the floor.
function fieldSeries(samples, field) {
  var source = Array.isArray(samples) ? samples : []
  var result = []
  for (var i = 0; i < source.length; ++i) {
    var sample = source[i]
    if (!usableSample(sample)) {
      result.push(null)
      continue
    }
    var value = sample[field]
    result.push({
      t: sample.t,
      v: (value === null || value === undefined || !isFiniteNumber(value))
        ? null : value,
      e: sample.e
    })
  }
  return result
}
