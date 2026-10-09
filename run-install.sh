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
#   * No emulated VGA: the 290X is the guest's only display in every phase. The
#     console comes from the card's own framebuffer, and SeaBIOS's CSM installs the
#     SeaVGABIOS that draws it (CONFIG_VGA_COREBOOT=y). SeaVGABIOS needs only a
#     framebuffer, which it takes from whichever device is VGA class, and with no
#     emulated VGA present that device is the card.
#
#     The card's ROM plays no part in this and could not if it tried: it is two
#     legacy ATOMBIOS expansion images of 64 KiB each with no UEFI payload at all,
#     no PE/COFF header, no EFI firmware volume, no MZ or TE image, and no GOP
#     protocol GUID. The "GOP AMD REV: x.x.x.x.x" string it does carry is an
#     unstamped ATOMBIOS placeholder in the legacy image, not a driver.
#
#     x-vga is never used. It is a legacy-VGA knob (IO ports, the 0xA0000 window,
#     BIOS-era console), not a GOP mechanism, and it is unavailable here regardless:
#     neither GPU on this host holds the legacy VGA resources (both boot_vga=0, no
#     0x3b0-0x3df range in /proc/ioports), so the kernel's vfio refuses the VGA
#     region and QEMU rejects the flag.
#
#     The card's legacy OpROM is kept out of the dispatch path entirely with
#     rombar=0, because it cannot POST: it spins polling an unclaimed IO port rather
#     than failing, and dispatching it triple-faulted the guest. The AMD driver
#     programs the card's BARs itself and never executes that OpROM, so nothing is
#     lost by hiding it.

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

# OVMF variable store: rewritten per run, so nothing persists between boots.
cp -f /usr/share/edk2/x64/OVMF_VARS.4m.fd "$RIG/OVMF_VARS.fd" 
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
  -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,unit=1,file="$RIG/OVMF_VARS.fd" \
  -drive file="$RIG/xp64-scratch.raw",format=raw,if=ide,index=0,media=disk \
  -drive file="$ISO",format=raw,if=ide,index=2,media=cdrom \
  -drive file="$RIG/xp64-ansfloppy.img",format=raw,if=floppy,index=0 \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -device vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,rombar=0 \
  "$@"