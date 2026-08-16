#!/usr/bin/env bash
# Assemble plugin.js from src parts + vendored jsnes (chunked, truncation-safe).
# Output: $HERMES_HOME/desktop-plugins/nes-emulator/plugin.js (deployed)
# and   : $WS/plugin.js (source copy).
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WS=$(dirname "$SCRIPT_DIR")
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
DEP=$HERMES_HOME/desktop-plugins/nes-emulator
TMP="$WS/.plugin.js.tmp"

cat "$WS/src/header.txt" > "$TMP"
cat "$WS/jsnes.min.js" >> "$TMP"
printf '\n' >> "$TMP"
cat "$WS/src/body.js" >> "$TMP"
printf '\n' >> "$TMP"
cat "$WS/src/body2.js" >> "$TMP"

mkdir -p "$DEP"
cp "$TMP" "$DEP/plugin.js"
cp "$TMP" "$WS/plugin.js"
rm -f "$TMP"

echo "assembled plugin.js ($(wc -c < "$DEP/plugin.js") bytes)"
node --check "$DEP/plugin.js" && echo "node --check: OK"
