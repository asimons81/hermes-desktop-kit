"""Approval Inbox V2 — normalized candidate model (Card C).

Pure standard-library schema helpers for the adapter layer. Defines the
candidate envelope adapters emit, the binding source namespaces, the §7
suppression-code enum, per-source §8.1 canonical fingerprint fields, and
small deterministic extraction helpers (cron job ids, WordPress post ids,
TRT marker frontmatter).

This module does NOT classify: adapters preserve source provenance and
apply only source-provable pre-classification suppression codes. Everything
about actionability / attention class / owner / confidence / severity is the
classifier's job (Card D, attention_rules.py).

Binding docs:
    plans/hermes-approval-inbox-v2/evidence/attention-contract.md
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# Source namespaces (attention-contract §9 source_keys).
SOURCES = ("action", "ledger", "kanban", "cron", "trt")

# Exhaustive suppression-code enum (attention-contract §7). Every value an
# adapter attaches MUST be one of these exact codes.
SUPPRESSION_CODES = frozenset({
    "published",
    "marker_only",
    "resolved",
    "stale_resolved",
    "dependency_gated",
    "agent_owned",
    "no_reason",
    "first_fire",
    "disabled",
    "scheduler_restart",
    "recovered",
    "out_of_window",
    "unknown_owner",
    "merged_victim",
})

# Canonical fingerprint fields per source (attention-contract §8.1).
CANONICAL_FIELDS: dict[str, tuple[str, ...]] = {
    "action": ("id", "status", "updated"),
    "ledger": ("id", "status", "authority", "blocker"),
    "kanban": ("board", "task_id", "status", "block_kind", "reason"),
    "cron": ("job_id", "last_status", "last_run_at", "error"),
    "trt": ("post_id", "verdict", "code", "evaluated_at", "marker_status", "marker_needs_review"),
}

# Cron job ids are 12 lowercase hex chars (52d9a0d36bfc).
_CRON_JOB_ID_RE = re.compile(r"\b[0-9a-f]{12}\b")
# WordPress post ids in this fleet are 4-5 digit numbers (1081 .. 18571).
_POST_ID_RE = re.compile(r"\b\d{4,5}\b")
_YEAR_RE = re.compile(r"^(19|20)\d{2}$")


def new_candidate(
    source: str,
    source_key: str,
    title: str,
    state: str | None = None,
) -> dict[str, Any]:
    """Build an empty, validated-by-construction candidate envelope.

    Adapters fill ``canonical`` and ``native``, then call
    ``compute_fingerprint(candidate)`` before returning.
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}; expected one of {SOURCES}")
    return {
        "source": source,
        "source_key": source_key,
        "source_keys": [source_key],
        "key": source_key,
        "fingerprint": "",
        "canonical": {},
        "anchors": {},
        "title": (title or "").strip(),
        "state": state,
        "owner": None,  # native owner/assignee only; classifier resolves owner
        "authority_evidence": None,
        "created_at": None,
        "updated_at": None,
        "evidence": [],
        "native": {},
        "suppression_reason": None,
    }


def validate_candidate(c: dict[str, Any]) -> list[str]:
    """Return a list of contract violations (empty = valid candidate)."""
    errors: list[str] = []
    if c.get("source") not in SOURCES:
        errors.append(f"source {c.get('source')!r} not in {SOURCES}")
    if not isinstance(c.get("source_key"), str) or not c["source_key"]:
        errors.append("source_key must be a non-empty string")
    if not isinstance(c.get("key"), str) or not c["key"]:
        errors.append("key must be a non-empty string")
    if not isinstance(c.get("source_keys"), list) or not c["source_keys"]:
        errors.append("source_keys must be a non-empty list")
    if not isinstance(c.get("title"), str) or not c["title"]:
        errors.append("title must be a non-empty string")
    if not isinstance(c.get("canonical"), dict):
        errors.append("canonical must be a dict")
    if not isinstance(c.get("anchors"), dict):
        errors.append("anchors must be a dict")
    if not isinstance(c.get("evidence"), list):
        errors.append("evidence must be a list")
    if c.get("suppression_reason") is not None and c["suppression_reason"] not in SUPPRESSION_CODES:
        errors.append(
            f"suppression_reason {c['suppression_reason']!r} not in §7 enum"
        )
    return errors


def compute_fingerprint(candidate: dict[str, Any]) -> str:
    """sha256 over the candidate's §8.1 canonical fields, stable JSON.

    Ignores native provenance, evidence prose, timestamps that are not
    canonical, and local view state — per attention-contract §8.1.
    """
    canonical = candidate.get("canonical", {})
    if not isinstance(canonical, dict):
        canonical = {}
    payload = json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def extract_cron_job_ids(text: str | None) -> list[str]:
    """Deterministic list of 12-hex cron job ids mentioned in text."""
    if not text:
        return []
    seen: list[str] = []
    for match in _CRON_JOB_ID_RE.findall(text):
        if match not in seen:
            seen.append(match)
    return seen


def extract_post_ids(text: str | None) -> list[str]:
    """Deterministic list of WordPress post/draft ids mentioned in text.

    Excludes 4-digit year-like tokens (1900-2099) so ISO timestamps in
    blocker/reason text do not pollute the post-id anchor.
    """
    if not text:
        return []
    seen: list[str] = []
    for match in _POST_ID_RE.findall(text):
        if _YEAR_RE.match(match):
            continue
        if match not in seen:
            seen.append(match)
    return seen


def parse_marker_frontmatter(text: str) -> dict[str, Any]:
    """Parse YAML-ish frontmatter from a TRT ``*.blocked.md`` marker.

    Full-file parse: reads every line, takes the block between the opening
    ``---`` and the next top-level ``---``, and extracts only top-level
    scalar keys (nested blocks are skipped). Boolean values are normalized to
    Python bools. Returns {} when there is no frontmatter.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, Any] = {}
    in_block = False
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not in_block:
            # Top-level scalar key: `key: value` with no leading whitespace.
            m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
            if m:
                key, value = m.group(1), m.group(2).strip()
                if value:
                    out[key] = _coerce_frontmatter_value(value)
                else:
                    # Nested block follows; skip until the next top-level key.
                    in_block = True
        else:
            # Inside a nested block: a non-indented `key:` line ends it.
            if re.match(r"^[A-Za-z_][\w-]*:", line):
                m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
                if m and m.group(2).strip():
                    out[m.group(1)] = _coerce_frontmatter_value(m.group(2).strip())
                in_block = False
    return out


def _coerce_frontmatter_value(value: str) -> Any:
    """Normalize a scalar frontmatter value (quotes, booleans, numbers)."""
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    low = v.lower()
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
        return False
    return v
