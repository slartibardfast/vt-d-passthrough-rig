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
root bus at `00:04.0` and is the guest's only display: no emulated VGA is attached in
any phase.

The console is the card's own framebuffer. SeaBIOS's CSM installs the `SeaVGABIOS`
that draws it, and `SeaVGABIOS` takes its framebuffer from whichever device is VGA
class, which with no emulated VGA present is the card. The card's ROM does not supply
the CSM-phase console, which SeaVGABIOS provides directly; the ROM is nonetheless
hybrid rather than legacy-only (64 KiB of ATOMBIOS at offset 0, a 58368-byte EFI image
at `0x10000`, signature `0x0EF1` at +4), so it carries a GOP and OVMF finds one. An
earlier version of this file said it could not, on the strength of a whole-ROM search
for `_FVH` / `"PE\0\0"` / a GOP GUID: the EFI half is a PCI-style expansion ROM block
rather than an EFI firmware volume, so those signatures are absent by construction and
the search reported no GOP on a ROM that has one. `x-vga` is never used; it is a
legacy-VGA knob rather than a GOP mechanism, and this host assigns no legacy VGA
resources to any GPU, so the kernel's vfio refuses the VGA region.

The consequence for observation is that `screendump` no longer applies: QEMU captures
emulated framebuffers only, and there is no longer an emulated one. The channels that
remain are the serial log, the QEMU monitor and debugger over guest RAM, the physical
monitor, and writes to the guest disk.

CSMWrap is loaded by OVMF from a FAT16 partition on the guest disk itself rather than
from a separate helper disk, because CSMWrap sets its own boot device to the disk it
was loaded from. A separate disk makes SeaBIOS execute a non-bootable MBR and hang.

Two processors. CSMWrap reserves the highest-numbered AP as its own system thread and
hides it from the OS by patching the MADT, so at `-smp 2` the OS sees a single processor
and never brings up an application processor. This is load-bearing rather than a
performance choice: at `-smp 4` the three OS-visible processors deadlock on a kernel
spinlock while the reserved AP sits in QEMU's post-triple-fault reset state, and at
`-smp 1` CSMWrap halts with `No AP available for BIOS proxy`. See the comment block in
`run-gpu-vm.sh` for the full account.

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

Diagnosing a boot:

- `probe-freeze.sh [settle] [samples]` — runs the VM, waits until the framebuffer stops
  changing, then reads **every** vCPU's registers through the monitor and disassembles
  the BSP's RIP. Reading all four is the point: a vCPU that triple-faulted shows
  `CR0=0x11` with `CR3`/`CR4`/`EFER` zero and no RIP at all, which is what
  distinguishes "the OS lost a processor" from "the OS is merely slow".
- `probe-login.sh [settle]` — boots, types the Administrator password, and captures the
  logon screen and the desktop. Reaching the logon screen proves only that the loader
  and kernel started.
- `ppm2png.py in.ppm out.png [factor]` — converts a QEMU screendump to PNG. There is no
  ImageMagick on this host, and reading the guest's screen is the only way to tell a
  booted Windows from a frozen boot, so this is a rig tool rather than a convenience.
  Nearest-neighbour downscaling, because the things being read are text screens and
  progress bars where smoothing would blur the pixels that carry the information.
- `EXTRA_QEMU` is honoured by `probe-freeze.sh`, word-split on purpose, so a sweep can
  vary exactly one flag against an otherwise identical machine.

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
- `build-answer-floppy.sh`, `winnt.sif`, `winnt-nokey.sif`, the installer's answers.

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
- `set-ini.sh` only ever writes. It takes a list of keys to set, and every argument is
  appended, so calling it with a placeholder key to *read* the file injects that key
  into the guest's configuration. Mount the partition and read it directly instead.
- The QEMU monitor needs `0x` in front of an address: `x/8i fffff800...` answers
  "invalid char 'f' in expression" and reads like a bad address.
- `cpu N` followed by a failed `info registers` leaves the *previous* CPU selected, so
  a sweep over `cpu 0..3` silently reports the same CPU twice. Check that the reply
  names the CPU asked for rather than assuming the switch took.
- `x/16i` and `xp` accept `0x...`; there is no `xm` command in this QEMU.
- A booted XP desktop is a *static* framebuffer, so "the screen stopped changing" is not
  by itself evidence of a hang. A screendump has to be looked at.
