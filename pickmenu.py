#!/usr/bin/env python3
"""Watch the guest screen and pick an entry from XP's startup-recovery menu.

The menu auto-starts after a short countdown, so the choice has to be made the moment
it appears. Timing guesses missed it twice; this detects it instead: the menu is
720x400 text with a small but clearly non-zero amount of ink on it.
"""
import socket
import sys
import time
from PIL import Image

SOCK = "/home/dconnolly/xp64-rig/mon.sock"
SHOT = "/home/dconnolly/xp64-rig/probe.ppm"


def mon(cmd):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(SOCK)
    s.settimeout(5)
    s.sendall((cmd + "\n").encode())
    time.sleep(0.8)
    try:
        s.recv(65536)
    except socket.timeout:
        pass
    s.close()


def keys(seq):
    for k in seq.split():
        mon("sendkey " + k)
        time.sleep(0.12)


def looks_like_menu():
    mon(f'screendump {SHOT}')
    time.sleep(1.2)
    try:
        im = Image.open(SHOT).convert("RGB")
    except Exception:
        return False
    if im.size != (720, 400):
        return False
    px = list(im.getdata())
    ink = sum(1 for p in px if p != (0, 0, 0)) / len(px)
    return 0.03 < ink < 0.30


def main():
    choice = sys.argv[1] if len(sys.argv) > 1 else "lkg"
    # down x3 then enter lands on "Last Known Good Configuration"
    plan = {"lkg": "down down down ret",
            "safe": "ret",
            "safecmd": "down down down ret",
            "normal": "ret"}[choice]
    deadline = time.time() + 240
    seen = 0
    while time.time() < deadline:
        if looks_like_menu():
            seen += 1
            print(f"  menu detected (attempt {seen}) -> {plan}")
            keys(plan)
            time.sleep(3)
            # confirm we are no longer looking at the menu
            if not looks_like_menu():
                print("  menu dismissed")
                return 0
        time.sleep(1.5)
    print("  menu never appeared")
    return 1


sys.exit(main())