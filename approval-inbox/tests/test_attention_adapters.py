"""Adapter tests for the V2 attention pipeline (Card C, strict TDD).

These tests define the binding adapter contract FIRST (RED phase): each
adapter normalizes source-native records into candidates with full
provenance (source_key, source_keys, key, fingerprint, canonical fields,
evidence, authority evidence, native owner) and source-provable
pre-classification suppression codes. Adapters must NOT classify
(actionability/attention_class/owner resolution belong to Card D).

Binding docs:
    plans/hermes-approval-inbox-v2/evidence/attention-contract.md
    §1 field table, §7 suppression codes, §8.1 fingerprint fields, §9 keys

Run (from the plugin dir):
    env -u PYTHONPATH python -m pytest tests/test_attention_adapters.py -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import attention_model  # noqa: E402
import plugin_api  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_kanban_db(board_dir: Path, *, legacy: bool = False) -> None:
    """Create a kanban.db with the REAL live schema (or a legacy reduced one)."""
    conn = sqlite3.connect(board_dir / "kanban.db")
    if legacy:
        conn.execute(
            "CREATE TABLE tasks ("
            " id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT,"
            " status TEXT NOT NULL, priority INTEGER DEFAULT 0, created_by TEXT,"
            " created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,"
            " workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT,"
            " branch_name TEXT, project_id TEXT, claim_lock TEXT, claim_expires INTEGER,"
            " tenant TEXT, result TEXT, idempotency_key TEXT,"
            " consecutive_failures INTEGER NOT NULL DEFAULT 0, worker_pid INTEGER,"
            " last_failure_error TEXT, max_runtime_seconds INTEGER,"
            " last_heartbeat_at INTEGER, current_run_id INTEGER)"
        )
    else:
        conn.execute(
            "CREATE TABLE tasks ("
            " id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT, assignee TEXT,"
            " status TEXT NOT NULL, priority INTEGER DEFAULT 0, created_by TEXT,"
            " created_at INTEGER NOT NULL, started_at INTEGER, completed_at INTEGER,"
            " workspace_kind TEXT NOT NULL DEFAULT 'scratch', workspace_path TEXT,"
            " branch_name TEXT, project_id TEXT, claim_lock TEXT, claim_expires INTEGER,"
            " tenant TEXT, result TEXT, idempotency_key TEXT,"
            " consecutive_failures INTEGER NOT NULL DEFAULT 0, worker_pid INTEGER,"
            " last_failure_error TEXT, max_runtime_seconds INTEGER,"
            " last_heartbeat_at INTEGER, current_run_id INTEGER,"
            " workflow_template_id TEXT, current_step_key TEXT, skills TEXT,"
            " model_override TEXT, provider_override TEXT, reasoning_effort TEXT,"
            " max_retries INTEGER, goal_mode INTEGER, goal_max_turns INTEGER,"
            " session_id TEXT, block_kind TEXT, block_recurrences INTEGER)"
        )
        conn.execute(
            "CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE task_events ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,"
            " run_id INTEGER, kind TEXT NOT NULL, payload TEXT, created_at INTEGER NOT NULL)"
        )
    conn.commit()
    conn.close()


def _seed_kanban_task(board_dir: Path, task_id: str, title: str, status: str,
                      assignee: str | None = None, block_kind: str | None = None,
                      block_recurrences: int = 0, created_at: int = 1000,
                      priority: int = 0) -> None:
    conn = sqlite3.connect(board_dir / "kanban.db")
    cols = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    base_cols = ["id", "title", "assignee", "status", "priority", "created_at"]
    values: list = [task_id, title, assignee, status, priority, created_at]
    if "block_kind" in cols:
        base_cols.append("block_kind")
        values.append(block_kind)
    if "block_recurrences" in cols:
        base_cols.append("block_recurrences")
        values.append(block_recurrences)
    conn.execute(
        f"INSERT INTO tasks ({', '.join(base_cols)}) VALUES ({', '.join('?' for _ in base_cols)})",
        values,
    )
    conn.commit()
    conn.close()


def _seed_kanban_link(board_dir: Path, parent_id: str, child_id: str) -> None:
    conn = sqlite3.connect(board_dir / "kanban.db")
    conn.execute("INSERT INTO task_links (parent_id, child_id) VALUES (?,?)", (parent_id, child_id))
    conn.commit()
    conn.close()


def _seed_kanban_event(board_dir: Path, task_id: str, kind: str, payload: dict, created_at: int) -> None:
    conn = sqlite3.connect(board_dir / "kanban.db")
    conn.execute(
        "INSERT INTO task_events (task_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
        (task_id, None, kind, json.dumps(payload), created_at),
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> dict[str, Path]:
    """Build the full fake local-state tree and point the router/adapters at it."""

    # --- action items -------------------------------------------------------
    action_items = tmp_path / "state" / "action-items.json"
    _write_json(action_items, {
        "schema_version": 1,
        "updated_at": "2026-08-10T12:05:00Z",
        "items": [
            {
                "id": "newsletter-analytics-drift-pin",
                "text": "Approve pinning cron job to deepseek model",
                "artifact": "cron job 52d9a0d36bfc (newsletter-analytics)",
                "blocker": "Tony approval of: cronjob action=update job_id=52d9a0d36bfc provider=nous model=deepseek/deepseek-v4-flash-0731",
                "created": "2026-08-03T14:00:00Z",
                "updated": "2026-08-08T09:00:00Z",
                "status": "open",
            },
            {
                "id": "rabbit-r1-featured-image",
                "text": "Supply featured image for WP draft",
                "artifact": "WP draft 18517 (rabbit-r1-review)",
                "blocker": "Tony to supply image; gate 18517 keeps BLOCKED missing_source_pack",
                "created": "2026-08-06T00:00:00Z",
                "updated": "2026-08-09T00:00:00Z",
                "status": "open",
            },
            {
                "id": "closed-one",
                "text": "Already resolved",
                "artifact": "none",
                "blocker": "",
                "created": "2026-08-01T00:00:00Z",
                "updated": "2026-08-02T00:00:00Z",
                "status": "closed",
            },
        ],
    })

    # --- task ledger ---------------------------------------------------------
    task_ledger = tmp_path / "state" / "task-ledger.json"
    _write_json(task_ledger, {
        "schema_version": 1,
        "generated_by": "hermes-task-maintenance",
        "verified_at": "2026-08-10T11:00:00Z",
        "items": [
            {
                "id": "cron-newsletter-analytics-drift-pin",
                "title": "Pin newsletter analytics cron to deepseek-v4-flash-0731",
                "project": "hermes-cron-ops",
                "status": "approval_required",
                "priority": "p0",
                "created_at": "2026-08-05T20:33:58-05:00",
                "updated_at": "2026-08-10T09:00:00-05:00",
                "next_action": "Approve cronjob action=update job_id=52d9a0d36bfc",
                "blocker": "Tony approval (spending implication: model pin change)",
                "due_at": "2026-08-10T09:00:00-05:00",
                "source": {"kanban_board": None, "kanban_task_id": None},
                "authority": "human_gate",
                "attempts": 0,
                "last_evidence": ["/home/user/.hermes/cron/jobs.json"],
            },
            {
                "id": "yt-film-video-2-agent-overnight",
                "title": "Produce film video 2 overnight",
                "project": "youtube",
                "status": "pending",
                "priority": "p2",
                "created_at": "2026-08-09T20:00:00-05:00",
                "updated_at": "2026-08-09T20:00:00-05:00",
                "next_action": "Agent work overnight",
                "blocker": None,
                "due_at": None,
                "source": {"kanban_board": None, "kanban_task_id": None},
                "authority": "reversible_local",
                "attempts": 0,
                "last_evidence": [],
            },
            {
                "id": "done-ledger-item",
                "title": "Finished thing",
                "project": "misc",
                "status": "completed",
                "priority": "p3",
                "created_at": "2026-08-01T00:00:00-05:00",
                "updated_at": "2026-08-03T00:00:00-05:00",
                "next_action": None,
                "blocker": None,
                "due_at": None,
                "source": {"kanban_board": None, "kanban_task_id": None},
                "authority": "human_gate",
                "attempts": 1,
                "last_evidence": [],
            },
        ],
    })

    # --- kanban boards -------------------------------------------------------
    boards = tmp_path / "kanban" / "boards"
    alpha = boards / "alpha"
    alpha.mkdir(parents=True)
    _make_kanban_db(alpha)
    _seed_kanban_task(alpha, "t_tony_gate", "Awaiting Tony approval for PR #77 merge",
                      "blocked", assignee="hermes-dev", block_kind="needs_input",
                      block_recurrences=1, created_at=2000, priority=2)
    _seed_kanban_event(alpha, "t_tony_gate", "blocked",
                       {"reason": "Tony approval required: merge of PR #77 stays with coordinator/Tony", "kind": "needs_input", "recurrences": 1},
                       2200)
    _seed_kanban_task(alpha, "t_child", "Parent-gated child card", "todo",
                      assignee="hermes-dev", block_kind=None, created_at=1500)
    _seed_kanban_link(alpha, "t_parent", "t_child")
    _seed_kanban_task(alpha, "t_legacy_blocked", "Legacy blocked card", "blocked",
                      assignee="default", block_kind=None, created_at=3000)
    _seed_kanban_task(alpha, "t_done", "Done card", "done", assignee="default", created_at=4000)

    beta = boards / "beta"
    beta.mkdir(parents=True)
    _make_kanban_db(beta)
    _seed_kanban_task(beta, "t_beta_blocked", "Beta blocked", "blocked",
                      assignee="trt", block_kind="needs_input", block_recurrences=0, created_at=1200)

    # legacy-schema board (no block_kind / task_links / task_events columns)
    gamma = boards / "gamma"
    gamma.mkdir(parents=True)
    _make_kanban_db(gamma, legacy=True)
    _seed_kanban_task(gamma, "t_legacy_schema", "Legacy schema card", "blocked",
                      assignee="default", created_at=1100)

    # empty board dir (no kanban.db at all)
    delta = boards / "delta"
    delta.mkdir(parents=True)

    # --- cron -----------------------------------------------------------------
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True)
    exec_db = cron_dir / "executions.db"
    conn = sqlite3.connect(exec_db)
    conn.execute(
        "CREATE TABLE executions ("
        " id TEXT PRIMARY KEY, job_id TEXT NOT NULL, source TEXT NOT NULL,"
        " process_id TEXT NOT NULL, pid INTEGER NOT NULL, process_started_at INTEGER,"
        " status TEXT NOT NULL CHECK(status IN ('claimed','running','completed','failed','unknown')),"
        " claimed_at TEXT NOT NULL, started_at TEXT, finished_at TEXT, error TEXT)"
    )
    conn.executemany(
        "INSERT INTO executions (id, job_id, source, process_id, pid, status, claimed_at, started_at, finished_at, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("e1", "job-failed", "builtin", "p1", 1, "failed", "2026-08-10T08:00:00Z",
             "2026-08-10T08:00:00Z", "2026-08-10T08:00:10Z", "HTTP 503 upstream capacity"),
            ("e2", "job-recovered", "builtin", "p1", 1, "failed", "2026-08-09T08:00:00Z",
             "2026-08-09T08:00:00Z", None, "RuntimeError: boom"),
            ("e3", "job-recovered", "builtin", "p1", 1, "completed", "2026-08-10T08:00:00Z",
             "2026-08-10T08:00:00Z", "2026-08-10T08:00:05Z", None),
            ("e4", "job-restart", "builtin", "p1", 1, "unknown", "2026-08-10T06:00:00Z",
             "2026-08-10T06:00:00Z", None, "Scheduler restarted; execution state unknown"),
            ("e5", "job-old-failed", "builtin", "p1", 1, "failed", "2025-01-01T00:00:00Z",
             "2025-01-01T00:00:00Z", None, "ancient"),
        ],
    )
    conn.commit()
    conn.close()

    jobs_file = cron_dir / "jobs.json"
    _write_json(jobs_file, {
        "jobs": [
            {"id": "job-failed", "name": "Newsletter Analytics", "enabled": True, "state": "scheduled",
             "model_snapshot": "deepseek/deepseek-v4-flash-0731", "provider_snapshot": "nous",
             "last_status": "error", "last_run_at": "2026-08-10T08:00:00Z",
             "last_error": "HTTP 503 upstream capacity", "next_run_at": "2026-08-17T09:00:00-05:00",
             "repeat": {"times": None, "completed": 1}},
            {"id": "job-recovered", "name": "Recovered Job", "enabled": True, "state": "scheduled",
             "model_snapshot": None, "provider_snapshot": None,
             "last_status": "ok", "last_run_at": "2026-08-10T08:00:00Z", "last_error": None,
             "next_run_at": "2026-08-11T08:00:00-05:00", "repeat": {"times": None, "completed": 3}},
            {"id": "job-restart", "name": "Restart Job", "enabled": True, "state": "scheduled",
             "model_snapshot": None, "provider_snapshot": None,
             "last_status": None, "last_run_at": None, "last_error": None,
             "next_run_at": "2026-08-11T06:00:00-05:00", "repeat": {"times": None, "completed": 0}},
            {"id": "job-first-fire", "name": "First Fire Job", "enabled": True, "state": "scheduled",
             "model_snapshot": None, "provider_snapshot": None,
             "last_status": None, "last_run_at": None, "last_error": None,
             "next_run_at": "2026-08-17T22:00:00-05:00", "repeat": {"times": None, "completed": 0}},
            {"id": "job-disabled", "name": "Disabled Job", "enabled": False, "state": "paused",
             "model_snapshot": None, "provider_snapshot": None,
             "last_status": "error", "last_run_at": "2026-08-01T00:00:00Z", "last_error": "old failure",
             "next_run_at": None, "repeat": {"times": None, "completed": 0}},
            {"id": "job-old-failed", "name": "Old Failed Job", "enabled": True, "state": "scheduled",
             "model_snapshot": None, "provider_snapshot": None,
             "last_status": "error", "last_run_at": "2025-01-01T00:00:00Z", "last_error": "ancient",
             "next_run_at": None, "repeat": {"times": None, "completed": 0}},
        ]
    })

    # --- TRT -------------------------------------------------------------------
    trt = tmp_path / "trt" / "drafts"
    trt.mkdir(parents=True)
    (trt / "deepseek-v4-flash-0731-agent-update.blocked.md").write_text(
        "---\ntitle: \"DeepSeek update\"\nslug: deepseek-v4-flash-0731-agent-update\n"
        "status: published\nneeds_review: false\ncontent_type: news\n---\nbody\n", encoding="utf-8")
    (trt / "rabbit-r1-review.blocked.md").write_text(
        "---\ntitle: \"Rabbit R1 Review\"\nslug: rabbit-r1-review\n"
        "status: draft\nneeds_review: true\ncontent_type: review\n"
        "primary_keyword: rabbit r1\n"
        "seo_title: ...\nmeta_description: ...\nexcerpt: ...\ncategory: \"AI Hardware\"\n"
        "tags: [AI, Hardware]\ndisclosure: ...\nhands_on_tested: true\n"
        "source_pack: ...\nfreshness_verified: 2026-08-07\nvolatile_facts:\n  - vendor claim\n"
        "image_required: true\nimage_brief:\n  featured_image:\n    type: gpt_images\n    prompt: \"\"\n"
        "    output_size: \"1920x1080\"\n  inline_images: []\n  rationale: review\n"
        "yoast:\n  focus_keyword: rabbit r1\n  seo_title: ...\n  meta_description: ...\n"
        "---\n# Rabbit R1 Review\nbody\n", encoding="utf-8")
    (trt / "no-receipt-draft.blocked.md").write_text(
        "---\ntitle: \"No receipt draft\"\nslug: no-receipt-draft\nstatus: draft\nneeds_review: true\n---\nbody\n",
        encoding="utf-8")
    (trt / "openai-astra-critical-cyber-capabilities.blocked.md").write_text(
        "---\ntitle: \"OpenAI Astra critical cyber capabilities\"\nslug: openai-astra-critical-cyber-capabilities\n"
        "status: draft\nneeds_review: true\n---\nbody\n", encoding="utf-8")
    (trt / "post-c.md").write_text("# not blocked\n", encoding="utf-8")
    # Duplicate slug marker (live shape: undetected-agents-cybergym-2.blocked.md
    # carries the same slug: frontmatter as undetected-agents-cybergym.blocked.md).
    # The adapter must emit ONE marker candidate per slug, never two.
    (trt / "dup-slug-2.blocked.md").write_text(
        "---\ntitle: \"Dup slug\"\nslug: dup-slug\nstatus: draft\nneeds_review: true\n---\nbody\n",
        encoding="utf-8")
    (trt / "dup-slug.blocked.md").write_text(
        "---\ntitle: \"Dup slug\"\nslug: dup-slug\nstatus: draft\nneeds_review: true\n---\nbody\n",
        encoding="utf-8")

    trt_receipts = tmp_path / "trt" / "receipts"
    trt_receipts.mkdir(parents=True)
    _write_json(trt_receipts / "20260810T011509Z-18517.json", {
        "code": "missing_source_pack", "evaluated_at": "2026-08-10T01:15:10.282310+00:00",
        "mode": "shadow", "mutations": 0, "post_id": 18517, "run_id": "20260810T011509Z",
        "schema_version": 1, "verdict": "BLOCKED",
    })
    # Older tick for the SAME post (live shape: 16 receipts for 18517). The
    # adapter must emit ONE candidate per post, fingerprinting the LATEST.
    _write_json(trt_receipts / "20260809T201613Z-18517.json", {
        "code": "missing_source_pack", "evaluated_at": "2026-08-09T20:16:14.314360+00:00",
        "mode": "shadow", "mutations": 0, "post_id": 18517, "run_id": "20260809T201613Z",
        "schema_version": 1, "verdict": "BLOCKED",
    })
    _write_json(trt_receipts / "20260810T121703Z-18567.json", {
        "body_sha256": "abc", "codes": ["keyword_used_as_link_anchor"], "evaluated_at": "2026-08-10T12:17:03+00:00",
        "gates": [{"codes": ["keyword_used_as_link_anchor"], "name": "static", "verdict": "HOLD"}],
        "mode": "shadow", "mutations": 0, "post_id": 18567, "run_id": "20260810T121703Z",
        "schema_version": 1, "slug": "openai-astra-critical-cyber-capabilities",
        "title": "OpenAI Astra", "verdict": "HOLD",
    })

    trt_staging = tmp_path / "trt" / "staging"
    trt_staging.mkdir(parents=True)
    _write_json(trt_staging / "staging-18571-meta-muse-glimmer-open-weights.json", {
        "kind": "staging_receipt", "slug": "meta-muse-glimmer-open-weights", "post_id": 18571,
        "post_status": "draft", "staged_at": "2026-08-10T06:45:00Z",
        "source_pack_validation": {"verdict": "PASS", "codes": []},
        "gates": {"status_remains_draft": True, "no_publish": True},
    })

    os.environ["APPROVAL_INBOX_ACTION_ITEMS"] = str(action_items)
    os.environ["APPROVAL_INBOX_TASK_LEDGER"] = str(task_ledger)
    os.environ["APPROVAL_INBOX_KANBAN_DIR"] = str(boards)
    os.environ["APPROVAL_INBOX_CRON_EXECUTIONS_DB"] = str(exec_db)
    os.environ["APPROVAL_INBOX_CRON_JOBS"] = str(jobs_file)
    os.environ["APPROVAL_INBOX_TRT_DIR"] = str(trt)
    os.environ["APPROVAL_INBOX_TRT_RECEIPTS"] = str(trt_receipts)
    os.environ["APPROVAL_INBOX_TRT_STAGING_RECEIPTS"] = str(trt_staging)
    os.environ["APPROVAL_INBOX_CRON_FAIL_WINDOW_DAYS"] = "14"

    return {
        "tmp": tmp_path, "boards": boards, "exec_db": exec_db, "trt": trt,
        "trt_receipts": trt_receipts, "trt_staging": trt_staging,
        "action_items": action_items, "task_ledger": task_ledger,
    }


# ---------------------------------------------------------------------------
# Action items adapter
# ---------------------------------------------------------------------------

def test_adapt_action_items_preserves_provenance(fixture_tree) -> None:
    result = plugin_api.adapt_action_items()
    assert result["ok"] is True
    assert result["error"] is None
    keys = {c["source_key"] for c in result["candidates"]}
    assert keys == {
        "action:newsletter-analytics-drift-pin",
        "action:rabbit-r1-featured-image",
        "action:closed-one",
    }

    drift = next(c for c in result["candidates"] if c["source_key"] == "action:newsletter-analytics-drift-pin")
    assert drift["source"] == "action"
    assert drift["source_keys"] == ["action:newsletter-analytics-drift-pin"]
    assert drift["key"] == "action:newsletter-analytics-drift-pin"
    assert drift["title"] == "Approve pinning cron job to deepseek model"
    assert drift["state"] == "open"
    assert "Tony approval" in (drift["authority_evidence"] or "")
    assert "52d9a0d36bfc" in drift["canonical"].get("id", "") or "52d9a0d36bfc" in json.dumps(drift["native"])
    assert "cron job 52d9a0d36bfc" in json.dumps(drift["native"])
    assert drift["suppression_reason"] is None


def test_adapt_action_items_extracts_anchors(fixture_tree) -> None:
    result = plugin_api.adapt_action_items()
    drift = next(c for c in result["candidates"] if c["source_key"] == "action:newsletter-analytics-drift-pin")
    assert "52d9a0d36bfc" in drift["anchors"]["cron_job_ids"]
    rabbit = next(c for c in result["candidates"] if c["source_key"] == "action:rabbit-r1-featured-image")
    assert "18517" in rabbit["anchors"]["post_ids"]


def test_adapt_action_items_closed_is_resolved_suppressed(fixture_tree) -> None:
    result = plugin_api.adapt_action_items()
    closed = [c for c in result["candidates"] if c["source_key"] == "action:closed-one"]
    assert len(closed) == 1
    assert closed[0]["suppression_reason"] == "resolved"


# ---------------------------------------------------------------------------
# Task ledger adapter
# ---------------------------------------------------------------------------

def test_adapt_task_ledger_preserves_authority(fixture_tree) -> None:
    result = plugin_api.adapt_task_ledger()
    assert result["ok"] is True
    keys = {c["source_key"] for c in result["candidates"]}
    assert keys == {
        "ledger:cron-newsletter-analytics-drift-pin",
        "ledger:yt-film-video-2-agent-overnight",
        "ledger:done-ledger-item",
    }

    gate = next(c for c in result["candidates"] if c["source_key"] == "ledger:cron-newsletter-analytics-drift-pin")
    assert gate["source"] == "ledger"
    assert gate["state"] == "approval_required"
    assert gate["canonical"]["authority"] == "human_gate"
    assert gate["authority_evidence"] == "human_gate"
    assert gate["project"] == "hermes-cron-ops"
    assert gate["suppression_reason"] is None

    reversible = next(c for c in result["candidates"] if c["source_key"] == "ledger:yt-film-video-2-agent-overnight")
    assert reversible["canonical"]["authority"] == "reversible_local"
    assert reversible["suppression_reason"] is None


def test_adapt_task_ledger_completed_is_resolved_suppressed(fixture_tree) -> None:
    result = plugin_api.adapt_task_ledger()
    done = [c for c in result["candidates"] if c["source_key"] == "ledger:done-ledger-item"]
    assert len(done) == 1
    assert done[0]["suppression_reason"] == "resolved"


# ---------------------------------------------------------------------------
# Kanban adapter
# ---------------------------------------------------------------------------

def test_adapt_kanban_preserves_block_kind_and_reason_from_event(fixture_tree) -> None:
    result = plugin_api.adapt_kanban()
    assert result["ok"] is True
    keys = {c["source_key"] for c in result["candidates"]}
    assert "kanban:alpha:t_tony_gate" in keys
    assert "kanban:alpha:t_child" in keys
    assert "kanban:beta:t_beta_blocked" in keys
    assert "kanban:alpha:t_done" not in keys  # done excluded

    gate = next(c for c in result["candidates"] if c["source_key"] == "kanban:alpha:t_tony_gate")
    assert gate["canonical"]["board"] == "alpha"
    assert gate["canonical"]["task_id"] == "t_tony_gate"
    assert gate["canonical"]["block_kind"] == "needs_input"
    # reason MUST come from the latest blocked event payload, not a tasks column
    assert gate["canonical"]["reason"] == "Tony approval required: merge of PR #77 stays with coordinator/Tony"
    assert gate["authority_evidence"] == gate["canonical"]["reason"]
    assert gate["state"] == "blocked"
    assert gate["owner"] == "hermes-dev"  # native assignee preserved, NOT classified
    assert gate["suppression_reason"] is None


def test_adapt_kanban_preserves_parent_edges(fixture_tree) -> None:
    result = plugin_api.adapt_kanban()
    child = next(c for c in result["candidates"] if c["source_key"] == "kanban:alpha:t_child")
    assert child["native"]["parents"] == ["t_parent"]
    assert child["suppression_reason"] is None  # dependency_gated is Card D's call


def test_adapt_kanban_missing_db_not_created(fixture_tree) -> None:
    result = plugin_api.adapt_kanban()
    assert result["ok"] is True
    delta = fixture_tree["boards"] / "delta"
    assert not (delta / "kanban.db").exists()  # mode=ro invariant
    assert all("kanban:delta:" not in c["source_key"] for c in result["candidates"])


def test_adapt_kanban_legacy_schema_fail_soft(fixture_tree) -> None:
    """A board DB without block_kind/task_links/task_events must degrade, not crash."""
    result = plugin_api.adapt_kanban()
    assert result["ok"] is True
    legacy = next(c for c in result["candidates"] if c["source_key"] == "kanban:gamma:t_legacy_schema")
    assert legacy["canonical"]["block_kind"] is None
    assert legacy["canonical"]["reason"] is None


# ---------------------------------------------------------------------------
# Cron adapter
# ---------------------------------------------------------------------------

def test_adapt_cron_failed_candidate_with_registry_fields(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    assert result["ok"] is True
    failed = next(c for c in result["candidates"] if c["source_key"] == "cron:job-failed")
    assert failed["canonical"]["job_id"] == "job-failed"
    assert failed["canonical"]["last_status"] == "error"
    assert failed["canonical"]["last_run_at"] == "2026-08-10T08:00:00Z"
    assert failed["state"] == "failed"
    assert failed["native"]["enabled"] is True
    assert failed["native"]["model_snapshot"] == "deepseek/deepseek-v4-flash-0731"
    assert failed["suppression_reason"] is None  # agent_fixable is Card D's call


def test_adapt_cron_recovered_is_suppressed(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    rec = [c for c in result["candidates"] if c["source_key"] == "cron:job-recovered"]
    assert len(rec) == 1
    assert rec[0]["suppression_reason"] == "recovered"


def test_adapt_cron_scheduler_restart_flagged(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    restart = [c for c in result["candidates"] if c["source_key"] == "cron:job-restart"]
    assert len(restart) == 1
    assert restart[0]["suppression_reason"] == "scheduler_restart"


def test_adapt_cron_first_fire_flagged(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    ff = [c for c in result["candidates"] if c["source_key"] == "cron:job-first-fire"]
    assert len(ff) == 1
    assert ff[0]["suppression_reason"] == "first_fire"


def test_adapt_cron_disabled_flagged(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    dis = [c for c in result["candidates"] if c["source_key"] == "cron:job-disabled"]
    assert len(dis) == 1
    assert dis[0]["suppression_reason"] == "disabled"


def test_adapt_cron_out_of_window_flagged(fixture_tree) -> None:
    result = plugin_api.adapt_cron()
    old = [c for c in result["candidates"] if c["source_key"] == "cron:job-old-failed"]
    assert len(old) == 1
    assert old[0]["suppression_reason"] == "out_of_window"


# ---------------------------------------------------------------------------
# TRT adapter
# ---------------------------------------------------------------------------

def test_adapt_trt_published_marker_suppressed(fixture_tree) -> None:
    """Card C acceptance: published/no-review markers are suppressed BEFORE classification."""
    result = plugin_api.adapt_trt()
    assert result["ok"] is True
    published = [c for c in result["candidates"] if c["source_key"] == "trt:marker:deepseek-v4-flash-0731-agent-update"]
    assert len(published) == 1
    assert published[0]["suppression_reason"] == "published"
    assert published[0]["canonical"]["marker_status"] == "published"
    assert published[0]["canonical"]["marker_needs_review"] is False


def test_adapt_trt_marker_only_when_no_receipt(fixture_tree) -> None:
    result = plugin_api.adapt_trt()
    only = [c for c in result["candidates"] if c["source_key"] == "trt:marker:no-receipt-draft"]
    assert len(only) == 1
    assert only[0]["suppression_reason"] == "marker_only"
    assert only[0]["canonical"]["marker_status"] == "draft"
    assert only[0]["canonical"]["marker_needs_review"] is True


def test_adapt_trt_marker_with_receipt_not_marker_only(fixture_tree) -> None:
    """A draft marker whose slug appears in a slug-carrying gate receipt is linked in-source."""
    result = plugin_api.adapt_trt()
    linked = [c for c in result["candidates"] if c["source_key"] == "trt:marker:openai-astra-critical-cyber-capabilities"]
    assert len(linked) == 1
    assert linked[0]["suppression_reason"] is None
    # Receipt evidence is attached: verdict/code from the 18567 HOLD receipt (slug-carrying)
    assert linked[0]["canonical"]["post_id"] == 18567
    assert linked[0]["canonical"]["verdict"] == "HOLD"
    assert linked[0]["canonical"]["code"] == "keyword_used_as_link_anchor"


def test_adapt_trt_marker_without_in_source_receipt_link_is_marker_only(fixture_tree) -> None:
    """rabbit-r1-review is draft+needs_review but its 18517 receipt has no slug field:
    the adapter cannot prove the link in-source, so it stays marker_only here.
    The cross-source join (action item 'WP draft 18517 (rabbit-r1-review)') is Card D's job."""
    result = plugin_api.adapt_trt()
    rabbit = [c for c in result["candidates"] if c["source_key"] == "trt:marker:rabbit-r1-review"]
    assert len(rabbit) == 1
    assert rabbit[0]["suppression_reason"] == "marker_only"
    assert rabbit[0]["canonical"]["marker_status"] == "draft"
    assert rabbit[0]["canonical"]["marker_needs_review"] is True


def test_adapt_trt_gate_receipt_candidate(fixture_tree) -> None:
    result = plugin_api.adapt_trt()
    receipt = next(c for c in result["candidates"] if c["source_key"] == "trt:18517")
    assert receipt["canonical"]["post_id"] == 18517
    assert receipt["canonical"]["verdict"] == "BLOCKED"
    assert receipt["canonical"]["code"] == "missing_source_pack"
    assert receipt["canonical"]["evaluated_at"] == "2026-08-10T01:15:10.282310+00:00"
    assert any("20260810T011509Z-18517.json" in e for e in receipt["evidence"])
    assert receipt["suppression_reason"] is None  # interpretation is Card D's call

    hold = next(c for c in result["candidates"] if c["source_key"] == "trt:18567")
    assert hold["canonical"]["verdict"] == "HOLD"
    assert hold["canonical"]["code"] == "keyword_used_as_link_anchor"


def test_adapt_trt_staging_receipt_candidate(fixture_tree) -> None:
    result = plugin_api.adapt_trt()
    staging = [c for c in result["candidates"] if c["source_key"] == "trt:18571"]
    assert len(staging) == 1
    assert staging[0]["canonical"]["post_id"] == 18571
    assert staging[0]["canonical"]["verdict"] == "staging_draft"
    assert staging[0]["canonical"]["code"] is None
    assert staging[0]["canonical"]["marker_status"] is None  # no marker for this post
    assert any("staging-18571" in e for e in staging[0]["evidence"])
    assert staging[0]["suppression_reason"] is None  # watching is Card D's call


def test_adapt_trt_full_file_frontmatter_parse(fixture_tree) -> None:
    """Long frontmatter must parse to the real slug/status (full-file read)."""
    result = plugin_api.adapt_trt()
    rabbit = [c for c in result["candidates"] if c["source_key"] == "trt:marker:rabbit-r1-review"]
    assert len(rabbit) == 1
    assert rabbit[0]["native"]["slug"] == "rabbit-r1-review"
    assert rabbit[0]["canonical"]["marker_status"] == "draft"
    assert rabbit[0]["canonical"]["marker_needs_review"] is True


def test_adapt_trt_ignores_non_blocked_markers(fixture_tree) -> None:
    result = plugin_api.adapt_trt()
    assert not any("post-c.md" in c["source_key"] for c in result["candidates"])


def test_adapt_trt_one_receipt_candidate_per_post_latest_wins(fixture_tree) -> None:
    """Live shape: 16 receipt ticks per post. The adapter emits ONE candidate
    per trt:<post_id> and fingerprints the LATEST receipt (contract §8.1)."""
    result = plugin_api.adapt_trt()
    candidates = [c for c in result["candidates"] if c["source_key"] == "trt:18517"]
    assert len(candidates) == 1, f"expected 1 trt:18517 candidate, got {len(candidates)}"
    cand = candidates[0]
    assert cand["canonical"]["evaluated_at"] == "2026-08-10T01:15:10.282310+00:00"
    assert cand["canonical"]["verdict"] == "BLOCKED"
    assert cand["canonical"]["code"] == "missing_source_pack"
    assert any("20260810T011509Z-18517.json" in e for e in cand["evidence"])


def test_adapt_trt_marker_slug_collapse(fixture_tree) -> None:
    """Live shape: undetected-agents-cybergym-2.blocked.md duplicates the slug
    of undetected-agents-cybergym.blocked.md. One marker candidate per slug."""
    result = plugin_api.adapt_trt()
    markers = [c for c in result["candidates"] if c["source_key"] == "trt:marker:dup-slug"]
    assert len(markers) == 1, f"expected 1 dup-slug marker candidate, got {len(markers)}"


# ---------------------------------------------------------------------------
# Aggregator / envelope
# ---------------------------------------------------------------------------

def test_collect_candidates_all_sources(fixture_tree) -> None:
    agg = plugin_api.collect_candidates()
    assert set(agg.keys()) == {"action_items", "task_ledger", "kanban", "cron", "trt"}
    for name, env in agg.items():
        assert env["ok"] is True
        assert env["error"] is None
        assert isinstance(env["candidates"], list)
        assert all(attention_model.validate_candidate(c) == [] for c in env["candidates"])


def test_collect_candidates_fail_soft_missing_source(tmp_path) -> None:
    os.environ["APPROVAL_INBOX_ACTION_ITEMS"] = str(tmp_path / "nope.json")
    os.environ["APPROVAL_INBOX_TASK_LEDGER"] = str(tmp_path / "nope-ledger.json")
    os.environ["APPROVAL_INBOX_KANBAN_DIR"] = str(tmp_path / "nope-boards")
    os.environ["APPROVAL_INBOX_CRON_EXECUTIONS_DB"] = str(tmp_path / "nope.db")
    os.environ["APPROVAL_INBOX_CRON_JOBS"] = str(tmp_path / "nope-jobs.json")
    os.environ["APPROVAL_INBOX_TRT_DIR"] = str(tmp_path / "nope-trt")
    os.environ["APPROVAL_INBOX_TRT_RECEIPTS"] = str(tmp_path / "nope-receipts")
    os.environ["APPROVAL_INBOX_TRT_STAGING_RECEIPTS"] = str(tmp_path / "nope-staging")

    agg = plugin_api.collect_candidates()
    assert set(agg.keys()) == {"action_items", "task_ledger", "kanban", "cron", "trt"}
    for name, env in agg.items():
        assert env["ok"] is False
        assert env["error"] is not None
        assert env["candidates"] == []
    # nothing was created on disk
    assert not (tmp_path / "nope.db").exists()


def test_adapters_do_not_classify(fixture_tree) -> None:
    """Card C boundary: adapters preserve provenance, they do NOT classify."""
    agg = plugin_api.collect_candidates()
    for name, env in agg.items():
        for c in env["candidates"]:
            assert "actionability" not in c, f"{name} adapter must not classify"
            assert "attention_class" not in c, f"{name} adapter must not classify"
            assert "confidence" not in c, f"{name} adapter must not classify"
            assert "severity" not in c, f"{name} adapter must not classify"


def test_all_candidates_have_deterministic_fingerprints(fixture_tree) -> None:
    agg = plugin_api.collect_candidates()
    for name, env in agg.items():
        for c in env["candidates"]:
            assert len(c["fingerprint"]) == 64, f"{name}:{c['source_key']} missing fingerprint"
    # Determinism: two runs produce identical candidate lists (modulo nothing)
    agg2 = plugin_api.collect_candidates()
    for name in agg:
        a = [(c["source_key"], c["fingerprint"], c["suppression_reason"]) for c in agg[name]["candidates"]]
        b = [(c["source_key"], c["fingerprint"], c["suppression_reason"]) for c in agg2[name]["candidates"]]
        assert a == b, f"{name} adapter not deterministic"
