#!/usr/bin/env bash
# Rewrite csmwrap.ini inside the XP disk's CSMWrap partition.
#
# That partition is the boot path: run-gpu-vm.sh attaches only xp64-gpu.raw, and
# CSMWrap boots from EFI/BOOT/BOOTX64.EFI on its FAT16 partition 2. The helper
# disk carries a second copy of the same two files but is never attached, so an
# edit made there has no effect on the guest.
#
# Usage: set-ini.sh 'vga = 01:00.0' 'serial = true' ...
# Every key given on the command line replaces or joins the set. The image is
# always remounted and the result read back, so a silent write failure cannot
# masquerade as a successful edit.
set -euo pipefail

IMG=/home/dconnolly/xp64-rig/xp64-gpu.raw
PART=p2
MNT=/mnt/csmwrap-ini
INI=$MNT/EFI/BOOT/csmwrap.ini

sudo rm -rf "$MNT"; sudo mkdir -p "$MNT"
LOOP=$(sudo losetup --show -fP --partscan "$IMG")
trap 'sudo umount "$MNT" 2>/dev/null || true; sudo losetup -d "$LOOP" 2>/dev/null || true' EXIT

sudo mount "${LOOP}${PART}" "$MNT"
grep -q "$MNT" /proc/mounts || { echo "mount failed; refusing to write" >&2; exit 1; }
[ -f "$INI" ] || { echo "$INI absent on the boot path; refusing to write" >&2; exit 1; }

for kv in "$@"; do
  key=${kv%%=*}
  # Strip any existing occurrence, then append the new one.
  sudo sed -i "/^[[:space:]]*${key}[[:space:]]*=/d" "$INI"
  printf '%s\n' "$kv" | sudo tee -a "$INI" >/dev/null
done
sudo sync

echo "  csmwrap.ini now reads:"
sudo grep -vE '^\s*;' "$INI" | grep -vE '^\s*$' | sed 's/^/    /'