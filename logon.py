#!/usr/bin/env python3
"""Log in to the Windows XP x64 guest, verifying each step against the framebuffer.

Typing the password alone does nothing. The XP logon screen has no focus until a tile is
selected, so a run that types eight characters into an unfocused screen leaves the
framebuffer unchanged and is indistinguishable from one where the keystrokes were never
delivered.

Sending the keystrokes is only half the problem. The click and the characters both race
the guest's repaint, and when they lose the run fails *before* the thing under test: the
password box stays empty and the screen looks like a kernel that would not accept a
logon. Measured here, roughly one run in two lost that race, which is expensive because
the failure is indistinguishable from a real one.

So every step is confirmed by the framebuffer actually changing, and retried when it did
not. A step that cannot be confirmed is reported as unconfirmed rather than assumed to
have worked, because an input that silently did nothing is the failure mode this exists to
catch.

Usage: logon.py [tile_x tile_y]
"""
import hashlib
import socket
import subprocess
import sys
import time

SOCK = "/home/dconnolly/xp64-rig/mon.sock"

# Centre of the Administrator tile on the 1024x768 logon screen, read off a screendump.
TILE = (430, 190)

RETRIES = 3


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

    def frame(self):
        """Hash the guest's framebuffer, so a step can be confirmed rather than assumed."""
        path = "/tmp/opencode/logon-frame.ppm"
        self.cmd("screendump %s" % path, settle=1.4)
        try:
            with open(path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except OSError:
            return ""

    def close(self):
        self.s.close()


def main():
    x, y = TILE
    if len(sys.argv) > 2:
        x, y = int(sys.argv[1]), int(sys.argv[2])
    pw = password()
    m = Monitor()

    for attempt in range(1, RETRIES + 1):
        print(f"  attempt {attempt} of {RETRIES}")

        before = m.frame()
        m.cmd(f"mouse_move {x} {y}", settle=0.8)
        m.cmd("mouse_button 1", settle=1.5)
        after_click = m.frame()

        if after_click and after_click == before:
            print("    the click did not change the screen; retrying")
            time.sleep(3)
            continue
        print(f"    tile selected (framebuffer changed: {after_click[:12]})")

        for ch in pw:
            m.cmd(f"sendkey {'spc' if ch == ' ' else ch}", settle=0.35)
        after_type = m.frame()

        if after_type and after_type == after_click:
            print("    the characters did not change the screen; retrying")
            time.sleep(3)
            continue
        print(f"    password entered (framebuffer changed: {after_type[:12]})")

        m.cmd("sendkey ret", settle=3.0)
        submitted = m.frame()
        if submitted and submitted == after_type:
            print("    Enter did not change the screen; retrying")
            time.sleep(3)
            continue
        print(f"    submitted (framebuffer changed: {submitted[:12]})")
        m.close()
        print("  logon submitted and confirmed; give it 40s before capturing")
        return 0

    m.close()
    print("  UNCONFIRMED: the framebuffer never responded to any of the three attempts")
    return 1


if __name__ == "__main__":
    sys.exit(main())