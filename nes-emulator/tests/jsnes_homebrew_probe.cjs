// Run the self-authored homebrew ROM through jsnes: frames, audio, non-blank px.
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
const rom = fs.readFileSync(process.argv[2] || 'homebrew.nes')
const bytes = new Uint8Array(rom)
let frames = 0, samples = 0, nonZero = 0
const nes = new J.NES({
  onFrame: buf => { frames++; for (let i = 0; i < buf.length; i++) if (buf[i] & 0xffffff) nonZero++ },
  onAudioSample: () => { samples++ }
})
nes.loadROM(bytes)
for (let i = 0; i < 600; i++) nes.frame()
console.log('frames:', frames, 'audioSamples:', samples, 'nonZeroPixels:', nonZero)
