#!/usr/bin/env bash
# Put the install channel and the letter witness onto the disks, from the tracked
# templates in install-channel/, and refuse to call it done until the volume
# carries no drive letter the guest could disagree with.
#
# This exists because drv.bat had been dropped onto the volume by hand and had
# drifted into the defect it was meant to avoid: thirty `D:\drv\...` sources, and
# a hard-coded `D:` in probe/phantom.bat. The rig already knew the law - see the
# comment in deploy-driver-image.sh about a hard-coded B: - and nothing enforced
# it, so the same mistake was made twice in one component.
#
# The machine is off, and this says so: it refuses to write while a QEMU holds
# the images.
#
# Usage: stage-install-channel.sh [os-disk-image] [driver-image]
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)

# No argument may start a machine. `--help` used to boot one: this script does its work by
# running, so a caller that probed the interface got a live guest instead of a usage line.
# That happened twice on 2026-11-15, and both guests had to be stopped mid-boot. Flags are
# refused here, before any mount, any hive and any QEMU; only a plain path is an argument.
case "${1:-}" in
  -h|--help)
    printf 'usage: %s\n' 'stage-install-channel.sh [os-disk-image] [driver-image]' >&2
    exit 0;;
esac
for arg in "$@"; do
  case "$arg" in
    -*)
      printf 'unknown argument: %s\nusage: %s\n' "$arg" 'stage-install-channel.sh [os-disk-image] [driver-image]' >&2
      exit 2;;
  esac
done
OSD=${1:-/home/dconnolly/xp64-rig/xp64-gpu.raw}
DRV=${2:-$RIG/gpudrv.img}
RUN=$RIG/work/runs/stage-channel-$(date -u +%Y%m%dT%H%M%SZ)

if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'a QEMU is running: these images are open. Shut the guest down first.\n'
  exit 2
fi
for f in "$OSD" "$DRV"; do
  test -e "$f" || { printf 'missing %s\n' "$f"; exit 2; }
  if findmnt -n -o SOURCE | grep -q "$(basename "$f")"; then
    printf '%s is mounted somewhere: unmount it first\n' "$f"
    exit 2
  fi
done
mkdir -p "$RUN/replaced"

offset_of_first_partition() { python3 "$RIG/partition-offset.py" "$1"; }

DOFF=$(offset_of_first_partition "$DRV") || exit 2
POFF=$(offset_of_first_partition "$OSD") || exit 2

M=/tmp/opencode/stage-channel-mnt
sudo mkdir -p "$M"

# The driver volume: the installer, the witness, and the re-lettered probe.
sudo mount -o loop,offset="$DOFF" "$DRV" "$M" || { printf 'cannot mount %s\n' "$DRV"; exit 2; }
for pair in "drv.bat:drv/drv.bat" "letters.bat:letters.bat" "phantom.bat:probe/phantom.bat"; do
  src=${pair%%:*}; dst=${pair##*:}
  if sudo test -e "$M/$dst"; then
    sudo cp "$M/$dst" "$RUN/replaced/$(echo "$dst" | tr / _)"
    printf 'replacing %s (previous copy kept)\n' "$dst"
  fi
  sudo cp "$RIG/install-channel/$src" "$M/$dst"
done
sync
sudo umount "$M"

# The system volume: the Startup trigger that runs the witness at logon.
sudo mount -o offset="$POFF" "$OSD" "$M" || { printf 'cannot mount %s\n' "$OSD"; exit 2; }
STARTUP="Documents and Settings/All Users/Start Menu/Programs/Startup"
if sudo test -d "$M/WINDOWS"; then
  :
else
  printf '%s has no WINDOWS directory; refusing to guess where Startup is\n' "$OSD"
  sudo umount "$M"; exit 2
fi
if sudo test -e "$M/$STARTUP/letters.bat"; then
  sudo cp "$M/$STARTUP/letters.bat" "$RUN/replaced/startup-letters.bat"
  printf 'replacing the Startup trigger (previous copy kept)\n'
fi
sudo cp "$RIG/install-channel/startup-hook.bat" "$M/$STARTUP/letters.bat"
sync
sudo umount "$M" || { printf 'unmount failed after writing the Startup trigger\n'; exit 1; }

# Read back from the images and hold the staged files to the law: a staged script
# may name the system drive and no other volume letter. Only the files this tool
# wrote are in scope - the guest's own tree carries old staged scripts that this
# tool neither wrote nor may silently rewrite.
sudo mount -o ro,loop,offset="$DOFF" "$DRV" "$M"
sudo cp "$M/drv/drv.bat" "$RUN/drv.bat"
sudo cp "$M/letters.bat" "$RUN/letters.bat"
sudo cp "$M/probe/phantom.bat" "$RUN/phantom.bat"
sudo umount "$M"
sudo mount -o ro,offset="$POFF" "$OSD" "$M"
sudo cp "$M/$STARTUP/letters.bat" "$RUN/startup-hook-installed.bat"
sudo umount "$M"
sudo chown "$(id -u):$(id -g)" "$RUN"/*.bat

python3 "$RIG/channel_lint.py" "$RUN/drv.bat" "$RUN/letters.bat" "$RUN/phantom.bat" \
        "$RUN/startup-hook-installed.bat" || {
  printf 'staged scripts are not letter-free; not calling this done\n'
  exit 1
}

printf '\nstaged, read back, and clean:\n'
sha256sum "$RUN/drv.bat" "$RUN/letters.bat" "$RUN/phantom.bat" "$RUN/startup-hook-installed.bat" \
  | tee "$RUN/volume-hashes.txt"
printf 'run directory: %s\n' "$RUN"
