import QtQuick
import Quickshell.Io

Item {
  id: root

  function urlToLocalPath(url) {
    var value = url.toString()
    if (value.indexOf("file://") === 0) return decodeURIComponent(value.substring(7))
    return value
  }

  readonly property string bridgePath: urlToLocalPath(Qt.resolvedUrl("bin/walkingpad-bridge"))

  // Safety limits are the BRIDGE's, not these. These only bound what this
  // service asks for; every command is clamped again in Python, which is where
  // the tested policy lives. See docs/design-spec.md D-5.
  property real maxSpeed: 6.0
  property real speedStep: 0.5
  property real startSpeed: 2.0
  // Mirrors Safety.MIN_GOAL_KMH so the number on screen is the number the
  // bridge will accept. The bridge clamps again regardless -- this exists so
  // the UI never shows a goal that is quietly going to be cut.
  property real minGoal: 0.5

  readonly property string linkState: state.linkState
  readonly property string linkMessage: state.linkMessage
  readonly property bool hasStatus: state.hasStatus
  readonly property string beltState: state.beltState
  readonly property real currentSpeed: state.currentSpeed
  readonly property var commandedSpeed: state.commandedSpeed
  readonly property real padSetSpeed: state.padSetSpeed
  readonly property string mode: state.mode
  readonly property int elapsedSec: state.elapsedSec
  readonly property real distanceKm: state.distanceKm
  readonly property int steps: state.steps
  readonly property var samples: state.samples
  // Stage 3 §7 and ADR D-5 make this a read-time presentation preference: the
  // popup forwards it when settings exist, while this working default keeps
  // grouping defined when no widget exists. It is never sent to the bridge.
  //
  // Only the ANALYSIS WINDOW consumes it now. The popup used to slice this
  // buffer into "the current session" for its chart; charts §1 replaced that
  // with 60 fixed wall-clock minutes over the last hour, because a gap is the
  // information there and a session tail begins after the last gap by
  // definition. So the slice is gone rather than left computing at 1 Hz for a
  // property nothing reads.
  property real gapMinutes: 5
  readonly property string lastErrorCode: state.lastErrorCode
  readonly property string lastErrorMessage: state.lastErrorMessage

  // Optimistic target held only while the debounce below is in flight, so a
  // click is visibly acknowledged before any frame could confirm it.
  readonly property var pendingSpeed: state.pendingSpeed

  // The standing goal the bridge is walking the belt toward, and the local
  // optimistic copy held while a press is still settling.
  readonly property var goalSpeed: state.pendingGoal !== null
    ? state.pendingGoal : state.goalSpeed

  // What a UI should show as the target. A live goal outranks the intermediate
  // step the bridge is currently commanding: the goal is what the person asked
  // for, and commandedSpeed is just how far along the way the belt has been
  // told to go this second.
  readonly property var targetSpeed: root.goalSpeed !== null
    ? root.goalSpeed
    : (state.pendingSpeed !== null ? state.pendingSpeed : state.commandedSpeed)

  readonly property bool bridgeAlive: bridgeProc.running

  // Fact 8: only "running" means the belt is moving. Gate on that, never on
  // the absence of some other value.
  readonly property bool running: state.beltState === "running"

  // Fact 2: reported speed is ACTUAL and the pad ramps ~0.25 km/h per second,
  // with no ACK anywhere. Without this a click looks failed for two seconds.
  // The deadband is wider than it looks like it needs to be. The pad reports
  // speed in tenths, and a running belt hunts around its target by one tenth,
  // so a 0.05 threshold flipped this true/false roughly every other frame --
  // recorded on hardware 2026-08-25 as the label alternating Ramping/Running
  // fourteen times in one 60 s walk. Anything under a step and a half is the
  // pad holding its speed, not travelling to a new one.
  readonly property real rampDeadband: 0.15

  readonly property bool ramping: state.hasStatus
    && root.targetSpeed !== null
    && Math.abs(state.currentSpeed - Number(root.targetSpeed)) >= root.rampDeadband

  // -- commands -------------------------------------------------------------

  function send(obj) {
    if (!bridgeProc.running) {
      state.lastErrorCode = "no_bridge"
      state.lastErrorMessage = "The WalkingPad bridge is not running"
      return false
    }
    // A new action supersedes the last refusal, so a stale message never sits
    // under a button the user has since pressed again -- but not before it has
    // been readable. Reported from hardware 2026-08-25: refusals "flickered,
    // too fast to read", because pressing the button again is the natural
    // response to a button that did nothing, and that press cleared the very
    // message explaining why. The dwell timer holds it regardless.
    if (!errorDwell.running) {
      state.lastErrorCode = ""
      state.lastErrorMessage = ""
    }
    bridgeProc.write(JSON.stringify(obj) + "\n")
    return true
  }

  Timer {
    id: errorDwell
    interval: 4000
  }

  // Explicit dismissal, which outranks the dwell timer: the dwell exists to
  // stop an impatient second press from wiping the explanation, not to hold a
  // message against someone who has deliberately closed the panel.
  function clearError() {
    errorDwell.stop()
    state.lastErrorCode = ""
    state.lastErrorMessage = ""
  }

  // Fact 12: the pad ramps to ~3.5 km/h on START whatever speed it has stored.
  // The bridge owns the two-part sequence; passing kmh is what arms it.
  function start() {
    state.pendingSpeed = null
    state.pendingGoal = null
    goalDebounce.stop()
    speedDebounce.stop()
    send({ cmd: "start", kmh: root.startSpeed })
  }

  // Fact 16: the stop opcode is a TOGGLE. The bridge refuses it without recent
  // evidence of motion, and says so; this never second-guesses that here.
  // Fact 18: stop is never throttled, so it is never debounced either.
  function stop() {
    state.pendingSpeed = null
    state.pendingGoal = null
    goalDebounce.stop()
    speedDebounce.stop()
    send({ cmd: "stop" })
  }

  // One-shot speed, kept for callers that want a single command rather than a
  // standing intention. The buttons use setGoal().
  function setSpeed(kmh) {
    var value = Math.max(0, Math.min(root.maxSpeed, Number(kmh)))
    state.pendingGoal = null
    state.pendingSpeed = Math.round(value * 10) / 10
    // Spec §5.2: rapid clicks must not become rapid writes. One command goes
    // out for the settled value, never one per click.
    speedDebounce.restart()
  }

  // The window a goal may occupy right now, mirroring Safety.clamp_goal(). It
  // is published rather than kept inside clampGoal() so the panel can SHOW the
  // bounds instead of quietly cutting a number someone typed. There is still
  // exactly one copy of the arithmetic: clampGoal() reads these, and the bridge
  // clamps again regardless -- these bound what the UI ASKS for, never what the
  // pad is allowed to receive (spec D-5).
  //
  // ⚠ Fixed since 2026-08-28. These used to follow the belt, holding the goal
  // within maxGoalLead of the ACTUAL speed. That bound was removed because it
  // silently ate presses the "+0.5" label had already promised -- three presses
  // from 2.0 km/h reached 3.0, not 3.5. Read Safety.clamp_goal() for what the
  // removal gives up. The rounding that used to live here went with it: there
  // is no longer any arithmetic to produce 1.7999999999999998.
  readonly property real goalHigh: root.maxSpeed
  readonly property real goalLow: root.minGoal

  function clampGoal(kmh) {
    var value = Math.max(root.goalLow, Math.min(root.goalHigh, Number(kmh)))
    return Math.round(value * 10) / 10
  }

  // Bounded by the pad's own range and nothing tighter, mirroring
  // Safety.clamp_goal_absolute(). The lead bound in clampGoal() above is aimed
  // at the REPEATING path -- a held +/- key; a value someone typed and
  // committed is one deliberate act naming a destination, and cutting it to
  // where the belt happens to be right now only makes them type it again.
  function clampGoalAbsolute(kmh) {
    var value = Math.max(root.minGoal, Math.min(root.maxSpeed, Number(kmh)))
    return Math.round(value * 10) / 10
  }

  function setGoal(kmh) {
    state.pendingSpeed = null
    state.pendingGoalAbsolute = false
    state.pendingGoal = clampGoal(kmh)
    goalDebounce.restart()
  }

  function setExactGoal(kmh) {
    state.pendingSpeed = null
    state.pendingGoalAbsolute = true
    state.pendingGoal = clampGoalAbsolute(kmh)
    goalDebounce.restart()
  }

  // Steps the GOAL, not the belt. Pressing down after up moves the same
  // standing intention rather than queueing a second action, which is the whole
  // point of a goal: the buttons stay live while the belt is still catching up.
  function speedBy(delta) {
    // Spec §5.2: the label says "+0.5", so N presses must mean N * 0.5. The
    // pending goal comes first because it is the only one of these that is
    // already up to date: goalSpeed arrives from the bridge a round trip later,
    // so two presses inside the 250 ms debounce would otherwise both step from
    // the same stale number and the second would be lost.
    var base = state.pendingGoal !== null
      ? Number(state.pendingGoal)
      : (root.goalSpeed !== null
          ? Number(root.goalSpeed)
          : (state.hasStatus ? state.currentSpeed : 0))
    setGoal(base + delta)
  }

  function speedUp() { speedBy(root.speedStep) }
  function speedDown() { speedBy(-root.speedStep) }

  Timer {
    id: goalDebounce
    interval: 250
    onTriggered: {
      if (state.pendingGoal === null) return
      root.send({ cmd: "goal", kmh: state.pendingGoal,
                  absolute: state.pendingGoalAbsolute })
    }
  }

  Timer {
    id: speedDebounce
    interval: 250
    onTriggered: {
      if (state.pendingSpeed === null) return
      root.send({ cmd: "speed", kmh: state.pendingSpeed })
      // Cleared once sent; from here the bridge's own target is the truth.
      state.pendingSpeed = null
    }
  }

  // -- bridge output --------------------------------------------------------

  function handleStatus(event) {
    // A status frame is the bridge's proof of a working link; BlueZ connection
    // state alone is not sufficient on this pad (fact 9).
    var speed = Number(event.speed)
    var commanded = event.target === null ? null : Number(event.target)
    var goal = (event.goal === null || event.goal === undefined)
      ? null : Number(event.goal)
    var padSet = Number(event.pad_set_speed)
    var frameMode = String(event.mode)
    var elapsed = Number(event.time)
    var distance = Number(event.dist_km)
    var frameSteps = Number(event.steps)
    var belt = String(event.belt)

    state.linkState = "connected"
    state.linkMessage = ""
    state.hasStatus = true
    state.currentSpeed = speed
    state.commandedSpeed = commanded
    state.goalSpeed = goal
    // The optimistic copy has done its job once the bridge is echoing a goal
    // and nothing further is queued to send.
    if (!goalDebounce.running) state.pendingGoal = null
    state.padSetSpeed = padSet
    state.mode = frameMode
    state.elapsedSec = elapsed
    state.distanceKm = distance
    state.steps = frameSteps
    // Assigned LAST, deliberately. beltState is the property consumers watch to
    // decide the belt is moving, and every other field in this frame has to be
    // in place before that signal goes out -- otherwise a handler reads the new
    // belt state beside the PREVIOUS frame's speed and mode.
    state.beltState = belt
    // NOTE: the last error is deliberately NOT cleared here. Status arrives at
    // 1 Hz, so clearing it per frame flashed every refusal for under a second
    // and made stop look like it had simply done nothing. A refusal stays up
    // until the user's next action clears it in send().

    var wall = Date.now() / 1000
    var active = belt === "starting" || belt === "running"
    var recordable
    // Design §2.2: recording every idle poll would keep consecutive gaps below
    // gapMinutes and silently merge separate walks. Record starting/running and
    // a 5 s tail so both ends of the measured speed ramp remain visible.
    if (active) {
      state.tailDeadline = 0
      recordable = true
    } else {
      if (state.wasActive) state.tailDeadline = wall + 5
      recordable = state.tailDeadline > 0 && wall <= state.tailDeadline
      if (!recordable) state.tailDeadline = 0
    }
    state.wasActive = active

    if (recordable) {
      var samples = state.samples.slice()
      samples.push({
        t: wall,
        e: 0,
        b: belt,
        m: frameMode,
        v: speed,
        c: commanded,
        g: goal,
        d: distance,
        s: frameSteps,
        pe: elapsed
      })
      if (samples.length > 3600) samples.shift()
      // QML observes the property assignment, not an in-place array mutation.
      state.samples = samples
    }
  }

  function handleLink(event) {
    var nextState = String(event.state || "")
    if (nextState !== "scanning"
        && nextState !== "connected"
        && nextState !== "absent"
        && nextState !== "unavailable"
        && nextState !== "lost"
        && nextState !== "error") return

    state.linkState = nextState
    state.linkMessage = String(event.msg || "")
    state.hasStatus = false
    state.pendingSpeed = null
    state.pendingGoal = null
    state.goalSpeed = null
    // Fact 3 makes link loss and sleep ordinary resting states, so design §5.7's
    // dormant view must retain its last curve. The read-time gap rule owns
    // session boundaries: a long absence starts a new session and a short blip
    // keeps one together, whereas an explicit wipe destroys both distinctions.
    state.wasActive = false
    state.tailDeadline = 0
    state.lastErrorCode = ""
    state.lastErrorMessage = ""
  }

  function handleError(event) {
    // Message before code. Each property signals its own change, so anything
    // watching onLastErrorCodeChanged reads whatever the OTHER property holds
    // at that instant -- and with the code assigned first that was the previous
    // message, or an empty one. Assigning the trigger last makes the pair
    // coherent for imperative readers; bindings settle either way.
    state.lastErrorMessage = String(event.msg || "")
    state.lastErrorCode = String(event.code || "")
    // Hold this one on screen even if the user immediately presses again.
    if (state.lastErrorCode !== "") errorDwell.restart()
    // The bridge gave up on a start it had armed, so drop the optimistic
    // target with it rather than showing a speed nothing is heading for.
    if (state.lastErrorCode === "start_unconfirmed") state.pendingSpeed = null
  }

  function handleLine(line) {
    var event
    try {
      event = JSON.parse(line)
    } catch (error) {
      state.linkState = "error"
      state.linkMessage = "The bridge sent invalid status output"
      state.hasStatus = false
      return
    }

    if (!event || typeof event.t !== "string") return
    if (event.t === "status") handleStatus(event)
    else if (event.t === "link") handleLink(event)
    else if (event.t === "error") handleError(event)
  }

  QtObject {
    id: state

    property string linkState: "scanning"
    property string linkMessage: ""
    property bool hasStatus: false
    property string beltState: "unknown"
    property real currentSpeed: 0
    property var commandedSpeed: null
    property var pendingSpeed: null
    property var goalSpeed: null
    property var pendingGoal: null
    property bool pendingGoalAbsolute: false
    property real padSetSpeed: 0
    property string mode: "unknown"
    property int elapsedSec: 0
    property real distanceKm: 0
    property int steps: 0
    property var samples: []
    property bool wasActive: false
    property real tailDeadline: 0
    property string lastErrorCode: ""
    property string lastErrorMessage: ""
    property int respawns: 0
  }

  Process {
    id: bridgeProc
    command: [root.bridgePath]
    running: true

    // The bridge writes commands and reads status over this pipe, and treats
    // EOF as its parent exiting.
    stdinEnabled: true

    // The bridge must not write __pycache__ into the installed plugin, or the
    // shell's own inotify watch reloads every plugin. That is enforced in
    // bin/walkingpad-bridge with sys.dont_write_bytecode, not with an
    // environment variable here: Process.environment is a QVariantHash and a
    // QML object literal binds to it as a QVariantMap, which qmllint rejects.

    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }

    onExited: function(exitCode, exitStatus) {
      state.hasStatus = false
      state.pendingSpeed = null
      // A new process has no memory of what the old one commanded, and it
      // certainly is not still walking the belt toward a goal.
      state.pendingGoal = null
      state.goalSpeed = null
      state.commandedSpeed = null
      // A bridge lifetime is not a session boundary. Stage 3 §1.4 deliberately
      // lets the gap rule re-merge crash fragments, and fact 3 says a respawn or
      // sleeping pad must not blank the dormant chart. The transition-local 5 s
      // tail still ends here because it cannot survive a link change.
      state.wasActive = false
      state.tailDeadline = 0

      // Bounded respawn. This is NOT the auto-retry fact 17 forbids: that rule
      // is about re-sending COMMANDS to the pad, and a fresh bridge sends none
      // -- it scans. Any half-finished start died with the old process. The
      // cap exists so a bridge that cannot run fails visibly instead of
      // respawning forever.
      if (state.respawns < 3) {
        state.respawns++
        state.linkState = "scanning"
        state.linkMessage = "Restarting the bridge"
        respawnWindow.restart()
        respawnDelay.restart()
        return
      }

      state.linkState = "error"
      state.linkMessage = "The bridge keeps exiting (last code " + exitCode + ")"
    }
  }

  // Successive failures only count as a run when they are close together, so a
  // bridge that dies once a day still gets restarted.
  Timer {
    id: respawnWindow
    interval: 60000
    onTriggered: state.respawns = 0
  }

  Timer {
    id: respawnDelay
    interval: 1000
    onTriggered: bridgeProc.running = true
  }
}
