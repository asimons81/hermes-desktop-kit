# Hermes Desktop Kit

Field-tested desktop plugins for [Hermes Agent](https://github.com/NousResearch/hermes-agent) — built and shipped from real use, not toys.

Each plugin is a plain runtime file (or file + small Python backend) that loads into the Hermes Desktop app without touching core. Install by copying the plugin folder into `~/.hermes/desktop-plugins/` (frontend) and, where noted, the `dashboard/` folder into `~/.hermes/plugins/<id>/` (backend). The app hot-reloads frontends within seconds; backend plugins need the serve process restarted after first install.

## Plugins

| Plugin | What it does | Backend | Requires |
|---|---|---|---|
| [token-speed](token-speed/) | Live tok/s chip in the status bar for the active model | — | — |
| [approval-inbox](approval-inbox/) | "What's waiting on me" — action items, blocked kanban, failed cron, TRT drafts in one pane | Python | Local Hermes sources (env-configurable) |
| [github-pr-dashboard](github-pr-dashboard/) | Created / review-requested / closed PRs across your account | Python | `gh` CLI, authenticated |
| [typefully-q](typefully-q/) | Typefully queue calendar — reschedule, publish, delete from the desktop | Python | `TYPEFULLY_API_KEY` |
| [nes-emulator](nes-emulator/) | HerNES — play .nes ROMs you already own in a full-pane jsnes canvas (save states, OS-mute detection) | Python | — |

## Install (any single plugin)

```bash
# Frontend (all plugins)
mkdir -p ~/.hermes/desktop-plugins
cp -r <plugin> ~/.hermes/desktop-plugins/

# Backend (approval-inbox, github-pr-dashboard, typefully-q, nes-emulator only)
mkdir -p ~/.hermes/plugins/<plugin>
cp -r <plugin>/dashboard ~/.hermes/plugins/<plugin>/
```

Enable backend plugins by adding them to `plugins.enabled` in `~/.hermes/config.yaml` as a proper YAML list:

```yaml
plugins:
  enabled:
    - approval-inbox
    - github-pr-dashboard
    - typefully-q
    - nes-emulator
```

Frontend plugins load automatically from `~/.hermes/desktop-plugins/` — no config needed. If a frontend doesn't appear within a few seconds, hit `⌘K` → **Reload desktop plugins**.

Backend installs require a serve-process restart (the desktop respawns `hermes serve`). Verify each mount in `~/.hermes/logs/agent.log`: `Mounted plugin API routes: /api/plugins/<id>/`.

## Plugin index

Also check out [hermes-desktop-achievements](https://github.com/asimons81/hermes-desktop-achievements) — the achievements page + sidebar plugin, published separately.

## Development

- Frontend plugins are plain ESM, loaded uncompiled; the only import surface is `@hermes/plugin-sdk` + `react`. See the [desktop plugin SDK docs](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk).
- Backend plugins are FastAPI routers mounted by the Hermes serve process; the manifest declares `api: plugin_api.py`.
- Tests: approval-inbox ships a full pytest + ESM suite (`109` backend tests); typefully-q ships a refresh-control unit test.

## License

MIT — see [LICENSE](LICENSE).
