#!/usr/bin/env python3
"""Read the guest's text console out of physical memory and print it.

Why this exists: the only display the operator can see is the physical monitor on the
290X, and with `-display none` there is no window for the emulated std VGA. So the console
is read where a VGA text console actually lives rather than from a screenshot.

A VGA colour text console is 80x25 cells of (character, attribute) at physical 0xB8000,
stride 160. That address is inside the legacy aperture 0xA0000-0xBFFFF, so one pmemsave
of 0x20000 bytes from 0xA0000 covers it.

pmemsave takes a PHYSICAL address and the filename must be quoted or relative - `/tmp/x`
fails with "invalid char 't' in expression" because QEMU parses `/` as division.

Usage: console.py <monitor.sock> [--watch SECONDS] [--cell]
"""
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

CONSOLE_PHYS = 0xB8000
WIDTH, HEIGHT = 80, 25
TMP = Path("/tmp/opencode/_console.bin")


def qmon(sock: str, command: str, timeout: int = 120) -> str:
    return subprocess.run(
        ["python3", str(Path(__file__).with_name("qmon.py")), sock, command],
        capture_output=True, text=True, timeout=timeout,
    ).stdout


def dump(sock: str) -> bytes:
    """Read the aperture through pmemsave and return the console slice."""
    tmp = TMP
    if tmp.exists():
        tmp.unlink()
    qmon(sock, f'pmemsave 0xA0000 0x20000 "{tmp}"')
    if not tmp.exists():
        return b""
    data = tmp.read_bytes()
    tmp.unlink(missing_ok=True)
    return data[CONSOLE_PHYS - 0xA0000:][: WIDTH * HEIGHT * 2]


def render(data: bytes, cell: bool) -> str:
    if len(data) < WIDTH * HEIGHT * 2:
        return "(console not readable yet)"
    rows = []
    for r in range(HEIGHT):
        line, attrs = [], set()
        for c in range(WIDTH):
            off = r * WIDTH * 2 + c * 2
            ch, at = data[off], data[off + 1]
            attrs.add(at)
            if cell:
                line.append(" " if ch == 0 else chr(ch) if 32 <= ch < 127 else ".")
            else:
                line.append(chr(ch) if 32 <= ch < 127 else " ")
        rows.append("".join(line).rstrip())
    out = "\n".join("|" + r for r in rows)
    if cell:
        out += f"\n[attributes in use: {sorted(attrs)}]"
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    sock = args[0]
    watch = 0.0
    cell = "--cell" in args
    if "--watch" in args:
        watch = float(args[args.index("--watch") + 1])
    seen = None
    while True:
        data = dump(sock)
        text = render(data, cell)
        if text != seen:
            print(f"\n=== console @ {time.strftime('%H:%M:%S')} ===")
            print(text)
            sys.stdout.flush()
            seen = text
        if watch <= 0:
            break
        time.sleep(watch)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())