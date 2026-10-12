import QtQuick
import QtQuick.Layouts
import Quickshell
import qs.Commons
import qs.Ui

KeyboardPanel {
  id: root
  property QtObject state

  readonly property var status: state ? state.status : ({})
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property bool sharingWifi: root.status.station !== "" && root.status.station !== undefined
  readonly property int clientCount: root.status.clients ? root.status.clients.length : 0

  // Unsaved edits. null means the fields mirror the saved profile.
  property var draft: null
  property bool showPassword: false
  readonly property var shown: draft || {ssid: root.status.ssid || "", password: root.status.password || "", band: root.status.band || "any"}
  readonly property bool dirty: draft !== null &&
    (draft.ssid !== root.status.ssid || draft.password !== root.status.password || draft.band !== root.status.band)
  readonly property string draftError: !dirty ? "" :
    (shown.ssid.length < 1 || shown.ssid.length > 32) ? "Network name must be 1-32 characters" :
    (shown.password.length < 8 || shown.password.length > 63) ? "Password must be 8-63 characters" : ""

  function edit(key, value) {
    var next = {ssid: shown.ssid, password: shown.password, band: shown.band}
    next[key] = value
    draft = next
  }

  function randomPassword() {
    var chars = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    var out = ""
    for (var i = 0; i < 12; i++) out += chars.charAt(Math.floor(Math.random() * chars.length))
    return out
  }

  function heroMeta() {
    if (!state) return ""
    if (state.action === "start") return "Turning on…"
    if (state.action === "stop") return "Turning off…"
    if (state.action === "set") return "Saving…"
    if (state.action === "setup") return "Setting up…"
    if (root.status.on) return "Sharing " + (root.status.uplink || "internet") + " · " +
      (clientCount === 1 ? "1 device" : clientCount + " devices")
    if (state.setupNeeded) return "Setup needed"
    return root.status.uplink ? "Off · will share " + root.status.uplink : "Off · no internet connection to share"
  }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function")
      return bar.switchPanelFrom(root.owner || root, direction)
    return false
  }

  focusTarget: keyCatcher
  contentWidth: fittedContentWidth(Style.space(360))
  contentHeight: fittedContentHeight(column.implicitHeight)

  PanelKeyCatcher {
    id: keyCatcher
    anchors.fill: parent
    blocked: (ssidField && ssidField.activeFocus) || (passwordField && passwordField.activeFocus)
    onCloseRequested: root.close()
    onTabRequested: function(direction) { root.switchPanel(direction) }

    ColumnLayout {
      id: column
      anchors.left: parent.left
      anchors.right: parent.right
      spacing: Style.space(12)

    // A successful save means the profile now matches the fields.
    Connections {
      target: root.state
      function onBusyChanged() {
        if (!root.state.busy && root.state.error === "") root.draft = null
      }
    }

    PanelHero {
      id: hero
      Layout.fillWidth: true
      title: "Mobile hotspot"
      meta: root.heroMeta()
      foreground: root.foreground
      fontFamily: root.fontFamily
      iconOpacity: root.status.on ? 1.0 : 0.5
      iconComponent: Component {
        Text {
          text: "󰀂"
          color: root.foreground
          font.family: "Symbols Nerd Font"
          font.pixelSize: Style.font.display
        }
      }
      trailingControl: Component {
        ToggleSwitch {
          checked: root.status.on === true
          busy: root.state && (root.state.action === "start" || root.state.action === "stop")
          interactive: root.state && !root.state.busy && (root.status.on || !root.state.setupNeeded)
          foreground: root.foreground
          onToggled: root.state.toggle()
        }
      }
    }

    Text {
      Layout.fillWidth: true
      visible: root.state && root.state.error !== ""
      text: root.state ? root.state.error : ""
      color: root.urgent
      font.family: root.fontFamily
      wrapMode: Text.WordWrap
    }

    RowLayout {
      Layout.fillWidth: true
      visible: root.state && root.state.setupNeeded && !root.status.on
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: "The hotspot needs a one-time admin setup. It installs a small helper. Requires hostapd (install with 'sudo pacman -S hostapd' if missing)."
        color: root.dim
        font.family: root.fontFamily
        wrapMode: Text.WordWrap
      }
      Button {
        text: "Set up"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: !root.state.busy
        onClicked: root.state.setup()
      }
    }

    PanelSeparator { Layout.fillWidth: true; foreground: root.foreground }

    PanelSectionHeader { text: "NETWORK NAME"; foreground: root.foreground; fontFamily: root.fontFamily }
    TextField {
      id: ssidField
      Layout.fillWidth: true
      text: root.shown.ssid
      foreground: root.foreground
      font.family: root.fontFamily
      maximumLength: 32
      onTextEdited: root.edit("ssid", text)
      Keys.onEscapePressed: root.close()
    }

    PanelSectionHeader { text: "PASSWORD"; foreground: root.foreground; fontFamily: root.fontFamily }
    RowLayout {
      Layout.fillWidth: true
      spacing: Style.space(4)
      TextField {
        id: passwordField
        Layout.fillWidth: true
        text: root.shown.password
        password: !root.showPassword
        foreground: root.foreground
        font.family: root.fontFamily
        maximumLength: 63
        onTextEdited: root.edit("password", text)
        Keys.onEscapePressed: root.close()
      }
      PanelActionButton {
        iconText: root.showPassword ? "󰈉" : "󰈈"
        tooltipText: root.showPassword ? "Hide password" : "Show password"
        foreground: root.foreground
        onClicked: root.showPassword = !root.showPassword
      }
      PanelActionButton {
        iconText: "󰑐"
        tooltipText: "Generate a new password"
        foreground: root.foreground
        onClicked: { root.edit("password", root.randomPassword()); root.showPassword = true }
      }
    }

    PanelSectionHeader { text: "BAND"; foreground: root.foreground; fontFamily: root.fontFamily }
    Dropdown {
      Layout.fillWidth: true
      showLabel: false
      enabled: !root.sharingWifi
      value: root.sharingWifi ? "match" : root.shown.band
      options: root.sharingWifi
        ? [{value: "match", label: "Same as your Wi-Fi connection"}]
        : [{value: "any", label: "Any available"}, {value: "2.4", label: "2.4 GHz"}, {value: "5", label: "5 GHz"}]
      fontFamily: root.fontFamily
      onChanged: function(value) { root.edit("band", value) }
    }

    RowLayout {
      Layout.fillWidth: true
      visible: root.dirty
      spacing: Style.space(8)
      Text {
        Layout.fillWidth: true
        text: root.draftError || (root.status.on ? "Saving restarts the hotspot" : "")
        color: root.draftError ? root.urgent : root.dim
        font.family: root.fontFamily
        wrapMode: Text.WordWrap
      }
      Button {
        text: "Cancel"
        foreground: root.foreground
        fontFamily: root.fontFamily
        onClicked: root.draft = null
      }
      Button {
        text: "Save"
        bordered: true
        foreground: root.foreground
        fontFamily: root.fontFamily
        enabled: root.draftError === "" && !root.state.busy
        onClicked: root.state.save(root.shown.ssid, root.shown.password, root.shown.band)
      }
    }

    PanelSeparator { Layout.fillWidth: true; visible: root.status.on; foreground: root.foreground }

    PanelSectionHeader {
      visible: root.status.on
      text: "CONNECTED DEVICES (" + root.clientCount + ")"
      foreground: root.foreground
      fontFamily: root.fontFamily
    }

    Text {
      visible: root.status.on && root.clientCount === 0
      text: "No devices connected"
      color: root.dim
      font.family: root.fontFamily
    }

    Repeater {
      model: root.status.on ? root.status.clients : []
      delegate: RowLayout {
        Layout.fillWidth: true
        Text {
          Layout.fillWidth: true
          text: modelData.name || modelData.mac
          color: root.foreground
          font.family: root.fontFamily
          elide: Text.ElideRight
        }
        Text {
          text: modelData.ip
          color: root.dim
          font.family: root.fontFamily
        }
      }
    }
  }
}
}
