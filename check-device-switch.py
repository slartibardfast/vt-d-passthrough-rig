#!/usr/bin/env python3
"""Check the x64 miniport candidate for the peer's Hawaii device switch.

The peer's working x32 driver retargets the miniport's device-ID switch from an unrelated ID to
`sub eax,0x67B0`, and corrects the device table for Hawaii. The stock x64 binary has
`sub eax,0x6640` at file 0x119D4F and the 0x67B0 record at 0x1F13D0. This check asserts the patched
immediate, the record, and a PE checksum that matches the patched bytes, so the candidate is auditable
before a boot.
"""
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "work/candidates/ati2mtag-x64-hawaii.sys"
SIZE = 8525824
SWITCH = bytes.fromhex("2d b0 67 00 00")
RECORD = bytes.fromhex("b0 67")


def checksum(data):
    d = bytearray(data)
    e = struct.unpack_from("<I", d, 0x3C)[0]
    opt = e + 24
    stored = struct.unpack_from("<I", d, opt + 64)[0]
    d[opt + 64:opt + 68] = b"\0\0\0\0"
    if len(d) % 2:
        d += b"\0"
    total = 0
    for i in range(0, len(d), 2):
        total += d[i] | (d[i + 1] << 8)
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    return stored, (total + len(data)) & 0xFFFFFFFF


def main():
    try:
        data = open(PATH, "rb").read()
    except OSError as e:
        print("no candidate at %s (%s)" % (PATH, e))
        return 1
    if len(data) != SIZE:
        print("candidate is %d bytes, expected %d" % (len(data), SIZE))
        return 1
    got = data[0x119D4F:0x119D54]
    if got != SWITCH:
        print("switch at 0x119D4F is %s, expected %s" % (got.hex(" "), SWITCH.hex(" ")))
        return 1
    if data[0x1F13D0 + 4:0x1F13D0 + 6] != RECORD:
        print("the device record at 0x1F13D0 does not name 0x67B0")
        return 1
    stored, computed = checksum(data)
    if stored != computed:
        print("checksum is %#010x, computed %#010x" % (stored, computed))
        return 1
    print("device switch: sub eax,0x67B0 at 0x119D4F; 0x67B0 record at 0x1F13D0; checksum %#010x"
          % stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
