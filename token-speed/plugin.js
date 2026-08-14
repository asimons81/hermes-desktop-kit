/**
 * token-speed — live tokens/sec status bar chip for the active model.
 *
 * A plain runtime desktop plugin (no build step, no core changes):
 *   - Listens to the live gateway stream (host.onEvent) for message.delta /
 *     reasoning.delta text deltas.
 *   - Estimates tokens from characters (~4 chars/token) because the gateway
 *     emits text deltas without per-frame token counts.
 *   - Computes a smoothed rolling tok/s and renders it as a status bar chip
 *     ('124 tok/s', or '-- tok/s' when idle/unavailable).
 *
 * Loads within a few seconds of landing in
 *   ~/.hermes/desktop-plugins/token-speed/plugin.js
 * and hot-reloads on every save. Toggleable via the status bar's right-click
 * menu ("Token speed").
 */

import { atom, cn, host, Tip, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

const ID = 'token-speed'

// Heuristic: gateway emits text, not token counts (~4 chars per token).
const CHARS_PER_TOKEN = 4
// Trailing window over which raw tok/s is measured.
const WINDOW_MS = 5000
// EMA smoothing factor (matches the original core provider).
const ALPHA = 0.35
// Stream considered idle after this silence.
const IDLE_MS = 4000
// Chip refresh interval.
const POLL_MS = 1000

// Rolling samples: { chars, ts } appended on every delta event.
let samples = []
let lastTps = null

function pushText(text) {
  if (!text) return
  const chars = String(text).length
  if (chars === 0) return
  const now = Date.now()
  samples.push({ chars, ts: now })
  // Evict outside the window.
  const cutoff = now - WINDOW_MS
  while (samples.length > 0 && samples[0].ts < cutoff) samples.shift()
}

function computeTps() {
  const now = Date.now()
  const cutoff = now - WINDOW_MS
  while (samples.length > 0 && samples[0].ts < cutoff) samples.shift()

  if (samples.length === 0) return null
  const span = now - samples[0].ts
  if (span < 250) return null // too few samples / too short a span
  const last = samples[samples.length - 1].ts
  if (now - last > IDLE_MS) return null // idle stream

  const totalChars = samples.reduce((sum, s) => sum + s.chars, 0)
  const raw = totalChars / CHARS_PER_TOKEN / (span / 1000)
  if (!Number.isFinite(raw) || raw <= 0) return null

  lastTps = lastTps === null ? raw : ALPHA * raw + (1 - ALPHA) * lastTps
  return Math.round(lastTps)
}

function formatTps(value) {
  return value === null ? '-- tok/s' : `${value} tok/s`
}

const $tps = atom(null)

function TokenSpeedChip() {
  const tps = useValue($tps)
  const model = useValue(host.state.model)
  const label = formatTps(tps)

  return jsx(Tip, {
    label: model ? `${model} — ${label}` : label,
    children: jsx('button', {
      className: cn(
        'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] tabular-nums transition-colors',
        'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
        tps !== null && 'text-foreground'
      ),
      type: 'button',
      children: jsxs('span', { children: [label] })
    })
  })
}

export default {
  id: ID,
  name: 'Token Speed',
  register(ctx) {
    // Live gateway stream: feed text deltas into the estimator.
    host.onEvent('*', (event) => {
      const type = event && event.type
      if (type === 'message.delta' || type === 'reasoning.delta') {
        const payload = event.payload || {}
        pushText(payload.text)
      }
    })

    // Poll the estimator and repaint the chip.
    const timer = setInterval(() => {
      $tps.set(computeTps())
    }, POLL_MS)

    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 130,
      render: () => jsx(TokenSpeedChip, {})
    })

    // Cleanup on plugin teardown / hot reload.
    return () => clearInterval(timer)
  }
}
