#!/usr/bin/env bash
# Screendump the guest console to a PNG. This is the only way to see the emulated std
# VGA: there is no window (-display none) and the operator's only display is the physical
# monitor on the 290X. Works because -device VGA gives QEMU a console surface to dump.
# console.py only reads text mode and goes blind once Setup reaches its GUI phase.
set -euo pipefail
RIG=/home/dconnolly/xp64-rig
NAME=${1:-shot}
MON=${MON:-$RIG/mon.sock}
rm -f "/tmp/opencode/$NAME.ppm"
python3 "$RIG/qmon.py" "$MON" "screendump /tmp/opencode/$NAME.ppm" >/dev/null 2>&1
python3 - "$NAME" <<'PY'
import sys
from PIL import Image
n = sys.argv[1]
im = Image.open(f"/tmp/opencode/{n}.ppm")
im.save(f"/tmp/opencode/{n}.png")
print(f"  {n}.png {im.size[0]}x{im.size[1]}")
PY
