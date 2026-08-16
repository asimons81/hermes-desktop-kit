// Probe: how to inject a real invalid-opcode crash, and which recovery works.
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'jsnes.min.js'), 'utf8');
(0, eval)(src);
const J = globalThis.jsnes;
const bytes = new Uint8Array(fs.readFileSync(process.argv[2] || 'homebrew.nes'));
const nes = new J.NES({ onFrame: function () {}, onAudioSample: function () {}, sampleRate: 48000 });
nes.loadROM(bytes);
for (let i = 0; i < 120; i++) nes.frame();

console.log('cpu keys:', Object.getOwnPropertyNames(Object.getPrototypeOf(nes.cpu)).slice(0, 20));
console.log('cpu own keys:', Object.keys(nes.cpu).slice(0, 25));
console.log('mmap keys:', Object.keys(nes.mmap).slice(0, 25));
console.log('nes own keys:', Object.keys(nes).slice(0, 30));
