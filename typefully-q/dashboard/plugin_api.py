"""Typefully Q dashboard plugin — backend API routes.

Mounted at /api/plugins/typefully-q/ by the Hermes plugin system
(manifest.json declares api=plugin_api.py; import requires the plugin to be
in plugins.enabled in config.yaml).

This proxies to the Typefully API v2 (https://api.typefully.com/v2) using
TYPEFULLY_API_KEY from the environment (loaded from ~/.hermes/.env by the
serve process). It is a thin read/write proxy: queue timeline, draft CRUD,
schedule/reschedule, publish.

Routes:
    GET    /health
    GET    /social-set            Resolve the default social set id + account info
    GET    /queue?start_date=&end_date=   Queue timeline for a date range
    GET    /drafts?status=&limit=&offset=&order_by=   Draft list (status: draft|scheduled|published|...; published defaults to -published_at)
    GET    /draft/<id>                    Single draft detail
    POST   /draft                         Create a new draft (body: text, platforms|platform, content_markdown, publish_at, tags, title, scratchpad)
    PATCH  /draft/<id>                    Update draft (body: publish_at, content, ...)
    DELETE /draft/<id>                    Delete a draft
    GET    /queue/schedule                Queue schedule rules
    GET    /analytics/x/posts?start_date=&end_date=&include_replies=&limit=&offset=   X post analytics (read-only)
    GET    /analytics/x/followers?start_date=&end_date=   X follower series (read-only)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()

API_BASE = os.environ.get("TYPEFULLY_API_BASE", "https://api.typefully.com/v2")
DEFAULT_SOCIAL_SET_ID = os.environ.get("TYPEFULLY_SOCIAL_SET_ID")

PLATFORM_KEYS = ("x", "linkedin", "threads", "bluesky", "mastodon", "substack")


def _headers() -> dict[str, str]:
    key = os.environ.get("TYPEFULLY_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="TYPEFULLY_API_KEY is not set in the serve process environment. "
            "Add it to ~/.hermes/.env and restart the desktop (kill the hermes serve child).",
        )
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _request(method: str, path: str, body: dict | None = None) -> Any:
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:500]
        raise HTTPException(
            status_code=e.code,
            detail=f"Typefully API {e.code}: {detail}",
        ) from e
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"Typefully unreachable: {e}") from e


def _social_set_id() -> int:
    """Resolve the default social set id (env override, else first from API)."""
    if DEFAULT_SOCIAL_SET_ID:
        return int(DEFAULT_SOCIAL_SET_ID)
    data = _request("GET", "/social-sets?limit=50")
    results = data.get("results") or data if isinstance(data, list) else data.get("results") or []
    if not results:
        raise HTTPException(status_code=404, detail="No social sets found for this account")
    return int(results[0]["id"])


def split_thread_text(text: str) -> list[str]:
    """Split thread text into posts on separator lines of exactly ``---``.

    Mirrors splitThreadText() in the typefully CLI script: a separator is a
    line containing only ``---`` with optional surrounding whitespace, using
    LF or CRLF line endings. Longer runs (``----``) are NOT separators.
    Empty/whitespace-only segments are dropped.
    """
    parts = re.split(r"\r?\n[ \t]*---[ \t]*\r?\n", text)
    return [p for p in parts if p.strip()]


def build_draft_body(payload: dict, platforms: list[str] | None = None) -> dict:
    """Build the v2 POST /social-sets/{sid}/drafts body from a composer payload.

    Supports both the new composer contract and the legacy
    ``{text, platform, publish_at}`` shape:

    - text (required): post text; lines that are exactly ``---`` split a thread.
    - platforms (optional): list of platform keys. Absent/empty keeps the old
      auto-detect-first-connected behavior (caller passes the resolved list).
    - content_markdown (required for x_article): standalone X Article body.
    - cover_media_id (optional, x_article only): ready media uuid for the cover.
    - publish_at (optional): ISO datetime | "now" | "next-free-slot".
    - tags (optional): list of strings -> body["tags"].
    - title (optional): internal draft title -> body["draft_title"].
    - scratchpad (optional): internal notes -> body["scratchpad_text"].

    Raises ValueError for missing text / missing article markdown, mixed
    platforms with x_article, and substack+thread conflicts so the route can
    map them to HTTP 400 without HTTP machinery here.
    """
    if platforms is None:
        # New shape: platforms list. Legacy shape: single platform string.
        raw = payload.get("platforms")
        if not raw and payload.get("platform"):
            raw = [payload["platform"]]
        platforms = raw or []
    platforms = [p for p in platforms if p]

    if "x_article" in platforms:
        return build_article_body(payload, platforms)

    text = payload.get("text")
    if not text or not isinstance(text, str):
        raise ValueError("text is required")

    posts = split_thread_text(text)
    if not posts:
        raise ValueError("text is required")

    if len(posts) > 1 and "substack" in platforms:
        raise ValueError(
            "substack (Substack Notes) supports a single post per draft — "
            "threads are not supported"
        )

    platforms_obj: dict[str, Any] = {}
    for key in platforms:
        platforms_obj[key] = {
            "enabled": True,
            "posts": [{"text": p} for p in posts],
        }

    body: dict[str, Any] = {"platforms": platforms_obj}

    publish_at = payload.get("publish_at")
    if publish_at:
        body["publish_at"] = publish_at

    tags = payload.get("tags")
    if tags:
        body["tags"] = tags

    title = payload.get("title")
    if title:
        body["draft_title"] = title

    scratchpad = payload.get("scratchpad")
    if scratchpad:
        body["scratchpad_text"] = scratchpad

    return body


def build_article_body(payload: dict, platforms: list[str] | None = None) -> dict:
    """Build the POST body for a standalone X Article draft.

    ``x_article`` is standalone: it must be the only platform and the payload
    must carry ``content_markdown`` (required; first block ``# Title``). A
    ready ``cover_media_id`` is optional. Post-only fields (``text``,
    ``posts``, ``media_ids``) are rejected because the Typefully v2 contract
    does not accept them for articles.
    """
    if platforms is None:
        raw = payload.get("platforms") or []
        platforms = [p for p in raw if p]
    if len(platforms) != 1 or platforms[0] != "x_article":
        raise ValueError("x_article is standalone — it cannot be combined with other platforms")

    markdown = payload.get("content_markdown")
    if not markdown or not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("content_markdown is required for x_article")

    if payload.get("text") or payload.get("posts"):
        raise ValueError("x_article takes content_markdown, not text/posts")

    article: dict[str, Any] = {"content_markdown": markdown}
    if payload.get("cover_media_id") is not None:
        article["cover_media_id"] = payload["cover_media_id"]

    body: dict[str, Any] = {"platforms": {"x_article": article}}

    publish_at = payload.get("publish_at")
    if publish_at:
        body["publish_at"] = publish_at

    tags = payload.get("tags")
    if tags:
        body["tags"] = tags

    title = payload.get("title")
    if title:
        body["draft_title"] = title

    scratchpad = payload.get("scratchpad")
    if scratchpad:
        body["scratchpad_text"] = scratchpad

    return body


def build_article_update_body(payload: dict) -> dict:
    """Build the PATCH body for an existing X Article draft.

    Accepts ``content_markdown`` (optional) and ``cover_media_id`` (omit to
    keep, ``None`` to remove). Preserves comment markers by default; callers
    can pass ``force_overwrite_comments: true`` as the documented escape
    hatch. Raises ValueError when there is nothing to update.
    """
    article: dict[str, Any] = {}
    if "content_markdown" in payload and payload.get("content_markdown") is not None:
        article["content_markdown"] = payload["content_markdown"]
    if "cover_media_id" in payload:
        article["cover_media_id"] = payload["cover_media_id"]

    if not article:
        raise ValueError("nothing to update for x_article (content_markdown or cover_media_id)")

    body: dict[str, Any] = {"platforms": {"x_article": article}}
    if payload.get("force_overwrite_comments"):
        body["force_overwrite_comments"] = True
    return body


@router.get("/health")
def health() -> dict:
    return {
        "ok": True,
        "key_set": bool(os.environ.get("TYPEFULLY_API_KEY", "")),
        "api_base": API_BASE,
        "social_set": DEFAULT_SOCIAL_SET_ID or "auto",
    }


@router.get("/social-set")
def social_set() -> dict:
    sid = _social_set_id()
    return _request("GET", f"/social-sets/{sid}")


@router.get("/queue")
def queue(start_date: str, end_date: str) -> dict:
    sid = _social_set_id()
    params = urllib.parse.urlencode({"start_date": start_date, "end_date": end_date})
    return _request("GET", f"/social-sets/{sid}/queue?{params}")


@router.get("/drafts")
def drafts(status: str | None = None, limit: int = 50, offset: int = 0,
           order_by: str | None = None) -> dict:
    sid = _social_set_id()
    # Typefully v2 rejects limit > 50 with HTTP 422; clamp hard at 50.
    params = {"limit": min(max(limit, 1), 50)}
    if status:
        params["status"] = status
    if offset:
        params["offset"] = offset
    # Published history is reverse-chronological by default.
    if order_by is None:
        order_by = "-published_at" if status == "published" else None
    if order_by:
        params["order_by"] = order_by
    qs = urllib.parse.urlencode(params)
    return _request("GET", f"/social-sets/{sid}/drafts?{qs}")


@router.get("/draft/{draft_id}")
def draft(draft_id: int) -> dict:
    sid = _social_set_id()
    return _request("GET", f"/social-sets/{sid}/drafts/{draft_id}")


@router.patch("/draft/{draft_id}")
def draft_update(draft_id: int, payload: dict) -> dict:
    sid = _social_set_id()
    # Article PATCH: {platforms: ["x_article"], content_markdown, cover_media_id}
    # or the direct article object shape. Build the canonical body so the
    # comment-marker contract and cover removal semantics are preserved.
    if payload.get("platforms") == ["x_article"] or "content_markdown" in payload:
        try:
            body = build_article_update_body(payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _request("PATCH", f"/social-sets/{sid}/drafts/{draft_id}", body)
    return _request("PATCH", f"/social-sets/{sid}/drafts/{draft_id}", payload)


@router.post("/draft")
def draft_create(payload: dict) -> dict:
    """Create a new draft and optionally schedule it.

    New composer body:
        text         (required) — post text; lines that are exactly "---"
                                  split into a thread
        platforms    (optional) — array of platform keys
                                  (x, linkedin, threads, bluesky, mastodon,
                                  substack); absent/empty auto-detects the
                                  first connected platform
        publish_at   (optional) — ISO datetime, "now", or "next-free-slot"
        tags         (optional) — array of strings
        title        (optional) — internal draft title
        scratchpad   (optional) — internal notes

    Legacy body {text, platform, publish_at} still works.
    """
    sid = _social_set_id()

    # Auto-detect the first connected platform when no platforms given.
    if not payload.get("platforms") and not payload.get("platform"):
        ss = _request("GET", f"/social-sets/{sid}")
        plats = ss.get("platforms") or {}
        for p in PLATFORM_KEYS:
            if plats.get(p):
                payload = {**payload, "platforms": [p]}
                break
        else:
            raise HTTPException(status_code=400, detail="No connected platform found for this account")

    try:
        body = build_draft_body(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return _request("POST", f"/social-sets/{sid}/drafts", body)


@router.delete("/draft/{draft_id}")
def draft_delete(draft_id: int) -> dict:
    sid = _social_set_id()
    return _request("DELETE", f"/social-sets/{sid}/drafts/{draft_id}")


@router.get("/queue/schedule")
def queue_schedule() -> dict:
    sid = _social_set_id()
    return _request("GET", f"/social-sets/{sid}/queue/schedule")


# ---------------------------------------------------------------------------
# X analytics (read-only) — Typefully v2 only supports platform=x, max 366 days
# ---------------------------------------------------------------------------

ANALYTICS_MAX_DAYS = 366
ANALYTICS_DAY_OPTIONS = frozenset({7, 30, 90})
ANALYTICS_POSTS_LIMIT_MAX = 100


def analytics_date_range(days: int, end: date | None = None) -> tuple[str, str]:
    """Return (start_date, end_date) ISO strings for a 7/30/90-day inclusive window."""
    if days not in ANALYTICS_DAY_OPTIONS:
        raise ValueError(f"days must be one of {sorted(ANALYTICS_DAY_OPTIONS)}")
    end_d = end or date.today()
    start_d = end_d - timedelta(days=days - 1)
    return start_d.isoformat(), end_d.isoformat()


def validate_analytics_range(start_date: str, end_date: str, max_days: int = ANALYTICS_MAX_DAYS) -> tuple[str, str]:
    """Validate YYYY-MM-DD range; max inclusive span is ``max_days`` (366)."""
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as e:
        raise ValueError("start_date and end_date must be YYYY-MM-DD") from e
    if start > end:
        raise ValueError("start_date must be on or before end_date")
    # Inclusive day count: Aug 1–Aug 1 = 1 day.
    span = (end - start).days + 1
    if span > max_days:
        raise ValueError(f"analytics range cannot exceed {max_days} days")
    return start.isoformat(), end.isoformat()


def format_metric(value: Any) -> str:
    """Display helper: null → 'unavailable'; numbers stay numbers (never coerce null→0)."""
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def _metric_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == int(value):
        return int(value)
    return None


def summarize_x_posts(posts: list[dict] | None) -> dict[str, Any]:
    """Aggregate impressions/engagements and rank top posts by impressions.

    Null metrics are preserved as None (UI shows 'unavailable') and excluded
    from numeric totals so missing data is never treated as zero.
    """
    rows = posts or []
    total_impr: int | None = None
    total_eng: int | None = None
    top: list[dict[str, Any]] = []

    for p in rows:
        metrics = p.get("metrics") if isinstance(p, dict) else None
        impressions = None
        engagements = None
        if isinstance(metrics, dict):
            impressions = _metric_int(metrics.get("impressions"))
            eng = metrics.get("engagement")
            if isinstance(eng, dict):
                engagements = _metric_int(eng.get("total"))
            else:
                engagements = _metric_int(eng)

        if impressions is not None:
            total_impr = (total_impr or 0) + impressions
        if engagements is not None:
            total_eng = (total_eng or 0) + engagements

        top.append({
            "post_id": p.get("post_id") if isinstance(p, dict) else None,
            "draft_id": p.get("draft_id") if isinstance(p, dict) else None,
            "preview": (p.get("preview_text") or p.get("preview") or "") if isinstance(p, dict) else "",
            "url": p.get("url") if isinstance(p, dict) else None,
            "created_at": p.get("created_at") if isinstance(p, dict) else None,
            "impressions": impressions,
            "engagements": engagements,
        })

    # Null impressions sort last; otherwise descending by impressions.
    top.sort(key=lambda r: (1, 0) if r["impressions"] is None else (0, -r["impressions"]))

    rate = None
    if total_impr is not None and total_impr > 0 and total_eng is not None:
        rate = total_eng / total_impr

    return {
        "total_impressions": total_impr,
        "total_engagements": total_eng,
        "engagement_rate": rate,
        "post_count": len(rows),
        "top_posts": top,
    }


def follower_delta(payload: dict | None) -> dict[str, Any]:
    """Current follower count + delta vs first daily datapoint in the series."""
    payload = payload or {}
    current = _metric_int(payload.get("current_followers_count"))
    data = payload.get("data") or []
    start = None
    if isinstance(data, list) and data:
        first = data[0] if isinstance(data[0], dict) else None
        if first is not None:
            start = _metric_int(first.get("followers_count"))
        if current is None:
            # Fall back to latest non-null in series when current is omitted.
            for point in reversed(data):
                if isinstance(point, dict):
                    current = _metric_int(point.get("followers_count"))
                    if current is not None:
                        break
    delta = None
    if current is not None and start is not None:
        delta = current - start
    return {"current": current, "start": start, "delta": delta}


@router.get("/analytics/x/posts")
def analytics_x_posts(
    start_date: str,
    end_date: str,
    include_replies: bool = False,
    limit: int = ANALYTICS_POSTS_LIMIT_MAX,
    offset: int = 0,
) -> dict:
    """Proxy GET /v2/social-sets/{id}/analytics/x/posts (read-only)."""
    try:
        start_date, end_date = validate_analytics_range(start_date, end_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    sid = _social_set_id()
    params = {
        "start_date": start_date,
        "end_date": end_date,
        "include_replies": "true" if include_replies else "false",
        "limit": min(max(int(limit or 1), 1), ANALYTICS_POSTS_LIMIT_MAX),
    }
    if offset:
        params["offset"] = max(int(offset), 0)
    qs = urllib.parse.urlencode(params)
    return _request("GET", f"/social-sets/{sid}/analytics/x/posts?{qs}")


@router.get("/analytics/x/followers")
def analytics_x_followers(start_date: str | None = None, end_date: str | None = None) -> dict:
    """Proxy GET /v2/social-sets/{id}/analytics/x/followers (read-only)."""
    sid = _social_set_id()
    params: dict[str, str] = {}
    if start_date or end_date:
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="start_date and end_date are both required when either is set")
        try:
            start_date, end_date = validate_analytics_range(start_date, end_date)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        params["start_date"] = start_date
        params["end_date"] = end_date
    qs = urllib.parse.urlencode(params)
    path = f"/social-sets/{sid}/analytics/x/followers"
    if qs:
        path = f"{path}?{qs}"
    return _request("GET", path)
