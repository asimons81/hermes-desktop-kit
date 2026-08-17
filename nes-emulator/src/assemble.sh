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
# Idle-menu art: plugin.js is import()ed from a blob: URL (no base path), so the
# menu background is inlined as a data URL. The readable source of truth stays
# at src/assets/hernes-menu-bg.webp (L3 scan covers only the post-import body,
# so this prefix var stays outside it).
MENU_BG_B64=$(base64 "$WS/src/assets/hernes-menu-bg.webp" | tr -d '\n')
printf "\nvar HERMES_NES_MENU_BG = 'data:image/webp;base64,%s';\n" "$MENU_BG_B64" >> "$TMP"
cat "$WS/src/body.js" >> "$TMP"
printf '\n' >> "$TMP"
cat "$WS/src/body2.js" >> "$TMP"

mkdir -p "$DEP"
cp "$TMP" "$DEP/plugin.js"
cp "$TMP" "$WS/plugin.js"
rm -f "$TMP"

echo "assembled plugin.js ($(wc -c < "$DEP/plugin.js") bytes)"
node --check "$DEP/plugin.js" && echo "node --check: OK"
