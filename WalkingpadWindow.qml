import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui

Item {
  id: root

  // The panel loader assigns these after the asynchronous Loader resolves.
  // None is required to exist at mount time, especially with keepLoaded=true.
  property var omarchyPath: null
  property var shell: null
  property var manifest: null
  property var barWidgetRegistry: null
  property var pluginRegistry: null
  property var service: null
  property bool opened: false

  // This loader never injects `bar`, so every live palette binding comes from
  // the menu roles. Referencing the popup's bar palette would crash this root.
  property color background: Color.menu.background
  property color foreground: Color.menu.text
  property color accent: Color.menu.selectedText
  property color chip: Color.menu.selectedBackground
  property color border: Color.menu.border
  property color scrim: Color.menu.scrim
  property var borderSpec: Border.surfaceSpec(
    "menu", "border", border, Math.max(1, Style.space(2)))
  property string fontFamily: Style.font.menuFamily
  readonly property int cornerRadius: Style.cornerRadius
  readonly property int contentMargin: Style.spacing.panelPadding

  function urlToLocalPath(url) {
    var value = url.toString()
    if (value.indexOf("file://") === 0) return decodeURIComponent(value.substring(7))
    return value
  }

  readonly property string statsPath:
    root.urlToLocalPath(Qt.resolvedUrl("bin/walkingpad-stats"))
  readonly property real manifestGapMinutes: {
    var barWidget = root.manifest && root.manifest.barWidget
    var defaults = barWidget && barWidget.defaults
    var value = defaults ? Number(defaults.gapMinutes) : NaN
    return isFinite(value) ? value : 5
  }
  // Stage 3 §7 and ADR D-5: the service wins because widget forwarding is the
  // only live settings path. The overlay loader injects no settings, so its
  // manifest default wins when that path is absent. This value only changes
  // read-time grouping and is never routed into the bridge's safety policy.
  readonly property real gapMinutes:
    (root.service && typeof root.service.gapMinutes === "number"
      && isFinite(root.service.gapMinutes))
      ? Number(root.service.gapMinutes) : root.manifestGapMinutes

  property string period: "day"
  readonly property var periodOptions: ["day", "week", "month", "all"]
  property int refreshEpoch: 0
  property var currentTotals: root.emptyTotals()
  property string totalsError: ""
  property var sessions: []
  property var sessionGroups: []
  property string sessionsError: ""
  property var selectedSessionStart: null
  property var selectedSessionSamples: []
  property bool detailLoaded: false
  property string detailError: ""

  function emptyTotals() {
    return {
      dur_s: 0,
      dist_km: 0,
      steps: 0,
      v_avg: 0,
      unobserved_s: 0,
      unobserved_dist_km: 0,
      unobserved_steps: 0
    }
  }

  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value)
  }

  function numeric(value) {
    return root.finiteNumber(value) ? value : 0
  }

  function formatDuration(value) {
    var total = Math.max(0, Math.round(root.numeric(value)))
    var hours = Math.floor(total / 3600)
    var minutes = Math.floor((total % 3600) / 60)
    var seconds = total % 60
    if (hours > 0) {
      return String(hours) + ":" + String(minutes).padStart(2, "0")
        + ":" + String(seconds).padStart(2, "0")
    }
    return String(minutes) + ":" + String(seconds).padStart(2, "0")
  }

  function formatDistance(value) {
    return root.numeric(value).toFixed(2) + " km"
  }

  function formatSteps(value) {
    return String(Math.max(0, Math.round(root.numeric(value))))
  }

  function formatAverageSpeed(value) {
    if (value === null || value === undefined) return "—"
    return root.numeric(value).toFixed(1) + " km/h"
  }

  function localDateISO(date) {
    var y = date.getFullYear()
    var m = String(date.getMonth() + 1).padStart(2, "0")
    var d = String(date.getDate()).padStart(2, "0")
    return y + "-" + m + "-" + d
  }

  function currentBucketKey(selectedPeriod) {
    var now = new Date()
    if (selectedPeriod === "all") return "all"
    if (selectedPeriod === "month") {
      return now.getFullYear() + "-"
        + String(now.getMonth() + 1).padStart(2, "0")
    }
    if (selectedPeriod === "week") {
      // Python keys weeks by the local date of Monday, while JavaScript starts
      // getDay() on Sunday. This shift keeps both sides on the same bucket.
      var mondayOffset = (now.getDay() + 6) % 7
      var monday = new Date(now)
      monday.setDate(now.getDate() - mondayOffset)
      return root.localDateISO(monday)
    }
    return root.localDateISO(now)
  }

  function periodLabel(selectedPeriod) {
    if (selectedPeriod === "all") return "All time"
    return "Current " + selectedPeriod
  }

  function dayLabel(key) {
    var parts = key.split("-")
    var date = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]))
    return Qt.formatDate(date, "dddd, d MMMM yyyy")
  }

  function timeRange(session) {
    var start = new Date(root.numeric(session.start) * 1000)
    var end = new Date(root.numeric(session.end) * 1000)
    return Qt.formatTime(start, "HH:mm") + " – " + Qt.formatTime(end, "HH:mm")
  }

  function sessionSummary(session) {
    return root.timeRange(session) + "  ·  " + root.formatDuration(session.dur_s)
      + "  ·  " + root.formatDistance(session.dist_km)
      + "  ·  " + root.formatSteps(session.steps) + " steps"
  }

  function groupSessionsByDay(source) {
    var byDay = ({})
    var ordered = Array.isArray(source) ? source.slice() : []
    ordered.sort(function(a, b) { return root.numeric(b.start) - root.numeric(a.start) })
    for (var i = 0; i < ordered.length; ++i) {
      var key = root.localDateISO(new Date(root.numeric(ordered[i].start) * 1000))
      if (!byDay[key]) byDay[key] = []
      byDay[key].push(ordered[i])
    }
    var keys = Object.keys(byDay)
    keys.sort(function(a, b) { return a < b ? 1 : (a > b ? -1 : 0) })
    var groups = []
    for (var j = 0; j < keys.length; ++j) {
      groups.push({ key: keys[j], label: root.dayLabel(keys[j]), sessions: byDay[keys[j]] })
    }
    return groups
  }

  function parseStatsOutput(text, fallbackMessage) {
    var payload
    try {
      payload = JSON.parse(String(text || ""))
    } catch (error) {
      return { ok: false, message: fallbackMessage }
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return { ok: false, message: fallbackMessage }
    }
    if (typeof payload.error === "string" && payload.error !== "") {
      return { ok: false, message: payload.error }
    }
    return { ok: true, value: payload }
  }

  function refreshSessions() {
    if (!root.opened) return
    if (sessionsProc.running) {
      sessionsProc.queued = true
      return
    }
    sessionsProc.queued = false
    sessionsProc.requestGap = root.gapMinutes
    sessionsProc.requestEpoch = root.refreshEpoch
    sessionsProc.running = true
  }

  function refreshTotals() {
    if (!root.opened) return
    if (totalsProc.running) {
      totalsProc.queued = true
      return
    }
    totalsProc.queued = false
    totalsProc.requestPeriod = root.period
    totalsProc.requestEpoch = root.refreshEpoch
    totalsProc.running = true
  }

  function refreshSessionDetail() {
    if (!root.opened || root.selectedSessionStart === null) return
    if (sessionProc.running) {
      sessionProc.queued = true
      return
    }
    sessionProc.queued = false
    sessionProc.requestStart = Number(root.selectedSessionStart)
    sessionProc.requestGap = root.gapMinutes
    sessionProc.requestEpoch = root.refreshEpoch
    sessionProc.running = true
  }

  function handleSessions(text, requestGap, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch || requestGap !== root.gapMinutes) return
    var result = root.parseStatsOutput(text, "The session history could not be read.")
    if (!result.ok) {
      root.sessions = []
      root.sessionGroups = []
      root.sessionsError = result.message
      return
    }
    if (!Array.isArray(result.value.sessions)) {
      root.sessions = []
      root.sessionGroups = []
      root.sessionsError = "The session history has an unexpected shape."
      return
    }
    root.sessionsError = ""
    root.sessions = result.value.sessions
    root.sessionGroups = root.groupSessionsByDay(result.value.sessions)
  }

  function handleTotals(text, requestPeriod, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch || requestPeriod !== root.period) return
    root.currentTotals = root.emptyTotals()
    var result = root.parseStatsOutput(text, "The totals could not be read.")
    if (!result.ok) {
      root.totalsError = result.message
      return
    }
    if (!Array.isArray(result.value.buckets)) {
      root.totalsError = "The totals have an unexpected shape."
      return
    }
    root.totalsError = ""
    var key = root.currentBucketKey(requestPeriod)
    for (var i = 0; i < result.value.buckets.length; ++i) {
      if (String(result.value.buckets[i].key) === key) {
        root.currentTotals = result.value.buckets[i]
        return
      }
    }
    // No bucket for today/this week/etc. is the normal empty-store state, not
    // a reporting failure, so the strip deliberately remains zeroed.
  }

  function handleSessionDetail(text, requestStart, requestGap, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch
        || requestGap !== root.gapMinutes
        || root.selectedSessionStart === null
        || requestStart !== Number(root.selectedSessionStart)) return
    var result = root.parseStatsOutput(text, "The selected session could not be read.")
    if (!result.ok) {
      root.selectedSessionSamples = []
      root.detailLoaded = false
      root.detailError = result.message
      return
    }
    if (!Array.isArray(result.value.samples)) {
      root.selectedSessionSamples = []
      root.detailLoaded = false
      root.detailError = "The selected session has an unexpected shape."
      return
    }
    root.detailError = ""
    root.selectedSessionSamples = result.value.samples
    root.detailLoaded = true
  }

  function setPeriod(value) {
    if (root.periodOptions.indexOf(value) === -1 || root.period === value) return
    root.period = value
    root.currentTotals = root.emptyTotals()
    root.totalsError = ""
    root.refreshTotals()
  }

  function selectSession(session) {
    root.selectedSessionStart = session.start
    root.selectedSessionSamples = []
    root.detailLoaded = false
    root.detailError = ""
    root.refreshSessionDetail()
  }

  function open(payloadJson) {
    var payload = ({})
    try { payload = JSON.parse(payloadJson || "{}") } catch (error) { payload = ({}) }
    if (!payload || typeof payload !== "object") payload = ({})

    root.refreshEpoch++
    root.period = "day"
    root.currentTotals = root.emptyTotals()
    root.totalsError = ""
    root.sessions = []
    root.sessionGroups = []
    root.sessionsError = ""
    root.selectedSessionStart = null
    root.selectedSessionSamples = []
    root.detailLoaded = false
    root.detailError = ""
    root.opened = true
    root.refreshSessions()
    root.refreshTotals()
    Qt.callLater(function() { keyCatcher.forceActiveFocus() })
  }

  function close() {
    root.opened = false
  }

  function dismiss() {
    root.opened = false
    if (root.shell && typeof root.shell.hide === "function") {
      root.shell.hide((root.manifest && root.manifest.id)
        || "io.github.shllg.walkingpad")
    }
  }

  onGapMinutesChanged: {
    // A new gap can regroup every row, so retaining a selection would ask the
    // exact-float detail query for a session boundary that may no longer exist.
    if (!root.opened) return
    root.sessions = []
    root.sessionGroups = []
    root.sessionsError = ""
    root.selectedSessionStart = null
    root.selectedSessionSamples = []
    root.detailLoaded = false
    root.detailError = ""
    root.refreshSessions()
  }

  Process {
    id: sessionsProc
    property real requestGap: 0
    property int requestEpoch: 0
    property bool queued: false
    command: [root.statsPath, "sessions", "--gap", String(requestGap)]
    stdout: StdioCollector { id: sessionsOut; waitForEnd: true }
    onExited: function() {
      root.handleSessions(sessionsOut.text, requestGap, requestEpoch)
      if (queued || requestGap !== root.gapMinutes
          || requestEpoch !== root.refreshEpoch) {
        Qt.callLater(function() { root.refreshSessions() })
      }
    }
  }

  Process {
    id: totalsProc
    property string requestPeriod: "day"
    property int requestEpoch: 0
    property bool queued: false
    // totals has no --gap option. Passing the display preference here makes
    // argparse return an error instead of totals, so only --by belongs here.
    command: [root.statsPath, "totals", "--by", requestPeriod]
    stdout: StdioCollector { id: totalsOut; waitForEnd: true }
    onExited: function() {
      root.handleTotals(totalsOut.text, requestPeriod, requestEpoch)
      if (queued || requestPeriod !== root.period
          || requestEpoch !== root.refreshEpoch) {
        Qt.callLater(function() { root.refreshTotals() })
      }
    }
  }

  Process {
    id: sessionProc
    property real requestStart: 0
    property real requestGap: 0
    property int requestEpoch: 0
    property bool queued: false
    // String() preserves the stored epoch double. Rounding would break the
    // query's exact server-side match against the first bout's start value.
    command: [root.statsPath, "session", "--start", String(requestStart),
      "--gap", String(requestGap)]
    stdout: StdioCollector { id: sessionOut; waitForEnd: true }
    onExited: function() {
      root.handleSessionDetail(
        sessionOut.text, requestStart, requestGap, requestEpoch)
      if (root.opened && root.selectedSessionStart !== null
          && (queued || requestStart !== Number(root.selectedSessionStart)
            || requestGap !== root.gapMinutes
            || requestEpoch !== root.refreshEpoch)) {
        Qt.callLater(function() { root.refreshSessionDetail() })
      }
    }
  }

  PanelWindow {
    id: analysisPanel
    visible: root.opened
    anchors { top: true; bottom: true; left: true; right: true }
    color: Qt.rgba(root.background.r, root.background.g, root.background.b, 0)
    WlrLayershell.namespace: "walkingpad-analysis"
    WlrLayershell.layer: WlrLayer.Overlay
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive
    exclusionMode: ExclusionMode.Ignore

    Rectangle {
      anchors.fill: parent
      color: root.scrim
    }

    MouseArea {
      anchors.fill: parent
      onClicked: root.dismiss()
    }

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true
      Keys.priority: Keys.BeforeItem
      // This is a browsable data view. Only Escape dismisses it; a catch-all
      // handler would consume keys from any focusable controls added later.
      Keys.onEscapePressed: function(event) {
        root.dismiss()
        event.accepted = true
      }
    }

    BorderSurface {
      id: analysisCard
      width: Math.min(parent.width * 0.82, Style.space(1800))
      height: Math.min(parent.height * 0.88, Style.space(1300))
      anchors.centerIn: parent
      radius: root.cornerRadius
      color: root.background
      borderSpec: root.borderSpec
      padding: root.contentMargin

      // The full-screen dismiss target sits behind the card. This catcher
      // prevents unused card space from behaving like an outside click.
      MouseArea { anchors.fill: parent; onClicked: function() {} }

      Flickable {
        anchors.fill: parent
        anchors.topMargin: analysisCard.contentTopInset
        anchors.leftMargin: analysisCard.contentLeftInset
        anchors.rightMargin: analysisCard.contentRightInset
        anchors.bottomMargin: analysisCard.contentBottomInset
        contentWidth: width
        contentHeight: analysisContent.implicitHeight
        clip: true

        Column {
          id: analysisContent
          width: parent.width
          spacing: Style.space(14)

          Text {
            width: parent.width
            text: "WalkingPad — Analysis"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.displayLarge
            font.bold: true
            wrapMode: Text.WordWrap
          }

          Row {
            id: periodRow
            width: parent.width
            spacing: Style.space(8)
            readonly property real cellWidth:
              (width - spacing * (root.periodOptions.length - 1))
                / root.periodOptions.length

            Repeater {
              model: root.periodOptions

              Rectangle {
                required property string modelData
                width: periodRow.cellWidth
                implicitHeight: periodText.implicitHeight
                  + Style.spacing.controlPaddingY * 2
                radius: Style.cornerRadius
                color: modelData === root.period ? root.chip : root.background

                Text {
                  id: periodText
                  anchors.centerIn: parent
                  text: modelData.charAt(0).toUpperCase() + modelData.slice(1)
                  color: modelData === root.period ? root.accent : root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: modelData === root.period
                }

                MouseArea {
                  anchors.fill: parent
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.setPeriod(parent.modelData)
                }
              }
            }
          }

          Text {
            width: parent.width
            text: root.periodLabel(root.period)
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Row {
            id: totalsRow
            width: parent.width
            spacing: Style.space(12)
            readonly property real cellWidth: (width - spacing * 3) / 4

            TotalStat {
              width: totalsRow.cellWidth
              label: "Distance"
              value: root.formatDistance(root.currentTotals.dist_km)
              // The headline is the exact counter total. The second line names
              // the part reconstructed from counters instead of hiding its provenance.
              unobserved: root.numeric(root.currentTotals.unobserved_dist_km) > 0
                ? "+ " + root.formatDistance(root.currentTotals.unobserved_dist_km)
                  + " unobserved" : ""
            }
            TotalStat {
              width: totalsRow.cellWidth
              label: "Steps"
              value: root.formatSteps(root.currentTotals.steps)
              unobserved: root.numeric(root.currentTotals.unobserved_steps) > 0
                ? "+ " + root.formatSteps(root.currentTotals.unobserved_steps)
                  + " unobserved" : ""
            }
            TotalStat {
              width: totalsRow.cellWidth
              label: "Time"
              value: root.formatDuration(root.currentTotals.dur_s)
              unobserved: root.numeric(root.currentTotals.unobserved_s) > 0
                ? "+ " + root.formatDuration(root.currentTotals.unobserved_s)
                  + " unobserved" : ""
            }
            TotalStat {
              width: totalsRow.cellWidth
              label: "Average speed"
              value: root.formatAverageSpeed(root.currentTotals.v_avg)
            }
          }

          SectionNote {
            visible: root.totalsError !== ""
            text: root.totalsError
          }

          Text {
            width: parent.width
            text: "Sessions"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          SectionNote {
            visible: root.sessionsError !== ""
            text: root.sessionsError
          }

          SectionNote {
            visible: root.sessionsError === "" && root.sessionGroups.length === 0
            text: "No walks recorded yet"
          }

          Repeater {
            model: root.sessionGroups

            Column {
              id: dayGroup
              required property var modelData
              width: analysisContent.width
              spacing: Style.spacing.labelGap

              Text {
                width: parent.width
                text: dayGroup.modelData.label
                color: root.foreground
                opacity: 0.65
                font.family: root.fontFamily
                font.pixelSize: Style.font.subtitle
                font.bold: true
              }

              Repeater {
                model: dayGroup.modelData.sessions

                Rectangle {
                  id: sessionRow
                  required property var modelData
                  width: dayGroup.width
                  implicitHeight: sessionLabels.implicitHeight
                    + Style.spacing.controlPaddingY * 2
                  radius: Style.cornerRadius
                  readonly property bool selected:
                    root.selectedSessionStart !== null
                      && Number(modelData.start) === Number(root.selectedSessionStart)
                  color: selected
                    ? root.chip
                    : (sessionMouse.containsMouse
                      ? Qt.rgba(root.chip.r, root.chip.g, root.chip.b,
                          root.chip.a * 0.35)
                      : root.background)

                  Column {
                    id: sessionLabels
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: Style.spacing.controlPaddingX
                    anchors.rightMargin: Style.spacing.controlPaddingX
                    spacing: Style.spacing.labelGap

                    Text {
                      width: parent.width
                      text: root.sessionSummary(sessionRow.modelData)
                      color: sessionRow.selected ? root.accent : root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.body
                      font.bold: sessionRow.selected
                      wrapMode: Text.WordWrap
                    }

                    Text {
                      width: parent.width
                      visible: root.numeric(sessionRow.modelData.unobserved_s) > 0
                      text: "Partly reconstructed from pad counters"
                      color: sessionRow.selected ? root.accent : root.foreground
                      opacity: 0.65
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      wrapMode: Text.WordWrap
                    }
                  }

                  MouseArea {
                    id: sessionMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.selectSession(sessionRow.modelData)
                  }
                }
              }
            }
          }

          Rectangle {
            width: parent.width
            height: Style.space(1)
            color: root.border
            opacity: 0.4
          }

          Text {
            width: parent.width
            text: "Session chart"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          SectionNote {
            visible: root.selectedSessionStart === null
            text: "Select a session above to see its chart."
          }

          SectionNote {
            visible: root.selectedSessionStart !== null
              && !root.detailLoaded && root.detailError === ""
            text: "Loading the selected session…"
          }

          SectionNote {
            visible: root.detailError !== ""
            text: root.detailError
          }

          SectionNote {
            // A wholly reconstructed session has exact counter totals but no
            // samples. An empty chart would imply a rendering failure, so state
            // explicitly why there is no curve instead of mounting the canvas.
            visible: root.detailLoaded
              && root.selectedSessionSamples.length === 0
            text: "No samples recorded for this session — every bout was reconstructed "
              + "from the pad's own counters, not measured, so there is no curve to draw."
          }

          WalkingpadChartStack {
            id: sessionChart
            width: parent.width
            visible: root.detailLoaded && root.selectedSessionSamples.length > 0
            samples: root.selectedSessionSamples
            rows: ["speed", "cadence", "distance", "steps"]
            // Stage 3 §6.3 makes the analysis rows deliberately tall. As in
            // the compact popup, every Canvas dimension enters through Style so
            // theme scaling reaches the reusable component without importing it.
            implicitWidth: Style.space(960)
            rowHeight: Style.space(56)
            rowSpacing: Style.space(6)
            axisHeight: Style.space(16)
            fg: root.foreground
            accent: root.accent
            muted: Qt.darker(root.foreground, 1.6)
            fontFamily: root.fontFamily
            labelFontSize: Style.font.caption
            lineWidth: Style.space(1)
            dashLength: Style.space(4)
            dashSpacing: Style.space(3)

            // The chart deliberately never assigns its own cursorIndex -- it
            // only emits cursorMoved, so a second driver (keyboard, elsewhere)
            // could own the property without a mouse fighting it. This window
            // has no such second driver, so hover is the only input and must
            // close the loop itself or the crosshair line never appears.
            onCursorMoved: function(index) { sessionChart.cursorIndex = index }
          }
        }
      }
    }
  }

  component TotalStat: Column {
    property string label: ""
    property string value: ""
    property string unobserved: ""
    spacing: Style.spacing.labelGap

    Text {
      width: parent.width
      text: parent.label
      color: root.foreground
      opacity: 0.65
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
    }

    Text {
      width: parent.width
      text: parent.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      font.bold: true
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
    }

    Text {
      width: parent.width
      visible: parent.unobserved !== ""
      text: parent.unobserved
      color: root.foreground
      opacity: 0.65
      font.family: root.fontFamily
      font.pixelSize: Style.font.caption
      horizontalAlignment: Text.AlignHCenter
      wrapMode: Text.WordWrap
    }
  }

  component SectionNote: Text {
    width: parent.width
    color: root.foreground
    opacity: 0.65
    font.family: root.fontFamily
    font.pixelSize: Style.font.bodySmall
    wrapMode: Text.WordWrap
  }
}
