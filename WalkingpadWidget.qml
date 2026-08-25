import QtQuick
import qs.Commons
import qs.Ui

// Read-only until QML is exercised on hardware; this widget cannot move the belt.
BarWidget {
  id: root
  moduleName: "io.github.shllg.walkingpad"

  readonly property var service: bar?.shell?.serviceFor("io.github.shllg.walkingpad")
  readonly property string displayText: statusText()

  function commandedText() {
    if (service.commandedSpeed === null || service.commandedSpeed === undefined)
      return "no commanded speed"
    return "commanded " + Number(service.commandedSpeed).toFixed(1) + " km/h"
  }

  function elapsedText() {
    var total = Math.max(0, Number(service.elapsedSec))
    var hours = Math.floor(total / 3600)
    var minutes = Math.floor((total % 3600) / 60)
    var seconds = Math.floor(total % 60)
    if (hours > 0)
      return hours + ":" + String(minutes).padStart(2, "0") + ":" + String(seconds).padStart(2, "0")
    return minutes + ":" + String(seconds).padStart(2, "0")
  }

  function sessionText() {
    return Number(service.distanceKm).toFixed(2) + " km · "
      + service.steps + " steps · " + elapsedText()
  }

  function statusText() {
    if (!service) return "WalkingPad service unavailable"

    if (service.linkState === "scanning") return "Looking for WalkingPad"
    if (service.linkState === "absent") return "WalkingPad asleep"
    if (service.linkState === "unavailable") return "WalkingPad in use by the phone app"
    if (service.linkState === "lost") return "WalkingPad connection lost"
    if (service.linkState === "error")
      return service.linkMessage || "WalkingPad bridge error"
    if (service.linkState !== "connected") return "WalkingPad status unavailable"

    if (service.mode === "standby" || service.lastErrorCode === "standby")
      return "In standby — press M on the panel to control it."
    if (service.lastErrorMessage) return service.lastErrorMessage
    if (service.lastErrorCode) return "WalkingPad: " + service.lastErrorCode
    if (!service.hasStatus) return "WalkingPad connected — waiting for status"

    var actual = Number(service.currentSpeed).toFixed(1) + " km/h actual"
    if (service.beltState === "running")
      return "WalkingPad " + actual + " · " + commandedText()
    if (service.beltState === "starting")
      return "WalkingPad starting · " + actual + " · " + commandedText()
    if (service.beltState === "idle")
      return "WalkingPad idle · " + sessionText() + " · " + commandedText()
    if (service.beltState === "stopped")
      return "WalkingPad stopped · " + sessionText() + " · " + commandedText()
    return "WalkingPad state unknown · " + actual + " · " + commandedText()
  }

  implicitWidth: statusLabel.implicitWidth + Style.space(14)
  implicitHeight: barSize

  Text {
    id: statusLabel
    anchors.centerIn: parent
    text: root.displayText

    // Absent, unavailable, and standby are normal or instructional states,
    // so they stay on the bar's adaptive foreground instead of an urgent role.
    color: root.bar.barForeground
    font.family: root.bar.fontFamily
    font.pixelSize: Style.font.body
  }
}
