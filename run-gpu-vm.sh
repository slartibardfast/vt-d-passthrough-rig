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
#   * SeaBIOS runs at CONFIG_DEBUG_LEVEL=1 and says nothing after handoff, so the
#     screen is the only instrument during the legacy phase.
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
  -device VGA,id=stdvga,addr=0x2 \
  -monitor unix:"$RIG/mon.sock",server,nowait \
  -serial file:"$RIG/csmwrap-serial.log" \
  -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd \
  -drive if=pflash,format=raw,unit=1,file="$RIG/OVMF_VARS.fd" \
  -drive file="$RIG/xp64-gpu.raw",format=raw,if=ide,index=0,media=disk \
  -netdev user,id=net0 \
  -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -device vfio-pci,host="$GPU",id=radeon290x,bus=pci.0,addr=0x4,\
romfile="$RIG/290x-vbios.rom" \
  -boot order=c,menu=off \
  "$@"