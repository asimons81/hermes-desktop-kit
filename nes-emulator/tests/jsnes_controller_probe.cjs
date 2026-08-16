// Probe the vendored jsnes Controller implementation (real semantics, not regex).
const fs = require('fs');
const src = fs.readFileSync(__dirname + '/../jsnes.min.js', 'utf8');
(0, eval)(src);
const C = globalThis.jsnes.Controller;
console.log('=== Controller constants ===');
for (const k of Object.keys(C)) console.log(k, '=', C[k]);
console.log('=== buttonDown source ===');
console.log(String(C.prototype.buttonDown));
console.log('=== buttonUp source ===');
console.log(String(C.prototype.buttonUp));
console.log('=== poll source ===');
console.log(String(C.prototype.poll));
console.log('=== read source ===');
console.log(String(C.prototype.read));
console.log('=== ctor source ===');
console.log(String(C.prototype.constructor).slice(0, 1200));
