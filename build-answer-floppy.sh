#!/usr/bin/env bash
# Build the XP unattended-install answer floppy.
#
# Why this is a script and not a hand-made image: two things about winnt.sif are
# easy to get wrong and both fail late and opaquely. XP setup wants CRLF line
# endings, and an LF-only file produced "Internal Setup data structures are
# corrupted (phase 0)" after the GUI phase began. And Eula=1 has to be present,
# or the licence page still needs a keypress. Getting either wrong costs a full
# 35 minute install to discover.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
IMG="$RIG/xp64-ansfloppy.img"
SIF="$RIG/winnt.sif"
MNT=/mnt/xp64floppy

[ -f "$SIF" ] || { echo "missing $SIF" >&2; exit 1; }

# Normalise to CRLF, which is what setup expects.
printf 'answer file line endings: '
python3 - "$SIF" <<'PY'
import sys
p = sys.argv[1]
data = open(p, 'rb').read().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
open(p, 'wb').write(data)
crlf = data.count(b'\r\n')
bare = data.count(b'\n') - crlf
print(f"{crlf} CRLF, {bare} bare LF")
PY

rm -f "$IMG"
dd if=/dev/zero of="$IMG" bs=1k count=1440 status=none
mkfs.fat -F 12 -n ANSWER "$IMG" >/dev/null 2>&1

sudo mkdir -p "$MNT"
sudo mount -o loop "$IMG" "$MNT"
sudo cp "$SIF" "$MNT/winnt.sif"
sudo sync
sudo umount "$MNT"

echo "floppy built: $(du -h "$IMG" | cut -f1)"
echo "  volume label: $(file -b "$IMG" | grep -o 'label: "[^"]*"')"