/**
 * ESM registration smoke test for the approval-inbox runtime plugin.
 *
 * Loads the REAL plugin.js as an actual ESM module with a temporary Node
 * loader that maps @hermes/plugin-sdk, react, and react/jsx-runtime to local
 * stubs, then invokes register(ctx) with a stub context and asserts:
 *   - register() completes without throwing
 *   - the four expected contributions register (page /inbox, sidebar nav,
 *     statusbar chip, palette command)
 *   - storage is read synchronously (no .then usage)
 *   - the raw source passes the loader's import-scan (only SDK/react)
 *
 * Run: node tests/esm-registration.mjs
 */

import { readFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const STUBS_DIR = join(__dirname, '.stubs')
// Resolve the plugin relative to this repo layout (tests/ sits next to plugin.js).
const PLUGIN_PATH = join(__dirname, '..', 'plugin.js')

// ---------------------------------------------------------------------------
// SDK stub — mirrors the exports plugin.js imports
// ---------------------------------------------------------------------------

const sdkStub = {
  Badge: 'Badge',
  Button: 'Button',
  cn: (...args) => args.filter(Boolean).join(' '),
  Codicon: 'Codicon',
  EmptyState: 'EmptyState',
  ErrorState: 'ErrorState',
  haptic: () => {},
  host: {
    navigate: () => {},
    notify: () => {},
    state: {}
  },
  Loader: 'Loader',
  PALETTE_AREA: 'palette',
  queryClient: { invalidateQueries: () => {}, getQueryCache: () => ({ findAll: () => [] }) },
  relativeTime: () => 'ago',
  ROUTES_AREA: 'routes',
  ScrollArea: 'ScrollArea',
  SIDEBAR_NAV_AREA: 'sidebar.nav',
  STATUSBAR_AREAS: { left: 'statusBar.left', right: 'statusBar.right' },
  Tip: 'Tip',
  useQuery: () => ({ data: undefined, isLoading: true, isError: false })
}

const reactStub = {
  useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  useMemo: fn => (typeof fn === 'function' ? fn() : fn),
  createElement: (type, props, ...children) => ({ type, props, children })
}

const jsxRuntimeStub = {
  jsx: (type, props) => ({ type, props: props || {} }),
  jsxs: (type, props) => ({ type, props: props || {} }),
  Fragment: 'Fragment'
}

// ---------------------------------------------------------------------------
// Write stub module files FIRST (loader must resolve to existing files)
// ---------------------------------------------------------------------------

mkdirSync(STUBS_DIR, { recursive: true })

const sdkSrc =
  `const sdk = ${JSON.stringify(sdkStub, null, 2)}\n` +
  'export default sdk\n' +
  Object.keys(sdkStub).map(k => `export const ${k} = sdk.${k}`).join('\n') +
  '\n'
writeFileSync(join(STUBS_DIR, 'sdk.mjs'), sdkSrc)

writeFileSync(
  join(STUBS_DIR, 'react.mjs'),
  `export const useState = ${reactStub.useState.toString()}\n` +
    `export const useEffect = ${reactStub.useEffect.toString()}\n` +
    `export const useMemo = ${reactStub.useMemo.toString()}\n` +
    'export default { useState, useEffect, useMemo }\n'
)

writeFileSync(
  join(STUBS_DIR, 'jsx-runtime.mjs'),
  `export const jsx = ${jsxRuntimeStub.jsx.toString()}\n` +
    `export const jsxs = ${jsxRuntimeStub.jsxs.toString()}\n` +
    "export const Fragment = 'Fragment'\n"
)

const stubUrls = {
  '@hermes/plugin-sdk': pathToFileURL(join(STUBS_DIR, 'sdk.mjs')).href,
  react: pathToFileURL(join(STUBS_DIR, 'react.mjs')).href,
  'react/jsx-runtime': pathToFileURL(join(STUBS_DIR, 'jsx-runtime.mjs')).href
}

writeFileSync(
  join(__dirname, 'loader-hooks.mjs'),
  `const STUB_URLS = ${JSON.stringify(stubUrls, null, 2)}

export function resolve(specifier, context, nextResolve) {
  if (STUB_URLS[specifier]) {
    return { url: STUB_URLS[specifier], shortCircuit: true }
  }
  return nextResolve(specifier, context)
}
`
)

// ---------------------------------------------------------------------------
// Register the loader, then import the plugin through it
// ---------------------------------------------------------------------------

import { register } from 'node:module'

register(new URL('./loader-hooks.mjs', import.meta.url))

// ---------------------------------------------------------------------------
// Test body
// ---------------------------------------------------------------------------

let failures = 0
const check = (cond, msg) => {
  if (!cond) {
    failures += 1
    console.error('FAIL: ' + msg)
  }
}

// 1) import-scan regex check on raw source (the hermes-achievements bug)
const src = readFileSync(PLUGIN_PATH, 'utf8')
const badImports = src.match(/(from|import)[\s]*\(?[\s]*['"][^'"]+['"]/g) || []
const allowed = new Set(["from '@hermes/plugin-sdk'", "from 'react'", "from 'react/jsx-runtime'"])
const illegal = badImports.map(m => m.trim()).filter(m => !allowed.has(m))
check(illegal.length === 0, 'loader import-scan found illegal specifiers: ' + JSON.stringify(illegal))

// 2) syntax + import (import throws on syntax error)
let plugin
try {
  plugin = (await import(pathToFileURL(PLUGIN_PATH).href + '?t=' + Date.now())).default
  check(true, 'plugin import')
} catch (err) {
  check(false, 'plugin failed to import: ' + err.message)
  process.exit(process.exitCode || 1)
}

check(plugin.id === 'approval-inbox', 'plugin id is approval-inbox')
check(typeof plugin.register === 'function', 'register is a function')

// 3) register() with stub ctx
const contributions = []
const storageStore = {}
const ctx = {
  rest: path => Promise.resolve({ count: 0, items: [], error: null }),
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

try {
  plugin.register(ctx)
  check(true, 'register() completed')
} catch (err) {
  check(false, 'register() threw: ' + (err && err.stack ? err.stack : err))
}

// 4) contributions
check(contributions.length === 4, 'expected 4 contributions, got ' + contributions.length)
const areas = contributions.map(c => c.area)
check(areas.includes('routes'), 'missing ROUTES_AREA page contribution')
check(areas.includes('sidebar.nav'), 'missing SIDEBAR_NAV_AREA contribution')
check(areas.includes('statusBar.right'), 'missing statusbar chip contribution')
check(areas.includes('palette'), 'missing PALETTE_AREA contribution')

const page = contributions.find(c => c.area === 'routes')
check(page && page.data && page.data.path === '/inbox', 'page path is /inbox')
check(page && typeof page.render === 'function', 'page render is a function')

const nav = contributions.find(c => c.area === 'sidebar.nav')
check(nav && nav.data && nav.data.path === '/inbox', 'nav path is /inbox')
check(nav && nav.data && nav.data.label === 'Inbox', 'nav label is Inbox')

const chip = contributions.find(c => c.area === 'statusBar.right')
check(chip && typeof chip.render === 'function', 'chip render is a function')

const palette = contributions.find(c => c.area === 'palette')
check(palette && palette.data && palette.data.id === 'approval-inbox.open', 'palette command id')

// 5) render functions don't blow up with the stub hooks (page-level smoke)
try {
  const rendered = page.render()
  check(rendered && typeof rendered === 'object', 'page render returned a vdom node')
} catch (err) {
  check(false, 'page render threw: ' + (err && err.stack ? err.stack : err))
}

try {
  const chipNode = chip.render()
  check(chipNode && typeof chipNode === 'object', 'chip render returned a vdom node')
} catch (err) {
  check(false, 'chip render threw: ' + (err && err.stack ? err.stack : err))
}

console.log(
  failures === 0
    ? 'PASS: approval-inbox ESM registration smoke (' + contributions.length + ' contributions)'
    : 'FAILURES: ' + failures
)
process.exit(failures === 0 ? 0 : 1)
