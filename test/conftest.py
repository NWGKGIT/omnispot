"""pytest fixtures and stubs for hotspot backend tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure lib directory is on sys.path for test execution
lib_dir = str(Path(__file__).resolve().parent.parent / "lib")
if lib_dir not in sys.path:
    sys.path.insert(0, lib_dir)


@pytest.fixture
def isolated_env(tmp_path: Path):
    """Set up a fully isolated environment for testing.

    Patches all path constants in the hotspot module so nothing touches
    the real filesystem, NetworkManager, or system binaries.
    """
    config_dir = tmp_path / "config" / "omarchy" / "hotspot"
    run_dir = tmp_path / "run"
    helper = tmp_path / "helper"

    config_dir.mkdir(parents=True)
    run_dir.mkdir(parents=True)

    patches = {
        "hotspot.CONFIG_DIR": config_dir,
        "hotspot.SETTINGS": config_dir / "settings.json",
        "hotspot.RUN_DIR": run_dir,
        "hotspot.HELPER": helper,
        "hotspot.POLKIT_RULE": tmp_path / "polkit.rules",
        "hotspot.NM_CONF": tmp_path / "nm.conf",
        "hotspot.HERE": Path(__file__).resolve().parent.parent,
    }

    stack = []
    for target, value in patches.items():
        p = patch(target, value)
        p.start()
        stack.append(p)

    yield {
        "tmp": tmp_path,
        "config_dir": config_dir,
        "settings": config_dir / "settings.json",
        "run_dir": run_dir,
        "helper": helper,
    }

    for p in stack:
        p.stop()


@pytest.fixture
def write_settings(isolated_env):
    """Helper to write a settings.json file in the test environment."""
    def _write(ssid: str = "TestNet", password: str = "testpass123", band: str = "any"):
        data = {"ssid": ssid, "password": password, "band": band}
        isolated_env["settings"].write_text(json.dumps(data))
    return _write
