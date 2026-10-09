#!/usr/bin/env bash
# Build the install target disk for stage 3 of the from-scratch runbook.
#
#   p1: left entirely absent; setup's AutoPartition=1 creates and formats it in the
#       head free space, exactly as the October layout shows (p1 41913522 sectors).
#   p2: 29440 sectors of FAT16 in the tail, carrying CSMWrap at EFI/BOOT/BOOTX64.EFI
#       plus csmwrap.ini, carved the way October carved it (start 41913600, type 0x0e).
#   MBR: partition table plus an inert boot sector, with the 55aa signature left in
#       place. The entry carries no active flag, and CSMWrap's fork reads sector 0,
#       finds no active partition, and so steps the disk behind the CD: the disk is
#       always tried first because it is the medium CSMWrap booted from, and it has to
#       yield for the installer to be reachable at all. XP setup writes a real MBR
#       with an active partition on first reboot, after which the disk takes priority
#       back. Removing the 55aa instead would make SeaBIOS decline the disk through
#       its own "not a bootable disk" path, but those two bytes are also what make p2
#       visible to mkfs.vfat and to OVMF, and OVMF must find p2 to load CSMWrap at all.
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
# Sector 0 carries no operating system: the partition table, an inert ret, and a
# signature. The signature stays, because removing it hides p2 from mkfs.vfat and from
# OVMF, and OVMF has to read p2 to load CSMWrap. The decliner works the other way:
# the entry below is not flagged active, which is what CSMWrap's fork tests for when
# it decides the disk cannot boot and gives the installer priority ahead of it.
mbr[0] = 0xc3                      # ret, inert; setup overwrites sector 0 on install
e = 0x1CE                          # entry 1; entry 0 stays empty for setup
mbr[e+0] = 0x00                    # not bootable
mbr[e+4] = 0x0e                    # FAT16 LBA
struct.pack_into("<I", mbr, e+8, start)
struct.pack_into("<I", mbr, e+12, sectors)
mbr[510], mbr[511] = 0x55, 0xaa
with open(loop, "r+b") as f:
    f.write(mbr)
print("  MBR written: inert sector 0 (55aa present) + p2 entry at 0x1CE")
PY
sudo -n partprobe "$LOOP" 2>/dev/null || true
sleep 1

echo "=== p2 entry after (read directly: sfdisk and fdisk both need the 55aa"
echo "=== signature this script deliberately omits, so neither will parse it) ==="
sudo -n python3 - "$P2_START" "$P2_SECTORS" "$DISK" <<'PY'
import sys, struct
start_want, size_want = int(sys.argv[1]), int(sys.argv[2])
f = open(sys.argv[3] if len(sys.argv) > 3 else "xp64-scratch.raw", "rb")
f.seek(0x1CE); e = f.read(16)
start, size = struct.unpack("<II", e[8:16])
print(f"  entry1: type=0x{e[4]:02x} start={start} size={size}")
assert e[4] == 0x0e and start == start_want and size == size_want, "p2 entry mismatch"
print("  ASSERT PASS: p2 entry is the FAT16 tail partition setup must leave alone")
PY

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