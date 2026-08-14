"""Approval Inbox V2 — deterministic classification, verification, dedupe, ranking (Card D).

Turns the Card C normalized candidates into trusted attention buckets. This
module implements the binding rules from
plans/hermes-approval-inbox-v2/evidence/attention-contract.md:

    §2 attention_class (incident > input_required > approval > decision > watching)
    §3 actionability decision table (the primary-queue gate)
    §4 owner resolution (never from titles alone)
    §5 confidence (high = 2+ structured signals; medium = 1; low = title-only)
    §6 severity + rank order within human_now
    §7 suppression codes (every suppression carries a machine-readable reason)
    §8.1 merged fingerprint (union of canonical fields, sorted, hashed)
    §9 dedupe anchors (structured anchors first; title fallback is last resort)
    §13.1 API invariants (deterministic output for identical fixtures)

The adapter layer (Card C) does NOT classify; this module is the only place
actionability / attention_class / owner / confidence / severity are decided.
Deterministic: no LLM, no wall-clock randomness — identical fixtures produce
identical output (modulo generated timestamps).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

# Binding enums (§1 field table).
ATTENTION_CLASSES = ("decision", "approval", "input_required", "incident", "watching")
ACTIONABILITY = ("human_now", "agent_fixable", "dependency_wait", "informational")
OWNERS = ("tony", "default", "hermes-dev", "hermes-researcher", "trt", "growth", "unknown")
SEVERITIES = ("urgent", "high", "normal", "low")
CONFIDENCES = ("high", "medium", "low")
VERIFY_STATUS = ("verified", "stale", "unverified")

# §7 suppression codes that remove a record entirely (never returned).
PRE_CLASSIFICATION_SUPPRESSED = frozenset({
    "published", "resolved", "stale_resolved", "recovered", "out_of_window",
})
# §7 codes that keep the record as an informational item.
INFORMATIONAL_SUPPRESSED = frozenset({
    "marker_only", "first_fire", "disabled", "scheduler_restart",
})

AGENT_ASSIGNEES = frozenset({"default", "hermes-dev", "hermes-researcher", "trt", "growth"})

# How old a source-native record must be before its item is labeled stale.
DEFAULT_STALE_DAYS = 7

_TONY_RE = re.compile(r"\btony\b", re.I)
_DECISION_RE = re.compile(r"\bdecision\b|\bdecide\b", re.I)
_INPUT_RE = re.compile(r"\bsupply\b|\bprovide\b|\binput\b|\bimage\b|\basset\b", re.I)
_APPROVAL_RE = re.compile(r"\bapprov\w*", re.I)
_DEPENDENCY_PROOF_RE = re.compile(
    r"not dispatched until|verified parent|parent artifact|depends on|awaiting parent|"
    r"waiting on artifact|parent wait",
    re.I,
)
_FREEZE_PROOF_RE = re.compile(r"freeze|graph construction|no worker should run|internal", re.I)
_REVIEW_REQUIRED_RE = re.compile(r"review-required|requesting independent review|needs (human|maintainer|independent) review", re.I)
_DRIFT_RE = re.compile(r"drift|unintended spend|inference config drifted", re.I)
_AGENT_FIXABLE_CRON_RE = re.compile(r"HTTP 5\d\d|blocked_config|import error|exit -|traceback", re.I)
_PR_RE = re.compile(r"PR #(\d+)|github\.com/[^\s/]+/[^\s/]+/pull/(\d+)", re.I)
# §10 boundary 2: public-repo PR merge gates need Tony even without the word
# "Tony" in the reason (live: t_f6d34b2a "Needs human/independent review
# before merge").
_PR_MERGE_LANG_RE = re.compile(r"\bmerge\b|review before merge|maintainer review|human/independent review", re.I)
# Issue/PR scope tokens for stale_resolved cross-source contradiction (§7):
# "#N" refs and hyphen/slash runs like "62-66" (NOT 12-hex cron ids or WP
# post ids, which are single numbers).
_SCOPE_TOKEN_RE = re.compile(r"#(\d+)|(?:^|[-/])(\d{2,4})(?=[-/]|$)")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Post-publication / already-live gates (§12.3 row 13): piece already live →
# low severity. Explicitly called out in review Finding 1 (t_8d211f86).
_ALREADY_LIVE_RE = re.compile(
    r"already live|published.*verified|post-publication", re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------------

def _iso_now(now: datetime | None) -> str:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.isoformat()


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _names_tony(text: str | None) -> bool:
    """True when text names Tony as an actor.

    A filesystem path segment such as ``/home/user/projects/...`` is NOT an
    explicit operator actor (live: kanban reasons often reference the
    operator's home dir) — skip matches followed by a path separator.
    ``coordinator/tony -- do not auto-merge`` (github-health) still counts
    because the name is followed by a space.
    """
    if not text:
        return False
    s = str(text)
    for m in _TONY_RE.finditer(s):
        nxt = s[m.end():m.end() + 1] if m.end() < len(s) else ""
        if nxt in ("/", "\\"):
            continue  # path segment, not an actor mention
        return True
    return False


def _blocker_of(c: dict[str, Any]) -> str | None:
    """The action/ledger blocker text wherever the adapter stored it."""
    return (
        (c.get("canonical") or {}).get("blocker")
        or (c.get("native") or {}).get("blocker")
        or None
    )


def _truncate(text: str | None, limit: int = 300) -> str | None:
    if not text:
        return None
    t = str(text).strip()
    return t if len(t) <= limit else t[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Anchor extraction (§9) — structured anchors for the deduper
# ---------------------------------------------------------------------------

def _candidate_anchor_entries(c: dict[str, Any]) -> list[tuple[int, str]]:
    """Return dedupe anchor entries as (priority, token) pairs.

    Priority order (§9): 1 explicit gate/action ID, 2 cron job ID,
    3 kanban task/project ID (scoped), 4 TRT slug / WP draft ID,
    5 artifact path / PR ref. Title fallback (6) is handled separately and
    NEVER merges at high confidence.
    """
    entries: list[tuple[int, str]] = []
    source = c.get("source")
    anchors = c.get("anchors") or {}
    canonical = c.get("canonical") or {}

    if source == "action":
        aid = anchors.get("action_id")
        if aid:
            entries.append((1, f"action:{aid}"))
        for jid in anchors.get("cron_job_ids") or []:
            entries.append((2, f"cron:{jid}"))
        for pid in anchors.get("post_ids") or []:
            entries.append((4, f"trt:{pid}"))
        # F3: extract slug-like tokens from blocker/artifact text so TRT
        # markers and action items share trt:slug: anchors. Saves live
        # trt-t235e7ab9-dup-decision ↔ openai-education-plugins-2026 merge.
        native = c.get("native") or {}
        blocker_text = native.get("blocker") or ""
        artifact_text = native.get("artifact") or ""
        for slug in _extract_slugs(f"{blocker_text} {artifact_text}"):
            entries.append((4, f"trt:slug:{slug}"))
    elif source == "ledger":
        aid = anchors.get("action_id")
        if aid:
            entries.append((1, f"ledger:{aid}"))
        for jid in anchors.get("cron_job_ids") or []:
            entries.append((2, f"cron:{jid}"))
    elif source == "cron":
        jid = anchors.get("cron_job_id")
        if jid:
            entries.append((2, f"cron:{jid}"))
    elif source == "kanban":
        board = canonical.get("board")
        task_id = canonical.get("task_id")
        if board and task_id:
            entries.append((3, f"kanban:{board}:{task_id}"))
        for ref in _extract_pr_refs(c):
            entries.append((5, f"pr:{ref}"))
    elif source == "trt":
        for pid in anchors.get("post_ids") or []:
            entries.append((4, f"trt:{pid}"))
        slug = anchors.get("trt_slug")
        if slug:
            entries.append((4, f"trt:slug:{slug}"))

    # Artifact path anchor (priority 5) — exact path equality only. The
    # kanban board DB path and cron executions/jobs paths are SOURCE
    # CONTAINERS shared by every candidate of that source — they would merge
    # unrelated rows, so they are excluded here (kanban merges via PR refs;
    # cron merges via job id).
    if source in ("trt",):
        for ev in c.get("evidence") or []:
            entries.append((5, f"path:{ev}"))
    return entries


def _extract_pr_refs(c: dict[str, Any]) -> list[str]:
    """Pull PR references out of reason/authority/title text (deterministic)."""
    canonical = c.get("canonical") or {}
    text = " ".join(
        str(x) for x in (
            canonical.get("reason"),
            c.get("authority_evidence"),
            c.get("title"),
        ) if x
    )
    refs: list[str] = []
    for m in _PR_RE.finditer(text):
        refs.append(m.group(1) or m.group(2))
    return sorted(set(refs))


_SLUG_RE = re.compile(
    r"\b([a-z][a-z0-9]{3,}(?:-[a-z0-9]+)+)\b",
    re.IGNORECASE,
)
_SLUG_STOP = frozenset({"tony", "assign", "your", "that", "this", "with", "true", "false",
                         "none", "only", "into", "from", "will", "been", "also", "such",
                         "they", "were", "than", "then", "open", "gate", "item",
                         "alive", "live", "close", "need", "more", "done", "keep",
                         "each", "make", "same", "part", "name", "take", "have",
                         "must", "does", "left", "just", "last", "next", "most",
                         "here", "when", "what", "well", "mode", "kind"})


def _extract_slugs(text: str) -> list[str]:
    """Extract slug-like tokens from free text (e.g. openai-education-plugins-2026)."""
    if not text:
        return []
    out: set[str] = set()
    for m in _SLUG_RE.finditer(str(text)):
        slug = m.group(1).lower().rstrip("-")
        if slug and slug not in _SLUG_STOP and not slug.isdigit():
            out.add(slug)
    return sorted(out)


def _scope_tokens(text: str | None) -> set[str]:
    """Deterministic issue/PR scope tokens for stale_resolved checks.

    Catches "#62", "62-66", "62/66", "PR #77" style refs. Excludes 12-hex
    cron job ids and 4-5 digit WP post ids (single numbers without a hyphen
    run, or hex runs).
    """
    if not text:
        return set()
    out: set[str] = set()
    for m in _SCOPE_TOKEN_RE.finditer(str(text)):
        tok = m.group(1) or m.group(2)
        if tok:
            out.add(tok)
    return out


def _normalized_title(title: str | None) -> str:
    t = (title or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _stale_resolved_action_keys(candidates: list[dict[str, Any]]) -> set[str]:
    """source_keys of open action candidates contradicted by a completed
    ledger record sharing issue/PR scope tokens (§7 stale_resolved).

    Live (§12.1 row 2): action ``hermes-vault-62-66-merge-gate`` demands
    closing issues #62/#66, while ledger ``hermes-vault-62-66-live-followups``
    is completed with evidence — the issue-close portion is done, so the
    action item is suppressed. A surviving sub-gate (PR #77) re-enters via
    its own kanban evidence; no valid gate is hidden.
    """
    ledger_tokens: set[str] = set()
    for c in candidates:
        if c.get("source") != "ledger" or (c.get("canonical") or {}).get("status") != "completed":
            continue
        text = " ".join(str(x) for x in [
            c.get("title"),
            (c.get("canonical") or {}).get("blocker"),
            (c.get("native") or {}).get("next_action"),
            " ".join(str(e) for e in (c.get("evidence") or [])),
        ] if x)
        ledger_tokens |= _scope_tokens(text)
    if not ledger_tokens:
        return set()

    stale: set[str] = set()
    for c in candidates:
        if c.get("source") != "action":
            continue
        if (c.get("state") or "open") not in (None, "open"):
            continue  # closed/resolved handled elsewhere
        text = " ".join(str(x) for x in [
            c.get("title"),
            (c.get("canonical") or {}).get("blocker"),
            (c.get("native") or {}).get("blocker"),
            (c.get("native") or {}).get("artifact"),
            " ".join(str(e) for e in (c.get("evidence") or [])),
        ] if x)
        if _scope_tokens(text) & ledger_tokens:
            stale.add(c["source_key"])
    return stale


def _merged_key(group: list[dict[str, Any]]) -> tuple[str, str, int]:
    """Deterministic merged key + winning anchor for a candidate group.

    Returns (key, anchor_token, priority). For singleton groups the key is
    ``att:<source_key>``; for merges it is derived from the highest-priority
    shared anchor (§1 field 1: att:<anchor-source>:<anchor-value>).
    """
    if len(group) == 1:
        c = group[0]
        return f"att:{c['source_key']}", "", 99

    # Find the highest-priority anchor shared by >= 2 group members.
    shared: dict[tuple[int, str], list[int]] = {}
    for idx, c in enumerate(group):
        for priority, token in _candidate_anchor_entries(c):
            shared.setdefault((priority, token), []).append(idx)

    best: tuple[int, str] | None = None
    for (priority, token), members in shared.items():
        if len(members) >= 2:
            if best is None or priority < best[0] or (priority == best[0] and token < best[1]):
                best = (priority, token)
    if best is None:
        # No shared anchor: deterministic fallback to first sorted source_key.
        first = sorted(c["source_key"] for c in group)[0]
        return f"att:{first}", "", 99

    priority, token = best
    if token.startswith(("cron:", "trt:", "action:", "ledger:", "kanban:")):
        return f"att:{token}", token, priority
    if token.startswith("pr:"):
        # PR ref is an artifact anchor; scope to the first sorted kanban card.
        kanban_keys = sorted(
            c["source_key"] for c in group if c["source_key"].startswith("kanban:")
        )
        if kanban_keys:
            return f"att:{kanban_keys[0]}", token, priority
        return f"att:{sorted(c['source_key'] for c in group)[0]}", token, priority
    # path anchor or unknown: deterministic first-sorted source_key.
    return f"att:{sorted(c['source_key'] for c in group)[0]}", token, priority


def _anchor_holder_key(group: list[dict[str, Any]], anchor_token: str) -> str | None:
    """The source_key that owns the winning anchor (merge anchor holder).

    Returns None for artifact/pr/path anchors that have no single owner.
    """
    if not anchor_token:
        return None
    for c in group:
        if c["source_key"] == anchor_token:
            return c["source_key"]
    # A kanban scoped anchor matches the canonical form of its own source_key.
    if anchor_token.startswith("kanban:"):
        return anchor_token
    return None


# ---------------------------------------------------------------------------
# Deduper (§9)
# ---------------------------------------------------------------------------

def _group_candidates(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Union-find grouping by shared structured anchors, deterministic."""
    ordered = sorted(candidates, key=lambda c: (c["source_key"], c["key"]))
    token_map: dict[str, list[int]] = {}
    for i, c in enumerate(ordered):
        for _priority, token in _candidate_anchor_entries(c):
            token_map.setdefault(token, []).append(i)

    parent = list(range(len(ordered)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for token, members in token_map.items():
        if len(members) >= 2 and not token.startswith("kanban:"):
            # kanban scoped ids are identity anchors, not merge anchors —
            # a card never merges with itself through its own id.
            for j in members[1:]:
                union(members[0], j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, c in enumerate(ordered):
        groups.setdefault(find(i), []).append(c)
    return [groups[k] for k in sorted(groups)]


def _merged_fingerprint(group: list[dict[str, Any]]) -> str:
    """§8.1 merged fingerprint: union of contributing canonical fields, sorted."""
    payload: list[tuple[str, dict[str, Any]]] = []
    for c in sorted(group, key=lambda c: c["source_key"]):
        payload.append((c["source_key"], c.get("canonical") or {}))
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Verifier (§1 field 16) — verified / stale / unverified
# ---------------------------------------------------------------------------

def _gate_evidence(c: dict[str, Any]) -> list[str]:
    """Gate-specific evidence for verification (source-aware).

    A kanban DB path alone does not verify a gate claim — the reason/event
    does. Markers/receipts keep their paths; action/ledger/cron keep theirs.
    """
    if c.get("source") == "kanban":
        reason = (c.get("canonical") or {}).get("reason")
        if reason:
            board = (c.get("canonical") or {}).get("board")
            task_id = (c.get("canonical") or {}).get("task_id")
            return [f"kanban event reason ({board}:{task_id})"]
        return []
    out = []
    for ev in c.get("evidence") or []:
        if str(ev) not in out:
            out.append(str(ev))
    return out


def _verify(group: list[dict[str, Any]], now: datetime | None, stale_days: int) -> dict[str, Any]:
    """Compute {verified_at, status, evidence} for a group's attention item.

    Staleness uses the LEAST-recently-updated contributing record: if any
    evidence source's last source update is old, the item is labeled stale
    (cannot masquerade as a current fact, per §2 done criteria). Created_at
    (first appearance) is only a fallback when updated_at is missing.
    """
    evidence: list[str] = []
    latest: datetime | None = None
    for c in sorted(group, key=lambda c: c["source_key"]):
        for ev in _gate_evidence(c):
            if ev not in evidence:
                evidence.append(ev)
        ts = _parse_iso(c.get("updated_at")) or _parse_iso(c.get("created_at"))
        if ts and (latest is None or ts < latest):
            latest = ts

    status = "unverified"
    if evidence:
        if latest is None:
            status = "verified"  # evidence exists; no age signal to contradict
        else:
            cutoff = (now if now is not None else datetime.now(timezone.utc)) - timedelta(days=stale_days)
            if latest < cutoff:
                status = "stale"
            else:
                status = "verified"
    return {
        "verified_at": _iso_now(now) if status != "unverified" else None,
        "status": status,
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# Classifier (§2–§5, §7)
# ---------------------------------------------------------------------------

def _suppression_for_group(group: list[dict[str, Any]]) -> str | None:
    """Return the §7 code for an informational/dependency group, or None."""
    for c in group:
        reason = c.get("suppression_reason")
        if reason in INFORMATIONAL_SUPPRESSED:
            return reason
    # Kanban-derived informational codes.
    for c in group:
        if c.get("source") != "kanban":
            continue
        canonical = c.get("canonical") or {}
        native = c.get("native") or {}
        reason = canonical.get("reason")
        block_kind = canonical.get("block_kind")
        if c.get("state") == "todo":
            if native.get("parents"):
                return "dependency_gated"
            return "unknown_owner"
        if block_kind is None and not reason:
            return "no_reason"
        if reason and _FREEZE_PROOF_RE.search(reason):
            return "agent_owned" if native.get("assignee") in AGENT_ASSIGNEES else "no_reason"
        if reason and _REVIEW_REQUIRED_RE.search(reason):
            return "agent_owned"
        if block_kind in ("needs_input", "capability") and not _names_tony(reason):
            return "agent_owned" if native.get("assignee") in AGENT_ASSIGNEES else "unknown_owner"
        return "no_reason"
    return None


def _classify_group(group: list[dict[str, Any]], now: datetime | None) -> dict[str, Any]:
    """Classify one merged group into an AttentionItem (binding fields)."""
    # ---- gather cross-source signals ------------------------------------
    ledger_human_gate = any(
        c.get("source") == "ledger"
        and (c.get("canonical") or {}).get("authority") == "human_gate"
        and c.get("state") == "approval_required"
        for c in group
    )
    action_tony = any(
        c.get("source") == "action" and _names_tony(_blocker_of(c))
        for c in group
    )
    kanban_tony = any(
        c.get("source") == "kanban"
        and c.get("state") == "blocked"
        and (c.get("canonical") or {}).get("block_kind") in ("needs_input", "capability")
        and _names_tony((c.get("canonical") or {}).get("reason"))
        for c in group
    )
    # §10 boundary 2 + §12.3 row 20: public-repo PR merge gates are human_only
    # even WITHOUT the literal word "Tony" — the gate evidence is a real PR
    # ref + merge/review-before-merge language (live: t_f6d34b2a "Needs
    # human/independent review before merge" of hermes-gpt PR #7).
    kanban_pr_merge = any(
        c.get("source") == "kanban"
        and c.get("state") == "blocked"
        and (c.get("canonical") or {}).get("block_kind") in ("needs_input", "capability")
        and bool(_extract_pr_refs(c))
        and bool(_PR_MERGE_LANG_RE.search((c.get("canonical") or {}).get("reason") or ""))
        for c in group
    )
    kanban_freeze = any(
        c.get("source") == "kanban"
        and c.get("state") == "blocked"
        and _FREEZE_PROOF_RE.search((c.get("canonical") or {}).get("reason") or "")
        for c in group
    )
    kanban_dependency = any(
        c.get("source") == "kanban"
        and c.get("state") == "blocked"
        and _DEPENDENCY_PROOF_RE.search((c.get("canonical") or {}).get("reason") or "")
        for c in group
    )
    kanban_todo_parent = any(
        c.get("source") == "kanban"
        and c.get("state") == "todo"
        and bool((c.get("native") or {}).get("parents"))
        for c in group
    )
    trt_blocked_tony = any(
        c.get("source") == "trt"
        and (c.get("canonical") or {}).get("verdict") in ("BLOCKED", "HOLD")
        and (c.get("canonical") or {}).get("code") == "missing_source_pack"
        and action_tony
        for c in group
    )
    trt_editorial = any(
        c.get("source") == "trt"
        and (c.get("canonical") or {}).get("verdict") in ("BLOCKED", "HOLD")
        and not trt_blocked_tony
        for c in group
    )
    cron_failed = any(
        c.get("source") == "cron" and c.get("state") in ("failed", "error")
        for c in group
    )
    cron_drift = any(
        c.get("source") == "cron"
        and c.get("state") in ("failed", "error")
        and _DRIFT_RE.search((c.get("canonical") or {}).get("error") or "")
        for c in group
    )
    informational_suppressed = any(
        c.get("suppression_reason") in INFORMATIONAL_SUPPRESSED for c in group
    )
    action_no_tony = any(
        c.get("source") == "action"
        and not _names_tony(_blocker_of(c))
        and bool(_blocker_of(c))
        for c in group
    )
    ledger_reversible = any(
        c.get("source") == "ledger"
        and (c.get("canonical") or {}).get("authority") == "reversible_local"
        for c in group
    )

    # ---- §3 decision table ------------------------------------------------
    if ledger_human_gate:
        actionability = "human_now"
        attention_class = "approval"
        blocker_text = next(
            ((c.get("canonical") or {}).get("blocker") or "") for c in group
            if c.get("source") == "ledger" and (c.get("canonical") or {}).get("authority") == "human_gate"
        )
        if _DECISION_RE.search(blocker_text):
            attention_class = "decision"
        elif _INPUT_RE.search(blocker_text):
            attention_class = "input_required"
        owner = "tony"
    elif action_tony:
        actionability = "human_now"
        blocker_text = next(
            _blocker_of(c) for c in group
            if c.get("source") == "action" and _names_tony(_blocker_of(c))
        )
        if _DECISION_RE.search(blocker_text):
            attention_class = "decision"
        elif _INPUT_RE.search(blocker_text):
            attention_class = "input_required"
        elif _APPROVAL_RE.search(blocker_text):
            attention_class = "approval"
        else:
            attention_class = "approval"
        owner = "tony"
    elif kanban_tony:
        actionability = "human_now"
        attention_class = "approval"
        owner = "tony"
    elif kanban_pr_merge:
        actionability = "human_now"
        attention_class = "approval"
        owner = "tony"
    elif trt_blocked_tony:
        actionability = "human_now"
        attention_class = "input_required"
        owner = "tony"
    elif cron_drift and (ledger_human_gate or action_tony):
        actionability = "human_now"
        attention_class = "approval"
        owner = "tony"
    elif informational_suppressed:
        # first-fire / disabled / scheduler-restart / marker_only — watching.
        actionability = "informational"
        attention_class = "watching"
        owner = "default"
    elif kanban_dependency:
        actionability = "dependency_wait"
        attention_class = "watching"
        owner = _kanban_owner(group)
    elif kanban_todo_parent:
        actionability = "dependency_wait"
        attention_class = "watching"
        owner = _kanban_owner(group)
    elif cron_drift:
        # model-drift guard WITHOUT an explicit Tony decision → watching.
        actionability = "informational"
        attention_class = "watching"
        owner = "default"
    elif cron_failed:
        actionability = "agent_fixable"
        attention_class = "watching"
        owner = "default"
    elif trt_editorial:
        actionability = "agent_fixable"
        attention_class = "watching"
        owner = "trt"
    elif action_no_tony:
        actionability = "agent_fixable"
        attention_class = "watching"
        owner = "default"
    elif ledger_reversible:
        actionability = "informational"
        attention_class = "watching"
        owner = "default"
    elif kanban_freeze:
        actionability = "informational"
        attention_class = "watching"
        owner = _fallback_owner(group)
    else:
        actionability = "informational"
        attention_class = "watching"
        owner = _fallback_owner(group)

    # ---- §5 confidence -----------------------------------------------------
    signals = sum([
        int(ledger_human_gate),
        int(action_tony),
        int(kanban_tony),
        int(kanban_pr_merge),
        int(bool(trt_blocked_tony and action_tony)),
        int(bool(cron_drift and (ledger_human_gate or action_tony))),
    ])
    if signals >= 2:
        confidence = "high"
    elif signals == 1:
        confidence = "medium"
    else:
        confidence = "low"
    # Two kanban cards on the same PR are two independent structured signals.
    if (
        kanban_tony
        and len([c for c in group if c.get("source") == "kanban" and c.get("state") == "blocked"]) >= 2
    ):
        confidence = "high"

    # ---- §6 severity --------------------------------------------------------
    severity = _severity(group, now, attention_class, actionability)

    # ---- fields -------------------------------------------------------------
    key, anchor_token, _prio = _merged_key(group)
    source_keys = sorted({c["source_key"] for c in group})
    title, authority, why_tony, reason_now, recommended, project = _item_prose(
        group, actionability, attention_class
    )
    suppression = None
    if actionability == "dependency_wait":
        suppression = "dependency_gated"
    elif actionability == "informational":
        suppression = _suppression_for_group(group)

    recurrences = max(
        [int((c.get("native") or {}).get("block_recurrences") or 0)
         for c in group if c.get("source") == "kanban"] or [0]
    )
    release_gate = attention_class == "approval" and bool(
        _PR_RE.search(" ".join(str(x) for c in group for x in [
            (c.get("canonical") or {}).get("reason"),
            (c.get("canonical") or {}).get("blocker"),
            c.get("title"),
        ] if x))
        or "merge" in " ".join(str(x) for c in group for x in [
            (c.get("canonical") or {}).get("reason"),
            (c.get("canonical") or {}).get("blocker"),
            c.get("title"),
        ] if x).lower()
    )
    input_gate = attention_class == "input_required"

    item = {
        "key": key,
        "source_keys": source_keys,
        "attention_class": attention_class,
        "actionability": actionability,
        "owner": owner,
        "authority": authority,
        "title": title,
        "why_tony": why_tony if actionability == "human_now" else None,
        "reason_now": reason_now,
        "recommended_action": recommended if actionability == "human_now" else None,
        "alternatives": [],
        "consequence_of_delay": _consequence_of_delay(attention_class) if actionability == "human_now" else None,
        "project": project,
        "severity": severity,
        "confidence": confidence,
        "verification": _verify(group, now, DEFAULT_STALE_DAYS),
        "created_at": _min_ts(group, "created_at"),
        "updated_at": _max_ts(group, "updated_at"),
        "source_health": [],
        "fingerprint": _merged_fingerprint(group),
        "view_state": {
            "snoozed_until": None,
            "hidden": False,
            "hidden_reason": None,
            "fingerprint_at_hide": None,
        },
        "suppression_reason": suppression,
        # private rank metadata (stripped before the envelope is returned)
        "_recurrences": recurrences,
        "_release_gate": release_gate,
        "_input_gate": input_gate,
    }
    return item


def _kanban_owner(group: list[dict[str, Any]]) -> str:
    for c in group:
        if c.get("source") == "kanban":
            assignee = (c.get("native") or {}).get("assignee")
            if assignee in AGENT_ASSIGNEES:
                return assignee
    return "default"


def _fallback_owner(group: list[dict[str, Any]]) -> str:
    for c in group:
        if c.get("source") == "kanban":
            assignee = (c.get("native") or {}).get("assignee")
            if assignee:
                return assignee
        if c.get("source") == "trt":
            return "trt" if (c.get("canonical") or {}).get("verdict") in ("BLOCKED", "HOLD") else "default"
    return "unknown"


def _severity(group: list[dict[str, Any]], now: datetime | None, attention_class: str, actionability: str) -> str:
    if actionability != "human_now":
        return "low"
    due = next(
        ((c.get("native") or {}).get("due_at") for c in group
         if c.get("source") == "ledger" and (c.get("native") or {}).get("due_at")),
        None,
    )
    if due:
        due_dt = _parse_iso(due)
        ref = now if now is not None else datetime.now(timezone.utc)
        if due_dt and due_dt < ref:
            return "urgent"
        if due_dt and due_dt <= ref + timedelta(days=2):
            return "high"
    text = " ".join(
        str(x) for c in group for x in [
            (c.get("canonical") or {}).get("reason"),
            (c.get("canonical") or {}).get("blocker"),
            c.get("title"),
        ] if x
    )
    if attention_class == "approval":
        if _PR_RE.search(text) or "merge" in text.lower():
            return "high"
    if attention_class == "input_required":
        return "high"
    if attention_class == "decision":
        return "normal"
    if _ALREADY_LIVE_RE.search(text):
        return "low"
    return "normal"


def _item_prose(group: list[dict[str, Any]], actionability: str, attention_class: str) -> tuple:
    """(title, authority, why_tony, reason_now, recommended_action, project)."""
    def first_source(*sources: str) -> dict[str, Any] | None:
        for src in sources:
            for c in sorted(group, key=lambda c: c["source_key"]):
                if c.get("source") == src:
                    return c
        return None

    ledger = first_source("ledger")
    action = first_source("action")
    kanban = first_source("kanban")
    trt = first_source("trt")
    cron = first_source("cron")

    title = (
        (ledger or {}).get("title")
        or (action or {}).get("title")
        or (kanban or {}).get("title")
        or (trt or {}).get("title")
        or (cron or {}).get("title")
        or "Untitled"
    )

    authority = None
    why_tony = None
    reason_now = None
    recommended = None
    project = None

    if ledger:
        native = ledger.get("native") or {}
        canonical = ledger.get("canonical") or {}
        project = native.get("project") or project
        authority = _truncate(native.get("next_action") or canonical.get("blocker"))
        why_tony = "Explicit human-gate approval required (authority=human_gate)"
        due = native.get("due_at")
        if due:
            reason_now = f"DUE {due}"
        else:
            reason_now = "Approval gate active"
        recommended = authority or "Approve the requested action"
    elif action:
        native = action.get("native") or {}
        blocker = _blocker_of(action) or ""
        authority = _truncate(blocker)
        why_tony = blocker or "Tony action required"
        reason_now = "Explicit Tony action item open"
        recommended = authority
    elif kanban:
        canonical = kanban.get("canonical") or {}
        native = kanban.get("native") or {}
        reason = canonical.get("reason")
        authority = _truncate(reason)
        why_tony = reason or "Tony approval required"
        reason_now = reason or "Blocked gate"
        recommended = reason or "Resolve the block"
        project = canonical.get("board") or native.get("board") or project
    elif trt:
        canonical = trt.get("canonical") or {}
        verdict = canonical.get("verdict")
        code = canonical.get("code")
        post_id = canonical.get("post_id")
        authority = f"Gate {verdict} {code} for WP post {post_id}" if post_id else f"Gate {verdict} {code}"
        why_tony = "Tony-owned asset/input required"
        reason_now = f"Gate {verdict} {code}"
        recommended = authority
    elif cron:
        canonical = cron.get("canonical") or {}
        error = canonical.get("error")
        authority = _truncate(error)
        why_tony = None
        reason_now = f"Latest execution: {canonical.get('last_status')}"
        recommended = None

    return title, authority, why_tony, reason_now, recommended, project


def _consequence_of_delay(attention_class: str) -> str:
    if attention_class == "approval":
        return "Gate stays blocked; the requested outbound action remains unperformed"
    if attention_class == "input_required":
        return "Gate stays BLOCKED; publication/delivery is delayed until the input is supplied"
    if attention_class == "decision":
        return "Decision deferred; duplicate/stale state persists until resolved"
    return "Gate stays unresolved until addressed"


def _min_ts(group: list[dict[str, Any]], key: str) -> str | None:
    vals = [_parse_iso(c.get(key)) for c in group]
    vals = [v for v in vals if v is not None]
    return min(vals).isoformat() if vals else None


def _max_ts(group: list[dict[str, Any]], key: str) -> str | None:
    vals = [_parse_iso(c.get(key)) for c in group]
    vals = [v for v in vals if v is not None]
    return max(vals).isoformat() if vals else None


# ---------------------------------------------------------------------------
# Ranker (§6.2 + Card D acceptance: stale/unverified rank below verified)
# ---------------------------------------------------------------------------

_VERIFY_RANK = {"verified": 0, "stale": 1, "unverified": 2}
_CONF_RANK = {"high": 0, "medium": 1, "low": 2}


def _rank_key(item: dict[str, Any], now: datetime | None) -> tuple:
    """Deterministic rank key for human_now items.

    Verification is the first key so a stale/unverified gate NEVER ranks above
    a verified gate (Card D acceptance). Then §6.2: overdue, repeated blocked
    recurrence, release/security/publication gate, input required, age,
    confidence. Tie-break: key lexicographic.
    """
    verification = _VERIFY_RANK.get((item.get("verification") or {}).get("status"), 2)

    overdue = 0
    due_m = re.search(r"DUE (.+)", (item.get("reason_now") or ""))
    if due_m:
        due_dt = _parse_iso(due_m.group(1))
        ref = now if now is not None else datetime.now(timezone.utc)
        if due_dt and due_dt < ref:
            overdue = 1

    recurrence = 0 if int(item.get("_recurrences") or 0) >= 2 else 1
    release_gate = 0 if item.get("_release_gate") else 1
    input_gate = 0 if item.get("_input_gate") else 1
    age = _parse_iso(item.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc)
    confidence = _CONF_RANK.get(item.get("confidence"), 2)
    return (
        0 if verification == 0 else 1,
        0 if overdue else 1,
        recurrence,
        release_gate,
        input_gate,
        age,
        confidence,
        item["key"],
    )


def _rank_items(items: list[dict[str, Any]], now: datetime | None) -> list[dict[str, Any]]:
    """Order human_now items per §6.2; secondary buckets sorted deterministically."""
    human_now = [i for i in items if i["actionability"] == "human_now"]
    secondary = [i for i in items if i["actionability"] != "human_now"]

    human_now.sort(key=lambda i: _rank_key(i, now))
    secondary.sort(key=lambda i: (
        _parse_iso(i.get("created_at")) or datetime.min.replace(tzinfo=timezone.utc),
        i["key"],
    ))
    return human_now + secondary


# ---------------------------------------------------------------------------
# Envelope builder (§1.1)
# ---------------------------------------------------------------------------

def _health(*envelopes: dict[str, Any]) -> dict[str, Any]:
    ok = all(e.get("ok") for e in envelopes)
    errors = [e.get("error") for e in envelopes if e.get("error")]
    return {"ok": ok, "error": errors[0] if errors else None}


def _strip_private(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if not k.startswith("_")}


def build_attention(aggregate: dict[str, dict[str, Any]], now: datetime | None = None) -> dict[str, Any]:
    """Turn collect_candidates() output into the §1.1 attention envelope.

    Deterministic for identical fixtures (modulo generated_at / verified_at).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # ---- flatten candidates across sources (fixed order) ------------------
    candidates: list[dict[str, Any]] = []
    suppressed: list[dict[str, Any]] = []
    source_order = ("action_items", "task_ledger", "kanban", "cron", "trt")

    # Cross-source §7 stale_resolved: computed from ALL candidates (including
    # the completed ledger record, which is itself pre-classification
    # suppressed) BEFORE partitioning. A completed ledger sharing issue/PR
    # scope tokens contradicts an open action item still demanding them.
    all_candidates: list[dict[str, Any]] = []
    for name in source_order:
        env = aggregate.get(name) or {}
        if not env.get("ok"):
            continue
        all_candidates.extend(env.get("candidates") or [])
    stale_resolved_keys = _stale_resolved_action_keys(all_candidates)

    for name in source_order:
        env = aggregate.get(name) or {}
        if not env.get("ok"):
            continue
        for c in env.get("candidates") or []:
            reason = c.get("suppression_reason")
            if c.get("source_key") in stale_resolved_keys:
                suppressed.append({
                    "source": c.get("source"),
                    "source_key": c.get("source_key"),
                    "reason": "stale_resolved",
                })
            elif reason in PRE_CLASSIFICATION_SUPPRESSED:
                suppressed.append({
                    "source": c.get("source"),
                    "source_key": c.get("source_key"),
                    "reason": reason,
                })
            else:
                candidates.append(c)

    # ---- dedupe + classify -------------------------------------------------
    groups = _group_candidates(candidates)
    items = [_classify_group(g, now) for g in groups]

    # ---- merge-victim diagnostics (provenance retained on merged item) -----
    for g in groups:
        if len(g) <= 1:
            continue
        _key, anchor_token, _prio = _merged_key(g)
        holder = _anchor_holder_key(g, anchor_token)
        for c in g:
            if c["source_key"] == holder:
                continue
            suppressed.append({
                "source": c.get("source"),
                "source_key": c.get("source_key"),
                "reason": "merged_victim",
                "merged_into": _key,
            })

    # ---- buckets + rank -----------------------------------------------------
    all_items = [i for i in items]
    ranked = _rank_items(all_items, now)
    primary = [i for i in ranked if i["actionability"] == "human_now"]
    secondary = {
        "agent_fixable": [i for i in ranked if i["actionability"] == "agent_fixable"],
        "dependency_wait": [i for i in ranked if i["actionability"] == "dependency_wait"],
        "informational": [i for i in ranked if i["actionability"] == "informational"],
    }

    primary = [_strip_private(i) for i in primary]
    secondary = {k: [_strip_private(i) for i in v] for k, v in secondary.items()}

    suppressed.sort(key=lambda d: (d["reason"], d["source_key"]))
    counts = {
        "human_now": len(primary),
        "agent_fixable": len(secondary["agent_fixable"]),
        "dependency_wait": len(secondary["dependency_wait"]),
        "informational": len(secondary["informational"]),
        "suppressed_invalid": len(suppressed),
    }

    return {
        "generated_at": _iso_now(now),
        "verified_at": _iso_now(now) if primary else None,
        "counts": counts,
        "primary": primary,
        "secondary": secondary,
        "source_health": {
            "action_items": _health(aggregate.get("action_items") or {}, aggregate.get("task_ledger") or {}),
            "kanban": _health(aggregate.get("kanban") or {}),
            "cron": _health(aggregate.get("cron") or {}),
            "trt": _health(aggregate.get("trt") or {}),
        },
        "suppressed": suppressed,
    }
