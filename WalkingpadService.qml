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
  readonly property string lastErrorCode: state.lastErrorCode
  readonly property string lastErrorMessage: state.lastErrorMessage

  function handleStatus(event) {
    // A status frame is the bridge's proof of a working link; BlueZ connection
    // state alone is not sufficient on this pad.
    state.linkState = "connected"
    state.linkMessage = ""
    state.hasStatus = true
    state.beltState = String(event.belt)
    state.currentSpeed = Number(event.speed)
    state.commandedSpeed = event.target === null ? null : Number(event.target)
    state.padSetSpeed = Number(event.pad_set_speed)
    state.mode = String(event.mode)
    state.elapsedSec = Number(event.time)
    state.distanceKm = Number(event.dist_km)
    state.steps = Number(event.steps)
    state.lastErrorCode = ""
    state.lastErrorMessage = ""
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
    state.lastErrorCode = ""
    state.lastErrorMessage = ""
  }

  function handleError(event) {
    state.lastErrorCode = String(event.code || "")
    state.lastErrorMessage = String(event.msg || "")
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
    property real padSetSpeed: 0
    property string mode: "unknown"
    property int elapsedSec: 0
    property real distanceKm: 0
    property int steps: 0
    property string lastErrorCode: ""
    property string lastErrorMessage: ""
  }

  Process {
    id: bridgeProc
    command: [root.bridgePath]
    running: true

    // The bridge treats stdin EOF as its parent exiting, even in this
    // read-only slice where the service never writes a command.
    stdinEnabled: true

    stdout: SplitParser {
      onRead: function(line) { root.handleLine(line) }
    }

    onExited: function(exitCode, exitStatus) {
      state.linkState = "error"
      state.linkMessage = "The bridge exited with code " + exitCode
      state.hasStatus = false
    }
  }
}
