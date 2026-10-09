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
#   * The card's OpROM is its own hash-verified dump, so CSMWrap is offered the
#     exact vendor VBIOS rather than SeaVGABIOS.
#   * The machine gets two CPUs, not four. CSMWrap reserves the highest-numbered
#     AP as its own system thread and hides it from the OS by patching the MADT,
#     but SeaBIOS separately learns the CPU count from fw_cfg, which counts every
#     vCPU QEMU created including the one CSMWrap stole, and it builds the legacy
#     MP table from that. At -smp 4 the OS-visible processors are APIC 0, 1 and 2,
#     and the boot deadlocks: the three live CPUs end up spinning on one kernel
#     spinlock while the reserved AP sits in QEMU's post-triple-fault reset state,
#     CR0=0x11 with no RIP. At -smp 2 there is exactly one OS-visible processor,
#     no application processor is ever brought up, and Windows XP x64 boots to the
#     logon screen and then to the desktop. At -smp 1 CSMWrap halts outright with
#     "No AP available for BIOS proxy", so two is the floor and the smallest count
#     that boots is the one used here. probe-freeze.sh reads every vCPU's
#     registers at the freeze, which is how the deadlock was told apart from a
#     slow boot.
#
#   * A USB tablet is attached, and it is load-bearing for anything measured rather
#     than merely watched. The machine runs with -nodefaults, so without it the only
#     pointing device is the i8042's relative PS/2 mouse, and the QEMU monitor's
#     mouse_move takes absolute coordinates. An absolute move sent to a relative
#     mouse goes nowhere, so clicking the Administrator tile on the XP logon screen
#     silently did nothing. A run would then sit at the logon page with an empty
#     password box, which looks exactly like a kernel refusing a logon and cost
#     several runs before it was noticed. logon.py now confirms the click against
#     the framebuffer so this cannot fail silently again. The UHCI controller is
#     named because -nodefaults suppresses the default one, and SeaBIOS here has
#     USB_UHCI and USB_MOUSE built in.
#
#   * The CSM's SeaBIOS is built with CONFIG_DEBUG_LEVEL=1 and CONFIG_DEBUG_SERIAL=y, so
#     its boot messages land on 0x3f8 alongside CSMWrap's own. Without that the CSM phase
#     is mute, and "no serial output" carries no information: with no emulated VGA there is
#     no framebuffer to screendump either, so the serial log is the only readable channel
#     the guest has before the XP driver loads. The level stays at 1 rather than 3 because
#     Csm16.bin is 128 KiB against a 64 KiB F segment, and the UMA heap the CSM allocates
#     from shrinks as the binary grows; at level 3 it runs out and CD boot fails with
#     "Unable to allocate resource at cdrom_prepboot".
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
GPU=02:00.0

cd "$RIG"

# OVMF variable store: rewritten per run, so nothing persists between boots.
cp -f /usr/share/edk2/x64/OVMF_VARS.4m.fd "$RIG/OVMF_VARS.fd"

exec qemu-system-x86_64 \
  -name xp64-290x \
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
  -device vfio-pci,host="$GPU",id=radeon290x,bus=pci.0,addr=0x4,\
rombar=0 \
  -boot order=c,menu=off \
  "$@"