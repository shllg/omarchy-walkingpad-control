import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import QtQuick
import qs.Commons
import qs.Ui
import "WalkingpadModel.js" as Model

// The analysis window. Charts §2 restructures it around one rule: exactly ONE
// thing is selected at a time, and the pane header always names what is on
// screen. The shipped version put period tabs and a session list on screen
// together, both driving the same chart, with nothing saying which had won.
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
  readonly property color muted: Qt.darker(root.foreground, 1.6)

  function urlToLocalPath(url) {
    var value = url.toString()
    if (value.indexOf("file://") === 0) return decodeURIComponent(value.substring(7))
    return value
  }

  readonly property string statsPath:
    root.urlToLocalPath(Qt.resolvedUrl("bin/walkingpad-stats"))
  // Named in the integrity note below, because "something is wrong" without the
  // command that fixes it is a worse message than none.
  readonly property string repairPath:
    root.urlToLocalPath(Qt.resolvedUrl("bin/walkingpad-repair-gaps"))
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

  // ------------------------------------------------------------------ state
  property string period: "day"
  readonly property var periodOptions: [
    { key: "day", label: "Today" },
    { key: "week", label: "Week" },
    { key: "month", label: "Month" },
    { key: "all", label: "All time" }
  ]
  property int refreshEpoch: 0

  property var profile: root.emptyProfile()
  property string profileError: ""
  // The standing audit's last answer, or null before it has run once.
  property var verifyResult: null
  property var sessions: []
  property string sessionsError: ""

  // Charts §2.1: selecting a session swaps the right pane, and selecting a
  // period clears the session. Exactly one of these two states is live.
  property var selectedSessionStart: null
  property var detailSession: null
  property var detailSamples: []
  property bool detailLoaded: false
  property string detailError: ""

  readonly property bool sessionMode: root.selectedSessionStart !== null

  // ------------------------------------------------------------- formatting
  function finiteNumber(value) {
    return typeof value === "number" && isFinite(value)
  }

  function numeric(value) {
    return root.finiteNumber(value) ? value : 0
  }

  function emptyTotals() {
    return {
      dur_s: 0, dist_km: 0, steps: 0, v_avg: null,
      unobserved_s: 0, unobserved_dist_km: 0, unobserved_steps: 0
    }
  }

  function emptyProfile() {
    return {
      bucket_s: 60, start: null, end: null,
      buckets: [], speed_histogram: [], totals: root.emptyTotals()
    }
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

  function formatMinutes(value) {
    var minutes = Math.round(root.numeric(value) / 60)
    if (minutes < 1) return "under a minute"
    return String(minutes) + " min"
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

  // The chart publishes the span it actually drew, so the axis is labelled with
  // the numbers on screen rather than a figure that happens to be nearby.
  function rangeLabel(range, wantMaximum, decimals, unit) {
    if (!range) return ""
    var value = wantMaximum ? range.maximum : range.minimum
    if (!root.finiteNumber(value)) return ""
    return value.toFixed(decimals) + unit
  }

  function formatClock(seconds) {
    return Qt.formatTime(new Date(root.numeric(seconds) * 1000), "HH:mm")
  }

  function formatDay(seconds) {
    return Qt.formatDate(new Date(root.numeric(seconds) * 1000), "d MMM")
  }

  function localDateISO(date) {
    var y = date.getFullYear()
    var m = String(date.getMonth() + 1).padStart(2, "0")
    var d = String(date.getDate()).padStart(2, "0")
    return y + "-" + m + "-" + d
  }

  function localISO(date) {
    return root.localDateISO(date) + "T"
      + String(date.getHours()).padStart(2, "0") + ":"
      + String(date.getMinutes()).padStart(2, "0") + ":"
      + String(date.getSeconds()).padStart(2, "0")
  }

  // The query tool reads --since/--until as literal LOCAL timestamps, and its
  // bout filter is inclusive at both ends. So the window ends one second before
  // the next period begins; passing the boundary itself would pull a walk that
  // started exactly at midnight into the day before it.
  function periodWindow(selectedPeriod) {
    if (selectedPeriod === "all") return { since: "", until: "" }
    var now = new Date()
    var start = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    var end = new Date(start.getFullYear(), start.getMonth(), start.getDate() + 1)

    if (selectedPeriod === "week") {
      // Python keys weeks from Monday, while JavaScript starts getDay() on
      // Sunday. This shift keeps both sides on the same week.
      var mondayOffset = (now.getDay() + 6) % 7
      start = new Date(now.getFullYear(), now.getMonth(),
                       now.getDate() - mondayOffset)
      end = new Date(start.getFullYear(), start.getMonth(),
                     start.getDate() + 7)
    } else if (selectedPeriod === "month") {
      start = new Date(now.getFullYear(), now.getMonth(), 1)
      end = new Date(now.getFullYear(), now.getMonth() + 1, 1)
    }

    return {
      since: root.localISO(start),
      until: root.localISO(new Date(end.getTime() - 1000))
    }
  }

  function periodLabelFor(selectedPeriod) {
    for (var i = 0; i < root.periodOptions.length; ++i) {
      if (root.periodOptions[i].key === selectedPeriod) {
        return root.periodOptions[i].label
      }
    }
    return selectedPeriod
  }

  function sessionAt(start) {
    if (start === null) return null
    for (var i = 0; i < root.sessions.length; ++i) {
      if (Number(root.sessions[i].start) === Number(start)) return root.sessions[i]
    }
    return null
  }

  // The detail query's own summary is authoritative once it arrives; until then
  // the rail's row already knows enough to title the pane, so the header never
  // blinks through a nameless state.
  readonly property var activeSession: root.detailSession !== null
    ? root.detailSession : root.sessionAt(root.selectedSessionStart)

  // Charts §2.1: the header always names what is displayed. That, not the
  // layout, is what removes the ambiguity.
  readonly property string paneTitle: {
    if (!root.sessionMode) return root.periodLabelFor(root.period)
    if (root.activeSession === null) return "Session"
    return "Session · " + root.formatClock(root.activeSession.start)
      + " – " + root.formatClock(root.activeSession.end)
  }

  readonly property string paneSubtitle: {
    if (!root.sessionMode) return "All sessions combined"
    if (root.activeSession === null) return ""
    return root.formatDay(root.activeSession.start)
  }

  function railLabel(session) {
    var label = root.formatClock(session.start)
      + "  ·  " + root.formatMinutes(session.dur_s)
    // A day view needs no date; every other period spans several.
    if (root.period === "day") return label
    return root.formatDay(session.start) + "  " + label
  }

  // ------------------------------------------------------------ chart series
  readonly property var paneTotals: root.sessionMode
    ? (root.activeSession !== null ? root.activeSession : root.emptyTotals())
    : root.profile.totals

  readonly property string unobservedNote: {
    var seconds = root.numeric(root.paneTotals.unobserved_s)
    var distance = root.numeric(root.paneTotals.unobserved_dist_km)
    var steps = root.numeric(root.paneTotals.unobserved_steps)
    if (seconds <= 0 && distance <= 0 && steps <= 0) return ""
    // Stage 3 §2.3: `observed: false` marks PROVENANCE, not accuracy. The
    // residual makes the totals above exact; what is missing is samples and
    // placement, which is why this share can be stated but not drawn.
    return "+ " + root.formatMinutes(seconds) + "  ·  "
      + root.formatDistance(distance) + "  ·  "
      + root.formatSteps(steps) + " steps reconstructed from the pad's counters, "
      + "not measured — exact in these totals, absent from the charts"
  }

  // The one thing the totals above cannot say: whether they are complete.
  //
  // Between 2026-08-27 and 2026-09-08 this window reported every day as fact
  // while roughly 40% of each walk was missing from it, and nothing on screen
  // could have told anyone. `walkingpad-stats verify` holds the stored bouts
  // against the raw counters in samples/, and this is where it says so.
  readonly property string integrityNote: {
    if (root.verifyResult === null) return ""
    var missing = root.numeric(root.verifyResult.missing_steps)
    if (!(missing > 0)) return ""
    var days = root.numeric(root.verifyResult.days_mismatched)
    return "⚠ " + root.formatSteps(missing) + " steps the pad counted are "
      + "missing from history on " + days + (days === 1 ? " day" : " days")
      + ". They are still in the stored counters — recover them with "
      + root.repairPath + " --apply"
  }

  readonly property real periodBucketSeconds:
    root.finiteNumber(root.profile.bucket_s) && root.profile.bucket_s > 0
      ? root.profile.bucket_s : 60

  readonly property real periodRangeStart: root.numeric(root.profile.start)
  readonly property real periodRangeEnd: {
    var end = root.numeric(root.profile.end)
    var source = root.profile.buckets
    if (source.length > 0) {
      end = Math.max(end,
        source[source.length - 1].t + root.periodBucketSeconds)
    }
    return end
  }

  readonly property var periodStepBuckets: {
    var out = []
    var source = root.profile.buckets
    for (var i = 0; i < source.length; ++i) {
      out.push({ t: source[i].t, value: source[i].steps })
    }
    return out
  }

  readonly property var periodSpeedPoints: {
    var out = []
    var source = root.profile.buckets
    // Charts §2.2: in period mode the speed curve runs through per-bucket
    // AVERAGES, never per-second samples. A day holds up to 86 400 of those, and
    // pulling them into QML is exactly the unbounded UI-thread work the storage
    // design exists to prevent. The per-second trace is what session mode is for.
    var centre = root.periodBucketSeconds / 2
    for (var i = 0; i < source.length; ++i) {
      out.push({ t: source[i].t + centre, v: source[i].v_avg, e: 0 })
    }
    return out
  }

  readonly property var speedHistogram: {
    var source = root.profile.speed_histogram
    return Array.isArray(source) ? source : []
  }

  readonly property real histogramPeak: {
    var peak = 0
    for (var i = 0; i < root.speedHistogram.length; ++i) {
      peak = Math.max(peak, root.numeric(root.speedHistogram[i].dur_s))
    }
    return peak
  }

  readonly property real sessionRangeStart: root.activeSession !== null
    ? root.numeric(root.activeSession.start) : 0
  readonly property real sessionRangeEnd: root.activeSession !== null
    ? Math.max(root.numeric(root.activeSession.end), root.sessionRangeStart + 1)
    : 1

  readonly property var sessionSpeedPoints:
    Model.fieldSeries(root.detailSamples, "v")
  readonly property var sessionCommandedPoints:
    Model.fieldSeries(root.detailSamples, "c")
  readonly property var sessionGoalPoints:
    Model.fieldSeries(root.detailSamples, "g")
  readonly property var sessionCadencePoints:
    Model.cadenceSeries(root.detailSamples)
  readonly property var sessionStepBuckets: Model.stepBucketsInRange(
    root.detailSamples, root.sessionRangeStart, root.sessionRangeEnd + 1, 60)

  readonly property bool sessionDrawable:
    root.detailLoaded && root.detailSamples.length > 0

  // ------------------------------------------------------------------ queries
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
    var window = root.periodWindow(root.period)
    sessionsProc.queued = false
    sessionsProc.requestGap = root.gapMinutes
    sessionsProc.requestPeriod = root.period
    sessionsProc.requestSince = window.since
    sessionsProc.requestUntil = window.until
    sessionsProc.requestEpoch = root.refreshEpoch
    sessionsProc.running = true
  }

  function refreshProfile() {
    if (!root.opened) return
    if (profileProc.running) {
      profileProc.queued = true
      return
    }
    var window = root.periodWindow(root.period)
    profileProc.queued = false
    profileProc.requestGap = root.gapMinutes
    profileProc.requestPeriod = root.period
    profileProc.requestSince = window.since
    profileProc.requestUntil = window.until
    profileProc.requestEpoch = root.refreshEpoch
    profileProc.running = true
  }

  function refreshVerify() {
    if (!root.opened) return
    // No queueing and no period arguments: it audits the whole store, so one
    // in flight is always still the answer to the question being asked.
    if (verifyProc.running) return
    verifyProc.requestEpoch = root.refreshEpoch
    verifyProc.running = true
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

  function handleSessions(text, requestPeriod, requestGap, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch || requestGap !== root.gapMinutes
        || requestPeriod !== root.period) return
    var result = root.parseStatsOutput(
      text, "The session history could not be read.")
    if (!result.ok) {
      root.sessions = []
      root.sessionsError = result.message
      return
    }
    if (!Array.isArray(result.value.sessions)) {
      root.sessions = []
      root.sessionsError = "The session history has an unexpected shape."
      return
    }
    root.sessionsError = ""
    var ordered = result.value.sessions.slice()
    ordered.sort(function(a, b) { return root.numeric(b.start) - root.numeric(a.start) })
    root.sessions = ordered
    // A regrouped list may no longer contain the selected session at all, and a
    // pane titled for a session the rail cannot show is exactly the ambiguity
    // charts §2.1 removes.
    if (root.sessionMode && root.sessionAt(root.selectedSessionStart) === null
        && root.detailSession === null) {
      root.clearSession()
    }
  }

  function handleProfile(text, requestPeriod, requestGap, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch || requestGap !== root.gapMinutes
        || requestPeriod !== root.period) return
    var result = root.parseStatsOutput(text, "The totals could not be read.")
    if (!result.ok) {
      root.profile = root.emptyProfile()
      root.profileError = result.message
      return
    }
    var payload = result.value
    if (!Array.isArray(payload.buckets) || !Array.isArray(payload.speed_histogram)
        || !payload.totals || typeof payload.totals !== "object") {
      root.profile = root.emptyProfile()
      root.profileError = "The totals have an unexpected shape."
      return
    }
    root.profileError = ""
    root.profile = payload
  }

  function handleVerify(text, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch) return
    var result = root.parseStatsOutput(text, "")
    // Silent on failure by design. The audit is a background reassurance, not
    // something the user asked for, so a broken one must not push an error in
    // front of totals that are themselves fine.
    root.verifyResult = result.ok ? result.value : null
  }

  function handleSessionDetail(text, requestStart, requestGap, requestEpoch) {
    if (requestEpoch !== root.refreshEpoch
        || requestGap !== root.gapMinutes
        || root.selectedSessionStart === null
        || requestStart !== Number(root.selectedSessionStart)) return
    var result = root.parseStatsOutput(
      text, "The selected session could not be read.")
    if (!result.ok) {
      root.detailSamples = []
      root.detailSession = null
      root.detailLoaded = false
      root.detailError = result.message
      return
    }
    if (!Array.isArray(result.value.samples) || !result.value.session) {
      root.detailSamples = []
      root.detailSession = null
      root.detailLoaded = false
      root.detailError = "The selected session has an unexpected shape."
      return
    }
    root.detailError = ""
    root.detailSession = result.value.session
    root.detailSamples = result.value.samples
    root.detailLoaded = true
  }

  function setPeriod(value) {
    var known = false
    for (var i = 0; i < root.periodOptions.length; ++i) {
      if (root.periodOptions[i].key === value) known = true
    }
    if (!known) return

    if (root.period === value) {
      // Clicking the period the rail is already scoped to is the way BACK from a
      // session to the combined view. Returning early here would leave the
      // selected session as the only reachable state in that period.
      root.clearSession()
      return
    }

    root.period = value
    // Charts §2.1: selecting a period clears the session. One thing is selected.
    root.clearSession()
    root.profile = root.emptyProfile()
    root.profileError = ""
    root.sessions = []
    root.sessionsError = ""
    root.refreshProfile()
    root.refreshSessions()
    root.refreshVerify()
  }

  function selectSession(session) {
    root.selectedSessionStart = session.start
    root.detailSession = null
    root.detailSamples = []
    root.detailLoaded = false
    root.detailError = ""
    root.refreshSessionDetail()
  }

  function clearSession() {
    root.selectedSessionStart = null
    root.detailSession = null
    root.detailSamples = []
    root.detailLoaded = false
    root.detailError = ""
  }

  function open(payloadJson) {
    var payload = ({})
    try { payload = JSON.parse(payloadJson || "{}") } catch (error) { payload = ({}) }
    if (!payload || typeof payload !== "object") payload = ({})

    root.refreshEpoch++
    // Charts §2.1: the default is Today, combined, with no session selected.
    root.period = "day"
    root.profile = root.emptyProfile()
    root.profileError = ""
    root.sessions = []
    root.sessionsError = ""
    root.clearSession()
    root.opened = true
    root.refreshProfile()
    root.refreshSessions()
    // The audit runs whenever the window is looked at, which is the only place
    // it could ever be seen. It reads the whole store, so it is far too heavy
    // for the poll loop and exactly right here.
    root.refreshVerify()
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
    root.sessionsError = ""
    root.clearSession()
    root.refreshSessions()
  }

  Process {
    id: sessionsProc
    property string requestPeriod: "day"
    property string requestSince: ""
    property string requestUntil: ""
    property real requestGap: 0
    property int requestEpoch: 0
    property bool queued: false
    command: {
      var argv = [root.statsPath, "sessions", "--gap", String(requestGap)]
      // Omitted rather than passed empty: an empty --since reaches
      // datetime.fromisoformat and comes back as an error object, not sessions.
      if (requestSince !== "") argv.push("--since", requestSince)
      if (requestUntil !== "") argv.push("--until", requestUntil)
      return argv
    }
    stdout: StdioCollector { id: sessionsOut; waitForEnd: true }
    onExited: function() {
      root.handleSessions(
        sessionsOut.text, requestPeriod, requestGap, requestEpoch)
      if (queued || requestGap !== root.gapMinutes
          || requestPeriod !== root.period
          || requestEpoch !== root.refreshEpoch) {
        Qt.callLater(function() { root.refreshSessions() })
      }
    }
  }

  Process {
    id: profileProc
    property string requestPeriod: "day"
    property string requestSince: ""
    property string requestUntil: ""
    property real requestGap: 0
    property int requestEpoch: 0
    property bool queued: false
    // --bucket is left at its default `auto`, so charts §2.4's ladder is applied
    // server-side and bucket_s reports what it chose.
    command: {
      var argv = [root.statsPath, "profile", "--gap", String(requestGap)]
      if (requestSince !== "") argv.push("--since", requestSince)
      if (requestUntil !== "") argv.push("--until", requestUntil)
      return argv
    }
    stdout: StdioCollector { id: profileOut; waitForEnd: true }
    onExited: function() {
      root.handleProfile(
        profileOut.text, requestPeriod, requestGap, requestEpoch)
      if (queued || requestGap !== root.gapMinutes
          || requestPeriod !== root.period
          || requestEpoch !== root.refreshEpoch) {
        Qt.callLater(function() { root.refreshProfile() })
      }
    }
  }

  Process {
    id: verifyProc
    property int requestEpoch: 0
    command: [root.statsPath, "verify"]
    stdout: StdioCollector { id: verifyOut; waitForEnd: true }
    onExited: function() {
      root.handleVerify(verifyOut.text, requestEpoch)
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

      Item {
        anchors.fill: parent
        anchors.topMargin: analysisCard.contentTopInset
        anchors.leftMargin: analysisCard.contentLeftInset
        anchors.rightMargin: analysisCard.contentRightInset
        anchors.bottomMargin: analysisCard.contentBottomInset

        // ------------------------------------------------------ title bar
        Item {
          id: titleBar
          anchors.top: parent.top
          anchors.left: parent.left
          anchors.right: parent.right
          implicitHeight: Math.max(windowTitle.implicitHeight,
                                   closeButton.implicitHeight)

          Text {
            id: windowTitle
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: "WalkingPad · History"
            color: root.foreground
            font.family: root.fontFamily
            font.pixelSize: Style.font.heading
            font.bold: true
          }

          Button {
            id: closeButton
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: "✕"
            fontSize: Style.font.body
            foreground: root.foreground
            accent: root.accent
            fontFamily: root.fontFamily
            onClicked: root.dismiss()
          }
        }

        Rectangle {
          id: titleRule
          anchors.top: titleBar.bottom
          anchors.topMargin: Style.spacing.md
          anchors.left: parent.left
          anchors.right: parent.right
          height: Math.max(1, Style.space(1))
          color: root.border
          opacity: 0.4
        }

        // ----------------------------------------------------- the left rail
        Item {
          id: rail
          anchors.top: titleRule.bottom
          anchors.topMargin: Style.spacing.panelGap
          anchors.left: parent.left
          anchors.bottom: parent.bottom
          width: Style.space(230)

          Column {
            id: periodColumn
            anchors.top: parent.top
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.rightMargin: Style.spacing.lg
            spacing: Style.spacing.xxs

            Repeater {
              model: root.periodOptions

              Rectangle {
                id: periodRow
                required property var modelData
                // Two different facts, deliberately shown differently. `scoping`
                // says which period the session list below is drawn from, and
                // stays true in session mode; `selected` says this is what the
                // right pane is showing, and charts §2.1 allows exactly one of
                // those at a time across the whole rail.
                readonly property bool scoping:
                  root.period === periodRow.modelData.key
                readonly property bool selected:
                  !root.sessionMode && periodRow.scoping

                width: periodColumn.width
                implicitHeight: periodText.implicitHeight
                  + Style.spacing.controlPaddingY * 2
                radius: Style.cornerRadius
                color: periodRow.selected
                  ? root.chip
                  : (periodMouse.containsMouse
                    ? Qt.rgba(root.chip.r, root.chip.g, root.chip.b,
                              root.chip.a * 0.35)
                    : "transparent")

                Text {
                  id: periodText
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: Style.spacing.controlPaddingX
                  anchors.rightMargin: Style.spacing.controlPaddingX
                  text: periodRow.modelData.label
                  color: periodRow.selected ? root.accent : root.foreground
                  opacity: periodRow.scoping ? 1.0 : 0.75
                  font.family: root.fontFamily
                  font.pixelSize: Style.font.body
                  font.bold: periodRow.scoping
                  elide: Text.ElideRight
                }

                MouseArea {
                  id: periodMouse
                  anchors.fill: parent
                  hoverEnabled: true
                  cursorShape: Qt.PointingHandCursor
                  onClicked: root.setPeriod(periodRow.modelData.key)
                }
              }
            }
          }

          PanelSectionHeader {
            id: sessionsHeader
            anchors.top: periodColumn.bottom
            anchors.topMargin: Style.spacing.panelGap
            anchors.left: parent.left
            anchors.leftMargin: Style.spacing.controlPaddingX
            text: "SESSIONS"
            foreground: root.foreground
            fontFamily: root.fontFamily
          }

          Flickable {
            id: sessionScroll
            anchors.top: sessionsHeader.bottom
            anchors.topMargin: Style.spacing.sm
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.rightMargin: Style.spacing.lg
            anchors.bottom: parent.bottom
            contentWidth: width
            contentHeight: sessionColumn.implicitHeight
            clip: true

            Column {
              id: sessionColumn
              width: parent.width
              spacing: Style.spacing.xxs

              SectionNote {
                visible: root.sessionsError !== ""
                text: root.sessionsError
              }

              SectionNote {
                visible: root.sessionsError === "" && root.sessions.length === 0
                text: "No walks in this period"
              }

              Repeater {
                model: root.sessions

                Rectangle {
                  id: sessionRow
                  required property var modelData
                  readonly property bool selected: root.sessionMode
                    && Number(sessionRow.modelData.start)
                      === Number(root.selectedSessionStart)

                  width: sessionColumn.width
                  implicitHeight: sessionLabels.implicitHeight
                    + Style.spacing.controlPaddingY * 2
                  radius: Style.cornerRadius
                  color: sessionRow.selected
                    ? root.chip
                    : (sessionMouse.containsMouse
                      ? Qt.rgba(root.chip.r, root.chip.g, root.chip.b,
                                root.chip.a * 0.35)
                      : "transparent")

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
                      text: root.railLabel(sessionRow.modelData)
                      color: sessionRow.selected ? root.accent : root.foreground
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.bodySmall
                      font.bold: sessionRow.selected
                      elide: Text.ElideRight
                    }

                    Text {
                      width: parent.width
                      visible: root.numeric(
                        sessionRow.modelData.unobserved_s) > 0
                      text: "partly reconstructed"
                      color: sessionRow.selected ? root.accent : root.foreground
                      opacity: 0.65
                      font.family: root.fontFamily
                      font.pixelSize: Style.font.caption
                      elide: Text.ElideRight
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
        }

        Rectangle {
          id: railRule
          anchors.left: rail.right
          anchors.top: titleRule.bottom
          anchors.bottom: parent.bottom
          width: Math.max(1, Style.space(1))
          color: root.border
          opacity: 0.4
        }

        // ---------------------------------------------------- the right pane
        Flickable {
          anchors.left: railRule.right
          anchors.leftMargin: Style.spacing.panelGap
          anchors.right: parent.right
          anchors.top: titleRule.bottom
          anchors.topMargin: Style.spacing.panelGap
          anchors.bottom: parent.bottom
          contentWidth: width
          contentHeight: paneContent.implicitHeight
          clip: true

          Column {
            id: paneContent
            width: parent.width
            spacing: Style.spacing.panelGap

            Column {
              width: parent.width
              spacing: Style.spacing.labelGap

              Text {
                width: parent.width
                text: root.paneTitle
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: Style.font.display
                font.bold: true
                elide: Text.ElideRight
              }

              Text {
                width: parent.width
                visible: root.paneSubtitle !== ""
                text: root.paneSubtitle
                color: root.foreground
                opacity: 0.65
                font.family: root.fontFamily
                font.pixelSize: Style.font.bodySmall
                elide: Text.ElideRight
              }
            }

            // ---------- Written values, in both modes ----------
            Row {
              id: totalsRow
              width: parent.width
              spacing: Style.spacing.xxl
              readonly property real cellWidth: (width - spacing * 3) / 4

              TotalStat {
                width: totalsRow.cellWidth
                label: "Distance"
                value: root.formatDistance(root.paneTotals.dist_km)
              }
              TotalStat {
                width: totalsRow.cellWidth
                label: "Steps"
                value: root.formatSteps(root.paneTotals.steps)
              }
              TotalStat {
                width: totalsRow.cellWidth
                label: "Time"
                value: root.formatDuration(root.paneTotals.dur_s)
              }
              TotalStat {
                width: totalsRow.cellWidth
                label: "Average speed"
                value: root.formatAverageSpeed(root.paneTotals.v_avg)
              }
            }

            SectionNote {
              visible: root.unobservedNote !== ""
              text: root.unobservedNote
            }

            SectionNote {
              visible: !root.sessionMode && root.profileError !== ""
              text: root.profileError
            }

            SectionNote {
              visible: root.integrityNote !== ""
              text: root.integrityNote
            }

            // ---------- Period mode ----------
            Column {
              width: parent.width
              visible: !root.sessionMode
              spacing: Style.spacing.panelGap

              ChartFrame {
                width: parent.width
                title: "Steps per " + root.formatMinutes(root.periodBucketSeconds)
                note: root.profile.buckets.length === 0
                  ? "No measured walking in this period." : ""

                WalkingpadBarChart {
                  width: parent.width
                  visible: root.profile.buckets.length > 0
                  buckets: root.periodStepBuckets
                  bucketSeconds: root.periodBucketSeconds
                  rangeStart: root.periodRangeStart
                  rangeEnd: root.periodRangeEnd
                  fg: root.foreground
                  accent: root.accent
                  muted: root.muted
                  plotHeight: Style.space(110)
                  axisHeight: Style.space(16)
                  barSpacing: Style.space(2)
                  maxBarWidth: Style.space(44)
                  cornerRadius: Style.cornerRadius
                  lineWidth: Style.space(1)
                  fontFamily: root.fontFamily
                  labelFontSize: Style.font.caption
                  valueUnit: "steps"
                  guideCount: 1
                  axisStartLabel: root.formatClock(root.periodRangeStart)
                  axisEndLabel: root.formatClock(root.periodRangeEnd)
                }
              }

              ChartFrame {
                width: parent.width
                title: "Speed over time"
                note: root.periodSpeedPoints.length === 0
                  ? "No measured speed in this period." : ""

                WalkingpadTimeChart {
                  id: periodSpeedChart
                  width: parent.width
                  visible: root.periodSpeedPoints.length > 0
                  points: root.periodSpeedPoints
                  filled: true
                  zeroBased: true
                  minimumSpan: 1.0
                  // Fixed-width buckets: the derived median cannot tell a
                  // missing bucket from the normal spacing, so state the bound.
                  gapSeconds: root.periodBucketSeconds * 1.5
                  rangeStart: root.periodRangeStart
                  rangeEnd: root.periodRangeEnd
                  fg: root.foreground
                  accent: root.accent
                  muted: root.muted
                  plotHeight: Style.space(110)
                  axisHeight: Style.space(16)
                  lineWidth: Style.space(1)
                  fontFamily: root.fontFamily
                  labelFontSize: Style.font.caption
                  guideCount: 1
                  minimumLabel: root.rangeLabel(
                    periodSpeedChart.valueRange, false, 1, " km/h")
                  maximumLabel: root.rangeLabel(
                    periodSpeedChart.valueRange, true, 1, " km/h")
                  axisStartLabel: root.formatClock(root.periodRangeStart)
                  axisEndLabel: root.formatClock(root.periodRangeEnd)
                }
              }

              ChartFrame {
                width: parent.width
                title: "Time at speed"
                note: root.speedHistogram.length === 0
                  ? "No measured running seconds in this period." : ""

                Column {
                  width: parent.width
                  spacing: Style.spacing.xs

                  Repeater {
                    model: root.speedHistogram

                    // Horizontal bars, and rectangles again for the same reason
                    // (charts §3.1). A time axis would be meaningless here: the
                    // axis is speed, and the length is how long it was held.
                    Item {
                      id: histogramRow
                      required property var modelData
                      width: parent.width
                      implicitHeight: Math.max(histogramLabel.implicitHeight,
                                               Style.space(14))

                      Text {
                        id: histogramLabel
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        width: Style.space(58)
                        text: root.numeric(histogramRow.modelData.kmh).toFixed(1)
                          + " km/h"
                        color: root.muted
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                        horizontalAlignment: Text.AlignRight
                      }

                      Rectangle {
                        id: histogramBar
                        anchors.left: histogramLabel.right
                        anchors.leftMargin: Style.spacing.lg
                        anchors.verticalCenter: parent.verticalCenter
                        height: Style.space(10)
                        width: root.histogramPeak > 0
                          ? Math.max(1, (histogramRow.width - histogramLabel.width
                              - histogramDuration.width - Style.spacing.lg * 2)
                              * root.numeric(histogramRow.modelData.dur_s)
                              / root.histogramPeak)
                          : 0
                        radius: Style.cornerRadius
                        color: Qt.rgba(root.foreground.r, root.foreground.g,
                                       root.foreground.b, 0.8)

                        Behavior on width { NumberAnimation { duration: 180 } }
                      }

                      Text {
                        id: histogramDuration
                        anchors.left: histogramBar.right
                        anchors.leftMargin: Style.spacing.lg
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.formatDuration(histogramRow.modelData.dur_s)
                        color: root.muted
                        font.family: root.fontFamily
                        font.pixelSize: Style.font.caption
                      }
                    }
                  }
                }
              }
            }

            // ---------- Session mode ----------
            Column {
              width: parent.width
              visible: root.sessionMode
              spacing: Style.spacing.panelGap

              SectionNote {
                visible: root.detailError !== ""
                text: root.detailError
              }

              SectionNote {
                visible: !root.detailLoaded && root.detailError === ""
                text: "Loading the selected session…"
              }

              SectionNote {
                // A wholly reconstructed session has exact counter totals but no
                // samples. An empty chart would imply a rendering failure, so
                // say why there is no curve instead of mounting one.
                visible: root.detailLoaded && root.detailSamples.length === 0
                text: "No samples were recorded for this session — every bout was "
                  + "reconstructed from the pad's own counters, not measured, so "
                  + "there is no curve to draw."
              }

              ChartFrame {
                width: parent.width
                visible: root.sessionDrawable
                title: "Speed"
                note: ""

                WalkingpadTimeChart {
                  id: sessionSpeedChart
                  width: parent.width
                  points: root.sessionSpeedPoints
                  // Fact 2: reported speed is ACTUAL and the pad ramps about
                  // 0.25 km/h per second with no ACK anywhere. The gap between
                  // commanded and actual IS that ramp, and splitting them into
                  // separate rows would hide the one thing worth seeing.
                  overlays: [
                    { points: root.sessionCommandedPoints,
                      role: "accent", dashed: false },
                    { points: root.sessionGoalPoints,
                      role: "muted", dashed: true }
                  ]
                  filled: true
                  zeroBased: true
                  minimumSpan: 1.0
                  rangeStart: root.sessionRangeStart
                  rangeEnd: root.sessionRangeEnd
                  fg: root.foreground
                  accent: root.accent
                  muted: root.muted
                  plotHeight: Style.space(140)
                  axisHeight: Style.space(16)
                  lineWidth: Style.space(1)
                  dashLength: Style.space(4)
                  dashSpacing: Style.space(3)
                  fontFamily: root.fontFamily
                  labelFontSize: Style.font.caption
                  guideCount: 2
                  minimumLabel: root.rangeLabel(
                    sessionSpeedChart.valueRange, false, 1, " km/h")
                  maximumLabel: root.rangeLabel(
                    sessionSpeedChart.valueRange, true, 1, " km/h")
                  axisStartLabel: root.formatClock(root.sessionRangeStart)
                  axisEndLabel: root.formatClock(root.sessionRangeEnd)
                }
              }

              ChartFrame {
                width: parent.width
                visible: root.sessionDrawable
                title: "Cadence"
                note: ""

                WalkingpadTimeChart {
                  id: cadenceChart
                  width: parent.width
                  points: root.sessionCadencePoints
                  filled: false
                  minimumSpan: 10
                  rangeStart: root.sessionRangeStart
                  rangeEnd: root.sessionRangeEnd
                  fg: root.foreground
                  accent: root.accent
                  muted: root.muted
                  plotHeight: Style.space(90)
                  axisHeight: Style.space(16)
                  lineWidth: Style.space(1)
                  fontFamily: root.fontFamily
                  labelFontSize: Style.font.caption
                  guideCount: 1
                  minimumLabel: root.rangeLabel(
                    cadenceChart.valueRange, false, 0, " spm")
                  maximumLabel: root.rangeLabel(
                    cadenceChart.valueRange, true, 0, " spm")
                  axisStartLabel: root.formatClock(root.sessionRangeStart)
                  axisEndLabel: root.formatClock(root.sessionRangeEnd)
                }
              }

              ChartFrame {
                width: parent.width
                visible: root.sessionDrawable
                title: "Steps per minute"
                note: ""

                WalkingpadBarChart {
                  width: parent.width
                  // The same component and the same derivation as the popup, so
                  // the two read as one system (charts §2.3).
                  buckets: root.sessionStepBuckets
                  bucketSeconds: 60
                  rangeStart: Model.bucketStart(root.sessionRangeStart, 60)
                  rangeEnd: Model.bucketStart(root.sessionRangeEnd, 60) + 60
                  fg: root.foreground
                  accent: root.accent
                  muted: root.muted
                  plotHeight: Style.space(90)
                  axisHeight: Style.space(16)
                  barSpacing: Style.space(2)
                  maxBarWidth: Style.space(44)
                  cornerRadius: Style.cornerRadius
                  lineWidth: Style.space(1)
                  fontFamily: root.fontFamily
                  labelFontSize: Style.font.caption
                  valueUnit: "steps"
                  axisStartLabel: root.formatClock(root.sessionRangeStart)
                  axisEndLabel: root.formatClock(root.sessionRangeEnd)
                }
              }
            }
          }
        }
      }
    }
  }

  // Charts §3.3: real padding, a title above the plot, and no box around it.
  component ChartFrame: Column {
    id: frame
    property string title: ""
    property string note: ""
    spacing: Style.spacing.md

    Text {
      width: frame.width
      text: frame.title
      color: root.foreground
      opacity: 0.75
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      font.bold: true
      elide: Text.ElideRight
    }

    SectionNote {
      visible: frame.note !== ""
      text: frame.note
    }
  }

  component TotalStat: Column {
    id: stat
    property string label: ""
    property string value: ""
    spacing: Style.spacing.labelGap

    Text {
      width: stat.width
      text: stat.label
      color: root.foreground
      opacity: 0.65
      font.family: root.fontFamily
      font.pixelSize: Style.font.bodySmall
      elide: Text.ElideRight
    }

    Text {
      width: stat.width
      text: stat.value
      color: root.foreground
      font.family: root.fontFamily
      font.pixelSize: Style.font.title
      font.bold: true
      elide: Text.ElideRight
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
