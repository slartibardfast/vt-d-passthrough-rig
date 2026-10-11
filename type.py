#!/usr/bin/env python3
"""Type a string into the guest, one QEMU monitor sendkey per character.

QEMU's `sendkey` takes a single key name per command and uses '-' for chords, so a string
has to be sent one character at a time, with a gap. Each key is held briefly because the
emulated 8042 keyboard controller coalesces events that arrive too close together.

Usage: type.py <monitor.sock> <string>
"""
import subprocess
import sys
import time
from pathlib import Path

# QEMU key names for characters that are not simply their lowercase letter.
SPECIAL = {
    " ": "spc", "-": "minus", "_": "shift-minus", ".": "dot", ",": "comma",
    "/": "slash", "\\": "backslash", "'": "apostrophe", ";": "semicolon",
    "=": "equal", "[": "bracket_left", "]": "bracket_right", "\n": "ret",
    '"': "shift-apostrophe", "`": "shift-grave_accent", "!": "shift-1",
    "@": "shift-2", "#": "shift-3", "$": "shift-4", "%": "shift-5",
    "^": "shift-6", "&": "shift-7", "*": "shift-8", "(": "shift-9",
    ")": "shift-0", "+": "shift-equal", "{": "shift-bracket_left",
    "}": "shift-bracket_right", ":": "shift-semicolon", "?": "shift-slash",
    "<": "shift-comma", ">": "shift-dot", "|": "shift-backslash", "~": "shift-grave_accent",
}


def keyname(ch: str) -> str:
    """Map a character to a QEMU key name, or refuse.

    An unmapped character used to be passed through unchanged, and QEMU's monitor
    silently ignored it - so a missing quote turned `findstr /c:"ven_1002"` into
    `findstr /ven_1002` and produced a screen full of "FINDSTR: / ignored" instead of an
    error. An instrument that quietly loses input is worse than one that refuses, because
    its failure reads as a fact about the subject. So an unknown character is now fatal.
    """
    if ch in SPECIAL:
        return SPECIAL[ch]
    if ch.isupper():
        return f"shift-{ch.lower()}"
    if ch.islower() or ch.isdigit():
        return ch
    raise ValueError(f"type.py: no QEMU key name for {ch!r} (U+{ord(ch):04X})")


def main() -> int:
    sock, text = sys.argv[1], sys.argv[2]
    qmon = str(Path(__file__).with_name("qmon.py"))
    for ch in text:
        subprocess.run(["python3", qmon, sock, f"sendkey {keyname(ch)}"],
                       capture_output=True, timeout=30)
        time.sleep(0.12)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())