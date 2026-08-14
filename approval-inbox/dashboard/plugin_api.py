"""Approval Inbox dashboard plugin — backend API routes.

Mounted at /api/plugins/approval-inbox/ by the Hermes dashboard plugin
system (manifest.json declares api=plugin_api.py; the plugin must be in
plugins.enabled in config.yaml for the serve process to import it).

Read-mostly aggregation answering "what's waiting on me":

    GET /health          Liveness probe.
    GET /attention       V2 normalized attention envelope (primary queue,
                         secondary buckets, per-source health, counts —
                         the operator badge input is counts.human_now).
    GET /overview        Lightweight raw counts for the statusbar chip
                         (actionItems, kanbanBlocked, kanbanTodo,
                         cronFailed, trtBlocked + grand total) — DIAGNOSTIC.
    GET /action-items    Open items from the nexus-wiki action-items.json
                         — DIAGNOSTIC.
    GET /kanban          Blocked + todo cards across every board under
                         the kanban boards dir — DIAGNOSTIC.
    GET /cron            Jobs whose most recent execution failed (or whose
                         registry last_status is error), with names from
                         jobs.json — DIAGNOSTIC.
    GET /trt-blocked     TRT editorial *.blocked.md draft markers
                         — DIAGNOSTIC.

Security / design contract
--------------------------
* READ-ONLY, fail-closed: there are NO mutation routes (no POST/PUT/PATCH/
  DELETE). FastAPI returns 405 for a wrong method on a known path and 404
  for unknown paths — nothing in this router can write, merge, publish,
  spend, or touch credentials.
* SQLite sources are opened with ``mode=ro`` URIs. This router never calls
  ``kanban_db.init_db`` or any schema-creating helper — a fresh/missing
  database simply degrades that section.
* Per-source fail-soft: every data endpoint returns a section envelope
  ``{count, items, error}``. If one source is missing or unreadable the
  section reports ``error`` and ``count: 0`` instead of 500ing the whole
  request, so the desktop chip stays alive even when a file is transiently
  locked.
* Paths are env-overridable (APPROVAL_INBOX_*), defaulting to the live
  local sources — which also lets the unit tests point the router at
  temp fixtures.
* HTTP auth is handled by the dashboard's session-token middleware, same
  as the in-tree kanban plugin; this router does not reimplement it.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter

# The Hermes web_server mounts plugin APIs with
# ``importlib.util.spec_from_file_location`` and does NOT add this
# plugin's dashboard/ directory to sys.path. Without this self-registration,
# sibling imports below fail at serve time with
# ``No module named 'attention_model'`` even though tests (which insert the
# path themselves) pass. Registering our own directory keeps the plugin
# importable under the real loader contract. This is a no-op when the dir is
# already on sys.path (e.g. under pytest).
_PLUGIN_DIR = str(Path(__file__).resolve().parent)
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

import attention_model
import attention_rules

log = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Path resolution (env-overridable for tests and other installs; defaults are
# home-relative so the plugin works on any machine without leaking the
# original author's username/layout). Set APPROVAL_INBOX_* env vars to point
# at your own sources — the defaults below are the author's live sources.
# ---------------------------------------------------------------------------

_HOME = Path.home()


def _home_default(rel: str) -> str:
    return str(_HOME / rel)


DEFAULTS = {
    "APPROVAL_INBOX_ACTION_ITEMS": _home_default("nexus-wiki/ops/state/action-items.json"),
    "APPROVAL_INBOX_TASK_LEDGER": _home_default("nexus-wiki/ops/state/task-ledger.json"),
    "APPROVAL_INBOX_KANBAN_DIR": _home_default(".hermes/kanban/boards"),
    "APPROVAL_INBOX_CRON_EXECUTIONS_DB": _home_default(".hermes/cron/executions.db"),
    "APPROVAL_INBOX_CRON_JOBS": _home_default(".hermes/cron/jobs.json"),
    "APPROVAL_INBOX_TRT_DIR": _home_default("projects/trt-editorial-ops/drafts"),
    "APPROVAL_INBOX_TRT_RECEIPTS": _home_default("nexus-wiki/ops/evidence/trt-editor/receipts"),
    "APPROVAL_INBOX_TRT_STAGING_RECEIPTS": _home_default("projects/trt-editorial-ops/ops/evidence/trt-editor/receipts"),
    # How far back a failed cron run still counts as "recent attention".
    "APPROVAL_INBOX_CRON_FAIL_WINDOW_DAYS": "14",
}


def _env(name: str) -> str:
    return os.environ.get(name, DEFAULTS[name])


def _action_items_path() -> Path:
    return Path(_env("APPROVAL_INBOX_ACTION_ITEMS"))


def _task_ledger_path() -> Path:
    return Path(_env("APPROVAL_INBOX_TASK_LEDGER"))


def _kanban_dir() -> Path:
    return Path(_env("APPROVAL_INBOX_KANBAN_DIR"))


def _cron_executions_db() -> Path:
    return Path(_env("APPROVAL_INBOX_CRON_EXECUTIONS_DB"))


def _cron_jobs_path() -> Path:
    return Path(_env("APPROVAL_INBOX_CRON_JOBS"))


def _trt_dir() -> Path:
    return Path(_env("APPROVAL_INBOX_TRT_DIR"))


def _trt_receipts_dir() -> Path:
    return Path(_env("APPROVAL_INBOX_TRT_RECEIPTS"))


def _trt_staging_receipts_dir() -> Path:
    return Path(_env("APPROVAL_INBOX_TRT_STAGING_RECEIPTS"))


def _fail_window_days() -> int:
    try:
        return max(1, int(_env("APPROVAL_INBOX_CRON_FAIL_WINDOW_DAYS")))
    except ValueError:
        return 14


# ---------------------------------------------------------------------------
# Section helpers — each returns a {count, items, error} envelope
# ---------------------------------------------------------------------------

def _section(items: list[dict[str, Any]], error: str | None = None) -> dict[str, Any]:
    return {"count": len(items), "items": items, "error": error}


def _read_json(path: Path) -> Any:
    """Read a small JSON file, raising a descriptive error on failure."""
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _open_ro(db_path: Path) -> sqlite3.Connection:
    """Open a SQLite database strictly read-only (URI mode)."""
    if not db_path.exists():
        raise FileNotFoundError(f"{db_path} does not exist")
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)


def _iso_age_hours(iso: str | None) -> float | None:
    """Age in hours of an ISO-8601 timestamp, or None when unparsable."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds() / 3600.0, 1)
    except (ValueError, TypeError):
        return None


def _collect_action_items() -> dict[str, Any]:
    try:
        data = _read_json(_action_items_path())
    except Exception as exc:  # noqa: BLE001 — section fail-soft
        log.warning("approval-inbox action-items unreadable: %s", exc)
        return _section([], f"action-items.json unreadable: {exc}")

    items: list[dict[str, Any]] = []
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") not in (None, "open"):
            continue
        item_id = str(raw.get("id", ""))
        created = raw.get("created") or raw.get("created_at")
        items.append(
            {
                "key": f"action:{item_id}",
                "id": item_id,
                "text": raw.get("text", ""),
                "artifact": raw.get("artifact", ""),
                "blocker": raw.get("blocker", ""),
                "created": created,
                "age_hours": _iso_age_hours(created),
                "status": raw.get("status", "open"),
            }
        )
    # Newest first.
    items.sort(key=lambda it: it.get("age_hours") if it.get("age_hours") is not None else -1, reverse=True)
    return _section(items)


def _collect_kanban() -> dict[str, Any]:
    base = _kanban_dir()
    if not base.exists():
        return _section([], f"kanban boards dir {base} does not exist")

    items: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        board_dirs = sorted(p for p in base.iterdir() if p.is_dir() and p.name != "_archived")
    except OSError as exc:
        return _section([], f"cannot list kanban boards: {exc}")

    for board_dir in board_dirs:
        db_path = board_dir / "kanban.db"
        if not db_path.exists():
            continue
        board = board_dir.name
        try:
            with _open_ro(db_path) as conn:
                rows = conn.execute(
                    "SELECT id, title, status, assignee, priority, created_at "
                    "FROM tasks WHERE status IN ('blocked', 'todo') "
                    "ORDER BY CASE status WHEN 'blocked' THEN 0 ELSE 1 END, priority DESC, created_at"
                ).fetchall()
        except sqlite3.Error as exc:
            errors.append(f"{board}: {exc}")
            continue
        for row in rows:
            task_id, title, status, assignee, priority, created_at = row
            items.append(
                {
                    "key": f"kanban:{board}:{task_id}",
                    "board": board,
                    "task_id": task_id,
                    "title": title,
                    "status": status,
                    "assignee": assignee,
                    "priority": priority,
                    "created_at": created_at,
                }
            )

    blocked = sum(1 for it in items if it["status"] == "blocked")
    todo = sum(1 for it in items if it["status"] == "todo")
    section = _section(items, "; ".join(errors) or None)
    section["blocked"] = blocked
    section["todo"] = todo
    return section


def _collect_cron() -> dict[str, Any]:
    """Jobs needing attention: latest execution failed, or registry error."""
    window_days = _fail_window_days()
    exec_db = _cron_executions_db()
    jobs_path = _cron_jobs_path()

    section_errors: list[str] = []

    # job_id -> latest failed run info from executions.db (any time).
    exec_failed: dict[str, dict[str, Any]] = {}
    if not exec_db.exists():
        section_errors.append(f"cron executions db {exec_db} does not exist")
    else:
        try:
            with _open_ro(exec_db) as conn:
                # Most recent execution per job (claimed_at is ISO text, sorts lexically).
                rows = conn.execute(
                    "SELECT job_id, status, started_at, finished_at, error "
                    "FROM executions e "
                    "WHERE claimed_at = (SELECT MAX(claimed_at) FROM executions e2 WHERE e2.job_id = e.job_id)"
                ).fetchall()
            for job_id, status, started_at, finished_at, error in rows:
                if status == "failed":
                    exec_failed[job_id] = {
                        "status": "failed",
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "error": error,
                    }
        except sqlite3.Error as exc:
            log.warning("approval-inbox cron executions unreadable: %s", exc)
            section_errors.append(f"cron executions db unreadable: {exc}")

    # Registry view: name + last_status.
    registry: dict[str, dict[str, Any]] = {}
    if not jobs_path.exists():
        section_errors.append(f"cron jobs registry {jobs_path} does not exist")
    else:
        try:
            data = _read_json(jobs_path)
            for job in data.get("jobs", []) if isinstance(data, dict) else []:
                if isinstance(job, dict) and job.get("id"):
                    registry[str(job["id"])] = job
        except Exception as exc:  # noqa: BLE001 — section fail-soft
            log.warning("approval-inbox cron jobs.json unreadable: %s", exc)
            section_errors.append(f"cron jobs registry unreadable: {exc}")

    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _within_window(age_h: float | None) -> bool:
        # Unknown age counts as recent (nothing to compare against).
        return age_h is None or age_h <= window_days * 24

    # 1) Executions whose latest run failed, within the recent window.
    for job_id, info in exec_failed.items():
        started = info.get("started_at")
        age_h = _iso_age_hours(started)
        if not _within_window(age_h):
            continue
        seen.add(job_id)
        reg = registry.get(job_id, {})
        items.append(
            {
                "key": f"cron:{job_id}",
                "job_id": job_id,
                "name": reg.get("name") or job_id,
                "enabled": reg.get("enabled", True),
                "status": "failed",
                "last_run_at": started,
                "last_error": (info.get("error") or "")[:300],
                "age_hours": age_h,
                "recent_window_days": window_days,
            }
        )

    # 2) Registry jobs flagged error but absent from executions (e.g. fresh),
    #    also within the recent window.
    if jobs_path.exists():
        try:
            data = _read_json(jobs_path)
            for job in data.get("jobs", []) if isinstance(data, dict) else []:
                if not isinstance(job, dict):
                    continue
                job_id = str(job.get("id", ""))
                if job_id in seen:
                    continue
                last_status = job.get("last_status")
                if last_status != "error":
                    continue
                last_error = job.get("last_error") or ""
                last_run = job.get("last_run_at")
                age_h = _iso_age_hours(last_run)
                if not _within_window(age_h):
                    continue
                seen.add(job_id)
                items.append(
                    {
                        "key": f"cron:{job_id}",
                        "job_id": job_id,
                        "name": job.get("name") or job_id,
                        "enabled": job.get("enabled", True),
                        "status": "error",
                        "last_run_at": last_run,
                        "last_error": (last_error or "")[:300],
                        "age_hours": age_h,
                        "recent_window_days": window_days,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("approval-inbox cron registry merge failed: %s", exc)

    items.sort(key=lambda it: it.get("age_hours") if it.get("age_hours") is not None else 1e9)
    return _section(items, "; ".join(section_errors) or None)


def _collect_trt_blocked() -> dict[str, Any]:
    base = _trt_dir()
    if not base.exists():
        return _section([], f"TRT drafts dir {base} does not exist")
    try:
        markers = sorted(base.glob("*.blocked.md"))
    except OSError as exc:
        return _section([], f"cannot list TRT blocked markers: {exc}")

    items: list[dict[str, Any]] = []
    for path in markers:
        try:
            stat = path.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime = None
        items.append(
            {
                "key": f"trt:{path.name}",
                "path": str(path),
                "name": path.name,
                "mtime": mtime,
                "size": stat.st_size if "stat" in locals() else None,
            }
        )
    # Oldest-stuck first (biggest mtime age = most stale).
    items.sort(key=lambda it: it.get("mtime") or "", reverse=False)
    return _section(items)


# ---------------------------------------------------------------------------
# V2 adapters — normalize source-native records into candidates (Card C)
#
# Each adapter returns {"ok", "error", "candidates"}. Candidates preserve
# full provenance (source, source_key, source_keys, key, fingerprint,
# canonical §8.1 fields, anchors, native record, evidence, timestamps,
# authority evidence) and apply ONLY source-provable pre-classification
# suppression codes (§7). Actionability/attention-class/owner resolution is
# Card D's job — adapters never classify.
# ---------------------------------------------------------------------------

def _finalize(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate, log-and-drop invalid candidates, and sort deterministically."""
    out: list[dict[str, Any]] = []
    for c in candidates:
        errors = attention_model.validate_candidate(c)
        if errors:
            log.warning("approval-inbox dropped invalid candidate %s: %s",
                        c.get("source_key"), errors)
            continue
        out.append(c)
    out.sort(key=lambda c: (c["source_key"], c["key"]))
    return out


def _iso_from_ts(value: Any) -> str | None:
    """Convert a unix epoch int (kanban.created_at) to ISO-8601, or pass ISO through."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return s


def adapt_action_items() -> dict[str, Any]:
    """Normalize open + terminal action-items.json records into candidates."""
    try:
        data = _read_json(_action_items_path())
    except Exception as exc:  # noqa: BLE001 — section fail-soft
        log.warning("approval-inbox action-items unreadable: %s", exc)
        return {"ok": False, "error": f"action-items.json unreadable: {exc}", "candidates": []}

    candidates: list[dict[str, Any]] = []
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id", "")).strip()
        if not item_id:
            continue
        status = raw.get("status") or "open"
        artifact = raw.get("artifact") or ""
        blocker = raw.get("blocker") or ""
        text = raw.get("text") or ""
        created = raw.get("created") or raw.get("created_at")
        updated = raw.get("updated") or raw.get("updated_at")

        c = attention_model.new_candidate(
            source="action",
            source_key=f"action:{item_id}",
            title=text,
            state=status,
        )
        c["canonical"] = {
            "id": item_id,
            "status": status,
            "updated": updated,
        }
        c["anchors"] = {
            "action_id": item_id,
            "cron_job_ids": attention_model.extract_cron_job_ids(f"{artifact} {blocker}"),
            "post_ids": attention_model.extract_post_ids(f"{artifact} {blocker}"),
        }
        c["authority_evidence"] = blocker or None
        c["created_at"] = _iso_from_ts(created)
        c["updated_at"] = _iso_from_ts(updated)
        c["evidence"] = [a for a in [artifact] if a]
        c["native"] = {
            "id": item_id,
            "text": text,
            "artifact": artifact,
            "blocker": blocker,
            "status": status,
        }
        if status not in (None, "open"):
            c["suppression_reason"] = "resolved"
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    return {"ok": True, "error": None, "candidates": _finalize(candidates)}


def adapt_task_ledger() -> dict[str, Any]:
    """Normalize task-ledger.json records (canonical human-gate input)."""
    try:
        data = _read_json(_task_ledger_path())
    except Exception as exc:  # noqa: BLE001 — section fail-soft
        log.warning("approval-inbox task-ledger unreadable: %s", exc)
        return {"ok": False, "error": f"task-ledger.json unreadable: {exc}", "candidates": []}

    candidates: list[dict[str, Any]] = []
    for raw in data.get("items", []) if isinstance(data, dict) else []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("id", "")).strip()
        if not item_id:
            continue
        status = raw.get("status") or "pending"
        authority = raw.get("authority")
        blocker = raw.get("blocker")
        title = raw.get("title") or item_id
        created = raw.get("created_at")
        updated = raw.get("updated_at")

        c = attention_model.new_candidate(
            source="ledger",
            source_key=f"ledger:{item_id}",
            title=title,
            state=status,
        )
        c["canonical"] = {
            "id": item_id,
            "status": status,
            "authority": authority,
            "blocker": blocker,
        }
        c["anchors"] = {
            "action_id": item_id,
            "cron_job_ids": attention_model.extract_cron_job_ids(
                f"{raw.get('next_action') or ''} {blocker or ''}"
            ),
        }
        c["authority_evidence"] = authority
        c["project"] = raw.get("project")
        c["created_at"] = _iso_from_ts(created)
        c["updated_at"] = _iso_from_ts(updated)
        c["evidence"] = [str(p) for p in (raw.get("last_evidence") or []) if p]
        c["native"] = {
            "id": item_id,
            "title": title,
            "status": status,
            "authority": authority,
            "blocker": blocker,
            "project": raw.get("project"),
            "next_action": raw.get("next_action"),
            "due_at": raw.get("due_at"),
            "source": raw.get("source"),
        }
        if status == "completed":
            c["suppression_reason"] = "resolved"
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    return {"ok": True, "error": None, "candidates": _finalize(candidates)}


def _kanban_table_cols(conn: sqlite3.Connection) -> set[str]:
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    except sqlite3.Error:
        return set()


def _kanban_has_table(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def _kanban_latest_reason(conn: sqlite3.Connection, task_id: str) -> str | None:
    """Reason from the latest blocked/dependency_wait event payload (NOT a column)."""
    if not _kanban_has_table(conn, "task_events"):
        return None
    try:
        rows = conn.execute(
            "SELECT payload FROM task_events "
            "WHERE task_id=? AND kind IN ('blocked','dependency_wait') "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (task_id,),
        ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    payload = rows[0][0]
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict):
        return data.get("reason")
    return None


def _kanban_parents(conn: sqlite3.Connection, task_id: str) -> list[str]:
    if not _kanban_has_table(conn, "task_links"):
        return []
    try:
        rows = conn.execute(
            "SELECT parent_id FROM task_links WHERE child_id=?", (task_id,)
        ).fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def adapt_kanban() -> dict[str, Any]:
    """Normalize blocked + todo kanban cards across every board into candidates."""
    base = _kanban_dir()
    if not base.exists():
        return {"ok": False, "error": f"kanban boards dir {base} does not exist", "candidates": []}
    try:
        board_dirs = sorted(p for p in base.iterdir() if p.is_dir() and p.name != "_archived")
    except OSError as exc:
        return {"ok": False, "error": f"cannot list kanban boards: {exc}", "candidates": []}

    candidates: list[dict[str, Any]] = []
    for board_dir in board_dirs:
        db_path = board_dir / "kanban.db"
        if not db_path.exists():
            continue  # missing DB stays uncreated (mode=ro invariant)
        board = board_dir.name
        try:
            with _open_ro(db_path) as conn:
                cols = _kanban_table_cols(conn)
                has_block_kind = "block_kind" in cols
                has_block_recurrences = "block_recurrences" in cols
                rows = conn.execute(
                    "SELECT id, title, status, assignee, priority, created_at "
                    "FROM tasks WHERE status IN ('blocked', 'todo') "
                    "ORDER BY CASE status WHEN 'blocked' THEN 0 ELSE 1 END, priority DESC, created_at"
                ).fetchall()
                for task_id, title, status, assignee, priority, created_at in rows:
                    reason = _kanban_latest_reason(conn, task_id)
                    block_kind = None
                    block_recurrences = 0
                    if has_block_kind:
                        try:
                            row = conn.execute(
                                "SELECT block_kind FROM tasks WHERE id=?", (task_id,)
                            ).fetchone()
                            block_kind = row[0] if row else None
                        except sqlite3.Error:
                            block_kind = None
                    if has_block_recurrences:
                        try:
                            row = conn.execute(
                                "SELECT block_recurrences FROM tasks WHERE id=?", (task_id,)
                            ).fetchone()
                            block_recurrences = row[0] if row else 0
                        except sqlite3.Error:
                            block_recurrences = 0
                    parents = _kanban_parents(conn, task_id)

                    c = attention_model.new_candidate(
                        source="kanban",
                        source_key=f"kanban:{board}:{task_id}",
                        title=title,
                        state=status,
                    )
                    c["canonical"] = {
                        "board": board,
                        "task_id": task_id,
                        "status": status,
                        "block_kind": block_kind,
                        "reason": reason,
                    }
                    c["owner"] = assignee  # native assignee, NOT classified
                    c["authority_evidence"] = reason
                    c["created_at"] = _iso_from_ts(created_at)
                    c["evidence"] = [str(db_path)]
                    c["native"] = {
                        "board": board,
                        "task_id": task_id,
                        "title": title,
                        "assignee": assignee,
                        "status": status,
                        "priority": priority,
                        "block_kind": block_kind,
                        "block_recurrences": block_recurrences,
                        "parents": parents,
                        "reason": reason,
                    }
                    # dependency_gated / agent_owned / no_reason are Card D calls.
                    c["suppression_reason"] = None
                    c["fingerprint"] = attention_model.compute_fingerprint(c)
                    candidates.append(c)
        except sqlite3.Error as exc:
            log.warning("approval-inbox kanban board %s unreadable: %s", board, exc)
            continue

    return {"ok": True, "error": None, "candidates": _finalize(candidates)}


def _cron_latest_executions() -> tuple[dict[str, dict[str, Any]], str | None]:
    """Latest execution per job from executions.db (mode=ro). Returns (map, error)."""
    exec_db = _cron_executions_db()
    latest: dict[str, dict[str, Any]] = {}
    if not exec_db.exists():
        return latest, f"cron executions db {exec_db} does not exist"
    try:
        with _open_ro(exec_db) as conn:
            rows = conn.execute(
                "SELECT job_id, status, started_at, finished_at, error, claimed_at "
                "FROM executions e "
                "WHERE claimed_at = (SELECT MAX(claimed_at) FROM executions e2 WHERE e2.job_id = e.job_id)"
            ).fetchall()
        for job_id, status, started_at, finished_at, error, claimed_at in rows:
            latest[str(job_id)] = {
                "status": status,
                "started_at": started_at,
                "finished_at": finished_at,
                "error": error,
                "claimed_at": claimed_at,
            }
        return latest, None
    except sqlite3.Error as exc:
        return latest, f"cron executions db unreadable: {exc}"


def adapt_cron() -> dict[str, Any]:
    """Normalize cron jobs into candidates: latest execution + registry state."""
    window_days = _fail_window_days()
    jobs_path = _cron_jobs_path()

    errors: list[str] = []
    latest, exec_err = _cron_latest_executions()
    if exec_err:
        errors.append(exec_err)

    registry: dict[str, dict[str, Any]] = {}
    if not jobs_path.exists():
        errors.append(f"cron jobs registry {jobs_path} does not exist")
    else:
        try:
            data = _read_json(jobs_path)
            for job in data.get("jobs", []) if isinstance(data, dict) else []:
                if isinstance(job, dict) and job.get("id"):
                    registry[str(job["id"])] = job
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cron jobs registry unreadable: {exc}")

    if not latest and not registry:
        return {"ok": False, "error": "; ".join(errors) or "cron sources unavailable", "candidates": []}

    candidates: list[dict[str, Any]] = []
    all_job_ids = sorted(set(latest) | set(registry))

    for job_id in all_job_ids:
        reg = registry.get(job_id, {})
        exec_info = latest.get(job_id)
        enabled = reg.get("enabled", True)
        last_status = reg.get("last_status")
        last_run_at = reg.get("last_run_at")
        last_error = reg.get("last_error") or ""
        name = reg.get("name") or job_id
        model_snapshot = reg.get("model_snapshot")
        provider_snapshot = reg.get("provider_snapshot")
        next_run_at = reg.get("next_run_at")

        exec_status = exec_info.get("status") if exec_info else None
        exec_error = exec_info.get("error") if exec_info else None

        # Determine suppression (source-provable, §7 codes only).
        suppression: str | None = None
        if enabled is False:
            suppression = "disabled"
        elif exec_status == "unknown" and exec_error and "Scheduler restarted" in exec_error:
            suppression = "scheduler_restart"
        elif exec_info is None and last_status is None and last_run_at is None and next_run_at:
            suppression = "first_fire"
        elif exec_status == "completed":
            suppression = "recovered"
        elif exec_status == "failed":
            age_h = _iso_age_hours(exec_info.get("started_at"))
            if age_h is not None and age_h > window_days * 24:
                suppression = "out_of_window"

        # Only jobs with a signal become candidates.
        has_signal = (
            suppression is not None
            or exec_status in ("failed", "unknown")
            or last_status == "error"
        )
        if not has_signal:
            continue

        state = exec_status or last_status or ("first_fire" if suppression == "first_fire" else "unknown")
        c = attention_model.new_candidate(
            source="cron",
            source_key=f"cron:{job_id}",
            title=name,
            state=state,
        )
        c["canonical"] = {
            "job_id": job_id,
            "last_status": last_status,
            "last_run_at": last_run_at,
            "error": (last_error or exec_error or "")[:300],
        }
        c["anchors"] = {"cron_job_id": job_id}
        c["authority_evidence"] = (last_error or exec_error or "")[:300] or None
        c["created_at"] = _iso_from_ts(exec_info.get("claimed_at") if exec_info else last_run_at)
        c["updated_at"] = _iso_from_ts(last_run_at)
        c["evidence"] = [str(_cron_executions_db()), str(_cron_jobs_path())]
        c["native"] = {
            "job_id": job_id,
            "name": name,
            "enabled": enabled,
            "state": reg.get("state"),
            "last_status": last_status,
            "last_run_at": last_run_at,
            "last_error": last_error,
            "next_run_at": next_run_at,
            "model_snapshot": model_snapshot,
            "provider_snapshot": provider_snapshot,
            "repeat": reg.get("repeat"),
            "execution_status": exec_status,
            "execution_error": (exec_error or "")[:300] if exec_error else None,
            "execution_started_at": exec_info.get("started_at") if exec_info else None,
        }
        c["suppression_reason"] = suppression
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    return {
        "ok": True,
        "error": "; ".join(errors) or None,
        "candidates": _finalize(candidates),
    }


def _receipt_post_id_from_name(name: str) -> str | None:
    import re as _re
    # Gate receipts: <run_id>-<post_id>.json       e.g. 20260810T011509Z-18517.json
    # Staging receipts: staging-<post_id>-<slug>.json  e.g. staging-18571-meta-muse-glimmer-open-weights.json
    m = _re.search(r"-(\d{4,5})(?:-|\.json$)", name)
    return m.group(1) if m else None


def _receipt_timestamp_key(data: dict[str, Any], path: Path) -> tuple[str, str, str]:
    """Deterministic recency key for a receipt: (ISO timestamp, run_id, name)."""
    ts = data.get("evaluated_at") or data.get("staged_at") or ""
    run_id = str(data.get("run_id") or "")
    return (str(ts), run_id, path.name)


def _latest_receipt_per_post(
    pairs: list[tuple[Path, dict[str, Any], str]],
) -> dict[str, tuple[Path, dict[str, Any], str]]:
    """Collapse multiple receipt ticks per post to the single LATEST one.

    Live shape: 520 gate-receipt files across 70 posts (up to 26 ticks per
    post). The adapter emits one candidate per ``trt:<post_id>`` and
    fingerprints the latest receipt (attention-contract §8.1).
    """
    per_post: dict[str, list[tuple[Path, dict[str, Any], str]]] = {}
    for path, data, post_id in pairs:
        per_post.setdefault(post_id, []).append((path, data, post_id))
    out: dict[str, tuple[Path, dict[str, Any], str]] = {}
    for post_id, group in per_post.items():
        out[post_id] = max(group, key=lambda t: _receipt_timestamp_key(t[1], t[0]))
    return out


def adapt_trt() -> dict[str, Any]:
    """Normalize TRT markers + gate receipts + staging receipts into candidates.

    Marker frontmatter is parsed FULL-FILE (never head-truncated). Published /
    needs_review:false markers are suppressed pre-classification (Card C
    acceptance); draft markers without an in-source receipt link are
    marker_only. Cross-source joins (action item ↔ post id) are Card D's job.
    """
    base = _trt_dir()
    if not base.exists():
        return {"ok": False, "error": f"TRT drafts dir {base} does not exist", "candidates": []}

    # ---- receipts (authoritative evidence) -------------------------------
    # Collapse fan-out: live data has up to 26 receipt ticks per post. Keep
    # the single latest receipt per post so `trt:<post_id>` stays unique.
    gate_receipts: list[tuple[Path, dict[str, Any], str]] = []
    slug_to_receipt: dict[str, dict[str, Any]] = {}
    receipts_dir = _trt_receipts_dir()
    if receipts_dir.exists():
        try:
            for path in sorted(receipts_dir.glob("*.json")):
                post_id = _receipt_post_id_from_name(path.name)
                if not post_id:
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                gate_receipts.append((path, data, post_id))
                slug = data.get("slug")
                if slug:
                    slug_to_receipt.setdefault(str(slug), data)
        except OSError as exc:
            log.warning("approval-inbox TRT receipts unreadable: %s", exc)
    latest_gate = _latest_receipt_per_post(gate_receipts)

    staging_receipts: list[tuple[Path, dict[str, Any], str]] = []
    staging_dir = _trt_staging_receipts_dir()
    if staging_dir.exists():
        try:
            for path in sorted(staging_dir.glob("*.json")):
                post_id = _receipt_post_id_from_name(path.name)
                if not post_id:
                    continue
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict):
                    continue
                staging_receipts.append((path, data, post_id))
                slug = data.get("slug")
                if slug:
                    slug_to_receipt.setdefault(str(slug), data)
        except OSError as exc:
            log.warning("approval-inbox TRT staging receipts unreadable: %s", exc)
    latest_staging = _latest_receipt_per_post(staging_receipts)

    # When a post has both a gate receipt and a staging receipt (live: 18571),
    # the gate receipt is authoritative AND newer — never emit two trt:<post_id>
    # candidates. Staging receipts remain candidates only for posts with no
    # gate receipt.
    staging_only = {
        post_id: t for post_id, t in latest_staging.items() if post_id not in latest_gate
    }

    # ---- markers (secondary; full-file frontmatter) ----------------------
    candidates: list[dict[str, Any]] = []
    try:
        markers = sorted(base.glob("*.blocked.md"))
    except OSError as exc:
        return {"ok": False, "error": f"cannot list TRT blocked markers: {exc}", "candidates": []}

    # Collapse markers that share a slug (live: undetected-agents-cybergym-2
    # duplicates the canonical slug). The canonical `slug.blocked.md` name wins;
    # evidence keeps every collapsed path.
    markers_by_slug: dict[str, list[Path]] = {}
    marker_cache: dict[str, dict[str, Any]] = {}
    for path in markers:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = attention_model.parse_marker_frontmatter(text)
        slug = str(fm.get("slug") or path.name.replace(".blocked.md", ""))
        markers_by_slug.setdefault(slug, []).append(path)
        marker_cache[str(path)] = fm

    for slug, paths in markers_by_slug.items():
        canonical = [p for p in paths if p.name == f"{slug}.blocked.md"]
        chosen = (canonical or sorted(paths))[0]
        path = chosen
        fm = marker_cache[str(path)]
        marker_status = fm.get("status")
        marker_needs_review = fm.get("needs_review")
        title = fm.get("title") or slug

        # Adapter-level suppression from marker state (pre-classification).
        suppression: str | None = None
        linked: dict[str, Any] | None = None
        if marker_status == "published" and marker_needs_review is False:
            suppression = "published"
        elif slug in slug_to_receipt:
            linked = slug_to_receipt[slug]
        else:
            suppression = "marker_only"

        c = attention_model.new_candidate(
            source="trt",
            source_key=f"trt:marker:{slug}",
            title=title,
            state=marker_status,
        )
        c["canonical"] = {
            "post_id": linked.get("post_id") if linked else None,
            "verdict": linked.get("verdict") if linked else None,
            "code": (linked.get("code") or (linked.get("codes") or [None])[0]) if linked else None,
            "evaluated_at": linked.get("evaluated_at") if linked else None,
            "marker_status": marker_status,
            "marker_needs_review": marker_needs_review,
        }
        c["anchors"] = {
            "trt_slug": slug,
            "post_ids": [str(linked["post_id"])] if linked and linked.get("post_id") else [],
        }
        c["authority_evidence"] = (
            f"receipt {linked.get('verdict')} {linked.get('code')}" if linked else None
        )
        c["created_at"] = _iso_from_ts(fm.get("freshness_verified") or path.stat().st_mtime)
        c["evidence"] = [str(p) for p in paths] + (
            [str(receipts_dir / f"{linked.get('run_id')}-{linked.get('post_id')}.json")]
            if linked and linked.get("run_id") and linked.get("post_id") else []
        )
        c["native"] = {
            "path": str(path),
            "name": path.name,
            "slug": slug,
            "title": title,
            "marker_status": marker_status,
            "marker_needs_review": marker_needs_review,
        }
        c["suppression_reason"] = suppression
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    # ---- gate receipt candidates ------------------------------------------
    for path, data, post_id in latest_gate.values():
        code = data.get("code") or (data.get("codes") or [None])[0]
        title = data.get("title") or f"TRT gate {post_id}"
        c = attention_model.new_candidate(
            source="trt",
            source_key=f"trt:{post_id}",
            title=title,
            state=data.get("verdict"),
        )
        c["canonical"] = {
            "post_id": data.get("post_id", post_id),
            "verdict": data.get("verdict"),
            "code": code,
            "evaluated_at": data.get("evaluated_at"),
            "marker_status": None,
            "marker_needs_review": None,
        }
        c["anchors"] = {
            "trt_slug": data.get("slug"),
            "post_ids": [str(data.get("post_id", post_id))],
        }
        c["authority_evidence"] = (
            f"gate receipt {data.get('verdict')} {code}" if data.get("verdict") else None
        )
        c["created_at"] = _iso_from_ts(data.get("evaluated_at"))
        c["updated_at"] = _iso_from_ts(data.get("evaluated_at"))
        c["evidence"] = [str(path)]
        c["native"] = {
            "path": str(path),
            "run_id": data.get("run_id"),
            "post_id": data.get("post_id", post_id),
            "slug": data.get("slug"),
            "title": title,
            "verdict": data.get("verdict"),
            "code": code,
            "evaluated_at": data.get("evaluated_at"),
            "mode": data.get("mode"),
            "mutations": data.get("mutations"),
        }
        c["suppression_reason"] = None  # interpretation (human_now vs agent_fixable) is Card D
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    # ---- staging receipt candidates -----------------------------------------
    for path, data, post_id in staging_only.values():
        title = data.get("slug") or f"TRT staging {post_id}"
        c = attention_model.new_candidate(
            source="trt",
            source_key=f"trt:{post_id}",
            title=title,
            state=data.get("post_status"),
        )
        c["canonical"] = {
            "post_id": data.get("post_id", post_id),
            "verdict": "staging_draft",
            "code": None,
            "evaluated_at": data.get("staged_at"),
            "marker_status": None,
            "marker_needs_review": None,
        }
        c["anchors"] = {
            "trt_slug": data.get("slug"),
            "post_ids": [str(data.get("post_id", post_id))],
        }
        c["authority_evidence"] = None
        c["created_at"] = _iso_from_ts(data.get("staged_at"))
        c["updated_at"] = _iso_from_ts(data.get("staged_at"))
        c["evidence"] = [str(path)]
        c["native"] = {
            "path": str(path),
            "kind": data.get("kind"),
            "slug": data.get("slug"),
            "post_id": data.get("post_id", post_id),
            "post_status": data.get("post_status"),
            "staged_at": data.get("staged_at"),
            "source_pack_validation": data.get("source_pack_validation"),
            "gates": data.get("gates"),
        }
        c["suppression_reason"] = None  # watching is Card D's call
        c["fingerprint"] = attention_model.compute_fingerprint(c)
        candidates.append(c)

    return {"ok": True, "error": None, "candidates": _finalize(candidates)}


def collect_candidates() -> dict[str, dict[str, Any]]:
    """Run every adapter and return per-source envelopes for the attention pipeline."""
    return {
        "action_items": adapt_action_items(),
        "task_ledger": adapt_task_ledger(),
        "kanban": adapt_kanban(),
        "cron": adapt_cron(),
        "trt": adapt_trt(),
    }


# ---------------------------------------------------------------------------
# Serialization-boundary secret redaction (attention-contract §13.1.5,
# regression #11). The normalized envelope is assembled from source text
# (blockers, errors, evidence); before anything crosses the wire we mask
# secret-shaped values so no raw credential material appears in the
# serialized response. Deterministic and conservative — only well-known
# secret shapes are matched, so fingerprints/ids/cron job ids pass through.
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # OpenAI-style keys (sk-...).
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # GitHub personal access tokens (ghp_...).
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    # Bearer tokens.
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE),
    # key= / key: assignments for known secret names.
    re.compile(
        r"\b(?:api[_-]?key|token|secret|password|passwd|auth[_-]?token|"
        r"access[_-]?token|refresh[_-]?token)\b\s*[=:]\s*['\"]?"
        r"[A-Za-z0-9._~+/=-]{8,}",
        re.IGNORECASE,
    ),
    # AWS access key ids.
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # Slack tokens (xoxb-/xoxp-/xoxa-/xoxr-/xoxs-).
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)


def _redact_text(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value


def _redact_secrets(node: Any) -> Any:
    """Recursively mask secret-shaped strings before JSON serialization."""
    if isinstance(node, str):
        return _redact_text(node)
    if isinstance(node, list):
        return [_redact_secrets(item) for item in node]
    if isinstance(node, dict):
        return {key: _redact_secrets(val) for key, val in node.items()}
    return node


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "plugin": "approval-inbox", "read_only": True}


@router.get("/overview")
def overview() -> dict[str, Any]:
    """Lightweight per-section counts for the statusbar chip.

    Also returns the flat ``live_keys`` list so the renderer can compute the
    count that survives local ack/snooze filtering without re-fetching every
    section body.
    """
    sections = {
        "actionItems": _collect_action_items(),
        "kanban": _collect_kanban(),
        "cron": _collect_cron(),
        "trtBlocked": _collect_trt_blocked(),
    }
    counts = {
        "actionItems": sections["actionItems"]["count"],
        "kanbanBlocked": sections["kanban"].get("blocked", 0),
        "kanbanTodo": sections["kanban"].get("todo", 0),
        "cronFailed": sections["cron"]["count"],
        "trtBlocked": sections["trtBlocked"]["count"],
    }
    total = counts["actionItems"] + counts["kanbanBlocked"] + counts["kanbanTodo"] + counts["cronFailed"] + counts["trtBlocked"]
    live_keys: list[str] = []
    for sec in sections.values():
        live_keys.extend(item["key"] for item in sec.get("items", []))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "total": total,
        "live_keys": live_keys,
        "errors": {name: sec.get("error") for name, sec in sections.items() if sec.get("error")},
    }


@router.get("/action-items")
def action_items() -> dict[str, Any]:
    return _collect_action_items()


@router.get("/kanban")
def kanban() -> dict[str, Any]:
    return _collect_kanban()


@router.get("/cron")
def cron() -> dict[str, Any]:
    return _collect_cron()


@router.get("/trt-blocked")
def trt_blocked() -> dict[str, Any]:
    return _collect_trt_blocked()


@router.get("/attention")
def attention() -> dict[str, Any]:
    """V2 normalized attention envelope (attention-contract §1.1 / §13.1).

    Runs the five adapters (collect_candidates) and the deterministic
    classifier/deduper/ranker (attention_rules.build_attention), returning:

        generated_at / verified_at
        counts          {human_now, agent_fixable, dependency_wait,
                         informational, suppressed_invalid}
        primary         ranked human_now items only (the badge input)
        secondary       {agent_fixable, dependency_wait, informational}
        source_health   per-source {ok, error} — one failing source NEVER
                        hides healthy queues (regression #6)
        suppressed      diagnostics with a §7 code per suppression

    The response is passed through a serialization-boundary secret redactor
    so no raw credential material crosses the wire (regression #11). GET-only:
    POST/PUT/PATCH/DELETE remain rejected (405). Session-token auth is
    inherited from the Hermes dashboard plugin system like every other route.
    """
    aggregate = collect_candidates()
    envelope = attention_rules.build_attention(aggregate)
    return _redact_secrets(envelope)
