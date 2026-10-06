#!/usr/bin/env python3
"""Query the running VM's QEMU monitor over its unix socket.

Usage: mon.py <command> [<command> ...]
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/mon.sock"


def run(cmds, wait=2.0):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.settimeout(4.0)
    out = []
    for c in cmds:
        s.sendall((c + "\n").encode())
        time.sleep(wait)
        buf = b""
        try:
            while True:
                d = s.recv(65536)
                if not d:
                    break
                buf += d
                if buf.rstrip().endswith(b"(qemu)"):
                    break
        except socket.timeout:
            pass
        out.append((c, buf.decode(errors="replace")))
    s.close()
    return out


if __name__ == "__main__":
    for c, r in run(sys.argv[1:]):
        print(f"===== {c} =====")
        print(r)