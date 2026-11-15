#!/usr/bin/env python3
"""Run a QEMU monitor command and print the reply, reading until the stream is quiet.

mon.py breaks on a trailing "(qemu)" but the monitor echoes each character as it is
typed, so that matches mid-echo. This drains the socket until it stops producing.
"""
import os
import socket
import sys
import time

SOCK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mon.sock")


def drain(s, quiet=1.5, limit=12.0):
    buf = b""
    start = time.time()
    last = time.time()
    while time.time() - start < limit:
        s.settimeout(0.4)
        try:
            d = s.recv(65536)
        except socket.timeout:
            if time.time() - last > quiet:
                break
            continue
        if not d:
            break
        buf += d
        last = time.time()
    return buf


def run(cmd, wait=1.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.settimeout(4.0)
    drain(s)                      # clear the banner and any prompt
    s.sendall((cmd + "\n").encode())
    time.sleep(wait)
    out = drain(s)
    s.close()
    return out.decode(errors="replace")


ERROR_WORDS = ("error", "invalid", "not found", "no console", "cannot", "could not")


def looks_like_a_refusal(text):
    """Whether the monitor answered with a refusal rather than a result.

    This script used to exit 0 whatever came back, so a caller that gated on it reported a
    screendump that never happened. A checker that cannot fail is worse than no checker, and this
    one manufactured a positive line in a graded run: `screendump` under `-display none` answers
    "There is no console to take a screendump from", and the runner printed a filename anyway.
    """
    low = text.lower()
    return any(w in low for w in ERROR_WORDS)


if __name__ == "__main__":
    bad = 0
    for c in sys.argv[1:]:
        # strip the echoed command and the trailing prompt
        txt = run(c)
        lines = [l for l in txt.replace("\r", "").split("\n") if l.strip()]
        lines = [l for l in lines if not l.startswith("(qemu)")]
        print(f"===== {c} =====")
        for l in lines[-14:]:
            print("  " + l[:160])
        if looks_like_a_refusal(txt):
            bad = 1
    sys.exit(bad)