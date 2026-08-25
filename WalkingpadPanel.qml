import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "WalkingpadModel.js" as Model

// Bar icon plus popup, the shape every first-party panel uses: one bar-widget
// entry point whose root is qs.Ui's Panel, which owns the open/close lifecycle.
Panel {
  id: root
  moduleName: "io.github.shllg.walkingpad"
  ipcTarget: "walkingpad"
  // manageIpc: false so this panel can own the single IpcHandler the target
  // permits -- needed for the motion methods below.
  manageIpc: false

  // The bar injects only bar/moduleName/settings into a bar-widget; the
  // service auto-injection in shell.qml applies to panel/overlay/menu kinds.
  // So the singleton is looked up by id, which is also what keeps ONE bridge
  // across however many monitors the bar is drawn on (fact 5: the pad accepts
  // exactly one BLE connection).
  readonly property var service: bar?.shell?.serviceFor("io.github.shllg.walkingpad")

  readonly property string linkState: service ? service.linkState : "error"
  readonly property string beltState: service ? service.beltState : "unknown"
  readonly property bool hasStatus: service ? service.hasStatus : false
  readonly property bool ramping: service ? service.ramping : false
  readonly property real currentSpeed: service ? service.currentSpeed : 0
  readonly property string mode: service ? service.mode : "unknown"
  readonly property real maxSpeed: service ? service.maxSpeed : 6.0
  readonly property real speedStep: service ? service.speedStep : 0.5

  readonly property string stateLabel: service
    ? Model.stateLabel(linkState, beltState, hasStatus, ramping)
    : "Service unavailable"

  readonly property string instruction: service
    ? Model.instruction(linkState, mode, service.lastErrorCode, service.lastErrorMessage)
    : "The WalkingPad service is not loaded."

  // A refusal is the one thing here that earns the urgent role. Everything
  // else -- asleep, standby, in use elsewhere -- is a normal state or an
  // instruction, and stays on the ordinary foreground (spec §5.5).
  // Written as an explicit comparison rather than `service && ...`: a missing
  // service makes that expression undefined, and QML warns on assigning
  // undefined to a bool.
  readonly property bool refusing: service !== null && service !== undefined
    && service.lastErrorCode !== ""
    && service.lastErrorCode !== "standby"

  property int cursorIndex: -1

  readonly property var actions: [
    { key: "start", label: "Start", icon: Model.ICON_START },
    { key: "stop", label: "Stop", icon: Model.ICON_STOP },
    { key: "down", label: "-" + root.speedStep.toFixed(1), icon: Model.ICON_MINUS },
    { key: "up", label: "+" + root.speedStep.toFixed(1), icon: Model.ICON_PLUS }
  ]

  function invoke(key) {
    if (!service) return
    // One command per user action (fact 17). Nothing here retries, and nothing
    // here second-guesses a refusal -- the bridge owns both.
    if (key === "start") service.start()
    else if (key === "stop") service.stop()
    else if (key === "down") service.speedDown()
    else if (key === "up") service.speedUp()
  }

  function activateCursor() {
    if (cursorIndex < 0 || cursorIndex >= actions.length) return
    invoke(actions[cursorIndex].key)
  }

  function moveCursor(delta) {
    if (cursorIndex < 0) { cursorIndex = 0; return }
    var next = cursorIndex + delta
    cursorIndex = Math.max(0, Math.min(actions.length - 1, next))
  }

  // Spec §7 and D-5: forwarded settings bound what this panel ASKS for. They
  // are not the safety limits -- the bridge clamps every command again against
  // its own, which is the copy that has tests.
  function applySettings() {
    if (!service) return
    service.maxSpeed = Number(setting("maxSpeed", 6.0))
    service.speedStep = Number(setting("speedStep", 0.5))
    service.startSpeed = Number(setting("startSpeed", 2.0))
  }

  onServiceChanged: applySettings()
  onSettingsChanged: applySettings()
  onOpenedChanged: if (opened) cursorIndex = -1

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  IpcHandler {
    target: "walkingpad"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }

    // Spec §5.1: Hyprland keybinds are one-liners against these, with no extra
    // binary -- `qs ipc call walkingpad speedUp`.
    function start(): void { root.invoke("start") }
    function stop(): void { root.invoke("stop") }
    function speedUp(): void { root.invoke("up") }
    function speedDown(): void { root.invoke("down") }
    function status(): string { return root.stateLabel }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: Model.barIcon(root.linkState, root.beltState)
    tooltipText: root.service
      ? Model.tooltip(root.linkState, root.beltState, root.hasStatus,
                      root.ramping, root.currentSpeed)
      : "WalkingPad service unavailable"

    // Fact 3: a sleeping pad is the normal resting state, so absence is dimmed
    // rather than coloured. Nothing about it is an error to announce.
    opacity: Model.barDimmed(root.linkState) ? 0.55 : 1.0
    Behavior on opacity { NumberAnimation { duration: 200 } }

    onPressed: function(b) {
      // Spec §6.2: right click always means stop, at every level of the UI.
      // The bridge still gates it on recent evidence of motion (fact 16), and
      // refuses out loud when that evidence is missing.
      if (b === Qt.RightButton) root.invoke("stop")
      // Spec §6.1: left click NEVER starts the belt. It only opens the panel.
      else root.toggle()
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(340))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx !== 0 ? dx : dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // ---------- Hero: glyph · title/state · speed ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight,
                                   heroLabels.implicitHeight,
                                   heroSpeed.implicitHeight)

          Text {
            id: heroIcon
            text: Model.barIcon(root.linkState, root.beltState)
            color: root.bar.foreground
            opacity: Model.barDimmed(root.linkState) ? 0.55 : 1.0
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter

            Behavior on color { ColorAnimation { duration: 200 } }
            Behavior on opacity { NumberAnimation { duration: 200 } }
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: heroSpeed.left
            anchors.rightMargin: Style.space(10)
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              text: "WalkingPad"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              text: root.stateLabel.toUpperCase()
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }

          Column {
            id: heroSpeed
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: 0

            Text {
              // Fact 2: this is the ACTUAL belt speed. The commanded number is
              // shown separately below, because the pad ramps toward it at
              // ~0.25 km/h per second and never acknowledges anything.
              text: root.hasStatus ? Model.speedText(root.currentSpeed) : "—"
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.displayLarge
              font.bold: true
              anchors.right: parent.right

              Behavior on color { ColorAnimation { duration: 200 } }
            }

            Text {
              text: "KM/H"
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              anchors.right: parent.right
            }
          }
        }

        // ---------- Speed against the ceiling ----------
        Item {
          width: parent.width
          implicitHeight: Style.space(8)
          visible: root.hasStatus

          Rectangle {
            id: speedTrack
            anchors.fill: parent
            radius: height / 2
            color: Qt.rgba(root.bar.foreground.r, root.bar.foreground.g,
                           root.bar.foreground.b, 0.12)
          }

          Rectangle {
            anchors.left: speedTrack.left
            anchors.verticalCenter: speedTrack.verticalCenter
            height: speedTrack.height
            radius: speedTrack.radius
            color: root.bar.foreground
            width: Math.max(
              root.currentSpeed > 0 ? speedTrack.height : 0,
              speedTrack.width * Math.max(0, Math.min(1, root.maxSpeed > 0
                ? root.currentSpeed / root.maxSpeed : 0)))

            Behavior on width { NumberAnimation { duration: 320; easing.type: Easing.OutCubic } }
            Behavior on color { ColorAnimation { duration: 220 } }

            // Fact 2 again: while the belt is still ramping there is no ACK and
            // no other signal that the command landed. The pulse is that signal.
            SequentialAnimation on opacity {
              running: root.ramping && root.opened
              loops: Animation.Infinite
              alwaysRunToEnd: true
              NumberAnimation { from: 1.0; to: 0.55; duration: 950; easing.type: Easing.InOutSine }
              NumberAnimation { from: 0.55; to: 1.0; duration: 950; easing.type: Easing.InOutSine }
            }
          }
        }

        // ---------- What to do about it ----------
        // Its own line rather than a replacement for the state above: the pad
        // reports belt=idle and mode=standby in the SAME frame, so folding this
        // into the state label hid every statistic behind "In standby" for most
        // of the time the pad was reachable.
        Text {
          width: parent.width
          visible: root.instruction !== ""
          text: root.instruction
          color: root.refusing ? Color.urgent : Qt.darker(root.bar.foreground, 1.2)
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        // ---------- Session ----------
        Row {
          visible: root.hasStatus
          width: parent.width
          spacing: Style.space(20)

          Column {
            width: (parent.width - parent.spacing) / 2
            spacing: Style.spacing.labelGap
            InfoPair { label: "Distance"; value: root.service ? root.service.distanceKm.toFixed(2) + " km" : "" }
            InfoPair { label: "Steps"; value: root.service ? String(root.service.steps) : "" }
          }

          Column {
            width: (parent.width - parent.spacing) / 2
            spacing: Style.spacing.labelGap
            InfoPair { label: "Time"; value: root.service ? Model.elapsedText(root.service.elapsedSec) : "" }
            InfoPair {
              label: root.ramping ? "Heading for" : "Commanded"
              value: root.service ? Model.targetText(root.service.targetSpeed) : "—"
            }
          }
        }

        // ---------- Controls ----------
        PanelSeparator {
          foreground: root.bar.foreground
        }

        Column {
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "CONTROL"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
          }

          Row {
            id: actionRow
            width: parent.width
            spacing: Style.space(6)

            readonly property real cellWidth:
              (width - spacing * (root.actions.length - 1)) / root.actions.length

            Repeater {
              model: root.actions

              Button {
                required property var modelData
                required property int index

                width: actionRow.cellWidth
                iconText: modelData.icon
                iconSize: Style.font.title
                text: modelData.label
                fontSize: Style.font.bodySmall
                foreground: root.bar.foreground
                fontFamily: root.bar.fontFamily
                horizontalPadding: Style.spacing.controlPaddingX
                verticalPadding: Style.spacing.controlPaddingY + Style.space(2)
                bordered: true
                // Fact 8: only "running" means the belt is moving.
                active: modelData.key === "stop" && root.beltState === "running"
                hasCursor: root.cursorIndex === index
                onClicked: root.invoke(modelData.key)
                onHovered: function(h) { if (h) root.cursorIndex = index }
              }
            }
          }
        }
      }
    }
  }

  component InfoPair: Row {
    property string label: ""
    property string value: ""

    width: parent.width
    spacing: Style.space(8)

    InfoLabel { text: label }
    Item {
      width: Math.max(0, parent.width - parent.children[0].implicitWidth
        - parent.children[2].implicitWidth - parent.spacing * 2)
      height: 1
    }
    InfoValue { text: value }
  }

  component InfoLabel: Text {
    color: root.bar.foreground
    opacity: 0.6
    font.family: root.bar.fontFamily
    font.pixelSize: Style.font.bodySmall
  }

  component InfoValue: Text {
    color: root.bar.foreground
    font.family: root.bar.fontFamily
    font.pixelSize: Style.font.bodySmall
  }
}
