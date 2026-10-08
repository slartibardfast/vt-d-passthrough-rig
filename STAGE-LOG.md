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

## Stage 3 — unattended install, 290X only display (budget 2 boots) — RUNNING

- Launch line: run-install.sh (stock ISO, answer floppy, hpet=off, pc, ide, smp 2,
  usb-kbd + usb-tablet, e1000, vfio 02:00.0 x-vga=on romfile=290x-vbios.rom, boot d).
- Observations to be appended below as they land.
