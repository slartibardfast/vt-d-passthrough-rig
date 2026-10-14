#!/usr/bin/env python3
"""SZDD (Microsoft compress.exe/expand.exe "A" variant) codec.

Domain constants (the format is the contract - Microsoft fixes them):
  - 4096-byte history ring, initialised to spaces (0x20)
  - the ring position starts at 4096-16
  - a control byte covers 8 tokens; set bit = literal, clear bit =
    a 2-byte back-reference {low byte, high nibble}: an absolute ring
    start plus a (nibble + 3) copy length

Spec: specs/szdd.allium (allium check/analyse/plan gate this).
"""

import sys, struct

WINDOW_SIZE = 4096
WINDOW_START = 4080            # 4096 - 16
MIN_REFERENCE_LENGTH = 3
MAX_REFERENCE_LENGTH = 18
GROUP_ITEMS = 8

SZDD_MAGIC = b"SZDD\x88\xf0\x27\x33"
MISSING_CHAR = 0x41            # 'A' - the variant Windows' expand.exe writes


def szdd_expand(data: bytes) -> bytes:
    """Decode one SZDD-A stream. Raises ValueError for foreign input."""
    if data[:4] != b"SZDD":
        raise ValueError("not SZDD")
    ring = bytearray(b"\x20" * WINDOW_SIZE)
    r = WINDOW_START
    out = bytearray()
    i = 14
    end = len(data)
    while i < end:
        ctrl = data[i]; i += 1
        for bit in range(GROUP_ITEMS):
            if i >= end:
                break
            if ctrl & (1 << bit):
                ch = data[i]; i += 1
                out.append(ch)
                ring[r] = ch
                r = (r + 1) & (WINDOW_SIZE - 1)
            else:
                if i + 2 > end:
                    break
                b1, b2 = data[i], data[i + 1]; i += 2
                src = b1 | ((b2 & 0xF0) << 4)
                length = (b2 & 0x0F) + MIN_REFERENCE_LENGTH
                for k in range(length):
                    ch = ring[(src + k) & (WINDOW_SIZE - 1)]
                    out.append(ch)
                    ring[r] = ch
                    r = (r + 1) & (WINDOW_SIZE - 1)
    return bytes(out)


def szdd_compress(payload: bytes, missing: int = MISSING_CHAR) -> bytes:
    """Encode payload as an SZDD-A stream that szdd_expand decodes back
    byte-for-byte (the Roundtrip invariant)."""
    # the on-disk header is 14 bytes: magic(8) + missing char + the size
    # low half + filler (measured against real .sy_ files; the decoder
    # starts the token stream at byte 14)
    out = bytearray(SZDD_MAGIC + bytes([missing])
                    + bytes([len(payload) & 0xFF, (len(payload) >> 8) & 0xFF])
                    + b"\x00\x00\x00")
    ring = bytearray(b"\x20" * WINDOW_SIZE)
    r = WINDOW_START
    pos = 0
    n = len(payload)

    def emit_token(token):
        group.append(token)
        if len(group) == GROUP_ITEMS:
            flush_group()

    def flush_group():
        if not group:
            return
        ctrl = 0
        body = bytearray()
        for i, tok in enumerate(group):
            if tok[0] == 0:               # literal
                ctrl |= 1 << i
                body.append(tok[1])
            else:                          # back-reference: 2 bytes
                body += tok[1]
        out.append(ctrl)
        out.extend(body)
        group.clear()

    group = []

    while pos < n:
        # greedy longest match against the ring, emulating the decoder's
        # read/write interleave exactly
        best_len = 0
        best_src = 0
        remaining = n - pos
        if remaining >= MIN_REFERENCE_LENGTH:
            max_len = min(MAX_REFERENCE_LENGTH, remaining)
            tmp = bytearray(ring)
            for src in range(WINDOW_SIZE):
                if payload[pos] != tmp[src]:
                    continue
                length = 0
                tr = r
                for k in range(max_len):
                    ch = tmp[(src + k) & (WINDOW_SIZE - 1)]
                    if ch != payload[pos + k]:
                        break
                    tmp[tr] = ch
                    tr = (tr + 1) & (WINDOW_SIZE - 1)
                    length += 1
                if length > best_len:
                    best_len = length
                    best_src = src
                    if best_len == max_len:
                        break
        if best_len >= MIN_REFERENCE_LENGTH:
            b2 = ((best_src >> 4) & 0xF0) | (best_len - MIN_REFERENCE_LENGTH)
            emit_token((1, bytes([best_src & 0xFF, b2])))
            for k in range(best_len):
                ch = payload[pos + k]
                ring[r] = ch
                r = (r + 1) & (WINDOW_SIZE - 1)
            pos += best_len
        else:
            ch = payload[pos]
            emit_token((0, ch))
            ring[r] = ch
            r = (r + 1) & (WINDOW_SIZE - 1)
            pos += 1
    flush_group()
    return bytes(out)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    data = open(src, "rb").read()
    if src.endswith(".sz") or "--compress" in sys.argv:
        out = szdd_compress(data)
    else:
        out = szdd_expand(data)
    open(dst, "wb").write(out)
    print(f"{src}: {len(data)} -> {dst}: {len(out)} bytes, head {out[:8].hex(' ')}")
