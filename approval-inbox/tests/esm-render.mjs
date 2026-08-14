/**
 * SSR render test for V2 approval-inbox — attention envelope shape.
 *
 * Mounts InboxPage with react-dom/server, stubs the SDK, feeds a V2 /attention
 * envelope fixture, and asserts: primary section titles, card fields,
 * secondary collapsed sections, source-health banner, absence of "Ack".
 *
 * Run: node tests/esm-render.mjs
 */

import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const STUBS_DIR = join(__dirname, '.stubs')
// Resolve the plugin relative to this repo layout (tests/ sits next to plugin.js).
const PLUGIN_PATH = join(__dirname, '..', 'plugin.js')
// React/react-dom live in the Hermes desktop app's node_modules. Point
// HERMES_AGENT_NODE_MODULES at them, or default to a sibling node_modules.
const REPO = process.env.HERMES_AGENT_NODE_MODULES || join(__dirname, '..', '..', 'node_modules')

// ---------------------------------------------------------------------------
// V2 attention envelope fixture
// ---------------------------------------------------------------------------

const FIXTURE_ATTENTION = {
  generated_at: '2026-08-10T12:00:00+00:00',
  verified_at: '2026-08-10T12:00:00+00:00',
  counts: { human_now: 4, agent_fixable: 3, dependency_wait: 4, informational: 5, suppressed_invalid: 20 },
  primary: [
    {
      key: 'att:ledger:cron-newsletter-analytics-drift-pin',
      source_keys: ['action:newsletter-analytics-drift-pin', 'ledger:cron-newsletter-analytics-drift-pin', 'cron:52d9a0d36bfc'],
      attention_class: 'approval',
      actionability: 'human_now',
      owner: 'tony',
      authority: 'Approve cronjob action=update job_id=52d9a0d36bfc',
      title: 'Newsletter Analytics drift pin',
      why_tony: 'Explicit human-gate approval required (authority=human_gate)',
      reason_now: 'DUE 2026-08-10T09:00:00',
      recommended_action: 'Approve the model/provider pin',
      alternatives: [],
      consequence_of_delay: 'Gate stays blocked; the requested outbound action remains unperformed',
      project: 'hermes-cron-ops',
      severity: 'urgent',
      confidence: 'high',
      verification: { verified_at: '2026-08-10T12:00:00+00:00', status: 'verified', evidence: ['path/to/evidence'] },
      created_at: '2026-08-09T09:00:00+00:00',
      updated_at: '2026-08-10T09:00:00+00:00',
      source_health: [],
      fingerprint: 'abc123',
      view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
      suppression_reason: null
    },
    {
      key: 'att:kanban:github-health:t_ee5395ee',
      source_keys: ['kanban:github-health:t_ee5395ee', 'kanban:github-health:t_c0fce74f'],
      attention_class: 'approval',
      actionability: 'human_now',
      owner: 'tony',
      authority: 'merge stays with coordinator/Tony — do not auto-merge',
      title: 'hermes-vault PR #77 merge gate',
      why_tony: 'merge stays with coordinator/Tony — do not auto-merge',
      reason_now: 'Blocked gate',
      recommended_action: 'merge stays with coordinator/Tony — do not auto-merge',
      alternatives: [],
      consequence_of_delay: 'Gate stays blocked; the requested outbound action remains unperformed',
      project: 'github-health',
      severity: 'high',
      confidence: 'high',
      verification: { verified_at: '2026-08-10T12:00:00+00:00', status: 'verified', evidence: ['kanban event reason (github-health:t_ee5395ee)'] },
      created_at: '2026-08-08T12:00:00+00:00',
      updated_at: '2026-08-09T12:00:00+00:00',
      source_health: [],
      fingerprint: 'def456',
      view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
      suppression_reason: null
    },
    {
      key: 'att:action:rabbit-r1-featured-image',
      source_keys: ['action:rabbit-r1-featured-image', 'trt:18517'],
      attention_class: 'input_required',
      actionability: 'human_now',
      owner: 'tony',
      authority: 'Gate BLOCKED missing_source_pack for WP post 18517',
      title: 'Rabbit R1 featured image',
      why_tony: 'Tony-owned asset/input required (missing_source_pack)',
      reason_now: 'Gate BLOCKED missing_source_pack',
      recommended_action: 'Supply the featured image for draft 18517',
      alternatives: [],
      consequence_of_delay: 'Gate stays BLOCKED; publication/delivery is delayed until the input is supplied',
      project: 'trt',
      severity: 'high',
      confidence: 'high',
      verification: { verified_at: '2026-08-10T12:00:00+00:00', status: 'verified', evidence: ['path/to/receipt'] },
      created_at: '2026-08-05T00:00:00+00:00',
      updated_at: '2026-08-10T12:00:00+00:00',
      source_health: [],
      fingerprint: 'ghi789',
      view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
      suppression_reason: null
    },
    {
      key: 'att:action:openai-education-plugins-dup',
      source_keys: ['action:trt-t235e7ab9-dup-decision', 'trt:marker:openai-education-plugins-2026'],
      attention_class: 'decision',
      actionability: 'human_now',
      owner: 'tony',
      authority: 'Tony decision: duplicate openai-education-plugins-2026 draft',
      title: 'openai-education-plugins duplicate decision',
      why_tony: 'Tony decision',
      reason_now: 'Explicit Tony action item open',
      recommended_action: 'Decide fate of the duplicate draft',
      alternatives: [],
      consequence_of_delay: 'Decision deferred; duplicate/stale state persists until resolved',
      project: 'trt',
      severity: 'normal',
      confidence: 'high',
      verification: { verified_at: '2026-08-10T12:00:00+00:00', status: 'verified', evidence: [] },
      created_at: '2026-08-07T00:00:00+00:00',
      updated_at: null,
      source_health: [],
      fingerprint: 'jkl000',
      view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
      suppression_reason: null
    }
  ],
  secondary: {
    agent_fixable: [
      {
        key: 'att:cron:7e726b05dac3',
        source_keys: ['cron:7e726b05dac3'],
        attention_class: 'watching',
        actionability: 'agent_fixable',
        owner: 'default',
        title: 'Cron HTTP 503 upstream',
        project: null,
        created_at: '2026-08-09T00:00:00+00:00',
        severity: 'low',
        confidence: 'low',
        verification: { status: 'unverified', evidence: [] },
        fingerprint: 'xxx',
        view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
        suppression_reason: null
      }
    ],
    dependency_wait: [
      {
        key: 'att:kanban:approval-inbox-v2:t_70f7c0f2',
        source_keys: ['kanban:approval-inbox-v2:t_70f7c0f2'],
        attention_class: 'watching',
        actionability: 'dependency_wait',
        owner: 'hermes-dev',
        title: 'Card C — Backend model and source adapters',
        project: 'approval-inbox-v2',
        created_at: '2026-08-10T00:00:00+00:00',
        severity: 'low',
        confidence: 'low',
        verification: { status: 'unverified', evidence: [] },
        fingerprint: 'yyy',
        view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
        suppression_reason: 'dependency_gated'
      }
    ],
    informational: [
      {
        key: 'att:trt:marker:eu-ai-act-enforcement-live',
        source_keys: ['trt:marker:eu-ai-act-enforcement-live'],
        attention_class: 'watching',
        actionability: 'informational',
        owner: 'default',
        title: 'EU AI Act Enforcement Live',
        project: null,
        created_at: '2026-08-01T00:00:00+00:00',
        severity: 'low',
        confidence: 'low',
        verification: { status: 'unverified', evidence: [] },
        fingerprint: 'zzz',
        view_state: { snoozed_until: null, hidden: false, hidden_reason: null, fingerprint_at_hide: null },
        suppression_reason: 'marker_only'
      }
    ]
  },
  source_health: {
    action_items: { ok: true, error: null },
    kanban: { ok: true, error: null },
    cron: { ok: false, error: 'cron executions db does not exist' },
    trt: { ok: true, error: null }
  },
  suppressed: []
}

// ---------------------------------------------------------------------------
// SDK stub — useQuery returns the fixture for /attention
// ---------------------------------------------------------------------------

const FIXTURE_ATTENTION_JSON = JSON.stringify(FIXTURE_ATTENTION)

const USE_QUERY_SRC = `opts => {
  const path = opts && opts.queryKey ? opts.queryKey[1] || '' : ''
  const FIXTURE = ${FIXTURE_ATTENTION_JSON}
  if (path === 'attention') return { data: FIXTURE, isLoading: false, isError: false, error: null }
  return { data: null, isLoading: false, isError: false, error: null }
}`

const sdkStub = {
  Badge: 'Badge',
  Button: 'Button',
  cn: (...args) => args.filter(Boolean).join(' '),
  Codicon: 'Codicon',
  EmptyState: 'EmptyState',
  ErrorState: 'ErrorState',
  haptic: () => {},
  host: { navigate: () => {}, notify: () => {}, state: {} },
  Loader: 'Loader',
  PALETTE_AREA: 'palette',
  queryClient: { invalidateQueries: () => {}, getQueryCache: () => ({ findAll: () => [] }) },
  relativeTime: () => 'ago',
  ROUTES_AREA: 'routes',
  ScrollArea: 'ScrollArea',
  SIDEBAR_NAV_AREA: 'sidebar.nav',
  STATUSBAR_AREAS: { left: 'statusBar.left', right: 'statusBar.right' },
  Tip: 'Tip',
  useQuery: USE_QUERY_SRC
}

// ---------------------------------------------------------------------------
// Write stubs
// ---------------------------------------------------------------------------

mkdirSync(STUBS_DIR, { recursive: true })

function serializeStub(obj) {
  return Object.entries(obj)
    .map(([k, v]) => {
      if (typeof v === 'function') return `export const ${k} = ${v.toString()}`
      if (typeof v === 'string' && /^(opts|\(|function|async)/.test(v.trim())) return `export const ${k} = ${v}`
      return `export const ${k} = ${JSON.stringify(v)}`
    })
    .join('\n')
}

const sdkSrc = serializeStub(sdkStub) + '\n'
writeFileSync(join(STUBS_DIR, 'render-sdk.mjs'), sdkSrc)

writeFileSync(
  join(STUBS_DIR, 'render-react.mjs'),
  `export * from ${JSON.stringify(pathToFileURL(join(REPO, 'react', 'index.js')).href)}\n`
)
writeFileSync(
  join(STUBS_DIR, 'render-jsx-runtime.mjs'),
  `export * from ${JSON.stringify(pathToFileURL(join(REPO, 'react', 'jsx-runtime.js')).href)}\n`
)

const stubUrls = {
  '@hermes/plugin-sdk': pathToFileURL(join(STUBS_DIR, 'render-sdk.mjs')).href,
  react: pathToFileURL(join(STUBS_DIR, 'render-react.mjs')).href,
  'react/jsx-runtime': pathToFileURL(join(STUBS_DIR, 'render-jsx-runtime.mjs')).href
}

writeFileSync(
  join(__dirname, 'render-loader-hooks.mjs'),
  `const STUB_URLS = ${JSON.stringify(stubUrls, null, 2)}\n\n` +
    'export function resolve(specifier, context, nextResolve) {\n' +
    '  if (STUB_URLS[specifier]) {\n' +
    '    return { url: STUB_URLS[specifier], shortCircuit: true }\n' +
    '  }\n' +
    '  return nextResolve(specifier, context)\n' +
    '}\n'
)

import { register } from 'node:module'
register(new URL('./render-loader-hooks.mjs', import.meta.url))

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

let failures = 0
const check = (cond, msg) => {
  if (!cond) { failures += 1; console.error('FAIL: ' + msg) }
}

let plugin
try {
  plugin = (await import(pathToFileURL(PLUGIN_PATH).href + '?t=' + Date.now())).default
} catch (err) {
  check(false, 'plugin import failed: ' + (err && err.stack ? err.stack : err))
  process.exit(1)
}

const contributions = []
const storageStore = {}
const ctx = {
  rest: path => {
    if (path === 'attention') return Promise.resolve(FIXTURE_ATTENTION)
    return Promise.resolve({ count: 0, items: [], error: null })
  },
  socket: () => () => {},
  storage: {
    get: key => (key in storageStore ? storageStore[key] : undefined),
    set: (key, value) => { storageStore[key] = value }
  },
  i18n: { register: () => {} },
  onDispose: () => {},
  register: c => contributions.push(c),
  registerMany: cs => contributions.push(...cs)
}
plugin.register(ctx)

const page = contributions.find(c => c.area === 'routes')
check(!!page, 'page contribution exists')

const React = (await import(pathToFileURL(join(REPO, 'react', 'index.js')).href)).default
const { renderToString } = await import(pathToFileURL(join(REPO, 'react-dom', 'server.browser.js')).href)

let html = ''
try {
  html = renderToString(React.createElement(page.render))
  check(html.length > 100, 'rendered html is non-trivial (len=' + html.length + ')')
} catch (err) {
  check(false, 'renderToString threw: ' + (err && err.stack ? err.stack : err))
}

const expectIn = (needle, label) => check(html.includes(needle), 'html contains ' + label + ' (' + needle + ')')

// V2 page structure
expectIn('Approval Inbox', 'page title')
expectIn('what needs your attention', 'subtitle')

// Primary decision group
expectIn('Needs your decision', 'decision section title')
expectIn('Newsletter Analytics drift pin', 'drift pin card title')
expectIn('hermes-vault PR #77 merge gate', 'PR #77 card title')
expectIn('openai-education-plugins duplicate decision', 'dup decision card title')

// Primary input group
expectIn('Needs something from you', 'input section title')
expectIn('Rabbit R1 featured image', 'R1 card title')

// Card fields
expectIn('Why you', 'why_tony label')
expectIn('Why now', 'reason_now label')
expectIn('Recommend', 'recommended_action label')
expectIn('If delayed', 'consequence label')
expectIn('Action', 'authority field label')
expectIn('urgent', 'severity badge')
expectIn('high', 'high severity badge')
expectIn('normal', 'normal severity badge')

// Secondary collapsible sections (collapsed by default)
expectIn('Agent can handle', 'agent_fixable section title')
expectIn('Waiting on system', 'dependency_wait section title')
expectIn('Watching', 'informational section title')

// Source health banner (cron is unhealthy)
expectIn('Source issue', 'health banner')

// Actions: Snooze / Hide, NOT Ack
expectIn('Snooze until', 'snooze button')
expectIn('Hide from this view', 'hide button')
check(!html.includes('Ack>') && !html.includes('Ack<'), 'Ack button absent')

// V2 does NOT have old V1 section names
check(!html.includes('Action Items>'), 'no V1 Action Items section')
check(!html.includes('Kanban Blocked'), 'no V1 Kanban Blocked section')
check(!html.includes('Failed Cron Runs'), 'no V1 Failed Cron Runs section')
check(!html.includes('TRT Blocked Drafts'), 'no V1 TRT Blocked Drafts section')
check(!html.includes('Acknowledged'), 'no V1 Ack strip')

console.log(failures === 0 ? 'PASS: approval-inbox V2 SSR render (' + html.length + ' chars)' : 'FAILURES: ' + failures)
process.exit(failures === 0 ? 0 : 1)
