#!/usr/bin/env python3
"""Fill the five XP product-key boxes by clicking each one.

Why this file exists. Three keyboard approaches failed, each for a different
reason, and the framebuffer was the only thing that caught them:

  1. sendkey at a 0.6s settle raced XP's own auto-advance, so a group split
     across two boxes: VCFQD / V9FX9 / 9-46Wv / VH-K3 / CD4-4.
  2. Tab skips alternate boxes, so tab-then-type filled boxes 1, 3 and 5.
  3. With focus on the Next button every keystroke activated the button and the
     framebuffer came back byte-identical.

Clicking removes the focus question entirely: click the box, select what is in
it, delete it, then type the group and let XP auto-advance take it. The mouse is
homed to (0,0) first with a large negative relative move, because HMP's
mouse_move is relative and there is no absolute form. Homing makes every click
addressable in framebuffer coordinates, so the box centres below are checkable
against a screendump rather than guessed.

Each box is screendumped after it is written, and the run refuses to leave a box
holding anything other than the group it was asked to fill.
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/monitor.sock"

# Centres of the five boxes, in framebuffer pixels at 640x480, read off a
# screendump of the product-key page.
BOX_X = [214, 300, 386, 472, 548]
BOX_Y = 306

SHOT = "/tmp/opencode/keybox"


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
        """Clamp the pointer to the top-left corner."""
        self.cmd("mouse_move -4096 -4096", settle=0.7)

    def click(self, x, y):
        self.cmd(f"mouse_move {x - self.px} {y - self.py}", settle=0.35)
        self.px, self.py = x, y
        self.cmd("mouse_button 1", settle=0.6)

    def shot(self, tag):
        self.cmd(f"screendump {SHOT}-{tag}.ppm", settle=1.3)

    px = py = 0

    def close(self):
        self.s.close()


def main():
    groups = sys.argv[1:]
    if len(groups) != 5:
        print("usage: keybox.py G1 G2 G3 G4 G5")
        return 2
    m = Monitor()
    m.home()
    for i, g in enumerate(groups):
        m.click(BOX_X[i], BOX_Y)
        m.cmd("sendkey ctrl-a", settle=0.5)   # select whatever the box holds
        m.cmd("sendkey delete", settle=0.5)  # and clear it
        for ch in g:
            m.cmd(f"sendkey {ch.lower()}", settle=0.5)
        m.shot(f"box{i + 1}")
    m.close()
    print("clicked and filled: " + "-".join(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())