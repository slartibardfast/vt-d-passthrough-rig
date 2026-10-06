#!/usr/bin/env python3
"""Click a product-key box by absolute framebuffer coordinate, then report.

Keyboard entry failed twice because focus was on the Next button: sendkey ret
activated the button and every subsequent character went there, which is why two
attempts produced an identical framebuffer. Clicking removes the focus question.
The framebuffer is 640x480 during the setup GUI phase.
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/monitor.sock"

# Centres of the five product-key boxes, read off a screendump.
BOXES = [(214, 306), (300, 306), (386, 306), (472, 306), (548, 306)]


class Monitor:
    def __init__(self, sock=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock)
        time.sleep(0.4)
        self.s.recv(65536)

    def cmd(self, c, settle=1.0):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        try:
            self.s.recv(65536)
        except OSError:
            pass

    def close(self):
        self.s.close()


def main():
    if len(sys.argv) < 2 or sys.argv[1] == "--boxes":
        print("centres: " + " ".join(f"{x},{y}" for x, y in BOXES))
        return 0
    groups = sys.argv[1:]
    m = Monitor()
    for i, g in enumerate(groups):
        x, y = BOXES[i]
        m.cmd(f"mouse_move {x} {y}", settle=0.6)
        m.cmd("mouse_button 1", settle=0.6)
        for ch in g:
            m.cmd(f"sendkey {ch.lower()}", settle=0.55)
    m.cmd("screendump /tmp/opencode/keyclick.ppm")
    m.close()
    print("clicked and typed: " + "-".join(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())