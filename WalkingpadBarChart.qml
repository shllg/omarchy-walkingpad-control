pragma ComponentBehavior: Bound

import QtQuick

// Bucketed bars on a real time axis, drawn as Rectangles.
//
// Charts §3.1: a bar chart IS rectangles. A Repeater of them gives crisp edges
// at any DPI, takes theme colours directly, animates for free, and needs no
// repaint plumbing or manual antialiasing -- which is also why the Canvas this
// replaced looked muddy beside the rest of the shell.
//
// Charts §3.5 keeps two rules load-bearing here. The component owns NO colour
// and imports no palette: the panel loader injects `service` but not `bar`
// (shell.qml:629-637), so the analysis window cannot use the bar palette while
// the popup already does, and a component importing either would be unusable in
// one of its two homes. Sizes arrive the same way, and callers pass Style
// values. It does not own its cursor either -- it renders what it is given and
// emits cursorMoved.
Item {
  id: root

  // ---------------------------------------------------------------- data
  //
  // Each entry is { t: bucket start in epoch seconds, value: number }. A bucket
  // that was never observed is ABSENT from this array rather than present at
  // zero: charts §1 makes that gap the information, so nothing here invents a
  // bar for it. A bucket that was observed and did not advance is present at
  // zero and draws a baseline-height bar, which is a different statement.
  property var buckets: []
  property real bucketSeconds: 60

  // The axis extent, as epoch seconds. rangeEnd is EXCLUSIVE, matching the way
  // a bucket covers [t, t + bucketSeconds). Leave either non-finite to derive it
  // from the data.
  property real rangeStart: NaN
  property real rangeEnd: NaN

  // The bucket that is still filling. Charts §1: it must render distinctly or a
  // half-collected minute reads as a slowdown.
  property real partialBucketStart: NaN

  // ------------------------------------------------------------- palette
  property color fg
  property color accent
  property color muted

  // ------------------------------------------------------------ geometry
  required property real plotHeight
  required property real axisHeight
  required property real barSpacing
  // Charts §3.4: ten bars must look like ten bars, not ten billboards.
  required property real maxBarWidth
  required property real cornerRadius
  required property real lineWidth
  required property string fontFamily
  required property real labelFontSize
  // Charts §3.3: no box around the plot -- a baseline, and at most two faint
  // horizontal guides.
  property int guideCount: 0

  // --------------------------------------------------------------- labels
  // Formatting is the host's, not this component's: the popup names minutes and
  // the window names days, and neither locale decision belongs in a renderer.
  property string axisStartLabel: ""
  property string axisEndLabel: ""
  property string valueUnit: ""

  // --------------------------------------------------------------- cursor
  property int cursorIndex: -1
  property bool interactive: true
  signal cursorMoved(int index)

  // What was actually drawn, in drawing order. Published so a host can render a
  // readout for cursorIndex and bound its own keyboard navigation without
  // repeating the validation and ordering below.
  readonly property var entries: chart.entries

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value)
  }

  function alphaColor(source, alpha) {
    // The hue always comes from the caller. Only opacity belongs here, because
    // the same series must stay recognisable in both independent palettes.
    return Qt.rgba(source.r, source.g, source.b, source.a * alpha)
  }

  // Degenerate input renders an empty frame and never throws (charts §3.5):
  // an empty array, a null entry, a non-numeric bucket time, a negative value
  // and a non-monotonic sequence all pass through here into something drawable.
  function usableBuckets(source) {
    var result = []
    if (!Array.isArray(source)) return result
    for (var i = 0; i < source.length; ++i) {
      var entry = source[i]
      if (entry === null || entry === undefined) continue
      if (!root.finiteNumber(entry.t)) continue
      var value = root.finiteNumber(entry.value) ? entry.value : 0
      result.push({ t: entry.t, value: Math.max(0, value) })
    }
    result.sort(function(left, right) { return left.t - right.t })
    return result
  }

  function peakOf(source) {
    var peak = 0
    for (var i = 0; i < source.length; ++i) peak = Math.max(peak, source[i].value)
    return peak
  }

  function nearestIndex(mouseX) {
    var count = chart.entries.length
    if (count === 0) return -1
    var nearest = -1
    var nearestDistance = Infinity
    for (var i = 0; i < count; ++i) {
      var centre = chart.slotX(i) + chart.slotWidth / 2
      var distance = Math.abs(centre - mouseX)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = i
      }
    }
    return nearest
  }

  QtObject {
    id: chart

    readonly property var entries: root.usableBuckets(root.buckets)
    readonly property real peak: root.peakOf(chart.entries)

    readonly property real bucketWidth:
      root.finiteNumber(root.bucketSeconds) && root.bucketSeconds > 0
        ? root.bucketSeconds : 60

    readonly property real dataStart:
      chart.entries.length > 0 ? chart.entries[0].t : 0
    readonly property real dataEnd: chart.entries.length > 0
      ? chart.entries[chart.entries.length - 1].t + chart.bucketWidth
      : chart.bucketWidth

    readonly property real spanStart: root.finiteNumber(root.rangeStart)
      ? root.rangeStart : chart.dataStart
    // At least one bucket wide, so a single-bucket view and a zero range both
    // divide by something finite instead of collapsing the mapping.
    readonly property real spanEnd: Math.max(
      root.finiteNumber(root.rangeEnd) ? root.rangeEnd : chart.dataEnd,
      chart.spanStart + chart.bucketWidth)
    readonly property real spanSeconds: chart.spanEnd - chart.spanStart

    readonly property real available: Math.max(0, root.width)
    readonly property real naturalSlot:
      chart.available * chart.bucketWidth / chart.spanSeconds
    readonly property real slotCeiling: Math.max(1, root.maxBarWidth + root.barSpacing)
    // Charts §3.4: rather than stretching a handful of buckets across the whole
    // width, shrink the PLOT and left-align it. The time mapping survives at a
    // smaller scale instead of being abandoned, so a six-minute session inside a
    // day view still occupies six minutes of width.
    readonly property real plotWidth: chart.naturalSlot > chart.slotCeiling
      ? chart.available * chart.slotCeiling / chart.naturalSlot
      : chart.available
    readonly property real slotWidth: Math.min(chart.naturalSlot, chart.slotCeiling)
    readonly property real barWidth: Math.max(1, chart.slotWidth - root.barSpacing)

    function slotX(index) {
      if (index < 0 || index >= chart.entries.length) return 0
      var x = (chart.entries[index].t - chart.spanStart)
        / chart.spanSeconds * chart.plotWidth
      if (!root.finiteNumber(x)) return 0
      return Math.max(0, Math.min(chart.plotWidth - chart.slotWidth, x))
    }

    function barHeight(value) {
      if (!(chart.peak > 0)) return 0
      var height = value / chart.peak * Math.max(0, root.plotHeight)
      return root.finiteNumber(height) ? Math.max(0, height) : 0
    }
  }

  // Read from implicitHeight rather than assigning peakLabel.height: a Text's
  // implicit height depends on its own height once wrapping is in play, and
  // binding one to the other is a loop Qt reports at every rebuild.
  readonly property real headerHeight:
    peakLabel.visible ? peakLabel.implicitHeight : 0

  // Sizes are the caller's (charts §3.5), so the only implicit width this can
  // honestly claim is the space its own bars need at their maximum.
  implicitWidth: Math.max(1, chart.entries.length)
    * (root.maxBarWidth + root.barSpacing)
  implicitHeight: root.headerHeight + root.plotHeight + root.axisHeight

  Text {
    id: peakLabel
    // Aligned to the PLOT, not the item. When §3.4 shrinks the plot so the bars
    // stay narrow, a label pinned to the item's right edge would sit far past
    // the last bar and, for the axis labels below, name a time that is not there.
    x: Math.max(0, chart.plotWidth - peakLabel.width)
    y: 0
    // Charts §1: the peak value is the only y-axis furniture. It sits ABOVE the
    // plot rather than inside it so it can never overlay the tallest bar, which
    // by construction reaches exactly the top of the plot.
    visible: chart.peak > 0
    text: Math.round(chart.peak)
      + (root.valueUnit !== "" ? " " + root.valueUnit : "")
    color: root.muted
    font.family: root.fontFamily
    font.pixelSize: root.labelFontSize
  }

  Item {
    id: plotArea
    x: 0
    y: root.headerHeight
    width: root.width
    height: Math.max(0, root.plotHeight)

    // Charts §3.3: at most two faint guides, and no box.
    Repeater {
      model: Math.max(0, Math.min(2, root.guideCount))

      Rectangle {
        required property int index
        x: 0
        y: plotArea.height * (index + 1) / (Math.min(2, root.guideCount) + 1)
        width: chart.plotWidth
        height: Math.max(1, root.lineWidth)
        color: root.alphaColor(root.muted, 0.18)
      }
    }

    Repeater {
      // An integer model, not the array: the popup replaces its buckets once a
      // second, and rebinding persistent delegates lets the height Behavior
      // below animate the change instead of tearing every bar down and building
      // it again.
      model: chart.entries.length

      Rectangle {
        id: bar
        required property int index
        readonly property var entry: chart.entries[bar.index]
        readonly property bool partial: root.finiteNumber(root.partialBucketStart)
          && bar.entry !== undefined
          && bar.entry.t === root.partialBucketStart
        readonly property bool selected: root.cursorIndex === bar.index

        x: chart.slotX(bar.index)
        width: chart.barWidth
        height: bar.entry === undefined ? 0 : chart.barHeight(bar.entry.value)
        y: plotArea.height - bar.height
        radius: Math.min(root.cornerRadius, bar.width / 2)
        antialiasing: bar.radius > 0

        // The still-filling bucket is dimmed rather than hidden: charts §1 wants
        // it visible AND unmistakable, because a bucket that is 20 s into its
        // minute would otherwise read as a genuine slowdown.
        color: bar.selected
          ? root.accent
          : root.alphaColor(root.fg, bar.partial ? 0.35 : 0.8)

        Behavior on height { NumberAnimation { duration: 180 } }
        Behavior on color { ColorAnimation { duration: 160 } }
      }
    }

    Rectangle {
      // The baseline. A measured zero draws no bar at all, so this is what says
      // the axis is there rather than the chart having failed to render.
      x: 0
      y: plotArea.height - Math.max(1, root.lineWidth)
      width: chart.plotWidth
      height: Math.max(1, root.lineWidth)
      color: root.alphaColor(root.muted, 0.5)
    }

    MouseArea {
      id: pointerArea
      anchors.fill: parent
      enabled: root.interactive && chart.entries.length > 0
      hoverEnabled: true
      acceptedButtons: Qt.NoButton
      // Emitted, never assigned: keyboard navigation owns cursorIndex in the
      // popup, and a component that assigned it would fight its own host.
      onPositionChanged: function(mouse) {
        root.cursorMoved(root.nearestIndex(mouse.x))
      }
      onExited: root.cursorMoved(-1)
    }
  }

  Item {
    id: axisStrip
    x: 0
    y: root.headerHeight + root.plotHeight
    width: Math.max(0, chart.plotWidth)
    height: Math.max(0, root.axisHeight)
    clip: true

    // Charts §3.3: axis labels outside the plot, small and muted, never
    // overlaying data.
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
