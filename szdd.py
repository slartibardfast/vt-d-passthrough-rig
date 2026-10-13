#!/usr/bin/env python3
"""SZDD (Microsoft compress.exe/expand.exe 'A' variant) decompressor.

Header: "SZDD" + 88 F0 27 33 + 1 byte (the missing char, filename-only).
LZSS: 4096-byte ring initialised to 0x20, ring position starts at
4096-16; control byte = 8 groups, set bit = literal, clear bit = a
2-byte code {low byte, high nibble}: absolute ring start, length
(high nibble->low nibble field) + 3.
"""
import sys, struct

def szdd_expand(data: bytes) -> bytes:
    if data[:4] != b"SZDD":
        raise ValueError("not SZDD")
    ring = bytearray(b"\x20" * 4096)
    r = 4096 - 16
    out = bytearray()
    i = 14
    end = len(data)
    while i < end:
        ctrl = data[i]; i += 1
        for bit in range(8):
            if i >= end:
                break
            if ctrl & (1 << bit):
                ch = data[i]; i += 1
                out.append(ch)
                ring[r] = ch
                r = (r + 1) & 4095
            else:
                if i + 2 > end:
                    break
                b1, b2 = data[i], data[i + 1]; i += 2
                src = b1 | ((b2 & 0xF0) << 4)
                length = (b2 & 0x0F) + 3
                for k in range(length):
                    ch = ring[(src + k) & 4095]
                    out.append(ch)
                    ring[r] = ch
                    r = (r + 1) & 4095
    return bytes(out)

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    out = szdd_expand(data)
    open(dst, "wb").write(out)
    print(f"{src}: {len(data)} -> {dst}: {len(out)} bytes, head {out[:8].hex(' ')}")
