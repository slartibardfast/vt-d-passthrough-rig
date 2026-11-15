#!/usr/bin/env bash
# Put the display registration on the disk that the next boot reads, while the
# machine is off, and prove it from the disk afterwards.
#
# Why offline: the choice of display driver is made by win32k early in session
# startup from SYSTEM\Control\Video, and by PnP from the devnode's Service value.
# Neither can be repaired from inside a session that is already wrong, and the
# in-guest route is closed by other measured laws: SetupAPI demotes the unsigned
# package (#I087, see setupapi.log), file protection reverts the system32 copy of
# the miniport, and Administrators are denied the Enum tree. Editing the hive is
# the only channel left, and call/0009 is the letter law that makes the driver
# volume path in it deterministic.
#
# Why a separate script from offline-guest-settings.sh: that one's contract is the
# drive letters and the logon, and its receipt is recorded against that work. This
# one changes what a boot means, so it gets its own run directory and its own
# verdict line. They share the mount discipline, deliberately copied rather than
# factored out under a helper, so neither can break the other mid-campaign.
#
# Refuses a running QEMU: a hive the guest has open is not a file, it is cached
# state, and writing it back corrupts the registry.
#
# Usage: offline-registration.sh [os-disk-image] [driver-image] [inf]
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
OSD=${1:-/home/dconnolly/xp64-rig/xp64-gpu.raw}
DRV=${2:-$RIG/gpudrv.img}
INF=${3:-/home/dconnolly/xp64-corpus/extracted-inf/icafe-100.50-xp64-9.00.300.3005/Packages/Drivers/Display/XP6A_INF/CA178340.inf}
RUN=$RIG/work/runs/registration-graft-$(date -u +%Y%m%dT%H%M%SZ)

for f in "$OSD" "$DRV" "$INF"; do
  test -e "$f" || { printf 'missing %s\n' "$f"; exit 2; }
done
if pgrep -f '[q]emu-system-x86_64' >/dev/null; then
  printf 'a QEMU is running: the guest has these hives open. Shut it down first.\n'
  printf 'a clean shutdown flushes the writeback cache, and the cache holds the evidence.\n'
  exit 2
fi

mkdir -p "$RUN/rollback" "$RUN/work" "$RUN/after"
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

M=/tmp/opencode/registration-mnt
sudo mkdir -p "$M"
sudo mount -o ro,offset="$OFF" "$OSD" "$M" || { printf 'read-only mount failed\n'; exit 2; }
sudo cp "$M/WINDOWS/system32/config/system" "$RUN/rollback/system"
sudo cp "$M/WINDOWS/system32/config/system" "$RUN/work/system"
sudo umount "$M"
sudo chown -R "$(id -u):$(id -g)" "$RUN"
printf 'system hive extracted; original kept in %s/rollback\n' "$RUN"

python3 "$RIG/registration_graft.py" graft "$RUN/work/system" --inf "$INF" \
  | tee "$RUN/graft.txt" || { printf 'graft failed; the disk is untouched\n'; exit 1; }

sudo mount -o offset="$OFF" "$OSD" "$M" || { printf 'read-write mount failed\n'; exit 2; }
sudo cp "$RUN/work/system" "$M/WINDOWS/system32/config/system"
sudo chmod 644 "$M/WINDOWS/system32/config/system"
sync
sudo umount "$M" || { printf 'unmount failed; the image may hold a partial write\n'; exit 1; }
printf 'hive installed\n'

# Read back from the image, not from the working copy: the claim is about the disk.
sudo mount -o ro,offset="$OFF" "$OSD" "$M"
sudo cp "$M/WINDOWS/system32/config/system" "$RUN/after/system"
sudo umount "$M"
sudo chown -R "$(id -u):$(id -g)" "$RUN"

printf '\n=== verdict, read off the disk ===\n'
python3 "$RIG/registration_graft.py" check "$RUN/after/system" | tee "$RUN/check-after.txt"
RC=${PIPESTATUS[0]}
printf '\n=== the letter the ImagePath depends on, from the same hive ===\n'
python3 "$RIG/hive-pin.py" letters "$RUN/after/system" "$DRV" | tee "$RUN/letters-after.txt"
printf '\nrun directory: %s\n' "$RUN"
exit "$RC"
