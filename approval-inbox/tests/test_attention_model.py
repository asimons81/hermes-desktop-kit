"""Unit tests for the V2 attention candidate model (attention_model.py).

Card C scope: the normalized candidate envelope adapters emit. Tests define
the contract FIRST (strict TDD) — they fail until attention_model.py exists
and implements the binding field/fingerprint/suppression semantics from
plans/hermes-approval-inbox-v2/evidence/attention-contract.md §1/§7/§8.

Run (from the plugin dir):
    env -u PYTHONPATH python -m pytest tests/test_attention_model.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DASHBOARD = HERE.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import attention_model  # noqa: E402


# ---------------------------------------------------------------------------
# Candidate envelope
# ---------------------------------------------------------------------------

def test_new_candidate_has_all_binding_fields() -> None:
    c = attention_model.new_candidate(
        source="action",
        source_key="action:open-one",
        title="Approve pinning cron job X",
        state="open",
    )
    for field in (
        "source", "source_key", "source_keys", "key", "fingerprint",
        "canonical", "anchors", "title", "state", "owner",
        "authority_evidence", "created_at", "updated_at", "evidence",
        "native", "suppression_reason",
    ):
        assert field in c, f"candidate missing field {field}"
    assert c["source"] == "action"
    assert c["source_key"] == "action:open-one"
    assert c["source_keys"] == ["action:open-one"]
    assert c["key"] == "action:open-one"
    assert c["suppression_reason"] is None
    assert c["anchors"] == {}


def test_new_candidate_accepts_only_binding_sources() -> None:
    for source in attention_model.SOURCES:
        c = attention_model.new_candidate(source=source, source_key=f"{source}:x", title="t")
        assert c["source"] == source
    try:
        attention_model.new_candidate(source="bogus", source_key="bogus:x", title="t")
    except ValueError:
        pass
    else:  # pragma: no cover - contract violation must raise
        raise AssertionError("unknown source must raise ValueError")


def test_validate_candidate_ok() -> None:
    c = attention_model.new_candidate(source="action", source_key="action:one", title="t")
    assert attention_model.validate_candidate(c) == []


def test_validate_candidate_rejects_missing_required() -> None:
    c = attention_model.new_candidate(source="action", source_key="action:one", title="t")
    del c["source_key"]
    errors = attention_model.validate_candidate(c)
    assert any("source_key" in e for e in errors)


def test_validate_candidate_rejects_unknown_suppression_code() -> None:
    c = attention_model.new_candidate(source="action", source_key="action:one", title="t")
    c["suppression_reason"] = "not_a_real_code"
    errors = attention_model.validate_candidate(c)
    assert any("suppression_reason" in e for e in errors)


def test_suppression_codes_exhaustive_enum() -> None:
    """§7 codes are binding; the enum must contain exactly the contract codes."""
    expected = {
        "published", "marker_only", "resolved", "stale_resolved",
        "dependency_gated", "agent_owned", "no_reason", "first_fire",
        "disabled", "scheduler_restart", "recovered", "out_of_window",
        "unknown_owner", "merged_victim",
    }
    assert set(attention_model.SUPPRESSION_CODES) == expected


# ---------------------------------------------------------------------------
# Fingerprint (§8.1)
# ---------------------------------------------------------------------------

def test_fingerprint_deterministic_and_source_specific() -> None:
    a = attention_model.new_candidate(source="action", source_key="action:one", title="t")
    a["canonical"] = {"id": "one", "status": "open", "updated": "2026-08-10T00:00:00Z"}
    b = attention_model.new_candidate(source="action", source_key="action:one", title="t")
    b["canonical"] = {"id": "one", "status": "open", "updated": "2026-08-10T00:00:00Z"}
    f1 = attention_model.compute_fingerprint(a)
    f2 = attention_model.compute_fingerprint(b)
    assert f1 == f2
    assert len(f1) == 64  # sha256 hex

    # Different canonical field content -> different fingerprint
    b["canonical"]["status"] = "closed"
    assert attention_model.compute_fingerprint(b) != f1


def test_fingerprint_ignores_native_and_timestamps() -> None:
    """§8.1: fingerprint over canonical fields only, not provenance/native."""
    a = attention_model.new_candidate(source="cron", source_key="cron:52d9a0d36bfc", title="t")
    a["canonical"] = {"job_id": "52d9a0d36bfc", "last_status": "error", "last_run_at": "2026-08-03T09:00:10Z", "error": "drift"}
    a["native"] = {"noisy": "field that must not matter"}
    b = attention_model.new_candidate(source="cron", source_key="cron:52d9a0d36bfc", title="t")
    b["canonical"] = {"job_id": "52d9a0d36bfc", "last_status": "error", "last_run_at": "2026-08-03T09:00:10Z", "error": "drift"}
    b["native"] = {}
    assert attention_model.compute_fingerprint(a) == attention_model.compute_fingerprint(b)


def test_canonical_fields_are_binding_per_source() -> None:
    assert attention_model.CANONICAL_FIELDS["action"] == ("id", "status", "updated")
    assert attention_model.CANONICAL_FIELDS["ledger"] == ("id", "status", "authority", "blocker")
    assert attention_model.CANONICAL_FIELDS["kanban"] == ("board", "task_id", "status", "block_kind", "reason")
    assert attention_model.CANONICAL_FIELDS["cron"] == ("job_id", "last_status", "last_run_at", "error")
    assert set(attention_model.CANONICAL_FIELDS["trt"]) == {
        "post_id", "verdict", "code", "evaluated_at", "marker_status", "marker_needs_review",
    }


# ---------------------------------------------------------------------------
# Anchor extraction helpers
# ---------------------------------------------------------------------------

def test_extract_cron_job_ids() -> None:
    text = "Approve cronjob action=update job_id=52d9a0d36bfc model=deepseek"
    assert attention_model.extract_cron_job_ids(text) == ["52d9a0d36bfc"]
    assert attention_model.extract_cron_job_ids("no job here") == []
    assert attention_model.extract_cron_job_ids("artifact: cron job 5fa6c6b6726f") == ["5fa6c6b6726f"]


def test_extract_post_ids() -> None:
    assert attention_model.extract_post_ids("draft 18517 keeps BLOCKED") == ["18517"]
    assert attention_model.extract_post_ids("no ids") == []
    # A bare t_* kanban id must not be mistaken for a WP post id
    assert attention_model.extract_post_ids("gate t_235e7ab9") == []


# ---------------------------------------------------------------------------
# Marker frontmatter parsing (full-file, stdlib only)
# ---------------------------------------------------------------------------

def test_parse_marker_frontmatter_full_file() -> None:
    """Frontmatter is a full-file parse: the closing --- may be many lines in."""
    text = (
        "---\n"
        "title: \"Rabbit R1 Review\"\n"
        "slug: rabbit-r1-review\n"
        "status: draft\n"
        "needs_review: true\n"
        "content_type: review\n"
        "primary_keyword: \"rabbit r1\"\n"
        "seo_title: \"Rabbit R1 Review: ...\"\n"
        "meta_description: \"...\"\n"
        "excerpt: \"...\"\n"
        "category: \"AI Hardware\"\n"
        "tags: [AI, Hardware, Rabbit]\n"
        "disclosure: \"No commercial relationship\"\n"
        "hands_on_tested: true\n"
        "source_pack: \"source-packs/rabbit-r1-review-source-pack.md\"\n"
        "freshness_verified: 2026-08-07\n"
        "volatile_facts:\n"
        "  - \"Vendor-reported benchmark numbers\"\n"
        "image_required: true\n"
        "image_brief:\n"
        "  featured_image:\n"
        "    type: gpt_images\n"
        "    prompt: \"\"\n"
        "    output_size: \"1920x1080\"\n"
        "  inline_images: []\n"
        "  rationale: \"Review requires image\"\n"
        "yoast:\n"
        "  focus_keyword: \"rabbit r1\"\n"
        "  seo_title: \"...\"\n"
        "  meta_description: \"...\"\n"
        "---\n"
        "# Rabbit R1 Review\n"
        "body content...\n"
    )
    fm = attention_model.parse_marker_frontmatter(text)
    assert fm["slug"] == "rabbit-r1-review"
    assert fm["status"] == "draft"
    assert fm["needs_review"] is True


def test_parse_marker_frontmatter_normalizes_booleans() -> None:
    fm = attention_model.parse_marker_frontmatter(
        "---\nslug: x\nstatus: published\nneeds_review: false\n---\nbody\n"
    )
    assert fm["status"] == "published"
    assert fm["needs_review"] is False


def test_parse_marker_frontmatter_missing_returns_empty() -> None:
    assert attention_model.parse_marker_frontmatter("# no frontmatter\n") == {}
