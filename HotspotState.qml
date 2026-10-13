import QtQuick
import Quickshell
import Quickshell.Io

// All hotspot logic lives in lib/hotspot.py; this just polls its JSON status and
// runs one action at a time. Every command produces structured JSON:
//   {"ok": true, "data": {...}}   or   {"ok": false, "error": "..."}
Item {
  id: root
  visible: false

  readonly property string pluginDir: String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
  readonly property string backend: pluginDir + "/lib/hotspot.py"
  property var status: ({on: false, ssid: "", password: "", band: "any", iface: "", wifi: "", station: "", uplink: "", helper: false, clients: []})
  property string lastJson: ""
  property bool loaded: false
  property bool busy: false
  property string action: ""
  property string error: ""

  // hostapd and the root helper are installed once via "Set up".
  readonly property bool setupNeeded: loaded && !status.helper

  function refresh() { if (!poll.running) poll.running = true }

  function run(args, env) {
    if (busy) return
    error = ""
    busy = true
    action = args[0]
    runner.environment = env || ({})
    runner.command = ["python3", backend].concat(args)
    runner.running = true
  }

  function clearError() { root.error = "" }

  function start() { run(["start"]) }
  function stop() { run(["stop"]) }
  function toggle() { status.on ? stop() : start() }
  // Name and password go through the environment, not the command line,
  // so they cannot be read from the process list.
  function save(ssid, password, band) {
    run(["set", band], {OMNISPOT_SSID: ssid, OMNISPOT_PASS: password})
  }
  function setup() { run(["setup"]) }

  Process {
    id: poll
    command: ["python3", root.backend, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          if (result.ok && result.data) {
            var raw = JSON.stringify(result.data)
            if (raw === root.lastJson) return
            root.status = result.data
            root.lastJson = raw
            root.loaded = true
          }
        } catch (e) {}
      }
    }
  }

  // Parse structured JSON from the runner instead of scraping stderr.
  Process {
    id: runner
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        try {
          var result = JSON.parse(text)
          if (!result.ok) {
            root.error = result.error || "Something went wrong"
          }
        } catch (e) {
          root.error = "Backend error"
        }
        root.busy = false
        root.action = ""
        root.refresh()
      }
    }
    onExited: function(code) {
      // If stdout handler already ran, this is a no-op; if the process
      // exited without producing JSON (e.g. Python crashed), report that.
      if (root.busy) {
        if (code !== 0 && root.error === "") root.error = "Backend exited with code " + code
        root.busy = false
        root.action = ""
        root.refresh()
      }
    }
  }

  // Adaptive polling: fast when on (clients may change), slow when off.
  readonly property int pollInterval: root.status.on ? 2000 : 10000
  Timer { interval: root.pollInterval; repeat: true; running: true; onTriggered: root.refresh() }
  Component.onCompleted: refresh()
}
