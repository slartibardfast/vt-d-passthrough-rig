#!/usr/bin/env python3
"""Decide whether the ring address is inside either live GPUVM context.

The evidence file is the vmprobe transcript: the BAR 5 base, the busy word,
and one `0xOFFSET 0xVALUE` line per VM context register. The check recomputes
the ring's page and the two context ranges from those values, so a capture that
does not actually show the exclusion fails rather than passing on presence.
"""
import re
import sys

BUSY = 0xA0003028
RING = 0xFF740000
GART_START = 0x155C
GART_END = 0x157C
GART_BASE = 0x153C
VM1_START = 0x1560
VM1_END = 0x1580
VM1_BASE = 0x1540


def parse(path):
    values = {}
    busy = None
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.decode("utf-8", "replace").strip()
            m = re.match(r"^busy\s+0x([0-9a-fA-F]{8})$", line)
            if m:
                busy = int(m.group(1), 16)
                continue
            m = re.match(r"^0x([0-9A-Fa-f]{4})\s+0x([0-9a-fA-F]{8})$", line)
            if m:
                values[int(m.group(1), 16)] = int(m.group(2), 16)
    return busy, values


def main(argv):
    if len(argv) != 2:
        print("usage: check-ring-context.py <evidence.txt>", file=sys.stderr)
        return 2
    busy, v = parse(argv[1])
    if busy != BUSY:
        print("FAIL busy word 0x%s, expected 0x%08x" % ("%08x" % busy if busy is not None else "missing", BUSY))
        return 1
    for off in (GART_START, GART_END, GART_BASE, VM1_START, VM1_END, VM1_BASE, 0xC100):
        if off not in v:
            print("FAIL missing register 0x%04X" % off)
            return 1
    if v[0xC100] != RING:
        print("FAIL CP_RB0_BASE 0x%08x, expected 0x%08x" % (v[0xC100], RING))
        return 1
    page = RING >> 12
    in_gart = v[GART_START] <= page <= v[GART_END]
    vm1_disabled = v[VM1_BASE] == 0 and v[VM1_START] == 0 and v[VM1_END] == 0
    in_vm1 = (not vm1_disabled) and v[VM1_START] <= page <= v[VM1_END]
    print("ring page 0x%x" % page)
    print("context 0 GART pages 0x%x-0x%x: %s" % (v[GART_START], v[GART_END], "inside" if in_gart else "outside"))
    print("context 1 base 0x%x pages 0x%x-0x%x: %s" % (v[VM1_BASE], v[VM1_START], v[VM1_END],
                                                        "disabled" if vm1_disabled else ("inside" if in_vm1 else "outside")))
    if in_gart or in_vm1:
        print("FAIL the ring page is inside a live context, so the VRAM conclusion is wrong")
        return 1
    print("ok: the ring page is outside both live contexts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
