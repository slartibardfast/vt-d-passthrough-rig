#!/usr/bin/env python3
"""Convert a QEMU screendump (PPM) to PNG, optionally downscaling.

There is no ImageMagick on this host, and reading the guest's screen is the only way
to tell a booted Windows from a frozen boot, so this is a rig tool rather than a
convenience. QEMU writes binary P6 with a maxval of 255; anything else is refused
rather than guessed at, because a misparsed header silently yields a plausible-looking
picture of noise.

Downscaling is nearest-neighbour by integer factor. Nearest-neighbour is the right
choice here: the things being read are 80x25 text screens and XP progress bars, where
a smoothing filter would blur exactly the pixels that carry the information.

Usage: ppm2png.py in.ppm out.png [factor]
"""
import struct
import sys
import zlib


def read_ppm(path):
    with open(path, "rb") as f:
        data = f.read()

    # The header is magic, width, height, maxval, each separated by whitespace, with
    # '#' comments allowed between tokens. Parse tokens rather than splitting on
    # newlines, because QEMU puts width/height/maxval on one line.
    pos = 0
    tokens = []
    while len(tokens) < 4:
        while pos < len(data) and data[pos : pos + 1].isspace():
            pos += 1
        if data[pos : pos + 1] == b"#":
            while pos < len(data) and data[pos : pos + 1] != b"\n":
                pos += 1
            continue
        start = pos
        while pos < len(data) and not data[pos : pos + 1].isspace():
            pos += 1
        tokens.append(data[start:pos])
    magic, width, height, maxval = tokens
    if magic != b"P6":
        raise SystemExit(f"not a binary PPM: magic is {magic!r}")
    if int(maxval) != 255:
        raise SystemExit(f"only maxval 255 is supported, got {maxval!r}")
    pos += 1  # exactly one whitespace byte separates header from pixels
    width, height = int(width), int(height)
    want = width * height * 3
    pixels = data[pos : pos + want]
    if len(pixels) != want:
        raise SystemExit(f"truncated: wanted {want} pixel bytes, found {len(pixels)}")
    return width, height, pixels


def write_png(path, width, height, rgb):
    def chunk(tag, payload):
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw += rgb[y * stride : (y + 1) * stride]

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 6)))
        f.write(chunk(b"IEND", b""))


def downscale(width, height, pixels, factor):
    if factor <= 1:
        return width, height, pixels
    ow, oh = width // factor, height // factor
    out = bytearray(ow * oh * 3)
    stride = width * 3
    for y in range(oh):
        src_row = y * factor * stride
        base = y * ow * 3
        for x in range(ow):
            s = src_row + x * factor * 3
            out[base + x * 3 : base + x * 3 + 3] = pixels[s : s + 3]
    return ow, oh, bytes(out)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    w, h, px = read_ppm(sys.argv[1])
    factor = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    w, h, px = downscale(w, h, px, factor)
    write_png(sys.argv[2], w, h, px)
    print(f"  {sys.argv[1]} -> {sys.argv[2]}  {w}x{h} (factor {factor})")