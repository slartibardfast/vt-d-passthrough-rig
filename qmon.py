#!/usr/bin/env python3
"""One monitor command, one clean line of output. Used by probe-run.sh.

usage: qmon.py <socket> <command> [settle_seconds]

`xp <addr>` reads PHYSICAL memory and `x <addr>` reads VIRTUAL memory. That
difference matters here: gdb's `dump memory` goes through the guest MMU, so every
"unmapped" address measured with it was a statement about the guest's virtual map,
not about the machine's RAM. Use this for physical reads.

The monitor echoes the command back one character at a time, so the raw reply is
"xxpxp xp /xp /1xp ..." then a newline then the real output. The echo must be
removed without touching the payload: an earlier version matched a subsequence of
the command and silently deleted the reply, so a working read came back empty.
"""
import re
import socket
import sys
import time


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: qmon.py <socket> <command> [settle]", file=sys.stderr)
        return 2
    path, command = sys.argv[1], sys.argv[2]
    settle = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(20)
    try:
        s.connect(path)
    except OSError as e:
        print(f"monitor unreachable: {e}", file=sys.stderr)
        return 1
    try:
        # Read the banner until its prompt too. Treating it as optional and
        # giving it a short timeout meant the socket was sometimes still draining
        # the banner when the command went out, and the reply was then mis-framed
        # - intermittent empty results that looked like a fact about the guest.
        banner = b""
        b_end = time.monotonic() + 5.0
        while time.monotonic() < b_end:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            banner += chunk
            if b"(qemu)" in banner[-200:]:
                break
        # Read until the prompt "(qemu)" actually arrives. A timeout is NOT the
        # end of the reply: the monitor writes the echo and the output separately,
        # and a short inter-write gap made the same address return data on one
        # call and nothing on the next. Treating a pause as completion produced
        # an intermittent instrument, which is worse than a broken one because it
        # looks like a fact about the guest. Only the prompt ends the read; the
        # ceiling is a backstop against a hung monitor.
        s.settimeout(20)
        s.sendall(command.encode() + b"\n")
        buf = b""
        deadline = time.monotonic() + max(settle, 8.0)
        while time.monotonic() < deadline:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            if b"(qemu)" in buf[-200:]:
                break
    except OSError as e:
        print(f"monitor error: {e}", file=sys.stderr)
        return 1
    finally:
        s.close()

    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", buf.decode(errors="replace"))
    text = text.replace("\r", "")
    # The echo and the reply are separated by the FIRST newline, not by a line of
    # their own: the monitor writes the echoed command with no line break, so a
    # line-based filter threw the payload away with the echo and every read came
    # back empty - including `info status`, which made it look like the socket was
    # dead rather than the parser being wrong.
    head, sep, tail = text.partition("\n")
    body = tail if sep else ""
    out = []
    for line in body.split("\n"):
        line = line.strip()
        if not line or "(qemu)" in line:
            continue
        out.append(line)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())