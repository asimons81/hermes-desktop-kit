# Token Speed

A live tokens-per-second chip for the Hermes Desktop status bar.

Shows the active model's streaming throughput as `124 tok/s` (or `-- tok/s` when idle) in the right status bar cluster. Toggleable via the status bar's right-click menu → **Token speed**.

## Install

Copy the folder into your desktop plugins directory:

```bash
mkdir -p ~/.hermes/desktop-plugins
cp -r token-speed ~/.hermes/desktop-plugins/
```

The desktop app hot-reloads the plugin within a few seconds. If it doesn't appear, hit `⌘K` → **Reload desktop plugins**.

## How it works

- Listens to the live gateway stream (`host.onEvent`) for `message.delta` / `reasoning.delta` text deltas.
- Estimates tokens from characters (~4 chars/token) because the gateway emits text deltas without per-frame token counts.
- Computes a smoothed rolling tok/s over a trailing 5s window (EMA α=0.35).
- Returns `-- tok/s` when idle (no stream for >4s) or when no samples exist.

## Caveat

This is a **text-based estimate**, not an exact TPS readout. If the gateway ever emits real per-frame token counts, the provider becomes exact with a tiny patch.
