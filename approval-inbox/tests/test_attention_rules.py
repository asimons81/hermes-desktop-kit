"""Card D tests — classifier, verifier, deduper, ranker (strict TDD, RED first).

These tests define the binding Card D contract from
plans/hermes-approval-inbox-v2/evidence/attention-contract.md:

  - §1 AttentionItem field table / §1.1 envelope shape
  - §2 attention_class, §3 actionability decision table (the primary gate)
  - §4 owner resolution, §5 confidence, §6 severity + rank order
  - §7 suppression codes, §8.1 merged fingerprint, §9 dedupe anchors
  - §12 live-record mappings, §13.1 API invariants (deterministic output)

attention_rules.py implements the binding rules; run 9 completed the two
remaining live-contract rules (PR-merge gates without the literal word Tony,
and stale_resolved cross-source contradiction). Run from the plugin dir:

    env -u PYTHONPATH python -m pytest tests/test_attention_rules.py -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import attention_model  # noqa: E402
import attention_rules  # noqa: E402  (RED: module does not exist yet)
import plugin_api  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture tree — same shape as Card C's adapters fixture, so the rules are
# exercised against the exact candidates the adapters produce.
# ---------------------------------------------------------------------------

def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _make_kanban_db(board_dir: Path, *, legacy: bool = False) -> None:
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
        conn.execute("CREATE TABLE task_links (parent_id TEXT NOT NULL, child_id TEXT NOT NULL)")
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


def _build_fixture_tree(tmp_path: Path) -> dict[str, Path]:
    """Build the full fake local-state tree and point the router/adapters at it."""
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

    boards = tmp_path / "kanban" / "boards"
    alpha = boards / "alpha"
    alpha.mkdir(parents=True)
    _make_kanban_db(alpha)
    _seed_kanban_task(alpha, "t_tony_gate", "Tony review gate — close approval",
                      "blocked", assignee="default", block_kind="needs_input",
                      block_recurrences=1, created_at=2000, priority=2)
    _seed_kanban_event(alpha, "t_tony_gate", "blocked",
                       {"reason": "Tony review gate: awaiting Tony's explicit approval to close the gate (no response in session window)", "kind": "needs_input", "recurrences": 1},
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

    gamma = boards / "gamma"
    gamma.mkdir(parents=True)
    _make_kanban_db(gamma, legacy=True)
    _seed_kanban_task(gamma, "t_legacy_schema", "Legacy schema card", "blocked",
                      assignee="default", created_at=1100)

    delta = boards / "delta"
    delta.mkdir(parents=True)

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

    trt = tmp_path / "trt" / "drafts"
    trt.mkdir(parents=True)
    (trt / "deepseek-v4-flash-0731-agent-update.blocked.md").write_text(
        "---\ntitle: \"DeepSeek update\"\nslug: deepseek-v4-flash-0731-agent-update\n"
        "status: published\nneeds_review: false\ncontent_type: news\n---\nbody\n", encoding="utf-8")
    (trt / "rabbit-r1-review.blocked.md").write_text(
        "---\ntitle: \"Rabbit R1 Review\"\nslug: rabbit-r1-review\n"
        "status: draft\nneeds_review: true\ncontent_type: review\n"
        "primary_keyword: rabbit r1\nseo_title: ...\nmeta_description: ...\nexcerpt: ...\n"
        "category: \"AI Hardware\"\ntags: [AI, Hardware]\ndisclosure: ...\nhands_on_tested: true\n"
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
        "action_items": action_items, "task_ledger": task_ledger, "cron_dir": cron_dir,
    }


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> dict[str, Path]:
    return _build_fixture_tree(tmp_path)


def _add_drift_cron(fixture: dict[str, Path]) -> None:
    """Add the live drift-guard job 52d9a0d36bfc to the fixture cron sources."""
    exec_db = fixture["exec_db"]
    conn = sqlite3.connect(exec_db)
    conn.execute(
        "INSERT INTO executions (id, job_id, source, process_id, pid, status, claimed_at, started_at, finished_at, error) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("e6", "52d9a0d36bfc", "builtin", "p1", 1, "failed", "2026-08-10T07:00:00Z",
         "2026-08-10T07:00:00Z", None,
         "Skipped to prevent unintended spend: global inference config drifted since this job was created"),
    )
    conn.commit()
    conn.close()
    jobs_file = fixture["cron_dir"] / "jobs.json"
    data = json.loads(jobs_file.read_text(encoding="utf-8"))
    data["jobs"].append({
        "id": "52d9a0d36bfc", "name": "newsletter-analytics", "enabled": True, "state": "scheduled",
        "model_snapshot": "deepseek/deepseek-v4-flash-0731", "provider_snapshot": "nous",
        "last_status": "error", "last_run_at": "2026-08-10T07:00:00Z",
        "last_error": "Skipped to prevent unintended spend: global inference config drifted since this job was created",
        "next_run_at": "2026-08-17T09:00:00-05:00", "repeat": {"times": None, "completed": 4},
    })
    jobs_file.write_text(json.dumps(data), encoding="utf-8")


def _add_pr_card(fixture: dict[str, Path], task_id: str, title: str, reason: str,
                 recurrences: int = 0) -> None:
    """Add a second github-health-style kanban card sharing a PR ref in its reason."""
    boards = fixture["boards"]
    board = boards / "github-health"
    board.mkdir(parents=True, exist_ok=True)
    if not (board / "kanban.db").exists():
        _make_kanban_db(board)
    _seed_kanban_task(board, task_id, title, "blocked", assignee="hermes-dev",
                      block_kind="needs_input", block_recurrences=recurrences, created_at=2500)
    _seed_kanban_event(board, task_id, "blocked", {"reason": reason, "kind": "needs_input"}, 2600)


FIXED_NOW = datetime(2026, 8, 10, 16, 0, 0, tzinfo=timezone.utc)


def _attention(fixture: dict[str, Path], now: datetime | None = FIXED_NOW) -> dict:
    return attention_rules.build_attention(plugin_api.collect_candidates(), now=now)


def _by_key(items: list[dict], key: str) -> dict:
    matches = [i for i in items if i["key"] == key]
    assert matches, f"no item with key {key!r} in {[i['key'] for i in items]}"
    return matches[0]


def _primary_keys(envelope: dict) -> list[str]:
    return [i["key"] for i in envelope["primary"]]


def _secondary(envelope: dict, bucket: str) -> list[dict]:
    return envelope["secondary"][bucket]


# ---------------------------------------------------------------------------
# §1.1 envelope / pipeline shape
# ---------------------------------------------------------------------------

def test_build_attention_envelope_shape(fixture_tree) -> None:
    env = _attention(fixture_tree)
    assert set(env.keys()) >= {"generated_at", "verified_at", "counts", "primary", "secondary", "source_health"}
    assert set(env["counts"].keys()) == {
        "human_now", "agent_fixable", "dependency_wait", "informational", "suppressed_invalid",
    }
    assert set(env["secondary"].keys()) == {"agent_fixable", "dependency_wait", "informational"}
    # §13.1.2: counts.human_now == len(primary) exactly.
    assert env["counts"]["human_now"] == len(env["primary"])
    # §13.1.1: primary contains ONLY human_now items.
    assert all(i["actionability"] == "human_now" for i in env["primary"])
    # §13.1.3: secondary buckets contain only their own actionability.
    for bucket, items in env["secondary"].items():
        assert all(i["actionability"] == bucket for i in items), bucket
    # source health for all four sources is always present.
    assert set(env["source_health"].keys()) == {"action_items", "kanban", "cron", "trt"}


def test_attention_item_binding_fields(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = env["primary"][0]
    for field in (
        "key", "source_keys", "attention_class", "actionability", "owner", "authority",
        "title", "why_tony", "reason_now", "recommended_action", "alternatives",
        "consequence_of_delay", "project", "severity", "confidence", "verification",
        "created_at", "updated_at", "source_health", "fingerprint", "view_state",
        "suppression_reason",
    ):
        assert field in item, f"attention item missing field {field}"
    assert isinstance(item["source_keys"], list) and item["source_keys"]
    assert isinstance(item["verification"], dict)
    assert item["verification"]["status"] in ("verified", "stale", "unverified")
    assert isinstance(item["alternatives"], list) and len(item["alternatives"]) <= 4
    assert len(item["fingerprint"]) == 64


def test_suppressed_invalid_count_and_diagnostics(fixture_tree) -> None:
    env = _attention(fixture_tree)
    # published marker + 2 resolved + recovered + out_of_window + 3 merged victims.
    assert env["counts"]["suppressed_invalid"] >= 5
    diag = env.get("suppressed") or []
    reasons = [d["reason"] for d in diag]
    assert "published" in reasons
    assert "resolved" in reasons
    assert "recovered" in reasons
    assert "out_of_window" in reasons
    # every suppression has a §7 code.
    for d in diag:
        assert d["reason"] in attention_model.SUPPRESSION_CODES, d


# ---------------------------------------------------------------------------
# §3 ledger / action classification
# ---------------------------------------------------------------------------

def test_ledger_human_gate_approval_required_is_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    # drift pin merges action+ledger → one human_now approval item.
    keys = _primary_keys(env)
    assert any(k.startswith("att:cron:52d9a0d36bfc") for k in keys), keys
    item = next(i for i in env["primary"] if i["key"].startswith("att:cron:52d9a0d36bfc"))
    assert item["actionability"] == "human_now"
    assert item["attention_class"] == "approval"
    assert item["owner"] == "tony"
    assert item["confidence"] == "high"
    assert item["severity"] == "urgent"  # due 08-10 09:00-05:00 is past FIXED_NOW
    assert item["authority"] and "52d9a0d36bfc" in item["authority"]
    assert item["why_tony"]
    assert item["recommended_action"]
    assert item["consequence_of_delay"]
    assert item["suppression_reason"] is None


def test_ledger_reversible_local_pending_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:ledger:yt-film-video-2-agent-overnight")
    assert item["actionability"] == "informational"
    assert item["attention_class"] == "watching"
    assert item["owner"] == "default"
    assert item["suppression_reason"] is None
    assert item["key"] not in _primary_keys(env)


def test_action_tony_blocker_is_human_now_input_required(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:trt:18517")  # rabbit action + trt receipt merged
    assert item["actionability"] == "human_now"
    assert item["attention_class"] == "input_required"
    assert item["owner"] == "tony"
    assert item["confidence"] == "high"
    assert item["why_tony"]
    assert "image" in (item["authority"] or "").lower()


def test_action_no_tony_language_is_agent_fixable(fixture_tree) -> None:
    # add an action item with no Tony language in the blocker
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"].append({
        "id": "repair-loop",
        "text": "Repair the newsletter conveyor",
        "artifact": "trt-editorial-ops",
        "blocker": "Fix the blocked_config error in the jobs registry",
        "created": "2026-08-10T00:00:00Z",
        "updated": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "agent_fixable"), "att:action:repair-loop")
    assert item["actionability"] == "agent_fixable"
    assert item["attention_class"] == "watching"
    assert item["owner"] == "default"
    assert item["suppression_reason"] is None
    assert item["key"] not in _primary_keys(env)


def test_action_missing_blocker_is_informational_low_confidence(fixture_tree) -> None:
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"].append({
        "id": "no-owner-item",
        "text": "Investigate strange log line",
        "artifact": "",
        "blocker": "",
        "created": "2026-08-10T00:00:00Z",
        "updated": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:action:no-owner-item")
    assert item["actionability"] == "informational"
    assert item["confidence"] == "low"
    assert item["owner"] == "unknown"
    assert item["key"] not in _primary_keys(env)


def test_closed_action_resolved_suppressed(fixture_tree) -> None:
    env = _attention(fixture_tree)
    assert "att:action:closed-one" not in _primary_keys(env)
    assert "att:action:closed-one" not in [i["key"] for i in _secondary(env, "agent_fixable")]
    assert "att:action:closed-one" not in [i["key"] for i in _secondary(env, "informational")]
    diag = env.get("suppressed") or []
    assert any(d["source_key"] == "action:closed-one" and d["reason"] == "resolved" for d in diag)


# ---------------------------------------------------------------------------
# §3 kanban classification — dependency-blocked cards are NEVER human_now
# ---------------------------------------------------------------------------

def test_kanban_dependency_child_never_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "dependency_wait"), "att:kanban:alpha:t_child")
    assert item["actionability"] == "dependency_wait"
    assert item["suppression_reason"] == "dependency_gated"
    assert item["key"] not in _primary_keys(env)


def test_kanban_todo_no_parents_is_informational(fixture_tree) -> None:
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_todo_no_parent", "Queued work, no gate", "todo",
                      assignee="hermes-dev", block_kind=None, created_at=3500)
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:alpha:t_todo_no_parent")
    assert item["actionability"] == "informational"
    assert item["confidence"] == "low"
    assert item["key"] not in _primary_keys(env)


def test_kanban_blocked_needs_input_tony_reason_is_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:kanban:alpha:t_tony_gate")
    assert item["actionability"] == "human_now"
    assert item["attention_class"] == "approval"
    assert item["owner"] == "tony"
    assert item["confidence"] == "medium"  # one structured signal, no action item
    assert item["suppression_reason"] is None
    assert "Tony" in (item["title"] or item["reason_now"] or "")


def test_kanban_blocked_no_reason_is_informational_never_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:alpha:t_legacy_blocked")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "no_reason"
    assert item["key"] not in _primary_keys(env)


def test_kanban_blocked_needs_input_freeze_reason_not_human_now(fixture_tree) -> None:
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_freeze", "CALIBRATION TRT deals", "blocked",
                      assignee="hermes-dev", block_kind="needs_input", block_recurrences=0, created_at=3800)
    _seed_kanban_event(alpha, "t_freeze", "blocked",
                       {"reason": "Freeze during explicit Guides/how-to graph construction; no worker should run until prerequisites attached", "kind": "needs_input"},
                       3900)
    env = _attention(fixture_tree)
    # Title says CALIBRATION but reason proves internal freeze → informational, NOT primary.
    item = _by_key(_secondary(env, "informational"), "att:kanban:alpha:t_freeze")
    assert item["actionability"] == "informational"
    assert item["key"] not in _primary_keys(env)


def test_kanban_already_live_approval_is_low_severity(fixture_tree) -> None:
    """F1: t_8d211f86 — approval with 'already live' language → low severity."""
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_already_live", "Tony review gate — close approval",
                      "blocked", assignee="default", block_kind="needs_input",
                      block_recurrences=1, created_at=4000)
    _seed_kanban_event(alpha, "t_already_live", "blocked",
                       {"reason": "Tony review gate: piece ALREADY LIVE — awaiting close-out approval",
                        "kind": "needs_input"}, 4100)
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:kanban:alpha:t_already_live")
    assert item["severity"] == "low"


def test_action_slug_extraction_merges_with_trt_marker(fixture_tree) -> None:
    """F3: action item blocker with TRT slug merges with matching marker."""
    # Add matching TRT marker file
    trt = fixture_tree["trt"]
    (trt / "openai-education-plugins-2026.blocked.md").write_text(
        "---\ntitle: \"OpenAI Education Plugins\"\nslug: openai-education-plugins-2026\n"
        "status: draft\nneeds_review: true\n---\nbody\n", encoding="utf-8")
    # Add an action item whose blocker mentions openai-education-plugins-2026
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"].append({
        "id": "trt-t235e7ab9-dup-decision",
        "text": "Decide on openai-education-plugins duplicate",
        "artifact": "trt draft openai-education-plugins-2026",
        "blocker": "Tony decision: openai-education-plugins-2026 duplicate fate",
        "created": "2026-08-10T00:00:00Z",
        "updated": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    # The action item and the marker should merge via trt:slug:openai-education-plugins-2026
    merged = [i for i in env["primary"] if "openai-education" in (i.get("title", "") or "")]
    assert len(merged) == 1, f"Expected 1 merged item, got {[i['key'] for i in merged]}"
    item = merged[0]
    assert "action:trt-t235e7ab9-dup-decision" in item["source_keys"]
    marker_keys = [sk for sk in item["source_keys"] if "marker" in sk]
    assert marker_keys, f"No marker source_keys in {item['source_keys']}"
    assert item["actionability"] == "human_now"


def test_kanban_blocked_needs_input_parent_wait_is_dependency(fixture_tree) -> None:
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_parent_wait", "GUIDES control", "blocked",
                      assignee="hermes-dev", block_kind="needs_input", block_recurrences=0, created_at=3800)
    _seed_kanban_event(alpha, "t_parent_wait", "blocked",
                       {"reason": "not dispatched until its verified parent artifacts exist", "kind": "needs_input"},
                       3900)
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "dependency_wait"), "att:kanban:alpha:t_parent_wait")
    assert item["actionability"] == "dependency_wait"
    assert item["key"] not in _primary_keys(env)


def test_kanban_agent_review_gate_not_human_now(fixture_tree) -> None:
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_review", "NexusOS review gate", "blocked",
                      assignee="hermes-dev", block_kind=None, block_recurrences=0, created_at=3800)
    _seed_kanban_event(alpha, "t_review", "blocked",
                       {"reason": "review-required: requesting independent review", "kind": None}, 3900)
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:alpha:t_review")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "agent_owned"
    assert item["key"] not in _primary_keys(env)


def test_kanban_agent_assignee_without_tony_reason_is_agent_owned(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:beta:t_beta_blocked")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "agent_owned"
    assert item["owner"] == "trt"
    assert item["key"] not in _primary_keys(env)


# ---------------------------------------------------------------------------
# §3 cron classification — agent-fixable failures are NEVER human_now
# ---------------------------------------------------------------------------

def test_cron_agent_fixable_failure_not_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "agent_fixable"), "att:cron:job-failed")
    assert item["actionability"] == "agent_fixable"
    assert item["attention_class"] == "watching"
    assert item["owner"] == "default"
    assert item["suppression_reason"] is None
    assert item["key"] not in _primary_keys(env)


def test_cron_first_fire_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:cron:job-first-fire")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "first_fire"
    assert item["key"] not in _primary_keys(env)


def test_cron_disabled_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:cron:job-disabled")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "disabled"
    assert item["key"] not in _primary_keys(env)


def test_cron_scheduler_restart_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:cron:job-restart")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "scheduler_restart"
    assert item["key"] not in _primary_keys(env)


def test_cron_recovered_suppressed(fixture_tree) -> None:
    env = _attention(fixture_tree)
    assert "att:cron:job-recovered" not in _primary_keys(env)
    assert "att:cron:job-recovered" not in [i["key"] for i in _secondary(env, "agent_fixable")]
    diag = env.get("suppressed") or []
    assert any(d["source_key"] == "cron:job-recovered" and d["reason"] == "recovered" for d in diag)


def test_cron_out_of_window_suppressed(fixture_tree) -> None:
    env = _attention(fixture_tree)
    diag = env.get("suppressed") or []
    assert any(d["source_key"] == "cron:job-old-failed" and d["reason"] == "out_of_window" for d in diag)


def test_cron_drift_without_action_item_is_informational(fixture_tree) -> None:
    _add_drift_cron(fixture_tree)
    # Remove the drift-pin action item + ledger so no explicit Tony approval exists.
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"] = [i for i in data["items"] if i["id"] != "newsletter-analytics-drift-pin"]
    action_items.write_text(json.dumps(data), encoding="utf-8")
    ledger = fixture_tree["task_ledger"]
    ldata = json.loads(ledger.read_text(encoding="utf-8"))
    ldata["items"] = [i for i in ldata["items"] if i["id"] != "cron-newsletter-analytics-drift-pin"]
    ledger.write_text(json.dumps(ldata), encoding="utf-8")

    env = _attention(fixture_tree)
    # drift job WITHOUT an explicit Tony decision → informational (watching), not primary.
    item = _by_key(_secondary(env, "informational"), "att:cron:52d9a0d36bfc")
    assert item["actionability"] == "informational"
    assert item["attention_class"] == "watching"
    assert item["key"] not in _primary_keys(env)


# ---------------------------------------------------------------------------
# §3 TRT classification
# ---------------------------------------------------------------------------

def test_trt_published_marker_suppressed(fixture_tree) -> None:
    env = _attention(fixture_tree)
    assert "att:trt:marker:deepseek-v4-flash-0731-agent-update" not in [i["key"] for i in env["primary"]]
    diag = env.get("suppressed") or []
    assert any(
        d["source_key"] == "trt:marker:deepseek-v4-flash-0731-agent-update" and d["reason"] == "published"
        for d in diag
    )


def test_trt_marker_only_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:trt:marker:no-receipt-draft")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "marker_only"
    assert item["key"] not in _primary_keys(env)


def test_trt_blocked_missing_source_pack_with_action_is_human_now(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:trt:18517")
    assert item["actionability"] == "human_now"
    assert item["attention_class"] == "input_required"
    assert item["owner"] == "tony"
    assert item["confidence"] == "high"
    assert item["severity"] == "high"
    # provenance: action item + receipt both retained.
    assert "action:rabbit-r1-featured-image" in item["source_keys"]
    assert "trt:18517" in item["source_keys"]


def test_trt_hold_editorial_is_agent_fixable(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "agent_fixable"), "att:trt:18567")
    assert item["actionability"] == "agent_fixable"
    assert item["owner"] == "trt"
    assert item["attention_class"] == "watching"
    assert item["key"] not in _primary_keys(env)


def test_trt_staging_receipt_is_informational(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:trt:18571")
    assert item["actionability"] == "informational"
    assert item["attention_class"] == "watching"
    assert item["key"] not in _primary_keys(env)


# ---------------------------------------------------------------------------
# §9 dedupe — structured anchors first, provenance never discarded
# ---------------------------------------------------------------------------

def test_drift_pin_merges_three_sources_into_one_item(fixture_tree) -> None:
    _add_drift_cron(fixture_tree)
    env = _attention(fixture_tree)
    keys = _primary_keys(env)
    matching = [k for k in keys if k.startswith("att:cron:52d9a0d36bfc")]
    assert len(matching) == 1, keys
    item = next(i for i in env["primary"] if i["key"].startswith("att:cron:52d9a0d36bfc"))
    # All three contributing records retained (regression #4).
    assert "action:newsletter-analytics-drift-pin" in item["source_keys"]
    assert "ledger:cron-newsletter-analytics-drift-pin" in item["source_keys"]
    assert "cron:52d9a0d36bfc" in item["source_keys"]
    assert item["actionability"] == "human_now"
    assert item["confidence"] == "high"


def test_merge_victims_recorded_with_reason(fixture_tree) -> None:
    _add_drift_cron(fixture_tree)
    env = _attention(fixture_tree)
    diag = env.get("suppressed") or []
    victims = [d for d in diag if d["reason"] == "merged_victim"]
    # action + ledger fold into the cron item; both are victims (provenance kept).
    victim_keys = {d["source_key"] for d in victims}
    assert "action:newsletter-analytics-drift-pin" in victim_keys
    assert "ledger:cron-newsletter-analytics-drift-pin" in victim_keys
    # The primary item still carries every source_key.
    item = next(i for i in env["primary"] if i["key"].startswith("att:cron:52d9a0d36bfc"))
    assert set(item["source_keys"]) >= {"action:newsletter-analytics-drift-pin",
                                        "ledger:cron-newsletter-analytics-drift-pin",
                                        "cron:52d9a0d36bfc"}


def test_pr_merge_two_kanban_cards_same_pr(fixture_tree) -> None:
    _add_pr_card(fixture_tree, "t_ee5395ee", "Merge PR #77",
                 "Needs maintainer review/merge of PR #77")
    _add_pr_card(fixture_tree, "t_c0fce74f", "Do not auto-merge PR #77",
                 "PR #77 merge stays with coordinator/Tony — do not auto-merge")
    env = _attention(fixture_tree)
    pr_items = [i for i in env["primary"] if "PR #77" in (i["title"] or "") or "PR #77" in (i["reason_now"] or "")]
    assert len(pr_items) == 1, [i["key"] for i in env["primary"]]
    item = pr_items[0]
    assert "kanban:github-health:t_ee5395ee" in item["source_keys"]
    assert "kanban:github-health:t_c0fce74f" in item["source_keys"]
    assert item["confidence"] == "high"  # two independent structured signals


def test_title_fallback_is_low_confidence_and_last_resort(fixture_tree) -> None:
    # Two action items with identical titles but NO structured anchors do NOT
    # merge at high confidence; they stay separate low-confidence items.
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"].append({
        "id": "generic-one",
        "text": "Follow up on outstanding item",
        "artifact": "",
        "blocker": "",
        "created": "2026-08-10T00:00:00Z",
        "updated": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    data["items"].append({
        "id": "generic-two",
        "text": "Follow up on outstanding item",
        "artifact": "",
        "blocker": "",
        "created": "2026-08-10T00:00:00Z",
        "updated": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    informational = [i for i in _secondary(env, "informational")
                     if i["key"].startswith("att:action:generic")]
    # Structured identity anchors (action ids) differ → no merge, low confidence.
    assert len(informational) == 2
    assert all(i["confidence"] == "low" for i in informational)
    assert all(i["owner"] == "unknown" for i in informational)


# ---------------------------------------------------------------------------
# §1 field 16 verifier — stale/unverified labeled, ranked below verified
# ---------------------------------------------------------------------------

def test_human_now_item_verified_with_evidence(fixture_tree) -> None:
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:trt:18517")
    assert item["verification"]["status"] == "verified"
    assert item["verification"]["evidence"]
    assert item["verification"]["verified_at"] is not None


def test_stale_item_labeled(fixture_tree) -> None:
    # A human-now ledger gate with an updated_at far in the past is stale.
    ledger = fixture_tree["task_ledger"]
    data = json.loads(ledger.read_text(encoding="utf-8"))
    for it in data["items"]:
        if it["id"] == "cron-newsletter-analytics-drift-pin":
            it["updated_at"] = "2026-07-01T09:00:00-05:00"  # 40 days before FIXED_NOW
    ledger.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    item = next(i for i in env["primary"] if i["key"].startswith("att:cron:52d9a0d36bfc"))
    assert item["verification"]["status"] == "stale"
    assert item["verification"]["evidence"]


def test_unverified_item_labeled(fixture_tree) -> None:
    # A kanban todo with no parents and no evidence is unverified + low confidence.
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_no_evidence", "Queued work with no evidence", "todo",
                      assignee=None, block_kind=None, created_at=3500)
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:alpha:t_no_evidence")
    assert item["verification"]["status"] == "unverified"
    assert item["verification"]["evidence"] == []


def test_stale_ranks_below_verified_in_primary(fixture_tree) -> None:
    # Make the drift pin stale; the fresh rabbit gate must rank above it.
    ledger = fixture_tree["task_ledger"]
    data = json.loads(ledger.read_text(encoding="utf-8"))
    for it in data["items"]:
        if it["id"] == "cron-newsletter-analytics-drift-pin":
            it["updated_at"] = "2026-07-01T09:00:00-05:00"
    ledger.write_text(json.dumps(data), encoding="utf-8")
    env = _attention(fixture_tree)
    order = [i["key"] for i in env["primary"]]
    rabbit_idx = next(i for i, k in enumerate(order) if k.startswith("att:trt:18517"))
    drift_idx = next(i for i, k in enumerate(order) if k.startswith("att:cron:52d9a0d36bfc"))
    assert rabbit_idx < drift_idx, order


# ---------------------------------------------------------------------------
# §6.2 rank order within human_now
# ---------------------------------------------------------------------------

def test_rank_overdue_first(fixture_tree) -> None:
    env = _attention(fixture_tree)
    order = [i["key"] for i in env["primary"]]
    # drift pin (overdue, urgent) must be first.
    assert order[0].startswith("att:cron:52d9a0d36bfc"), order


def test_rank_recurrence_before_plain(fixture_tree) -> None:
    # Add a recurring blocked gate (block_recurrences >= 2) and one plain gate.
    boards = fixture_tree["boards"]
    alpha = boards / "alpha"
    _seed_kanban_task(alpha, "t_recurring", "Repeated blocked gate", "blocked",
                      assignee="hermes-dev", block_kind="needs_input", block_recurrences=3, created_at=3500)
    _seed_kanban_event(alpha, "t_recurring", "blocked",
                       {"reason": "awaiting Tony's explicit approval (recurring)", "kind": "needs_input", "recurrences": 3},
                       3600)
    _seed_kanban_task(alpha, "t_plain", "Plain Tony gate", "blocked",
                      assignee="hermes-dev", block_kind="needs_input", block_recurrences=0, created_at=2000)
    _seed_kanban_event(alpha, "t_plain", "blocked",
                       {"reason": "Tony approval required for plain gate", "kind": "needs_input", "recurrences": 0},
                       2100)
    env = _attention(fixture_tree)
    order = [i["key"] for i in env["primary"]]
    rec_idx = next(i for i, k in enumerate(order) if k == "att:kanban:alpha:t_recurring")
    plain_idx = next(i for i, k in enumerate(order) if k == "att:kanban:alpha:t_plain")
    assert rec_idx < plain_idx, order


def test_rank_confidence_descending_then_key(fixture_tree) -> None:
    env = _attention(fixture_tree)
    order = [i["key"] for i in env["primary"]]
    # rabbit (high) ranks above t_tony_gate (medium) once semantic keys tie.
    rabbit_idx = next(i for i, k in enumerate(order) if k.startswith("att:trt:18517"))
    tony_idx = next(i for i, k in enumerate(order) if k == "att:kanban:alpha:t_tony_gate")
    assert rabbit_idx < tony_idx, order


# ---------------------------------------------------------------------------
# §13.1.6 determinism — identical fixtures yield identical output
# ---------------------------------------------------------------------------

def test_identical_fixtures_deterministic(fixture_tree) -> None:
    _add_drift_cron(fixture_tree)
    _add_pr_card(fixture_tree, "t_ee5395ee", "Merge PR #77", "Needs maintainer review/merge of PR #77")
    env1 = _attention(fixture_tree)
    env2 = _attention(fixture_tree)
    assert env1["primary"] == env2["primary"]
    assert env1["secondary"] == env2["secondary"]
    assert env1["counts"] == env2["counts"]
    assert env1["suppressed"] == env2["suppressed"]
    assert env1["source_health"] == env2["source_health"]


def test_no_valid_human_gate_hidden_to_hit_count_target(fixture_tree) -> None:
    # Every candidate with explicit human-gate evidence must surface as
    # human_now — never dropped merely to shrink the primary count.
    _add_drift_cron(fixture_tree)
    _add_pr_card(fixture_tree, "t_ee5395ee", "Merge PR #77", "Needs maintainer review/merge of PR #77")
    _add_pr_card(fixture_tree, "t_c0fce74f", "Do not auto-merge PR #77",
                 "PR #77 merge stays with coordinator/Tony — do not auto-merge")
    env = _attention(fixture_tree)
    expected_gates = {
        "att:cron:52d9a0d36bfc",   # drift pin (action+ledger+cron)
        "att:trt:18517",           # rabbit input gate
        "att:kanban:alpha:t_tony_gate",  # kanban Tony gate (merges into PR #77 item)
        # PR #77 merged pair surfaces once
    }
    keys = _primary_keys(env)
    for gate in expected_gates:
        assert any(k.startswith(gate) or gate in k for k in keys), (gate, keys)
    pr_items = [i for i in env["primary"] if "PR #77" in (i["title"] or "")]
    assert len(pr_items) == 1


# ---------------------------------------------------------------------------
# Live-contract regressions (attention-contract §12) — verified read-only
# against the real sources; each becomes a fixture-level rule test.
# ---------------------------------------------------------------------------

def test_kanban_path_mention_does_not_name_tony_actor(fixture_tree) -> None:
    """Live: nexusos t_127ce68a reason says '...repo scaffold at
    /home/user/projects/some-plugin...' — the path is NOT an
    explicit Tony actor. §12.3 row 21 → informational (agent_owned), never
    human_now."""
    boards = fixture_tree["boards"]
    board = boards / "nexusos-hermes-desktop"
    board.mkdir(parents=True, exist_ok=True)
    if not (board / "kanban.db").exists():
        _make_kanban_db(board)
    _seed_kanban_task(board, "t_127ce68a", "NHD-02: Scaffold standalone integration repository",
                      "blocked", assignee="hermes-dev", block_kind="needs_input",
                      block_recurrences=1, created_at=4000)
    _seed_kanban_event(board, "t_127ce68a", "blocked",
                       {"reason": "review-required: NHD-02 scaffold complete and committed (892fd15) "
                        "with all gates green. Requesting independent review of the repo scaffold at "
                        "/home/user/projects/some-plugin before NHD-03/NHD-07 unblock. "
                        "Evidence in comment 3.", "kind": "needs_input", "recurrences": 1}, 4100)
    env = _attention(fixture_tree)
    item = _by_key(_secondary(env, "informational"), "att:kanban:nexusos-hermes-desktop:t_127ce68a")
    assert item["actionability"] == "informational"
    assert item["suppression_reason"] == "agent_owned"
    assert item["key"] not in _primary_keys(env)


def test_kanban_pr_merge_gate_without_tony_name_is_human_now(fixture_tree) -> None:
    """Live: github-health t_f6d34b2a (PR #7, 'Needs human/independent review
    before merge') — public-repo PR merge is a §10 human-only boundary even
    without the literal word Tony. §12.3 row 20 → human_now approval."""
    boards = fixture_tree["boards"]
    board = boards / "github-health"
    board.mkdir(parents=True, exist_ok=True)
    if not (board / "kanban.db").exists():
        _make_kanban_db(board)
    _seed_kanban_task(board, "t_f6d34b2a", "Merge hermes-gpt PR #7", "blocked",
                      assignee="hermes-dev", block_kind="needs_input",
                      block_recurrences=0, created_at=2600)
    _seed_kanban_event(board, "t_f6d34b2a", "blocked",
                       {"reason": "review-required: PR #7 (https://github.com/asimons81/hermes-gpt/pull/7) "
                        "implements the #6 WindowsApps WinError 5 fix. Local suite 395 passed, GitHub "
                        "Actions green on 3.10/3.11/3.12. Needs human/independent review before merge.",
                        "kind": "needs_input", "recurrences": 0}, 2700)
    env = _attention(fixture_tree)
    item = _by_key(env["primary"], "att:kanban:github-health:t_f6d34b2a")
    assert item["actionability"] == "human_now"
    assert item["attention_class"] == "approval"
    assert item["owner"] == "tony"
    assert item["severity"] == "high"


def test_stale_resolved_suppresses_contradicted_action_item(fixture_tree) -> None:
    """Live: hermes-vault-62-66-merge-gate — ledger
    hermes-vault-62-66-live-followups completed with human_gate authority →
    the #62/#66 portion is stale_resolved (§12.1 row 2). The surviving PR #77
    gate re-enters via the kanban github-health cards, so no valid gate is
    hidden."""
    # action item demanding issue-close of #62/#66
    action_items = fixture_tree["action_items"]
    data = json.loads(action_items.read_text(encoding="utf-8"))
    data["items"].append({
        "id": "hermes-vault-62-66-merge-gate",
        "text": "Approve merging 3 local hermes-vault repair commits into master, then close GitHub issues #62 and #66. Related: PR #77 rebased, CI green.",
        "artifact": "hermes-vault repo",
        "blocker": "Tony approval (outbound GitHub merge + issue close); PR #77 merge decision with Tony",
        "created": "2026-08-01T00:00:00Z",
        "updated": "2026-08-09T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(data), encoding="utf-8")
    # completed ledger followups for the same scope
    ledger = fixture_tree["task_ledger"]
    ldata = json.loads(ledger.read_text(encoding="utf-8"))
    ldata["items"].append({
        "id": "hermes-vault-62-66-live-followups",
        "title": "hermes-vault #62/#66 live followups",
        "project": "hermes-vault",
        "status": "completed",
        "priority": "p1",
        "created_at": "2026-08-08T00:00:00-05:00",
        "updated_at": "2026-08-08T01:50:00-05:00",
        "next_action": None,
        "blocker": None,
        "due_at": None,
        "source": {"kanban_board": None, "kanban_task_id": None},
        "authority": "human_gate",
        "attempts": 1,
        "last_evidence": ["/home/user/nexus-wiki/ops/evidence/some-close.json"],
    })
    ledger.write_text(json.dumps(ldata), encoding="utf-8")
    # matching github-health PR #77 gate so the surviving sub-gate still shows
    _add_pr_card(fixture_tree, "t_ee5395ee", "Merge PR #77",
                 "Needs maintainer review/merge of PR #77")
    _add_pr_card(fixture_tree, "t_c0fce74f", "Do not auto-merge PR #77",
                 "PR #77 merge stays with coordinator/Tony — do not auto-merge")

    env = _attention(fixture_tree)
    # the contradicted action item is suppressed as stale_resolved
    diag = env.get("suppressed") or []
    assert any(
        d["source_key"] == "action:hermes-vault-62-66-merge-gate" and d["reason"] == "stale_resolved"
        for d in diag
    )
    assert "att:action:hermes-vault-62-66-merge-gate" not in _primary_keys(env)
    # the surviving PR #77 merge gate is still primary
    pr_items = [i for i in env["primary"] if "PR #77" in (i["title"] or "")]
    assert len(pr_items) == 1
