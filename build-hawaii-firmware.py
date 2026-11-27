#!/usr/bin/env python3
"""Apply the x64 firmware-descriptor patch and its DIR64 relocation to the combined candidate."""
import struct
import sys

PATH = "work/candidates/combined-hawaii-table.sys"
SITE = 0x24A0D
CAVE = 0x24A30
RELOC_RAW = 0x808800
RELOC_SIZE = 0x19000
SITE_BEFORE = bytes.fromhex("48 8d 8f 24 03 00 00")
SITE_AFTER = bytes.fromhex("e9 1e 00 00 00 90 90")
CAVE_BYTES = bytes.fromhex("48 b9 28 37 30 00 00 00 00 00 e9 d5 ff ff ff")
PAGE = 0x25000
ENTRY = 0xA432


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
    d = bytearray(open(PATH, "rb").read())
    if d[SITE:SITE + 7] != SITE_BEFORE:
        print("site is %s" % d[SITE:SITE + 7].hex(" "), file=sys.stderr)
        return 1
    if d[CAVE:CAVE + 0x10] != b"\xcc" * 0x10:
        print("cave is not sixteen int3 bytes", file=sys.stderr)
        return 1
    d[SITE:SITE + 7] = SITE_AFTER
    d[CAVE:CAVE + 15] = CAVE_BYTES

    off = RELOC_RAW
    while off + 8 <= RELOC_RAW + RELOC_SIZE:
        page, bsize = struct.unpack_from("<II", d, off)
        if page == 0x251000:
            break
        off += bsize
    else:
        print("no 0x251000 relocation block", file=sys.stderr)
        return 1
    d[off:off] = struct.pack("<II", PAGE, 10) + struct.pack("<H", ENTRY)

    pe = struct.unpack_from("<I", d, 0x3C)[0]
    opt = pe + 24
    sizeofopt = struct.unpack_from("<H", d, pe + 20)[0]
    so = opt + sizeofopt
    hdr = so + 17 * 40
    vsz = struct.unpack_from("<I", d, hdr + 8)[0]
    rsz = struct.unpack_from("<I", d, hdr + 16)[0]
    struct.pack_into("<I", d, hdr + 8, vsz + 10)
    struct.pack_into("<I", d, hdr + 16, rsz + 10)
    init = struct.unpack_from("<I", d, opt + 8)[0]
    struct.pack_into("<I", d, opt + 8, init + 10)
    struct.pack_into("<I", d, opt + 64, 0)
    struct.pack_into("<I", d, opt + 64, checksum(d))
    open(PATH, "wb").write(d)
    print("site %s; cave %s; reloc block page %#x entry %#x; size %d; checksum %#010x"
          % (SITE_AFTER.hex(" "), CAVE_BYTES.hex(" "), PAGE, ENTRY, len(d),
             struct.unpack_from("<I", d, opt + 64)[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
