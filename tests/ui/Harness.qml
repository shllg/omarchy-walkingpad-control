import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import qs.Commons

Item {
  id: harness
  readonly property string target: Quickshell.env("WALKINGPAD_HARNESS_TARGET")
  readonly property string repo: Quickshell.env("WALKINGPAD_HARNESS_REPO")
  readonly property bool checking: Quickshell.env("WALKINGPAD_HARNESS_CHECK") === "1"
  readonly property bool useFake: Quickshell.env("WALKINGPAD_HARNESS_FAKE") === "1"
  readonly property string screenName: Quickshell.env("WALKINGPAD_HARNESS_SCREEN")
  readonly property var chosenScreen: screenName === "" ? (Quickshell.screens[0] || null)
    : Quickshell.screens.find(function(screen) { return screen.name === harness.screenName })
  property var production: null
  property var surface: null
  property var focusItem: null
  property var input: null
  property var heldVerify: null
  property var originalVerifyCommand: null
  property bool ready: false

  // These are the only service methods reachable from either entry point.
  // Every motion intent terminates in this array; there is no Process or BLE
  // implementation behind it and WalkingpadService.qml is never loaded.
  QtObject {
    id: fakeService
    property string linkState: "connected"
    property string beltState: "running"
    property bool hasStatus: true
    property bool ramping: false
    property real currentSpeed: 2.5
    property string mode: "manual"
    property real maxSpeed: 6.0
    property real minGoal: 0.5
    property real speedStep: 0.5
    property real startSpeed: 2.0
    property real gapMinutes: 5
    property real distanceKm: 1.35
    property int steps: 1782
    property int elapsedSec: 1840
    property var goalSpeed: 2.5
    property var targetSpeed: 2.5
    property string lastErrorCode: ""
    property string lastErrorMessage: ""
    property var samples: []
    property var commands: []
    function record(command) { commands = commands.concat([command]) }
    function clearError() { lastErrorCode = ""; lastErrorMessage = "" }
    function requestAttention() {}
    function clampGoalAbsolute(value) { return Math.max(minGoal, Math.min(maxSpeed, value)) }
    function setExactGoal(value) { record({ cmd: "goal", kmh: value }) }
    function start() { record({ cmd: "start" }) }
    function stop() { record({ cmd: "stop" }) }
    function speedUp() { record({ cmd: "up" }) }
    function speedDown() { record({ cmd: "down" }) }
    Component.onCompleted: {
      var rows = [], now = Date.now() / 1000
      for (var i = 0; i <= 600; ++i) {
        rows.push({ t: now - 600 + i, e: 0, v: 2.5,
          s: Math.floor(i * 1.1), d: i / 1500 })
      }
      samples = rows
    }
  }

  QtObject {
    id: fakeShell
    function serviceFor(id) { return harness.useFake ? fakeService : null }
    function summon(id, payload) {}
    function hide(id) {}
  }

  QtObject {
    id: fakeBar
    property var shell: fakeShell
    property color foreground: Color.popups.text
    property color barForeground: Color.bar.text
    property color urgent: Color.urgent
    property string fontFamily: Style.font.family
    property bool vertical: false
    property int barSize: Style.bar.sizeHorizontal
    property string position: "top"
    property bool foregroundAnimationEnabled: false
    property var activePopout: null
    property var clickTargets: []
    function hideTooltip(item) {}
    function showTooltip(item, text) {}
    function requestPopout(item) { activePopout = item }
    function releasePopout(item) { activePopout = null }
  }

  // Walk both trees: Item.children excludes PanelWindow, while .data retains
  // it. Visual descendants may have been reparented by a shell component.
  function descendants(start) {
    var queue = [start], seen = [], result = []
    while (queue.length > 0) {
      var item = queue.shift()
      if (!item || seen.indexOf(item) !== -1) continue
      seen.push(item)
      result.push(item)
      var lists = [item.data, item.children]
      for (var j = 0; j < lists.length; ++j) {
        var children = lists[j]
        if (!children || children.length === undefined) continue
        for (var k = 0; k < children.length; ++k) queue.push(children[k])
      }
      if (item.contentItem && item.contentItem.children !== undefined)
        queue.push(item.contentItem)
    }
    return result
  }

  function find(predicate) {
    var objects = descendants(production)
    for (var i = 0; i < objects.length; ++i) {
      if (predicate(objects[i])) return objects[i]
    }
    return null
  }

  function named(name) {
    var item = find(function(candidate) { return candidate.objectName === name })
    // Existing releases have no row objectName. Resolve the same production
    // delegate by its model identity so baseline runs fail on behavior.
    if (!item && name.indexOf("sessionRow_") === 0) {
      var index = Number(name.substring("sessionRow_".length))
      var session = production.sessions[index]
      if (session) item = find(function(candidate) {
        return candidate.modelData && candidate.modelData.start === session.start
          && candidate.activeFocusOnTab
      })
    }
    if (!item) throw new Error("Object not found: " + name)
    return item
  }

  function control(name) {
    if (name.indexOf("sessionRow_") === 0) return named(name)
    var exact = find(function(item) { return item.objectName === name })
    if (exact) return exact
    var item = find(function(candidate) {
      return typeof candidate.mapToItem === "function" && candidate.visible
        && candidate.width > 0 && candidate.height > 0
        && (candidate.Accessible.name === name
          || (candidate.text === name && typeof candidate.clicked === "function"))
    })
    if (!item) throw new Error("Visible control not found: " + name)
    return item
  }

  function fail(message) {
    console.error("harness: FAIL — " + message)
    Qt.quit()
  }

  function configure(item) {
    try {
      production = item
      if (screenName !== "" && !chosenScreen)
        throw new Error("Requested screen is unavailable: " + screenName)
      if (target === "panel") item.bar = fakeBar
      else {
        item.manifest = ({ id: "io.github.shllg.walkingpad",
          barWidget: { defaults: { gapMinutes: 5 } } })
        item.shell = fakeShell
        item.service = useFake ? fakeService : null
      }
      surface = find(function(candidate) {
        return candidate.screen !== undefined && candidate.visible !== undefined
          && (candidate.focusTarget !== undefined || candidate.contentItem !== undefined)
      })
      if (!surface) throw new Error("Production PanelWindow was not instantiated")
      surface.screen = Qt.binding(function() { return harness.chosenScreen })
      // KeyboardPanel.contentItem is a list alias, so focusTarget is the real
      // Item to use there. The analysis PanelWindow keeps its ordinary Item.
      focusItem = target === "panel" ? surface.focusTarget : surface.contentItem
      if (checking) {
        var component = Qt.createComponent(Qt.resolvedUrl("Input.qml"))
        if (component.status !== Component.Ready) throw new Error(component.errorString())
        input = component.createObject(focusItem)
        if (!input) throw new Error("Qt input helper failed to instantiate")
      }
      item.open()
      if (target === "window") {
        item.setPeriod(Quickshell.env("WALKINGPAD_HARNESS_PERIOD"))
        if (Quickshell.env("WALKINGPAD_HARNESS_MODE") === "session") sessionWait.start()
      }
      ready = true
    } catch (error) { fail(error.message) }
  }

  Timer {
    id: sessionWait
    interval: 100
    repeat: true
    property int attempts: 0
    onTriggered: {
      if (harness.production.sessions.length > 0) {
        stop()
        harness.production.selectSession(harness.production.sessions[0])
      } else if (++attempts >= 100) {
        stop()
        harness.fail("No session arrived to select")
      }
    }
  }

  PanelWindow {
    visible: harness.target === "panel"
    screen: harness.chosenScreen || null
    anchors { top: true; left: true; right: true }
    implicitHeight: Style.bar.sizeHorizontal
    exclusionMode: ExclusionMode.Ignore
    color: Color.bar.background
    WlrLayershell.namespace: "walkingpad-harness-bar"
    Loader {
      active: harness.target === "panel"
      width: Style.bar.iconSlot
      height: parent.height
      Component.onCompleted: if (active) {
        setSource("file://" + harness.repo + "/WalkingpadPanel.qml", { bar: fakeBar })
      }
      onLoaded: harness.configure(item)
      onStatusChanged: if (status === Loader.Error) harness.fail("Panel entry point failed to load")
    }
  }

  Loader {
    active: harness.target === "window"
    source: active ? "file://" + harness.repo + "/WalkingpadWindow.qml" : ""
    onLoaded: harness.configure(item)
    onStatusChanged: if (status === Loader.Error) harness.fail("Window entry point failed to load")
  }

  function inspect() {
    if (!ready) return { ready: false }
    var window = focusItem.Window.window
    if (!window) return { ready: false }
    var active = window.activeFocusItem
    var focusName = active ? (active.objectName || active.Accessible.name
      || active.toString()) : ""
    var state = { ready: true, opened: production.opened, focus: focusName,
      commands: fakeService.commands }
    if (target === "panel") {
      state.editing = production.editing
      state.editorFocused = active !== null && typeof active.selectAll === "function"
      state.cursorZone = production.cursorZone
      state.chartBuckets = production.chartBuckets.length
      state.chartCursorIndex = production.chartCursorIndex
    } else {
      var scroll = find(function(item) { return item.objectName === "sessionList" })
      if (!scroll && production.sessions.length > 0) {
        var ancestor = named("sessionRow_0").parent
        while (ancestor && ancestor.contentY === undefined) ancestor = ancestor.parent
        scroll = ancestor
      }
      var pane = find(function(item) { return item.objectName === "historyPane" })
      var note = production.integrityNote
      var label = find(function(item) { return item.visible && item.text === note && note !== "" })
      state.period = production.period
      state.epoch = production.refreshEpoch
      state.refreshAvailable = typeof production.refreshHistory === "function"
      state.loading = production.historyLoading !== undefined ? production.historyLoading
        : production.profileLoading || production.sessionsLoading
      state.detailLoaded = production.detailLoaded
      state.profileSteps = production.profile.totals.steps
      state.detailSteps = production.detailSession ? production.detailSession.steps : null
      state.detailSampleCount = production.detailSamples.length
      state.sessionSteps = production.sessions.map(function(session) { return session.steps })
      state.selectedSessionStart = production.selectedSessionStart
      state.sessionCount = production.sessions.length
      state.sessionStarts = production.sessions.map(function(session) { return session.start })
      state.sessionScroll = scroll ? scroll.contentY : 0
      state.paneScroll = pane ? pane.contentY : 0
      state.integrityNote = note
      state.noticeVisible = label !== null
      state.focusedRowVisible = false
      if (active) {
        for (var i = 0; i < production.sessions.length; ++i) {
          if (active === named("sessionRow_" + i)) state.focus = "sessionRow_" + i
        }
      }
      if (scroll && active && state.focus.indexOf("sessionRow_") === 0) {
        var point = active.mapToItem(scroll, 0, 0)
        state.focusedRowVisible = point.y >= -1 && point.y + active.height <= scroll.height + 1
      }
      var verify = verifyProcess()
      state.verifyRunning = verify.running
      state.verifyQueued = verify.queued === undefined ? false : verify.queued
      state.verifyEpoch = verify.requestEpoch
      state.verifySentinel = production.verifyResult !== null
        && production.verifyResult.harnessSentinel === true
    }
    return state
  }

  function verifyProcess() {
    var process = find(function(item) {
      return item.command && item.command[0] === production.statsPath
        && item.command[1] === "verify"
    })
    if (process) return process
    if (heldVerify) return heldVerify
    throw new Error("History verify Process was not found")
  }

  function holdVerify() {
    var process = verifyProcess()
    if (process.running) throw new Error("Cannot hold a running query")
    heldVerify = process
    originalVerifyCommand = Array.from(process.command)
    process.stdinEnabled = true
    // Delay only this test-owned query; releasing it executes the real stats
    // command with its real arguments and synthetic-store environment.
    process.command = ["/usr/bin/python3", "-c",
      "import os,sys; sys.stdin.readline(); os.execv(sys.argv[1], sys.argv[1:])"]
      .concat(originalVerifyCommand)
    production.verifyResult = { harnessSentinel: true }
  }

  function releaseVerify() {
    if (!heldVerify || !heldVerify.running) throw new Error("No held query is running")
    heldVerify.command = originalVerifyCommand
    heldVerify.write("\n")
  }

  function notice(payload) {
    var item = production
    var profile = JSON.parse(JSON.stringify(item.profile))
    profile.skipped_rows = payload.profileSkipped || 0
    item.handleProfile(JSON.stringify(profile), item.period, item.gapMinutes, item.refreshEpoch)
    item.handleSessions(JSON.stringify({ sessions: item.sessions,
      skipped_rows: payload.sessionsSkipped || 0 }), item.period, item.gapMinutes, item.refreshEpoch)
    if (item.selectedSessionStart !== null) {
      item.handleSessionDetail(JSON.stringify({ session: item.detailSession,
        samples: item.detailSamples, skipped_rows: payload.detailSkipped || 0 }),
        item.selectedSessionStart, item.gapMinutes, item.refreshEpoch)
    }
    item.handleVerify(JSON.stringify({ missing_steps: payload.missing_steps || 0,
      missing_dur_s: payload.missing_dur_s || 0, days_mismatched: payload.days_mismatched || 0,
      days_unchecked: payload.days_unchecked || 0, skipped_rows: payload.skipped_rows || 0 }),
      item.refreshEpoch)
  }

  IpcHandler {
    enabled: harness.checking
    target: "walkingpadHarness"
    function dispatch(action: string, json: string): string {
      try {
        var payload = JSON.parse(json), value = null
        if (action === "inspect") value = harness.inspect()
        else if (!harness.ready) throw new Error("Harness is not ready")
        else if (action === "click") {
          if (!harness.input.click(harness.control(payload))) throw new Error("Click target is not shown")
        } else if (action === "key") {
          if (!harness.input.key(payload)) throw new Error("Key target is not shown")
        } else if (action === "type") {
          if (!harness.input.typeText(payload)) throw new Error("Text field is not shown")
        } else if (action === "focus") harness.named(payload).forceActiveFocus(Qt.TabFocusReason)
        else if (action === "controlCursor") {
          harness.production.cursorZone = "controls"
          harness.production.cursorIndex = payload
          harness.focusItem.forceActiveFocus()
        } else if (action === "compact") {
          var card = harness.named("analysisCard")
          card.height = Math.min(card.height, Style.space(500))
        } else if (action === "scroll" || action === "scrollSessions") {
          var pane = harness.named(action === "scroll" ? "historyPane" : "sessionList")
          pane.contentY = Math.min(payload, Math.max(0, pane.contentHeight - pane.height))
        } else if (action === "notice") harness.notice(payload)
        else if (action === "holdVerify") harness.holdVerify()
        else if (action === "releaseVerify") harness.releaseVerify()
        else throw new Error("Unknown check action: " + action)
        return JSON.stringify({ ok: true, value: value })
      } catch (error) { return JSON.stringify({ ok: false, error: error.message }) }
    }
  }
}
