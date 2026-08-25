.pragma library

// Pure presentation logic for WalkingpadPanel.qml. No colours and no sizes live
// here -- those come from qs.Commons at the call site (spec §5.5).

// Nerd Font glyphs, by codepoint rather than as literals: these all sit in the
// Supplementary Private Use Area, where a stray editor or transport re-encoding
// silently truncates them into a different, valid-looking character. Every one
// was checked against JetBrainsMono Nerd Font with fc-list :charset=.
var ICON_WALK = String.fromCodePoint(0xF0583)    // nf-md-walk
var ICON_RUN = String.fromCodePoint(0xF070E)     // nf-md-run
var ICON_START = String.fromCodePoint(0xF040A)   // nf-md-play
var ICON_STOP = String.fromCodePoint(0xF04DB)    // nf-md-stop
var ICON_MINUS = String.fromCodePoint(0xF0376)   // nf-md-minus
var ICON_PLUS = String.fromCodePoint(0xF0415)    // nf-md-plus

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
