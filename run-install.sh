#!/usr/bin/env bash
# Fresh unattended install of Windows XP x64 from STOCK media, with the R9 290X as the
# guest's only display. Repeatable stage 3 of .opencode/plan/xp64-guest-from-scratch.md.
#
# Every choice below is load-bearing and each was learned the expensive way:
#
#   * Stock media, unmodified. No winnt.sif injected into the ISO: bare ISO9660 uppercases
#     names and setup's CD probe is case-sensitive, which is why an injected file reads as
#     "Couldn't Find WINNT" while sitting at the root. The answer file travels on the
#     floppy, the documented garden-variety path and the one our own October record used.
#
#   * hpet=off. The published working XP recipe sets it; QEMU's pc machine enables HPET by
#     default; XP's HAL does not want it. No run in this project set it before the runbook.
#
#   * The 290X is the only display (operator direction: using it is the point). No stdvga.
#     x-vga=on makes the vfio device primary so SeaBIOS runs the card's OpROM. Consequence:
#     screendump is blind from here on; observation is serial, disk writes, network probes,
#     offline bootlog, and the physical monitor.
#
#   * A keyboard device. Every earlier run line had -nodefaults with a tablet and no
#     keyboard, so monitor sendkey had no sink. usb-kbd is present from here on.
#
#   * -smp 2 at install and at every later boot, so the HAL chosen at install is the HAL
#     measured later, and the count matches CSMWrap's reserved-AP requirement.
#
#   * if=ide on pc/i440fx: this XP carries intelide.sys and neither msahci.sys nor
#     iastor.sys; q35's if=ide is AHCI, which it cannot drive.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
ISO=/home/dconnolly/xp64-corpus/iso/AX2PXVOL_EN.iso   # recorded known-good, MEMORY.md L394

[ -f "$ISO" ]                    || { echo "missing stock media: $ISO" >&2; exit 1; }
[ -f "$RIG/xp64-ansfloppy.img" ] || { echo "missing answer floppy; run build-answer-floppy.sh" >&2; exit 1; }
[ -f "$RIG/xp64-scratch.raw" ]   || { echo "missing scratch disk; see runbook stage 2" >&2; exit 1; }
[ -f "$RIG/290x-vbios.rom" ]     || { echo "missing card rom" >&2; exit 1; }

exec qemu-system-x86_64 \
  -name xp64-install \
  -machine pc,accel=kvm,hpet=off \
  -cpu host,-x2apic \
  -smp 2 \
  -m 4096 \
  -rtc base=localtime \
  -nodefaults \
  -display none \
  -device piix3-usb-uhci,id=usb,addr=0x5 \
  -device usb-kbd,bus=usb.0 \
  -device usb-tablet,bus=usb.0 \
  -monitor unix:"$RIG/mon.sock",server,nowait \
  -serial file:"$RIG/install-serial.log" \
  -drive file="$RIG/xp64-scratch.raw",format=raw,if=ide,index=0,media=disk \
  -drive file="$ISO",format=raw,if=ide,index=2,media=cdrom \
  -drive file="$RIG/xp64-ansfloppy.img",format=raw,if=floppy,index=0 \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -device vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,x-vga=on,romfile="$RIG/290x-vbios.rom" \
  -boot order=d,menu=off \
  "$@"