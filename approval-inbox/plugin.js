/**
 * Approval Inbox V2 — attention plane replacing raw source lists with ranked
 * attention views.
 *
 * Backend: /attention returns the normalized envelope (primary queue,
 * secondary buckets, source health, counts). Operator badge = counts.human_now.
 * Primary items render as decision/input cards with why_tony, reason_now,
 * recommended_action, alternatives, consequence_of_delay, age/freshness,
 * project, and evidence. Secondary views are collapsible.
 *
 * Local-only view state: Snooze / Hide / Restore. "Ack" removed per Card G.
 *
 * Plain ESM loaded uncompiled: jsx() calls, NOT JSX syntax. Only
 * @hermes/plugin-sdk, react, react/jsx-runtime resolve.
 */

import {
  Badge,
  Button,
  cn,
  Codicon,
  EmptyState,
  ErrorState,
  haptic,
  host,
  Loader,
  PALETTE_AREA,
  queryClient,
  relativeTime,
  ROUTES_AREA,
  ScrollArea,
  SIDEBAR_NAV_AREA,
  STATUSBAR_AREAS,
  Tip,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

const ID = 'approval-inbox'
const REFETCH_MS = 60_000
const STORAGE_KEY = 'v2_view_state'

// Bounded snooze choices (label -> seconds).
const SNOOZE_CHOICES = [
  { label: 'Snooze 1h',  seconds: 3600 },
  { label: 'Snooze 4h',  seconds: 14400 },
  { label: 'Snooze 1d',  seconds: 86400 },
  { label: 'Snooze 3d',  seconds: 259200 },
]

let rest = null
let storage = null

// { [itemKey]: { until: epoch|null, reason: string, created_at: epoch, fingerprint_at_hide: string|null } }
//   until === null  -> hidden (manual hide)
//   until === epoch -> snoozed until then
//   fingerprint_at_hide: the item's fingerprint when hidden/snoozed; source change overrides hiding.
let viewState = {}
const viewListeners = new Set()

function _now() { return Date.now() }

function bumpView() {
  for (const fn of viewListeners) { fn() }
  if (storage) { storage.set(STORAGE_KEY, viewState) }
}

function hideItem(key, fingerprint) {
  viewState = {
    ...viewState,
    [key]: { until: null, reason: 'hidden', created_at: _now(), fingerprint_at_hide: fingerprint || null }
  }
  bumpView()
}

function snoozeItem(key, seconds, label, fingerprint) {
  viewState = {
    ...viewState,
    [key]: {
      until: _now() + seconds * 1000,
      reason: label || 'snoozed',
      created_at: _now(),
      fingerprint_at_hide: fingerprint || null
    }
  }
  bumpView()
}

function restoreItem(key) {
  const next = { ...viewState }
  delete next[key]
  viewState = next
  bumpView()
}

function isSuppressed(key, fingerprint) {
  const e = viewState[key]
  if (!e) return false
  // Source change overrides local hiding: if the item's fingerprint changed
  // since it was hidden/snoozed, it's no longer suppressed (acceptance §5).
  if (fingerprint && e.fingerprint_at_hide && fingerprint !== e.fingerprint_at_hide) return false
  if (e.until === null) return true
  return e.until > _now()
}

function useViewVersion() {
  const [, force] = useState(0)
  useEffect(() => {
    const fn = () => force(v => v + 1)
    viewListeners.add(fn)
    return () => viewListeners.delete(fn)
  }, [])
  return 0
}

// ---------------------------------------------------------------------------
// CSS — theme vars only
// ---------------------------------------------------------------------------

function ensurePluginStyles() {
  if (typeof document === 'undefined') return
  const css = [
    '.ai-row { display: flex; align-items: flex-start; gap: 0.75rem; padding: 0.5rem 0.75rem; border-bottom: 1px solid var(--ui-stroke-secondary); }',
    '.ai-row:hover { background: var(--ui-bg-tertiary); }',
    '.ai-row-main { flex: 1 1 auto; min-width: 0; }',
    '.ai-row-actions { display: flex; align-items: center; gap: 0.25rem; flex-shrink: 0; }',
    '.ai-section-title { display: flex; align-items: center; gap: 0.5rem; padding: 0.625rem 0.75rem 0.375rem; font-size: 0.6875rem; font-weight: 600; letter-spacing: 0.02em; text-transform: uppercase; color: var(--ui-text-secondary); }',
    '.ai-section-count { margin-left: auto; }',
    '.ai-chip-dot { display: inline-block; width: 8px; height: 8px; border-radius: 9999px; background: var(--ui-orange); }',
    '.ai-chip-dot-ok { background: var(--ui-green); }',
    // Primary card
    '.ai-card { margin: 0.375rem 0.5rem; border-radius: 6px; border: 1px solid var(--ui-stroke-secondary); overflow: hidden; }',
    '.ai-card-header { display: flex; align-items: flex-start; gap: 0.5rem; padding: 0.5rem 0.625rem; background: var(--ui-bg-secondary); }',
    '.ai-card-title { font-size: 0.8125rem; line-height: 1.35; color: var(--ui-text-primary); overflow-wrap: anywhere; font-weight: 600; flex: 1 1 auto; }',
    '.ai-card-body { padding: 0.375rem 0.625rem 0.5rem; }',
    '.ai-card-field { display: flex; gap: 0.375rem; font-size: 0.6875rem; line-height: 1.35; margin-top: 0.25rem; }',
    '.ai-card-label { color: var(--ui-text-quaternary); flex-shrink: 0; min-width: 5rem; }',
    '.ai-card-value { color: var(--ui-text-secondary); overflow-wrap: anywhere; }',
    '.ai-card-missing { color: var(--ui-text-quaternary); font-style: italic; }',
    '.ai-card-meta { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.375rem; padding-top: 0.375rem; border-top: 1px solid var(--ui-stroke-tertiary); }',
    // Collapsible
    '.ai-collapse-trigger { display: flex; align-items: center; gap: 0.375rem; cursor: pointer; user-select: none; }',
    '.ai-collapse-arrow { transition: transform 0.15s; font-size: 0.625rem; }',
    '.ai-collapse-arrow-open { transform: rotate(90deg); }',
    // Severity / class badges
    '.ai-badge-sev { font-size: 0.625rem; padding: 0.0625rem 0.375rem; border-radius: 3px; font-weight: 600; text-transform: uppercase; }',
    '.ai-badge-urgent { background: var(--ui-red); color: var(--ui-text-on-color); }',
    '.ai-badge-high { background: var(--ui-orange); color: var(--ui-text-on-color); }',
    '.ai-badge-normal { background: var(--ui-bg-tertiary); color: var(--ui-text-secondary); }',
    '.ai-badge-low { background: none; color: var(--ui-text-quaternary); }',
    // Source health banner
    '.ai-health-banner { margin: 0.375rem 0.75rem; padding: 0.375rem 0.625rem; font-size: 0.6875rem; line-height: 1.35; color: var(--ui-orange); background: var(--ui-bg-tertiary); border-radius: 4px; border-left: 3px solid var(--ui-orange); }',
    // Collapsed section
    '.ai-collapsed { display: none; }',
    // Snooze dropdown
    '.ai-snooze-menu { position: absolute; top: 100%; right: 0; z-index: 50; min-width: 8rem; padding: 0.25rem 0; background: var(--ui-bg-secondary); border: 1px solid var(--ui-stroke-secondary); border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.25); }',
    '.ai-snooze-item { display: block; width: 100%; padding: 0.25rem 0.75rem; font-size: 0.75rem; text-align: left; color: var(--ui-text-primary); background: none; border: none; cursor: pointer; }',
    '.ai-snooze-item:hover { background: var(--ui-bg-tertiary); }',
  ].join('\n')
  let style = document.getElementById('ai-styles')
  if (!style) {
    style = document.createElement('style')
    style.id = 'ai-styles'
    document.head.appendChild(style)
  }
  if (style.textContent !== css) { style.textContent = css }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtAge(iso) {
  try {
    const ms = new Date(iso).getTime()
    return Number.isFinite(ms) ? relativeTime(ms) : ''
  } catch { return '' }
}

function fmtAgeStr(iso) {
  const a = fmtAge(iso)
  return a ? `open ${a}` : ''
}

function sevBadgeClass(severity) {
  if (severity === 'urgent') return 'ai-badge-sev ai-badge-urgent'
  if (severity === 'high') return 'ai-badge-sev ai-badge-high'
  if (severity === 'normal') return 'ai-badge-sev ai-badge-normal'
  return 'ai-badge-sev ai-badge-low'
}

function classLabel(ac) {
  if (ac === 'approval') return 'Approval'
  if (ac === 'decision') return 'Decision'
  if (ac === 'input_required') return 'Input needed'
  if (ac === 'incident') return 'Incident'
  return ac || ''
}

function primaryGroup(ac) {
  if (ac === 'decision' || ac === 'approval') return 'decision'
  return 'input'
}

// ---------------------------------------------------------------------------
// Data hook
// ---------------------------------------------------------------------------

function useAttention() {
  return useQuery({
    queryKey: [ID, 'attention'],
    queryFn: () => rest('/attention'),
    refetchInterval: REFETCH_MS,
    staleTime: 15_000
  })
}

// ---------------------------------------------------------------------------
// Row actions (local only) — Snooze until… / Hide from this view / Restore
// ---------------------------------------------------------------------------

function RowActions({ itemKey, fingerprint }) {
  const [snoozeOpen, setSnoozeOpen] = useState(false)
  const suppressed = isSuppressed(itemKey, fingerprint)
  if (suppressed) {
    return jsx(Button, {
      size: 'sm', variant: 'ghost',
      onClick: () => { haptic('tap'); restoreItem(itemKey) },
      children: 'Restore'
    })
  }
  return jsxs('div', {
    className: 'ai-row-actions',
    style: { position: 'relative' },
    children: [
      jsx(Button, {
        size: 'sm', variant: 'ghost',
        onClick: () => { haptic('tap'); setSnoozeOpen(o => !o) },
        children: 'Snooze until…'
      }),
      snoozeOpen ? jsx('div', {
        className: 'ai-snooze-menu',
        children: SNOOZE_CHOICES.map(ch =>
          jsx('button', {
            key: ch.label,
            className: 'ai-snooze-item',
            type: 'button',
            onClick: () => {
              haptic('tap')
              snoozeItem(itemKey, ch.seconds, ch.label, fingerprint)
              setSnoozeOpen(false)
            },
            children: ch.label
          })
        )
      }) : null,
      jsx(Button, {
        size: 'sm', variant: 'ghost',
        onClick: () => { haptic('tap'); hideItem(itemKey, fingerprint) },
        children: 'Hide from this view'
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Source health banner
// ---------------------------------------------------------------------------

function SourceHealthBanner({ sourceHealth }) {
  if (!sourceHealth) return null
  const unhealthy = Object.entries(sourceHealth)
    .filter(([, v]) => v && !v.ok)
    .map(([k]) => k)
  if (unhealthy.length === 0) return null
  return jsx('div', {
    className: 'ai-health-banner',
    children: `Source issue: ${unhealthy.join(', ')} — data may be incomplete`
  })
}

// ---------------------------------------------------------------------------
// Primary attention card
// ---------------------------------------------------------------------------

function AttentionCard({ item }) {
  const ac = item.attention_class || ''
  const group = primaryGroup(ac)
  const fp = item.fingerprint || ''
  const suppressed = isSuppressed(item.key, fp)
  if (suppressed) return null

  const missing = jsx('span', { className: 'ai-card-missing', children: '(not available)' })

  return jsxs('div', { className: 'ai-card', children: [
    // Header
    jsxs('div', { className: 'ai-card-header', children: [
      jsx('div', { className: 'ai-card-title', children: item.title || item.key }),
      item.severity ? jsx('span', { className: sevBadgeClass(item.severity), children: item.severity }) : null,
      jsx(RowActions, { itemKey: item.key, fingerprint: fp })
    ] }),
    // Body
    jsxs('div', { className: 'ai-card-body', children: [
      item.why_tony ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'Why you' }),
        jsx('span', { className: 'ai-card-value', children: String(item.why_tony) })
      ] }) : null,
      item.reason_now ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'Why now' }),
        jsx('span', { className: 'ai-card-value', children: String(item.reason_now) })
      ] }) : null,
      item.recommended_action ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'Recommend' }),
        jsx('span', { className: 'ai-card-value', children: trunc(item.recommended_action) })
      ] }) : null,
      (item.alternatives && item.alternatives.length > 0) ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'Alternatives' }),
        jsx('span', { className: 'ai-card-value', children: item.alternatives.slice(0, 4).join(' · ') })
      ] }) : null,
      item.consequence_of_delay ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'If delayed' }),
        jsx('span', { className: 'ai-card-value', children: String(item.consequence_of_delay) })
      ] }) : null,
      item.authority ? jsxs('div', { className: 'ai-card-field', children: [
        jsx('span', { className: 'ai-card-label', children: 'Action' }),
        jsx('span', { className: 'ai-card-value', children: trunc(item.authority, 200) })
      ] }) : null,
      // Meta line: project, age, confidence, verification
      jsxs('div', { className: 'ai-card-meta', children: [
        item.project ? jsx(Badge, { variant: 'muted', children: String(item.project) }) : null,
        ac ? jsx(Badge, { variant: 'outline', children: classLabel(ac) }) : null,
        item.created_at ? jsx('span', { className: 'ai-card-value', children: fmtAgeStr(item.created_at) }) : null,
        item.confidence ? jsx('span', { className: 'ai-card-value', children: `· ${item.confidence} confidence` }) : null,
        (item.verification && item.verification.status !== 'verified') ? jsx('span', { className: 'ai-card-missing', children: `· ${item.verification.status}` }) : null,
        (item.verification && item.verification.evidence && item.verification.evidence.length > 0)
          ? jsx('span', { className: 'ai-card-value', children: `· ${item.verification.evidence.length} evidence` })
          : jsx('span', { className: 'ai-card-missing', children: '· no evidence' })
      ] })
    ] })
  ] })
}

function trunc(s, n) {
  if (!s) return ''
  const t = String(s)
  return t.length <= (n || 180) ? t : t.slice(0, (n || 180) - 3) + '…'
}

// ---------------------------------------------------------------------------
// Collapsible secondary section
// ---------------------------------------------------------------------------

function CollapsibleSection({ title, icon, count, children, defaultOpen }) {
  const [open, setOpen] = useState(!!defaultOpen)
  return jsxs('section', { children: [
    jsxs('div', { className: 'ai-section-title', children: [
      jsx('span', { className: 'ai-collapse-trigger', onClick: () => { haptic('tap'); setOpen(o => !o) }, children: [
        jsx('span', { className: cn('ai-collapse-arrow', open && 'ai-collapse-arrow-open'), children: '▶' }),
        icon ? jsx(Codicon, { name: icon, size: '0.85rem' }) : null,
        jsx('span', { children: title })
      ] }),
      count > 0 ? jsx(Badge, { variant: 'muted', className: 'ai-section-count', children: String(count) }) : null
    ] }),
    jsx('div', { className: open ? undefined : 'ai-collapsed', children })
  ] })
}

// ---------------------------------------------------------------------------
// Secondary item row (simple)
// ---------------------------------------------------------------------------

function SecondaryRow({ item }) {
  const fp = item.fingerprint || ''
  if (isSuppressed(item.key, fp)) return null
  return jsxs('div', { key: item.key, className: 'ai-row', children: [
    jsxs('div', { className: 'ai-row-main', children: [
      jsx('div', { className: 'ai-row-title', children: item.title || item.key }),
      item.project ? jsx('div', { className: 'ai-row-sub', children: item.project }) : null,
      item.created_at ? jsx('div', { className: 'ai-row-meta', children: fmtAgeStr(item.created_at) }) : null
    ] }),
    jsx(RowActions, { itemKey: item.key, fingerprint: fp })
  ] })
}

// ---------------------------------------------------------------------------
// Suppressed strip
// ---------------------------------------------------------------------------

function SuppressedStrip({ allItems }) {
  useViewVersion()
  const hidden = allItems.filter(it => isSuppressed(it.key, it.fingerprint || ''))
  if (hidden.length === 0) return null
  return jsxs('div', {
    className: 'mt-4 border-t border-(--ui-stroke-tertiary) pt-2',
    children: [
      jsx('div', {
        className: 'px-3 pb-1 text-[0.625rem] font-medium uppercase tracking-wide text-(--ui-text-quaternary)',
        children: `Hidden / Snoozed (${hidden.length})`
      }),
      jsx('div', {
        children: hidden.map(item =>
          jsxs('div', { key: item.key, className: 'ai-row', children: [
            jsx('div', { className: 'ai-row-main', children: jsx('div', { className: 'ai-row-title', children: item.title || item.key }) }),
            jsx(Button, {
              size: 'sm', variant: 'ghost',
              onClick: () => { haptic('tap'); restoreItem(item.key) },
              children: 'Restore'
            })
          ] })
        )
      })
    ]
  })
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

function InboxPage() {
  useViewVersion()
  const q = useAttention()

  if (q.isLoading) {
    return jsx('div', { className: 'flex h-full items-center justify-center', children: jsx(Loader, {}) })
  }
  if (q.isError) {
    return jsx(ErrorState, {
      title: 'Approval Inbox unavailable',
      description: 'The plugin backend could not be reached.',
      children: jsx(Button, { size: 'sm', onClick: () => { haptic('tap'); void queryClient.invalidateQueries({ queryKey: [ID] }) }, children: 'Retry' })
    })
  }

  const data = q.data
  const primary = (data && data.primary) || []
  const secondary = (data && data.secondary) || {}
  const counts = (data && data.counts) || {}
  const sourceHealth = (data && data.source_health) || {}

  const primaryDecision = primary.filter(it => primaryGroup(it.attention_class) === 'decision')
  const primaryInput = primary.filter(it => primaryGroup(it.attention_class) === 'input')

  const af = secondary.agent_fixable || []
  const dw = secondary.dependency_wait || []
  const info = secondary.informational || []

  const allItems = [...primary, ...af, ...dw, ...info]

  const visiblePrimary = primary.filter(it => !isSuppressed(it.key, it.fingerprint || ''))
  const anyContent = visiblePrimary.length > 0 || af.length > 0 || dw.length > 0 || info.length > 0

  return jsxs('div', { className: 'flex h-full flex-col', children: [
    // Header
    jsxs('div', { className: 'flex items-center gap-2 border-b border-(--ui-stroke-tertiary) px-4 py-3', children: [
      jsx(Codicon, { name: 'inbox', size: '1.1rem' }),
      jsx('div', { className: 'text-sm font-semibold', children: 'Approval Inbox' }),
      jsx('div', { className: 'text-xs text-(--ui-text-tertiary)', children: 'what needs your attention' }),
      jsx('div', { className: 'ml-auto' }),
      jsx(Button, { size: 'sm', variant: 'ghost', onClick: () => { haptic('tap'); void queryClient.invalidateQueries({ queryKey: [ID] }) }, children: 'Refresh' })
    ] }),
    jsx(SourceHealthBanner, { sourceHealth }),
    jsx(ScrollArea, { className: 'flex-1', children: jsxs('div', { className: 'pb-6', children: [
      // ---- Primary ----------------------------------------------------------
      primaryDecision.length > 0 ? jsxs('section', { children: [
        jsxs('div', { className: 'ai-section-title', children: [
          jsx(Codicon, { name: 'request-changes', size: '0.85rem' }),
          jsx('span', { children: 'Needs your decision' }),
          jsx(Badge, { variant: 'warn', className: 'ai-section-count', children: String(primaryDecision.length) })
        ] }),
        jsx('div', { children: primaryDecision.map(item => jsx(AttentionCard, { key: item.key, item })) })
      ] }) : null,
      primaryInput.length > 0 ? jsxs('section', { children: [
        jsxs('div', { className: 'ai-section-title', children: [
          jsx(Codicon, { name: 'edit', size: '0.85rem' }),
          jsx('span', { children: 'Needs something from you' }),
          jsx(Badge, { variant: 'warn', className: 'ai-section-count', children: String(primaryInput.length) })
        ] }),
        jsx('div', { children: primaryInput.map(item => jsx(AttentionCard, { key: item.key, item })) })
      ] }) : null,
      // ---- Empty primary state: don't claim all sources are healthy ---------
      visiblePrimary.length === 0 && af.length === 0 && dw.length === 0 && info.length === 0
        ? jsx(EmptyState, {
            title: 'Nothing waiting',
            description: Object.values(sourceHealth).some(v => v && !v.ok)
              ? 'Some sources are unavailable — data may be incomplete.'
              : 'No items require your attention right now.'
          })
        : null,
      // ---- Secondary (collapsible) -----------------------------------------
      af.length > 0 ? jsx(CollapsibleSection, {
        title: 'Agent can handle', icon: 'robot', count: af.length, defaultOpen: false,
        children: jsx('div', { children: af.map(item => jsx(SecondaryRow, { key: item.key, item })) })
      }) : null,
      dw.length > 0 ? jsx(CollapsibleSection, {
        title: 'Waiting on system', icon: 'sync', count: dw.length, defaultOpen: false,
        children: jsx('div', { children: dw.map(item => jsx(SecondaryRow, { key: item.key, item })) })
      }) : null,
      info.length > 0 ? jsx(CollapsibleSection, {
        title: 'Watching', icon: 'eye', count: info.length, defaultOpen: false,
        children: jsx('div', { children: info.map(item => jsx(SecondaryRow, { key: item.key, item })) })
      }) : null,
      // ---- Suppressed strip -------------------------------------------------
      jsx(SuppressedStrip, { allItems })
    ] }) })
  ] })
}

// ---------------------------------------------------------------------------
// Statusbar chip — human_now only; tooltip shows secondary + source health
// ---------------------------------------------------------------------------

function InboxChip() {
  useViewVersion()
  const q = useAttention()

  const data = q.data
  if (!data || !data.counts) return null

  const humanNow = data.counts.human_now || 0
  // Filter out suppressed items from the visible count
  const primary = data.primary || []
  const live = primary.filter(it => !isSuppressed(it.key, it.fingerprint || '')).length
  if (live === 0) return null

  const af = data.counts.agent_fixable || 0
  const dw = data.counts.dependency_wait || 0
  const info = data.counts.informational || 0
  const sourceHealth = data.source_health || {}
  const unhealthy = Object.entries(sourceHealth).filter(([, v]) => v && !v.ok).map(([k]) => k)
  const healthWarn = unhealthy.length > 0 ? ` ⚠ ${unhealthy.join(', ')}` : ''

  const tipText = `${live} needs you · ${af} agent · ${dw} waiting · ${info} watching${healthWarn}`

  return jsx(Tip, {
    label: 'Approval Inbox — ' + tipText,
    children: jsx('button', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
      ),
      type: 'button',
      onClick: () => { haptic('tap'); host.navigate('/inbox') },
      children: [
        jsx('span', { className: 'ai-chip-dot' }),
        jsx('span', { children: String(live) })
      ]
    })
  })
}

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

export default {
  id: ID,
  name: 'Approval Inbox',
  description: '\u201cWhat needs your attention\u201d — ranked decision and input gates from action items, kanban, cron, and TRT sources.',
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    storage = ctx.storage

    const saved = ctx.storage.get(STORAGE_KEY)
    viewState = saved && typeof saved === 'object' ? saved : {}

    ensurePluginStyles()

    ctx.i18n.register({
      en: {
        pageTitle: 'Approval Inbox',
        chipTip: 'Approval Inbox'
      }
    })

    const openInbox = () => {
      haptic('tap')
      host.navigate('/inbox')
    }

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/inbox' },
        title: 'Approval Inbox',
        render: () => jsx(InboxPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 55,
        data: { path: '/inbox', label: 'Inbox', codicon: 'inbox' }
      },
      {
        id: 'chip',
        area: STATUSBAR_AREAS.right,
        order: 115,
        render: () => jsx(InboxChip, {})
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'approval-inbox.open',
          label: 'Open Approval Inbox',
          keywords: ['inbox', 'approval', 'action', 'blocked', 'cron', 'waiting', 'todo', 'attention'],
          run: openInbox
        }
      }
    ])
  }
}
