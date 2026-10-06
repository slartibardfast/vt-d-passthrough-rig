#!/usr/bin/env python3
"""Fill the five XP product-key boxes from the inside out.

What went wrong before, in order:
  * sendkey at a fast settle raced XP's auto-advance, so groups landed split
    across boxes (VCFQD / V9FX9 / 9-46Wv / VH-K3 / CD4-4).
  * Tab skips alternate boxes, so tab-then-type filled boxes 1, 3 and 5 only.
  * With no box focused every keystroke went to the Next button, and the
    framebuffer came back byte-identical. An unchanged screenshot is the tell
    that nothing reached the dialog.

The fix is to focus each box with two shift-tabs from the Next button (which
lands in box 5, the last one), walk leftwards with the left arrow, and type each
group slowly. Each group is screendumped and read back, so a wrong landing is
visible rather than assumed away.
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

    def cmd(self, c, settle=1.0):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        try:
            self.s.recv(65536)
        except OSError:
            pass

    def key(self, ch):
        self.cmd(f"sendkey {ch.lower()}", settle=0.5)

    def shot(self, name):
        self.cmd(f"screendump /tmp/opencode/{name}.ppm", settle=1.2)

    def close(self):
        self.s.close()


def main():
    groups = sys.argv[1:]
    if len(groups) != 5:
        print("usage: keywalk.py G1 G2 G3 G4 G5")
        return 2
    m = Monitor()
    # Focus the Next button first so the shift-tab count is deterministic, then
    # land in box 5.
    m.cmd("sendkey shift-tab", settle=0.4)  # Back button
    m.cmd("sendkey shift-tab", settle=0.4)  # Back into box 5
    m.cmd("sendkey delete", settle=0.6)     # clear whatever was selected
    m.shot("keywalk_start")

    for g in reversed(groups):
        # Box 5 already has focus; each group moves one box left before typing.
        for ch in g:
            m.key(ch)
        m.cmd("sendkey left", settle=0.5)
    m.shot("keywalk_done")
    m.close()
    print("typed inside-out: " + "-".join(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())