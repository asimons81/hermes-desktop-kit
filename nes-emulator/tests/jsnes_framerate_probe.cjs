// Why did frame() produce 0 onFrame callbacks? Inspect the timing gate.
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
const nes = new J.NES()
console.log('getFPS:', nes.getFPS())
nes.setFramerate(60)
console.log('getFPS after set 60:', nes.getFPS())
const rom = new Uint8Array(16 + 32768 + 8192)
rom[0] = 0x4e; rom[1] = 0x45; rom[2] = 0x53; rom[3] = 0x1a
rom[4] = 2; rom[5] = 1
let frames = 0, samples = 0
nes.onFrame = buf => { frames++; if (frames === 1) console.log('frame buffer len', buf.length) }
nes.onAudioSample = (l, r) => { samples++ }
nes.loadROM(rom)
// wait a tick so the frame timer allows the first frame
setTimeout(() => {
  for (let i = 0; i < 5; i++) nes.frame()
  console.log('frames after 5 (post-timeout):', frames, 'samples:', samples)
}, 50)
