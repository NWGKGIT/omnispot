import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

BarWidget {
  id: root
  moduleName: "omnispot.omarchy"
  property bool panelOpen: false
  property bool panelRequested: false
  readonly property bool on: state.status.on === true
  readonly property int clients: state.status.clients ? state.status.clients.length : 0

  HotspotState { id: state }

  function open() {
    if (!panelRequested) {
      panelLoader.setSource(Qt.resolvedUrl("HotspotPanel.qml"), {
        bar: root.bar, anchorItem: button, state: state, owner: root, open: true
      })
      panelRequested = true
    }
    panelOpen = true
  }
  function close() { panelOpen = false }
  function toggle() { if (panelOpen) close(); else open() }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight
  readonly property bool opened: panelOpen
  property bool popoutSwitchClosingState: false
  readonly property bool popoutSwitchClosing: popoutSwitchClosingState
  function closeForPopoutSwitch() {
    popoutSwitchClosingState = true
    close()
    Qt.callLater(function() { popoutSwitchClosingState = false })
  }

  Loader {
    id: panelLoader
    active: root.panelRequested
    visible: false
  }

  onPanelOpenChanged: if (panelLoader.item) panelLoader.item.open = panelOpen
  onBarChanged: if (panelLoader.item) panelLoader.item.bar = bar

  IpcHandler {
    target: "omnispot.omarchy"
    function open(): void { root.open() }
    function close(): void { root.close() }
    function toggle(): void { root.toggle() }
    function start(): void { state.start() }
    function stop(): void { state.stop() }
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    tooltipText: root.on
      ? "Hotspot on · " + state.status.ssid + "\n" + (root.clients === 1 ? "1 device" : root.clients + " devices") + " connected"
      : "Hotspot off\nRight-click to turn on"
    iconComponent: Component {
      Text {
        anchors.centerIn: parent
        text: "󰀂"
        color: root.on ? Color.accent : (root.bar ? root.bar.barForeground : Color.foreground)
        opacity: root.on || state.busy ? 1 : 0.6
        font.family: "Symbols Nerd Font"
        font.pixelSize: Style.bar.iconFont
      }
    }
    onPressed: function(code) {
      if (code === Qt.RightButton) {
        if (state.setupNeeded && !root.on) root.open()
        else state.toggle()
      } else root.toggle()
    }
  }
}
