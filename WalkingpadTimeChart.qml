import QtQuick
import QtQuick.Shapes

// A continuous series on a real time axis, drawn with QtQuick.Shapes.
//
// Charts §3.2: Shapes is part of Qt 6 and the shell already imports it
// (Ui/SpeedTestOverlay.qml, Ui/BorderOverlay.qml, plugins/background), so this
// adds no runtime dependency. What it buys over the Canvas it replaces is
// antialiased vector paths with real joins and caps, and a gradient fill.
//
// Charts §3.5 keeps the same rules load-bearing here as in WalkingpadBarChart:
// no colour and no palette import, sizes from the caller, and ONE MouseArea
// spanning the whole chart. Per-row mouse areas produce N cursors that then have
// to be kept in sync; that bug is avoided by construction, not by discipline.
// This component does not own its cursor index either -- it renders what it is
// given and emits cursorMoved.
Item {
  id: root

  // ---------------------------------------------------------------- data
  //
  // Each point is { t: epoch seconds, v: number or null, e: epoch id }. A null
  // `v` is missing evidence, not zero, and breaks the line where it sits.
  // Indices are preserved exactly as given, so cursorIndex addresses the
  // caller's own array.
  property var points: []

  // [{ points: [...], role: "fg" | "accent" | "muted", dashed: bool }]. Roles
  // rather than colours: a colour baked into a model array would not follow a
  // live Omarchy theme swap, and §3.5 forbids this component owning one anyway.
  property var overlays: []

  property bool filled: true
  // Pin the value axis at zero. A filled area whose baseline is a floating
  // minimum implies the value fell to that minimum; for speed it should not.
  property bool zeroBased: false
  // The zero-range guard, in the series' own units, for a flat or one-point set.
  property real minimumSpan: 0.5

  // How far apart two points may be and still be joined. Left non-finite, it is
  // derived from the data's own cadence, which is right for a per-second sample
  // trace. A caller plotting fixed-width buckets must state it instead: with
  // evenly spaced buckets the derived median cannot tell a missing one from the
  // normal spacing, and the gap would silently be drawn through.
  property real gapSeconds: NaN

  // Axis extent as epoch seconds; leave either non-finite to derive it from the
  // data. Charts §3.4: with the period's own extent here, a six-minute session
  // inside a day view occupies six minutes of width.
  property real rangeStart: NaN
  property real rangeEnd: NaN

  // ------------------------------------------------------------- palette
  property color fg
  property color accent
  property color muted

  // ------------------------------------------------------------ geometry
  property real plotHeight: 90
  property real axisHeight: 14
  property real lineWidth: 1
  property real dashLength: 4
  property real dashSpacing: 3
  property string fontFamily: "monospace"
  property real labelFontSize: 9
  // Charts §3.3: a baseline, and at most two faint horizontal guides.
  property int guideCount: 0

  // --------------------------------------------------------------- labels
  property string axisStartLabel: ""
  property string axisEndLabel: ""
  property string minimumLabel: ""
  property string maximumLabel: ""

  // The value span this chart actually drew, as { minimum, maximum }, or null
  // when nothing was drawable. Published so a host can label the axis with the
  // numbers on screen instead of a figure it happens to have nearby -- the top
  // of a padded, zero-based axis is not the series average, and saying it is
  // misreads the whole plot. Formatting stays with the host, which owns the unit.
  readonly property var valueRange: chart.range

  // --------------------------------------------------------------- cursor
  property int cursorIndex: -1
  property bool interactive: true
  signal cursorMoved(int index)

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value)
  }

  function alphaColor(source, alpha) {
    return Qt.rgba(source.r, source.g, source.b, source.a * alpha)
  }

  function roleColor(role) {
    if (role === "accent") return root.accent
    if (role === "muted") return root.muted
    return root.fg
  }

  // Degenerate input renders an empty frame and never throws (charts §3.5).
  // Nulls are KEPT so array indices survive: the caller's cursorIndex addresses
  // its own array, and silently compacting here would misalign every readout.
  function series(source) {
    var result = []
    if (!Array.isArray(source)) return result
    for (var i = 0; i < source.length; ++i) {
      var point = source[i]
      if (point === null || point === undefined || !root.finiteNumber(point.t)) {
        result.push(null)
        continue
      }
      result.push({
        t: point.t,
        v: root.finiteNumber(point.v) ? point.v : null,
        e: root.finiteNumber(point.e) ? point.e : 0
      })
    }
    return result
  }

  // Three times the lower median of positive adjacent spacings. That accepts the
  // measured 1 s walking / 2 s tail cadence plus jitter without assuming a poll
  // rate, and the lower middle keeps one outage from raising its own threshold
  // in a small even data set.
  function gapThreshold(points) {
    var spacings = []
    for (var i = 1; i < points.length; ++i) {
      var before = points[i - 1]
      var current = points[i]
      if (before === null || current === null) continue
      var spacing = current.t - before.t
      if (root.finiteNumber(spacing) && spacing > 0) spacings.push(spacing)
    }
    if (spacings.length === 0) return null
    spacings.sort(function(left, right) { return left - right })
    return spacings[Math.floor((spacings.length - 1) / 2)] * 3
  }

  // Charts §3.5: lines break across a gap larger than the sampling cadence and
  // across an epoch change. A line drawn over an unobserved interval is a line
  // that did not happen, and Stage 3 §10's rule stands -- inventing numbers is
  // worse than showing a gap.
  function segmentsOf(points, threshold) {
    var segments = []
    var current = []
    for (var i = 0; i < points.length; ++i) {
      var point = points[i]
      if (i > 0 && current.length > 0) {
        var before = points[i - 1]
        var broken = before === null || point === null
        if (!broken) {
          var elapsed = point.t - before.t
          // A reversed or duplicate timestamp cannot support a segment either.
          broken = !root.finiteNumber(elapsed) || !(elapsed > 0)
            || (threshold !== null && elapsed > threshold)
            || before.e !== point.e
        }
        if (broken) {
          segments.push(current)
          current = []
        }
      }
      if (point === null || point.v === null) {
        if (current.length > 0) {
          segments.push(current)
          current = []
        }
        continue
      }
      current.push(i)
    }
    if (current.length > 0) segments.push(current)
    return segments
  }

  function collectValues(points, into) {
    for (var i = 0; i < points.length; ++i) {
      if (points[i] !== null && points[i].v !== null) into.push(points[i].v)
    }
    return into
  }

  function computeValueRange(values) {
    if (values.length === 0) return null
    var minimum = values[0]
    var maximum = values[0]
    for (var i = 1; i < values.length; ++i) {
      minimum = Math.min(minimum, values[i])
      maximum = Math.max(maximum, values[i])
    }

    if (root.zeroBased) {
      // Ten percent headroom keeps the peak off the top edge.
      return { minimum: 0, maximum: Math.max(maximum, root.minimumSpan) * 1.1 }
    }
    var centre = (minimum + maximum) / 2
    // The minimum span is the zero-range guard for a flat or one-point series.
    var span = Math.max(maximum - minimum, root.minimumSpan) * 1.2
    return { minimum: centre - span / 2, maximum: centre + span / 2 }
  }

  function xForTime(time) {
    var x = (time - chart.spanStart) / chart.spanSeconds * chart.plotWidth
    return root.finiteNumber(x) ? x : 0
  }

  function yForValue(value, range) {
    if (range === null) return chart.plotHeightPx / 2
    var span = range.maximum - range.minimum
    if (!(span > 0) || !root.finiteNumber(span)) return chart.plotHeightPx / 2
    var y = chart.plotHeightPx
      - (value - range.minimum) / span * chart.plotHeightPx
    return root.finiteNumber(y) ? y : chart.plotHeightPx / 2
  }

  function strokePaths(points, segments, range) {
    var paths = []
    for (var s = 0; s < segments.length; ++s) {
      var indices = segments[s]
      // A one-point segment has no line. Charts §3.5 asks for an empty frame
      // rather than an invented neighbour to join it to.
      if (indices.length < 2) continue
      var path = []
      for (var i = 0; i < indices.length; ++i) {
        var point = points[indices[i]]
        // An index with no point cannot be drawn. Charts §3.5 says degenerate
        // input renders an empty frame and never throws, so that is enforced
        // here rather than assumed of every caller.
        if (point === null || point === undefined) continue
        path.push(Qt.point(root.xForTime(point.t), root.yForValue(point.v, range)))
      }
      if (path.length >= 2) paths.push(path)
    }
    return paths
  }

  function fillPaths(points, segments, range) {
    var paths = []
    var bottom = chart.plotHeightPx
    for (var s = 0; s < segments.length; ++s) {
      var indices = segments[s]
      if (indices.length < 2) continue
      var first = points[indices[0]]
      var last = points[indices[indices.length - 1]]
      if (!first || !last) continue
      var path = [Qt.point(root.xForTime(first.t), bottom)]
      for (var i = 0; i < indices.length; ++i) {
        var point = points[indices[i]]
        if (point === null || point === undefined) continue
        path.push(Qt.point(root.xForTime(point.t), root.yForValue(point.v, range)))
      }
      // Closed at the same evidence boundary as the stroke. Carrying the fill
      // across a gap would put a roof over an absence the line itself refuses.
      path.push(Qt.point(root.xForTime(last.t), bottom))
      paths.push(path)
    }
    return paths
  }

  function nearestIndex(mouseX) {
    var nearest = -1
    var nearestDistance = Infinity
    for (var i = 0; i < chart.points.length; ++i) {
      var point = chart.points[i]
      if (point === null) continue
      var distance = Math.abs(root.xForTime(point.t) - mouseX)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = i
      }
    }
    return nearest
  }

  QtObject {
    id: chart

    readonly property var points: root.series(root.points)
    readonly property var overlaySeries: {
      var result = []
      var source = Array.isArray(root.overlays) ? root.overlays : []
      for (var i = 0; i < source.length; ++i) {
        var overlay = source[i]
        if (overlay === null || overlay === undefined) continue
        result.push({
          points: root.series(overlay.points),
          role: typeof overlay.role === "string" ? overlay.role : "muted",
          dashed: overlay.dashed === true
        })
      }
      return result
    }

    // One threshold for every series in the chart, taken from the primary. The
    // overlays are sampled at the same instants, so giving each its own would
    // let a sparse commanded-speed track disagree with the actual it annotates.
    readonly property var threshold: root.finiteNumber(root.gapSeconds)
      ? root.gapSeconds : root.gapThreshold(chart.points)

    // Actual, commanded and goal share km/h, so they share one range too. Fact
    // 2: the gap between commanded and actual IS the measured ~0.25 km/h per
    // second ramp, and separate scales would erase exactly that.
    readonly property var range: {
      var values = root.collectValues(chart.points, [])
      for (var i = 0; i < chart.overlaySeries.length; ++i) {
        root.collectValues(chart.overlaySeries[i].points, values)
      }
      return root.computeValueRange(values)
    }

    readonly property real plotHeightPx: Math.max(0, root.plotHeight)
    readonly property real plotWidth: Math.max(0, root.width)

    readonly property real dataStart: {
      for (var i = 0; i < chart.points.length; ++i) {
        if (chart.points[i] !== null) return chart.points[i].t
      }
      return 0
    }
    readonly property real dataEnd: {
      for (var i = chart.points.length - 1; i >= 0; --i) {
        if (chart.points[i] !== null) return chart.points[i].t
      }
      return 1
    }
    readonly property real spanStart: root.finiteNumber(root.rangeStart)
      ? root.rangeStart : chart.dataStart
    readonly property real spanEnd: Math.max(
      root.finiteNumber(root.rangeEnd) ? root.rangeEnd : chart.dataEnd,
      chart.spanStart + 1)
    readonly property real spanSeconds: chart.spanEnd - chart.spanStart

    // Each of these derives its segments from the SAME array it then walks, in
    // one binding. Holding the segments in a property of their own is what
    // produced 13 TypeErrors on the first live run: `points` and `segments` are
    // separate bindings over the same input, so during a settle they can be one
    // revision apart and a stale segment indexes a point that no longer exists.
    // Recomputing costs one pass and removes the whole class.
    readonly property var linePaths: {
      var points = chart.points
      return root.strokePaths(
        points, root.segmentsOf(points, chart.threshold), chart.range)
    }
    readonly property var areaPaths: {
      if (!root.filled) return []
      var points = chart.points
      return root.fillPaths(
        points, root.segmentsOf(points, chart.threshold), chart.range)
    }
    readonly property var overlayPaths: {
      var result = []
      var series = chart.overlaySeries
      for (var i = 0; i < series.length; ++i) {
        result.push({
          paths: root.strokePaths(
            series[i].points,
            root.segmentsOf(series[i].points, chart.threshold),
            chart.range),
          role: series[i].role,
          dashed: series[i].dashed
        })
      }
      return result
    }

    readonly property real cursorX: {
      if (root.cursorIndex < 0 || root.cursorIndex >= chart.points.length) return -1
      var point = chart.points[root.cursorIndex]
      if (point === null) return -1
      return root.xForTime(point.t)
    }
  }

  implicitWidth: Math.max(1, chart.points.length)
  implicitHeight: root.plotHeight + root.axisHeight

  Item {
    id: plotArea
    x: 0
    y: 0
    width: root.width
    height: chart.plotHeightPx
    clip: true

    Repeater {
      model: Math.max(0, Math.min(2, root.guideCount))

      Rectangle {
        required property int index
        x: 0
        y: plotArea.height * (index + 1) / (Math.min(2, root.guideCount) + 1)
        width: plotArea.width
        height: Math.max(1, root.lineWidth)
        color: root.alphaColor(root.muted, 0.18)
      }
    }

    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      visible: root.filled && chart.areaPaths.length > 0

      ShapePath {
        strokeColor: "transparent"
        strokeWidth: 0
        fillGradient: LinearGradient {
          x1: 0
          y1: 0
          x2: 0
          y2: plotArea.height
          GradientStop { position: 0.0; color: root.alphaColor(root.fg, 0.30) }
          GradientStop { position: 1.0; color: root.alphaColor(root.fg, 0.02) }
        }

        PathMultiline { paths: chart.areaPaths }
      }
    }

    Repeater {
      // Shape is an Item, so a Repeater can carry an unbounded number of
      // overlays without this component naming them or fixing their order. The
      // model is a COUNT rather than the array, so a geometry change rebinds the
      // existing delegates instead of tearing every path down and rebuilding it.
      model: chart.overlayPaths.length

      Shape {
        id: overlayShape
        required property int index
        readonly property var overlay: chart.overlayPaths[overlayShape.index]

        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        visible: overlayShape.overlay !== undefined
          && overlayShape.overlay.paths.length > 0

        ShapePath {
          strokeColor: root.alphaColor(
            root.roleColor(overlayShape.overlay
              ? overlayShape.overlay.role : "muted"), 0.9)
          strokeWidth: Math.max(1, root.lineWidth)
          fillColor: "transparent"
          capStyle: ShapePath.RoundCap
          joinStyle: ShapePath.RoundJoin
          strokeStyle: overlayShape.overlay && overlayShape.overlay.dashed
            ? ShapePath.DashLine : ShapePath.SolidLine
          // dashPattern is in multiples of strokeWidth, so the caller's pixel
          // lengths have to be divided back out to survive a theme scale change.
          dashPattern: [
            Math.max(0.1, root.dashLength / Math.max(1, root.lineWidth)),
            Math.max(0.1, root.dashSpacing / Math.max(1, root.lineWidth))
          ]

          PathMultiline {
            paths: overlayShape.overlay ? overlayShape.overlay.paths : []
          }
        }
      }
    }

    Shape {
      anchors.fill: parent
      preferredRendererType: Shape.CurveRenderer
      visible: chart.linePaths.length > 0

      ShapePath {
        strokeColor: root.alphaColor(root.fg, 0.95)
        strokeWidth: Math.max(1, root.lineWidth)
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin

        PathMultiline { paths: chart.linePaths }
      }
    }

    Rectangle {
      // The baseline, so an empty frame still reads as a chart rather than a
      // rendering failure.
      x: 0
      y: plotArea.height - Math.max(1, root.lineWidth)
      width: plotArea.width
      height: Math.max(1, root.lineWidth)
      color: root.alphaColor(root.muted, 0.5)
    }

    Rectangle {
      visible: chart.cursorX >= 0
      x: chart.cursorX
      y: 0
      width: Math.max(1, root.lineWidth)
      height: plotArea.height
      color: root.alphaColor(root.accent, 0.85)
    }

    // Charts §3.5: exactly ONE pointer authority for the whole chart.
    MouseArea {
      anchors.fill: parent
      enabled: root.interactive && chart.points.length > 0
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
      onPositionChanged: function(mouse) {
        root.cursorMoved(root.nearestIndex(mouse.x))
      }
      onExited: root.cursorMoved(-1)
    }

    // Charts §3.3: value labels sit outside the data, small and muted.
    Text {
      anchors.right: parent.right
      anchors.top: parent.top
      visible: root.maximumLabel !== ""
      text: root.maximumLabel
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
    }

    Text {
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      visible: root.minimumLabel !== ""
      text: root.minimumLabel
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
    }
  }

  Item {
    id: axisStrip
    x: 0
    y: chart.plotHeightPx
    width: root.width
    height: Math.max(0, root.axisHeight)
    clip: true

    Text {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      text: root.axisStartLabel
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
    }

    Text {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      text: root.axisEndLabel
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: root.labelFontSize
    }
  }
}
