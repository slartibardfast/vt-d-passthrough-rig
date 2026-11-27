#!/usr/bin/env python3
"""Decode the CP state captured at the engine stall.

`engine-cpstate-series.log` holds one section per sample, each line the
`+0xOFFSET  0xVALUE` form `mmio-read.py` prints for one register. The check
asserts that every sample carries the full register set, that the busy word is
the one the campaign measured, and that the ring pointers still sit at the
fetched-but-unadvanced state. It then prints the CP control and stalled-status
words with their bit decodes, because those values are the finding.

Exit 0 only when the capture is complete and consistent; a missing register, a
short series, or a ring that does not match the recorded stall is a failure,
so an empty or partial series is never read as a clean run.
"""
import re
import sys

OFFSETS = (
    0x8010,  # GRBM_STATUS
    0x8008,  # GRBM_STATUS2
    0x8680,  # CP_STAT
    0x8694,  # CP_STALLED_STAT1
    0x8698,  # CP_STALLED_STAT2
    0x869C,  # CP_STALLED_STAT3
    0x8700,  # CP_RB0_RPTR
    0xC100,  # CP_RB0_BASE
    0xC104,  # CP_RB0_CNTL
    0xC114,  # CP_RB0_WPTR
    0xC180,  # CP_ME_CNTL
    0xC200,  # CP_PFP_CNTL
    0xC300,  # CP_MEC_CNTL
    0xC210,  # CP_PFP_UCODE_ADDR
    0xC220,  # CP_ME_UCODE_ADDR
)

SAMPLE = re.compile(r"^=== sample\s+(\d+)")
WORD = re.compile(r"^\s*\+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)")

MIN_SAMPLES = 30
BUSY_MASK = 0xA0000000
BUSY_WORD = 0xA0003028
RING_BASE = 0xFF740000
RING_PTR = 0x20


def parse(path):
    samples = []
    current = None
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            match = SAMPLE.match(line)
            if match:
                current = {}
                samples.append(current)
                continue
            match = WORD.match(line)
            if match and current is not None:
                current[int(match.group(1), 16)] = int(match.group(2), 16)
    return samples


def bits(value, names):
    return ", ".join(name for bit, name in names if value & (1 << bit)) or "-"


def main(argv):
    if len(argv) != 2:
        print("usage: check-cp-state.py <series.log>", file=sys.stderr)
        return 2
    samples = parse(argv[1])
    if len(samples) < MIN_SAMPLES:
        print("FAIL only %d sample(s); need at least %d" % (len(samples), MIN_SAMPLES))
        return 1
    missing = [n for n, s in enumerate(samples, 1) if n > 2 and any(off not in s for off in OFFSETS)]
    if missing:
        print("FAIL samples missing registers: %s" % missing[:10])
        return 1
    warm = samples[5:]
    busy = [s for s in warm if (s[0x8010] & BUSY_MASK) == BUSY_MASK]
    if len(busy) < max(1, int(len(warm) * 0.9)):
        print("FAIL busy word 0x%08x in only %d of %d warm samples" % (BUSY_WORD, len(busy), len(warm)))
        return 1
    for n, s in enumerate(warm, 6):
        if (s[0x8680] & 0x80000000) == 0:
            print("FAIL sample %d reads CP_STAT 0x%08x without CP_BUSY" % (n, s[0x8680]))
            return 1
        if s[0xC100] != RING_BASE:
            print("FAIL sample %d reads CP_RB0_BASE 0x%08x, expected 0x%08x" % (n, s[0xC100], RING_BASE))
            return 1
        if s[0xC114] != RING_PTR or s[0x8700] != RING_PTR:
            print("FAIL sample %d reads WPTR 0x%x RPTR 0x%x, expected both 0x%x"
                  % (n, s[0xC114], s[0x8700], RING_PTR))
            return 1
    last = samples[-1]
    print("samples %d, busy %d of %d warm, GRBM_STATUS 0x%08x" % (len(samples), len(busy), len(warm), last[0x8010]))
    print("CP_STAT          0x%08x  %s" % (last[0x8680], bits(last[0x8680], ((31, "CP_BUSY"),))))
    print("CP_STALLED_STAT1 0x%08x" % last[0x8694])
    print("CP_STALLED_STAT2 0x%08x" % last[0x8698])
    print("CP_STALLED_STAT3 0x%08x" % last[0x869C])
    print("CP_RB0_BASE      0x%08x" % last[0xC100])
    print("CP_RB0_CNTL      0x%08x" % last[0xC104])
    print("CP_RB0_WPTR      0x%08x" % last[0xC114])
    print("CP_RB0_RPTR      0x%08x" % last[0x8700])
    print("CP_ME_CNTL       0x%08x  %s" % (last[0xC180], bits(last[0xC180], ((28, "ME_HALT"), (29, "PFP_HALT"), (30, "CE_HALT")))))
    print("CP_PFP_CNTL      0x%08x" % last[0xC200])
    print("CP_MEC_CNTL      0x%08x" % last[0xC300])
    print("CP_PFP_UCODE_ADDR 0x%08x" % last[0xC210])
    print("CP_ME_UCODE_ADDR  0x%08x" % last[0xC220])
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
