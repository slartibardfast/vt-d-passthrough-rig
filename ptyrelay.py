#!/usr/bin/env python3
"""Holds a stable PTY at /tmp/com1 and shuttles bytes to QEMU's KD socket.

The Windows-side debugger (kd.exe under Wine) opens COM1 once; if the PTY
changes (socat restarts create a new pts), the mapping breaks. This relay
creates one PTY pair, keeps the master open for its whole life (so the slave
path stays valid), and reconnects the QEMU socket side whenever it drops.
"""
import os
import pty
import select
import socket
import time

SOCK = "/tmp/opencode/kd.sock"
LINK = "/tmp/com1"

import termios
master, slave = pty.openpty()
attrs = termios.tcgetattr(slave)
attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP |
              termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
attrs[1] &= ~termios.OPOST
attrs[2] &= ~(termios.CSIZE | termios.PARENB)
attrs[2] |= termios.CS8
attrs[3] &= ~(termios.ECHO | termios.ECHOE | termios.ECHOK | termios.ECHONL |
              termios.ICANON | termios.ISIG | termios.IEXTEN)
termios.tcsetattr(slave, termios.TCSANOW, attrs)
sname = os.ttyname(slave)
try:
    os.remove(LINK)
except FileNotFoundError:
    pass
os.symlink(sname, LINK)
print(f"pty: {LINK} -> {sname}", flush=True)

while True:
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(SOCK)
        print("qemu socket connected", flush=True)
        while True:
            r, _, _ = select.select([s, master], [], [], 0.5)
            if s in r:
                d = s.recv(4096)
                if not d:
                    raise ConnectionError("qemu socket closed")
                if d:
                    print(f"  WINE->GUEST {len(d)}B: {d.hex(' ')}", flush=True)
                os.write(master, d)
            if master in r:
                try:
                    d = os.read(master, 4096)
                except OSError:
                    d = b""
                if d:
                    print(f"  GUEST->WINE {len(d)}B: {d.hex(' ')}", flush=True)
                    txt = "".join(chr(c) if 32 <= c < 127 else "." for c in d)
                    print(f"    ascii: {txt}", flush=True)
                    s.sendall(d)
    except Exception as e:
        print(f"relay: {e!r} - retrying", flush=True)
        time.sleep(1)
