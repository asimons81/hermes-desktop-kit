// Regression test: the A key must HOLD, not auto-fire.
// Old mapping: KeyA -> BUTTON_TURBO_A (jsnes toggles state every frame -> SMB repeat-jumps)
// New mapping: KeyA -> BUTTON_A       (state stays pressed until keyup -> high jump works)
const fs = require('fs');
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8');
(0, eval)(src);
const C = globalThis.jsnes.Controller;

const FRAMES = 12;

// OLD behavior: A key wired to TURBO_A
const oldC = new C();
oldC.buttonDown(C.BUTTON_TURBO_A);
const oldStates = [];
for (let i = 0; i < FRAMES; i++) { oldC.clock(); oldStates.push(oldC.state[C.BUTTON_A]); }
const oldPressedFrames = oldStates.filter(s => s === 65).length;
console.log('OLD (A=turbo):  state[A] per frame =', oldStates.join(','));

// NEW behavior: A key wired to BUTTON_A
const newC = new C();
newC.buttonDown(C.BUTTON_A);
const newStates = [];
for (let i = 0; i < FRAMES; i++) { newC.clock(); newStates.push(newC.state[C.BUTTON_A]); }
const newPressedFrames = newStates.filter(s => s === 65).length;
console.log('NEW (A=hold):   state[A] per frame =', newStates.join(','));

newC.buttonUp(C.BUTTON_A);
newC.clock();
console.log('NEW after release: state[A] =', newC.state[C.BUTTON_A]);

let ok = true;
if (!(oldPressedFrames < FRAMES)) { console.log('FAIL: turbo path should NOT hold all frames'); ok = false; }
if (newPressedFrames !== FRAMES) { console.log('FAIL: real button must stay pressed while held'); ok = false; }
if (newC.state[C.BUTTON_A] !== 64) { console.log('FAIL: release must clear the button'); ok = false; }
console.log(ok ? 'PASS: A-key hold semantics verified' : 'FAIL');
process.exit(ok ? 0 : 1);
