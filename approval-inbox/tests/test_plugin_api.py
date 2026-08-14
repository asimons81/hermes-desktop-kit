"""Unit tests for the approval-inbox plugin backend router.

The router is a thin, READ-ONLY aggregator over local files/SQLite. Tests
point it at temp fixtures via APPROVAL_INBOX_* env vars and assert the
envelope shapes, counts, fail-soft behaviour, and the no-mutation contract
(405 on non-GET, no schema writes to the kanban DB).

Run (from the plugin dir, with your Hermes venv's python):
    env -u PYTHONPATH python -m pytest tests -q
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Make the router importable from the sibling dashboard/ dir.
HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import plugin_api  # noqa: E402


@pytest.fixture()
def fixture_tree(tmp_path: Path) -> dict[str, Path]:
    """Build a complete fake local-state tree and point the router at it."""
    # --- action items -------------------------------------------------------
    action_items = tmp_path / "state" / "action-items.json"
    action_items.parent.mkdir(parents=True)
    action_items.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-08-07T12:05:00Z",
                "items": [
                    {
                        "id": "open-one",
                        "text": "Approve pinning cron job X",
                        "artifact": "cron job abc",
                        "blocker": "Tony approval of cronjob update",
                        "created": "2026-08-03T14:00:00Z",
                        "status": "open",
                    },
                    {
                        "id": "open-two",
                        "text": "Decide fate of duplicate draft",
                        "artifact": "trt-editorial-ops drafts/x.md",
                        "blocker": "Tony decision",
                        "created": "2026-08-04T21:10:00Z",
                        "status": "open",
                    },
                    {
                        "id": "closed-one",
                        "text": "Already resolved",
                        "artifact": "none",
                        "created": "2026-08-01T00:00:00Z",
                        "status": "closed",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    # --- kanban boards ------------------------------------------------------
    boards = tmp_path / "kanban" / "boards"
    board_a = boards / "alpha"
    board_b = boards / "beta"
    for b in (board_a, board_b):
        b.mkdir(parents=True)

    def seed_board(path: Path, rows: list[tuple]) -> None:
        conn = sqlite3.connect(path / "kanban.db")
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
        conn.executemany(
            "INSERT INTO tasks (id, title, assignee, status, priority, created_at) "
            "VALUES (?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        conn.close()

    seed_board(
        board_a,
        [
            ("t_blocked_1", "Blocked card A", "hermes-dev", "blocked", 2, 1000),
            ("t_blocked_2", "Blocked card B", None, "blocked", 1, 2000),
            ("t_todo_1", "Todo card A", "default", "todo", 0, 3000),
            ("t_done_1", "Done card", "default", "done", 0, 4000),
            ("t_run_1", "Running card", "default", "running", 0, 5000),
        ],
    )
    seed_board(
        board_b,
        [
            ("t_blocked_3", "Blocked card C", "trt", "blocked", 0, 1500),
            ("t_arch_1", "Archived card", None, "archived", 0, 2500),
        ],
    )
    # A stray DB file at the top level must be ignored (not a board dir).
    (boards / "stray.db").write_bytes(b"not-a-real-db")

    # --- cron ----------------------------------------------------------------
    cron_dir = tmp_path / "cron"
    cron_dir.mkdir()
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
            ("e1", "job-failed", "builtin", "p1", 1, "failed", "2026-08-07T09:00:00Z",
             "2026-08-07T09:00:00Z", "2026-08-07T09:00:10Z", "RuntimeError: boom"),
            ("e2", "job-failed", "builtin", "p1", 1, "failed", "2026-08-06T09:00:00Z",
             "2026-08-06T09:00:00Z", None, "RuntimeError: boom again"),
            ("e3", "job-ok", "builtin", "p1", 1, "completed", "2026-08-07T10:00:00Z",
             "2026-08-07T10:00:00Z", "2026-08-07T10:00:05Z", None),
            ("e4", "job-old-failed", "builtin", "p1", 1, "failed", "2025-01-01T00:00:00Z",
             "2025-01-01T00:00:00Z", None, "ancient"),
        ],
    )
    conn.commit()
    conn.close()

    jobs_file = cron_dir / "jobs.json"
    jobs_file.write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "job-failed", "name": "Newsletter Analytics", "enabled": True,
                     "last_status": "error", "last_error": "drift guard", "last_run_at": "2026-08-07T09:00:00Z"},
                    {"id": "job-ok", "name": "Daily Brief", "enabled": True,
                     "last_status": "ok", "last_error": None, "last_run_at": "2026-08-07T10:00:00Z"},
                ]
            }
        ),
        encoding="utf-8",
    )

    # --- TRT blocked markers -------------------------------------------------
    trt = tmp_path / "trt" / "drafts"
    trt.mkdir(parents=True)
    (trt / "post-a.blocked.md").write_text("# blocked\n", encoding="utf-8")
    (trt / "post-b.blocked.md").write_text("# blocked\n", encoding="utf-8")
    (trt / "post-c.md").write_text("# not blocked\n", encoding="utf-8")

    # --- task ledger (empty by default; keeps /attention hermetic) ----------
    task_ledger = tmp_path / "state" / "task-ledger.json"
    task_ledger.write_text(
        json.dumps({"schema_version": 1, "items": []}), encoding="utf-8"
    )

    # --- TRT receipt dirs (empty by default; keeps /attention hermetic) ------
    trt_receipts = tmp_path / "trt" / "receipts"
    trt_receipts.mkdir(parents=True, exist_ok=True)
    trt_staging = tmp_path / "trt" / "staging"
    trt_staging.mkdir(parents=True, exist_ok=True)

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
        "task_ledger": task_ledger, "trt_receipts": trt_receipts,
        "trt_staging": trt_staging, "cron_dir": cron_dir,
    }


@pytest.fixture()
def client(fixture_tree: dict[str, Path]) -> TestClient:
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["read_only"] is True


def test_action_items_only_open(client: TestClient) -> None:
    resp = client.get("/action-items")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["count"] == 2
    keys = {item["key"] for item in body["items"]}
    assert keys == {"action:open-one", "action:open-two"}
    item = next(i for i in body["items"] if i["key"] == "action:open-one")
    assert item["status"] == "open"
    assert item["artifact"] == "cron job abc"
    assert item["age_hours"] is not None


def test_kanban_blocked_and_todo_across_boards(client: TestClient) -> None:
    resp = client.get("/kanban")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["count"] == 4  # 3 blocked + 1 todo (running/done/archived excluded)
    assert body["blocked"] == 3
    assert body["todo"] == 1
    boards = {item["board"] for item in body["items"]}
    assert boards == {"alpha", "beta"}
    keys = {item["key"] for item in body["items"]}
    assert "kanban:alpha:t_blocked_1" in keys
    assert "kanban:beta:t_blocked_3" in keys


def test_kanban_read_only_no_schema_write(fixture_tree: dict[str, Path]) -> None:
    """A missing board DB stays missing — the router never creates schema."""
    boards = fixture_tree["boards"]
    empty_board = boards / "gamma"
    empty_board.mkdir()
    os.environ["APPROVAL_INBOX_KANBAN_DIR"] = str(boards)
    app = FastAPI()
    app.include_router(plugin_api.router)
    client = TestClient(app)
    resp = client.get("/kanban")
    assert resp.status_code == 200
    # gamma has no kanban.db and the router must NOT create one.
    assert not (empty_board / "kanban.db").exists()


def test_cron_failed_latest_wins(client: TestClient) -> None:
    resp = client.get("/cron")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    # job-failed (latest failed) surfaces; job-ok (latest completed) does not;
    # job-old-failed latest is ancient and older than the fail window.
    assert body["count"] == 1
    item = body["items"][0]
    assert item["job_id"] == "job-failed"
    assert item["name"] == "Newsletter Analytics"
    assert item["status"] == "failed"
    assert "boom" in item["last_error"]


def test_cron_registry_error_only(client: TestClient) -> None:
    """A job absent from executions but flagged error in jobs.json surfaces."""
    resp = client.get("/cron")
    body = resp.json()
    assert body["count"] == 1  # only job-failed in this fixture


def test_trt_blocked_markers_only(client: TestClient) -> None:
    resp = client.get("/trt-blocked")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert body["count"] == 2
    names = {item["name"] for item in body["items"]}
    assert names == {"post-a.blocked.md", "post-b.blocked.md"}


def test_overview_counts_and_live_keys(client: TestClient) -> None:
    resp = client.get("/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"] == {
        "actionItems": 2,
        "kanbanBlocked": 3,
        "kanbanTodo": 1,
        "cronFailed": 1,
        "trtBlocked": 2,
    }
    assert body["total"] == 9
    assert len(body["live_keys"]) == 9
    assert body["errors"] == {}


def test_non_get_rejected(client: TestClient) -> None:
    resp = client.post("/overview")
    assert resp.status_code == 405
    resp = client.put("/action-items")
    assert resp.status_code == 405
    resp = client.delete("/kanban")
    assert resp.status_code == 405


def test_fail_soft_missing_source(tmp_path: Path) -> None:
    """A missing source yields a 200 envelope with an error, not a 500."""
    os.environ["APPROVAL_INBOX_ACTION_ITEMS"] = str(tmp_path / "nope.json")
    os.environ["APPROVAL_INBOX_KANBAN_DIR"] = str(tmp_path / "nope-boards")
    os.environ["APPROVAL_INBOX_CRON_EXECUTIONS_DB"] = str(tmp_path / "nope.db")
    os.environ["APPROVAL_INBOX_CRON_JOBS"] = str(tmp_path / "nope-jobs.json")
    os.environ["APPROVAL_INBOX_TRT_DIR"] = str(tmp_path / "nope-trt")
    app = FastAPI()
    app.include_router(plugin_api.router)
    client = TestClient(app)

    resp = client.get("/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert len(body["errors"]) == 4

    resp = client.get("/action-items")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["error"] is not None


def test_overview_includes_live_keys(client: TestClient) -> None:
    resp = client.get("/overview")
    body = resp.json()
    keys = set(body["live_keys"])
    assert "action:open-one" in keys
    assert "kanban:alpha:t_blocked_1" in keys
    assert "cron:job-failed" in keys
    assert "trt:post-a.blocked.md" in keys


# ---------------------------------------------------------------------------
# Card E — GET /attention normalized contract
#
# Acceptance checks (spec §9, attention-contract §13.1):
#   * partial-source resilience — one source failure never hides healthy queues
#   * counts.human_now == len(primary) exactly (regression #10)
#   * raw counts exist only as diagnostics, never the operator badge input
#   * POST/PUT/PATCH/DELETE remain rejected (405)
#   * no secret values or raw credential material in the serialized response
#   * session-token auth is inherited from the Hermes router (no new auth code)
# ---------------------------------------------------------------------------

def _attention_route_present() -> bool:
    return any(
        getattr(r, "path", None) == "/attention" and "GET" in getattr(r, "methods", set())
        for r in plugin_api.router.routes
    )


def test_attention_route_registered_on_shared_router(client: TestClient) -> None:
    """The route lives on the same APIRouter, so it inherits Hermes'
    session-token middleware exactly like the other routes (no new auth code,
    no auth bypass)."""
    assert _attention_route_present()


def test_attention_envelope_shape_and_count_parity(client: TestClient) -> None:
    resp = client.get("/attention")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) >= {
        "generated_at", "verified_at", "counts", "primary", "secondary",
        "source_health",
    }
    assert set(body["counts"].keys()) == {
        "human_now", "agent_fixable", "dependency_wait", "informational",
        "suppressed_invalid",
    }
    assert set(body["secondary"].keys()) == {
        "agent_fixable", "dependency_wait", "informational",
    }
    assert set(body["source_health"].keys()) == {
        "action_items", "kanban", "cron", "trt",
    }
    # §13.1.2: counts.human_now == len(primary) exactly (regression #10).
    assert body["counts"]["human_now"] == len(body["primary"])
    # §13.1.1: primary contains ONLY human_now items.
    assert all(i["actionability"] == "human_now" for i in body["primary"])
    # §13.1.3: secondary buckets contain only their own actionability.
    for bucket, items in body["secondary"].items():
        assert all(i["actionability"] == bucket for i in items), bucket
    # This fixture has 2 open Tony action items → 2 primary.
    assert body["counts"]["human_now"] == 2
    keys = [i["key"] for i in body["primary"]]
    assert "att:action:open-one" in keys
    assert "att:action:open-two" in keys


def test_attention_no_raw_counts_as_badge_input(client: TestClient) -> None:
    """Raw V1 counts (actionItems/kanbanBlocked/...) must NOT appear in the
    /attention envelope — the operator badge derives from counts.human_now
    only. Raw counts stay on the diagnostic /overview route."""
    resp = client.get("/attention")
    body = resp.json()
    raw_keys = {"actionItems", "kanbanBlocked", "kanbanTodo", "cronFailed", "trtBlocked"}
    assert not (raw_keys & set(body.keys()))
    assert not (raw_keys & set(body["counts"].keys()))
    # The badge input is exactly counts.human_now.
    assert body["counts"]["human_now"] == len(body["primary"])
    # The raw diagnostic route still exists and still reports raw counts.
    ov = client.get("/overview").json()
    assert "actionItems" in ov["counts"]
    assert "kanbanBlocked" in ov["counts"]


def test_attention_partial_source_failure_keeps_healthy_queues(
    fixture_tree: dict[str, Path], client: TestClient,
) -> None:
    """Regression #6: break the kanban source; the action queue must still
    render and source_health must report the failure per source."""
    os.environ["APPROVAL_INBOX_KANBAN_DIR"] = str(fixture_tree["tmp"] / "missing-boards")
    resp = client.get("/attention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_health"]["kanban"]["ok"] is False
    assert body["source_health"]["kanban"]["error"] is not None
    # Healthy action queue still visible, count parity intact.
    assert body["counts"]["human_now"] == 2
    assert body["counts"]["human_now"] == len(body["primary"])
    assert body["source_health"]["action_items"]["ok"] is True
    keys = [i["key"] for i in body["primary"]]
    assert "att:action:open-one" in keys
    assert "att:action:open-two" in keys
    # The failing source contributes no items; healthy trt section remains.
    assert all(not k.startswith("att:kanban:") for k in keys)
    assert body["source_health"]["trt"]["ok"] is True
    assert body["counts"]["informational"] >= 1  # trt marker_only items


def test_attention_all_sources_missing_empty_but_errors_visible(client: TestClient) -> None:
    """Every source missing → 200 with empty primary + per-source errors.
    Empty primary means 'no human-now items', NOT 'all sources healthy'."""
    missing = "/tmp/approval-inbox-definitely-missing"
    for var in (
        "APPROVAL_INBOX_ACTION_ITEMS", "APPROVAL_INBOX_TASK_LEDGER",
        "APPROVAL_INBOX_KANBAN_DIR", "APPROVAL_INBOX_CRON_EXECUTIONS_DB",
        "APPROVAL_INBOX_CRON_JOBS", "APPROVAL_INBOX_TRT_DIR",
        "APPROVAL_INBOX_TRT_RECEIPTS", "APPROVAL_INBOX_TRT_STAGING_RECEIPTS",
    ):
        os.environ[var] = missing
    resp = client.get("/attention")
    assert resp.status_code == 200
    body = resp.json()
    assert body["counts"]["human_now"] == 0
    assert body["primary"] == []
    for source, health in body["source_health"].items():
        assert health["ok"] is False, source
        assert health["error"], source


def test_attention_non_get_rejected(client: TestClient) -> None:
    """POST/PUT/PATCH/DELETE on /attention remain rejected (405)."""
    assert client.post("/attention").status_code == 405
    assert client.put("/attention").status_code == 405
    assert client.patch("/attention").status_code == 405
    assert client.delete("/attention").status_code == 405


def test_attention_response_contains_no_secret_material(
    fixture_tree: dict[str, Path], client: TestClient,
) -> None:
    """Regression #11: seed secret-shaped values into the sources; the
    serialized /attention response must not contain them."""
    # Seed a bearer token into the cron jobs registry (flows into the
    # agent_fixable item's authority via canonical.error).
    jobs_file = fixture_tree["cron_dir"] / "jobs.json"
    data = json.loads(jobs_file.read_text(encoding="utf-8"))
    for job in data["jobs"]:
        if job["id"] == "job-failed":
            job["last_error"] = (
                "HTTP 503: Authorization: Bearer sk-secret-test-abcdef1234567890 "
                "upstream capacity"
            )
    jobs_file.write_text(json.dumps(data), encoding="utf-8")
    # Seed an api_key= style secret into an action blocker.
    action_items = fixture_tree["tmp"] / "state" / "action-items.json"
    adata = json.loads(action_items.read_text(encoding="utf-8"))
    adata["items"].append({
        "id": "secret-leak-probe",
        "text": "Approve third-party integration",
        "artifact": "probe",
        "blocker": "Tony approval: api_key=ghp_testSecretValue1234567890abcdef",
        "created": "2026-08-10T00:00:00Z",
        "status": "open",
    })
    action_items.write_text(json.dumps(adata), encoding="utf-8")

    resp = client.get("/attention")
    assert resp.status_code == 200
    serialized = json.dumps(resp.json())
    # No raw credential material survives serialization.
    assert "sk-secret-test-abcdef1234567890" not in serialized
    assert "ghp_testSecretValue1234567890abcdef" not in serialized
    assert "api_key=ghp" not in serialized
    # The masked marker is present where redaction happened.
    assert "[REDACTED]" in serialized
