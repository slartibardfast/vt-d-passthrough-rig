#!/usr/bin/env bash
# Fresh unattended install of Windows XP x64 from STOCK media, with the R9 290X passed
# through as a PCI display device and an emulated std VGA carrying the console.
#
# This replaces run-install.sh, which drove the install through CSMWrap + OVMF with the
# 290X as the guest's only display. That arrangement could not deliver video unless a UEFI
# phase initialised the card through its GOP, and a UEFI phase holding the machine is what
# forces the CSMWrap architecture in the first place. Measured: under CSMWrap the console
# works and the kernel hangs; under SeaBIOS the kernel boots on all processors and the
# card renders into an aperture it is not scanning out, because nothing ran the vendor
# init that brings up its PLL, memory clock and output mux.
#
# A display driver does not need any of that. It programs the BARs and registers itself,
# and ATOMBIOS is a POST-time artefact. So the card only has to be *present* for Windows
# to drive it - it does not have to be the boot display.
#
# Every choice below is load-bearing:
#
#   * SeaBIOS, and only SeaBIOS. No pflash, no OVMF, no CSMWrap. SeaBIOS's own CSM is
#     already proven here: the seabios-stdvga corner reaches the XP x64 kernel with all
#     four vCPUs in kernel code. QEMU and SeaBIOS then own the machine description
#     outright, so it cannot disagree with the hardware.
#
#   * -smp 4, not 2. With CSMWrap gone there is no reserved AP, so the count is now a
#     free variable rather than one constrained by the firmware. The seabios-card and
#     seabios-stdvga corners were both measured at -smp 4 with all vCPUs up.
#
#   * An emulated std VGA carries the console. This is the scope change: the deliverable
#     originally specified no std VGA. It is the install-time crutch, and the acceptance
#     criteria - driver loads, no Code 10, 256 MB VRAM, output on the card's own
#     connector, acceleration - are all driver-level and none of them needs the card to
#     be the boot display. Once Windows is running, making the card primary and dropping
#     std VGA is a driver/display-settings change rather than a firmware one.
#
#   * The card is passed through whole: real device, all 128 KiB of ROM, all BARs. The
#     only change is the two bytes of the legacy Option ROM header. SeaBIOS's
#     is_valid_rom() tests signature != 0x55AA before anything else, so zeroing them
#     makes init_optionrom() return -1 without dispatching, and is_pci_vga() - which
#     needs no ROM at all, only class 0x0300 plus PCI_COMMAND IO+MEMORY plus bridge VGA
#     enable - leaves the card a fully capable VGA device. Nothing is lost: the bytes
#     from offset 2 on are identical, and a display driver takes its identity from PCI
#     config space, not from the ROM header.
#
#     The legacy OpROM must not run. The card's ATOMBIOS contains 411 IN/OUT
#     instructions, clustered into a register-poll loop at 0x3480-0x3493, and the
#     seabios-card corner triple-faulted at exactly RIP 0x348b when SeaBIOS dispatched it.
#
#   * -boot order=d. QEMU's pc machine defaults to floppy,disk,CD, and stock SeaBIOS takes
#     the empty target disk instead of the installer.
#
#   * hpet=off. QEMU's pc machine enables HPET by default; XP's HAL does not want it.
#
#   * if=ide on pc/i440fx: this XP carries intelide.sys and neither msahci.sys nor
#     iastor.sys; q35's if=ide is AHCI, which it cannot drive.
#
#   * A keyboard, or monitor sendkey has no sink.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
ISO=/home/dconnolly/xp64-corpus/iso/AX2PXVOL_EN.iso   # recorded known-good, MEMORY.md L394
SMP=${SMP:-4}
WORK=${WORK:-$RIG/install-work.raw}
LOG=${LOG:-$RIG/install-seabios.log}
MON=${MON:-$RIG/mon.sock}

[ -f "$ISO" ]                       || { echo "missing stock media: $ISO" >&2; exit 1; }
[ -f "$RIG/xp64-ansfloppy.img" ]    || { echo "missing answer floppy; run build-answer-floppy.sh" >&2; exit 1; }
[ -f "$RIG/xp64-scratch.raw" ]      || { echo "missing scratch disk; see runbook stage 2" >&2; exit 1; }
[ -f "$RIG/290x-legacy-off.rom" ]   || { echo "missing derived card ROM" >&2; exit 1; }

# Work on a copy so the pristine scratch disk survives a failed install. reflink needs
# both paths on one filesystem; $RIG and the scratch disk are both under /home.
if [ ! -e "$WORK" ]; then
  cp --reflink=always "$RIG/xp64-scratch.raw" "$WORK"
  echo "  working disk: $WORK (reflink copy of xp64-scratch.raw)"
fi
rm -f "$LOG" "$MON"

exec qemu-system-x86_64 \
  -name xp64-install-seabios \
  -machine pc,accel=kvm,hpet=off \
  -cpu host,-x2apic \
  -smp "$SMP" \
  -m 4096 \
  -rtc base=localtime \
  -nodefaults \
  -display none \
  -device VGA,id=console \
  -device piix3-usb-uhci,id=usb,addr=0x5 \
  -device usb-kbd,bus=usb.0 \
  -device usb-tablet,bus=usb.0 \
  -monitor unix:"$MON",server,nowait \
  -serial file:"$LOG" \
  -boot order=d \
  -drive file="$WORK",format=raw,if=ide,index=0,media=disk \
  -drive file="$ISO",format=raw,if=ide,index=2,media=cdrom \
  -drive file="$RIG/xp64-ansfloppy.img",format=raw,if=floppy,index=0 \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -device vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,\
romfile="$RIG/290x-legacy-off.rom"