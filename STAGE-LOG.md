# STAGE LOG — xp64 guest from scratch

One section per runbook stage (.opencode/plan/xp64-guest-from-scratch.md).
A stage whose record is not written did not happen.

## Stage 0 — inventory and provenance (0 boots) — PASS

- Inputs checksummed into `stage0-checksums.txt`:
  stock ISO `AX2PXVOL_EN` 8fac68e1…, pristine base 266f6a81…, answer floppy (pre-rebuild)
  18ec41f2…, winnt.sif (pre-restore) 27c8c8dd….
- Volume id read from PVD sector 16 with dd: `AX2PXVOL_EN`, magic `CD001`.
- Stock root carries AMD64, I386, SETUP.EXE, WIN51, WIN51AP and **no** winnt.sif.
- winnt.sif differed from HEAD (this session had rewritten it: quoted AdminPassword,
  added AutoLogon/DriverSigningPolicy/AutomaticUpdates/UnattendedSwitch/WaitForReboot).
  Restored from HEAD per runbook rule; restored file shows `AdminPassword=password`
  unquoted and no AutoLogon, i.e. the committed October file.
- QEMU 11.1.0; `pc` = pc-i440fx-11.1. Recorded because XP-vs-modern-QEMU ACPI
  compatibility is a known issue class (QEMU #1947).
- Host vfio state: 02:00.0 + 02:00.1 (1002:67b0 / 1002:aac8) both bound vfio-pci,
  iommu_group 17, group viable. MEMORY's "group 2" is a stale renumbering.
- 290x-vbios.rom sha prefix e185ed8722af6d47 = the recorded card ROM.

## Stage 1 — answer floppy (0 boots) — PASS

- `./build-answer-floppy.sh` from the restored file: 45 CRLF, 0 bare LF.
- Floppy winnt.sif byte-identical to source; first bytes `3b 53 65 74` (";Set", no BOM);
  image 1474560 bytes; boot signature `55 aa` at offset 510.
- Floppy sha 412586e9… appended to stage0-checksums.txt.

## Stage 2 — scratch disk (0 boots) — PASS

- `xp64-scratch.raw`, 21474836480 bytes, owner dconnolly (a root-owned image is not
  writable by QEMU; that exact bit cost one failed launch earlier).
- Empty-disk sha prefix 6cb118a8f8b3c193. Leftover zeroed `xp64-fresh.raw` removed.
- No pre-partitioning: AutoPartition=1 is setup's job, and a pre-made partition would
  change what the install proves.

## Stage 3 — unattended install through CSMWrap, 290X only display (budget 2 boots) — BLOCKED

Launch line: run-install.sh (OVMF -> CSMWrap on p2 -> BBS -> stock CD, hpet=off, pc,
ide, smp 2, usb-kbd + usb-tablet, e1000, vfio 02:00.0 romfile=290x-vbios.rom).

### What the boots established

- **SeaBIOS's serial is silent.** QEMU's bundled SeaBIOS is a release build with no
  serial debug. The only firmware voice is CSMWrap's own printf, up to and including
  `UEFI ResetSystem` (the CSM handoff). Everything after the handoff is on the
  physical monitor, which is now SeaVGABIOS over the card's GOP framebuffer. This is
  why earlier "no output" readings were uninformative, and why the observation stack
  moved to serial + disk writes + the monitor.
- **The card's legacy OpROM cannot POST on this host.** Disassembly of the stall:
  CS=c000 EIP=348b, `inl %dx / testl $0x80000000 / jne` with DX=0xff04, EAX=ffffffff
  — an IO port no device claims, so the OpROM waits forever. Neither GPU holds legacy
  VGA resources (boot_vga=0, no 0x3b0-0x3df in /proc/ioports), so the kernel cannot
  expose a VGA region and `x-vga=on` is rejected outright.
- **Fork key 1 works.** `oprom = false` skips the dispatch and reaches SeaVGABIOS;
  commit 644795f, artifact 2ebc83cb…, built in the recorded image after the pin
  reproduced e7323826… byte-for-byte.
- **Fork key 2, first attempt, was wrong.** Typing the CD by PCI class cannot work:
  class code belongs to the IDE controller function and reads class=01 subclass=01
  for the disk and the CD alike. Reverted.
- **Fork key 2, corrected, works.** `RemovableBlockMedia` per media is the reliable
  signal. Commit 477d893, artifact 7af70104…. The BBS changed from
  `BBS[0] HDD, BBS[1] HDD` to `BBS[0] HDD, BBS[1] CDROM`, which is the CD now
  reachable in SeaBIOS's CMOS boot order at all.
- **Still blocked, honestly:** after the CSM handoff (`UEFI ResetSystem`) the machine
  resets before SeaBIOS prints any `Booting from`. Two launches, both ending the same
  way; the guest is found reset (CR0=0x10, IDT limit 0x3ff, code unreadable). So the
  CD is now in the boot order but SeaBIOS dies before it uses it. Not yet diagnosed.

### Budget

Stage 3 has consumed 4 boots against a budget of 2 (two were spent before the
diagnosis was in hand). Per the stop rule, work halts here for operator direction
rather than continuing to boot.
