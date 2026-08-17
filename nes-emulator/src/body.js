// ── NES Emulator plugin body ─────────────────────────────────────────────────
import {
  STATUSBAR_AREAS,
  atom,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SIDEBAR_NAV_AREA,
  useValue
} from '@hermes/plugin-sdk'
import { useEffect, useMemo, useRef, useState } from 'react'
import { jsx, jsxs } from 'react/jsx-runtime'

// jsnes (vendored above) attaches to globalThis.jsnes inside this ESM blob.
var J = globalThis.jsnes
var ID = 'nes-emulator'
var CONTROLLER = J.Controller
var B = CONTROLLER

// 256x240 framebuffer in ARGB, from onFrame (see tests/jsnes_buffer_probe.cjs).
var W = 256
var H = 240

// ---- plugin-scoped state (atoms; no render-closure reads) ------------------
var $roms = atom([])            // library: [{fileName,title,path,size,header}]
var $scanDir = atom('')         // custom scan dir ('' = plugin roms/ dir)
var $scanError = atom(null)
var $scanning = atom(false)
var $playing = atom(null)       // rom object currently playing (null=none)
var $paused = atom(false)
var $muted = atom(false)
var $osMuted = atom(false)        // backend-detected OS mute (mirror of audioStats.osMuted)
var $osMuteAvailable = atom(false) // backend found a Chromium stream for this process
var $showLib = atom(false)      // user re-opened the library while a game runs
var $status = atom('idle')      // 'idle' | 'loading' | 'running' | 'error'
var $statusText = atom('')

// ---- module-level runtime refs (owned by the EmulatorCanvas component) ------
var restRef = null
var storageRef = null
var workletNode = null           // AudioWorkletNode (audio thread pulls from it)
var workletPort = null           // node.port — main thread posts sample batches
var audioEnabled = false         // false when WebAudio is unavailable or the worklet failed to load
var audioToken = 0               // guards async ensureAudio against teardown/restart races
var startToken = 0               // guards async startNes against teardown/restart races
var statsTimer = 0               // 250ms audio-stats poll (powers window.__nesAudioStats)
var osMuteTimer = 0              // 2000ms OS-mute poll (WirePlumber/Chromium restore detection)

// ---- WebAudio APU: AudioWorklet path (module-level; one emulator at a time) --
// jsnes emits onAudioSample(l, r) ~48k times/sec. v1 scheduled a fresh
// createBufferSource PER SAMPLE — that turned every sample into a full-scale
// blip (loud click-train + audio-thread churn). v2 pulled from a ring through
// ONE ScriptProcessorNode(4096) — stable, but the 4096-frame buffer added
// ~85ms of fixed latency and the node is deprecated. v3 (this) uses an
// AudioWorklet: the main thread batches samples (128) and posts them to the
// worklet via port.postMessage; the worklet drains them in process() at the
// hardware render quantum (128 frames ≈ 2.7ms @ 48 kHz).
//
// The plugin ships as a single ESM blob with no filesystem, but
// audioWorklet.addModule() accepts a blob: URL, so the worklet source is
// embedded as a string below — probe-verified reachable in the isolate
// (tests/audio_cap_probe.py). jsnes's own Browser class ships the identical
// pattern (blob worklet + 128-sample batches + 8192-sample ring).
//
// No SharedArrayBuffer here, so the worklet owns the ring and the main thread
// only posts batches (never blocks, never reads the ring). Ring-drop safety:
// on overflow the worklet drops the OLDEST samples, capping latency — the
// audio thread is never blocked and the main thread never waits.
//
// Measured latency budget (isolate, PipeWire 48 kHz): a startup prefill of
// WORKLET_PREFILL (~43ms) is held as silence, then the ring rides near
// RING_TARGET. A ring-level controller (stats poll, 250ms) steers the frame
// interval so the ring stays near target, cancelling the accumulated rate
// error that would otherwise drain the ring into underruns or fill it into
// overflow drops. Typical added latency ≈ 25-40ms, worst observed ~49ms.
var AUDIO_RATE = 48000
var MASTER_GAIN = 0.3
var WORKLET_CAPACITY = 8192      // ≈170ms cushion @ 48 kHz; oldest-dropped on overflow
var WORKLET_PREFILL = 2048       // ≈43ms held before first sound; keeps the ring above
                                 // underrun level between main-thread port deliveries
var RING_TARGET = 1792           // controller target: ring is steered to ~this level
var RING_DEADBAND = 320          // ±samples around RING_TARGET needing no correction
var RATE_STEP_MS = 0.005         // frame-interval nudge per control tick (≈0.03% rate)
var BATCH_SIZE = 128             // samples per postMessage (jsnes-proven value)
var WORKLET_NAME = 'nes-audio-processor'

var WORKLET_SRC = [
  'class NesAudioProcessor extends AudioWorkletProcessor {',
  '  constructor() {',
  '    super();',
  '    this.capacity = ' + WORKLET_CAPACITY + ';',
  '    this.prefill = ' + WORKLET_PREFILL + ';',
  '    this.started = false;',
  '    this.buf = new Float32Array(this.capacity);',
  '    this.readPos = 0;',
  '    this.writePos = 0;',
  '    this.count = 0;',
  '    this.dropped = 0;',
  '    this.played = 0;',
  '    this.port.onmessage = (e) => {',
  '      const d = e.data;',
  '      if (d.type === "samples") {',
  '        const a = d.data;',
  '        const len = a.length;',
  '        if (this.count + len > this.capacity) {',
  '          const drop = this.count + len - this.capacity;',
  '          this.readPos = (this.readPos + drop) % this.capacity;',
  '          this.count -= drop;',
  '          this.dropped += drop;',
  '        }',
  '        for (let i = 0; i < len; i++) {',
  '          this.buf[this.writePos] = a[i];',
  '          this.writePos = (this.writePos + 1) % this.capacity;',
  '        }',
  '        this.count += len;',
  '      } else if (d.type === "reset") {',
  '        this.readPos = 0; this.writePos = 0; this.count = 0; this.started = false;',
  '      } else if (d.type === "getStats") {',
  '        this.port.postMessage({ type: "stats", count: this.count, dropped: this.dropped, played: this.played, started: this.started });',
  '      }',
  '    };',
  '  }',
  '  process(inputs, outputs) {',
  '    const out = outputs[0];',
  '    if (!out || out.length < 1) return true;',
  '    const ch = out[0];',
  '    const size = ch.length;',
  '    if (this.count >= size && (this.started || this.count >= this.prefill)) {',
  '      // enough audio: play from the ring. Before the first play we hold until',
  '      // the ring holds the startup cushion (prefill); afterwards we play',
  '      // whatever is available and only report a true underrun on starvation.',
  '      this.started = true;',
  '      for (let i = 0; i < size; i++) {',
  '        ch[i] = this.buf[this.readPos];',
  '        this.readPos = (this.readPos + 1) % this.capacity;',
  '      }',
  '      this.count -= size;',
  '      this.played += size;',
  '    } else {',
  '      // Not enough samples: output silence. Before the first play this is the',
  '      // intentional boot/prefill silence (not a glitch); after start it is a',
  '      // real starvation underrun (the ring was drained between main-thread',
  '      // port deliveries).',
  '      ch.fill(0);',
  '      this.played += size;',
  '      if (this.started) {',
  '        this.count = 0;',
  '        this.readPos = 0;',
  '        this.writePos = 0;',
  '        this.port.postMessage({ type: "underrun" });',
  '      }',
  '    }',
  '    return true;',
  '  }',
  '}',
  'registerProcessor("' + WORKLET_NAME + '", NesAudioProcessor);'
].join('\n')

var batchBuf = new Float32Array(BATCH_SIZE)
var batchPos = 0

function flushBatch() {
  if (batchPos <= 0 || !workletPort) { batchPos = 0; return }
  workletPort.postMessage({ type: 'samples', data: batchBuf.slice(0, batchPos) })
  batchPos = 0
}
function pushSample(v) {
  if (!workletPort) return
  batchBuf[batchPos] = v
  batchPos++
  if (batchPos >= BATCH_SIZE) flushBatch()
}
function clearAudio() {
  batchPos = 0
  if (workletPort) workletPort.postMessage({ type: 'reset' })
}

// OS-mute detection: WirePlumber restores per-application mute state keyed by
// application.name. All Electron/Chromium audio streams report
// application.name="Chromium", so a muted browser stream can be silently
// restored onto the NES playback stream. The backend parses pactl and can
// unmute the stream for this process. We poll while audio is running so the UI
// can warn and offer a one-click fix.
function checkOsMute() {
  apiRest('/audio/os-state').then(function (state) {
    if (!state) return
    audioStats.osMuted = !!state.osMuted
    audioStats.osMuteAvailable = !!state.available
    $osMuted.set(audioStats.osMuted)
    $osMuteAvailable.set(audioStats.osMuteAvailable)
  }, function (err) {
    // backend may be off or pactl missing; silently clear state
    audioStats.osMuted = false
    audioStats.osMuteAvailable = false
    $osMuted.set(false)
    $osMuteAvailable.set(false)
  })
}
function unmuteOsAudio() {
  apiRest('/audio/os-unmute', { method: 'POST' }).then(function (state) {
    audioStats.osMuted = !!(state && state.osMuted)
    audioStats.osMuteAvailable = !!(state && state.available)
    $osMuted.set(audioStats.osMuted)
    $osMuteAvailable.set(audioStats.osMuteAvailable)
  }, function (err) {
    console.warn('[nes] OS unmute failed:', err && err.message ? err.message : err)
  })
}

// audio diagnostics hook (CDP-verifiable; drives tests/cdp_audio_latency.py)
var audioStats = {
  mode: 'worklet',
  sampleRate: AUDIO_RATE,
  workletCapacity: WORKLET_CAPACITY,
  workletCount: -1,        // samples currently buffered in the worklet ring
  workletStarted: false,   // worklet has begun playback (prefill done)
  playedTotal: 0,          // samples the worklet has output since load
  droppedTotal: 0,         // samples dropped on ring overflow (latency cap)
  underruns: 0,            // post-start starvation events (silence gaps)
  frames: 0,               // emulated frames generated since last reset
  acState: 'unknown',      // AudioContext state (running/suspended)
  osMuted: false,          // OS-level mute detected via backend
  osMuteAvailable: false   // backend found a Chromium stream for this process
}
window.__nesAudioStats = audioStats

function resetAudioStats() {
  audioStats.workletCount = -1
  audioStats.workletStarted = false
  audioStats.playedTotal = 0
  audioStats.droppedTotal = 0
  audioStats.underruns = 0
  audioStats.frames = 0
  audioStats.osMuted = false
  audioStats.osMuteAvailable = false
}

function titleFromName(name) {
  var stem = name.toLowerCase().endsWith('.nes') ? name.slice(0, -4) : name
  return stem.replace(/[_-]+/g, ' ').trim() || stem
}

function prettySize(n) {
  if (n == null) return ''
  if (n < 1024) return n + ' B'
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB'
  return (n / (1024 * 1024)).toFixed(2) + ' MB'
}

// ---- backend REST (ctx.rest is the sanctioned door; bridge is the fallback) --
function apiRest(path, opts) {
  return restRef ? restRef(path, opts) : Promise.reject(new Error('plugin rest not ready'))
}

function base64ToBytes(b64) {
  var bin = atob(b64)
  var out = new Uint8Array(bin.length)
  for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

// scan dir via backend; __roms__ resolves to the plugin's own roms/ dir.
function scanLib() {
  var dir = $scanDir.get() || '__roms__'
  $scanning.set(true)
  $scanError.set(null)
  apiRest('/scan?dir=' + encodeURIComponent(dir)).then(function (res) {
    $roms.set(res.roms || [])
    $scanning.set(false)
  }, function (err) {
    $scanError.set(String(err && err.message ? err.message : err))
    $scanning.set(false)
  })
}

function loadRomBytes(rom) {
  return apiRest('/bytes?path=' + encodeURIComponent(rom.path)).then(function (res) {
    return base64ToBytes(res.base64)
  })
}

// ---- canvas + emulator (owned by EmulatorCanvas) ----------------------------
function EmulatorCanvas() {
  var rom = useValue($playing)
  var paused = useValue($paused)
  var muted = useValue($muted)
  var showLib = useValue($showLib)
  var statusText = useValue($statusText)
  var controlsState = useState(true)  // controls overlay visible (auto-hides on idle)
  var controlsVisible = controlsState[0]
  var setControlsVisible = controlsState[1]
  var osMuted = useValue($osMuted)   // local atom mirror of audioStats.osMuted for re-render
  var osMuteAvailable = useValue($osMuteAvailable)
  var canvasRef = useRef(null)
  var holderRef = useRef(null)
  var nesRef = useRef(null)
  var romBytesRef = useRef(null)   // current ROM bytes — used for reload recovery
  var acRef = useRef(null)
  var gainRef = useRef(null)
  var rafRef = useRef(0)
  var statusSetter = function (s) { $status.set(s) }
  var statusTextSetter = function (t) { $statusText.set(t) }

  // stop everything (audio + rAF + nes)
  var teardown = function () {
    startToken++
    audioToken++
    if (rafRef.current) { cancelAnimationFrame(rafRef.current); rafRef.current = 0 }
    clearCatchups()
    if (statsTimer) { clearInterval(statsTimer); statsTimer = 0 }
    if (osMuteTimer) { clearInterval(osMuteTimer); osMuteTimer = 0 }
    if (acRef.current) { try { acRef.current.close() } catch (e) {} }
    acRef.current = null
    gainRef.current = null
    workletNode = null
    workletPort = null
    batchPos = 0
    nesRef.current = null
    audioEnabled = false
    $paused.set(false)
    resetAudioStats()
  }

  // build the AudioContext + worklet path (WebAudio APU). Async: worklet
  // addModule needs a microtask. Fails soft — the game still runs silent.
  var ensureAudio = function () {
    if (acRef.current) return Promise.resolve()
    var AC = window.AudioContext || window.webkitAudioContext
    if (!AC) { audioEnabled = false; return Promise.resolve() }
    var token = ++audioToken
    var ac = null
    try {
      ac = new AC({ sampleRate: AUDIO_RATE })
    } catch (e) {
      audioEnabled = false
      return Promise.resolve()
    }
    acRef.current = ac
    gainRef.current = ac.createGain()
    gainRef.current.gain.value = muted ? 0 : MASTER_GAIN
    gainRef.current.connect(ac.destination)
    var blob = new Blob([WORKLET_SRC], { type: 'application/javascript' })
    var url = URL.createObjectURL(blob)
    var fail = function (err) {
      URL.revokeObjectURL(url)
      console.warn('[nes] audio worklet unavailable — running silent:', err && err.message ? err.message : err)
      audioEnabled = false
      try { ac.close() } catch (e) {}
      if (acRef.current === ac) { acRef.current = null; gainRef.current = null }
    }
    return ac.audioWorklet.addModule(url).then(function () {
      URL.revokeObjectURL(url)
      if (token !== audioToken) return          // teardown/restart superseded this
      var node = new AudioWorkletNode(ac, WORKLET_NAME, { outputChannelCount: [1] })
      workletNode = node
      workletPort = node.port
      node.port.onmessage = function (e) {
        var d = e.data
        if (!d) return
        if (d.type === 'underrun') {
          audioStats.underruns++
        } else if (d.type === 'stats') {
          audioStats.workletCount = d.count
          audioStats.droppedTotal = d.dropped
          audioStats.playedTotal = d.played
          audioStats.workletStarted = !!d.started
          // ring-level controller: steer the frame interval so the worklet ring
          // stays near RING_TARGET. Above target = emulator running fast (slow
          // the frames); below = running slow (speed them). Tiny deadbanded
          // steps keep the rate within ±0.1% of 60.0988fps — invisible to
          // gameplay, but it cancels the accumulated rate error that would
          // otherwise let the ring drift into underruns or overflow drops.
          // The controller only runs once the worklet has started playing (the
          // prefill hold legitimately sits below target and must not speed the
          // emulator up).
          var c = d.count
          if (c >= 0 && d.started) {
            if (c > RING_TARGET + RING_DEADBAND) frameInterval = Math.min(FRAME_BASE_MS * 1.01, frameInterval + RATE_STEP_MS)
            else if (c < RING_TARGET - RING_DEADBAND) frameInterval = Math.max(FRAME_BASE_MS * 0.99, frameInterval - RATE_STEP_MS)
          }
        }
      }
      node.connect(gainRef.current)
      audioEnabled = true
      if (ac.state === 'suspended') ac.resume()
      if (statsTimer) clearInterval(statsTimer)
      statsTimer = setInterval(function () {
        if (workletPort) workletPort.postMessage({ type: 'getStats' })
        if (acRef.current) audioStats.acState = acRef.current.state
      }, 250)
      if (osMuteTimer) clearInterval(osMuteTimer)
      osMuteTimer = setInterval(checkOsMute, 2000)
      resetAudioStats()
    }, fail)
  }

  // start a fresh NES for a ROM (called once per play). Async because the
  // worklet must finish loading before jsnes starts emitting samples.
  var startNes = function (bytes) {
    teardown()
    var token = ++startToken
    frameInterval = FRAME_BASE_MS          // fresh rate; the controller re-steers it
    romBytesRef.current = bytes
    ensureAudio().then(function () {
      if (token !== startToken) return          // a newer start/teardown won
      var canvas = canvasRef.current
      if (!canvas) return
      var ctx = canvas.getContext('2d')
      // Framebuffer is 256x240 (ARGB). putImageData CANNOT scale, so a naive
      // `ctx.putImageData(img, 0, 0)` writes the game into the top-left corner
      // of the ResizeObserver-scaled buffer — a ~8% content island inside a
      // mostly-blank pane (QA 2026-08-15: 240x224 of 1009x946, nonEmptyFrac
      // 5.2%). Paint 1:1 into an offscreen 256x240 canvas, then drawImage the
      // scaled frame onto the full buffer with smoothing off (pixelated).
      var offCanvas = document.createElement('canvas')
      offCanvas.width = W
      offCanvas.height = H
      var offCtx = offCanvas.getContext('2d')
      var img = offCtx.createImageData(W, H)

      var nes = new J.NES({
        onFrame: function (buf) {
          // buf is a Uint32Array (ARGB) of length W*H (probe-verified).
          var data = img.data
          for (var i = 0; i < W * H; i++) {
            var px = buf[i]
            data[i * 4] = px & 0xff
            data[i * 4 + 1] = (px >> 8) & 0xff
            data[i * 4 + 2] = (px >> 16) & 0xff
            data[i * 4 + 3] = 255
          }
          offCtx.putImageData(img, 0, 0)
          ctx.imageSmoothingEnabled = false
          ctx.drawImage(offCanvas, 0, 0, canvas.width, canvas.height)
        },
        onAudioSample: function (l, r) {
          pushSample((l + r) * 0.5)
        },
        sampleRate: acRef.current ? acRef.current.sampleRate : AUDIO_RATE
      })
      nes.loadROM(bytes)
      nesRef.current = nes
      statusSetter('running')
    })
  }

  // frame pump — paced at the NTSC frame rate (60.0988fps) so the emulator's
  // audio production matches the 48 kHz hardware clock. Plain
  // requestAnimationFrame (v2) ran at the DISPLAY refresh instead: a 120 Hz
  // display overproduces audio (ring fills + drops) and a 60 Hz display
  // underproduces by ~0.16% (periodic silence blips). The timer snaps rAF
  // timestamps to a 1000/60.0988 grid and spreads any missed frames across the
  // interval with setTimeout — the same design as the frame timer in jsnes's
  // own Browser class. Crash recovery stays: RELOAD the ROM (jsnes reset() is
  // broken in this build: it boots SMB straight into an invalid opcode at
  // $1fd — see tests/jsnes_reset_bug.cjs — so reload is the only clean
  // restart path).
  var FRAME_BASE_MS = 1000 / 60.0988
  var frameInterval = FRAME_BASE_MS   // mutable: steered by the ring-level controller
  var frameState = { last: 0, catchups: [], reloadAt: 0 }

  var clearCatchups = function () {
    for (var i = 0; i < frameState.catchups.length; i++) clearTimeout(frameState.catchups[i])
    frameState.catchups = []
  }

  var runFrame = function () {
    var nes = nesRef.current
    if (!nes || $paused.get()) return
    try {
      nes.frame()
      flushBatch()
      audioStats.frames++
    } catch (e) {
      statusSetter('error')
      statusTextSetter(String(e && e.message ? e.message : e))
      console.warn('[nes] frame error — reloading ROM:', e && e.message ? e.message : e)
      var now = Date.now()
      if (now - frameState.reloadAt > 5000 && romBytesRef.current) {
        frameState.reloadAt = now
        try { clearAudio(); nes.loadROM(romBytesRef.current); statusSetter('running'); statusTextSetter('') } catch (e2) { console.warn('[nes] reload failed:', e2) }
      }
    }
  }

  var pump = function (ts) {
    rafRef.current = requestAnimationFrame(pump)
    var nes = nesRef.current
    if (!nes || $paused.get()) {
      frameState.last = 0
      clearCatchups()
      return
    }
    var e = ts % frameInterval
    var s = ts - e
    if (!frameState.last) { frameState.last = s; return }
    var n = Math.round((s - frameState.last) / frameInterval)
    if (n <= 0) return
    if (n > 8) {
      // way behind (tab hidden / main thread stalled for >130ms): don't burst
      // a backlog of stale audio into the worklet ring — drop the stale clock,
      // silence the ring, and restart clean from here.
      frameState.last = s
      clearAudio()
      runFrame()
      return
    }
    frameState.last += n * frameInterval
    runFrame()
    if (n > 1) {
      // n frames came due since the last rAF: run the first now, spread the
      // rest across the remainder of this interval so audio production stays
      // smooth (no burst into the worklet ring).
      var r = frameInterval - e
      for (var i = 1; i < n; i++) {
        (function (delay) {
          var id = setTimeout(runFrame, delay)
          frameState.catchups.push(id)
        })(i * r / n)
      }
    }
  }

  // (re)start the frame pump + load when a new ROM is selected
  useEffect(function () {
    if (!rom) { teardown(); statusSetter('idle'); statusTextSetter(''); return }
    statusSetter('loading')
    loadRomBytes(rom).then(function (bytes) {
      startNes(bytes)
      if (!rafRef.current) rafRef.current = requestAnimationFrame(pump)
    }, function (err) {
      statusSetter('error')
      statusTextSetter(String(err && err.message ? err.message : err))
    })
    return teardown
  }, [rom])

  // mute toggles the gain
  useEffect(function () {
    if (gainRef.current) gainRef.current.gain.value = muted ? 0 : MASTER_GAIN
  }, [muted])

  // sync OS-mute atoms with the audioStats hook so the UI can react
  useEffect(function () {
    var id = setInterval(function () {
      $osMuted.set(audioStats.osMuted)
      $osMuteAvailable.set(audioStats.osMuteAvailable)
    }, 250)
    return function () { clearInterval(id) }
  }, [])

  // keyboard capture on the page container (page-scoped; composer stands down)
  useEffect(function () {
    var KEYMAP = {
      ArrowUp: 'BUTTON_UP', ArrowDown: 'BUTTON_DOWN',
      ArrowLeft: 'BUTTON_LEFT', ArrowRight: 'BUTTON_RIGHT',
      KeyX: 'BUTTON_A', KeyA: 'BUTTON_A',       // X or A = jump; HOLD for a higher jump
      KeyZ: 'BUTTON_B', KeyS: 'BUTTON_B',       // Z or S = run / shoot
      Enter: 'BUTTON_START', ShiftRight: 'BUTTON_SELECT',
      KeyQ: 'BUTTON_TURBO_A', KeyW: 'BUTTON_TURBO_B'  // turbo moved off A/S (auto-fire ≠ hold)
    }
    var down = function (e) {
      if (!nesRef.current || e.repeat) return
      var btn = KEYMAP[e.code]
      if (btn) { e.preventDefault(); nesRef.current.buttonDown(1, B[btn]) }
    }
    var up = function (e) {
      if (!nesRef.current) return
      var btn = KEYMAP[e.code]
      if (btn) { e.preventDefault(); nesRef.current.buttonUp(1, B[btn]) }
    }
    window.addEventListener('keydown', down, { capture: false })
    window.addEventListener('keyup', up, { capture: false })
    return function () {
      window.removeEventListener('keydown', down, { capture: false })
      window.removeEventListener('keyup', up, { capture: false })
    }
  }, [])


  // controls overlay auto-hide: visible on any pointer activity over the page,
  // fade out after 2.5s of idle so the game gets the whole in-window height.
  // Never hidden while paused (the pause overlay would leave no way to resume
  // without moving the mouse) — pause/error/library keep it pinned.
  useEffect(function () {
    var timer = 0
    var wake = function () {
      setControlsVisible(true)
      if (timer) clearTimeout(timer)
      timer = setTimeout(function () { setControlsVisible(false) }, 2500)
    }
    var move = function () { wake() }
    var down = function () { wake() }
    wake()
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('pointerdown', down, { passive: true })
    return function () {
      if (timer) clearTimeout(timer)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerdown', down)
    }
  }, [])

  // ResizeObserver on the holder, resize the canvas (width/height attrs, not CSS)
  useEffect(function () {
    var holder = holderRef.current
    var canvas = canvasRef.current
    if (!holder || !canvas) return
    var scale = function () {
      var cw = holder.clientWidth
      var ch = holder.clientHeight
      var s = Math.min(cw / W, ch / H, 6)  // fit the holder, no crop zoom
      var w = Math.max(1, Math.floor(W * s))
      var h = Math.max(1, Math.floor(H * s))
      if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h }
    }
    scale()
    var raf2 = requestAnimationFrame(scale)   // re-scale once layout has settled
    var ro = new ResizeObserver(scale)
    ro.observe(holder)
    return function () { cancelAnimationFrame(raf2); ro.disconnect() }
    // deps [rom]: must re-run when the canvas actually mounts (rom becomes set).
    // With [] the effect no-ops at first mount (canvas null) and never starts the
    // ResizeObserver, leaving the buffer at 256x240. Zoom removal: the canvas
    // always fills its holder, so no crop-only transform scale is applied.
  }, [rom])

  if (!rom) {
    // Idle "menu" screen: branded promo art (data URL inlined by assemble.sh
    // as HERMES_NES_MENU_BG) with the library hint below it. An <img> with
    // max-width/max-height 100% keeps the poster at natural size (no upscale
    // blur on huge panes) and can never crop or overflow.
    return jsxs('div', { className: 'nes-menu', children: [
      jsx('img', { className: 'nes-menu-art', src: HERMES_NES_MENU_BG, alt: 'HerNES' }),
      jsx('div', { className: 'nes-menu-hint', children: 'Select a game from the library to start playing.' })
    ] })
  }

  return jsxs('div', { className: 'nes-player', children: [
    jsxs('div', {
      ref: holderRef,
      className: 'nes-canvas-holder',
      children: [
        jsx('canvas', {
          ref: canvasRef,
          width: W,
          height: H,
          style: { position: 'absolute', inset: '0', width: '100%', height: '100%', objectFit: 'contain', imageRendering: 'pixelated', display: 'block' }
        }),
        jsxs('div', { className: 'nes-controls' + (controlsVisible || paused || showLib ? '' : ' nes-controls-hidden'), children: [
          jsx('button', { type: 'button', onClick: function () { var n = nesRef.current; if (n && romBytesRef.current) { clearAudio(); n.loadROM(romBytesRef.current) } }, children: 'Reset' }),
          jsx('button', { type: 'button', onClick: function () { $paused.set(!$paused.get()) }, children: paused ? 'Resume' : 'Pause' }),
          jsx('button', { type: 'button', onClick: function () { $muted.set(!$muted.get()) }, children: muted ? 'Unmute' : 'Mute' }),
          osMuteAvailable && osMuted
            ? jsx('button', { type: 'button', className: 'nes-os-mute', onClick: unmuteOsAudio, children: 'Unmute OS audio' })
            : null,
          jsx('button', { type: 'button', onClick: function () { $showLib.set(!$showLib.get()) }, children: showLib ? 'Hide Library' : 'Library' }),
          jsx('button', { type: 'button', onClick: function () { $playing.set(null) }, children: 'Stop' }),
          jsx('span', { className: 'nes-status-text', children: statusText })
        ] })
      ]
    })
  ] })
}
