#!/usr/bin/env python3
"""Dump the 290X output state as seen from the host through the running VM.

Two windows onto the physical card are read through the QEMU monitor of the
live guest, and nothing is written to the card:

  1. guest physical 0x000a0000-0x000bffff: the legacy VGA aperture. mtree
     shows this range served by `vfio-vga-mmio@0xa0000`, i.e. real MMIO to
     the card's BAR0 VGA window, not a QEMU buffer.
  2. guest physical 0xe0000000+off: the card's BAR0 register space, the
     display (CRTC) block at 0x6000 and the MC block at 0x5000.

What the dump can settle, and what it cannot:

  - If the register block reads non-zero, the read path is live, so the
    aperture content is real screen content and can be decoded.
  - If the register block reads all zero AND the aperture is uniform, the
    honest verdict is "unproven": a dead read path and a blank screen look
    identical, and this script says which of the two it is.

Usage: vga-dump.py [monitor.sock] [outdir]
"""
import json
import os
import re
import socket
import sys
import time

RIG = os.path.dirname(os.path.abspath(__file__))
SOCK = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RIG, "mon.sock")
OUT = sys.argv[2] if len(sys.argv) > 2 else os.path.join(RIG, "work/runs/vga-dump")

APERTURE_BASE = 0x000A0000
APERTURE_LEN = 0x20000              # 128 KiB, A0000-BFFFF
TEXT_BASE = 0x000B8000              # colour text memory inside the aperture
REG_BASE = 0xE0000000
REG_WINDOWS = [(0x5000, 0x100), (0x6000, 0x2000)]   # MC block, display block
CHUNK_WORDS = 2048                  # 8 KiB per monitor command
LINE = re.compile(r"^([0-9a-f]{8,16}):\s+(.*)$")


class Mon:
    """One monitor connection, commands read until the prompt returns.

    qmon.py documents why the prompt, not a quiet socket, ends a reply: a
    pause between the monitor's separate writes produced empty reads that
    looked like facts about the guest.
    """

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(60)
        self.s.connect(path)
        self._until(b"(qemu)", 10)

    def _until(self, needle, ceiling):
        buf = b""
        end = time.monotonic() + ceiling
        while time.monotonic() < end:
            try:
                chunk = self.s.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            buf += chunk
            if needle in buf[-200:]:
                break
        return buf

    def cmd(self, command, ceiling=60):
        self.s.sendall(command.encode() + b"\n")
        raw = self._until(b"(qemu)", ceiling).decode(errors="replace")
        raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw).replace("\r", "")
        head, sep, tail = raw.partition("\n")
        return tail if sep else ""

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def parse_words(text):
    data = bytearray()
    for line in text.splitlines():
        m = LINE.match(line.strip())
        if not m:
            continue
        for tok in m.group(2).split():
            try:
                data.extend(int(tok, 16).to_bytes(4, "little"))
            except ValueError:
                pass
    return bytes(data)


def read_range(mon, base, length, label):
    data = bytearray()
    for off in range(0, length, CHUNK_WORDS * 4):
        n = min(CHUNK_WORDS, (length - off) // 4)
        chunk = parse_words(mon.cmd("xp/%dxw 0x%x" % (n, base + off)))
        if len(chunk) != n * 4:
            sys.stderr.write("short read at %#x (%d/%d bytes)\n" %
                             (base + off, len(chunk), n * 4))
        data.extend(chunk)
        print("  %s %#x+%#x %d/%d" % (label, base, off, len(data), length), flush=True)
    return bytes(data[:length])


def summary(name, data):
    words = [int.from_bytes(data[i:i + 4], "little") for i in range(0, len(data) - 3, 4)]
    nz = [i for i, w in enumerate(words) if w]
    hist = {}
    for b in data:
        hist[b] = hist.get(b, 0) + 1
    top = sorted(hist.items(), key=lambda kv: -kv[1])[:5]
    return {
        "name": name,
        "bytes": len(data),
        "distinct_bytes": len(hist),
        "top_bytes": [("0x%02x" % b, c) for b, c in top],
        "nonzero_words": len(nz),
        "first_nonzero_offset": hex(nz[0] * 4) if nz else None,
        "last_nonzero_offset": hex(nz[-1] * 4) if nz else None,
    }


def text_screen(aperture):
    off = TEXT_BASE - APERTURE_BASE
    if off < 0 or off + 4000 > len(aperture):
        return None
    rows = []
    for y in range(25):
        row = ""
        for x in range(80):
            ch = aperture[off + (y * 80 + x) * 2]
            row += chr(ch) if 32 <= ch < 127 else (" " if ch in (0, 0xFF, 0x20) else ".")
        rows.append(row.rstrip())
    return rows


def main():
    os.makedirs(OUT, exist_ok=True)
    mon = Mon(SOCK)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    status = mon.cmd("info status").strip()

    print("aperture:", flush=True)
    aperture = read_range(mon, APERTURE_BASE, APERTURE_LEN, "aperture")
    print("registers:", flush=True)
    regs = {}
    for off, length in REG_WINDOWS:
        regs[off] = read_range(mon, REG_BASE + off, length, "regs 0x%x" % off)

    # A repeated small window tells a live path from a dead one: content that
    # the guest is drawing changes, a failed read repeats exactly.
    samples = []
    for t in (0, 2, 4):
        if t:
            time.sleep(2.0)
        samples.append({
            "t": t,
            "a0000": parse_words(mon.cmd("xp/256xw 0x%x" % APERTURE_BASE)),
            "b8000": parse_words(mon.cmd("xp/256xw 0x%x" % TEXT_BASE)),
        })
    mon.close()

    paths = {"aperture.bin": aperture}
    for off, blob in regs.items():
        paths["bar0-%04x.bin" % off] = blob
    for s in samples:
        for key in ("a0000", "b8000"):
            paths["sample-%s-t%d.bin" % (key, s["t"])] = s[key]
    for name, blob in paths.items():
        with open(os.path.join(OUT, name), "wb") as f:
            f.write(blob)

    reg_words = {}
    for off, blob in regs.items():
        reg_words[off] = [int.from_bytes(blob[i:i + 4], "little") for i in range(0, len(blob) - 3, 4)]
    nz_regs = [(off + i * 4, w) for off, ws in reg_words.items()
               for i, w in enumerate(ws) if w]

    screen = text_screen(aperture)
    report = {
        "stamp": stamp,
        "monitor": SOCK,
        "vm_status": status,
        "read_paths": {
            "aperture": "guest phys 0xa0000-0xbfff = mtree vfio-vga-mmio@0xa0000 (real BAR0 MMIO)",
            "registers": "guest phys 0xe0000000+off = mtree 0000:02:00.0 BAR 0",
        },
        "summaries": [summary("aperture", aperture)] +
                     [summary("regs 0x%x" % off, blob) for off, blob in sorted(regs.items())],
        "nonzero_register_count": len(nz_regs),
        "nonzero_registers_first30": [("0x%x" % o, "0x%08x" % w) for o, w in nz_regs[:30]],
        "live_read_check": {
            "a0000_t0_vs_t2": samples[0]["a0000"] != samples[1]["a0000"],
            "a0000_t2_vs_t4": samples[1]["a0000"] != samples[2]["a0000"],
            "b8000_t0_vs_t2": samples[0]["b8000"] != samples[1]["b8000"],
            "b8000_t2_vs_t4": samples[1]["b8000"] != samples[2]["b8000"],
        },
        "text_mode_screen": screen,
    }
    with open(os.path.join(OUT, "report.json"), "w") as f:
        json.dump(report, f, indent=2)

    for s in report["summaries"]:
        print("%(name)s: bytes=%(bytes)d distinct=%(distinct_bytes)d top=%(top_bytes)s "
              "nonzero_words=%(nonzero_words)s first_nz=%(first_nonzero_offset)s" % s)
    print("registers that read non-zero: %d" % len(nz_regs))
    print("live read check: %s" % json.dumps(report["live_read_check"]))
    if screen is not None:
        shown = [r for r in screen if r.strip()]
        print("text rows with a printable glyph: %d" % len(shown))
        for r in shown[:12]:
            print("  |%s|" % r[:78])
    print("wrote %s" % OUT)


if __name__ == "__main__":
    main()
