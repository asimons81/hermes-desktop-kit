import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'

const source = await readFile(new URL('../plugin.js', import.meta.url), 'utf8')

test('calendar refresh control is explicit, view-aware, and reports in-flight state', () => {
  // Refresh only applies to the queue view; disabled + label reflect fetching.
  assert.match(source, /children:\s*view === 'queue' && queue\.isFetching\s*\?\s*'Refreshing…'\s*:\s*'Refresh'/)
  assert.match(source, /disabled:\s*view === 'queue' && queue\.isFetching/)
  assert.match(source, /'aria-label':\s*view === 'queue' && queue\.isFetching\s*\?\s*'Refreshing Typefully queue'\s*:\s*'Refresh Typefully'/)
  assert.match(source, /onClick:\s*\(\)\s*=>\s*void \(view === 'queue' \? queue\.refetch\(\) : null\)/)
})
