#!/usr/bin/env bash
# L3: theme-var-only scan of the plugin's OWN code (exclude the vendored jsnes
# blob, which is the section before the body's first `import {` line).
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WS=$(dirname "$SCRIPT_DIR")
HERMES_HOME=${HERMES_HOME:-$HOME/.hermes}
PLUGIN=$WS/plugin.js
BODY_START=$(grep -n '^import {' "$PLUGIN" | head -1 | cut -d: -f1)
BODY=$(sed -n "${BODY_START},\$p" "$PLUGIN")
echo "=== hardcoded colors in plugin body (should be none) ==="
printf '%s\n' "$BODY" | grep -nE '#[0-9a-fA-F]{3,8}\b|rgba?\(|black\b|white\b' || echo 'none'
echo "=== shipped ROMs (should be none; roms/ user library excluded) ==="
find "$HERMES_HOME/desktop-plugins/nes-emulator" "$HERMES_HOME/plugins/nes-emulator" \
  -type d -name roms -prune -o -name '*.nes' -print 2>/dev/null || echo 'none'
