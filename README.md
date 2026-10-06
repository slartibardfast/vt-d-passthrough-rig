# VT-d passthrough rig

The QEMU harness that boots Windows XP x64 on an i440fx machine with an AMD Radeon
R9 290X passed through from the host, so the guest can be driven and observed from
outside. This repository holds the scripts and the installer's answer files. It holds
no guest disks and no captured frames; those are re-derivable, and the hashes that
stand in for them are recorded in the governing host's `plan/0002` and `plan/0003`.

The governing project is `agentic-winxp-x64-r290x`, which owns this software as a
Where-room component. Findings that a later session needs live in that project's
`MEMORY.md` and `call/`, not here.

## Machine shape

i440fx with PIIX3 IDE, chosen because the installed XP predates AHCI and q35's IDE is
an AHCI controller the guest cannot drive. The passed-through card sits on the PCI
root bus at `00:04.0`. QEMU's own std VGA sits at `00:02.0` and is what CSMWrap
initialises, which is deliberate: it runs SeaBIOS's `SeaVGABIOS` and `screendump`
captures it, so the guest framebuffer is observable.

CSMWrap is loaded by OVMF from a FAT16 partition on the guest disk itself rather than
from a separate helper disk, because CSMWrap sets its own boot device to the disk it
was loaded from. A separate disk makes SeaBIOS execute a non-bootable MBR and hang.

## Disk images

| image | what it is | sha256 |
|---|---|---|
| `xp64-gpu.raw` | the working guest, NTFS plus a FAT16 partition carrying CSMWrap | mutable |
| `rollback/xp64-disk-good.raw` | the pristine post-install base to rebuild from | `266f6a813d7d94d5485f4f87dfcbaa32ea1635d1761ffb8ae1e720f2f216db06` |
| `gpudrv.img` | the AMD display driver payload, FAT32 label `AMDINSTALL` | payload disk |
| `xp64-ansfloppy.img` | the XP installer's answer-file floppy | answer disk |

To rebuild the working guest from the pristine base, restore `rollback/` and copy the
base over `xp64-gpu.raw`. The pristine base is 20 GiB and is not in this repository.

## Scripts

Launch paths, all writing a serial log and exposing a QEMU monitor socket:

- `run-gpu-vm.sh` — the working configuration, with the 290X attached.
- `run-nogpu-vm.sh` — control with the card removed. Differs from the above by exactly
  one `-device vfio-pci` line, which is what makes it a usable control.
- `run-nononic-vm.sh` — control with the NIC removed, so no Option ROM is dispatched.
- `run-diag-seabios.sh` — plain SeaBIOS instead of CSMWrap. XP boots under this path,
  which is what makes it a diagnostic baseline rather than a delivery.

Guest configuration:

- `set-ini.sh` — rewrites `csmwrap.ini` on the guest's FAT16 partition. That partition
  is the boot path; `run-gpu-vm.sh` attaches only `xp64-gpu.raw`. It refuses to write
  if the ini is absent, because a script that mounts, writes and reads back proves it
  wrote a file, not that it wrote the one that is read.

Monitor and input:

- `mon.py` — run one QEMU monitor command.
- `monq.py` — the same, but drains the socket until quiet. The monitor echoes each
  character as it is typed, so a reader that stops at the echoed prompt truncates.
- `probe.py`, `walk.py`, `keywalk.py` — screen sampling and change detection.
- `sendkeys.py`, `typekey.py`, `typepass.py`, `typefill.py`, `keyfill.py`, `keyclick.py`,
  `keybox.py` — keystroke injection. The typers abort on an unmapped character, because
  a silently dropped key reads as a hung guest.
- `pickmenu.py` — chooses an entry from XP's startup-recovery menu by watching for it
  rather than by timed keystrokes. The countdown is too short to hit reliably.
- `build-answer-floppy.sh`, `winnt.sif`, `winnt-nokey.sif` — the installer's answers.

## Known traps

These cost real time and are recorded here because a later session will meet them.

- A stale QEMU holds the image lock. `sudo fuser -k xp64-gpu.raw` releases it, and a
  failed start otherwise reads as a disk problem.
- `mount` can fail silently and leave a host directory writable in its place, after
  which a `cp` appears to succeed. Every write asserts the mount is in `/proc/mounts`
  and reads the result back from the guest filesystem.
- Partition entries live at `0x1BE + 16*i`. Writing at `0x1C6` destroys entry one's
  LBA field.
- A byte diff is not a correctness check on a disk image. Assert the partition table
  before and after.
- `ps -eo comm=` truncates `qemu-system-x86` to fifteen characters, so process counts
  read zero when a VM is running.
- A single-quoted `screendump` target keeps `$i` literal.
- `boot.ini` and the INFs are CRLF, so a `$` anchor never matches in them.
