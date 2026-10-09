#!/usr/bin/env bash
# Extract the EFI image from the card's hybrid ROM and hand it to QEMU as romfile.
#
# The 290X's 128 KiB ROM is two PCI expansion ROM images, which AMD's own tool
# reports as "Hybrid Images":
#
#   Image[0]  0x00000  65536 B  Legacy Image   (ATOMBIOS, init byte 0xe9)
#   Image[1]  0x10000  58368 B  EFI Image      (signature 0x0EF1 at +4, PCIR devid 0x67b0)
#
# SUPERSEDED. Do not serve the extracted slice. run-install.sh passes the whole
# 128 KiB to QEMU as romfile, because serving only the EFI half was tried and did
# not work: with the extracted slice the guest never reached the CSM. The card's
# legacy OpROM is kept out of dispatch by `oprom = false` in csmwrap-install.ini
# instead, which is where that decision belongs.
#
# This script is kept because it is the instrument that proved the ROM is hybrid:
# it walks the expansion ROM images and reports both, and it is the reason the
# claim "the card has no UEFI payload at all" was caught. Run it to inspect the
# card, not to produce an input for a boot.
#
# Why a full-ROM sweep finds nothing: the EFI image is a PCI-style expansion ROM
# block opened by 55 AA and carrying signature 0x0EF1. It is NOT an EFI firmware
# volume, so it has no _FVH header, no "PE\0\0" and no GOP protocol GUID. Searching
# for those signatures reports "no GOP" on a ROM that carries one. Check the
# signature at offset +4 of the second image instead.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
SRC="${1:-$RIG/290x-vbios.rom}"
OUT="${2:-$RIG/290x-efi.rom}"

[ -f "$SRC" ] || { echo "missing source ROM: $SRC" >&2; exit 1; }

python3 - "$SRC" "$OUT" <<'PY'
import struct, sys

src, out = sys.argv[1], sys.argv[2]
d = open(src, "rb").read()

# Walk the expansion ROM images the way a PCI option ROM reader does: each starts
# at 55 AA and declares its length in 512-byte blocks at offset +2.
off, images = 0, []
while off + 4 <= len(d) and d[off:off+2] == b"\x55\xaa":
    size = d[off + 2] * 512
    if size == 0 or off + size > len(d):
        break
    images.append((off, size, d[off+3], int.from_bytes(d[off+4:off+6], "little")))
    off += size

print(f"  source {src}: {len(d)} bytes, {len(images)} expansion image(s)")
for o, s, init, sig in images:
    kind = "EFI image" if sig == 0x0EF1 else "legacy image"
    print(f"    {o:#08x}  {s:6d} B  init={init:#04x}  sig={sig:#06x}  {kind}")

efi = [(o, s) for o, s, init, sig in images if sig == 0x0EF1]
if not efi:
    raise SystemExit("  ASSERT FAILED: no EFI image (signature 0x0EF1) in this ROM")
if len(efi) > 1:
    raise SystemExit(f"  ASSERT FAILED: {len(efi)} EFI images, expected 1")

o, s = efi[0]
img = d[o:o+s]

# The extracted image must stand on its own as an option ROM: same header, and a
# PCIR that names this GPU, or OVMF will not execute it.
assert img[:2] == b"\x55\xaa", "extracted image lost its 55 AA signature"
assert int.from_bytes(img[4:6], "little") == 0x0EF1, "extracted image lost its EFI signature"
assert img[2] * 512 == s, f"block count {img[2]} disagrees with declared size {s}"
p = int.from_bytes(img[0x1a:0x1c], "little") // 512 if False else 0x1c
assert img[p:p+4] == b"PCIR", f"no PCIR at {p:#x}, found {img[p:p+4]!r}"
devid = int.from_bytes(img[p+6:p+8], "little")
vendor = int.from_bytes(img[p+4:p+6], "little")
print(f"  extracted: PCIR vendor={vendor:#06x} device={devid:#06x} "
      f"({'R9 290X' if devid == 0x67b0 else 'UNEXPECTED DEVICE'})")
assert devid == 0x67b0, f"PCIR names device {devid:#06x}, expected 0x67b0"

open(out, "wb").write(img)
print(f"  wrote {out}: {len(img)} bytes ({len(img)//1024} KiB + {len(img)%1024} B)")
PY

echo
echo "=== the extracted image, verified standalone ==="
python3 - "$OUT" <<'PY'
import sys
d = open(sys.argv[1], "rb").read()
print(f"  size            : {len(d)} bytes")
print(f"  55 AA           : {d[:2].hex(' ')}")
print(f"  block count (+2): {d[2]:#04x} -> {d[2]*512} bytes")
print(f"  EFI signature   : {int.from_bytes(d[4:6],'little'):#06x} "
      f"({'valid' if int.from_bytes(d[4:6],'little')==0x0EF1 else 'INVALID'})")
print(f"  PCIR            : {d[0x1c:0x20]!r}")
print(f"  device id       : {int.from_bytes(d[0x22:0x24],'little'):#06x}")
print(f"  subsystem       : {int.from_bytes(d[0x24:0x28],'little'):#010x}")
# A GOP banner in this image is the vendor's version stamp, not the driver itself.
i = d.find(b"GOP AMD REV")
print(f"  GOP banner      : {d[i:i+34].decode('latin1').strip() if i>=0 else 'absent'}")
PY