#!/usr/bin/env bash
# Boot Windows XP x64 with the R9 290X passed through, on q35 with SMM.
#
# This is the SMM path. XP x64 on an ACPI platform requires System Management Mode,
# this platform publishes ACPI 2.0, and i440fx has no SMM of its own. Measured on the
# pc machine: reading IA32_SMM_FEATURE_CONTROL raises #GP, because KVM implements no SMM
# monitor there. That is not something CSMWrap can supply, since the state-save area,
# the two control MSRs and SMI entry all belong to the platform below it.
#
# q35 does have an SMM monitor, and smm=on turns it on. Both feasibility checks passed on
# this host: KVM accepts q35,smm=on, and a PIIX3 IDE controller attaches to q35, which
# matters because the installed XP has intelide.sys and no AHCI driver at all. QEMU's
# built-in q35 IDE is an ICH9 AHCI controller this guest cannot drive.
#
# Differences from run-gpu-vm.sh, all deliberate:
#   * q35 with smm=on, for the SMM monitor XP requires.
#   * piix3-ide plus an explicit ide-hd, because the guest predates AHCI.
#   * the passed-through card sits behind a pcie-pci-bridge, since q35's root bus is
#     PCIe and the card is a conventional PCI device.
#   * CSMWrap still lives on the guest's own FAT16 partition. There is no helper disk and
#     no virtio disk: CSMWrap sets its own boot device to the disk it was loaded from.
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
#
# The guest image is the same xp64-gpu.raw the pc path uses. If the disk controller
# turns out to be the wrong shape for this machine, the answer is to rebuild the guest
# from rollback/xp64-disk-good.raw rather than to contort the controller.
set -euo pipefail

RIG=/home/dconnolly/xp64-rig
GPU=02:00.0

cd "$RIG"

# OVMF variable store: rewritten per run, so nothing persists between boots.
cp -f /usr/share/edk2/x64/OVMF_VARS.4m.fd "$RIG/OVMF_VARS.fd"

# shellcheck disable=SC2086
exec qemu-system-x86_64 \
  -name xp64-290x-q35 \
  -machine q35,smm=on,accel=kvm \
  -cpu host,-x2apic \
  -smp 2 \
  -m 4096 \
  -rtc base=localtime \
  -nodefaults \
  -display none \
  -vga std \
  -monitor unix:"$RIG/mon.sock",server,nowait \
  -serial file:"$RIG/csmwrap-serial.log" \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,unit=1,file="$RIG/OVMF_VARS.fd" \
  -drive if=none,id=xp64,format=raw,file="$RIG/xp64-gpu.raw" \
  -device piix3-ide,id=ide0 \
  -device ide-hd,drive=xp64,bus=ide0.0 \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pcie.0,addr=0x3 \
  -device pcie-pci-bridge,id=br0,bus=pcie.0,addr=0x5 \
  -device "vfio-pci,host=$GPU,id=radeon290x,bus=br0,addr=0x0,rombar=0" \
  -boot order=c \
  "$@"