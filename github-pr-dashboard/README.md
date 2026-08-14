# GitHub Pull Requests

Account-wide pull requests dashboard for the Hermes Desktop: **Created / Review requested / Closed** views backed by your authenticated `gh` CLI. Read-only.

## Install

Two parts (frontend + backend):

```bash
# 1. Frontend
mkdir -p ~/.hermes/desktop-plugins
cp -r github-pr-dashboard ~/.hermes/desktop-plugins/

# 2. Backend
mkdir -p ~/.hermes/plugins/github-pr-dashboard
cp -r github-pr-dashboard/dashboard ~/.hermes/plugins/github-pr-dashboard/
```

Then add to `plugins.enabled` in `~/.hermes/config.yaml` and restart the backend if it was already serving. Verify the mount with `Mounted plugin API routes: /api/plugins/github-pr-dashboard/` in `~/.hermes/logs/agent.log`.

## Requirements

- The `gh` CLI installed, authenticated, and with the scopes needed to read the repos you care about (`repo` for private repos, default scopes for public).
- The backend shells out to `gh` with fixed argument arrays — no shell strings, no writes, no new dependencies.

## Security / design contract

- **Read-only.** Every operation shells out to the user's authenticated `gh` CLI; no mutation routes, no credential writes.
- Response shapes match the core Hermes implementation so the UI can ship independently of any upstream PR.

## Routes

| Route | Purpose |
|---|---|
| `GET /list?kind=created|review-requested&state=open|closed&limit=N` | PR lists |
| `GET /health` | Liveness probe |

## Tests

Backend tests live under `tests/` (see approval-inbox for the pytest pattern). Run from the plugin dir:

```bash
env -u PYTHONPATH python -m pytest tests -q
```
