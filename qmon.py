#!/usr/bin/env python3
"""One monitor command, one line of output. Used by probe-run.sh.

Kept as a separate file rather than embedded in the shell script: an embedded
heredoc sampler was launched in the background, hung inside its own connect, and
produced an empty log while its PID stayed alive, so every run reported "guest
state not measured" and the harness could not tell an instrument failure from a
guest fact. A single short-lived process per command has no such state to leak.

usage: qmon.py <socket> <command> [settle_seconds]
prints the response with the echoed command and prompt removed, or nothing and
exits 1 if the monitor did not answer.
"""
import re
import socket
import sys


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
        s.settimeout(3)
        try:
            s.recv(65536)  # banner, optional
        except socket.timeout:
            pass
        s.settimeout(20)
        s.sendall(command.encode() + b"\n")
        buf = b""
        deadline = settle
        while deadline > 0:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            if b"(qemu)" in buf[-200:]:
                break
            deadline -= 0.5
    except OSError as e:
        print(f"monitor error: {e}", file=sys.stderr)
        return 1
    finally:
        s.close()

    text = re.sub(rb"\x1b\[[0-9;]*[A-Za-z]", b"", buf).decode(errors="replace")
    # The monitor echoes the command back one character at a time, so the raw
    # text contains a prefix like "iininfinfo...registers" that interleaves with
    # the real reply. Drop any leading run that is a subsequence of the command
    # itself, and keep everything after it.
    text = text.replace("\r", "")
    cmd = command.strip()
    cut = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and all(ch in cmd for ch in stripped) and len(stripped) >= 3:
            cut += len(line)
            continue
        break
    text = text[cut:]
    out = [l.strip() for l in text.splitlines() if l.strip() and "(qemu)" not in l]
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())