import QtQuick

Item {
  id: root

  property var    samples: []
  property var    rows: ["speed", "cadence", "distance", "steps"]
  property real   rowHeight: 24
  property real   rowSpacing: 4
  property real   axisHeight: 14
  property color  fg
  property color  accent
  property color  muted
  property string fontFamily: "monospace"
  property real   labelFontSize: 9
  property int    cursorIndex: -1
  property bool   interactive: true
  signal cursorMoved(int index)
  property int    selectionStart: -1
  property int    selectionEnd: -1
  signal selectionChanged2(int from, int to)

  // These are geometry rather than palette choices. Keeping them caller-
  // replaceable avoids smuggling fixed pixel sizes into either host while the
  // frozen row, axis and font properties retain their exact meanings.
  property real lineWidth: 1
  property real dashLength: 4
  property real dashSpacing: 3

  QtObject {
    id: chartData

    // Unknown rows are omitted instead of being given an accidental rendering.
    // That makes a future store field fail as an empty row, not as misleading data.
    readonly property var activeRows: root.knownRows(root.rows)
    readonly property real plotHeight: chartData.activeRows.length * root.rowHeight
      + Math.max(0, chartData.activeRows.length - 1) * root.rowSpacing

    // Canvas does not observe JavaScript data dependencies. These caches are
    // replaced as whole values so labels update normally and every paint sees one
    // coherent snapshot of the derived data without becoming public API.
    property var sampleXs: []
    property var positionedSamples: []
    property var timeBreaks: []
    property var missingGapBreaks: []
    property var epochBreaks: []
    property var cadenceValues: []
    property var rowRanges: ({})
  }

  implicitWidth: 240
  implicitHeight: chartData.plotHeight + root.axisHeight
  clip: true

  function knownRows(requested) {
    var result = []
    if (!Array.isArray(requested)) return result
    for (var i = 0; i < requested.length; ++i) {
      var name = requested[i]
      if (name === "speed" || name === "cadence"
          || name === "distance" || name === "steps") {
        result.push(name)
      }
    }
    return result
  }

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value)
  }

  function usableSample(sample) {
    return sample !== null && sample !== undefined
      && root.finiteNumber(sample.t)
  }

  function alphaColor(source, alpha) {
    // The hue always comes from the caller. Only opacity belongs here, because
    // the same series must remain recognisable in both independent palettes.
    return Qt.rgba(source.r, source.g, source.b, source.a * alpha)
  }

  function rebuildXCache() {
    var source = Array.isArray(root.samples) ? root.samples : []
    var minimum = Infinity
    var maximum = -Infinity

    for (var i = 0; i < source.length; ++i) {
      if (!root.usableSample(source[i])) continue
      minimum = Math.min(minimum, source[i].t)
      maximum = Math.max(maximum, source[i].t)
    }

    var xs = []
    var positioned = []
    var span = maximum - minimum
    var plotWidth = Math.max(0, root.width)

    for (var j = 0; j < source.length; ++j) {
      if (!root.usableSample(source[j])) {
        // Preserve the array index as a real gap. A cursor can never resolve to
        // this slot, and line painters see the same break the store recorded.
        xs.push(null)
        continue
      }

      // The SAME cached mapping is consumed by the painters and hit-testing.
      // Recomputing it in each mouse event would both waste work and let the
      // linked crosshair drift a pixel away from the series it claims to inspect.
      var x = span > 0 ? (source[j].t - minimum) / span * plotWidth : 0
      if (!root.finiteNumber(x)) x = 0
      xs.push(x)
      positioned.push({ index: j, x: x })
    }

    chartData.sampleXs = xs
    chartData.positionedSamples = positioned
  }

  function rebuildContinuityCache() {
    var source = Array.isArray(root.samples) ? root.samples : []
    var spacings = []

    for (var i = 1; i < source.length; ++i) {
      var previous = source[i - 1]
      var sample = source[i]
      if (!root.usableSample(previous) || !root.usableSample(sample)) continue
      var spacing = sample.t - previous.t
      if (root.finiteNumber(spacing) && spacing > 0) spacings.push(spacing)
    }

    spacings.sort(function(left, right) { return left - right })
    // Stage 3 §2.3 says an unobserved bout has exact totals but NO samples, so
    // array adjacency cannot prove continuity. Three times the lower median of
    // positive adjacent spacings accepts the measured 1 s walking / 2 s tail
    // cadence plus jitter; the lower middle keeps one outage from raising its
    // own threshold in a small even data set. No poll-rate constant is assumed.
    var typicalSpacing = spacings.length > 0
      ? spacings[Math.floor((spacings.length - 1) / 2)] : null
    var gapThreshold = typicalSpacing === null ? null : typicalSpacing * 3
    var timeBreaks = []
    var missingGapBreaks = []
    var epochBreaks = []

    for (var j = 0; j < source.length; ++j) {
      timeBreaks.push(false)
      missingGapBreaks.push(false)
      epochBreaks.push(false)
      if (j === 0) continue

      var before = source[j - 1]
      var current = source[j]
      if (!root.usableSample(before) || !root.usableSample(current)) continue

      var elapsed = current.t - before.t
      var finiteElapsed = root.finiteNumber(elapsed)
      var missingGap = finiteElapsed && gapThreshold !== null
        && elapsed > gapThreshold
      // A reversed or duplicate timestamp cannot support a line segment either.
      // Treat it as a silent break so degenerate input stays finite and honest;
      // only a positive measured absence receives the visible gap treatment.
      timeBreaks[j] = !finiteElapsed || !(elapsed > 0) || missingGap
      missingGapBreaks[j] = missingGap
      // Stage 3 §1.3 defines `e` as the maximal non-decreasing counter span.
      // Crossing it would join two unrelated cumulative counter coordinate systems.
      epochBreaks[j] = before.e !== current.e
    }

    chartData.timeBreaks = timeBreaks
    chartData.missingGapBreaks = missingGapBreaks
    chartData.epochBreaks = epochBreaks
  }

  function timeBreakBefore(index) {
    return index > 0 && index < chartData.timeBreaks.length
      && chartData.timeBreaks[index] === true
  }

  function missingGapBefore(index) {
    return index > 0 && index < chartData.missingGapBreaks.length
      && chartData.missingGapBreaks[index] === true
  }

  function epochBreakBefore(index) {
    return index > 0 && index < chartData.epochBreaks.length
      && chartData.epochBreaks[index] === true
  }

  function deriveCadence() {
    var source = Array.isArray(root.samples) ? root.samples : []
    var derived = []

    for (var i = 0; i < source.length; ++i) {
      derived.push(null)
      var current = source[i]
      if (!root.usableSample(current) || !root.finiteNumber(current.s)) continue

      var earliest = i
      // At roughly 1.3 steps/s, integer steps arriving near 1 Hz make the raw
      // one-sample quotient jump by about 40 steps/minute from rounding alone.
      // A trailing 10 s window removes that invented cadence. Stage 3 §1.3-1.4
      // makes an epoch change a counter reset, while §2.3 makes a time gap absent
      // evidence, so neither boundary may enter the same smoothing window.
      for (var j = i - 1; j >= 0; --j) {
        var candidate = source[j]
        if (!root.usableSample(candidate) || !root.finiteNumber(candidate.s)) break
        if (root.timeBreakBefore(j + 1) || root.epochBreakBefore(j + 1)) break

        var elapsedToCandidate = current.t - candidate.t
        if (!root.finiteNumber(elapsedToCandidate)
            || !(elapsedToCandidate > 0) || elapsedToCandidate > 10.0) break

        // Guard every adjacent counter delta as well as the final quotient. If
        // malformed input decreases steps without changing `e`, smoothing across
        // it would recreate the reset spike the epoch boundary is meant to stop.
        var later = source[j + 1]
        var adjacentSteps = later.s - candidate.s
        if (!root.finiteNumber(adjacentSteps) || adjacentSteps < 0) break
        earliest = j
      }

      if (earliest === i) continue
      var elapsed = current.t - source[earliest].t
      var stepDelta = current.s - source[earliest].s
      if (!root.finiteNumber(elapsed) || !(elapsed > 0)
          || !root.finiteNumber(stepDelta) || stepDelta < 0) continue
      var cadence = stepDelta / elapsed * 60.0
      if (root.finiteNumber(cadence)) derived[i] = cadence
    }

    chartData.cadenceValues = derived
  }

  function paddedRange(values, minimumSpan) {
    if (values.length === 0) return null
    var minimum = values[0]
    var maximum = values[0]
    for (var i = 1; i < values.length; ++i) {
      minimum = Math.min(minimum, values[i])
      maximum = Math.max(maximum, values[i])
    }

    var center = (minimum + maximum) / 2
    var span = Math.max(maximum - minimum, minimumSpan)
    // Ten percent breathing room keeps a changing line off both edges. The
    // minimum span is the zero-range guard for a flat or one-sample series.
    span *= 1.2
    return { minimum: center - span / 2, maximum: center + span / 2 }
  }

  function cumulativeRange(values, minimumSpan) {
    if (values.length === 0) return null
    var minimum = values[0]
    var maximum = values[0]
    for (var i = 1; i < values.length; ++i) {
      minimum = Math.min(minimum, values[i])
      maximum = Math.max(maximum, values[i])
    }
    if (maximum > minimum) return { minimum: minimum, maximum: maximum }

    // Cumulative counters can remain unchanged for a whole view. Give that
    // single value a finite span rather than manufacturing a division by zero.
    if (minimum === 0) return { minimum: 0, maximum: minimumSpan }
    return {
      minimum: minimum - minimumSpan / 2,
      maximum: maximum + minimumSpan / 2
    }
  }

  function rebuildRanges() {
    var source = Array.isArray(root.samples) ? root.samples : []
    var speed = []
    var cadence = []
    var distance = []
    var steps = []

    for (var i = 0; i < source.length; ++i) {
      var sample = source[i]
      if (!root.usableSample(sample)) continue

      if (root.finiteNumber(sample.v)) speed.push(sample.v)
      // Null commanded and goal speeds are missing evidence, not zero. They do
      // not enter the range for the same reason they break their overlay lines.
      if (sample.c !== null && root.finiteNumber(sample.c)) speed.push(sample.c)
      if (sample.g !== null && root.finiteNumber(sample.g)) speed.push(sample.g)
      if (root.finiteNumber(chartData.cadenceValues[i])) cadence.push(chartData.cadenceValues[i])
      if (root.finiteNumber(sample.d)) distance.push(sample.d)
      if (root.finiteNumber(sample.s)) steps.push(sample.s)
    }

    chartData.rowRanges = {
      speed: root.paddedRange(speed, 0.5),
      cadence: root.paddedRange(cadence, 10),
      distance: root.cumulativeRange(distance, 0.02),
      steps: root.cumulativeRange(steps, 2)
    }
  }

  function rebuildDataCaches() {
    root.rebuildXCache()
    root.rebuildContinuityCache()
    root.deriveCadence()
    root.rebuildRanges()
  }

  function rangeFor(kind) {
    return chartData.rowRanges && chartData.rowRanges[kind]
      ? chartData.rowRanges[kind] : null
  }

  function xForIndex(index) {
    if (index < 0 || index >= chartData.sampleXs.length) return null
    var x = chartData.sampleXs[index]
    return root.finiteNumber(x) ? x : null
  }

  function nearestIndex(mouseX) {
    // The O(n) scan is over cached positions only. No timestamp range or x
    // geometry is rebuilt at mouse-move frequency, and null slots are absent.
    var nearest = -1
    var nearestDistance = Infinity
    for (var i = 0; i < chartData.positionedSamples.length; ++i) {
      var positioned = chartData.positionedSamples[i]
      var distance = Math.abs(positioned.x - mouseX)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = positioned.index
      }
    }
    return nearest
  }

  function yForValue(value, range, height) {
    if (!range || !root.finiteNumber(value)) return null
    var span = range.maximum - range.minimum
    if (!(span > 0) || !root.finiteNumber(span)) return height / 2
    var y = height - (value - range.minimum) / span * height
    return root.finiteNumber(y) ? y : height / 2
  }

  function formatValue(kind, value) {
    if (!root.finiteNumber(value)) return "—"
    if (kind === "speed") return value.toFixed(1)
    if (kind === "distance") return value.toFixed(2)
    return Math.round(value).toString()
  }

  function axisLabel(kind, maximum) {
    var range = root.rangeFor(kind)
    if (!range) return "—"
    return root.formatValue(kind, maximum ? range.maximum : range.minimum)
  }

  function elapsedLabel() {
    var source = Array.isArray(root.samples) ? root.samples : []
    if (source.length === 0) return "0:00"
    var first = source[0]
    var last = source[source.length - 1]
    if (!root.usableSample(first) || !root.usableSample(last)) return "0:00"

    var elapsed = Math.max(0, Math.floor(last.t - first.t))
    if (!root.finiteNumber(elapsed)) elapsed = 0
    var seconds = elapsed % 60
    var minutes = Math.floor(elapsed / 60) % 60
    var hours = Math.floor(elapsed / 3600)
    var secondsText = seconds < 10 ? "0" + seconds : seconds.toString()
    if (hours > 0) {
      var minutesText = minutes < 10 ? "0" + minutes : minutes.toString()
      return hours + ":" + minutesText + ":" + secondsText
    }
    return Math.floor(elapsed / 60) + ":" + secondsText
  }

  function beginSeries(ctx, colour, dashed) {
    ctx.lineWidth = Math.max(0, root.lineWidth)
    ctx.strokeStyle = colour
    ctx.setLineDash(dashed
      ? [Math.max(0, root.dashLength), Math.max(0, root.dashSpacing)] : [])
  }

  function paintValueLine(ctx, kind, range, height, stepAfter) {
    var source = Array.isArray(root.samples) ? root.samples : []
    root.beginSeries(ctx, root.alphaColor(root.fg, 0.95), false)
    ctx.beginPath()
    var active = false
    var previousY = 0

    for (var i = 0; i < source.length; ++i) {
      var sample = source[i]
      var x = root.xForIndex(i)
      var value = null
      if (root.usableSample(sample)) {
        if (kind === "cadence") value = chartData.cadenceValues[i]
        else if (kind === "distance") value = sample.d
        else if (kind === "steps") value = sample.s
      }

      // Stage 3 §2.3/§10 forbids joining across missing samples. Cadence and
      // cumulative rows also break at §1.3's counter epoch boundary; speed is
      // independent of the reset and therefore needs only the time break.
      if (root.timeBreakBefore(i)
          || ((kind === "cadence" || kind === "distance" || kind === "steps")
              && root.epochBreakBefore(i))) active = false

      if (x === null || !root.finiteNumber(value)) {
        // A missing derived value or stored hole ends the subpath. Joining over
        // it would assert that an unmeasured interval had a measured value.
        active = false
        continue
      }

      var y = root.yForValue(value, range, height)
      if (!active) {
        ctx.moveTo(x, y)
        active = true
      } else if (stepAfter) {
        ctx.lineTo(x, previousY)
        ctx.lineTo(x, y)
      } else {
        ctx.lineTo(x, y)
      }
      previousY = y
    }
    ctx.stroke()
  }

  function paintSpeedFill(ctx, range, height) {
    var source = Array.isArray(root.samples) ? root.samples : []
    ctx.fillStyle = root.alphaColor(root.fg, 0.18)
    var active = false
    var lastX = 0

    for (var i = 0; i <= source.length; ++i) {
      var sample = i < source.length ? source[i] : null
      var x = i < source.length ? root.xForIndex(i) : null
      var valid = root.usableSample(sample) && x !== null
        && root.finiteNumber(sample.v)

      if ((!valid || root.timeBreakBefore(i)) && active) {
        // Close the old polygon before processing the first post-gap sample;
        // otherwise the fill itself still fabricates a roof across the absence.
        ctx.lineTo(lastX, height)
        ctx.closePath()
        ctx.fill()
        active = false
      }

      if (!valid) {
        continue
      }

      var y = root.yForValue(sample.v, range, height)
      if (!active) {
        ctx.beginPath()
        ctx.moveTo(x, height)
        ctx.lineTo(x, y)
        active = true
      } else {
        ctx.lineTo(x, y)
      }
      lastX = x
    }
  }

  function paintSpeedLine(ctx, field, range, height, colour, dashed) {
    var source = Array.isArray(root.samples) ? root.samples : []
    root.beginSeries(ctx, colour, dashed)
    ctx.beginPath()
    var active = false

    for (var i = 0; i < source.length; ++i) {
      var sample = source[i]
      var x = root.xForIndex(i)
      var value = root.usableSample(sample) ? sample[field] : null

      if (root.timeBreakBefore(i)) active = false

      if (x === null || value === null || !root.finiteNumber(value)) {
        // Commanded and goal speeds are nullable. Ending the path here prevents
        // a missing command from becoming a fabricated segment through zero.
        active = false
        continue
      }

      var y = root.yForValue(value, range, height)
      if (!active) {
        ctx.moveTo(x, y)
        active = true
      } else {
        ctx.lineTo(x, y)
      }
    }
    ctx.stroke()
  }

  function paintRow(ctx, kind, width, height) {
    ctx.clearRect(0, 0, width, height)
    var range = root.rangeFor(kind)
    if (!range || !(width > 0) || !(height > 0)) return

    if (kind === "speed") {
      root.paintSpeedFill(ctx, range, height)
      root.paintSpeedLine(ctx, "v", range, height,
                          root.alphaColor(root.fg, 0.95), false)
      // Actual, commanded and goal share km/h. Their gap is the measured
      // ~0.25 km/h/s ramp and, because the pad has no ACK, the only visual
      // evidence that a command is being obeyed. Separate rows would hide it.
      root.paintSpeedLine(ctx, "c", range, height,
                          root.alphaColor(root.accent, 0.95), false)
      root.paintSpeedLine(ctx, "g", range, height,
                          root.alphaColor(root.muted, 0.95), true)
    } else if (kind === "cadence") {
      root.paintValueLine(ctx, kind, range, height, false)
    } else {
      // Distance advances in measured 10 m quanta -- only about every 14 s at
      // 2.5 km/h -- and steps are another coarse cumulative counter. Step-after
      // holds evidence until the next reading; a diagonal would invent
      // continuous sub-quantum motion neither counter measured.
      root.paintValueLine(ctx, kind, range, height, true)
    }
    ctx.setLineDash([])
  }

  function backgroundBand(sample) {
    if (!root.usableSample(sample)) return -1
    // Stage 3 §4.1 requires mode and belt state to share one background band.
    // Standby takes precedence because fact 13 gives it distinct user meaning:
    // every motion command is ignored until the panel's M button leaves it.
    if (sample.m === "standby") return 3
    // Fact 8: only an explicit `running` means the belt moves. An unmapped or
    // future state must fail safe into the not-moving band, never disappear as
    // if it were running; `starting` is separately the measured 3-2-1 countdown.
    if (sample.b === "running") return 0
    if (sample.b === "starting") return 1
    return 2
  }

  function fillBand(ctx, classification, minimum, maximum, width, height) {
    if (classification < 1 || !root.finiteNumber(minimum)
        || !root.finiteNumber(maximum)) return
    var left = Math.min(minimum, maximum)
    var right = Math.max(minimum, maximum)
    if (right === left) {
      left = Math.max(0, left - root.lineWidth / 2)
      right = Math.min(width, right + root.lineWidth / 2)
    }
    // Standby uses a stronger caller-supplied foreground layer so it remains
    // visibly distinct from ordinary idle without this component owning a hue.
    ctx.fillStyle = classification === 1
      ? root.alphaColor(root.accent, 0.12)
      : (classification === 3
          ? root.alphaColor(root.fg, 0.14)
          : root.alphaColor(root.muted, 0.08))
    ctx.fillRect(left, 0, Math.max(0, right - left), height)
  }

  function paintBackground(ctx, width, height) {
    ctx.clearRect(0, 0, width, height)
    if (!(width > 0) || !(height > 0)) return

    // Even an empty store leaves a minimal frame. The caller's muted role is
    // the only colour involved, so both hosts retain ownership of the surface.
    ctx.fillStyle = root.alphaColor(root.muted, 0.03)
    for (var row = 0; row < chartData.activeRows.length; ++row) {
      ctx.fillRect(0, row * (root.rowHeight + root.rowSpacing),
                   width, Math.max(0, root.rowHeight))
    }

    var source = Array.isArray(root.samples) ? root.samples : []
    var currentBand = -1
    var runMinimum = Infinity
    var runMaximum = -Infinity

    for (var i = 0; i <= source.length; ++i) {
      var sample = i < source.length ? source[i] : null
      var x = i < source.length ? root.xForIndex(i) : null
      var classification = x === null ? -1 : root.backgroundBand(sample)

      if (root.timeBreakBefore(i)) {
        // A state band is still a claim about the missing interval. End it at
        // the same evidence boundary as every plotted row before starting anew.
        root.fillBand(ctx, currentBand, runMinimum, runMaximum, width, height)
        currentBand = -1
        runMinimum = Infinity
        runMaximum = -Infinity
      }

      if (classification < 1) {
        root.fillBand(ctx, currentBand, runMinimum, runMaximum, width, height)
        currentBand = -1
        runMinimum = Infinity
        runMaximum = -Infinity
        continue
      }

      if (classification !== currentBand) {
        root.fillBand(ctx, currentBand, runMinimum, runMaximum, width, height)
        currentBand = classification
        runMinimum = x
        runMaximum = x
      } else {
        runMinimum = Math.min(runMinimum, x)
        runMaximum = Math.max(runMaximum, x)
      }
    }

    // An absent segment must not be visually interchangeable with a real zero.
    // Stage 3 §2.3 says no curve is drawable there, and §10 says inventing one is
    // worse than a gap, so mark the actual time span with the caller's muted role.
    ctx.fillStyle = root.alphaColor(root.muted, 0.12)
    for (var gap = 1; gap < source.length; ++gap) {
      if (!root.missingGapBefore(gap)) continue
      var beforeX = root.xForIndex(gap - 1)
      var afterX = root.xForIndex(gap)
      if (beforeX === null || afterX === null) continue
      var gapLeft = Math.min(beforeX, afterX)
      var gapRight = Math.max(beforeX, afterX)
      var gapWidth = Math.min(width - gapLeft,
                              Math.max(root.lineWidth, gapRight - gapLeft))
      ctx.fillRect(gapLeft, 0, Math.max(0, gapWidth), height)
    }
  }

  function setSelection(from, to) {
    if (root.selectionStart === from && root.selectionEnd === to) return
    root.selectionStart = from
    root.selectionEnd = to
    root.selectionChanged2(from, to)
  }

  onSamplesChanged: root.rebuildDataCaches()
  onWidthChanged: root.rebuildXCache()
  Component.onCompleted: root.rebuildDataCaches()

  Item {
    id: plotArea
    width: root.width
    height: chartData.plotHeight

    // Layer 1 paints only stable data and geometry. Cursor and selection state
    // are intentionally absent from its repaint triggers.
    Canvas {
      id: bandCanvas
      anchors.fill: parent
      z: 0
      antialiasing: true

      onPaint: {
        var ctx = bandCanvas.getContext("2d")
        root.paintBackground(ctx, bandCanvas.width, bandCanvas.height)
      }

      Connections {
        target: root
        function onSamplesChanged() { bandCanvas.requestPaint() }
        function onRowsChanged() { bandCanvas.requestPaint() }
        function onRowHeightChanged() { bandCanvas.requestPaint() }
        function onRowSpacingChanged() { bandCanvas.requestPaint() }
        function onWidthChanged() { bandCanvas.requestPaint() }
        function onHeightChanged() { bandCanvas.requestPaint() }
        function onFgChanged() { bandCanvas.requestPaint() }
        function onAccentChanged() { bandCanvas.requestPaint() }
        function onMutedChanged() { bandCanvas.requestPaint() }
        function onLineWidthChanged() { bandCanvas.requestPaint() }
      }
    }

    // Layer 2 has one transparent Canvas per requested, recognised row. None
    // listens to cursor or selection changes, so a hover cannot redraw data.
    Item {
      id: rowLayer
      anchors.fill: parent
      z: 1

      Repeater {
        id: rowRepeater
        model: chartData.activeRows

        delegate: Item {
          id: rowItem
          required property int index
          required property string modelData
          readonly property string kind: rowItem.modelData

          x: 0
          y: rowItem.index * (root.rowHeight + root.rowSpacing)
          width: rowLayer.width
          height: root.rowHeight
          clip: true

          Canvas {
            id: rowCanvas
            anchors.fill: parent
            antialiasing: true

            onPaint: {
              var ctx = rowCanvas.getContext("2d")
              root.paintRow(ctx, rowItem.kind, rowCanvas.width, rowCanvas.height)
            }

            Connections {
              target: root
              function onSamplesChanged() { rowCanvas.requestPaint() }
              function onRowsChanged() { rowCanvas.requestPaint() }
              function onRowHeightChanged() { rowCanvas.requestPaint() }
              function onRowSpacingChanged() { rowCanvas.requestPaint() }
              function onWidthChanged() { rowCanvas.requestPaint() }
              function onHeightChanged() { rowCanvas.requestPaint() }
              function onFgChanged() { rowCanvas.requestPaint() }
              function onAccentChanged() { rowCanvas.requestPaint() }
              function onMutedChanged() { rowCanvas.requestPaint() }
              function onLineWidthChanged() { rowCanvas.requestPaint() }
              function onDashLengthChanged() { rowCanvas.requestPaint() }
              function onDashSpacingChanged() { rowCanvas.requestPaint() }
            }
          }

          Text {
            anchors.top: parent.top
            anchors.right: parent.right
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: root.labelFontSize
            text: root.axisLabel(rowItem.kind, true)
          }

          Text {
            anchors.bottom: parent.bottom
            anchors.right: parent.right
            color: root.muted
            font.family: root.fontFamily
            font.pixelSize: root.labelFontSize
            text: root.axisLabel(rowItem.kind, false)
          }
        }
      }
    }

    // Layer 3 is deliberately the only canvas invalidated by cursor movement.
    // A high-frequency hover repaints one thin overlay, never four data rows
    // that are already being replaced by the live sample stream once a second.
    Canvas {
      id: overlayCanvas
      anchors.fill: parent
      z: 2
      antialiasing: true

      onPaint: {
        var ctx = overlayCanvas.getContext("2d")
        ctx.clearRect(0, 0, overlayCanvas.width, overlayCanvas.height)

        var startX = root.xForIndex(root.selectionStart)
        var endX = root.xForIndex(root.selectionEnd)
        if (startX !== null && endX !== null) {
          var left = Math.min(startX, endX)
          var right = Math.max(startX, endX)
          ctx.fillStyle = root.alphaColor(root.accent, 0.14)
          ctx.fillRect(left, 0, Math.max(0, right - left), overlayCanvas.height)
        }

        var cursorX = root.xForIndex(root.cursorIndex)
        if (cursorX !== null) {
          root.beginSeries(ctx, root.alphaColor(root.fg, 0.9), false)
          ctx.beginPath()
          ctx.moveTo(cursorX, 0)
          ctx.lineTo(cursorX, overlayCanvas.height)
          ctx.stroke()
        }
      }

      Connections {
        target: root
        function onSamplesChanged() { overlayCanvas.requestPaint() }
        function onRowsChanged() { overlayCanvas.requestPaint() }
        function onRowHeightChanged() { overlayCanvas.requestPaint() }
        function onRowSpacingChanged() { overlayCanvas.requestPaint() }
        function onWidthChanged() { overlayCanvas.requestPaint() }
        function onHeightChanged() { overlayCanvas.requestPaint() }
        function onFgChanged() { overlayCanvas.requestPaint() }
        function onAccentChanged() { overlayCanvas.requestPaint() }
        function onLineWidthChanged() { overlayCanvas.requestPaint() }
        function onCursorIndexChanged() { overlayCanvas.requestPaint() }
        function onSelectionStartChanged() { overlayCanvas.requestPaint() }
        function onSelectionEndChanged() { overlayCanvas.requestPaint() }
      }
    }
  }

  Item {
    id: axisStrip
    x: 0
    y: chartData.plotHeight
    width: root.width
    height: root.axisHeight
    clip: true

    Text {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
      text: "0:00"
    }

    Text {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
      text: root.elapsedLabel()
    }
  }

  // Exactly one pointer authority spans every row and the shared axis. It emits
  // a linked index but never assigns cursorIndex, because keyboard navigation
  // and other callers own that property and may keep driving it without a mouse.
  MouseArea {
    id: pointerArea
    anchors.fill: parent
    z: 3
    enabled: root.interactive
    hoverEnabled: true
    acceptedButtons: Qt.LeftButton
    property int dragAnchor: -1

    onPositionChanged: function(mouse) {
      var current = root.nearestIndex(mouse.x)
      root.cursorMoved(current)
      if (!pointerArea.pressed || pointerArea.dragAnchor < 0 || current < 0) return
      root.setSelection(Math.min(pointerArea.dragAnchor, current),
                        Math.max(pointerArea.dragAnchor, current))
    }

    onPressed: function(mouse) {
      pointerArea.dragAnchor = root.nearestIndex(mouse.x)
      root.cursorMoved(pointerArea.dragAnchor)
    }

    onReleased: function(mouse) {
      var current = root.nearestIndex(mouse.x)
      root.cursorMoved(current)
      if (pointerArea.dragAnchor < 0 || current === pointerArea.dragAnchor) {
        // A click names no interval. Clearing here also removes a previous range
        // without needing a modifier or a second pointer state machine.
        root.setSelection(-1, -1)
      } else if (current >= 0) {
        root.setSelection(Math.min(pointerArea.dragAnchor, current),
                          Math.max(pointerArea.dragAnchor, current))
      }
      pointerArea.dragAnchor = -1
    }

    onCanceled: pointerArea.dragAnchor = -1
  }
}
