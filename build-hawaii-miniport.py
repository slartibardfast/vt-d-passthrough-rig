#!/usr/bin/env python3
"""Patch the peer's Hawaii device switch into the x64 miniport.

The stock x64 miniport has `sub eax,0x6640` at file 0x119D4F. The peer's working x32 driver retargets the
same device-ID switch to `sub eax,0x67B0`, so the 0x67B0 device record selects Hawaii's data instead of an
unrelated ASIC's. This builder changes the immediate, recomputes the PE checksum, and writes the candidate.
"""
import argparse
import struct
import sys

SRC = "work/stageE/ati2mtag-s5_accessor_blob.sys"
DST = "work/candidates/ati2mtag-x64-hawaii.sys"
OFF = 0x119D4F
BEFORE = bytes.fromhex("2d 40 66 00 00")
AFTER = bytes.fromhex("2d b0 67 00 00")


def checksum(data):
    d = bytearray(data)
    e = struct.unpack_from("<I", d, 0x3C)[0]
    opt = e + 24
    d[opt + 64:opt + 68] = b"\0\0\0\0"
    if len(d) % 2:
        d += b"\0"
    total = 0
    for i in range(0, len(d), 2):
        total += d[i] | (d[i + 1] << 8)
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return (total + len(data)) & 0xFFFFFFFF


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=SRC)
    ap.add_argument("--out", default=DST)
    args = ap.parse_args()
    data = bytearray(open(args.base, "rb").read())
    if data[OFF:OFF + 5] != BEFORE:
        print("the switch at %#x is %s, not the stock immediate" % (OFF, data[OFF:OFF + 5].hex(" ")),
              file=sys.stderr)
        return 1
    data[OFF:OFF + 5] = AFTER
    e = struct.unpack_from("<I", data, 0x3C)[0]
    opt = e + 24
    struct.pack_into("<I", data, opt + 64, 0)
    struct.pack_into("<I", data, opt + 64, checksum(data))
    with open(args.out, "wb") as f:
        f.write(data)
    print("switch at %#x: %s -> %s; checksum %#010x"
          % (OFF, BEFORE.hex(" "), AFTER.hex(" "), struct.unpack_from("<I", data, opt + 64)[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
