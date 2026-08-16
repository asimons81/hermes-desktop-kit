// Long gameplay run: SMB, holding RIGHT + run, periodic jumps — progresses the
// game past title/1-1 so level-transition code paths actually execute.
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

const C = J.Controller;
// start: hold RIGHT + B (run) the whole time
nes.buttonDown(1, C.BUTTON_RIGHT);
nes.buttonDown(1, C.BUTTON_B);
let jumpTimer = 0;
let crash = null;

const FRAMES = 60000; // ~16.7 simulated minutes
for (let f = 0; f < FRAMES; f++) {
  // press A (jump) every ~90 frames for 12 frames (run-jump cadence)
  if (jumpTimer > 0) {
    jumpTimer--;
    nes.buttonDown(1, C.BUTTON_A);
  } else {
    nes.buttonUp(1, C.BUTTON_A);
    if (f % 90 === 0) jumpTimer = 12;
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
console.log('NO CRASH over', FRAMES, 'frames of gameplay input');
