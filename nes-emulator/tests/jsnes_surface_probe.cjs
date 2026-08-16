// Enumerate the jsnes v2.1.0 surface we rely on in plugin.js.
const fs = require('fs')
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8')
;(0, eval)(src)
const J = globalThis.jsnes
console.log('exports:', Object.keys(J))
console.log('NES proto methods:', Object.getOwnPropertyNames(J.NES.prototype).filter(n => !n.startsWith('_')))
console.log('Controller buttons:', Object.keys(J.Controller).filter(k => k.startsWith('BUTTON')))
console.log('Browser?', typeof J.Browser)
