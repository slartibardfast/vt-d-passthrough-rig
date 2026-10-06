#!/usr/bin/env python3
"""Decisive test: does a relative mouse_move plus click reach the dialog at all?

Every keyboard approach failed and each failure had a different cause, so the
open question is narrow and worth one measurement rather than another attempt:
if clicking a box and typing a marker puts the marker in that box, then clicks
register and only the clearing step was wrong. If the marker lands nowhere, the
click path itself is not working and no amount of typing will help.
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/monitor.sock"


class Monitor:
    def __init__(self, sock=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock)
        time.sleep(0.4)
        self.s.recv(65536)

    def cmd(self, c, settle=0.9):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        try:
            self.s.recv(65536)
        except OSError:
            pass

    def home(self):
        self.cmd("mouse_move -4096 -4096", settle=0.8)

    def click(self, x, y):
        self.cmd(f"mouse_move {x} {y}", settle=0.4)
        self.cmd("mouse_button 1", settle=0.7)

    def close(self):
        self.s.close()


def main():
    m = Monitor()
    m.home()
    m.click(300, 306)          # centre of product-key box 2
    for ch in "ZZZZZ":
        m.cmd(f"sendkey {ch.lower()}", settle=0.5)
    m.cmd("screendump /tmp/opencode/probe.ppm", settle=1.4)
    m.close()
    print("probe: clicked (300,306), typed ZZZZZ")
    return 0


if __name__ == "__main__":
    sys.exit(main())