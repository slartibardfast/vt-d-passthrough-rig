#!/usr/bin/env python3
"""Fill the XP product-key boxes one group at a time, slowly.

sendkey at the default settle races the UI: XP advances to the next box on its own
schedule, and a keystroke that lands after the advance puts a character in the wrong
group. Each group is therefore typed into one box, verified by the box count, and the
settle is long enough that no keystroke arrives before the advance.
"""
import socket
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/monitor.sock"

MAP = {
    "-": "minus",
    "0": "0", "1": "1", "2": "2", "3": "3", "4": "4",
    "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
}


class Monitor:
    def __init__(self, sock=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock)
        time.sleep(0.4)
        self.s.recv(65536)

    def cmd(self, c, settle=1.2):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        try:
            self.s.recv(65536)
        except OSError:
            pass

    def key(self, ch):
        name = MAP.get(ch) or ch.lower()
        self.cmd(f"sendkey {name}")

    def close(self):
        self.s.close()


def main():
    groups = sys.argv[1:]
    if not groups:
        print("usage: keyfill.py G1 G2 G3 G4 G5")
        return 1
    m = Monitor()
    # Wipe whatever is already there. Backspace in an empty box moves to the previous
    # box, so this clears 5..1 regardless of which box the focus started in.
    for _ in range(60):
        m.cmd("sendkey backspace", settle=0.04)
    time.sleep(1.0)
    # No separators: each box auto-advances on its fifth character, so the 25
    # characters fill the five boxes on their own. Tab skips alternate boxes here.
    for ch in "".join(groups):
        m.key(ch)
    m.cmd("screendump /tmp/opencode/keyfill.ppm")
    m.close()
    print("typed: " + "-".join(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())