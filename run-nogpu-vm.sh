# CONTROL: CSMWrap with NO GPU passed through, differing from run-gpu-vm.sh by
# exactly the -device vfio-pci line and nothing else.
#
# That was not true when this control was first written, and it is the whole point of a
# control. It also ran at -smp 4 and had no USB tablet, so it varied three things at once,
# and -smp 4 is now known to deadlock on its own for a reason that has nothing to do with
# the GPU. A run of that script could not have answered the question it existed to answer,
# and its header claimed otherwise. Both differences are fixed here so the only variable
# is the card.
#!/usr/bin/env bash
# Boot Windows XP x64 with the R9 290X passed through, through OVMF and CSMWrap.
#
# Every choice below is load-bearing. The ones that cost the most to learn:
#
#   * CSMWrap lives on the XP disk, not on a separate helper disk. CSMWrap sets
#     its own boot device to the disk it was loaded from, so a helper disk makes
#     SeaBIOS try to boot that non-bootable disk and hang. The FAT partition is
#     carved from free space that already existed past the NTFS partition, so
#     the NTFS partition is not moved and not renumbered.
#
#   * The machine is i440fx with PIIX3 IDE, not q35. The installed XP has
#     intelide.sys and no msahci.sys or iastor.sys, so it was installed against
#     an Intel IDE controller. q35's if=ide is AHCI, which this XP cannot drive.
#
#   * std VGA is kept. Dropping it was tried and is strictly worse: with no
#     emulated VGA there is no GOP anywhere, and CSMWrap fails with
#     "Failed to locate GOP handles: 14" before reaching the GPU at all. It is
#     kept because it is the only source of a GOP CSMWrap can use, not because
#     it drives the output. x-vga is never used.
#
#   * The card's OpROM is its own hash-verified dump, so CSMWrap is offered the
#     exact vendor VBIOS rather than SeaVGABIOS.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
GPU=02:00.0

cd "$RIG"

# OVMF variable store: rewritten per run, so nothing persists between boots.
cp -f /usr/share/edk2/x64/OVMF_VARS.4m.fd "$RIG/OVMF_VARS.fd"

exec qemu-system-x86_64 \
  -name xp64-nogpu \
  -machine pc,accel=kvm \
  -cpu host,-x2apic \
  -smp 2 \
  -m 4096 \
  -rtc base=localtime \
  -nodefaults \
  -display none \
  -device VGA,id=stdvga,addr=0x2 \
  -device piix3-usb-uhci,id=usb,addr=0x5 \
  -device usb-tablet,bus=usb.0 \
  -monitor unix:"$RIG/mon.sock",server,nowait \
  -serial file:"$RIG/csmwrap-serial.log" \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,unit=1,file="$RIG/OVMF_VARS.fd" \
  -drive file="$RIG/xp64-gpu.raw",format=raw,if=ide,index=0,media=disk \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -boot order=c,menu=off \
  "$@"