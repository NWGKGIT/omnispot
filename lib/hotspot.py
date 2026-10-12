"""Mobile hotspot backend for the omnispot.omarchy bar widget.

Commands
--------
    hotspot.py status              Print state as JSON
    hotspot.py start               Turn the hotspot on
    hotspot.py stop                Turn the hotspot off
    hotspot.py set SSID PASS BAND  Save settings (BAND: any|2.4|5), restart if on
    hotspot.py setup               One-time root setup (installs helper + polkit)
    hotspot.py uninstall           Remove system files installed by setup

Every command writes a single JSON object to stdout:
    {"ok": true, "data": {...}}          on success
    {"ok": false, "error": "message"}    on failure

"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

HERE = Path(__file__).resolve().parent.parent  # plugin root (one level up from lib/)
AP = "ap0"

_config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
CONFIG_DIR = _config_home / "omarchy" / "hotspot"
SETTINGS = CONFIG_DIR / "settings.json"

RUN_DIR = Path(os.environ.get("HOTSPOT_RUN", "/run/omnispot"))

HELPER = Path(os.environ.get(
    "HOTSPOT_HELPER",
    "/usr/local/bin/omnispot-helper",
))
POLKIT_RULE = Path("/etc/polkit-1/rules.d/49-omnispot.rules")
NM_CONF = Path("/etc/NetworkManager/conf.d/omnispot.conf")

# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------

def json_ok(data: dict | None = None) -> str:
    payload: dict = {"ok": True}
    if data is not None:
        payload["data"] = data
    return json.dumps(payload)


def json_error(message: str) -> str:
    return json.dumps({"ok": False, "error": message})


def respond_ok(data: dict | None = None) -> None:
    print(json_ok(data))
    sys.exit(0)


def respond_error(message: str) -> None:
    print(json_error(message))
    sys.exit(1)

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def run_quiet(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, capturing output, never raising on failure."""
    return subprocess.run(cmd, check=False, capture_output=True, text=True, **kwargs)

# ---------------------------------------------------------------------------
# Subnet selection: 10.42.0.0/24 by default, with conflict resolution
# ---------------------------------------------------------------------------

DEFAULT_SUBNETS = [
    "10.42.0.0/24",
    "10.42.1.0/24",
    "10.42.2.0/24",
    "10.43.0.0/24",
]

FALLBACK_SUBNET = "10.42.99.0/24"


def get_routes() -> list[dict]:
    """Return the parsed output of `ip -j route`."""
    result = run_quiet(["ip", "-j", "route"])
    if result.returncode != 0:
        return []
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return []


def pick_subnet() -> str:
    """Pick the first candidate subnet not already in the routing table."""
    routes = get_routes()
    used: set[ipaddress.IPv4Network] = set()
    for r in routes:
        dst = r.get("dst", "")
        if dst == "default":
            continue
        try:
            parsed = ipaddress.ip_network(dst, strict=False)
            if isinstance(parsed, ipaddress.IPv4Network):
                used.add(parsed)
        except ValueError:
            pass

    for candidate in DEFAULT_SUBNETS:
        net = ipaddress.ip_network(candidate)
        if not any(net.overlaps(u) for u in used):
            return candidate
    return FALLBACK_SUBNET


def subnet_gateway(subnet: str) -> str:
    """Return the .1 address of a /24 subnet string, e.g. '10.42.0.1'."""
    net = ipaddress.ip_network(subnet)
    return str(next(iter(net.hosts())))


def subnet_prefix(subnet: str) -> str:
    """Return the first three octets, e.g. '10.42.0' from '10.42.0.0/24'."""
    return ".".join(subnet.split("/")[0].split(".")[:3])

# ---------------------------------------------------------------------------
# NetworkManager queries
# ---------------------------------------------------------------------------

def wifi_device() -> str:
    """Return the name of the first Wi-Fi device that is not the AP."""
    result = run_quiet(["nmcli", "-t", "-f", "DEVICE,TYPE", "device", "status"])
    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi" and parts[0] != AP:
            return parts[0]
    return ""


def station_connection(dev: str) -> str:
    """Name of the Wi-Fi network the station interface is connected to."""
    if not dev:
        return ""
    result = run_quiet([
        "nmcli", "-t", "-f", "GENERAL.STATE,GENERAL.CONNECTION",
        "device", "show", dev,
    ])
    state_up = False
    for line in result.stdout.strip().splitlines():
        if line.startswith("GENERAL.STATE:"):
            state_up = line.split(":")[1].strip().startswith("100")
        elif line.startswith("GENERAL.CONNECTION:") and state_up:
            return line.split(":", 1)[1].strip()
    return ""


def uplink_name() -> str:
    """Name of the active wired or wireless connection used as uplink."""
    result = run_quiet([
        "nmcli", "-t", "-f", "NAME,TYPE,DEVICE",
        "connection", "show", "--active",
    ])
    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[2] and parts[1] in ("802-3-ethernet", "802-11-wireless"):
            return parts[0]
    return ""

# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

def is_active() -> bool:
    """True if hostapd is running (its PID file points to a live process)."""
    pidfile = RUN_DIR / "hostapd.pid"
    try:
        pid = int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return False
    return Path(f"/proc/{pid}").is_dir()


def stop_process(pidfile: Path, timeout: float = 5.0) -> None:
    """Send SIGTERM, wait up to *timeout* seconds, then SIGKILL."""
    try:
        pid = int(pidfile.read_text().strip())
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pidfile.unlink(missing_ok=True)
        return

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        # Still alive after timeout: force kill.
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    pidfile.unlink(missing_ok=True)

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SAFE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def random_password(length: int = 12) -> str:
    return "".join(secrets.choice(_SAFE_CHARS) for _ in range(length))


def valid_ssid(ssid: str) -> bool:
    encoded = ssid.encode("utf-8")
    return 1 <= len(encoded) <= 32 and "\n" not in ssid


def valid_pass(password: str) -> bool:
    return (
        8 <= len(password) <= 63
        and all(32 <= ord(c) < 127 for c in password)
    )


def read_settings() -> dict:
    """Read settings.json, returning defaults if missing or invalid."""
    try:
        data = json.loads(SETTINGS.read_text())
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def write_settings(ssid: str, password: str, band: str) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"ssid": ssid, "password": password, "band": band}
    # Restrictive permissions: owner-only read/write.
    fd = os.open(
        SETTINGS,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
        0o600,
    )
    try:
        os.write(fd, json.dumps(payload, indent=2).encode() + b"\n")
    finally:
        os.close(fd)


def ensure_settings() -> dict:
    """Make sure a settings file exists; create with defaults if not."""
    settings = read_settings()
    if settings.get("ssid") and settings.get("password"):
        return settings
    ssid = settings.get("ssid") or f"{os.uname().nodename} hotspot"
    password = settings.get("password") or random_password()
    band = settings.get("band", "any")
    if not valid_ssid(ssid):
        ssid = "omarchy hotspot"
    if not valid_pass(password):
        password = random_password()
    if band not in ("any", "2.4", "5"):
        band = "any"
    write_settings(ssid, password, band)
    return {"ssid": ssid, "password": password, "band": band}


def band_pref(settings: dict) -> str:
    b = settings.get("band", "any")
    return b if b in ("2.4", "5") else "any"

# ---------------------------------------------------------------------------
# Setup detection
# ---------------------------------------------------------------------------

def helper_installed() -> bool:
    """True if the installed helper matches the bundled one and hostapd exists."""
    bundled = HERE / "omnispot-helper"
    if not HELPER.exists() or not bundled.exists():
        return False
    try:
        if HELPER.read_bytes() != bundled.read_bytes():
            return False
    except OSError:
        return False
    return shutil.which("hostapd") is not None

# ---------------------------------------------------------------------------
# Client enumeration
# ---------------------------------------------------------------------------

def list_clients() -> list[dict]:
    """List connected stations with their lease information."""
    result = run_quiet(["iw", "dev", AP, "station", "dump"])
    macs: list[str] = []
    for line in result.stdout.splitlines():
        if line.startswith("Station"):
            parts = line.split()
            if len(parts) >= 2:
                macs.append(parts[1])

    leases: dict[str, tuple[str, str]] = {}
    lease_file = RUN_DIR / "leases"
    if lease_file.exists():
        try:
            for line in lease_file.read_text().splitlines():
                parts = line.split()
                if len(parts) >= 4:
                    mac_lower = parts[1].lower()
                    ip_addr = parts[2]
                    name = parts[3] if parts[3] != "*" else ""
                    leases[mac_lower] = (ip_addr, name)
        except OSError:
            pass

    clients: list[dict] = []
    for mac in macs:
        ip_addr, name = leases.get(mac.lower(), ("", ""))
        clients.append({"mac": mac, "ip": ip_addr, "name": name})
    return clients

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_status() -> None:
    settings = ensure_settings()
    wifi = wifi_device()
    on = is_active()
    clients = list_clients() if on else []
    respond_ok({
        "on": on,
        "ssid": settings.get("ssid", ""),
        "password": settings.get("password", ""),
        "band": band_pref(settings),
        "wifi": wifi,
        "station": station_connection(wifi),
        "uplink": uplink_name(),
        "helper": helper_installed(),
        "clients": clients,
    })


def cmd_start() -> None:
    settings = ensure_settings()
    if not helper_installed():
        respond_error("Run setup first")

    wifi = wifi_device()
    if not wifi:
        respond_error("No Wi-Fi adapter found")

    station = station_connection(wifi)
    if station:
        # Follow the Wi-Fi connection's channel.
        result = run_quiet(["iw", "dev", wifi, "info"])
        channel = ""
        mode = "g"
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("channel"):
                parts = line.split()
                if len(parts) >= 2:
                    channel = parts[1]
                # Detect 5 GHz: frequency is in parentheses, e.g. "(5220 MHz)"
                if "(" in line:
                    freq_str = line.split("(")[1].split()[0]
                    try:
                        if int(freq_str) >= 5000:
                            mode = "a"
                    except ValueError:
                        pass
                break
        if not channel:
            channel = "6"
    else:
        b = band_pref(settings)
        if b == "5":
            mode = "a"
            channel = "36"
        else:
            mode = "g"
            channel = "6"

    subnet = pick_subnet()

    # Feed parameters to the root helper via stdin through pkexec.
    stdin_data = "\n".join([
        wifi,
        settings.get("ssid", ""),
        settings.get("password", ""),
        mode,
        channel,
        subnet,
    ]) + "\n"

    result = subprocess.run(
        ["pkexec", str(HELPER), "up"],
        input=stdin_data,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Extract a meaningful message from stderr.
        err = result.stderr.strip().splitlines()
        msg = err[-1] if err else "Could not start the hotspot"
        respond_error(msg)
    respond_ok()


def cmd_stop() -> None:
    if not HELPER.exists():
        respond_ok()
        return
    result = subprocess.run(
        ["pkexec", str(HELPER), "down"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = result.stderr.strip().splitlines()
        msg = err[-1] if err else "Could not stop the hotspot"
        respond_error(msg)
    respond_ok()


def cmd_set(args: list[str]) -> None:
    if len(args) < 3:
        respond_error("Usage: set SSID PASSWORD BAND")

    ssid, password, band = args[0], args[1], args[2]

    if not valid_ssid(ssid):
        respond_error("Network name must be 1-32 characters")
    if not valid_pass(password):
        respond_error("Password must be 8-63 printable characters")
    if band not in ("any", "2.4", "5"):
        respond_error(f"Unknown band: {band}")

    write_settings(ssid, password, band)

    if is_active():
        # Restart with new settings.
        cmd_start()
        return
    respond_ok()


def cmd_setup() -> None:
    # Check for hostapd before requesting root privileges:
    if not shutil.which("hostapd"):
        respond_error(
            "hostapd is required but not installed. Install it with: sudo pacman -S hostapd"
        )

    # If not root, re-exec through pkexec.
    if os.geteuid() != 0:
        result = subprocess.run(
            ["pkexec", sys.executable, str(Path(__file__).resolve()), "setup"],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        if stdout:
            try:
                data = json.loads(stdout)
                if not data.get("ok"):
                    respond_error(data.get("error", "Setup failed"))
            except (json.JSONDecodeError, TypeError):
                pass
            print(stdout)
            sys.exit(0 if result.returncode == 0 else 1)

        if result.returncode != 0:
            err = result.stderr.strip().splitlines()
            msg = err[-1] if err else "Setup failed"
            respond_error(msg)
        respond_ok()
        return

    # --- Running as root from here. ---

    if not shutil.which("hostapd"):
        respond_error(
            "hostapd is required but not installed. Install it with: sudo pacman -S hostapd"
        )

    # Install the helper.
    bundled_helper = HERE / "omnispot-helper"
    if not bundled_helper.exists():
        respond_error("Bundled helper not found")

    HELPER.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(bundled_helper, HELPER)
    HELPER.chmod(0o755)

    # NetworkManager: ignore the AP interface.
    NM_CONF.parent.mkdir(parents=True, exist_ok=True)
    NM_CONF.write_text(textwrap.dedent(f"""\
        # The omnispot.omarchy widget runs hostapd on {AP} itself.
        [device-omnispot]
        match-device=interface-name:{AP}
        managed=0
    """))
    NM_CONF.chmod(0o644)
    run_quiet(["nmcli", "general", "reload", "conf"])

    # Polkit: let the logged-in user run the helper without a password.
    POLKIT_RULE.parent.mkdir(parents=True, exist_ok=True)
    POLKIT_RULE.write_text(textwrap.dedent(f"""\
        // Lets local active users start/stop the omnispot.omarchy widget's hotspot.
        polkit.addRule(function(action, subject) {{
          if (action.id == "org.freedesktop.policykit.exec" &&
              action.lookup("program") == "{HELPER}" &&
              subject.local && subject.active) {{
            return polkit.Result.YES;
          }}
        }});
    """))
    POLKIT_RULE.chmod(0o644)

    respond_ok()


def cmd_uninstall() -> None:
    # Stop a running hotspot first.
    if is_active() and HELPER.exists():
        subprocess.run(
            ["pkexec", str(HELPER), "down"],
            check=False,
            capture_output=True,
            text=True,
        )

    # If not root, re-exec through pkexec for the file removals.
    if os.geteuid() != 0:
        result = subprocess.run(
            ["pkexec", sys.executable, str(Path(__file__).resolve()), "uninstall"],
            check=False,
            capture_output=True,
            text=True,
        )
        stdout = result.stdout.strip()
        if stdout:
            print(stdout)
            sys.exit(0 if result.returncode == 0 else 1)
        respond_ok()
        return

    # --- Running as root from here. ---
    removed: list[str] = []
    for path in [HELPER, POLKIT_RULE, NM_CONF]:
        if path.exists():
            path.unlink()
            removed.append(str(path))

    run_quiet(["nmcli", "general", "reload", "conf"])

    # Clean up the runtime directory.
    if RUN_DIR.exists():
        shutil.rmtree(RUN_DIR, ignore_errors=True)
        removed.append(str(RUN_DIR))

    respond_ok({"removed": removed})

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

COMMANDS = {
    "status": lambda args: cmd_status(),
    "start": lambda args: cmd_start(),
    "stop": lambda args: cmd_stop(),
    "set": cmd_set,
    "setup": lambda args: cmd_setup(),
    "uninstall": lambda args: cmd_uninstall(),
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        respond_error(
            "Usage: hotspot.py status|start|stop|set SSID PASSWORD BAND|setup|uninstall"
        )
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
