// Verify the ACTUAL crash-recovery pattern used by the plugin: after a crash,
// re-loadROM() the same bytes (NOT nes.reset(), which is broken in this build —
// see jsnes_reset_bug.cjs). Assert reload recovers and the game boots cleanly.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;

const bytes = new Uint8Array(fs.readFileSync(process.argv[2] || 'homebrew.nes'));
const nes = new J.NES({ onFrame: function () {}, onAudioSample: function () {}, sampleRate: 48000 });
nes.loadROM(bytes);
for (let i = 0; i < 120; i++) nes.frame();

// force the crashed state (what the pump's catch sees)
nes.crashed = true;
let threw = false;
try { nes.frame(); } catch (e) { threw = true; console.log('crashed frame() threw:', e.message); }
if (!threw) { console.log('FAIL: crashed frame() did not throw'); process.exit(1); }

// plugin recovery: reload the same ROM bytes (romBytesRef + loadROM)
nes.loadROM(bytes);
let ok = true;
try {
  for (let i = 0; i < 300; i++) nes.frame();
} catch (e) {
  ok = false;
  console.log('FAIL: frame() still throws after reload:', e.message);
}
console.log(ok ? 'PASS: loadROM() recovers from crashed state (300 frames run)' : 'FAIL');
process.exit(ok ? 0 : 1);
