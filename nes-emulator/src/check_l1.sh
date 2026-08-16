#!/usr/bin/env bash
# L1 backend gates + deployed-vs-workspace parity.
set -uo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WS=$(dirname "$SCRIPT_DIR")
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
DEP=$HERMES_HOME/plugins/nes-emulator/dashboard
PY=$HERMES_HOME/hermes-agent/venv/bin/python
[ -x "$PY" ] || PY=python3

echo "=== py_compile (workspace) ==="
"$PY" -m py_compile "$WS/dashboard/plugin_api.py" && echo OK
echo "=== json.tool (manifest) ==="
"$PY" -m json.tool "$WS/dashboard/manifest.json" > /dev/null && echo OK
echo "=== deployed == workspace (api + manifest) ==="
diff -q "$WS/dashboard/plugin_api.py" "$DEP/plugin_api.py" && echo "api: match"
diff -q "$WS/dashboard/manifest.json" "$DEP/manifest.json" && echo "manifest: match"
echo "=== ruff (workspace dashboard) ==="
env -u PYTHONPATH "$PY" -m ruff check "$WS/dashboard" 2>&1 | tail -5
