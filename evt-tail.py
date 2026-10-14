#!/usr/bin/env python3
"""evt-tail.py - pull and decode the tail of the guest's event logs.

Usage: evt-tail.py [N] [--sys|--sec]
Requires the guest disk to be mounted read-only at /mnt/xp-ro.

The .evt record layout, pinned this campaign: records start at offset 48;
magic "LfLe" at +4; the generated time at +12; the event id at +20; the
strings offset at +36. UTF-16 payload, NUL-terminated.
"""
import datetime
import struct
import subprocess
import sys

EVT = "/mnt/xp-ro/WINDOWS/system32/config/{}"


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def pull(which):
    src = EVT.format("SysEvent.Evt" if which == "sys" else "SecEvent.Evt")
    dst = f"/tmp/opencode/{which}.evt"
    r = sh("sudo", "cp", src, dst)
    if r.returncode != 0:
        print(f"  copy failed: {r.stderr.strip()}")
        sys.exit(1)
    sh("sudo", "chmod", "644", dst)
    return dst


def records(path, tail):
    data = open(path, "rb").read()
    off = 48
    out = []
    while off + 56 <= len(data):
        rlen = struct.unpack_from("<I", data, off)[0]
        if data[off+4:off+8] != b"LfLe" or rlen < 56 or off + rlen > len(data):
            break
        tgen = struct.unpack_from("<I", data, off + 12)[0]
        evid = struct.unpack_from("<I", data, off + 20)[0] & 0xFFFF
        str_off = struct.unpack_from("<I", data, off + 36)[0]
        end = off + min(str_off + 400, rlen)
        text = data[off + str_off:end].decode("utf-16-le", errors="replace")
        text = " ".join(s for s in text.split("\x00") if s.strip())
        out.append((tgen, evid, text.strip()[:120]))
        off += rlen
    return out[-tail:]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    tail = int(args[0]) if args else 20
    which = "sec" if "--sec" in sys.argv else "sys"
    path = pull(which)
    for t, eid, text in records(path, tail):
        ts = datetime.datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S") if t else "?"
        print(f"  [{ts}] evt{eid}: {text}")


if __name__ == "__main__":
    main()
