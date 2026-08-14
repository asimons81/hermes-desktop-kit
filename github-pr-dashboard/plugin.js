/**
 * GitHub Pull Requests — Hermes desktop plugin.
 *
 * Account-wide pull requests dashboard: Created / Review requested / Closed
 * views backed by the user's authenticated `gh` CLI. Read-only.
 *
 * Backend: ~/.hermes/plugins/github-pr-dashboard/dashboard/plugin_api.py
 * (mounted at /api/plugins/github-pr-dashboard/ — enabled via plugins.enabled
 * in config.yaml). This file is plain ESM loaded uncompiled: UI is jsx()
 * calls, NOT JSX syntax; only @hermes/plugin-sdk, react, react/jsx-runtime
 * resolve.
 */

import {
  Button,
  cn,
  Codicon,
  CopyButton,
  EmptyState,
  haptic,
  host,
  Loader,
  PALETTE_AREA,
  relativeTime,
  ROUTES_AREA,
  SearchField,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  usePluginI18n,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState } from 'react'

const ID = 'github-pr-dashboard'
const TABS = ['created', 'review-requested', 'closed']

// Assigned in register(ctx) — components can't see ctx directly.
let rest

// IMPORTANT: the app's compiled CSS bundle is built from core sources only.
// Tailwind classes used by runtime plugins (like md:grid-cols-[minmax(20rem,42%)_minmax(0,1fr)])
// are NOT generated — the bundle can't know about plugin files. Relying on them
// silently collapses the layout (list on top, detail below). Inject scoped CSS
// for anything the plugin needs beyond core classes.
function ensurePluginStyles() {
  if (typeof document === 'undefined') return
  if (document.getElementById('gprd-styles')) return
  const style = document.createElement('style')
  style.id = 'gprd-styles'
  style.textContent = [
    '@media (min-width: 768px) {',
    '  .gprd-split { grid-template-columns: minmax(20rem, 42%) minmax(0, 1fr); }',
    '}',
    '.gprd-row[aria-selected="true"] { background: var(--ui-bg-quaternary); }',
    '.gprd-label { max-width: 6rem; }',
    '.gprd-stats { column-gap: 1.25rem; }',
    '.gprd-wrap { overflow-wrap: anywhere; }'
  ].join('\n')
  document.head.appendChild(style)
}

function filterFor(tab) {
  return tab === 'review-requested'
    ? { kind: 'review-requested', state: 'open', limit: 100 }
    : { kind: 'created', state: tab === 'closed' ? 'closed' : 'open', limit: 100 }
}

function relativeTimeOrDash(iso) {
  if (!iso) return '—'
  const ts = new Date(iso).getTime()
  if (!Number.isFinite(ts)) return '—'
  return relativeTime(ts)
}

function openExternal(url) {
  const bridge = typeof window !== 'undefined' ? window.hermesDesktop : null
  if (bridge && typeof bridge.openExternal === 'function') {
    void bridge.openExternal(url)
  } else {
    window.open(url, '_blank', 'noopener')
  }
}

// ── List ────────────────────────────────────────────────────────────────────

function PullRequestList({ items, selected, onSelect, onOpen }) {
  return jsx('div', {
    className: 'h-full overflow-y-auto',
    role: 'listbox',
    children: items.map(item => {
      const status = item.isDraft ? 'DRAFT' : item.state
      const labels = item.labels || []
      const visibleLabels = labels.slice(0, 3)
      const extraLabels = labels.length - visibleLabels.length

      return jsxs('div', {
        className: cn(
          'gprd-row group flex w-full min-w-0 cursor-pointer items-start gap-3 px-4 py-3 text-left',
          'hover:bg-(--chrome-action-hover)'
        ),
        'aria-selected': selected === item.id,
        key: item.id,
        onClick: () => onSelect(item),
        onKeyDown: e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onSelect(item)
          }
        },
        role: 'option',
        tabIndex: 0,
        children: [
          jsx(Codicon, { className: 'mt-0.5 shrink-0 text-(--ui-text-tertiary)', name: 'git-pull-request' }),
          jsxs('span', {
            className: 'min-w-0 flex-1',
            children: [
              jsxs('span', {
                className: 'flex min-w-0 items-center gap-2 text-xs text-(--ui-text-secondary)',
                children: [
                  jsx('span', { className: 'truncate', children: item.repository }),
                  jsx('span', { className: 'shrink-0', children: `#${item.number}` }),
                  jsx('span', { className: 'shrink-0 text-[10px] font-medium', children: status })
                ]
              }),
              jsx('span', {
                className: 'mt-1 block truncate text-sm font-medium text-(--ui-text-primary)',
                children: item.title
              }),
              jsxs('span', {
                className: 'mt-1 flex min-w-0 items-center gap-2 text-xs text-(--ui-text-tertiary)',
                children: [
                  item.author ? jsx('span', { className: 'truncate', children: `@${item.author.login}` }) : null,
                  ...visibleLabels.map(label => jsx('span', { className: 'gprd-label truncate', key: label.name, children: label.name })),
                  extraLabels > 0 ? jsx('span', { children: `+${extraLabels}` }) : null,
                  jsx('span', { className: 'ml-auto shrink-0', children: jsxs('span', { children: [jsx(Codicon, { name: 'comment' }), ' ', item.commentsCount] }) }),
                  jsx('span', { className: 'shrink-0', children: relativeTimeOrDash(item.updatedAt) })
                ]
              })
            ]
          }),
          jsx(Button, {
            'aria-label': `Open ${item.repository} pull request ${item.number} on GitHub`,
            onClick: event => {
              event.stopPropagation()
              onOpen(item.url)
            },
            size: 'icon-xs',
            variant: 'ghost',
            children: jsx(Codicon, { name: 'link-external' })
          })
        ]
      })
    })
  })
}

// ── Detail ──────────────────────────────────────────────────────────────────

function PullRequestDetail({ t, detail, loading, error, onRetry, onBack }) {
  if (loading) {
    return jsx('div', {
      className: 'grid h-full place-items-center',
      children: jsx(Loader, { label: t('loadingDetails') })
    })
  }

  if (error) {
    return jsxs('div', {
      className: 'grid h-full place-items-center text-center',
      children: [
        jsx(EmptyState, { title: t('detailFailed') }),
        jsx(Button, { onClick: onRetry, size: 'sm', variant: 'secondary', children: t('retry') })
      ]
    })
  }

  if (!detail) {
    return jsx(EmptyState, { title: t('title') })
  }

  const state = detail.isDraft
    ? t('draft')
    : detail.state === 'MERGED'
      ? t('merged')
      : detail.state === 'CLOSED'
        ? t('closed')
        : t('open')

  const stats = [
    { label: t('changedFiles'), value: String(detail.changedFiles) },
    { label: t('additions'), value: `+${detail.additions}` },
    { label: t('deletions'), value: `-${detail.deletions}` },
    { label: t('reviewDecision'), value: detail.reviewDecision ?? '—' },
    { label: 'Merge state', value: detail.mergeStateStatus ?? '—' },
    { label: t('checks'), value: `${detail.checks.passed}/${detail.checks.total} · ${detail.checks.failed} ${t('failed')}` }
  ]

  return jsx('article', {
    className: 'h-full overflow-y-auto px-5 py-4',
    children: [
      jsx(Button, {
        className: 'mb-3 md:hidden',
        onClick: onBack,
        size: 'sm',
        variant: 'ghost',
        children: [jsx(Codicon, { name: 'arrow-left' }), t('back')]
      }),
      jsx('div', { className: 'text-xs text-(--ui-text-secondary)', children: `${detail.repository} #${detail.number}` }),
      jsx('h1', { className: 'mt-1 text-xl font-semibold leading-tight text-(--ui-text-primary)', children: detail.title }),
      jsxs('div', {
        className: 'mt-2 flex flex-wrap items-center gap-2 text-xs text-(--ui-text-secondary)',
        children: [
          jsx('span', { children: state }),
          detail.author ? jsx('span', { children: `@${detail.author.login}` }) : null,
          ...(detail.labels || []).map(label => jsx('span', { key: label.name, children: label.name }))
        ]
      }),
      jsx('div', {
        className: 'mt-2 text-xs text-(--ui-text-tertiary)',
        children: `${t('updated')}: ${new Date(detail.updatedAt).toLocaleString()} · ${t('created')}: ${new Date(detail.createdAt).toLocaleString()}`
      }),
      jsxs('div', {
        className: 'mt-4 flex flex-wrap gap-2',
        children: [
          jsx(Button, {
            onClick: () => openExternal(detail.url),
            size: 'sm',
            variant: 'secondary',
            children: [jsx(Codicon, { name: 'link-external' }), t('openGithub')]
          }),
          jsx(CopyButton, { text: detail.url, buttonSize: 'sm', label: t('copyUrl'), showLabel: true })
        ]
      }),
      jsx('dl', {
        className: 'gprd-stats mt-5 grid grid-cols-2 gap-y-3 text-xs sm:grid-cols-3',
        children: stats.map(stat =>
          jsxs('div', {
            key: stat.label,
            children: [
              jsx('dt', { className: 'text-(--ui-text-tertiary)', children: stat.label }),
              jsx('dd', { className: 'mt-0.5 font-medium', children: stat.value })
            ]
          })
        )
      }),
      jsx('div', {
        className: 'gprd-wrap mt-5 text-xs text-(--ui-text-secondary)',
        children: jsxs('span', { children: [jsx(Codicon, { name: 'git-merge' }), ` ${detail.headRefName} → ${detail.baseRefName}`] })
      }),
      jsx('div', {
        className: 'mt-5 whitespace-pre-wrap break-words text-sm leading-6 text-(--ui-text-secondary)',
        children: detail.body || '—'
      })
    ]
  })
}

// ── Page ────────────────────────────────────────────────────────────────────

function PullRequestsPage() {
  const t = usePluginI18n(ID)
  const [tab, setTab] = useState('created')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState(null)

  const filter = filterFor(tab)
  const list = useQuery({
    queryKey: ['github-pr-dashboard', tab],
    queryFn: () => rest(`/list?kind=${filter.kind}&state=${filter.state}&limit=${filter.limit}`),
    staleTime: 30_000,
    retry: false
  })
  const detail = useQuery({
    queryKey: ['github-pr-dashboard-detail', selected?.repository, selected?.number],
    queryFn: () => rest(`/detail?repository=${encodeURIComponent(selected.repository)}&number=${selected.number}`),
    enabled: Boolean(selected),
    staleTime: 60_000,
    retry: false
  })

  const data = list.data || { authState: 'loading', items: [] }
  const items = data.items.filter(item => {
    if (!search) return true
    const q = search.toLowerCase()
    const haystack = [item.repository, item.title, String(item.number), item.author?.login || ''].join(' ').toLowerCase()
    return haystack.includes(q)
  })
  const emptyTitle =
    tab === 'created' ? t('noneCreated') : tab === 'review-requested' ? t('noneReview') : t('noneClosed')

  const changeTab = next => {
    setSelected(null)
    setSearch('')
    setTab(next)
  }

  let setup = null
  if (data.authState === 'gh-missing') setup = [t('ghMissing'), null]
  else if (data.authState === 'not-authenticated') setup = [t('authRequired'), t('authHint')]

  let body = null
  if (setup) {
    body = jsx(EmptyState, { className: 'h-full', description: setup[1], title: setup[0] })
  } else if (!list.data && list.isPending) {
    body = jsx('div', { className: 'grid h-full place-items-center', children: jsx(Loader, { label: t('loading') }) })
  } else if (data.authState === 'error' && !data.items.length) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center',
      children: [
        jsx(EmptyState, { description: data.error, title: t('loadFailed') }),
        jsx(Button, { onClick: () => void list.refetch(), size: 'sm', variant: 'secondary', children: t('retry') })
      ]
    })
  } else if (list.isError) {
    body = jsxs('div', {
      className: 'grid h-full place-items-center',
      children: [
        jsx(EmptyState, { description: list.error?.message || t('loadFailed'), title: t('loadFailed') }),
        jsx(Button, { onClick: () => void list.refetch(), size: 'sm', variant: 'secondary', children: t('retry') })
      ]
    })
  } else {
    body = jsxs('div', {
      className: 'gprd-split grid h-full min-w-0',
      children: [
        jsx('div', {
          className: cn(
            'min-w-0 overflow-hidden border-r border-(--ui-stroke-tertiary)',
            selected ? 'hidden md:block' : 'block'
          ),
          children: items.length
            ? jsx(PullRequestList, {
                items,
                onOpen: openExternal,
                onSelect: setSelected,
                selected: selected?.id
              })
            : jsx(EmptyState, { className: 'h-full', title: emptyTitle })
        }),
        jsx('div', {
          className: cn('min-w-0 overflow-hidden', selected ? 'block' : 'hidden md:block'),
          children: jsx(PullRequestDetail, {
            t,
            detail: detail.data,
            error: detail.isError,
            loading: detail.isPending && Boolean(selected),
            onBack: () => setSelected(null),
            onRetry: () => void detail.refetch()
          })
        })
      ]
    })
  }

  const tabs = TABS.map(value => ({
    id: value,
    label: value === 'created' ? t('created') : value === 'review-requested' ? t('reviewRequested') : t('closed')
  }))

  return jsxs('div', {
    className: 'flex h-full flex-col',
    children: [
      jsxs('div', {
        className: 'flex items-center gap-3 border-b border-(--ui-stroke-secondary) px-4 py-2',
        children: [
          jsx(SegmentedControl, { value: tab, onChange: changeTab, options: tabs }),
          jsx('div', { className: 'min-w-0 flex-1' }),
          jsx(SearchField, {
            placeholder: t('search'),
            value: search,
            onChange: setSearch,
            loading: list.isFetching
          }),
          jsx(Button, {
            'aria-label': t('refresh'),
            disabled: list.isFetching,
            onClick: () => void list.refetch(),
            size: 'icon-sm',
            variant: 'ghost',
            children: jsx(Codicon, { className: list.isFetching ? 'animate-spin' : '', name: 'refresh' })
          })
        ]
      }),
      jsx('div', { className: 'min-h-0 flex-1', children: body })
    ]
  })
}

// ── Plugin export ───────────────────────────────────────────────────────────

export default {
  id: ID,
  name: 'GitHub Pull Requests',
  description: 'Account-wide pull requests dashboard (created / review-requested / closed) backed by the gh CLI. Read-only.',
  defaultEnabled: true,
  register(ctx) {
    rest = ctx.rest
    ensurePluginStyles()

    ctx.i18n.register({
      en: {
        title: 'Pull Requests',
        created: 'Created',
        reviewRequested: 'Review requested',
        closed: 'Closed',
        search: 'Search pull requests…',
        refresh: 'Refresh',
        loading: 'Loading pull requests…',
        loadingDetails: 'Loading details…',
        loadFailed: 'Could not load pull requests',
        detailFailed: 'Could not load PR details',
        retry: 'Retry',
        ghMissing: 'GitHub CLI not found',
        authRequired: 'Sign in with GitHub CLI',
        authHint: 'Run `gh auth login` in a terminal, then refresh.',
        noneCreated: 'No open pull requests by you',
        noneReview: 'No pull requests waiting on your review',
        noneClosed: 'No recently closed pull requests',
        open: 'Open',
        draft: 'Draft',
        merged: 'Merged',
        back: 'Back',
        updated: 'Updated',
        createdLabel: 'Created',
        openGithub: 'Open on GitHub',
        copyUrl: 'Copy URL',
        changedFiles: 'Changed files',
        additions: 'Additions',
        deletions: 'Deletions',
        reviewDecision: 'Review',
        checks: 'Checks',
        failed: 'failed'
      }
    })

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/pull-requests' },
        title: 'Pull Requests',
        render: () => jsx(PullRequestsPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/pull-requests', label: 'Pull Requests', codicon: 'git-pull-request' }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: 'github-pr-dashboard.open',
          label: 'Open Pull Requests',
          keywords: ['pull', 'requests', 'pr', 'github'],
          run: () => {
            haptic('tap')
            host.navigate('/pull-requests')
          }
        }
      }
    ])
  }
}
