# Approval Inbox

"What's waiting on me" — a read-mostly Hermes Desktop pane aggregating local attention sources into one page + sidebar row + status bar chip.

Aggregates: open action items, blocked/todo kanban cards, recently-failed cron runs, and TRT blocked-draft markers. Snooze / hide / restore are local-only view state — nothing is written back to sources.

## Install

Two parts (frontend + backend):

```bash
# 1. Frontend
mkdir -p ~/.hermes/desktop-plugins
cp -r approval-inbox ~/.hermes/desktop-plugins/

# 2. Backend
mkdir -p ~/.hermes/plugins/approval-inbox
cp -r approval-inbox/dashboard ~/.hermes/plugins/approval-inbox/
```

Then add to `plugins.enabled` in `~/.hermes/config.yaml` (proper YAML list, not a string), and restart the backend if it was already serving (`kill -9` the `hermes serve` child; the desktop respawns it). Verify the mount with `Mounted plugin API routes: /api/plugins/approval-inbox/` in `~/.hermes/logs/agent.log`.

## Configuration

All source paths default to home-relative locations and are overridable via `APPROVAL_INBOX_*` env vars:

| Env var | Default (relative to `~`) |
|---|---|
| `APPROVAL_INBOX_ACTION_ITEMS` | `nexus-wiki/ops/state/action-items.json` |
| `APPROVAL_INBOX_TASK_LEDGER` | `nexus-wiki/ops/state/task-ledger.json` |
| `APPROVAL_INBOX_KANBAN_DIR` | `.hermes/kanban/boards` |
| `APPROVAL_INBOX_CRON_EXECUTIONS_DB` | `.hermes/cron/executions.db` |
| `APPROVAL_INBOX_CRON_JOBS` | `.hermes/cron/jobs.json` |
| `APPROVAL_INBOX_TRT_DIR` | `projects/trt-editorial-ops/drafts` |
| `APPROVAL_INBOX_TRT_RECEIPTS` | `nexus-wiki/ops/evidence/trt-editor/receipts` |
| `APPROVAL_INBOX_TRT_STAGING_RECEIPTS` | `projects/trt-editorial-ops/ops/evidence/trt-editor/receipts` |
| `APPROVAL_INBOX_CRON_FAIL_WINDOW_DAYS` | `14` |

## Security / design contract

- **No mutation routes.** Only GET endpoints; FastAPI returns 405 for any other method.
- SQLite sources open `mode=ro`; the router never creates schema.
- Per-source fail-soft: a missing/locked source yields `{count: 0, error}` in its section instead of 500ing the whole request.
- User actions (snooze/hide/restore) are LOCAL ONLY via `ctx.storage` — nothing is written back to the sources.

## Tests

```bash
env -u PYTHONPATH python -m pytest tests -q     # backend: 109 tests
node tests/esm-registration.mjs                 # frontend registration
HERMES_AGENT_NODE_MODULES=<desktop app node_modules> node tests/esm-render.mjs  # SSR render
```
