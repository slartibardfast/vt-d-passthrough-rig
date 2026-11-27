#!/usr/bin/env python3
"""Diff the engine-idle tests in the 32-bit and 64-bit ati2cqag builds.

Both builds carry a pair of waits. One tests GUI_ACTIVE (0x80000000), the other CP_BUSY (0x20000000),
each against the register at aperture offset 0x8010. The x64 build loads its adapter base from a
different field offset, which is a structure change between the builds and not a change in the wait.

The script finds the `add ...,0x8010` sites in each .text, reads the adapter load before each one and the
mask after it, and prints the pair. It exits 0 only when both builds carry the same register offset and
the same two masks, so the plan's question ("did the x64 build start polling where the x86 one did not?")
is answered by the check rather than by prose.

Usage: compare-idle-tests.py [--x86 PATH] [--x64 PATH]
"""
import argparse
import hashlib
import struct
import sys

ADD = {32: bytes.fromhex("05 10 80 00 00"), 64: bytes.fromhex("48 05 10 80 00 00")}
MASKS = {0x80000000: "GUI_ACTIVE", 0x20000000: "CP_BUSY"}


def image(path):
    data = open(path, "rb").read()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert data[pe:pe + 4] == b"PE\0\0", path
    machine = struct.unpack_from("<H", data, pe + 4)[0]
    bits = 64 if machine == 0x8664 else 32
    opt = pe + 24
    base = struct.unpack_from("<Q" if bits == 64 else "<I", data, opt + (24 if bits == 64 else 28))[0]
    nsec = struct.unpack_from("<H", data, pe + 6)[0]
    so = opt + struct.unpack_from("<H", data, pe + 20)[0]
    secs = []
    for i in range(nsec):
        o = so + i * 40
        name = data[o:o + 8].rstrip(b"\0").decode()
        vsz, va, rsz, ra = struct.unpack_from("<IIII", data, o + 8)
        secs.append((name, va, vsz, ra, rsz))
    return data, bits, base, secs


def text_rva(data, secs, rva):
    for name, va, vsz, ra, rsz in secs:
        if name == ".text" and va <= rva < va + max(vsz, rsz):
            return ra + (rva - va)
    raise SystemExit("rva %#x is not in .text" % rva)


def find_sites(path):
    data, bits, base, secs = image(path)
    _, va, vsz, ra, rsz = next(s for s in secs if s[0] == ".text")
    blob = data[ra:ra + rsz]
    pattern = ADD[bits]
    sites = []
    off = blob.find(pattern)
    while off != -1:
        rva = va + off
        before = blob[max(0, off - 0x20):off]
        after = blob[off:off + 0x40]
        adapter = None
        if bits == 32:
            for i in range(len(before) - 5):
                if before[i] == 0x8B and (before[i + 1] >> 6) == 2:
                    disp = struct.unpack_from("<I", before, i + 2)[0]
                    if disp in (0x100, 0x138):
                        adapter = disp
        else:
            for i in range(len(before) - 6):
                if before[i] in (0x48, 0x49, 0x4C) and before[i + 1] == 0x8B and (before[i + 2] >> 6) == 2:
                    disp = struct.unpack_from("<I", before, i + 3)[0]
                    if disp in (0x100, 0x138):
                        adapter = disp
        mask = None
        for value in MASKS:
            if struct.pack("<I", value) in after:
                mask = value
        if adapter is not None and mask is not None:
            sites.append((rva, adapter, mask))
        off = blob.find(pattern, off + 1)
    return path, bits, base, sites


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x86", default="work/x86-twin/ati2cqag.dll")
    ap.add_argument("--x64", default="work/package/ati2cqag.dll")
    ap.add_argument("--x86-sha", default="4e343ea6b54e54a99dcf959989796640dbc1dc3d6ba558ddc00816ca2f6ea40b")
    ap.add_argument("--x64-sha", default="160a0e5511a437d14e29cacbd8161dcc027e5e667511d507934514b38aa6b778")
    args = ap.parse_args()

    for path, expected in ((args.x86, args.x86_sha), (args.x64, args.x64_sha)):
        got = hashlib.sha256(open(path, "rb").read()).hexdigest()
        if got != expected:
            print("%s: sha256 %s, expected %s" % (path, got, expected))
            return 1

    rows = []
    for path in (args.x86, args.x64):
        path, bits, base, sites = find_sites(path)
        if len(sites) != 2:
            print("%s: found %d idle-test site(s), expected 2" % (path, len(sites)))
            return 1
        masks = sorted(m for _, _, m in sites)
        if masks != sorted(MASKS):
            print("%s: masks %s, expected both %s" % (path, [hex(m) for m in masks], [hex(m) for m in MASKS]))
            return 1
        for rva, adapter, mask in sites:
            rows.append((bits, path, rva, adapter, mask))

    for bits, path, rva, adapter, mask in rows:
        print("%2d-bit %-34s add 0x8010 at RVA %#07x, adapter field +%#05x, mask %#010x %s"
              % (bits, path, rva, adapter, mask, MASKS[mask]))

    x86 = sorted(r for r in rows if r[0] == 32)
    x64 = sorted(r for r in rows if r[0] == 64)
    if [m for _, _, _, _, m in x86] != [m for _, _, _, _, m in x64]:
        print("the two builds do not carry the same mask pair")
        return 1
    if {a for _, _, _, a, _ in x86} == {a for _, _, _, a, _ in x64}:
        print("both builds load the adapter base from the same field offset")
    else:
        print("the adapter field offset differs between the builds (%#x vs %#x); the wait does not"
              % (x86[0][3], x64[0][3]))
    print("both builds wait on aperture offset 0x8010: the x64 idle test is not novel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
