#!/usr/bin/env bash
# Build the install target disk for stage 3 of the from-scratch runbook.
#
#   p1: left entirely absent; setup's AutoPartition=1 creates and formats it in the
#       head free space, exactly as the October layout shows (p1 41913522 sectors).
#   p2: 29440 sectors of FAT16 in the tail, carrying CSMWrap at EFI/BOOT/BOOTX64.EFI
#       plus csmwrap.ini, carved the way October carved it (start 41913600, type 0x0e).
#   MBR: a one-instruction boot code (retf) that makes SeaBIOS record "boot failed"
#       for this disk and fall through to the next BBS entry, the stock CD. CSMWrap
#       pins the legacy boot device to the disk it was loaded from, so without this
#       decliner the install would chain to an empty MBR and hang. XP setup overwrites
#       the decliner with a real MBR on first reboot, after which the pin is correct.
#
# The partition table is asserted before and after every write: a write at 0x1CE that
# landed inside entry 0 once destroyed a boot partition silently, and a byte diff
# "verification" said nothing about whether the result was right.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
DISK="$RIG/xp64-scratch.raw"
EFI="$1"          # path to the CSMWrap artifact to deploy
INI="$2"          # path to the csmwrap.ini to deploy
P2_START=41913600
P2_SECTORS=29440

[ -f "$DISK" ] || { echo "missing $DISK" >&2; exit 1; }
[ -f "$EFI" ]  || { echo "missing CSMWrap artifact" >&2; exit 1; }
[ -f "$INI" ]  || { echo "missing csmwrap.ini" >&2; exit 1; }

assert_table() {  # $1 = label; an empty table is a valid before-state
  sudo -n fdisk -l "$DISK" | grep -E '^/dev|Device' | sed "s/^/  table[$1]: /" || echo "  table[$1]: (empty)"
}

echo "=== table before ==="
assert_table before

LOOP=$(sudo -n losetup --show -fP --partscan "$DISK")
trap 'sudo -n losetup -d "$LOOP" 2>/dev/null || true' EXIT

sudo -n python3 - "$LOOP" "$P2_START" "$P2_SECTORS" <<'PY'
import sys, struct
loop, start, sectors = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
mbr = bytearray(512)
mbr[0] = 0xcb                      # retf: "boot failed", SeaBIOS falls through
e = 0x1CE                          # entry 1; entry 0 stays empty for setup
mbr[e+0] = 0x00                    # not bootable
mbr[e+4] = 0x0e                    # FAT16 LBA
struct.pack_into("<I", mbr, e+8, start)
struct.pack_into("<I", mbr, e+12, sectors)
mbr[510], mbr[511] = 0x55, 0xaa
with open(loop, "r+b") as f:
    f.write(mbr)
print("  MBR written: decliner + p2 entry at 0x1CE")
PY
sudo -n partprobe "$LOOP" 2>/dev/null || true
sleep 1

echo "=== table after (must show exactly one partition, start $P2_START) ==="
assert_table after
sudo -n fdisk -l "$DISK" | grep -q " ${P2_START} " || { echo "  ASSERT FAILED: p2 start" >&2; exit 1; }

sudo -n mkfs.fat -F 16 -n CSMWRAP "${LOOP}p2" >/dev/null
MNT=/tmp/opencode/p2mnt; sudo -n rm -rf "$MNT"; sudo -n mkdir -p "$MNT"
sudo -n mount "${LOOP}p2" "$MNT"
sudo -n mkdir -p "$MNT/EFI/BOOT"
sudo -n cp "$EFI" "$MNT/EFI/BOOT/BOOTX64.EFI"
sudo -n cp "$INI" "$MNT/EFI/BOOT/csmwrap.ini"
sudo -n sync
echo "=== deployed ==="
sudo -n sha256sum "$MNT/EFI/BOOT/BOOTX64.EFI" | sed 's/^/  efi: /'
sudo -n tr -d '\r' < "$MNT/EFI/BOOT/csmwrap.ini" | sed 's/^/  ini: /'
sudo -n umount "$MNT"; sudo -n rm -rf "$MNT"
echo "  disk ready"