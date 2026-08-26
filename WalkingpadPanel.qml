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

  // Written as an explicit comparison rather than `service && ...`: a missing
  // service makes that expression undefined, and QML warns on assigning
  // undefined to a bool.
  readonly property bool refusing: service !== null && service !== undefined
    && service.lastErrorCode !== ""
    && service.lastErrorCode !== "standby"

  // Three registers, all from theme roles. An expected refusal the user can act
  // on gets `accent` -- attention, not failure. Only a plugin that is genuinely
  // not working gets `urgent`.
  readonly property bool refusalIsFault: root.refusing
    && !Model.isAdvisory(service.lastErrorCode)

  property int cursorIndex: -1
  // The control row and chart address different collections. Keeping their
  // cursors separate prevents a hovered bucket from becoming a button index;
  // the zone says which one the keyboard owns without conflating those indices.
  property int chartCursorIndex: -1
  // Charts §5: three zones, laid out the way they appear -- chart on top, then
  // the Stats action in the CONTROL header, then the control row.
  property string cursorZone: "controls"

  // The wall clock the chart's 60 fixed one-minute buckets are anchored to. It
  // has to advance on its own: while the pad is idle no samples arrive at all
  // (fact 3), and the axis must still scroll off the minute that just ended.
  property real nowSeconds: 0

  // Charts §1: the popup performs NO file I/O. This is derived from the service's
  // in-memory ring buffer alone, and only while the panel is open.
  readonly property var stepChart: root.opened && root.service
    ? Model.stepBuckets(root.service.samples, root.nowSeconds)
    : ({ buckets: [], rangeStart: 0, rangeEnd: 60, partialBucketStart: NaN })

  readonly property var chartBuckets: root.stepChart.buckets

  // The Set editor is disclosed rather than permanent. A text field living in a
  // bar popup holds focus away from the panel's key catcher for as long as it
  // exists, and the buttons already cover the common case.
  property bool editing: false

  // What the editor will actually DO with what is typed right now. Bound rather
  // than worked out on apply, so the goal window is visible BEFORE committing
  // instead of arriving afterwards as a number nobody chose.
  readonly property real editorTyped: root.editing && speedField.text.trim() !== ""
    ? Number(speedField.text)
    : NaN

  readonly property bool editorClamps: root.editing
    && service !== null && service !== undefined
    && isFinite(root.editorTyped)
    && Math.abs(root.editorTyped - service.clampGoalAbsolute(root.editorTyped)) >= 0.05

  readonly property string editorHint: {
    if (!service) return ""
    // The PAD's range, not the stepping window. An explicit value is bounded
    // by what the machine can do and nothing tighter -- the lead bound that
    // once narrowed this turned a typed 3.5 into 1.0 on a stopped belt, where
    // the goal is not serviced at all and the bound was protecting nothing.
    var window = "Allowed " + Model.speedText(service.minGoal)
      + " – " + Model.speedText(service.maxSpeed)
    if (!root.editorClamps) return window
    return window + " · " + Model.speedText(root.editorTyped)
      + " applies as " + Model.speedText(service.clampGoalAbsolute(root.editorTyped))
  }

  // Charts §0.4: every one of these acts on the TREADMILL. Stats navigates, so
  // it is not a sixth cell here -- adding it shrank each real control from about
  // 63 px to 52 px against 55 px of content, in a component with neither elide
  // nor clip. The width was the symptom; the category error was the cause.
  readonly property var actions: [
    { key: "start", label: "Start", icon: Model.ICON_START },
    { key: "stop", label: "Stop", icon: Model.ICON_STOP },
    { key: "down", label: "-" + root.speedStep.toFixed(1), icon: Model.ICON_MINUS },
    { key: "up", label: "+" + root.speedStep.toFixed(1), icon: Model.ICON_PLUS },
    { key: "set", label: "Set", icon: Model.ICON_SET }
  ]

  function openEditor() {
    if (!service) return
    root.cursorIndex = -1
    // Seeded with the target the panel is already showing, so the first thing
    // you can do is adjust a real number rather than retype one from scratch.
    var seed = service.targetSpeed !== null && service.targetSpeed !== undefined
      ? Number(service.targetSpeed)
      : (root.hasStatus ? root.currentSpeed : 0)
    speedField.text = Model.speedText(service.clampGoalAbsolute(seed))
    root.editing = true
  }

  function closeEditor() {
    // Early return so this is a no-op for the IPC methods, which reach invoke()
    // with the panel shut and have no business moving focus.
    if (!root.editing) return
    root.editing = false
    keyCatcher.forceActiveFocus()
  }

  function toggleEditor() {
    if (root.editing) root.closeEditor()
    else root.openEditor()
  }

  function applyEditor() {
    if (!service) { root.closeEditor(); return }
    // An empty field is a no-op, NOT a zero. Number("") is 0, which would sail
    // through clampGoal() as the floor and quietly slow the belt to minGoal.
    if (speedField.text.trim() === "") { root.closeEditor(); return }
    var value = Number(speedField.text)
    if (!isFinite(value)) { root.closeEditor(); return }
    // setExactGoal, not setGoal: this is the absolute path.
    service.setExactGoal(value)
    root.closeEditor()
  }

  function invoke(key) {
    if (key === "analysis") {
      if (root.bar && root.bar.shell
          && typeof root.bar.shell.summon === "function") {
        root.bar.shell.summon("io.github.shllg.walkingpad", "{}")
      }
      root.close()
      return
    }
    if (!service) return
    if (key === "set") { root.toggleEditor(); return }
    // Any real command supersedes a half-typed value. start() and stop() both
    // drop the goal in the service, so an editor left open would be offering a
    // number that nothing is heading for any more.
    root.closeEditor()
    // One command per user action (fact 17). Nothing here retries, and nothing
    // here second-guesses a refusal -- the bridge owns both.
    if (key === "start") service.start()
    else if (key === "stop") service.stop()
    else if (key === "down") service.speedDown()
    else if (key === "up") service.speedUp()
  }

  function activateCursor() {
    // Charts §5: this gate is SAFETY-relevant and must not be relaxed. Enter can
    // reach a belt command only from the control zone. The chart zone has
    // nothing to activate at all, and the stats zone's one action navigates
    // rather than moving the belt; falling through from either would turn Enter
    // while reading the chart into whichever command the retained control cursor
    // happens to name.
    if (root.cursorZone === "stats") { root.invoke("analysis"); return }
    if (root.cursorZone !== "controls") return
    if (cursorIndex < 0 || cursorIndex >= actions.length) return
    invoke(actions[cursorIndex].key)
  }

  function moveCursor(delta) {
    if (cursorIndex < 0) { cursorIndex = 0; return }
    var next = cursorIndex + delta
    cursorIndex = Math.max(0, Math.min(actions.length - 1, next))
  }

  function moveChartCursor(delta) {
    var count = root.chartBuckets.length
    if (count === 0) { chartCursorIndex = -1; return }
    var next = chartCursorIndex + delta
    chartCursorIndex = Math.max(0, Math.min(count - 1, next))
  }

  function enterChartZone() {
    // Fact 3 makes an empty chart the normal resting state, not an error, so
    // charts §5 leaves the zone unreachable rather than presenting a keyboard
    // cursor with nowhere to go. The newest bucket is the useful first reading.
    var count = root.chartBuckets.length
    if (count === 0) return false
    root.cursorZone = "chart"
    root.chartCursorIndex = count - 1
    return true
  }

  function handleMove(dx, dy) {
    // Charts §5 gives the axes different meanings: vertical changes zones and
    // horizontal moves within one. There is deliberately no vertical wrap,
    // because leaving the chart must be an explicit move back toward controls.
    if (dy !== 0) {
      if (dy < 0) {
        if (cursorZone === "controls") cursorZone = "stats"
        else if (cursorZone === "stats") root.enterChartZone()
      } else {
        if (cursorZone === "chart") {
          cursorZone = "stats"
          // Leaving the chart clears its cursor (charts §5), so a later return
          // to the zone starts at the live end rather than a stale minute.
          chartCursorIndex = -1
        } else if (cursorZone === "stats") {
          cursorZone = "controls"
        }
      }
      return
    }
    if (dx === 0) return
    // The stats zone holds one action, so horizontal movement has nothing to do.
    if (cursorZone === "controls") root.moveCursor(dx)
    else if (cursorZone === "chart") root.moveChartCursor(dx)
  }

  function clockText(seconds) {
    return Qt.formatTime(new Date(seconds * 1000), "HH:mm")
  }

  // Spec §7 and D-5: this forwarding channel carries preferences only.
  // gapMinutes regroups bounded read-side samples for presentation; the speed
  // values bound what this panel ASKS for, while tested bridge policy remains
  // authoritative for every command and receives none of these assignments.
  function applySettings() {
    if (!service) return
    service.maxSpeed = Number(setting("maxSpeed", 6.0))
    service.speedStep = Number(setting("speedStep", 0.5))
    service.startSpeed = Number(setting("startSpeed", 2.0))
    service.gapMinutes = Number(setting("gapMinutes", 5))
  }

  onServiceChanged: applySettings()
  onSettingsChanged: applySettings()
  // Opening or closing the panel dismisses the last refusal. It described the
  // press that provoked it, not a standing condition, and finding it still
  // there on the next visit reads as a fault the plugin is stuck in.
  onOpenedChanged: {
    cursorIndex = -1
    chartCursorIndex = -1
    root.cursorZone = "controls"
    // Seeded before the first frame so the chart never opens on a stale minute
    // from the last time the panel was up.
    if (root.opened) root.nowSeconds = Date.now() / 1000
    // Assigned directly rather than via closeEditor(), which hands focus back
    // to the key catcher -- not something to do to a panel on its way out.
    root.editing = false
    if (service) service.clearError()
  }

  // A half-typed goal outlives neither the link nor the pad's attention. The
  // service drops its goal on any link change (fact 3: absence is the pad's
  // normal resting state), so the editor must not sit there implying otherwise.
  onLinkStateChanged: if (root.linkState !== "connected") root.editing = false

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // Only while the panel is open, and deliberately not at 1 Hz: the bar for the
  // current minute already refreshes whenever a sample arrives, because the
  // bucket binding depends on the ring buffer. This timer exists solely so the
  // axis still rolls over on an idle pad, where no sample ever arrives.
  Timer {
    running: root.opened
    interval: 5000
    repeat: true
    onTriggered: root.nowSeconds = Date.now() / 1000
  }

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
      // PanelKeyCatcher's documented contract for an inline editor, and the
      // same thing the wifi passphrase prompt does. Keys.priority is
      // BeforeItem, so without this the panel swallows Escape, Enter and every
      // arrow before the text field below ever sees them.
      blocked: root.editing
      onMoveRequested: function(dx, dy) { root.handleMove(dx, dy) }
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

            // The pad's ceiling, stated rather than drawn. A filled bar is the
            // idiom for a TARGET, and spending it on speed-over-maximum made a
            // fixed limit look like something the belt was heading for.
            Text {
              text: "MAX " + Model.speedText(root.maxSpeed)
              color: root.bar.foreground
              opacity: 0.45
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.letterSpacing: 1.2
              anchors.right: parent.right
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
          color: root.refusalIsFault
            ? Color.urgent
            : (root.refusing ? Color.accent : Qt.darker(root.bar.foreground, 1.2))
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
          wrapMode: Text.WordWrap
        }

        // ---------- Steps per minute, on a real wall-clock axis ----------
        // Charts §1: one chart, and the one thing the numbers below cannot say.
        // Cumulative distance and steps were removed because they only ever rise
        // and each terminated at a figure printed in text beside it.
        //
        // Samples keep changing while the popup is closed, so the chart is
        // unloaded then: nothing recomputes buckets for a panel nobody is
        // looking at.
        Loader {
          id: chartLoader
          width: parent.width
          active: root.opened

          sourceComponent: Component {
            Column {
              width: chartLoader.width
              spacing: Style.spacing.labelGap

              WalkingpadBarChart {
                id: stepChart
                width: parent.width
                buckets: root.chartBuckets
                bucketSeconds: 60
                // The axis is ALWAYS the last hour, matching the ring buffer's
                // own bound, so a minute with no walking is visibly absent
                // rather than compressed away by a sample-index axis.
                rangeStart: root.stepChart.rangeStart
                rangeEnd: root.stepChart.rangeEnd
                partialBucketStart: root.stepChart.partialBucketStart

                fg: root.bar.foreground
                accent: Color.accent
                muted: Qt.darker(root.bar.foreground, 1.4)

                // Every dimension enters through Style, so the compact popup
                // proportions survive Omarchy's live spacing and font scales
                // without this component importing either palette.
                plotHeight: Style.space(46)
                axisHeight: Style.space(14)
                barSpacing: Style.space(1)
                maxBarWidth: Style.space(10)
                cornerRadius: Style.cornerRadius
                lineWidth: Style.space(1)
                fontFamily: root.bar.fontFamily
                labelFontSize: Style.font.caption
                valueUnit: "steps"

                axisStartLabel: root.clockText(root.stepChart.rangeStart)
                axisEndLabel: "now"

                cursorIndex: root.chartCursorIndex
                interactive: true
                // Pointer and keyboard share the same cursor owner. A valid
                // hover enters the chart zone so the next arrow continues at
                // that bucket; -1 is the empty sentinel and cannot focus it.
                onCursorMoved: function(index) {
                  if (index < 0) return
                  root.cursorZone = "chart"
                  root.chartCursorIndex = index
                }
              }

              Text {
                width: parent.width
                readonly property var bucket:
                  root.chartCursorIndex >= 0
                    && root.chartCursorIndex < root.chartBuckets.length
                    ? root.chartBuckets[root.chartCursorIndex] : null
                visible: bucket !== null
                text: bucket === null ? "" : root.clockText(bucket.t)
                  + "  ·  " + Math.round(bucket.value) + " steps"
                color: Qt.darker(root.bar.foreground, 1.4)
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.caption
              }
            }
          }
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
              // A live goal is what the person asked for; "Commanded" is only
              // the intermediate step, and naming it that when a goal is set
              // would show a number nobody chose.
              label: root.service && root.service.goalSpeed !== null
                ? "Goal" : (root.ramping ? "Heading for" : "Commanded")
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

          // Charts §5: Stats sits at the right-hand end of the section header,
          // where a secondary NAVIGATION action belongs. That is what returns
          // the control row below to five buttons at their original width.
          Item {
            width: parent.width
            implicitHeight: Math.max(controlHeader.implicitHeight,
                                     statsAction.implicitHeight)

            PanelSectionHeader {
              id: controlHeader
              anchors.left: parent.left
              anchors.verticalCenter: parent.verticalCenter
              text: "CONTROL"
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
            }

            Button {
              id: statsAction
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              iconText: Model.ICON_STATS
              iconSize: Style.font.bodySmall
              text: "Stats"
              fontSize: Style.font.caption
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              horizontalPadding: Style.spacing.controlPaddingX
              verticalPadding: Style.spacing.xxs
              hasCursor: root.cursorZone === "stats"
              onClicked: root.invoke("analysis")
              onHovered: function(h) {
                if (!h) return
                root.cursorZone = "stats"
                root.chartCursorIndex = -1
              }
            }
          }

          // ---------- Set a speed by hand ----------
          // The buttons step relative to where the belt already is, which can
          // never land on a round number from an odd tenth: the pad reports
          // speed in tenths and hunts around its target, so from 2.8 the only
          // reachable values are 2.3 and 3.3. This is the way onto an exact one.
          Column {
            width: parent.width
            visible: root.editing
            spacing: Style.space(6)

            Item {
              width: parent.width
              implicitHeight: speedField.implicitHeight

              InfoLabel {
                text: "Set speed"
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
              }

              Text {
                id: unitLabel
                text: "km/h"
                color: root.bar.foreground
                opacity: 0.6
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.bodySmall
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
              }

              TextField {
                id: speedField
                width: Style.space(76)
                anchors.right: unitLabel.left
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                foreground: root.bar.foreground
                font.family: root.bar.fontFamily
                font.pixelSize: Style.font.body
                horizontalPadding: Style.spacing.controlPaddingX
                verticalPadding: Style.spacing.controlPaddingY
                horizontalAlignment: Qt.AlignHCenter
                inputMethodHints: Qt.ImhFormattedNumbersOnly

                // Deliberately the pad's WHOLE range, not the live goal window.
                // That window moves as the belt ramps, and a validator that
                // rejects keystrokes against a moving bound reads as a broken
                // keyboard. The window is applied on commit instead, and the
                // caption below says what will happen before you commit.
                validator: DoubleValidator {
                  bottom: 0.0
                  top: root.maxSpeed
                  decimals: 1
                  notation: DoubleValidator.StandardNotation
                }

                onAccepted: root.applyEditor()
                Keys.onEscapePressed: root.closeEditor()

                // Qt.callLater because focus does not stick while the item is
                // still becoming visible -- the same guard the wifi prompt uses.
                onVisibleChanged: if (visible) Qt.callLater(function() {
                  speedField.forceActiveFocus()
                  speedField.selectAll()
                })
              }
            }

            Text {
              width: parent.width
              text: root.editorHint
              // Accent, not urgent: a value about to be clamped is the safety
              // window doing its job, not a fault (spec §5.5).
              color: root.editorClamps
                ? Color.accent
                : Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              wrapMode: Text.WordWrap
            }
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
                hasCursor: root.cursorZone === "controls" && root.cursorIndex === index
                onClicked: root.invoke(modelData.key)
                onHovered: function(h) {
                  if (!h) return
                  // Pointer and keyboard share one cursor owner. Handing it to the
                  // zone last addressed makes the next arrow continue from there.
                  root.cursorZone = "controls"
                  root.cursorIndex = index
                  root.chartCursorIndex = -1
                }
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
