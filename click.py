#!/usr/bin/env python3
"""Click inside the guest, in screendump coordinates.

Device Manager's "Update Driver -> Have Disk" is the supported way to apply an INF to an
existing device on XP, and it is the only route left here: `reg delete` on
HKLM\\SYSTEM\\CurrentControlSet\\Enum\\PCI is denied even to Administrator, because only the
PnP manager may remove a device instance, and `rundll32 setupapi.dll,InstallHinfSection,132`
processes the INF's sections rather than binding a PnP device (the display INF's DDInstall is
empty, so it does nothing and logs nothing).

The guest uses a USB tablet, so QEMU's mouse_move is absolute and the coordinates match
`screendump` one-for-one. That coupling is the whole reason this can be trusted: a relative
pointer on the PS/2 mouse would not line up, and a click that lands on the wrong control is
worse than no click.

Usage:
    click.py <monitor.sock> <x> <y> [right]
"""
import subprocess
import sys
import time
from pathlib import Path


def mon(sock, cmd):
    qmon = str(Path(__file__).with_name("qmon.py"))
    return subprocess.run(["python3", qmon, sock, cmd], capture_output=True, timeout=30)


def click(sock, x, y, button=1):
    # Move first and settle, so the guest sees a position change before the press. A button
    # event with no preceding motion is a click at the old pointer position.
    mon(sock, f"mouse_move {x} {y}")
    time.sleep(0.3)
    mon(sock, f"mouse_button {button}")
    time.sleep(0.15)
    mon(sock, "mouse_button 0")


if __name__ == "__main__":
    sock, x, y = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    button = 2 if len(sys.argv) > 4 and sys.argv[4] == "right" else 1
    click(sock, x, y, button)