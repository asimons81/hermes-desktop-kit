// The definitive recovery test:
// A) Does nes.reset() (the plugin's Reset button) crash SMB in this jsnes build?
// B) Does same-instance loadROM() recover after a crash?
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;
const bytes = new Uint8Array(fs.readFileSync(process.argv[2] || 'homebrew.nes'));

function makeNes() {
  return new J.NES({ onFrame: function () {}, onAudioSample: function () {}, sampleRate: 48000 });
}
function run(nes, n) {
  for (let i = 0; i < n; i++) nes.frame();
}

// A) Reset button behavior
const a = makeNes();
a.loadROM(bytes);
run(a, 120);
console.log('A: booted, 120 frames OK');
a.reset();
try {
  run(a, 120);
  console.log('A: reset() -> 120 frames OK (reset is healthy)');
} catch (e) {
  console.log('A: reset() -> CRASH:', e.message);
}

// B) recovery via same-instance loadROM after a crash
const b = makeNes();
b.loadROM(bytes);
run(b, 120);
// force a real crashed state via reset() (which we just proved crashes)
b.reset();
let threw = null;
try { run(b, 5); } catch (e) { threw = e.message; }
console.log('B: crash state confirmed:', threw);
try {
  b.loadROM(bytes);   // the pump's recovery: reload the same ROM
  run(b, 300);
  console.log('B: loadROM recovery -> 300 frames OK');
} catch (e) {
  console.log('B: loadROM recovery FAIL:', e.message);
}
