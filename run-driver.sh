#!/usr/bin/env bash
# Boot the installed XP x64 guest with the patched driver volume attached.
#
# plan/PLAN.md lists the driver's acceptance as fixed criteria rather than impressions:
#   - ConfigManagerErrorCode is 0
#   - VRAM reads 268435456 (256 MiB)
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

RIG=/home/dconnolly/xp64-rig
WORK=${WORK:-$RIG/install-blank.raw}
MON=${MON:-$RIG/mon.sock}
LOG=${LOG:-$RIG/driver-stage.log}

[ -f "$WORK" ]            || { echo "missing installed disk: $WORK" >&2; exit 1; }
[ -f "$RIG/gpudrv.img" ]  || { echo "missing patched driver volume" >&2; exit 1; }

rm -f "$LOG" "$MON"

# The installer CD is deliberately not attached. Boot order c is the first hard disk, and
# the guest has to come up on its own installed Windows for the driver to bind.
qemu-system-x86_64 \
  -name xp64-driver \
  -machine pc,accel=kvm,hpet=off \
  -cpu host,-x2apic \
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

# The physical GL.iNet keyboard/mouse (38eb:0001) is passed through to the guest on
# every launch. The host side never claims it (udev rule 91-glinet-passthrough.rules
# unbinds usbhid on bind); the attach still retries and tolerates failure so a
# missing or busy device can never block a boot.
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