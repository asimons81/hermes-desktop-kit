#!/usr/bin/env python3
"""Generate a legal, self-authored homebrew test ROM (solid-color screen).

Not a commercial ROM; no bundled ROM ships in the plugin — this lives under
/tmp for the isolated runtime test only. Standard nesdev "hello world"
pattern: init PPU, load a palette, enable background rendering, spin.
"""
from __future__ import annotations

import struct
from pathlib import Path


def rom_bytes() -> bytes:
    prg = bytearray(0x4000)  # 16 KB PRG (NROM-128)

    # code at $8000 (CPU $8000-$BFFF; reset vector at $FFFC)
    code = [
        0x78, 0xD8,                    # sei / cld
        0xA2, 0x40, 0x8E, 0x17, 0x40,  # ldx #$40; stx $4017
        0xA2, 0xFF, 0x9A,              # ldx #$FF; txs
        0xE8,                          # inx (x = 0)
        0x8E, 0x00, 0x20,              # stx $2000 (PPUCTRL = 0)
        0x8E, 0x01, 0x20,              # stx $2001 (PPUMASK = 0)
        0x8E, 0x10, 0x40,              # stx $4010 (DMC = 0)
    ]
    # wait two vblanks
    code += [0x2C, 0x02, 0x20, 0x10, 0xFB]  # vw1: bit $2002; bpl vw1
    code += [0x2C, 0x02, 0x20, 0x10, 0xFB]  # vw2: bit $2002; bpl vw2
    # load palette ($3F00..$3F1F)
    code += [
        0xA9, 0x3F, 0x8D, 0x06, 0x20,  # lda #$3F; sta $2006
        0xA9, 0x00, 0x8D, 0x06, 0x20,  # lda #$00; sta $2006
        0xA2, 0x00,                    # ldx #$00
    ]
    # pal loop
    pal_loop = [
        0xBD, 0x00, 0x80,              # lda palette,x
        0x8D, 0x07, 0x20,              # sta $2007
        0xE8,                          # inx
        0xE0, 0x20,                    # cpx #$20
        0xD0, 0xF5,                    # bne pal_loop (back 11 bytes)
    ]
    code += pal_loop
    # enable background rendering
    code += [
        0xA9, 0x1E, 0x8D, 0x01, 0x20,  # lda #$1E; sta $2001
        0x4C, 0x00, 0x80,              # jmp $8000 (spin)
    ]
    # palette data (32 bytes) follows code
    palette = bytes([
        0x0F, 0x30, 0x10, 0x20,  0x0F, 0x30, 0x10, 0x20,
        0x0F, 0x30, 0x10, 0x20,  0x0F, 0x30, 0x10, 0x20,
        0x0F, 0x30, 0x10, 0x20,  0x0F, 0x30, 0x10, 0x20,
        0x0F, 0x30, 0x10, 0x20,  0x0F, 0x30, 0x10, 0x20,
    ])
    prg[0:len(code)] = bytes(code)
    prg[0x100:0x100 + len(palette)] = palette  # palette at $8100 -> lda $8000 wrong? see below
    # NOTE: lda palette,x uses $8000 base; place palette right after code instead.
    prg[len(code):len(code) + len(palette)] = palette

    # reset vector at $FFFC -> $8000
    prg[0x3FFC] = 0x00
    prg[0x3FFD] = 0x80

    chr_rom = bytearray(0x2000)  # 8 KB CHR (blank)
    # a simple 8x8 tile in the first CHR tile so a colored pixel exists
    for row in range(8):
        chr_rom[row * 16] = 0xFF
        chr_rom[row * 16 + 8] = 0x00

    header = b"NES\x1a" + bytes([1, 1, 0x00, 0x00]) + bytes(8)
    return header + bytes(prg) + bytes(chr_rom)


if __name__ == "__main__":
    out = Path("/tmp/nes-test/homebrew.nes")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(rom_bytes())
    print("wrote", out, out.stat().st_size, "bytes")
