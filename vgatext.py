#!/usr/bin/env python3
"""Decode a dump of the VGA text console into readable lines.

usage: vgatext.py <b8000.bin> [rows]

The buffer is 80x25 cells of (character, attribute) - character FIRST. Decoding it
with the attribute first, which is how a linear framebuffer is laid out, yields
spaces everywhere, so the order matters.

This is the only visibility in the stock SeaBIOS corners: SeaBIOS ships with
CONFIG_DEBUG_LEVEL=0 so it writes no trace to port 0x402 or serial, and the guest
speaks no serial either. 0xB8000 is what the screen actually shows.
"""
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: vgatext.py <b8000.bin> [rows]", file=sys.stderr)
        return 2
    data = open(sys.argv[1], "rb").read()
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 25
    if not data:
        print("(buffer is empty)")
        return 1
    found = False
    for row in range(rows):
        off = row * 160
        if off + 160 > len(data):
            break
        cells = data[off:off + 160]
        text = "".join(
            chr(cells[i]) if 32 <= cells[i] < 127 else " " for i in range(0, 160, 2)
        ).rstrip()
        if text:
            found = True
            print(f"{row:2} |{text}")
    if not found:
        print(f"(all {len(data)} bytes present but no printable characters: "
              f"the screen is blank or cleared)")
    return 0


if __name__ == "__main__":
    sys.exit(main())