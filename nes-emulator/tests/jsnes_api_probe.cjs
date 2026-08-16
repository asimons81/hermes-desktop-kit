// Correct jsnes NES API: onFrame/onAudioSample are CONSTRUCTOR options, not
// assignable instance props (the frame loop calls this.ui.writeFrame = opts.onFrame).
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
const rom = new Uint8Array(16 + 32768 + 8192)
rom[0] = 0x4e; rom[1] = 0x45; rom[2] = 0x53; rom[3] = 0x1a
rom[4] = 2; rom[5] = 1
let frames = 0, samples = 0, frameLen = 0
const nes = new J.NES({
  onFrame: buf => { frames++; frameLen = buf.length },
  onAudioSample: (l, r) => { samples++ }
})
nes.loadROM(rom)
for (let i = 0; i < 100; i++) nes.frame()
console.log('frames after 100:', frames, 'frame buffer len:', frameLen, 'audio samples:', samples)
console.log('toJSON keys:', Object.keys(nes.toJSON()))
// save-state roundtrip
const snap = JSON.stringify(nes.toJSON())
const nes2 = new J.NES({ onFrame() {}, onAudioSample() {} })
nes2.fromJSON(JSON.parse(snap))
console.log('fromJSON roundtrip ok, snap bytes:', snap.length)
