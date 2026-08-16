// What is the exact type of the onFrame buffer? (drives the canvas write path)
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
const nes = new J.NES({
  onFrame: buf => {
    console.log('buffer ctor:', buf.constructor.name)
    console.log('length:', buf.length, 'byteLength:', buf.byteLength, 'BYTES_PER_ELEMENT:', buf.BYTES_PER_ELEMENT)
    console.log('byte0-3:', buf[0].toString(16), buf[1].toString(16), buf[2].toString(16), buf[3].toString(16))
    const u8 = new Uint8Array(buf.buffer, buf.byteOffset, 4)
    console.log('as bytes:', Array.from(u8).map(b => b.toString(16)).join(' '))
  },
  onAudioSample: () => {}
})
const rom = new Uint8Array(16 + 32768 + 8192)
rom[0] = 0x4e; rom[1] = 0x45; rom[2] = 0x53; rom[3] = 0x1a; rom[4] = 2; rom[5] = 1
nes.loadROM(rom)
nes.frame()
