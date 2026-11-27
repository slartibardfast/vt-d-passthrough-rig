#!/usr/bin/env python3
"""Check the combined+switch miniport with the Hawaii descriptor and the descriptor-walk redirect."""
import hashlib
import struct
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "work/candidates/combined-hawaii-table-full.sys"
SIZE = 8525824
SWITCH = bytes.fromhex("2d b0 67 00 00")
FIELDS = ((0x226D24, 16, "max_tile_pipes"), (0x226D44, 4, "max_shader_engines"),
          (0x226D54, 4, "max_backends_per_se"), (0x226D5C, 11, "max_cu_per_sh"),
          (0x226D64, 0x3A000012, "PA_SC_RASTER_CONFIG"), (0x226D84, 0x2E, "PA_SC_RASTER_CONFIG_1"))
# The walk at file 0x8EDF5 loads the table object; the reader at 0xA9BC4 turns that object into
# an entry array. P1 points the walk at the grafted donor tail, P2 keeps the table in rax across
# the abandoned helper, and P3 reads the entries inline after the count.
P1 = (0x8EDF5, bytes.fromhex("4c 8d 2d 2c 3f 26 00"), "walk load")
P2 = (0xA9BCC, bytes.fromhex("4c 89 e8 90 90"), "reader helper")
P3 = (0xA9BD1, bytes.fromhex("48 8d 40 12"), "reader value")
P1_TARGET = 0x303728
SLOT = 0x2E28AB
SLOT_SIZE = 60574
SLOT_SHA256 = "100355efec51d175afc3cfe2e4f0e03a3e6862f659773564c082a9a736cf167e"


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
    for off, dev, label in ((0x1F13D0, 0x67B0, "0x67B0"), (0x1F13F0, 0x67B1, "0x67B1")):
        if struct.unpack_from("<I", data, off + 4)[0] != dev:
            print("%s record device mismatch at %#x" % (label, off))
            return 1
        if struct.unpack_from("<I", data, off + 8)[0] != 1:
            print("%s record flags are not 1 at %#x" % (label, off))
            return 1
    if data[0x119D5B:0x119D5D] != bytes.fromhex("74 79"):
        print("0x67B1 branch at 0x119d5b is %s, expected 74 79"
              % data[0x119D5B:0x119D5D].hex(" "))
        return 1
    for i in range(32):
        flags, value = struct.unpack_from("<II", data, 0x226600 + i * 8)
        if (value >> 6) & 0x1F != 17:
            print("tile entry %d PIPE_CONFIG is %d, expected 17" % (i, (value >> 6) & 0x1F))
            return 1
    for off, want, label in (P1, P2, P3):
        got = data[off:off + len(want)]
        if got != want:
            print("%s at %#x is %s, expected %s" % (label, off, got.hex(" "), want.hex(" ")))
            return 1
    disp = struct.unpack_from("<i", data, P1[0] + 3)[0]
    target = (P1[0] + 0xA00 + 0x10000) + 7 + disp
    if target != P1_TARGET:
        print("walk target is %#x, expected %#x" % (target, P1_TARGET))
        return 1
    slot = data[SLOT:SLOT + SLOT_SIZE]
    if hashlib.sha256(slot).hexdigest() != SLOT_SHA256:
        print("donor slot at %#x is sha256 %s, expected %s"
              % (SLOT, hashlib.sha256(slot).hexdigest(), SLOT_SHA256))
        return 1
    count = struct.unpack_from("<I", slot, 0xEC7D)[0]
    value = struct.unpack_from("<I", slot, 0xEC7D + 0x12)[0]
    if count != 1 or value != 0:
        print("donor tail is count %d value %#x, expected count 1 value 0" % (count, value))
        return 1
    stored, computed = checksum(data)
    if stored != computed:
        print("checksum is %#010x, computed %#010x" % (stored, computed))
        return 1
    print("descriptor 16/4/4/11, raster 0x3A000012/0x2E, switch 0x67B0, records and flags 1, "
          "0x67B1 branch, 32 tile entries 17, "
          "walk -> %#x, donor %s count 1 value 0, checksum %#010x"
          % (target, SLOT_SHA256[:8], stored))
    return 0


if __name__ == "__main__":
    sys.exit(main())
