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
# FORMAT and CACHE are named rather than fixed so the iteration loop can boot a qcow2
# overlay with the caching the plan requires. The defaults reproduce the raw disk and
# QEMU's own default, which is writeback, so an unchanged invocation is unchanged.
FORMAT=${FORMAT:-raw}
CACHE=${CACHE:-writeback}
CHANNEL=${CHANNEL:-$RIG/gpudrv.img}
# GLINET=none keeps the GL.iNet composite off the bus. Its two mass-storage interfaces
# present no media, and one of them raises the modal that has blocked every instrument
# that waited on a button. Input arrives through the emulated usb-kbd and usb-tablet
# above, which is measured working, so nothing is lost. call/0013 keeps this rule
# whatever ErrorMode says.
GLINET=${GLINET:-attach}

[ -f "$WORK" ]            || { echo "missing installed disk: $WORK" >&2; exit 1; }
[ -f "$CHANNEL" ]         || { echo "missing patched driver volume: $CHANNEL" >&2; exit 1; }
for wrong in "$RIG/install-blank.raw" "$RIG/rollback/xp64-disk-good.raw"; do
  if [ -f "$wrong" ] && [ "$WORK" -ef "$wrong" ]; then
    printf 'refusing wrong or rollback disk read-write: %s\n' "$WORK" >&2
    exit 1
  fi
done

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
# exactly the ATOM spin's shape. call/0018 keeps the rig on linux-zen and
# permits only a forward kernel step: LTS is withdrawn because stock
# linux-lts does not carry the ACS override the card's isolation needs, so
# an LTS boot changes the kernel and the topology at once. The forward test
# is the newest linux-zen, one kernel change against the same candidate.
if [ "${ACCEL:-kvm}" = tcg ]; then MACHINE="pc,accel=tcg"; else MACHINE="pc,accel=kvm,hpet=off"; fi
# The argv is one array rather than an inline command, for two reasons. PRINT_ONLY can then
# print exactly what a boot would run, which is what the iteration loop's dry run needs, and
# a plan that is derived from the launcher cannot drift from the launcher.
QEMU=(qemu-system-x86_64
  -name xp64-driver
  -machine "$MACHINE"
  -cpu "${CPU:-host,-x2apic}"
  -smp "${SMP:-4}"
  -m 3072
  -rtc base=localtime
  -nodefaults
  -display none
  -device piix3-usb-uhci,id=usb,addr=0x5
  -device usb-kbd,bus=usb.0
  -device usb-tablet,bus=usb.0
  -monitor unix:"$MON",server,nowait
  -chardev socket,id=kd,path=${KD_SOCK:-/tmp/opencode/kd.sock},server=on,wait=off
  -chardev file,id=seabios,path=${SEABIOS_LOG:-/tmp/opencode/seabios.log}
  -device isa-debugcon,iobase=0x402,chardev=seabios
  -serial chardev:kd
  -boot order=c
  -drive "file=$WORK,format=$FORMAT,cache=$CACHE,if=ide,index=0,media=disk"
  -drive "file=$CHANNEL,format=raw,if=ide,index=2,media=disk"
  -device "vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,romfile=$RIG/290x-vbios.rom,x-vga=on")

if [ -n "${PRINT_ONLY:-}" ]; then
  printf 'qemu-argv: %s\n' "${QEMU[*]}"
  printf 'qemu-argv: monitor=%s glinet=%s log=%s\n' "$MON" "$GLINET" "$LOG"
  exit 0
fi

# A dry run must not disturb a live boot, so the log and the socket are cleared
# here rather than beside the guards above.
rm -f "$LOG" "$MON"

"${QEMU[@]}" &

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
if [ "$GLINET" = attach ]; then
  for _ in 1 2 3; do
    if python3 "$RIG/qmon.py" "$MON" "device_add usb-host,vendorid=0x38eb,productid=0x0001,id=glinet" 2>/dev/null | grep -qi error; then
      sleep 2
    else
      break
    fi
  done
else
  printf 'glinet=none: the composite stays off the bus, so no empty media appears\n'
fi

wait "$QEMUPID"