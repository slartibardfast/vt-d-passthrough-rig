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
#   * No emulated VGA: the 290X is the guest's only display in every phase. The
#     console comes from the card's own framebuffer, and SeaBIOS's CSM installs the
#     SeaVGABIOS that draws it (CONFIG_VGA_COREBOOT=y). SeaVGABIOS needs only a
#     framebuffer, which it takes from whichever device is VGA class, and with no
#     emulated VGA present that device is the card.
#
#     The card's ROM is hybrid: 64 KiB of legacy ATOMBIOS at offset 0 and a
#     58368-byte EFI image at 0x10000, signature 0x0EF1 at +4, PCIR 1002:67b0. It
#     does carry a GOP and OVMF does find it. Searching the whole ROM for _FVH /
#     "PE\0\0" / a GOP protocol GUID reports no GOP on a ROM that has one, because
#     the EFI half is a PCI-style expansion ROM block rather than an EFI firmware
#     volume, so those signatures are not there to be found. The legacy OpROM
#     cannot POST (it spins polling an unclaimed IO port, and dispatching it
#     triple-faulted the guest), which is why oprom = false keeps it out of
#     dispatch; rombar=0 is wrong now, since it hides the EFI half too.#
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