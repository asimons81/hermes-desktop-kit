/**
 * Typefully Queue — Hermes desktop plugin.
 *
 * The Typefully Q (queue) calendar as a full page in the desktop: a TIME-GRID
 * week view (Google-Calendar style) — 7 day columns, hours 7:00–21:00, each
 * draft anchored at its scheduled time, free queue slots as subtle empty
 * cells. Today is highlighted with a now-line. Click a draft for the detail
 * drawer: reschedule into a free slot, publish now, or delete.
 *
 * Backend: ~/.hermes/plugins/typefully-q/dashboard/plugin_api.py
 * (mounted at /api/plugins/typefully-q/ — enabled via plugins.enabled in
 * config.yaml). Plain ESM loaded uncompiled: UI is jsx() calls, NOT JSX
 * syntax; only @hermes/plugin-sdk, react, react/jsx-runtime resolve.
 */

import {
  Badge,
  Button,
  cn,
  EmptyState,
  haptic,
  host,
  Input,
  Loader,
  PALETTE_AREA,
  ROUTES_AREA,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  Textarea,
  Tip,
  usePluginI18n,
  useQuery,
  useMutation,
  queryClient
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useRef, useState } from 'react'

const ID = 'typefully-q'
const WEEK_MS = 7 * 24 * 60 * 60 * 1000
const GRID_START = 7 // 7:00 AM — earliest queue slot (CDT)
const GRID_END = 21 // 9:00 PM — latest queue slot
const ROW_H = 52 // px per hour
const GUTTER_W = '3.25rem'
const LIMIT = 50 // published-history page size (Typefully v2 max)

// Assigned in register(ctx) — components can't see ctx directly.
let rest

// Scoped CSS for the calendar grid (the compiled bundle cannot know plugin-only
// classes). Theme vars only — no hardcoded colors. NOTE: hot-reloads inject
// NEW css text, so compare content — a static id check alone leaves stale
// styles from the previous version in the DOM (the grid silently collapses).
function ensurePluginStyles() {
  if (typeof document === 'undefined') return
  const css = [
    '.tq-scroll { overflow: auto; }',
    '.tq-cal { display: grid; grid-template-columns: ' + GUTTER_W + ' repeat(7, minmax(9.5rem, 1fr)); }',
    '.tq-corner { border-bottom: 1px solid var(--ui-stroke-tertiary); border-right: 1px solid var(--ui-stroke-tertiary); }',
    '.tq-dayhead { position: sticky; top: 0; z-index: 3; background: var(--ui-bg-editor); border-bottom: 1px solid var(--ui-stroke-tertiary); border-right: 1px solid var(--ui-stroke-tertiary); padding: 0.375rem 0.5rem; }',
    '.tq-today .tq-dayhead { background: var(--ui-bg-tertiary); box-shadow: inset 0 2px 0 var(--ui-accent); }',
    '.tq-today .tq-daylabel { color: var(--ui-accent); }',
    '.tq-today.tq-daycol { background: var(--ui-bg-tertiary); }',
    '.tq-gutter { position: relative; border-right: 1px solid var(--ui-stroke-tertiary); }',
    '.tq-gutter-label { position: absolute; right: 0.375rem; font-size: 0.625rem; line-height: ' + ROW_H + 'px; color: var(--ui-text-quaternary); }',
    '.tq-daycol { position: relative; overflow: hidden; border-right: 1px solid var(--ui-stroke-tertiary); }',
    '.tq-daycol:last-child { border-right: 0; }',
    '.tq-hr { position: absolute; left: 0; right: 0; border-top: 1px solid var(--ui-stroke-secondary); }',
    '.tq-hr.tq-hr-major { border-top-color: var(--ui-stroke-tertiary); }',
    '.tq-hr.tq-hr-start { display: none; }',
    '.tq-slot { position: absolute; left: 0.375rem; top: 0; display: flex; align-items: center; justify-content: flex-end; padding: 0 0.5rem 0 0; color: var(--ui-text-quaternary); font-size: 0.5625rem; opacity: 0.45; }',
    '.tq-draft { position: absolute; left: 0.375rem; right: 0.375rem; border: 1px solid var(--ui-stroke-tertiary); border-left: 3px solid var(--ui-accent); border-radius: 7px; background: var(--ui-bg-elevated); box-shadow: 0 1px 3px rgba(0,0,0,0.35); padding: 0.25rem 0.5rem; overflow: hidden; cursor: grab; user-select: none; }',
    '.tq-draft:active { cursor: grabbing; }',
    '.tq-draft.tq-dragging { opacity: 0.5; z-index: 10; }',
    '.tq-draft.tq-drag-over { border-color: var(--ui-accent); border-left-width: 3px; box-shadow: 0 0 0 2px var(--ui-accent); }',
    '.tq-draft:hover { background: var(--ui-bg-quaternary); }',
    '.tq-draft[data-selected="true"] { border-color: var(--ui-accent); border-left-width: 3px; background: var(--ui-bg-tertiary); }',
    '.tq-draft-title { font-size: 0.6875rem; font-weight: 500; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
    '.tq-draft-preview { font-size: 0.625rem; line-height: 1.2; color: var(--ui-text-tertiary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }',
    '.tq-draft-time { flex-shrink: 0; font-size: 0.5625rem; color: var(--ui-text-quaternary); white-space: nowrap; }',
    '.tq-dot { display: inline-block; width: 8px; height: 8px; border-radius: 9999px; box-shadow: inset 0 0 0 1px rgba(255,255,255,0.18); }',
    '.tq-tag { font-size: 0.5625rem; line-height: 1rem; border: 1px solid var(--ui-stroke-tertiary); border-radius: 3px; padding: 0 0.2rem; color: var(--ui-text-tertiary); }',
    '.tq-now { position: absolute; left: 0; right: 0; border-top: 1px solid var(--ui-accent); z-index: 2; }',
    '.tq-now-dot { position: absolute; top: -3px; left: 0; width: 7px; height: 7px; border-radius: 9999px; background: var(--ui-accent); }',
    '.tq-detail { width: 20rem; min-width: 20rem; border-left: 1px solid var(--ui-stroke-tertiary); }',
    '.tq-full { white-space: pre-wrap; overflow-wrap: anywhere; }',
    '.tq-slotpick { border: 1px dashed var(--ui-stroke-secondary); border-radius: 6px; padding: 0.375rem 0.5rem; }',
    '.tq-slotpick:hover { background: var(--ui-bg-tertiary); color: var(--ui-text-secondary); }',
    '.tq-modal-backdrop { position: fixed; inset: 0; z-index: 100; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; }',
    '.tq-modal { background: var(--ui-bg-elevated); border: 1px solid var(--ui-stroke-tertiary); border-radius: 12px; width: 30rem; max-width: 92vw; max-height: 88vh; overflow-y: auto; padding: 1.25rem; box-shadow: 0 4px 24px rgba(0,0,0,0.5); }',
    '.tq-modal-header { display: flex; align-items: center; justify-content: space-between; gap: 0.5rem; }',
    '.tq-label { font-size: 0.6875rem; line-height: 1rem; color: var(--ui-text-tertiary); }',
    '.tq-chips { display: flex; flex-wrap: wrap; gap: 0.375rem; }',
    '.tq-chip { display: inline-flex; align-items: center; gap: 0.375rem; padding: 0.1875rem 0.625rem; border: 1px solid var(--ui-stroke-tertiary); border-radius: 9999px; background: transparent; color: var(--ui-text-secondary); font-size: 0.6875rem; line-height: 1.25rem; cursor: pointer; user-select: none; transition: background-color .12s ease, border-color .12s ease, color .12s ease; }',
    '.tq-chip:hover { background: var(--ui-bg-tertiary); color: var(--ui-text-primary); }',
    '.tq-chip[data-active="true"] { background: var(--ui-bg-tertiary); border-color: var(--ui-accent); color: var(--ui-text-primary); }',
    '.tq-compose { min-height: 7rem; }',
    '.tq-datetime { max-width: 15rem; }',
    '.tq-counter { font-size: 0.6875rem; line-height: 1rem; font-variant-numeric: tabular-nums; }',
    '.tq-count { color: var(--ui-text-tertiary); }',
    '.tq-count-warn { color: var(--ui-orange); }',
    '.tq-count-over { color: var(--ui-red); font-weight: 600; }',
    '.tq-perpost-row { display: inline-flex; align-items: center; gap: 0.5rem; }',
    '.tq-perpost { color: var(--ui-text-tertiary); }',
    '.tq-more-toggle { display: inline-flex; align-items: center; gap: 0.25rem; padding: 0.125rem 0; background: none; border: 0; color: var(--ui-text-secondary); font-size: 0.6875rem; line-height: 1.25rem; cursor: pointer; }',
    '.tq-more-toggle:hover { color: var(--ui-text-primary); }',
    '.tq-chev { display: inline-block; width: 0.75rem; text-align: center; color: var(--ui-text-quaternary); }',
    '.tq-hint { font-size: 0.625rem; line-height: 1rem; color: var(--ui-text-quaternary); }'
  ].join('\n')
  let style = document.getElementById('tq-styles')
  if (!style) {
    style = document.createElement('style')
    style.id = 'tq-styles'
    document.head.appendChild(style)
  }
  if (style.textContent !== css) {
    style.textContent = css
  }
}

function openExternal(url) {
  // Defense-in-depth: only http/https URLs may reach the bridge or
  // window.open. API-sourced URLs are https today; a hostile response must
  // never be handed to the shell bridge as javascript:/file:/etc.
  if (typeof url !== 'string' || !/^https?:\/\//i.test(url)) {
    console.warn('[typefully-q] blocked non-http(s) external URL:', String(url))
    return
  }
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
  if (bridge && typeof bridge.openExternal === 'function') {
    void bridge.openExternal(url)
  } else {
    window.open(url, '_blank', 'noopener')
  }
}

function fmtTime(iso) {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(new Date(iso))
  } catch {
    return ''
  }
}

/** Parse a date-only string ('YYYY-MM-DD') as LOCAL time — new Date(str)
 *  parses as UTC, which shifts the day back in negative-offset zones. */
function parseLocalDate(str) {
  const [y, m, d] = str.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function fmtDay(iso) {
  try {
    const d = typeof iso === 'string' && iso.length === 10 ? parseLocalDate(iso) : new Date(iso)
    return new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(d)
  } catch {
    return ''
  }
}

function fmtDate(iso) {
  try {
    const d = typeof iso === 'string' && iso.length === 10 ? parseLocalDate(iso) : new Date(iso)
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(d)
  } catch {
    return ''
  }
}

function fmtDayShort(iso) {
  try {
    const d = typeof iso === 'string' && iso.length === 10 ? parseLocalDate(iso) : new Date(iso)
    return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(d)
  } catch {
    return ''
  }
}

function isoDay(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

// ── X analytics pure helpers (module-top for headless verification) ─────────

const ANALYTICS_DAY_OPTIONS = [7, 30, 90]

/** Inclusive YYYY-MM-DD window ending on `end` (default today, local). */
function analyticsDateRange(days, end) {
  const endDate = end instanceof Date ? end : new Date()
  const endLocal = new Date(endDate.getFullYear(), endDate.getMonth(), endDate.getDate())
  const startLocal = new Date(endLocal)
  startLocal.setDate(startLocal.getDate() - (Number(days) - 1))
  return { start: isoDay(startLocal), end: isoDay(endLocal) }
}

/** Null → "unavailable"; never coerce missing metrics to 0. */
function formatMetric(value, opts) {
  if (value == null) return 'unavailable'
  if (opts && opts.style === 'rate') {
    const n = Number(value)
    if (!Number.isFinite(n)) return 'unavailable'
    return `${(n * 100).toFixed(2)}%`
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value.toLocaleString('en-US')
  }
  return String(value)
}

function _metricInt(value) {
  if (value == null || typeof value === 'boolean') return null
  if (typeof value === 'number' && Number.isFinite(value)) return Math.trunc(value)
  return null
}

/** Aggregate post metrics; nulls stay null and are excluded from totals. */
function summarizeXPosts(posts) {
  const rows = Array.isArray(posts) ? posts : []
  let totalImpr = null
  let totalEng = null
  const top = []
  for (const p of rows) {
    const metrics = p && p.metrics
    let impressions = null
    let engagements = null
    if (metrics && typeof metrics === 'object') {
      impressions = _metricInt(metrics.impressions)
      const eng = metrics.engagement
      engagements = eng && typeof eng === 'object' ? _metricInt(eng.total) : _metricInt(eng)
    }
    if (impressions != null) totalImpr = (totalImpr || 0) + impressions
    if (engagements != null) totalEng = (totalEng || 0) + engagements
    top.push({
      post_id: p && p.post_id,
      draft_id: p && p.draft_id,
      preview: (p && (p.preview_text || p.preview)) || '',
      url: p && p.url,
      created_at: p && p.created_at,
      impressions,
      engagements
    })
  }
  top.sort((a, b) => {
    if (a.impressions == null && b.impressions == null) return 0
    if (a.impressions == null) return 1
    if (b.impressions == null) return -1
    return b.impressions - a.impressions
  })
  let rate = null
  if (totalImpr != null && totalImpr > 0 && totalEng != null) rate = totalEng / totalImpr
  return {
    total_impressions: totalImpr,
    total_engagements: totalEng,
    engagement_rate: rate,
    post_count: rows.length,
    top_posts: top
  }
}

function followerDelta(payload) {
  const data = (payload && payload.data) || []
  let current = _metricInt(payload && payload.current_followers_count)
  let start = null
  if (Array.isArray(data) && data.length) {
    start = _metricInt(data[0] && data[0].followers_count)
    if (current == null) {
      for (let i = data.length - 1; i >= 0; i--) {
        current = _metricInt(data[i] && data[i].followers_count)
        if (current != null) break
      }
    }
  }
  let delta = null
  if (current != null && start != null) delta = current - start
  return { current, start, delta }
}

/** Typefully analytics posts page size max is 100. */
const ANALYTICS_POSTS_PAGE_LIMIT = 100
/** Defensive cap so a runaway next never unbounded-loops (100 × 20 = 2000 posts). */
const ANALYTICS_POSTS_MAX_PAGES = 20

/**
 * Fetch all analytics/x/posts pages for a date range via offset increments.
 * Does NOT follow arbitrary external `next` URLs — only uses `next` as a
 * boolean "more pages?" signal. Stops when a page returns fewer than `limit`
 * results, when `next` is null/empty, or when `maxPages` is hit (truncated).
 *
 * @param {(path: string) => Promise<any>} fetchPage rest-like getter
 * @param {{ startDate: string, endDate: string, includeReplies?: boolean, limit?: number, maxPages?: number }} opts
 * @returns {Promise<{ results: any[], truncated: boolean, pages: number, limit: number }>}
 */
async function fetchAllAnalyticsXPosts(fetchPage, opts) {
  const startDate = opts && opts.startDate
  const endDate = opts && opts.endDate
  const includeReplies = !!(opts && opts.includeReplies)
  const limit = Math.min(
    ANALYTICS_POSTS_PAGE_LIMIT,
    Math.max(1, Number((opts && opts.limit) || ANALYTICS_POSTS_PAGE_LIMIT) || ANALYTICS_POSTS_PAGE_LIMIT)
  )
  const maxPages = Math.max(
    1,
    Math.min(50, Number((opts && opts.maxPages) || ANALYTICS_POSTS_MAX_PAGES) || ANALYTICS_POSTS_MAX_PAGES)
  )
  const results = []
  let truncated = false
  let pages = 0

  for (let page = 0; page < maxPages; page++) {
    const offset = page * limit
    const path =
      `/analytics/x/posts?start_date=${encodeURIComponent(startDate)}` +
      `&end_date=${encodeURIComponent(endDate)}` +
      `&include_replies=${includeReplies ? 'true' : 'false'}` +
      `&limit=${limit}&offset=${offset}`
    const resp = await fetchPage(path)
    pages = page + 1
    const batch = resp && Array.isArray(resp.results) ? resp.results : []
    for (let i = 0; i < batch.length; i++) results.push(batch[i])

    const fullPage = batch.length >= limit
    const nextVal = resp && resp.next
    const hasNext = nextVal != null && nextVal !== ''
    if (!fullPage || !hasNext) {
      truncated = false
      break
    }
    // Full page + next signal: more data exists.
    if (page === maxPages - 1) {
      truncated = true
      break
    }
  }

  return { results, truncated, pages, limit }
}

function formatDelta(delta) {
  if (delta == null) return 'unavailable'
  if (delta > 0) return `+${delta.toLocaleString('en-US')}`
  return delta.toLocaleString('en-US')
}

function hourOf(iso) {
  const d = new Date(iso)
  return d.getHours() + d.getMinutes() / 60
}

function isToday(iso) {
  return iso === isoDay(new Date())
}

function nowHour() {
  const d = new Date()
  return d.getHours() + d.getMinutes() / 60
}

const PLATFORMS = [
  { key: 'x_post_enabled', api: 'x', label: 'X', color: '#e7e9ea', limit: 280 },
  { key: 'linkedin_post_enabled', api: 'linkedin', label: 'Li', color: '#0a66c2', limit: 3000 },
  { key: 'threads_post_enabled', api: 'threads', label: 'Th', color: '#a855f7', limit: 500 },
  { key: 'bluesky_post_enabled', api: 'bluesky', label: 'Bs', color: '#38bdf8', limit: 300 },
  { key: 'mastodon_post_enabled', api: 'mastodon', label: 'Ma', color: '#6364ff', limit: 500 },
  { key: 'substack_post_enabled', api: 'substack', label: 'Sb', color: '#ff6719', limit: 10000 }
]

// ── Pure composer math (module-top so it can be sanity-checked headlessly) ──

/** Character limits for the given platform API keys. */
function limitsFor(platformKeys) {
  return (platformKeys || []).map(k => {
    const p = PLATFORMS.find(x => x.api === k)
    return p ? p.limit : null
  }).filter(v => v != null)
}

/** Split draft text into posts on lines that are exactly "---" (mirrors the
 *  backend splitThreadText: LF or CRLF, surrounding spaces allowed, longer
 *  runs like "----" do not split; empty segments dropped). */
function postsFromText(text) {
  if (!text) return []
  return text.split(/\r?\n[ \t]*---[ \t]*\r?\n/).filter(t => t.trim())
}

/** The binding limit across selected platforms — the smallest per-platform
 *  limit, because every selected platform must fit the same post. Returns
 *  Infinity when no platforms are selected (no constraint). */
function bindingLimit(posts, platformKeys) {
  const ls = limitsFor(platformKeys)
  if (!ls.length) return Infinity
  return Math.min(...ls)
}

/** Length of a post, counting code points (emoji-safe-ish). */
function postLength(post) {
  return Array.from(post).length
}

function platformTags(draft) {
  if (!draft) return null
  const tags = PLATFORMS.filter(p => draft[p.key]).map(p => jsxs('span', {
    className: 'tq-tag flex items-center gap-1',
    children: [
      jsx('span', { className: 'tq-dot', style: { background: p.color } }),
      jsx('span', { children: p.label })
    ]
  }, `tag-${p.key}`))
  return tags.length ? jsxs('span', { className: 'flex shrink-0 items-center gap-0.5', children: tags }) : null
}

function freeSlotsIn(queueData) {
  if (!queueData || !Array.isArray(queueData.days)) return []
  const out = []
  for (const day of queueData.days) {
    for (const item of day.items || []) {
      if (item.kind === 'queue_slot') out.push({ at: item.at, date: day.date })
    }
  }
  return out
}

function hourLabels() {
  const labels = []
  for (let h = GRID_START; h <= GRID_END; h++) {
    labels.push(jsx('div', {
      className: 'tq-gutter-label',
      style: { top: `${(h - GRID_START) * ROW_H}px` },
      children: h === 12 ? '12 PM' : h < 12 ? `${h} AM` : `${h - 12} PM`
    }, `hl-${h}`))
  }
  return labels
}

// ── Detail drawer ───────────────────────────────────────────────────────────

function DraftDetail({ draft, slots, onClose, onPublish, onDelete, onMove }) {
  const [armed, setArmed] = useState(null) // 'publish' | 'delete' | null

  const publishBtn = jsx(Button, {
    size: 'sm',
    variant: armed === 'publish' ? 'destructive' : 'outline',
    onClick: () => {
      if (armed !== 'publish') { setArmed('publish'); return }
      setArmed(null)
      onPublish(draft)
    },
    children: armed === 'publish' ? 'Confirm publish?' : 'Publish now'
  })

  const deleteBtn = jsx(Button, {
    size: 'sm',
    variant: armed === 'delete' ? 'destructive' : 'outline',
    onClick: () => {
      if (armed !== 'delete') { setArmed('delete'); return }
      setArmed(null)
      onDelete(draft)
    },
    children: armed === 'delete' ? 'Confirm delete?' : 'Delete'
  })

  return jsxs('div', {
    className: 'tq-detail flex h-full flex-col gap-2 p-3',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2',
        children: [
          jsx('span', { className: 'text-sm font-medium', children: 'Draft' }),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: '✕' })
        ]
      }),
      jsx('div', { className: 'tq-full text-xs text-(--ui-text-secondary)', children: draft.preview || '(no preview)' }),
      platformTags(draft),
      jsxs('div', { className: 'flex flex-wrap items-center gap-1', children: [
        jsx(Button, { size: 'sm', variant: 'outline', onClick: () => openExternal(draft.private_url || draft.share_url), children: 'Open' }),
        publishBtn,
        deleteBtn
      ]}),
      jsx('div', { className: 'mt-1 border-t border-(--ui-stroke-tertiary) pt-2 text-[0.6875rem] text-(--ui-text-tertiary)', children: 'Move to free slot' }),
      jsxs('div', { className: 'flex flex-col gap-1', children: slots.slice(0, 8).map(s => jsx('button', {
        type: 'button',
        className: 'tq-slotpick flex items-center justify-between text-left text-[0.6875rem]',
        onClick: () => onMove(s.at, draft),
        children: jsxs('span', { className: 'flex items-center gap-1', children: [
          jsx('span', { children: fmtDayShort(s.at) }),
          jsx('span', { children: fmtTime(s.at) })
        ]})
      }, `slot-${s.at}-${draft.id}`)) })
    ]
  })
}

// ── Create Post modal ────────────────────────────────────────────────────────

function CreatePostModal({ onClose, onCreated }) {
  const [text, setText] = useState('')
  // Selected platform API keys (short keys: x, linkedin, ...). Initial default
  // is the first of all six; once /social-set resolves we switch to the first
  // CONNECTED platform (unless the user already toggled).
  const [platforms, setPlatforms] = useState([PLATFORMS[0].api])
  const [connectedKeys, setConnectedKeys] = useState(null) // null = unknown
  const touchedRef = useRef(false)
  const [scheduleMode, setScheduleMode] = useState('draft') // draft | next | pick
  const [pickAt, setPickAt] = useState('')
  const [showMore, setShowMore] = useState(false)
  const [title, setTitle] = useState('')
  const [tags, setTags] = useState('')
  const [scratchpad, setScratchpad] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Fetch the social set to learn which platforms are actually connected.
  useEffect(() => {
    let cancelled = false
    rest('/social-set')
      .then(data => {
        if (cancelled) return
        const plats = (data && data.platforms) || {}
        const keys = PLATFORMS.filter(p => plats[p.api]).map(p => p.api)
        setConnectedKeys(keys.length ? keys : null)
      })
      .catch(() => {
        if (!cancelled) setConnectedKeys(null)
      })
    return () => { cancelled = true }
  }, [])

  // Default to the first connected platform once known (if the user hasn't
  // touched the selection yet). Fall back to all six on failure (null).
  useEffect(() => {
    if (touchedRef.current) return
    if (connectedKeys && connectedKeys.length) {
      setPlatforms([connectedKeys[0]])
    }
  }, [connectedKeys])

  const visiblePlatforms = connectedKeys && connectedKeys.length
    ? PLATFORMS.filter(p => connectedKeys.includes(p.api))
    : PLATFORMS

  const togglePlatform = api => {
    touchedRef.current = true
    setPlatforms(prev => {
      if (prev.includes(api)) {
        // Keep at least one selected.
        return prev.length > 1 ? prev.filter(k => k !== api) : prev
      }
      return [...prev, api]
    })
  }

  const posts = postsFromText(text.trim())
  const bind = bindingLimit(posts, platforms)
  const counts = posts.map(postLength)
  const maxCount = counts.length ? Math.max(...counts) : 0
  const over = bind !== Infinity && maxCount > bind
  const warn = !over && bind !== Infinity && maxCount > bind * 0.9

  const countClass = (count, limit) => {
    if (limit === Infinity) return ''
    if (count > limit) return 'tq-count-over'
    if (count > limit * 0.9) return 'tq-count-warn'
    return ''
  }
  const maxClass = countClass(maxCount, bind)

  const canCreate = posts.length > 0 && platforms.length > 0 && !over && !submitting

  const handleCreate = async () => {
    if (!canCreate) return
    setSubmitting(true)
    try {
      const body = { text: text.trim(), platforms }
      if (scheduleMode === 'next') {
        body.publish_at = 'next-free-slot'
      } else if (scheduleMode === 'pick' && pickAt) {
        body.publish_at = new Date(pickAt).toISOString()
      }
      if (title.trim()) body.title = title.trim()
      const tagList = tags.split(',').map(s => s.trim()).filter(Boolean)
      if (tagList.length) body.tags = tagList
      if (scratchpad.trim()) body.scratchpad = scratchpad.trim()
      await rest('/draft', { method: 'POST', body })
      haptic('tap')
      host.notify({ kind: 'info', message: 'Post created' })
      onCreated()
      onClose()
    } catch (err) {
      host.notify({ kind: 'error', message: err?.message || 'Failed to create post' })
    } finally {
      setSubmitting(false)
    }
  }

  // Keyboard handlers must read state imperatively — keep the LATEST handlers
  // in refs (the listener is installed once) so Esc / Cmd+Enter never see
  // stale closures.
  const createRef = useRef(handleCreate)
  createRef.current = handleCreate
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const onKey = e => {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeRef.current()
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        createRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const chipNodes = visiblePlatforms.map(p => {
    const active = platforms.includes(p.api)
    return jsx('button', {
      type: 'button',
      'data-active': active ? 'true' : 'false',
      className: cn('tq-chip flex items-center gap-1', active && 'tq-chip-active'),
      onClick: () => togglePlatform(p.api),
      children: [
        jsx('span', { className: 'tq-dot', style: { background: p.color } }, 'dot'),
        jsx('span', { children: p.label }, 'label')
      ]
    }, `chip-${p.api}`)
  })

  const perPostNodes = posts.length > 1
    ? posts.map((p, i) => jsx('span', {
        className: cn('tq-perpost', countClass(postLength(p), bind)),
        children: `${i + 1}: ${postLength(p)}`
      }, `pp-${i}`))
    : null

  const counterLeft = posts.length > 1
    ? jsxs('span', { className: 'tq-perpost-row', children: perPostNodes })
    : null

  const counterRight = jsx('span', {
    className: cn('tq-count', maxClass),
    children: bind === Infinity ? String(maxCount) : `${maxCount} / ${bind}`
  })

  const scheduleNodes = jsxs('div', { className: 'flex flex-col gap-1', children: [
    jsx('span', { className: 'tq-label', children: 'Schedule' }, 'label'),
    jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
      jsx(SegmentedControl, {
        value: scheduleMode,
        onChange: setScheduleMode,
        options: [
          { id: 'draft', label: 'Draft' },
          { id: 'next', label: 'Next free slot' },
          { id: 'pick', label: 'Pick time' }
        ]
      }, 'seg'),
      scheduleMode === 'pick'
        ? jsx(Input, {
            type: 'datetime-local',
            value: pickAt,
            onChange: e => setPickAt(e.target.value),
            className: 'tq-datetime'
          }, 'pick-input')
        : null
    ]}, 'controls')
  ]})

  const moreNodes = showMore
    ? jsxs('div', { className: 'flex flex-col gap-2', children: [
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Internal title' }, 'lbl'),
          jsx(Input, { value: title, onChange: e => setTitle(e.target.value), placeholder: 'Optional internal title' }, 'inp')
        ]}, 'title-row'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Tags' }, 'lbl'),
          jsx(Input, { value: tags, onChange: e => setTags(e.target.value), placeholder: 'comma, separated' }, 'inp')
        ]}, 'tags-row'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Scratchpad' }, 'lbl'),
          jsx(Textarea, { value: scratchpad, onChange: e => setScratchpad(e.target.value), placeholder: 'Private notes for this draft' }, 'inp')
        ]}, 'scratch-row')
      ]})
    : null

  return jsx('div', {
    className: 'tq-modal-backdrop',
    onClick: e => { if (e.target === e.currentTarget) onClose() },
    children: jsxs('div', {
      className: 'tq-modal flex flex-col gap-3',
      onClick: e => e.stopPropagation(),
      children: [
        jsxs('div', { className: 'tq-modal-header', children: [
          jsx('span', { className: 'text-sm font-semibold', children: 'Create Post' }, 'title'),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: '✕' }, 'close')
        ]}, 'header'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Platforms' }, 'label'),
          jsx('div', { className: 'tq-chips', children: chipNodes }, 'chips')
        ]}, 'platforms'),
        jsx(Textarea, {
          className: 'tq-compose',
          placeholder: 'What do you want to post? Use --- on its own line to split a thread.',
          value: text,
          onChange: e => setText(e.target.value),
          autoFocus: true
        }, 'compose'),
        jsxs('div', { className: 'tq-counter flex items-center justify-between gap-2', children: [
          jsxs('div', { className: 'flex items-center gap-2', children: [
            posts.length > 1 ? jsx(Badge, { variant: 'muted', children: `${posts.length} posts` }, 'thread-badge') : null,
            counterLeft
          ]}, 'counter-left'),
          counterRight
        ]}, 'counter'),
        jsx('div', { className: 'flex flex-col gap-1', children: scheduleNodes }, 'schedule'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('button', {
            type: 'button',
            className: 'tq-more-toggle',
            onClick: () => setShowMore(s => !s),
            children: [
              jsx('span', { className: 'tq-chev', children: showMore ? '▾' : '▸' }, 'chev'),
              jsx('span', { children: 'More options' }, 'label')
            ]
          }, 'toggle'),
          moreNodes
        ]}, 'more'),
        jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
          jsx('span', { className: 'tq-hint', children: '⌘/Ctrl+Enter to post' }, 'hint'),
          jsxs('div', { className: 'flex items-center gap-2', children: [
            jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: 'Cancel' }, 'cancel'),
            jsx(Button, { size: 'sm', variant: 'default', onClick: handleCreate, disabled: !canCreate, children: submitting ? 'Creating…' : 'Create' }, 'create')
          ]}, 'footer-buttons')
        ]}, 'footer')
      ]
    })
  })
}

// ── Published history + X Article composer (V2) ─────────────────────────────

/** A list row "looks like" an X Article when NO post platform is enabled —
 *  x_article is standalone and never mixed, so article rows carry no
 *  *_post_enabled flags. The list schema never includes platforms /
 *  x_article_* / content_markdown; confirmation requires a detail fetch.
 */
function isArticleCandidate(row) {
  if (!row) return false
  return !PLATFORMS.some(p => row[p.key])
}

/** GET /draft/{id} and normalize to the article editor shape. Returns null
 *  when the draft is not an X Article. The detail carries
 *  platforms.x_article.content_markdown WITH comment markers — prefill exact
 *  markdown so a later PATCH round-trips the markers (no 409).
 */
async function fetchArticleDetail(row) {
  if (!row || !row.id) return null
  const data = await rest(`/draft/${row.id}`)
  if (!data || !data.platforms || !data.platforms.x_article) return null
  const art = data.platforms.x_article
  return {
    id: data.id != null ? data.id : row.id,
    content_markdown: art.content_markdown || '',
    cover_media_id: art.cover_media_id || '',
    private_url: data.private_url || row.private_url || '',
    share_url: data.share_url || row.share_url || '',
    x_article_published_url: data.x_article_published_url || ''
  }
}

/** Best URL to open for an article detail: the live X article when published,
 *  else the Typefully private URL, else the share URL. */
function articleOpenUrl(detail) {
  if (!detail) return null
  return detail.x_article_published_url || detail.private_url || detail.share_url || null
}

/** Standalone X Article composer. Create mode posts a new article draft;
 *  edit mode PATCHes an existing one. Never publishes or schedules.
 *  Props: { onClose, onSaved, editing } — editing is a draft object with
 *  { id, content_markdown, cover_media_id, private_url, share_url }.
 */
function ArticleComposerModal({ onClose, onSaved, editing }) {
  const isEdit = !!(editing && editing.id)
  const [markdown, setMarkdown] = useState(editing ? (editing.content_markdown || '') : '')
  const [cover, setCover] = useState(editing ? (editing.cover_media_id || '') : '')
  const [submitting, setSubmitting] = useState(false)
  const canSave = markdown.trim().length > 0 && !submitting

  const handleSave = async () => {
    if (!canSave) return
    setSubmitting(true)
    try {
      const body = { platforms: ['x_article'], content_markdown: markdown.trim() }
      if (cover.trim()) body.cover_media_id = cover.trim()
      if (isEdit) {
        const id = editing.id
        await rest(`/draft/${id}`, { method: 'PATCH', body })
      } else {
        await rest('/draft', { method: 'POST', body })
      }
      haptic('tap')
      host.notify({ kind: 'info', message: isEdit ? 'Article updated' : 'Article saved as draft' })
      if (onSaved) onSaved()
      onClose()
    } catch (err) {
      host.notify({ kind: 'error', message: err?.message || 'Failed to save article' })
    } finally {
      setSubmitting(false)
    }
  }

  const saveRef = useRef(handleSave)
  saveRef.current = handleSave
  const closeRef = useRef(onClose)
  closeRef.current = onClose
  useEffect(() => {
    const onKey = e => {
      if (e.key === 'Escape') {
        e.preventDefault()
        closeRef.current()
      } else if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault()
        saveRef.current()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const editUrl = editing && (editing.private_url || editing.share_url || 'https://typefully.com')

  return jsx('div', {
    className: 'tq-modal-backdrop',
    onClick: e => { if (e.target === e.currentTarget) onClose() },
    children: jsxs('div', {
      className: 'tq-modal flex flex-col gap-3',
      onClick: e => e.stopPropagation(),
      children: [
        jsxs('div', { className: 'tq-modal-header', children: [
          jsx('span', { className: 'text-sm font-semibold', children: isEdit ? 'Edit Article' : 'New Article' }, 'title'),
          jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: '✕' }, 'close')
        ]}, 'header'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Markdown' }, 'label'),
          jsx(Textarea, {
            className: 'tq-compose',
            placeholder: '# Title\n\nWrite the article body in Markdown…',
            value: markdown,
            onChange: e => setMarkdown(e.target.value),
            autoFocus: true
          }, 'markdown')
        ]}, 'markdown-row'),
        jsxs('div', { className: 'flex flex-col gap-1', children: [
          jsx('span', { className: 'tq-label', children: 'Cover media ID (optional)' }, 'label'),
          jsx(Input, {
            value: cover,
            onChange: e => setCover(e.target.value),
            placeholder: 'Ready media UUID for the cover'
          }, 'cover')
        ]}, 'cover-row'),
        jsxs('div', { className: 'flex items-center justify-between gap-2', children: [
          jsx('span', { className: 'tq-hint', children: 'Stored as a draft — no posting or scheduling.' }, 'hint'),
          jsxs('div', { className: 'flex items-center gap-2', children: [
            isEdit ? jsx(Button, { size: 'sm', variant: 'outline', onClick: () => openExternal(editUrl), children: 'Open in Typefully' }, 'open') : null,
            jsx(Button, { size: 'sm', variant: 'ghost', onClick: onClose, children: 'Cancel' }, 'cancel'),
            jsx(Button, { size: 'sm', variant: 'default', onClick: handleSave, disabled: !canSave, children: submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Save draft' }, 'save')
          ]}, 'footer-buttons')
        ]}, 'footer')
      ]
    })
  })
}

/** Reverse-chronological published history. Fetches GET /drafts?status=published
 *  with page size LIMIT and offset-based pagination. Rows show title, preview,
 *  published date, and links to the live platform post. Article-looking rows
 *  (no post-platform enables) get a safe Open action: it detail-fetches
 *  (GET /draft/{id}) and opens the live x_article_published_url or the
 *  Typefully private/share URL. Published articles are NOT editable in-plugin
 *  — Typefully documents post-publication edits as X-managed.
 */
function PublishedHistory() {
  const [offset, setOffset] = useState(0)
  const history = useQuery({
    queryKey: ['typefully-q', 'published', offset],
    queryFn: () => rest(`/drafts?status=published&limit=${LIMIT}&offset=${offset}&order_by=-published_at`),
    staleTime: 30_000,
    retry: false
  })

  const handleOpenArticle = async row => {
    try {
      const detail = await fetchArticleDetail(row)
      const url = articleOpenUrl(detail) || row.private_url || row.share_url || null
      if (!url) {
        host.notify({ kind: 'info', message: 'No public URL for this article' })
        return
      }
      openExternal(url)
    } catch (err) {
      host.notify({ kind: 'error', message: err?.message || 'Failed to open article' })
    }
  }

  let body
  if (history.isPending) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(Loader, { label: 'Loading published…' }) })
  } else if (history.isError) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center p-4',
      children: [
        jsx(EmptyState, { title: 'Could not load published posts', description: history.error?.message || '' }),
        jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => void history.refetch(), children: 'Retry' })
      ]
    })
  } else if (!history.data || !Array.isArray(history.data.results) || !history.data.results.length) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(EmptyState, { title: 'Nothing published yet' }) })
  } else {
    const rows = history.data.results.map(d => {
      const title = d.draft_title || d.preview || 'Untitled'
      const articleCandidate = isArticleCandidate(d)
      const links = []
      for (const p of PLATFORMS) {
        const url = d[`${p.api}_published_url`]
        if (d[p.key] && url) links.push({ label: p.label, url })
      }
      return jsxs('div', {
        className: 'flex items-center gap-3 border-b border-(--ui-stroke-tertiary) p-3',
        children: [
          jsxs('div', { className: 'min-w-0 flex-1', children: [
            jsx('div', { className: 'truncate text-sm font-medium', children: title }),
            d.preview ? jsx('div', { className: 'tq-draft-preview truncate text-xs', children: d.preview }) : null,
            jsxs('div', { className: 'flex items-center gap-2', children: [
              d.published_at ? jsx('span', { className: 'tq-hint', children: fmtDayShort(d.published_at) }) : null,
              ...links.map(l => jsx('a', {
                href: l.url,
                onClick: e => { e.preventDefault(); openExternal(l.url) },
                className: 'tq-tag cursor-pointer',
                children: l.label
              }, `link-${l.url}`)),
              articleCandidate ? jsx(Button, { size: 'sm', variant: 'outline', onClick: () => handleOpenArticle(d), children: 'Open article' }, 'open') : null
            ]}, 'meta')
          ]}, 'main')
        ]
      }, `row-${d.id}`)
    })
    const canPrev = offset > 0 || !!history.data.previous
    const canNext = !!history.data.next || (history.data.count > offset + (history.data.results.length || 0))
    body = jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
      jsx('div', { className: 'tq-scroll min-w-0 flex-1', children: rows }, 'list'),
      jsxs('div', { className: 'flex items-center justify-between gap-2 border-t border-(--ui-stroke-tertiary) p-2', children: [
        jsx('span', { className: 'tq-hint', children: history.data.count != null ? `${history.data.count} published` : '' }),
        jsxs('div', { className: 'flex items-center gap-1', children: [
          canPrev ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setOffset(o => Math.max(0, o - LIMIT)), children: '← Previous' }, 'prev') : null,
          canNext ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setOffset(o => o + LIMIT), children: 'Next →' }, 'next') : null
        ]}, 'pager')
      ]}, 'pager-row')
    ]})
  }

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
    jsxs('div', { className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-tertiary) p-3', children: [
      jsx('span', { className: 'text-sm font-semibold', children: 'Published' }),
      jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => void history.refetch(), disabled: history.isFetching, children: history.isFetching ? 'Refreshing…' : 'Refresh' })
    ]}),
    jsx('div', { className: 'min-h-0 flex-1', children: body })
  ] })
}

/** status=draft X Articles. Minimal discoverable UI: create a new standalone
 *  X Article (Write Article in the header) and load/edit existing draft
 *  articles. List rows never carry platforms; article-looking rows (no
 *  post-platform enables) get an Edit affordance that detail-fetches FIRST
 *  (GET /draft/{id}) to confirm x_article and prefill exact content_markdown.
 */
function DraftsList({ onEditArticle }) {
  const [offset, setOffset] = useState(0)
  const [busyId, setBusyId] = useState(null)
  const drafts = useQuery({
    queryKey: ['typefully-q', 'drafts', offset],
    queryFn: () => rest(`/drafts?status=draft&limit=${LIMIT}&offset=${offset}`),
    staleTime: 30_000,
    retry: false
  })

  const handleEditArticle = async row => {
    setBusyId(row.id)
    try {
      const detail = await fetchArticleDetail(row)
      if (!detail) {
        host.notify({ kind: 'info', message: 'Not an X Article draft' })
        return
      }
      if (onEditArticle) onEditArticle(detail)
    } catch (err) {
      host.notify({ kind: 'error', message: err?.message || 'Failed to load draft' })
    } finally {
      setBusyId(null)
    }
  }

  let body
  if (drafts.isPending) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(Loader, { label: 'Loading drafts…' }) })
  } else if (drafts.isError) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center p-4',
      children: [
        jsx(EmptyState, { title: 'Could not load drafts', description: drafts.error?.message || '' }),
        jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => void drafts.refetch(), children: 'Retry' })
      ]
    })
  } else if (!drafts.data || !Array.isArray(drafts.data.results) || !drafts.data.results.length) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(EmptyState, { title: 'No drafts yet' }) })
  } else {
    const rows = drafts.data.results.map(d => {
      const title = d.draft_title || d.preview || 'Untitled'
      const articleCandidate = isArticleCandidate(d)
      return jsxs('div', {
        className: 'flex items-center gap-3 border-b border-(--ui-stroke-tertiary) p-3',
        children: [
          jsxs('div', { className: 'min-w-0 flex-1', children: [
            jsx('div', { className: 'truncate text-sm font-medium', children: title }),
            d.preview ? jsx('div', { className: 'tq-draft-preview truncate text-xs', children: d.preview }) : null,
            jsxs('div', { className: 'flex items-center gap-2', children: [
              d.created_at ? jsx('span', { className: 'tq-hint', children: fmtDayShort(d.created_at) }) : null,
              articleCandidate ? jsx('span', { className: 'tq-tag', children: 'Article' }, 'tag') : null
            ]}, 'meta')
          ]}, 'main'),
          articleCandidate
            ? jsx(Button, {
                size: 'sm',
                variant: 'outline',
                disabled: busyId === d.id,
                onClick: () => handleEditArticle(d),
                children: busyId === d.id ? 'Loading…' : 'Edit'
              }, 'edit')
            : jsx(Button, {
                size: 'sm',
                variant: 'ghost',
                onClick: () => { if (d.private_url || d.share_url) openExternal(d.private_url || d.share_url) },
                children: 'Open'
              }, 'open')
        ]
      }, `row-${d.id}`)
    })
    const canPrev = offset > 0 || !!drafts.data.previous
    const canNext = !!drafts.data.next || (drafts.data.count > offset + (drafts.data.results.length || 0))
    body = jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
      jsx('div', { className: 'tq-scroll min-w-0 flex-1', children: rows }, 'list'),
      jsxs('div', { className: 'flex items-center justify-between gap-2 border-t border-(--ui-stroke-tertiary) p-2', children: [
        jsx('span', { className: 'tq-hint', children: drafts.data.count != null ? `${drafts.data.count} drafts` : '' }),
        jsxs('div', { className: 'flex items-center gap-1', children: [
          canPrev ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setOffset(o => Math.max(0, o - LIMIT)), children: '← Previous' }, 'prev') : null,
          canNext ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setOffset(o => o + LIMIT), children: 'Next →' }, 'next') : null
        ]}, 'pager')
      ]}, 'pager-row')
    ]})
  }

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
    jsxs('div', { className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-tertiary) p-3', children: [
      jsx('span', { className: 'text-sm font-semibold', children: 'Drafts' }),
      jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => void drafts.refetch(), disabled: drafts.isFetching, children: drafts.isFetching ? 'Refreshing…' : 'Refresh' })
    ]}),
    jsx('div', { className: 'min-h-0 flex-1', children: body })
  ] })
}

/** Read-only X analytics tab. Loads posts + followers for a 7/30/90-day window,
 *  shows follower count/delta, impression/engagement summary, and top posts
 *  sorted by impressions. Null metrics render as "unavailable" (never zero).
 *  Posts are paginated (limit 100); all pages are fetched via offset before
 *  summarize/top ranking. Partial-data warning if the defensive page cap hits.
 *  No publish/schedule/create actions.
 */
function AnalyticsPanel() {
  const [days, setDays] = useState(7)
  const range = analyticsDateRange(days)
  const analytics = useQuery({
    queryKey: ['typefully-q', 'analytics', days, range.start, range.end],
    queryFn: async () => {
      const qs = `start_date=${range.start}&end_date=${range.end}`
      const [postsPage, followers] = await Promise.all([
        fetchAllAnalyticsXPosts(rest, {
          startDate: range.start,
          endDate: range.end,
          includeReplies: false
        }),
        rest(`/analytics/x/followers?${qs}`)
      ])
      return {
        posts: {
          results: postsPage.results,
          limit: postsPage.limit,
          offset: 0,
          next: postsPage.truncated ? 'capped' : null,
          previous: null,
          truncated: postsPage.truncated,
          pages: postsPage.pages
        },
        followers,
        start: range.start,
        end: range.end,
        days,
        posts_truncated: postsPage.truncated
      }
    },
    staleTime: 30_000,
    retry: false
  })

  const header = jsxs('div', {
    className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-tertiary) p-3',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx('span', { className: 'text-sm font-semibold', children: 'Analytics' }, 'title'),
        jsx(Badge, { variant: 'muted', children: 'X' }, 'plat')
      ]}, 'left'),
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx(SegmentedControl, {
          value: String(days),
          onChange: v => setDays(Number(v)),
          options: ANALYTICS_DAY_OPTIONS.map(d => ({ id: String(d), label: `${d}d` }))
        }, 'days'),
        jsx(Button, {
          size: 'sm',
          variant: 'ghost',
          onClick: () => void analytics.refetch(),
          disabled: analytics.isFetching,
          children: analytics.isFetching ? 'Refreshing…' : 'Refresh'
        }, 'refresh')
      ]}, 'right')
    ]
  })

  let body
  if (analytics.isPending) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(Loader, { label: 'Loading analytics…' }) })
  } else if (analytics.isError) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center p-4',
      children: [
        jsx(EmptyState, { title: 'Could not load analytics', description: analytics.error?.message || '' }),
        jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => void analytics.refetch(), children: 'Retry' })
      ]
    })
  } else {
    const data = analytics.data || {}
    const postResults = (data.posts && data.posts.results) || []
    const postsTruncated = !!(data.posts_truncated || (data.posts && data.posts.truncated))
    const summary = summarizeXPosts(postResults)
    const fol = followerDelta(data.followers || {})
    const deltaLabel = formatDelta(fol.delta)

    const summaryCards = jsxs('div', {
      className: 'grid grid-cols-2 gap-2 p-3 sm:grid-cols-4',
      children: [
        jsxs('div', { className: 'tq-slotpick flex flex-col gap-0.5', children: [
          jsx('span', { className: 'tq-label', children: 'Followers' }),
          jsx('span', { className: 'text-sm font-semibold tq-counter', children: formatMetric(fol.current) }),
          jsx('span', { className: 'tq-hint', children: `Δ ${deltaLabel}` })
        ]}, 'followers'),
        jsxs('div', { className: 'tq-slotpick flex flex-col gap-0.5', children: [
          jsx('span', { className: 'tq-label', children: 'Impressions' }),
          jsx('span', { className: 'text-sm font-semibold tq-counter', children: formatMetric(summary.total_impressions) })
        ]}, 'impr'),
        jsxs('div', { className: 'tq-slotpick flex flex-col gap-0.5', children: [
          jsx('span', { className: 'tq-label', children: 'Engagements' }),
          jsx('span', { className: 'text-sm font-semibold tq-counter', children: formatMetric(summary.total_engagements) })
        ]}, 'eng'),
        jsxs('div', { className: 'tq-slotpick flex flex-col gap-0.5', children: [
          jsx('span', { className: 'tq-label', children: 'Engagement rate' }),
          jsx('span', { className: 'text-sm font-semibold tq-counter', children: formatMetric(summary.engagement_rate, { style: 'rate' }) })
        ]}, 'rate')
      ]
    })

    const partialWarning = postsTruncated
      ? jsx('div', {
          className: 'mx-3 mt-2 rounded border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary) px-3 py-2 text-xs',
          role: 'status',
          children: 'Partial data: post list was capped before all pages loaded. Totals and top posts may be incomplete.'
        }, 'partial')
      : null

    let list
    if (!summary.top_posts.length) {
      list = jsx('div', {
        className: 'grid place-items-center p-4',
        children: jsx(EmptyState, { title: 'No posts in this range', description: 'Nothing to show for the selected window.' })
      })
    } else {
      list = summary.top_posts.map(p => {
        const safeUrl = typeof p.url === 'string' && /^https?:\/\//i.test(p.url) ? p.url : null
        return jsxs('div', {
          className: 'flex items-start gap-3 border-b border-(--ui-stroke-tertiary) p-3',
          children: [
            jsxs('div', { className: 'min-w-0 flex-1', children: [
              jsx('div', { className: 'truncate text-sm font-medium', children: p.preview || '(no preview)' }),
              jsxs('div', { className: 'flex flex-wrap items-center gap-2', children: [
                p.created_at ? jsx('span', { className: 'tq-hint', children: fmtDayShort(p.created_at) }) : null,
                jsx('span', { className: 'tq-tag', children: `Imp ${formatMetric(p.impressions)}` }),
                jsx('span', { className: 'tq-tag', children: `Eng ${formatMetric(p.engagements)}` }),
                safeUrl
                  ? jsx('a', {
                      href: safeUrl,
                      onClick: e => { e.preventDefault(); openExternal(safeUrl) },
                      className: 'tq-tag cursor-pointer',
                      children: 'Open'
                    }, 'link')
                  : null
              ]})
            ]})
          ]
        }, `ap-${p.post_id || p.preview}`)
      })
    }

    body = jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
      partialWarning,
      summaryCards,
      jsx('div', { className: 'px-3 pb-1 tq-label', children: 'Top posts by impressions' }),
      jsx('div', { className: 'tq-scroll min-w-0 flex-1', children: list })
    ]})
  }

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
    header,
    jsx('div', { className: 'min-h-0 flex-1', children: body })
  ] })
}

// ── Full page ───────────────────────────────────────────────────────────────

function TypefullyPage() {
  const t = usePluginI18n(ID)
  const [weekOffset, setWeekOffset] = useState(0)
  const [selected, setSelected] = useState(null) // draft object
  const [showComposer, setShowComposer] = useState(false)
  const [view, setView] = useState('queue') // 'queue' | 'drafts' | 'published' | 'analytics'
  const [articleEditor, setArticleEditor] = useState(null) // null | { editing: draft|null }
  const [dragDraft, setDragDraft] = useState(null) // draft being dragged
  const [dragOverDay, setDragOverDay] = useState(null) // date string of day being hovered
  const [dragOverY, setDragOverY] = useState(null) // pixel y in the column

  const now = new Date()
  const weekStart = new Date(now.getTime() + weekOffset * WEEK_MS)
  weekStart.setHours(0, 0, 0, 0)
  const weekEnd = new Date(weekStart.getTime() + WEEK_MS - 1)
  const startDate = isoDay(weekStart)
  const endDate = isoDay(weekEnd)

  const queue = useQuery({
    queryKey: ['typefully-q', 'queue', startDate, endDate],
    queryFn: () => rest(`/queue?start_date=${startDate}&end_date=${endDate}`),
    staleTime: 30_000,
    refetchInterval: 120_000,
    retry: false
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['typefully-q'] })
  }

  const scheduleDraft = useMutation({
    mutationFn: ({ draftId, at }) =>
      rest(`/draft/${draftId}`, { method: 'PATCH', body: { publish_at: at } }),
    onSuccess: () => {
      setSelected(null)
      invalidate()
      haptic('tap')
      host.notify({ kind: 'info', message: 'Rescheduled' })
    }
  })

  const publishDraft = useMutation({
    mutationFn: draftId => rest(`/draft/${draftId}`, { method: 'PATCH', body: { publish_at: 'now' } }),
    onSuccess: () => {
      setSelected(null)
      invalidate()
      haptic('tap')
      host.notify({ kind: 'info', message: 'Published' })
    }
  })

  const deleteDraft = useMutation({
    mutationFn: draftId => rest(`/draft/${draftId}`, { method: 'DELETE' }),
    onSuccess: () => {
      setSelected(null)
      invalidate()
      haptic('tap')
      host.notify({ kind: 'info', message: 'Deleted' })
    }
  })

  const createDraft = useMutation({
    mutationFn: ({ text, publishAt }) =>
      rest('/draft', { method: 'POST', body: { text, publish_at: publishAt } }),
    onSuccess: () => {
      invalidate()
      haptic('tap')
      host.notify({ kind: 'info', message: 'Post created' })
    },
    onError: err => {
      host.notify({ kind: 'error', message: err?.message || 'Failed to create post' })
    }
  })

  const rescheduleDraft = useMutation({
    mutationFn: ({ draftId, at }) =>
      rest(`/draft/${draftId}`, { method: 'PATCH', body: { publish_at: at } }),
    onMutate: async ({ draftId, at }) => {
      // Cancel refetch so it doesn't clobber our optimistic update
      await queryClient.cancelQueries({ queryKey: ['typefully-q', 'queue'] })
      // Snapshot previous value
      const prevData = queryClient.getQueryData(['typefully-q', 'queue', startDate, endDate])
      // Optimistically move the draft in the queue data
      if (prevData && Array.isArray(prevData.days)) {
        const next = { ...prevData, days: prevData.days.map(day => {
          let found = false
          const items = (day.items || []).map(item => {
            if (item.draft && item.draft.id === draftId) {
              found = true
              // Move to target day + hour
              const targetDate = at.slice(0, 10)
              if (day.date === targetDate) {
                return { ...item, at }
              }
              // Moved to a different day: remove from source, add to target
              return null
            }
            return item
          }).filter(Boolean)

          // If target day is this one and draft wasn't already here, add it
          if (day.date === at.slice(0, 10) && !found) {
            const draft = prevData.days.flatMap(d => d.items || [])
              .find(i => i.draft && i.draft.id === draftId)
            if (draft) {
              items.push({ ...draft, at, kind: 'custom_time' })
            }
          }
          return { ...day, items }
        })}
        queryClient.setQueryData(['typefully-q', 'queue', startDate, endDate], next)
      }
      return { prevData }
    },
    onError: (err, vars, context) => {
      // Revert optimistic update
      if (context?.prevData) {
        queryClient.setQueryData(['typefully-q', 'queue', startDate, endDate], context.prevData)
      }
      host.notify({ kind: 'error', message: err?.message || 'Failed to reschedule' })
    },
    onSettled: () => {
      invalidate()
    },
    onSuccess: () => {
      haptic('tap')
      host.notify({ kind: 'info', message: 'Rescheduled' })
    }
  })

  const slots = freeSlotsIn(queue.data)
  const busy = scheduleDraft.isPending || publishDraft.isPending || deleteDraft.isPending || createDraft.isPending || rescheduleDraft.isPending

  const handleToggle = draft => {
    haptic('tap')
    setSelected(prev => (prev && prev.id === draft.id ? null : draft))
  }

  const handleMove = (at, draft) => scheduleDraft.mutate({ draftId: draft.id, at })
  const handlePublish = draft => publishDraft.mutate(draft.id)
  const handleDelete = draft => deleteDraft.mutate(draft.id)

  // ── Drag-and-drop handlers ────────────────────────────────────────────────
  const handleDragStart = (e, draft) => {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData('text/plain', draft.id)
    setDragDraft(draft)
  }

  const handleDragEnd = () => {
    setDragDraft(null)
    setDragOverDay(null)
    setDragOverY(null)
  }

  const handleDragOver = (e, date) => {
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (date !== dragOverDay) setDragOverDay(date)
    // Calculate target hour from cursor y
    const col = e.currentTarget
    const rect = col.getBoundingClientRect()
    const y = e.clientY - rect.top
    setDragOverY(y)
  }

  const handleDragLeave = (e, date) => {
    // Only clear if we're leaving the column, not entering a child
    if (e.currentTarget.contains(e.relatedTarget)) return
    if (dragOverDay === date) {
      setDragOverDay(null)
      setDragOverY(null)
    }
  }

  const handleDrop = (e, date) => {
    e.preventDefault()
    const draftId = parseInt(e.dataTransfer.getData('text/plain'), 10)
    if (!draftId || isNaN(draftId)) return

    // Calculate target hour from drop position
    const col = e.currentTarget
    const rect = col.getBoundingClientRect()
    const y = e.clientY - rect.top
    const hour = GRID_START + y / ROW_H
    const h = Math.floor(hour)
    const m = Math.round((hour - h) * 60 / 15) * 15
    const targetHour = m === 60 ? h + 1 : h
    const targetMin = m === 60 ? 0 : m

    // Build proper ISO datetime from the drop position (local time)
    const targetDate = new Date(
      `${date}T${String(targetHour).padStart(2, '0')}:${String(targetMin).padStart(2, '0')}:00`
    )

    // Guard against past times — Typefully will 422 these
    if (targetDate.getTime() < Date.now()) {
      host.notify({ kind: 'error', message: 'Cannot schedule in the past' })
      handleDragEnd()
      return
    }

    const targetAt = targetDate.toISOString()
    rescheduleDraft.mutate({ draftId, at: targetAt })
    handleDragEnd()
  }

  // ── Drag-over indicator inside column ─────────────────────────────────────
  const dragOverIndicator = dragOverDay && dragOverY != null
    ? jsx('div', {
        className: 'tq-now',
        style: { top: `${dragOverY}px`, borderTopStyle: 'dashed', borderTopColor: 'var(--ui-accent)', opacity: 0.7 },
        children: jsx('div', {
          className: 'tq-now-dot',
          style: { opacity: 0.8 }
        })
      })
    : null

  const header = jsxs('div', {
    className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-tertiary) p-3',
    children: [
      jsxs('div', { className: 'flex items-center gap-2', children: [
        jsx('span', { className: 'text-sm font-semibold', children: 'Typefully Q' }),
        view === 'queue'
          ? jsx(Badge, { variant: 'muted', children: `${fmtDayShort(startDate)} – ${fmtDayShort(endDate)}` })
          : null
      ]}),
      jsxs('div', { className: 'flex items-center gap-1', children: [
        jsx(Button, { size: 'sm', variant: 'outline', onClick: () => setArticleEditor({ editing: null }), children: 'Write Article' }),
        jsx(SegmentedControl, {
          value: view,
          onChange: setView,
          options: [
            { id: 'queue', label: 'Queue' },
            { id: 'drafts', label: 'Drafts' },
            { id: 'published', label: 'Published' },
            { id: 'analytics', label: 'Analytics' }
          ]
        }, 'view'),
        view === 'queue' ? jsx(Button, { size: 'sm', variant: 'primary', onClick: () => setShowComposer(true), children: 'Create Post' }) : null,
        view === 'queue' ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setWeekOffset(0), children: 'Today' }) : null,
        view === 'queue' ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setWeekOffset(o => o - 1), children: '‹' }) : null,
        view === 'queue' ? jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => setWeekOffset(o => o + 1), children: '›' }) : null,
        jsx(Button, {
          size: 'sm',
          variant: 'ghost',
          onClick: () => void (view === 'queue' ? queue.refetch() : null),
          disabled: view === 'queue' && queue.isFetching,
          'aria-label': view === 'queue' && queue.isFetching ? 'Refreshing Typefully queue' : 'Refresh Typefully',
          children: view === 'queue' && queue.isFetching ? 'Refreshing…' : 'Refresh'
        })
      ]})
    ]
  })

  const colHeight = (GRID_END - GRID_START) * ROW_H

  // Hour gridlines shared by all columns (positioned via inline style).
  const gridlines = []
  for (let h = GRID_START; h <= GRID_END; h++) {
    const isMajor = h % 2 === 0
    const isStart = h === GRID_START
    gridlines.push(jsx('div', {
      className: cn('tq-hr', isMajor && 'tq-hr-major', isStart && 'tq-hr-start'),
      style: { top: `${(h - GRID_START) * ROW_H}px` }
    }, `hr-${h}`))
  }

  let body
  if (view === 'analytics') {
    body = jsx(AnalyticsPanel, {})
  } else if (view === 'published') {
    body = jsx(PublishedHistory, {})
  } else if (view === 'drafts') {
    body = jsx(DraftsList, {
      onEditArticle: d => setArticleEditor({ editing: d })
    })
  } else if (queue.isPending) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(Loader, { label: t('loading') }) })
  } else if (queue.isError) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center p-4',
      children: [
        jsx(EmptyState, { title: t('loadFailed'), description: queue.error?.message || '' }),
        jsx(Button, { size: 'sm', variant: 'secondary', onClick: () => void queue.refetch(), children: t('retry') })
      ]
    })
  } else if (!queue.data || !Array.isArray(queue.data.days) || !queue.data.days.length) {
    body = jsx('div', { className: 'grid h-full place-items-center p-4', children: jsx(EmptyState, { title: t('empty') }) })
  } else {
    const days = queue.data.days

    // Per-day vertical positioning: anchor each item at its hour. Drafts that
    // share an hour nudge down slightly so they don't fully overlap. Skip
    // free-slot labels on hours already occupied by a draft.
    const positioned = days.map(day => {
      const items = day.items || []
      const occupied = new Set(
        items.filter(i => i.draft).map(i => Math.floor(hourOf(i.at)))
      )
      const rows = items
        .filter(i => i.draft || !occupied.has(Math.floor(hourOf(i.at))))
        .map(item => ({ item, top: (hourOf(item.at) - GRID_START) * ROW_H }))
        .sort((a, b) => a.top - b.top)
      for (let i = 1; i < rows.length; i++) {
        if (rows[i].top === rows[i - 1].top) rows[i].top += 14
      }
      return rows
    })

    const todayIdx = days.findIndex(d => isToday(d.date))

    body = jsxs('div', {
      className: 'flex h-full min-h-0',
      children: [
        jsxs('div', {
          className: 'tq-scroll min-w-0 flex-1',
          children: [
            jsx('div', {
              className: 'tq-cal',
              children: [
                // Row 1: corner + day headers
                jsx('div', { className: 'tq-corner', children: null }, 'corner'),
                ...days.map(day => jsxs('div', {
                  className: cn('tq-dayhead', isToday(day.date) && 'tq-today'),
                  children: [
                    jsx('div', { className: 'tq-daylabel text-[0.6875rem] font-medium', children: fmtDay(day.date) }),
                    jsx('div', { className: 'text-[0.6875rem] text-(--ui-text-tertiary)', children: fmtDate(day.date) })
                  ]
                }, `dh-${day.date}`)),
                // Row 2: time gutter + day columns
                jsx('div', {
                  className: 'tq-gutter',
                  style: { height: `${colHeight}px` },
                  children: hourLabels()
                }, 'gutter'),
                ...days.map((day, di) => {
                  const today = di === todayIdx
                  const rows = positioned[di]
                  return jsx('div', {
                    className: cn('tq-daycol', today && 'tq-today'),
                    style: { height: `${colHeight}px` },
                    onDragOver: e => handleDragOver(e, day.date),
                    onDragLeave: e => handleDragLeave(e, day.date),
                    onDrop: e => handleDrop(e, day.date),
                    children: [
                      ...gridlines,
                      today && nowHour() >= GRID_START && nowHour() <= GRID_END
                        ? jsxs('div', {
                            className: 'tq-now',
                            style: { top: `${(nowHour() - GRID_START) * ROW_H}px` },
                            children: [jsx('div', { className: 'tq-now-dot' }, 'now-dot')]
                          }, 'now-line')
                        : null,
                      dragOverDay === day.date ? dragOverIndicator : null,
                      ...rows
                        .filter(({ item }) => item.draft || item.kind === 'queue_slot')
                        .map(({ item, top }) => {
                        if (item.kind === 'queue_slot' && !item.draft) {
                          // Free slot: whisper-faint time label, no box.
                          return jsx('div', {
                            className: 'tq-slot',
                            style: { top: `${top}px`, height: `${ROW_H}px` },
                            children: fmtTime(item.at)
                          }, `slot-${item.at}`)
                        }
                        // item.draft must be truthy here (guaranteed by filter)
                        const d = item.draft
                        const isSel = selected && selected.id === d.id
                        return jsx('div', {
                          className: cn('tq-draft', dragDraft && dragDraft.id === d.id ? 'tq-dragging' : ''),
                          'data-selected': isSel ? 'true' : 'false',
                          style: { top: `${top}px`, height: `${ROW_H - 4}px` },
                          title: d.draft_title || 'Untitled',
                          draggable: true,
                          onDragStart: e => handleDragStart(e, d),
                          onDragEnd: handleDragEnd,
                          onClick: () => handleToggle(d),
                          children: jsxs('div', { className: 'flex h-full min-h-0 flex-col gap-0.5', children: [
                            jsxs('div', { className: 'flex items-center gap-1', children: [
                              jsx('span', { className: 'tq-draft-title min-w-0 flex-1', children: d.draft_title || 'Untitled' }),
                              jsx('span', { className: 'tq-draft-time', children: fmtTime(item.at) })
                            ]}),
                            platformTags(d)
                          ]})
                        }, `draft-${d.id}`)
                      })
                    ]
                  }, `day-${day.date}`)
                })
              ]
            })
          ]
        }),
        selected ? jsx(DraftDetail, {
          draft: selected,
          slots,
          onClose: () => setSelected(null),
          onPublish: handlePublish,
          onDelete: handleDelete,
          onMove: handleMove,
          busy
        }) : null
      ]
    })
  }

  return jsxs('div', { className: 'flex h-full min-h-0 flex-col', children: [
    header,
    jsx('div', { className: 'min-h-0 flex-1', children: body }),
    showComposer ? jsx(CreatePostModal, {
      onClose: () => setShowComposer(false),
      onCreated: () => invalidate()
    }) : null,
    articleEditor ? jsx(ArticleComposerModal, {
      editing: articleEditor.editing,
      onClose: () => setArticleEditor(null),
      onSaved: () => invalidate()
    }) : null
  ] })
}

// ── Statusbar chip ──────────────────────────────────────────────────────────

function QueueChip() {
  const today = isoDay(new Date())
  const queue = useQuery({
    queryKey: ['typefully-q', 'chip', today],
    queryFn: () => rest(`/queue?start_date=${today}&end_date=${today}`),
    staleTime: 60_000,
    refetchInterval: 300_000,
    retry: false
  })
  let count = null
  if (queue.data && Array.isArray(queue.data.days) && queue.data.days.length) {
    count = (queue.data.days[0].items || []).filter(i => i.draft).length
  }
  const label = count === null ? 'Q…' : count > 0 ? `Q ${count}` : 'Q 0'

  return jsx(Tip, {
    label: 'Typefully Q — open calendar',
    children: jsx('button', {
      type: 'button',
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      onClick: () => {
        haptic('tap')
        host.navigate('/typefully')
      },
      children: label
    })
  })
}

// ── Plugin export ───────────────────────────────────────────────────────────

export default {
  id: ID,
  name: 'Typefully Queue',
  description: 'Typefully Q calendar — full-page time-grid week view of scheduled drafts and free slots, reschedule / publish / delete from the desktop.',
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    ensurePluginStyles()

    ctx.i18n.register({
      en: {
        loading: 'Loading queue…',
        loadFailed: 'Could not load the queue',
        retry: 'Retry',
        empty: 'No queue data for this week'
      }
    })

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/typefully' },
        title: 'Typefully Q',
        render: () => jsx(TypefullyPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/typefully', label: 'Typefully Q', codicon: 'calendar' }
      },
      {
        id: 'chip',
        area: 'statusBar.right',
        order: 120,
        render: () => jsx(QueueChip, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'typefully-q.open',
          label: 'Open Typefully Queue',
          keywords: ['typefully', 'queue', 'calendar', 'schedule', 'q'],
          run: () => {
            haptic('tap')
            host.navigate('/typefully')
          }
        }
      }
    ])
  }
}
