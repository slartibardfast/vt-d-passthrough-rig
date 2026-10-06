#!/usr/bin/env python3
"""Log in to the Windows XP x64 guest: select the Administrator tile, type the password, submit.

Typing the password alone does nothing. The XP logon screen has no focus until a tile is
selected, so a run that types eight characters into an unfocused screen produces a
framebuffer identical to one where the keystrokes were never delivered, and the two are
indistinguishable from outside. That is exactly what happened: the password box stayed
empty and the tile unselected. Clicking first removes the question.

The password comes from the installer's answer file rather than being written here, so
there is one copy of it in the rig and it is the copy the installer actually applied.

Every monitor command drains the socket until it goes quiet first. The QEMU monitor
echoes each character as it is typed, so a writer that does not wait sends into a
half-read prompt and the guest sees nothing.

Usage: logon.py [tile_x tile_y]
"""
import socket
import subprocess
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/mon.sock"

# Centre of the Administrator tile on the 1024x768 logon screen, read off a screendump.
TILE = (430, 190)


def password():
    out = subprocess.run(
        ["grep", "-iE", "^AdminPassword=", "/home/dconnolly/xp64-rig/winnt.sif"],
        capture_output=True,
        text=True,
    ).stdout.split("=", 1)[1].strip().replace("\r", "")
    if not out:
        raise SystemExit("no AdminPassword in winnt.sif")
    return out


class Monitor:
    def __init__(self, sock=SOCK):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(sock)
        self.drain()

    def drain(self, quiet=1.0, limit=15.0):
        buf = b""
        start = last = time.time()
        while time.time() - start < limit:
            self.s.settimeout(0.4)
            try:
                d = self.s.recv(65536)
            except socket.timeout:
                if time.time() - last > quiet:
                    break
                continue
            if not d:
                break
            buf += d
            last = time.time()
        return buf

    def cmd(self, c, settle=0.5):
        self.s.sendall((c + "\n").encode())
        time.sleep(settle)
        self.drain()

    def close(self):
        self.s.close()


def main():
    x, y = TILE
    if len(sys.argv) > 2:
        x, y = int(sys.argv[1]), int(sys.argv[2])

    pw = password()
    m = Monitor()

    print(f"  selecting the Administrator tile at {x},{y}")
    m.cmd(f"mouse_move {x} {y}", settle=0.6)
    m.cmd("mouse_button 1", settle=1.2)

    print(f"  typing a {len(pw)}-character password")
    for ch in pw:
        m.cmd(f"sendkey {'spc' if ch == ' ' else ch}", settle=0.3)

    m.cmd("sendkey ret", settle=2.0)
    m.close()
    print("  submitted; give it 40s before capturing the desktop")


if __name__ == "__main__":
    main()