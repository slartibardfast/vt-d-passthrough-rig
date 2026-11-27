#!/usr/bin/env python3
"""Check the combined+switch miniport with the Hawaii descriptor and the firmware code patch."""
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "work/candidates/combined-hawaii-table.sys"
SIZE = 8525834
SWITCH = bytes.fromhex("2d b0 67 00 00")
FIELDS = ((0x226D24, 16, "max_tile_pipes"), (0x226D44, 4, "max_shader_engines"),
          (0x226D54, 4, "max_backends_per_se"), (0x226D5C, 11, "max_cu_per_sh"),
          (0x226D64, 0x3A000012, "PA_SC_RASTER_CONFIG"), (0x226D84, 0x2E, "PA_SC_RASTER_CONFIG_1"))
SITE = (0x24A0D, bytes.fromhex("e9 1e 00 00 00 90 90"), "firmware site")
CAVE = (0x24A30, bytes.fromhex("48 b9 28 37 30 00 00 00 00 00 e9 d5 ff ff ff"), "firmware cave")


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


def reloc_has(data, page, entry):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    opt = pe + 24
    so = opt + struct.unpack_from("<H", data, pe + 20)[0]
    for i in range(struct.unpack_from("<H", data, pe + 6)[0]):
        o = so + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode()
        if name != ".reloc":
            continue
        vsz, va, rsz, ra = struct.unpack_from("<IIII", data, o + 8)
        off = ra
        while off + 8 <= ra + rsz:
            blk, size = struct.unpack_from("<II", data, off)
            if size == 0:
                break
            if blk == page:
                return any(struct.unpack_from("<H", data, off + 8 + j * 2)[0] == entry
                           for j in range((size - 8) // 2))
            off += size
    return False


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
    for off, want, label in (SITE, CAVE):
        got = data[off:off + len(want)]
        if got != want:
            print("%s at %#x is %s" % (label, off, got.hex(" ")))
            return 1
    if not reloc_has(data, 0x25000, 0xA432):
        print("no DIR64 relocation at page 0x25000 offset 0x432")
        return 1
    stored, computed = checksum(data)
    if stored != computed:
        print("checksum is %#010x, computed %#010x" % (stored, computed))
        return 1
    print("descriptor 16/4/4/11, raster 0x3A000012/0x2E, switch 0x67B0, firmware site+cave+DIR64, checksum %#010x"
          % stored)
    return 0


if __name__ == "__main__":
    sys.exit(main())
