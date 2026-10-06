#!/usr/bin/env python3
"""Walk the focus ring one shift-tab at a time and screendump each step.

Measured facts so far:
  * sendkey reaches the guest. Typed characters appear in the boxes, so the
    input path is sound.
  * mouse_move does not reach the guest. A click at the centre of box 2 followed
    by typing ZZZZZ left box 2 empty, so every click-driven attempt was doomed.
    Clicking is abandoned.
  * shift-tab is the one navigation that verifiably works: two of them from the
    Next button landed focus in box 5 with its text selected.

Focus order in this dialog runs left to right across the five boxes, so the ring
should be box1..box5 then Back then Next. This walks it one press at a time and
captures each step, so the ring is read off the screen instead of assumed.
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/monitor.sock"
STEPS = 8


class Monitor:
    def __init__(self, sock=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock)
        time.sleep(0.4)
        self.s.recv(65536)

    def cmd(self, c, settle=0.7):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        try:
            self.s.recv(65536)
        except OSError:
            pass

    def close(self):
        self.s.close()


def main():
    m = Monitor()
    # Put focus on a known control first so the walk starts from a fixed point.
    m.cmd("sendkey shift-tab", settle=0.6)  # Back button
    m.cmd("sendkey shift-tab", settle=0.6)  # into box 5
    m.cmd("screendump /tmp/opencode/walk-0.ppm", settle=1.2)
    for i in range(1, STEPS + 1):
        m.cmd("sendkey shift-tab", settle=0.9)
        m.cmd(f"screendump /tmp/opencode/walk-{i}.ppm", settle=1.2)
    m.close()
    print(f"captured {STEPS + 1} focus steps")
    return 0


if __name__ == "__main__":
    sys.exit(main())