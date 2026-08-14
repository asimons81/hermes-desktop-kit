# Typefully Q

The Typefully queue calendar as a full page in the Hermes Desktop: a time-grid week view (Google Calendar style) of scheduled drafts and free slots. Click a draft for the detail drawer — reschedule into a free slot, publish now, or delete.

## Install

Two parts (frontend + backend):

```bash
# 1. Frontend
mkdir -p ~/.hermes/desktop-plugins
cp -r typefully-q ~/.hermes/desktop-plugins/

# 2. Backend
mkdir -p ~/.hermes/plugins/typefully-q
cp -r typefully-q/dashboard ~/.hermes/plugins/typefully-q/
```

Then add to `plugins.enabled` in `~/.hermes/config.yaml` and restart the backend if it was already serving. Verify the mount with `Mounted plugin API routes: /api/plugins/typefully-q/` in `~/.hermes/logs/agent.log`.

## Requirements

- A **Typefully API key** (`TYPEFULLY_API_KEY`) — loaded from `~/.hermes/.env` by the serve process. Get one from your Typefully account settings.
- The backend proxies to the Typefully API v2 (`https://api.typefully.com/v2`).

## Routes

| Route | Purpose |
|---|---|
| `GET /health` | Liveness probe |
| `GET /social-set` | Default social set id + account info |
| `GET /queue?start_date=&end_date=` | Queue timeline for a date range |
| `GET /drafts?status=&limit=&offset=` | Draft list |
| `GET /draft/<id>` | Single draft detail |
| `POST /draft` | Create a draft |
| `PATCH /draft/<id>` | Update a draft |
| `DELETE /draft/<id>` | Delete a draft |
| `GET /queue/schedule` | Queue schedule rules |
| `GET /analytics/x/posts` | X post analytics (read-only) |
| `GET /analytics/x/followers` | X follower series (read-only) |

Note: unlike the other plugins in this kit, Typefully Q **can write** (create/update/delete drafts, publish) — that's its purpose. Actions go straight to Typefully under your own API key.

## Tests

```bash
node tests/refresh-control.test.mjs   # refresh-control unit test
```
