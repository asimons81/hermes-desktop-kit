"""Regression: plugin_api.py must import under the real Hermes web_server loader.

The web_server mounts plugin APIs with
``importlib.util.spec_from_file_location(...)`` and does NOT add the
plugin's dashboard/ dir to ``sys.path`` (see hermes_cli/web_server.py
_mount_plugin_api_routes). A single-file plugin imports fine; a plugin
whose api file imports sibling modules (attention_model, attention_rules)
fails with ``No module named 'attention_model'`` unless the api file
itself makes its own directory importable.

This test simulates that loader contract exactly: a subprocess with a
clean sys.path and a neutral cwd loads plugin_api.py via
spec_from_file_location and must succeed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
API_FILE = DASHBOARD / "plugin_api.py"


def test_plugin_api_imports_under_web_server_loader() -> None:
    """Simulate the real loader: no dashboard dir on sys.path, neutral cwd."""
    loader_script = (
        "import importlib.util, sys\n"
        "from pathlib import Path\n"
        f"api_path = Path({str(API_FILE)!r})\n"
        "spec = importlib.util.spec_from_file_location('hermes_dashboard_plugin_approval-inbox', api_path)\n"
        "assert spec is not None and spec.loader is not None\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "sys.modules[spec.name] = mod\n"
        "spec.loader.exec_module(mod)\n"
        "assert getattr(mod, 'router', None) is not None, 'no router attribute'\n"
        "print('OK: imported under web_server loader contract')\n"
    )
    # Neutral cwd + only the venv's stdlib path, mirroring the serve process
    # (which never has the plugin dashboard dir on sys.path).
    proc = subprocess.run(
        [sys.executable, "-c", loader_script],
        cwd="/tmp",
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert proc.returncode == 0, (
        f"plugin_api.py failed to import under the web_server loader contract\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert "OK: imported" in proc.stdout
