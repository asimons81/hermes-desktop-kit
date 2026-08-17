// ── Library + page + registration ───────────────────────────────────────────
function Library() {
  var roms = useValue($roms)
  var scanning = useValue($scanning)
  var scanErr = useValue($scanError)
  var playing = useValue($playing)

  var rows = roms.map(function (rom) {
    var h = rom.header || {}
    var active = playing && playing.path === rom.path
    return jsx('div', {
      key: rom.path,
      className: 'nes-row' + (active ? ' nes-row-active' : ''),
      onClick: function () { $playing.set(rom); $showLib.set(false) },
      children: jsxs('div', { className: 'nes-row-inner', children: [
        jsx('span', { className: 'nes-row-title', children: rom.title }),
        jsx('span', { className: 'nes-row-meta', children: [
          h.mapper != null ? 'Mapper ' + h.mapper : '?',
          ' · ',
          h.prg_rom_kb != null ? h.prg_rom_kb + 'K PRG' : '?',
          ' · ',
          prettySize(rom.size)
        ].join('') })
      ] })
    })
  })

  return jsxs('div', { className: 'nes-library', children: [
    jsxs('div', { className: 'nes-toolbar', children: [
      jsx('button', { type: 'button', className: 'nes-btn', onClick: function () { scanLib() }, children: 'Scan for games' }),
      jsx('span', { className: 'nes-hint', children: 'Drop .nes files in the plugin roms/ folder, then scan.' })
    ] }),
    scanErr ? jsx('div', { className: 'nes-error', children: String(scanErr) }) : null,
    scanning ? jsx('div', { className: 'nes-hint', children: 'Scanning…' }) : null,
    roms.length === 0 && !scanning
      ? jsx('div', { className: 'nes-empty', children: 'No games found. Add legal .nes files you own to the roms/ folder and scan.' })
      : jsx('div', { className: 'nes-list', children: rows })
  ] })
}

function NesPage() {
  var playing = useValue($playing)
  var showLib = useValue($showLib)
  var libOpen = !playing || showLib
  var gameMode = !!playing && !showLib
  return jsxs('div', { className: 'nes-page' + (gameMode ? ' nes-page-game' : ''), children: [
    gameMode ? null : jsx('div', { className: 'nes-header', children: jsxs('h1', { className: 'nes-title', children: [
      'HerNES Emulator',
      playing ? ' — ' + playing.title : ''
    ] }) }),
    jsxs('div', { className: 'nes-body', children: [
      libOpen ? jsx('div', { className: 'nes-side', children: jsx(Library, {}) }) : null,
      jsx('div', { className: 'nes-main', children: jsx(EmulatorCanvas, {}) })
    ] })
  ] })
}

function StatusChip() {
  var playing = useValue($playing)
  var status = useValue($status)
  var label = playing ? (status === 'running' ? '▶ ' : status === 'loading' ? '… ' : '') + playing.title : 'HerNES'
  return jsx('span', {
    className: 'nes-chip',
    title: 'Open HerNES',
    onClick: function () { host.navigate('/nes') },
    children: label
  })
}

// ---- scoped styles (theme-var colors only; idempotent content check) --------
function ensureStyles() {
  var css = [
    '.nes-page { display:flex; flex-direction:column; height:100%; padding:1rem; gap:1rem; overflow:auto; }',
    // Game mode has no intrinsic content besides the player. Explicitly carry
    // the host pane's definite dimensions through every flex boundary so the
    // game cannot collapse to its 256x240 intrinsic canvas size.
    '.nes-page-game { padding:0; gap:0; min-width:0; min-height:100%; overflow:hidden; }',
    '.nes-title { font-size:1.15rem; font-weight:600; color:var(--ui-text-primary); margin:0; }',
    '.nes-body { display:flex; flex:1 1 auto; gap:1rem; min-width:0; min-height:0; }',
    '.nes-side { width:300px; flex:none; overflow:auto; border-right:1px solid var(--ui-stroke-secondary); padding-right:0.5rem; }',
    '.nes-main { flex:1 1 auto; min-width:0; min-height:0; height:100%; display:flex; flex-direction:column; }',
    '.nes-list { display:flex; flex-direction:column; gap:2px; }',
    '.nes-row { padding:6px 8px; border-radius:6px; cursor:pointer; }',
    '.nes-row:hover { background:var(--chrome-action-hover); }',
    '.nes-row-active { background:var(--chrome-action-active, var(--chrome-action-hover)); }',
    '.nes-row-title { font-weight:500; color:var(--ui-text-primary); display:block; }',
    '.nes-row-meta { font-size:0.75rem; color:var(--ui-text-quaternary); }',
    '.nes-toolbar { display:flex; align-items:center; gap:0.5rem; margin-bottom:0.5rem; }',
    '.nes-btn { padding:4px 10px; border:1px solid var(--ui-stroke-secondary); border-radius:6px; background:transparent; color:var(--ui-text-primary); cursor:pointer; }',
    '.nes-btn:hover { background:var(--chrome-action-hover); }',
    '.nes-hint { font-size:0.78rem; color:var(--ui-text-quaternary); }',
    '.nes-empty { color:var(--ui-text-quaternary); padding:1rem 0; }',
    // Idle menu: the promo art as a floating poster (data URL injected by
    // assemble.sh), hint below. The 1px stroke/radius sit ON the img so the
    // frame hugs the poster at any pane shape (no dead-space box). Theme vars
    // only — hardcoded colors trip L3.
    '.nes-menu { flex:1 1 auto; min-height:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:0.6rem; padding:0.75rem; overflow:hidden; background:radial-gradient(ellipse at 50% 42%, color-mix(in srgb, var(--ui-danger, var(--chrome-action-hover, transparent)) 8%, transparent) 0%, transparent 70%); }',
    '.nes-menu-art { max-width:100%; max-height:100%; width:auto; height:auto; object-fit:contain; display:block; border:1px solid var(--ui-stroke-secondary); border-radius:10px; }',
    '.nes-menu-hint { font-size:0.8rem; color:var(--ui-text-quaternary); }',
    '.nes-error { color:var(--ui-danger, var(--ui-text-secondary)); font-size:0.8rem; }',
    '.nes-canvas-holder { position:relative; flex:1 1 auto; min-width:0; min-height:0; width:100%; overflow:hidden; background:var(--chrome-surface, transparent); }',
    '.nes-player { flex:1 1 auto; min-width:0; min-height:0; display:flex; flex-direction:column; }',
    // Controls are an overlay pinned to the bottom of the canvas holder.
    // Auto-hidden on idle (2.5s no pointer activity) so the in-window game uses
    // the full height; always visible while paused / library open / error.
    '.nes-controls { position:absolute; left:0; right:0; bottom:0; display:flex; align-items:center; gap:0.5rem; padding:0.4rem 0.6rem; flex-wrap:wrap; background:color-mix(in srgb, var(--chrome-surface, transparent) 78%, transparent); border-top:1px solid var(--ui-stroke-secondary); opacity:1; transition:opacity 0.2s ease; pointer-events:auto; z-index:2; }',
    '.nes-controls.nes-controls-hidden { opacity:0; pointer-events:none; }',
    '.nes-status-text { font-size:0.78rem; color:var(--ui-text-quaternary); margin-left:auto; }',
    '.nes-os-mute { font-weight:600; color:var(--ui-danger); border-color:var(--ui-danger); }',
    '.nes-chip { cursor:pointer; color:var(--ui-text-secondary); }',
    '.nes-chip:hover { color:var(--ui-text-primary); }'
  ].join('\n')
  var el = document.getElementById('nes-emulator-styles')
  if (!el) {
    el = document.createElement('style')
    el.id = 'nes-emulator-styles'
    document.head.appendChild(el)
  }
  if (el.textContent !== css) el.textContent = css
}

var plugin = {
  id: ID,
  name: 'HerNES',
  description: 'Play .nes games you already own — local library scan, iNES header info, and a jsnes-based emulator canvas.',
  register: function (ctx) {
    restRef = ctx.rest
    storageRef = ctx.storage
    ensureStyles()
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/nes' }, render: function () { return jsx(NesPage, {}) } },
      { id: 'nav', area: SIDEBAR_NAV_AREA, order: 70, data: { codicon: 'game', label: 'HerNES', path: '/nes' } },
      { id: 'open', area: PALETTE_AREA, data: { id: 'nes.open', label: 'HerNES: Open', keywords: ['nes', 'hernes', 'emulator', 'games'], run: function () { host.navigate('/nes') } } },
      { id: 'statusbar', area: STATUSBAR_AREAS.right, order: 130, render: function () { return jsx(StatusChip, {}) } }
    ])
  }
}

export { plugin as default }
