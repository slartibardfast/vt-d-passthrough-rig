#!/usr/bin/env python3
"""kd_read via the kdc.py cmd-file protocol. Parses new 'READ' dumps from kdc-live.log."""
import time, sys, re

LOG = "/home/dconnolly/xp64-rig/kdc-live.log"
CMD = "/home/dconnolly/xp64-rig/cmd"

def logsize():
    try: return os.path.getsize(LOG)
    except OSError: return 0
import os

MAXRD = 16   # measured: the engine refuses reads >16 bytes (ret=c0000001)

def kd_read(addr, n):
    # one 16-byte-max read; answered errors return immediately (no retry),
    # silence retriggers once (the send flip lands the retry on the other id)
    n = min(n, MAXRD)
    mark = logsize()
    with open(CMD, "w") as f:
        f.write(f"read {addr:x} {n:x}\n")
    data, actual = _wait_read(mark)
    if actual:
        return data, actual
    if _last_ret[0] not in (None, 0):
        return b"", 0          # answered error: definitive, do not retry
    mark = logsize()
    with open(CMD, "w") as f:
        f.write(f"read {addr:x} {n:x}\n")
    return _wait_read(mark)

_last_ret = [None]

def _wait_read(mark, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.3)
        data = open(LOG, "rb").read()
        fresh = data[mark:].decode("ascii", "replace")
        if "READ ret=" in fresh:
            out = bytearray()
            actual = 0
            for line in fresh.splitlines():
                m = re.match(r"\[\S+\]\s+READ ret=(\S+) actual=(\S+)", line.strip())
                if m:
                    _last_ret[0] = int(m.group(1), 0)
                    actual = int(m.group(2), 0)
                m = re.match(r"\[\S+\]\s+([0-9a-f]{4})  ((?:[0-9a-f]{2} )+)", line.strip())
                if m:
                    for b in m.group(2).split():
                        out.append(int(b, 16))
            return bytes(out[:actual]), actual
    _last_ret[0] = None
    return None, 0

def read_block(addr, total):
    """Chunked read of total bytes at addr (16-byte steps)."""
    out = bytearray()
    a = addr
    while a < addr + total:
        data, actual = kd_read(a, min(16, addr + total - a))
        if not actual:
            print(f"    read_block: failed at {a:#x}")
            return bytes(out), a - addr
        out += data
        a += len(data)
    return bytes(out), total

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
