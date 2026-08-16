// Long-run stress: SMB through vendored jsnes with random button mashing.
// Reports frame count, audio samples, and any "Game crashed" invalid opcode.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;

const romPath = process.argv[2] || process.argv[2] || 'homebrew.nes';
const bytes = new Uint8Array(fs.readFileSync(romPath));

let samples = 0;
const nes = new J.NES({
  onFrame: function () {},
  onAudioSample: function () { samples++ },
  sampleRate: 48000
});
nes.loadROM(bytes);

const BUTTONS = ['BUTTON_A', 'BUTTON_B', 'BUTTON_UP', 'BUTTON_DOWN', 'BUTTON_LEFT', 'BUTTON_RIGHT', 'BUTTON_START', 'BUTTON_SELECT'];
let rng = 42;
const rand = () => (rng = (rng * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;

const FRAMES = 12000; // 200 simulated seconds
const held = new Set();
let crash = null;

for (let f = 0; f < FRAMES; f++) {
  // occasionally press/release random buttons (mash like a player)
  if (rand() < 0.15) {
    const btn = BUTTONS[Math.floor(rand() * BUTTONS.length)];
    if (rand() < 0.5) { held.add(btn); nes.buttonDown(1, J.Controller[btn]); }
    else { held.delete(btn); nes.buttonUp(1, J.Controller[btn]); }
  }
  try {
    nes.frame();
  } catch (e) {
    crash = { frame: f, message: String(e && e.message ? e.message : e) };
    break;
  }
}

console.log('frames run:', crash ? crash.frame : FRAMES);
console.log('audio samples:', samples);
if (crash) {
  console.log('CRASH:', crash.message);
  process.exit(2);
}
console.log('NO CRASH over', FRAMES, 'frames');
