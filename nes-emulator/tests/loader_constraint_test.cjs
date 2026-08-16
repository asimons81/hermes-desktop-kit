// L1/L3: replicate the desktop loader's import-constraint check against the
// ASSEMBLED plugin.js (not the parts). Assert only @hermes/plugin-sdk + react*
// are imported and a default export exists, then verify the vendored jsnes UMD
// attaches to globalThis.jsnes when evaluated in a bare realm (no
// exports/module/define). The jsnes blob is the section BEFORE the body's
// first import statement, so we evaluate exactly that prefix.
const fs = require('fs')
const path = require('path')

const file = path.join(__dirname, '..', 'plugin.js')
const src = fs.readFileSync(file, 'utf8')

// same regex as apps/desktop/src/contrib/runtime-loader.ts importSpecifierRe()
const re = /(from\s*|import\s*\(\s*|import\s+)(['"])([^'"]+)\2/g
const ALLOWED = new Set(['@hermes/plugin-sdk', 'react', 'react/jsx-runtime', 'react/jsx-dev-runtime'])
const seen = new Set()
for (const m of src.matchAll(re)) {
  const spec = m[3]
  if (spec && !/^[./]/.test(spec) && !/^[a-z][a-z0-9+.-]*:/i.test(spec)) seen.add(spec)
}
const bad = [...seen].filter(s => !ALLOWED.has(s))
if (bad.length) { console.error('UNSUPPORTED IMPORTS:', bad); process.exit(1) }
console.log('import specifiers OK:', [...seen].join(', ') || '(none)')

if (!/export\s*\{\s*plugin\s+as\s+default\s*\}/.test(src)) {
  console.error('missing default export'); process.exit(1)
}
console.log('default export present')

// isolate the jsnes UMD prefix: everything up to the body's first `import {`
const firstImport = src.indexOf('\nimport ')
const prefix = firstImport === -1 ? src : src.slice(0, firstImport)
const g = globalThis
;(0, eval)(prefix)
if (typeof g.jsnes?.NES !== 'function') { console.error('jsnes.NES missing after prefix eval'); process.exit(1) }
console.log('jsnes UMD attaches to globalThis.jsnes; NES present')

console.log('PASS')
