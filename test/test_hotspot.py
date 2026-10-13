"""Tests for the hotspot.py backend.

No root, Wi-Fi hardware, or running Omarchy shell needed. System commands
are mocked with unittest.mock so every test runs in an isolated tmpdir.
"""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import hotspot

# ---------------------------------------------------------------------------
# Subnet selection
# ---------------------------------------------------------------------------

class TestSubnetSelection:
    def test_default_when_no_conflicts(self):
        with patch("hotspot.get_routes", return_value=[]):
            assert hotspot.pick_subnet() == "10.42.0.0/24"

    def test_skips_first_when_in_use(self):
        routes = [{"dst": "10.42.0.0/24"}]
        with patch("hotspot.get_routes", return_value=routes):
            assert hotspot.pick_subnet() == "10.42.1.0/24"

    def test_skips_multiple_conflicts(self):
        routes = [
            {"dst": "10.42.0.0/24"},
            {"dst": "10.42.1.0/24"},
            {"dst": "10.42.2.0/24"},
            {"dst": "10.43.0.0/24"},
        ]
        with patch("hotspot.get_routes", return_value=routes):
            assert hotspot.pick_subnet() == "10.42.99.0/24"

    def test_ignores_default_route(self):
        routes = [{"dst": "default"}, {"dst": "10.42.0.0/24"}]
        with patch("hotspot.get_routes", return_value=routes):
            assert hotspot.pick_subnet() == "10.42.1.0/24"

    def test_handles_empty_routes(self):
        with patch("hotspot.get_routes", return_value=[]):
            assert hotspot.pick_subnet() == "10.42.0.0/24"

    def test_handles_malformed_routes(self):
        routes = [{"dst": "not-a-network"}, {}]
        with patch("hotspot.get_routes", return_value=routes):
            assert hotspot.pick_subnet() == "10.42.0.0/24"


class TestSubnetHelpers:
    def test_gateway(self):
        assert hotspot.subnet_gateway("10.42.0.0/24") == "10.42.0.1"
        assert hotspot.subnet_gateway("10.42.1.0/24") == "10.42.1.1"

    def test_prefix(self):
        assert hotspot.subnet_prefix("10.42.0.0/24") == "10.42.0"
        assert hotspot.subnet_prefix("192.168.12.0/24") == "192.168.12"


# ---------------------------------------------------------------------------
# Settings validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_ssid(self):
        assert hotspot.valid_ssid("a") is True
        assert hotspot.valid_ssid("My Network") is True
        assert hotspot.valid_ssid("a" * 32) is True
        assert hotspot.valid_ssid("Café Net") is True

    def test_invalid_ssid(self):
        assert hotspot.valid_ssid("") is False
        # 33 ASCII chars = 33 bytes > 32
        assert hotspot.valid_ssid("a" * 33) is False
        assert hotspot.valid_ssid("line\nbreak") is False

    def test_valid_password(self):
        assert hotspot.valid_pass("12345678") is True
        assert hotspot.valid_pass("a" * 63) is True
        assert hotspot.valid_pass("hunter2!@#$%") is True

    def test_invalid_password(self):
        assert hotspot.valid_pass("short") is False
        assert hotspot.valid_pass("a" * 64) is False
        assert hotspot.valid_pass("") is False


# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------

class TestSettings:
    def test_creates_on_first_run(self, isolated_env):
        settings = hotspot.ensure_settings()
        assert len(settings["password"]) == 12
        assert settings["band"] == "any"
        assert settings["ssid"].endswith("hotspot")

        # Verify file permissions (owner-only).
        mode = oct(isolated_env["settings"].stat().st_mode)[-3:]
        assert mode == "600"

    def test_reads_existing(self, isolated_env, write_settings):
        write_settings(ssid="Existing", password="mypass12345", band="5")
        settings = hotspot.ensure_settings()
        assert settings["ssid"] == "Existing"
        assert settings["password"] == "mypass12345"
        assert settings["band"] == "5"

    def test_writes_and_reads_roundtrip(self, isolated_env):
        hotspot.write_settings("Round Trip", "roundtrippass", "2.4")
        settings = hotspot.read_settings()
        assert settings == {"ssid": "Round Trip", "password": "roundtrippass", "band": "2.4"}

    def test_invalid_json_returns_empty(self, isolated_env):
        isolated_env["settings"].write_text("not json")
        settings = hotspot.read_settings()
        assert settings == {}

    def test_missing_file_returns_empty(self, isolated_env):
        isolated_env["settings"].unlink(missing_ok=True)
        settings = hotspot.read_settings()
        assert settings == {}


# ---------------------------------------------------------------------------
# JSON output helpers
# ---------------------------------------------------------------------------

class TestJsonOutput:
    def test_ok_with_data(self):
        result = json.loads(hotspot.json_ok({"foo": "bar"}))
        assert result["ok"] is True
        assert result["data"]["foo"] == "bar"

    def test_ok_without_data(self):
        result = json.loads(hotspot.json_ok())
        assert result["ok"] is True
        assert "data" not in result

    def test_error(self):
        result = json.loads(hotspot.json_error("something broke"))
        assert result["ok"] is False
        assert result["error"] == "something broke"


# ---------------------------------------------------------------------------
# Process management
# ---------------------------------------------------------------------------

class TestProcessManagement:
    def test_is_active_with_running_pid(self, isolated_env):
        # Use our own PID: we know we are running.
        pidfile = isolated_env["run_dir"] / "hostapd.pid"
        pidfile.write_text(str(os.getpid()))
        assert hotspot.is_active() is True

    def test_is_active_with_stale_pid(self, isolated_env):
        pidfile = isolated_env["run_dir"] / "hostapd.pid"
        pidfile.write_text("999999999")
        assert hotspot.is_active() is False

    def test_is_active_no_pidfile(self, isolated_env):
        assert hotspot.is_active() is False

    def test_stop_process_missing_pidfile(self, isolated_env):
        # Should not raise.
        hotspot.stop_process(isolated_env["run_dir"] / "nonexistent.pid")

    def test_stop_process_stale_pid(self, isolated_env):
        pidfile = isolated_env["run_dir"] / "test.pid"
        pidfile.write_text("999999999")
        hotspot.stop_process(pidfile)
        assert not pidfile.exists()


# ---------------------------------------------------------------------------
# Helper detection
# ---------------------------------------------------------------------------

class TestHelperDetection:
    def test_not_installed(self, isolated_env):
        assert hotspot.helper_installed() is False

    def test_installed_and_matches(self, isolated_env):
        bundled = hotspot.HERE / "omnispot-helper"
        if bundled.exists():
            isolated_env["helper"].write_bytes(bundled.read_bytes())
            with patch("shutil.which", return_value="/usr/bin/hostapd"):
                assert hotspot.helper_installed() is True

    def test_installed_but_different(self, isolated_env):
        bundled = hotspot.HERE / "omnispot-helper"
        if bundled.exists():
            isolated_env["helper"].write_text("# modified version\n")
            with patch("shutil.which", return_value="/usr/bin/hostapd"):
                assert hotspot.helper_installed() is False

    def test_installed_but_no_hostapd(self, isolated_env):
        bundled = hotspot.HERE / "omnispot-helper"
        if bundled.exists():
            isolated_env["helper"].write_bytes(bundled.read_bytes())
            with patch("shutil.which", return_value=None):
                assert hotspot.helper_installed() is False


# ---------------------------------------------------------------------------
# Band preference
# ---------------------------------------------------------------------------

class TestBandPref:
    def test_valid_bands(self):
        assert hotspot.band_pref({"band": "2.4"}) == "2.4"
        assert hotspot.band_pref({"band": "5"}) == "5"
        assert hotspot.band_pref({"band": "any"}) == "any"

    def test_invalid_defaults_to_any(self):
        assert hotspot.band_pref({"band": "bogus"}) == "any"
        assert hotspot.band_pref({}) == "any"


# ---------------------------------------------------------------------------
# Client enumeration
# ---------------------------------------------------------------------------

class TestClients:
    def test_empty_when_no_stations(self, isolated_env):
        with patch("hotspot.run_quiet") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            assert hotspot.list_clients() == []

    def test_parses_stations_with_leases(self, isolated_env):
        station_output = (
            "Station aa:bb:cc:dd:ee:01 (on ap0)\n"
            "  some info\n"
            "Station aa:bb:cc:dd:ee:02 (on ap0)\n"
        )
        with patch("hotspot.run_quiet") as mock_run:
            mock_run.return_value = MagicMock(stdout=station_output, returncode=0)
            lease_file = isolated_env["run_dir"] / "leases"
            lease_file.write_text(
                "1 aa:bb:cc:dd:ee:01 10.42.0.20 pixel *\n"
                "1 aa:bb:cc:dd:ee:02 10.42.0.21 * *\n"
            )
            clients = hotspot.list_clients()
            assert len(clients) == 2
            assert clients[0] == {"mac": "aa:bb:cc:dd:ee:01", "ip": "10.42.0.20", "name": "pixel"}
            assert clients[1] == {"mac": "aa:bb:cc:dd:ee:02", "ip": "10.42.0.21", "name": ""}


# ---------------------------------------------------------------------------
# Random password generation
# ---------------------------------------------------------------------------

class TestRandomPassword:
    def test_length(self):
        pw = hotspot.random_password()
        assert len(pw) == 12

    def test_custom_length(self):
        pw = hotspot.random_password(length=20)
        assert len(pw) == 20

    def test_is_valid(self):
        for _ in range(50):
            pw = hotspot.random_password()
            assert hotspot.valid_pass(pw)

    def test_no_ambiguous_chars(self):
        # The safe charset excludes 0, O, l, I, 1 to avoid confusion.
        for _ in range(100):
            pw = hotspot.random_password()
            for ch in "0OlI1":
                assert ch not in pw


# ---------------------------------------------------------------------------
# Regulatory country and 5 GHz channel selection
# ---------------------------------------------------------------------------

class TestChannels:
    OUT = (
        "Band 2:\n"
        "\t* 5180 MHz [36]\n"
        "\t  No IR\n"
        "\t* 5745 MHz [149]\n"
        "\t  Maximum TX power: 22.0 dBm\n"
        "Band 4:\n"
        "\t* 5955 MHz [1]\n"
    )

    def _pick(self, out):
        with patch("hotspot.phy_of", return_value="phy0"), patch("hotspot.run_quiet") as m:
            m.return_value = MagicMock(returncode=0, stdout=out)
            return hotspot.pick_5ghz_channel("wlo1")

    def test_picks_unrestricted(self):
        assert self._pick(self.OUT) == "149"

    def test_ignores_6ghz(self):
        assert self._pick("\t* 5955 MHz [1]\n") == ""

    def test_none_when_all_restricted(self):
        assert self._pick("\t* 5180 MHz [36]\n\t  No IR\n") == ""

    def test_none_without_phy(self):
        with patch("hotspot.phy_of", return_value=""):
            assert hotspot.pick_5ghz_channel("wlo1") == ""


# ---------------------------------------------------------------------------
# Command logic: cmd_set and cmd_start
# ---------------------------------------------------------------------------

class TestCommandLogic:
    def test_cmd_set_match_preserves_band(self, isolated_env, write_settings):
        write_settings(ssid="OldNet", password="oldpassword", band="5")
        with patch("hotspot.is_active", return_value=False), patch("sys.exit") as mock_exit:
            hotspot.cmd_set(["NewNet", "newpassword123", "match"])
            mock_exit.assert_called_with(0)
        s = hotspot.read_settings()
        assert s["ssid"] == "NewNet"
        assert s["band"] == "5"

    def test_cmd_start_translates_channel_error(self, isolated_env):
        with patch("hotspot.helper_installed", return_value=True), \
             patch("hotspot.wifi_radio_on", return_value=True), \
             patch("hotspot.wifi_device", return_value="wlo1"), \
             patch("hotspot.station_connection", return_value=""), \
             patch("subprocess.run") as mock_sub:
            mock_sub.return_value = MagicMock(
                returncode=1,
                stderr="hostapd: ap0: IEEE 802.11 Hardware does not support configured channel\n",
            )
            with patch("hotspot.respond_error", side_effect=SystemExit(1)) as mock_err:
                try:
                    hotspot.cmd_start()
                except SystemExit:
                    pass
                mock_err.assert_called_once()
                err_msg = mock_err.call_args[0][0]
                assert "regulatory restriction" in err_msg



class TestStartGuards:
    def _start(self, **extra):
        patches = {"hotspot.helper_installed": True, "hotspot.wifi_device": "wlo1"}
        patches.update(extra)
        with patch("hotspot.respond_error", side_effect=SystemExit(1)) as err:
            from contextlib import ExitStack
            with ExitStack() as st:
                for k, v in patches.items():
                    st.enter_context(patch(k, return_value=v))
                try:
                    hotspot.cmd_start()
                except SystemExit:
                    pass
            return err.call_args[0][0]

    def test_radio_off(self, isolated_env):
        assert "turned off" in self._start(**{"hotspot.wifi_radio_on": False})

    def test_no_5ghz_channel(self, isolated_env, write_settings):
        write_settings(band="5")
        msg = self._start(**{"hotspot.wifi_radio_on": True, "hotspot.station_connection": "", "hotspot.pick_5ghz_channel": ""})
        assert "5 GHz" in msg


class TestSetViaEnvironment:
    def test_env_secrets(self, isolated_env, monkeypatch):
        monkeypatch.setenv("OMNISPOT_SSID", "EnvNet")
        monkeypatch.setenv("OMNISPOT_PASS", "envpassword1")
        with patch("hotspot.is_active", return_value=False), patch("sys.exit"):
            hotspot.cmd_set(["2.4"])
        s = hotspot.read_settings()
        assert (s["ssid"], s["password"], s["band"]) == ("EnvNet", "envpassword1", "2.4")
