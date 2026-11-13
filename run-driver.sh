#!/usr/bin/env bash
# Boot the installed XP x64 guest with the patched driver volume attached.
#
# plan/PLAN.md lists the driver's acceptance as fixed criteria rather than impressions:
#   - ConfigManagerErrorCode is 0
#   - VRAM equals an independently established expected value
#   - the driver file is present and loaded
# Completing the installer without error is a failure.
#
# gpudrv.img is attached as its own IDE disk and its FAT32 partition is what the guest sees
# as B:. It is the channel both ways: install.bat is staged there before the boot and
# result.txt is written back for the host to read after. Nothing long is typed through
# sendkey, which plan/PLAN.md records as the thing that fails silently.
#
# DEVIATION FROM call/0006, stated rather than hidden. call/0006 makes the 290X the guest's
  # only display with no emulated VGA attached. That is not reachable yet, and the reason is
  # measured rather than assumed: SeaBIOS programs the card only through the standard VGA CRTC
# registers, and the 290X's output path needs its vendor blob, which only the GOP brings up,
# and a GOP needs a UEFI phase holding the machine - which is what forces CSMWrap in the
# first place. An emulated VGA is therefore the bootstrap console that makes the driver
# installable. Once the driver is in, the card renders without firmware help and the
# emulated VGA can go, which is the end state call/0006 describes.
set -euo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
WORK=${WORK:-$RIG/xp64-gpu.raw}
MON=${MON:-$RIG/mon.sock}
LOG=${LOG:-$RIG/driver-stage.log}

[ -f "$WORK" ]            || { echo "missing installed disk: $WORK" >&2; exit 1; }
[ -f "$RIG/gpudrv.img" ]  || { echo "missing patched driver volume" >&2; exit 1; }
for wrong in "$RIG/install-blank.raw" "$RIG/rollback/xp64-disk-good.raw"; do
  if [ -f "$wrong" ] && [ "$WORK" -ef "$wrong" ]; then
    printf 'refusing wrong or rollback disk read-write: %s\n' "$WORK" >&2
    exit 1
  fi
done

rm -f "$LOG" "$MON"

# The installer CD is deliberately not attached. Boot order c is the first hard disk, and
# the guest has to come up on its own installed Windows for the driver to bind.
# ACCEL/CPU are parameterized for A/B work (defaults reproduce the October
# config: kvm + host cpu + hpet=off). TCG needs CPU=max. The 2026-11-13 hang
# law, measured and researched: the 290X option ROM's decompressed ATOM
# runtime spins forever at guest 0xD016E (LOCK cmpxchg on card state) on
# kernel 7.2.15-zen under KVM AND TCG, on qemu 11.1.0 AND 11.1.3, while
# October's kernel 7.2.8-zen ran the identical config (kvm, hpet=off) daily.
# hpet=off is NOT the cause (October counterexample); the one diskless
# TCG+HPET clear is a single anomalous run, best explained by the racy class
# of 7.2 PCI regressions: since 10baa9b4df40 ("PCI: Drop unnecessary retries
# when restoring BARs"), devices returning not-ready after reset can lose
# their config/BAR restore, breaking VFIO passthrough device init (LKML Oct
# 2026, fixes 0f8b5d90d179 + e8df1d194c39). Dead card MMIO to the guest is
# exactly the ATOM spin's shape. The discriminating test is the LTS kernel
# (6.18.62, predates the 7.2 series): if KVM+LTS boots clean, the rig runs
# LTS until zen carries the fixes.
if [ "${ACCEL:-kvm}" = tcg ]; then MACHINE="pc,accel=tcg"; else MACHINE="pc,accel=kvm,hpet=off"; fi
qemu-system-x86_64 \
  -name xp64-driver \
  -machine "$MACHINE" \
  -cpu "${CPU:-host,-x2apic}" \
  -smp "${SMP:-4}" \
  -m 3072 \
  -rtc base=localtime \
  -nodefaults \
  -display none \
  -device piix3-usb-uhci,id=usb,addr=0x5 \
  -device usb-kbd,bus=usb.0 \
  -device usb-tablet,bus=usb.0 \
  -monitor unix:"$MON",server,nowait \
  -chardev socket,id=kd,path=/tmp/opencode/kd.sock,server=on,wait=off -chardev file,id=seabios,path=/tmp/opencode/seabios.log -device isa-debugcon,iobase=0x402,chardev=seabios -serial chardev:kd \
  -boot order=c \
  -drive file="$WORK",format=raw,if=ide,index=0,media=disk \
  -drive file="$RIG/gpudrv.img",format=raw,if=ide,index=2,media=disk \
  -device vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,\
romfile="$RIG/290x-vbios.rom",x-vga=on &

# The GL.iNet keyboard/mouse (38eb:0001) attaches through the monitor ~1.5s after
# launch - AFTER SeaBIOS's USB scan but BEFORE Windows boots, so Windows hot-plug
# claims it. Measured law: putting this device on the command line (present at
# SeaBIOS init) deadlocks the BIOS keyboard services - ntldr's menu freezes at its
# first key check and the boot never proceeds. Late attach avoids the firmware
# phase entirely; the menu itself is driven by QEMU sendkey (works - the BDA shows
# arrows enqueued and consumed).
QEMUPID=$!
for _ in $(seq 1 40); do
  [ -S "$MON" ] && break
  sleep 0.25
done
sleep 1
for _ in 1 2 3; do
  if python3 "$RIG/qmon.py" "$MON" "device_add usb-host,vendorid=0x38eb,productid=0x0001,id=glinet" 2>/dev/null | grep -qi error; then
    sleep 2
  else
    break
  fi
done

wait "$QEMUPID"