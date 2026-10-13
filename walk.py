#!/usr/bin/env python3
"""kd_read via the kdc.py cmd-file protocol. Parses new 'READ' dumps from kdc-live.log."""
import time, sys, re

LOG = "/home/dconnolly/xp64-rig/kdc-live.log"
CMD = "/home/dconnolly/xp64-rig/cmd"

def logsize():
    try: return os.path.getsize(LOG)
    except OSError: return 0
import os

_parity = [None]   # the parity of the last successful exchange

def kd_read(addr, n):
    import os as _os
    mark = logsize()
    with open(CMD, "w") as f:
        f.write(f"read {addr:x} {n:x}\n")
    data, actual = _wait_read(mark)
    if actual:
        return data, actual
    # the parity phase may be wrong: try both explicit parities
    for p in (0x80800001, 0x80800000):
        mark = logsize()
        with open(CMD, "w") as f:
            f.write(f"pid {p:x}\n")
        time.sleep(0.7)
        with open(CMD, "w") as f:
            f.write(f"read {addr:x} {n:x}\n")
        data, actual = _wait_read(mark)
        if actual:
            _parity[0] = p
            return data, actual
    return None, 0

def _wait_read(mark, timeout=12):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        data = open(LOG, "rb").read()
        fresh = data[mark:].decode("ascii", "replace")
        if "READ ret=" in fresh:
            out = bytearray()
            actual = 0
            for line in fresh.splitlines():
                m = re.match(r"\[\S+\]\s+READ ret=(\S+) actual=(\S+)", line.strip())
                if m: actual = int(m.group(2), 0)
                m = re.match(r"\[\S+\]\s+([0-9a-f]{4})  ((?:[0-9a-f]{2} )+)", line.strip())
                if m:
                    for b in m.group(2).split():
                        out.append(int(b, 16))
            return bytes(out[:actual]), actual
    return None, 0

if __name__ == "__main__":
    addr = int(sys.argv[1], 16)
    n = int(sys.argv[2], 16)
    data, actual = kd_read(addr, n)
    print(f"read {addr:#x} n={n:#x}: actual={actual}")
    print(" ".join(f"{b:02x}" for b in data))
