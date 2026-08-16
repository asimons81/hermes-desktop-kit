// Inspect the NES instance's runtime callable surface (frame loop, input).
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
const nes = new J.NES()
console.log('own props:', Object.getOwnPropertyNames(nes))
console.log('frame?', typeof nes.frame)
console.log('buttonDown?', typeof nes.buttonDown, 'buttonUp?', typeof nes.buttonUp)
// frame loop drives ui.writeFrame -> opts.onFrame (see probe 1)
const rom = new Uint8Array(16 + 32768 + 8192)
rom[0] = 0x4e; rom[1] = 0x45; rom[2] = 0x53; rom[3] = 0x1a; rom[4] = 2; rom[5] = 1
let n = 0
const nes2 = new J.NES({ onFrame: b => { n++; if (n === 1) console.log('frame len', b.length, 'is Int32Array?', b instanceof Int32Array) }, onAudioSample: () => {} })
nes2.loadROM(rom)
for (let i = 0; i < 3; i++) nes2.frame()
console.log('onFrame calls via frame():', n)
