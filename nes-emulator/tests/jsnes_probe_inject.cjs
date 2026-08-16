// Debug: which write path lands 0x02 in RAM, and how to make the CPU execute it.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;
const bytes = new Uint8Array(fs.readFileSync(process.argv[2] || 'homebrew.nes'));
const nes = new J.NES({ onFrame: function () {}, onAudioSample: function () {}, sampleRate: 48000 });
nes.loadROM(bytes);
for (let i = 0; i < 120; i++) nes.frame();

nes.cpu.write(0x0400, 0x02);
console.log('cpu.load(0x0400) after write =', nes.cpu.load(0x0400).toString(16));
console.log('cpu.load source:', String(nes.cpu.load).slice(0, 160));
console.log('cpu.write source:', String(nes.cpu.write).slice(0, 160));

// how does the cpu read opcodes? find the opcode fetch in emulate
const em = String(nes.cpu.emulate);
const idx = em.indexOf('load(');
console.log('emulate opcode fetch context:', em.slice(Math.max(0, idx - 120), idx + 120));
