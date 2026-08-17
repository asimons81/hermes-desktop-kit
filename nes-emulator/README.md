# HerNES — NES emulator for Hermes Desktop

![HerNES — NES emulator for Hermes Desktop](assets/hernes-readme-hero.png)

Named HerNES — shoutout to [@leviath666](https://x.com/leviath666) on X for the name idea.

Play `.nes` ROMs you already own in a full-pane jsnes canvas inside the Hermes Desktop app. Save states, keyboard controls, and a local folder scan are all bundled in.

## Features

- Full-pane 256×240 jsnes canvas in a desktop plugin pane
- Save and load states (per-game, per slot)
- Local folder scan to discover ROMs in a directory you choose
- OS-mute detection so the emulator pauses/respects system audio state
- Branded menu art when no game is loaded (inlined into `plugin.js` at build time)
- Keyboard controls:
  - Arrows = D-pad
  - X / A = jump
  - Z / S = run
  - Enter = Start
  - Right Shift = Select
  - Q / W = turbo

## Install

1. Copy the frontend plugin into the Hermes Desktop plugins folder:

   ```bash
   mkdir -p ~/.hermes/desktop-plugins
   cp -r nes-emulator ~/.hermes/desktop-plugins/
   ```

2. Copy the backend dashboard into the Hermes plugins folder:

   ```bash
   mkdir -p ~/.hermes/plugins/nes-emulator
   cp -r nes-emulator/dashboard ~/.hermes/plugins/nes-emulator/
   ```

3. Enable the backend plugin in `~/.hermes/config.yaml` as a YAML list entry:

   ```yaml
   plugins:
     enabled:
       - nes-emulator
   ```

4. Reload desktop plugins from the Hermes app with `⌘K` → **Reload desktop plugins**, or restart the desktop app. The backend serve process needs a restart after the first install (it respawns automatically).

## Legal posture

HerNES ships no ROMs and includes no ROM downloader. It plays only `.nes` ROMs the user already owns.

## License

- Plugin code: MIT — see the kit-wide [LICENSE](../LICENSE).
- Vendored jsnes v2.1.0 is included inline under the Apache License 2.0 — see [NOTICE.md](NOTICE.md).
