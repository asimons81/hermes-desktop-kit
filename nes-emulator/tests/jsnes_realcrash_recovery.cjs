// Real invalid-opcode crash injection + recovery comparison (reset vs loadROM).
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;
const romPath = process.argv[2] || 'homebrew.nes';
const bytes = new Uint8Array(fs.readFileSync(romPath));

function makeNes() {
  return new J.NES({ onFrame: function () {}, onAudioSample: function () {}, sampleRate: 48000 });
}

function runFrames(nes, n) {
  for (let i = 0; i < n; i++) nes.frame();
}

// --- inject a real crash: PC -> RAM byte 0x02 (illegal opcode) ---
const nes = makeNes();
nes.loadROM(bytes);
runFrames(nes, 120);
nes.cpu.write(0x0400, 0x02);       // illegal opcode in RAM
nes.cpu.REG_PC = 0x0400;
let threw = null;
try { runFrames(nes, 1); } catch (e) { threw = e.message; }
console.log('injected crash threw:', threw);
if (!threw) { console.log('FAIL: no crash injected'); process.exit(1); }

// --- recovery A: reset() ---
try {
  nes.reset();
  runFrames(nes, 300);
  console.log('recovery A (reset): OK — 300 frames ran');
} catch (e) {
  console.log('recovery A (reset): FAIL —', e.message);
}

// --- recovery B: fresh NES (same as re-loadROM in the plugin) ---
const nes2 = makeNes();
nes2.loadROM(bytes);
try {
  runFrames(nes2, 300);
  console.log('recovery B (fresh load): OK — 300 frames ran');
} catch (e) {
  console.log('recovery B (fresh load): FAIL —', e.message);
}
