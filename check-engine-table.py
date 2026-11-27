#!/usr/bin/env python3
"""Check the combined+switch miniport with the Hawaii descriptor table in place."""
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "work/candidates/combined-hawaii-table.sys"
SIZE = 8525824
SWITCH = bytes.fromhex("2d b0 67 00 00")
FIELDS = ((0x226D24, 16, "max_tile_pipes"), (0x226D44, 4, "max_shader_engines"),
          (0x226D54, 4, "max_backends_per_se"), (0x226D5C, 11, "max_cu_per_sh"),
          (0x226D64, 0x3A000012, "PA_SC_RASTER_CONFIG"), (0x226D84, 0x2E, "PA_SC_RASTER_CONFIG_1"))


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
    if data[0x119D4F:0x119D54] != SWITCH:
        print("device switch is %s, expected %s" % (data[0x119D4F:0x119D54].hex(" "), SWITCH.hex(" ")))
        return 1
    for off, want, label in FIELDS:
        got = struct.unpack_from("<I", data, off)[0]
        if got != want:
            print("%s at %#x is %#x, expected %#x" % (label, off, got, want))
            return 1
    stored, computed = checksum(data)
    if stored != computed:
        print("checksum is %#010x, computed %#010x" % (stored, computed))
        return 1
    print("descriptor: 16/4/4/11, raster 0x3A000012/0x2E, switch 0x67B0, checksum %#010x" % stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
