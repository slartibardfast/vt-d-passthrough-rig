#!/usr/bin/env python3
"""Read a register out of the passed-through card while the guest runs.

The engine-idle wait is a poll of one status word, and the word lives in the card, not in guest RAM:
`ati2cqag` reads `[adapter+0x138] + 0x8010` and loops while the bit is clear. A dump cannot carry a
device register, and a kernel breakpoint did not surface to this rig's collector, so the value is read
where it is written: the card's register aperture, which QEMU exposes to the monitor at a guest-physical
address and forwards to the hardware.

The address is not assumed. `info mtree` names the guest-physical range QEMU gives each BAR, and this
reads inside the range it found for the named BAR, so a wrong bar is a refusal rather than a plausible
number from the wrong place.

A zero is what an absent register and a dead read path both look like. On 2026-11-18 every read of BAR 0
and BAR 2 at 0x8010, 0x6000 and 0x5000 returned zero while the guest ran and the driver hung, so a zero
from this tool is reported as a zero and never as a register that holds nothing.

Usage: mmio-read.py [--bar 2] [--offset 0x8010] [--count 8] [--monitor SOCK] [--out FILE]
"""
import argparse
import json
import os
import re
import socket
import sys
import time

RIG = os.path.dirname(os.path.abspath(__file__))
LINE = re.compile(r"^([0-9a-f]{8,16}):\s+(.*)$")
RANGE = re.compile(r"^\s*([0-9a-f]{8,16})-([0-9a-f]{8,16})\s+.*BAR\s+(\d+)\s*$", re.I)


class Mon:
    """One monitor connection, commands read until the prompt returns."""

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(60)
        self.s.connect(path)
        self._until(b"(qemu)", 10)

    def _until(self, needle, ceiling):
        buf = b""
        end = time.monotonic() + ceiling
        while time.monotonic() < end:
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            if needle in buf[-200:]:
                break
        return buf

    def cmd(self, command, ceiling=60):
        self.s.sendall(command.encode() + b"\n")
        raw = self._until(b"(qemu)", ceiling).decode(errors="replace")
        raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).replace("\r", "")
        head, sep, tail = raw.partition("\n")
        return tail if sep else ""

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def bar_range(mon, bar):
    """The guest-physical range QEMU gives this BAR, from the monitor's own map."""
    text = mon.cmd("info mtree")
    hits = []
    for line in text.splitlines():
        m = RANGE.match(line)
        if m and int(m.group(3)) == bar:
            hits.append((int(m.group(1), 16), int(m.group(2), 16), line.strip()))
    return hits


def distinct_ranges(hits):
    """The map lists each BAR once per mapping layer; the address is what repeats."""
    seen, out = set(), []
    for lo, hi, line in hits:
        if (lo, hi) in seen:
            continue
        seen.add((lo, hi))
        out.append((lo, hi, line))
    return out


def read_words(mon, addr, count):
    text = mon.cmd("xp/%dxw 0x%x" % (count, addr))
    words = []
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        for tok in m.group(2).split():
            try:
                words.append(int(tok, 16) & 0xFFFFFFFF)
            except ValueError:
                pass
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bar", type=int, default=2,
                    help="which BAR the register block is expected in (default 2, the MMIO bar)")
    ap.add_argument("--offset", type=lambda s: int(s, 0), default=0x8010,
                    help="register offset inside the BAR (default 0x8010, the poll's offset)")
    ap.add_argument("--count", type=int, default=8, help="how many words to read from the offset")
    ap.add_argument("--monitor", default=os.path.join(RIG, "mon.sock"))
    ap.add_argument("--out", default=None, help="write the reading as JSON here")
    ap.add_argument("--repeat", type=int, default=1, help="read this many times")
    ap.add_argument("--interval", type=float, default=30.0)
    a = ap.parse_args()

    if not os.path.exists(a.monitor):
        sys.stderr.write("no monitor socket at %s; is a guest running?\n" % a.monitor)
        return 2

    readings = []
    mon = Mon(a.monitor)
    try:
        for attempt in range(a.repeat):
            hits = distinct_ranges(bar_range(mon, a.bar))
            if not hits:
                sys.stderr.write("no BAR %d in the monitor's memory map; refusing to guess an address\n"
                                 % a.bar)
                return 1
            if len(hits) > 1:
                sys.stderr.write("BAR %d appears %d times in the map; refusing to pick one\n"
                                 % (a.bar, len(hits)))
                for lo, hi, line in hits:
                    print("   " + line)
                return 1
            lo, hi, line = hits[0]
            addr = lo + a.offset
            if addr + a.count * 4 > hi + 1:
                sys.stderr.write("offset 0x%X plus %d words runs past BAR %d (0x%X-0x%X)\n"
                                 % (a.offset, a.count, a.bar, lo, hi))
                return 1
            words = read_words(mon, addr, a.count)
            if len(words) != a.count:
                sys.stderr.write("short read: %d of %d words\n" % (len(words), a.count))
                return 1
            print("BAR %d  guest phys 0x%X-0x%X   %s" % (a.bar, lo, hi, line))
            print("read   0x%X + 0x%X = 0x%X" % (lo, a.offset, addr))
            for i, w in enumerate(words):
                print("   +0x%03X  0x%08X  bit31=%d bit29=%d"
                      % (a.offset + i * 4, w, (w >> 31) & 1, (w >> 29) & 1))
            readings.append({"stamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "bar": a.bar, "bar_range": [lo, hi], "address": addr,
                             "base": lo, "offset": a.offset, "words": words})
            if attempt + 1 < a.repeat:
                time.sleep(a.interval)
    finally:
        mon.close()

    if a.out:
        with open(a.out, "w") as f:
            json.dump({"monitor": a.monitor, "readings": readings}, f, indent=2)
            f.write("\n")
        print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
