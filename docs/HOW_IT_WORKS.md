# How Omnispot Works

Omnispot connects three main components: a QML interface for the Omarchy desktop shell, a Python backend for configuration and device monitoring, and a lightweight privileged helper that configures networking.

```
    QML Surfaces                           Python Backend
    ┌──────────────────┐                  ┌───────────────────────┐
    │ BarWidget.qml    │                  │ lib/hotspot.py        │
    │   icon + tooltip │ ── polls ──────> │   status              │
    │                  │                  │   start / stop        │
    │ HotspotPanel.qml │ ── commands ───> │   set / setup         │
    │   settings UI    │                  │   uninstall           │
    │                  │                  │                       │
    │ HotspotState.qml │ <── JSON ──────  │   {"ok": true, ...}   │
    │   QML to Python  │                  └───────────┬───────────┘
    └──────────────────┘                              │ pkexec
                                          ┌───────────▼───────────┐
                                          │ omnispot-helper       │
                                          │   (root, bash)        │
                                          │   up / down           │
                                          └───────────────────────┘
```

## QML Components

- **BarWidget.qml**: Instantiated directly by the Omarchy top bar. Displays the hotspot status icon, live client count tooltip, and toggles the hotspot via right-click or opens the control panel on left-click.
- **HotspotPanel.qml**: Dropdown management panel. Provides quick on/off toggling, SSID and password editing, band selection (2.4 GHz, 5 GHz, or matching Wi-Fi), connected client listings with hostnames and IP addresses, and the one-time admin setup prompt.
- **HotspotState.qml**: Serves as the state bridge between QML and the Python backend. Regularly polls status and executes backend commands asynchronously, handling responses strictly through structured JSON.

## Python Backend (`lib/hotspot.py`)

The backend coordinates system detection and configuration logic:
- Detects the active wireless interface via NetworkManager (`nmcli`).
- Reads and persists configuration in `~/.config/omarchy/hotspot/settings.json`.
- Dynamically selects an available subnet (`10.42.0.0/24` by default, querying the routing table to avoid network conflicts).
- Determines operating channel and band based on active upstream Wi-Fi connections.
- Dispatches privileged network operations to the root helper via `pkexec`.
- Reports responses as clean JSON objects (`{"ok": true, "data": ...}` or `{"ok": false, "error": ...}`).

## Privileged Helper (`omnispot-helper`)

The root helper manages the virtual wireless interface and packet routing:
- Creates a virtual access point interface (`ap0`) on the wireless card.
- Launches `hostapd` for WPA2 access point services.
- Starts `dnsmasq` for lightweight DHCP and DNS addressing on the chosen subnet.
- Configures iptables rules for NAT and masquerading to upstream connections.
- Safely performs teardown during deactivation (terminating processes and cleaning iptables rules and virtual interfaces).

## Polling Strategy

HotspotState employs an adaptive polling interval to minimize overhead:
- **2 seconds** while active, ensuring real-time visibility as client devices connect or disconnect.
- **10 seconds** when idle.

## Installed System Files

| Path | Purpose |
|------|---------|
| `/usr/local/bin/omnispot-helper` | Privileged helper script |
| `/etc/polkit-1/rules.d/49-omnispot.rules` | Polkit authorization for local active users |
| `/etc/NetworkManager/conf.d/omnispot.conf` | Prevents NetworkManager from interfering with `ap0` |
