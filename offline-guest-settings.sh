#!/usr/bin/env bash
# Take the guest's drive letters and its autologon out of the hands of boot-time
# luck by editing its registry while the machine is off.
#
# Why offline: the letters a session shows come from SYSTEM\MountedDevices, and
# the kernel reads that before anything the rig can observe, so the only
# deterministic fix is to write the entry for the volume that should own the
# letter. hive-pin.py derives that identity from the disk images themselves (MBR
# disk signature plus partition byte offset), so the edit cannot retarget a
# letter onto the wrong volume by mistake - the defect this exists to remove.
#
# Why autologon here: the iteration loop the plan is building boots a variant
# disk, needs a session, and then needs the driver's own evidence. Typing a
# password through a HID for every experiment adds a step that can fail for
# reasons that have nothing to do with the question being asked. The operator
# decided this on 2026-11-14, in the open, and the password is read from the
# install answer file rather than written into this repository.
#
# Refuses a running QEMU: a hive the guest has open is not a file, it is cached
# state, and writing it back corrupts the registry.
#
# Usage: offline-guest-settings.sh [os-disk-image] [driver-image] [answer-file]
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OSD=${1:-/home/dconnolly/xp64-rig/xp64-gpu.raw}
DRV=${2:-$RIG/gpudrv.img}
SIF=${3:-/home/dconnolly/xp64-rig/winnt.sif}
RUN=$RIG/work/runs/offline-settings-$(date -u +%Y%m%dT%H%M%SZ)

for f in "$OSD" "$DRV" "$SIF"; do
  test -e "$f" || { printf 'missing %s\n' "$f"; exit 2; }
done
if pgrep -f '[q]emu-system-x86_64' >/dev/null; then
  printf 'a QEMU is running: the guest has these hives open. Shut it down first.\n'
  exit 2
fi
if findmnt -n -o SOURCE | grep -q "$(basename "$OSD")"; then
  printf '%s is already mounted somewhere: unmount it first\n' "$OSD"
  exit 2
fi

mkdir -p "$RUN/rollback" "$RUN/work" "$RUN/after"

# The partition offset comes from the image, so the tool follows a re-cut disk
# instead of assuming the layout this rig happens to hold today.
OFF=$(python3 - "$OSD" <<'PY'
import struct, sys
with open(sys.argv[1], "rb") as f:
    f.seek(0x1BE)
    entry = f.read(16)
start = struct.unpack("<I", entry[8:12])[0]
if start == 0:
    raise SystemExit("no first partition entry")
print(start * 512)
PY
) || { printf 'cannot read the first partition of %s\n' "$OSD"; exit 2; }
printf 'os disk %s: partition 1 at byte %s\n' "$OSD" "$OFF"

M=/tmp/opencode/offline-settings-mnt
sudo mkdir -p "$M"
sudo mount -o ro,offset="$OFF" "$OSD" "$M" || { printf 'read-only mount failed\n'; exit 2; }
for h in system software; do
  sudo cp "$M/WINDOWS/system32/config/$h" "$RUN/rollback/$h"
  sudo cp "$M/WINDOWS/system32/config/$h" "$RUN/work/$h"
done
sudo umount "$M"
sudo chown -R "$(id -u):$(id -g)" "$RUN"
printf 'hives extracted; originals kept in %s/rollback\n' "$RUN"

python3 "$RIG/hive-pin.py" letters "$RUN/work/system" "$DRV" || { printf 'letter pin failed\n'; exit 1; }

PW=$(grep -iE '^AdminPassword=' "$SIF" | cut -d= -f2- | tr -d '\r\n')
test -n "$PW" || { printf 'no AdminPassword in %s\n' "$SIF"; exit 2; }
python3 "$RIG/hive-pin.py" autologon "$RUN/work/software" "$PW" || { printf 'autologon failed\n'; exit 1; }

# The drive-with-no-media dialog is not a cosmetic problem here: it blocks. Every
# version of the letter witness stalled on it, and a stalled witness is the same
# silence the letter drift produced.
python3 "$RIG/hive-pin.py" harderror "$RUN/work/system" || { printf 'hard-error setting failed\n'; exit 1; }

sudo mount -o offset="$OFF" "$OSD" "$M" || { printf 'read-write mount failed\n'; exit 2; }
for h in system software; do
  sudo cp "$RUN/work/$h" "$M/WINDOWS/system32/config/$h"
  sudo chmod 644 "$M/WINDOWS/system32/config/$h"
done
sync
sudo umount "$M" || { printf 'unmount failed; the image may hold a partial write\n'; exit 1; }
printf 'hives installed\n'

# Read back from the image, not from the working copy: the claim being made is
# about the disk, and the only evidence for that is what the disk now holds.
sudo mount -o ro,offset="$OFF" "$OSD" "$M"
for h in system software; do sudo cp "$M/WINDOWS/system32/config/$h" "$RUN/after/$h"; done
sudo umount "$M"
sudo chown -R "$(id -u):$(id -g)" "$RUN"
python3 "$RIG/hive-pin.py" letters "$RUN/after/system" "$DRV" | tee "$RUN/letters-after.txt"
printf 'installed hive hashes:\n' | tee -a "$RUN/letters-after.txt"
sha256sum "$RUN/after/system" "$RUN/after/software" | tee -a "$RUN/letters-after.txt"
printf 'run directory: %s\n' "$RUN"
