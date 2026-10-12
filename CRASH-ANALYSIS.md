# ati2mtag.sys start crash, captured live under kdc.py (2026-10-12)

## The bugcheck, read off the wire

    *** Fatal System Error: 0x00000050
        (0xFFFFFB0DAB171970, 0x0000000000000000, 0xFFFFFADFAAFD8805, 0x0000000000000005)

    *** ati2mtag.sys - Address FFFFFADFAAFD8805 base at FFFFFADFAAF49000, DateStamp 52c905a5

- PAGE_FAULT_IN_NONPAGED_AREA, read of 0xFFFFFB0DAB171970 (system-PTE range:
  a dynamic kernel mapping, not the image, not classic pool).
- Driver: the Stage-C build (D:\drv\ati2mtag.sys, DateStamp 52c905a5, identical
  to work/stageC-full/ati2mtag.sys). Load base this boot 0xFFFFFADFAAF49000.
- Faulting instruction RVA 0x8F805 (.text, file offset 0x8ee05).

## The faulting code (VMA = RVA + ImageBase 0x10000)

    9f7f5:  mov  r13, QWORD PTR [rdi+0x80]   ; table pointer from the extension
    9f7fc:  lea  rsi, [rbx+0x1f8]            ; result array
    9f803:  xor  edi, edi
    9f805:  cmp  edi, DWORD PTR [r13+0x0]    ; <-- FAULT (r13 = 0xFFFFFB0DAB171970)
    9f809:  jae  9f822                        ; edi >= *r13 -> done
    9f80b:  mov  edx, edi
    9f80d:  mov  rcx, rbp
    9f810:  call ba5c4                        ; per-index reader
    9f815:  inc  edi
    9f817:  mov  DWORD PTR [rsi], eax         ; store 4-byte result
    9f819:  add  rsi, 4
    9f81d:  cmp  edi, 0x10                    ; at most 16 entries
    9f820:  jb   9f805

A table walk: count at [r13], up to 16 DWORD entries gathered through a helper,
stored into a per-adapter array. The pointer at [adapter+0x80] targets a
system-PTE mapping whose page is not valid in this VM. Preceded by a call to
RVA 0x9a338 with edx=0x125 (succeeded - eax!=0 branch taken) and then edx=8.

## Peer cross-reference (plan/0004)

The peer's x86 patch "table-walk instruction at file 0xB1AC8 (add eax, 0x324)
changed to mov eax, 0x289750" redirects a firmware-descriptor walk into their
grafted Win7 blob. Their x64 blocker: "disassemble the function around x64 file
offset ~0x119D00-0x11A000 (the device-ID switch neighborhood) to find the
equivalent table-walk / firmware-descriptor-read code - real RE work."

This crash is that code region found empirically: the x64 equivalent walk runs
against a dynamically mapped table instead of an image-relative blob, and the
mapping is not valid under QEMU/vfio.

## Next levers

1. Who writes [rdi+0x80]: find the store in the enclosing function / init path.
2. ReadMemory the frozen machine: [rdi+0x80] itself, the r13 page, the stack at
   Rsp 0xfffff80000347d68 (kdc.py "read <addr> <len>" cmd, api 0x3130).
3. The boot breaks on XP x64: STATUS_BREAKPOINT needs GetContext+Rip+1+SetContext
   (plain Continue and Continue2+DBG_CONTINUE both re-raise the int3 - measured).
4. GetVersion: api 0x3146 (0x313C is Continue2). sizeof(DBGKD_MANIPULATE_STATE64)
   on this build: 0x40-byte bodies accepted; smaller is RESENDed.
5. FILE_IO (packet type 11, "KdIo" + DbgKdCreateFileApi 0x3430, 0x40-byte header
   then a UTF-16 path): the .kdfiles channel - the kernel asks the debugger to
   supply every driver file; a 64-byte decline (api+STATUS_UNSUCCESSFUL+zeros)
   falls back to the local file. This channel can SUBSTITUTE driver binaries
   at load time without touching the disk.

## Iteration log (continued 2026-10-12)

### Stage-D: the 0x125 fallback branch (RVA 0x8f7f3 je->jmp)

The faulting function gathers clock/capability tables. For feature id 0x125 it
tests a capability BIT at (adapter+0x148)+0x1c8 (helper RVA 0x8a338 is a bit
test, not a query) and on bit-unset walks a fallback table at [rdi+0x80]. The
sibling 0xc2/0x10f group skips safely on failure; the 0x125 group walked a dead
pointer. Patch makes unset take the success path (count 0 - the state real
hardware's success path produces). Result: the 0x8F805 crash is GONE.

### Stage-E: the shared dead-table accessor (RVA 0x8f29a)

Second boot faulted at RVA 0xafe7e: `cmp [rax],ebx` after `call 0x9f278` - the
shared accessor returning [struct+0x80], the SAME dead pointer (identical value
across boots). Patched the accessor to return a 32-zero-byte .rdata blob:
both consumers see count 0. Result: NO bugcheck all boot. PowerPlay runs
(cislands_hwmgr prints, degraded), DAL user-mode loads, and the mode-set
SETS 1280x1024 - the monitor reports an active signal.

### Display state after Stage-E

Monitor: "Black, Active Signal 1280x1024". CRTC scans, content black: no
cursor, no desktop. Boot reaches idle (KD knock-breaks answered). Whether the
session (logon/desktop) is up is unconfirmed - blind Run-dialog typing produced
no alive.txt on D:.

### KD protocol laws added (all measured)

- Responses to manipulates can be QUEUED target-side and are flushed by the
  next reset handshake. "Silent" commands are not lost: reconnect and parse.
- ReadPhysicalMemory (api 0x3139) EXISTS but returns STATUS_INVALID_PARAMETER
  for MMIO/physical display ranges - virtual reads only.
- system_reset via the QEMU monitor is a NO-OP on this setup (vfio); reboot =
  kill and relaunch run-driver.sh. Restart kdc after every reboot (the knocker
  is one-shot).
- The boot breakpoint auto-step needs ntbase; classify breaks by exception
  address (boot break = nt+0x4b350, fatal break = elsewhere).
- Read route that works: read-VM (api 0x3130) on VIRTUAL addresses. The
  MMIO/VRAM mappings' VAs are the missing link (find via the driver globals or
  the adapter extension).

### Next moves (in order)

1. Find the adapter extension base (DriverEntry global store) via the extracted
   ntoskrnl-free static analysis; read-VM [adapter+BAR mapping fields].
2. With the MMIO VA: read GRPH_PRIMARY_SURFACE_ADDRESS / CRTC state live, and
   the framebuffer content through the VRAM window mapping.
3. PowerPlay "Invalid ASIC family" (cislands_hwmgr.c[6179]): the family check
   reads a value the VM fills differently; fixing it may complete the display
   unblank path.
4. Session liveness: alive.txt via the Run dialog failed - try after each boot;
   auto-logon registry state unverified.

### The extension walk (the read toolchain)

kdread.sh/kddump.sh: read-vm with the reconnect-flush cycle automated. The
chain measured this boot: DriverObject global at driver+0x606e40 ->
0xfffffadfb51a1e70; [DriverObject+8] -> DO 0xfffffadfb5172a70;
[DO+0x40] -> the VideoPort extension 0xfffffadfb5172bc0 ("vPrt" magic;
DeviceExtension is +0x40 on x64, not +0x38). The AMD miniport extension hangs
off the vp extension; candidates for its BAR mappings: 0xfffffadfad22c000 /
0xfffffadfad232000 (page-aligned pointers at vp+0x4b0/0x2d8). MMIO
identification probe pending (intermittent target deafness: retries work).

The watch order for the display question, once the MMIO VA is found:
GRPH_PRIMARY_SURFACE_ADDRESS (DCE8 ~0x6a04) vs the GDI surface, GRPH_ENABLE,
CRTC_MASTER_EN, and the framebuffer content through the VRAM window. PowerPlay
"Invalid ASIC family" (cislands_hwmgr.c[6179]) remains the likely unblank
blocker; its family read is fed by values the VM fills differently.

### USB input, controller passthrough assessment, and the safe-mode hang (2026-10-12 late)

Safe mode (/safeboot:minimal, dual-entry boot.ini now): 77 loads, the full USB
HID stack loads (usbport/usbuhci/USBD/usbhub/HIDPARSE/HIDCLASS/hidusb/kbdhid/
mouhid), win32k + vga.dll + framebuf.dll load (session display driver up), the
PnP safe-filter prints its last line (AFD.SYS skip) - then the boot SILENTLY
HANGS before logon. The operator sees the frozen boot-logo/logon-scr-like
image. Input "not working" is this hang, not USB: the stacks are all up.

Controller-level passthrough assessment (the operator's ask):
- 00:14.0 Intel xHCI (where the GL.iNet lives): XP x64 has no xHCI driver -
  passing it is a dead end.
- 00:1a.0 / 00:1d.0 Intel EHCI: clean IOMMU groups (8 / 14) but zero ports
  appear wired on this board - nothing to plug into.
- 0b:00.0 ASMedia ASM1042A: clean group (27), ASMedia shipped XP x64 drivers;
  VIABLE if the GL.iNet replugs into the card's ports (driver download + stage
  to the guest still needed).
All four controllers are alone in their IOMMU groups - individually passable.

kdread/kddump walk achieved this boot: DriverObject global (driver+0x606e40)
-> DO 0xfffffadfb5172a70 -> vp extension 0xfffffadfb5172bc0 (1024 bytes dumped
to /tmp/opencode/vpext.bin). The AMD miniport extension and the BAR mapping VAs
are the next hop. Also measured: poll_cmd exceptions killed the client - now
guarded; response dumps no longer capped at 0x40.

The hang experiment queued: freeze the next stall via knock, GetContext +
read-vm the stuck thread's stack - it names the waiter.

### Operator confirmed: all wired ports are USB3 (2026-10-12)

Both wired controllers are xHCI (Intel 00:14.0 + ASMedia 0b:00.0); the two
EHCI controllers have no physical ports. Controller passthrough for XP x64 is
therefore only the ASMedia card + its vendor XP driver + a replug into the
card's ports. Deferred as a fallback.

Priority finding: the input failure is the pre-logon hang, not USB - the full
HID stack loads and the devices enumerate in safe mode; the boot then hangs
after win32k/framebuf and before logon, identically in safe and normal modes.
Next experiment: reproduce the safe-mode hang, freeze at the stall with a
knock-break, GetContext + read-vm the stuck thread's stack - it names the
waiter. Working recipe: knock -> break -> ACK -> (hold-all absent: auto-step
only for nt-range addresses; hold-all makes every break hold) -> commands in
the same-call window -> reconnect to flush queued responses.

### The firmware keyboard: works, and the menu hang root-caused (2026-10-12 late)

The boot.ini menu (dual-entry, 30s) is the keyboard test bench - visible on the
physical monitor. Result: SeaBIOS's keyboard path WORKS. Proof: after L sent
six down-arrows via sendkey, the BDA keyboard buffer showed head=tail=0x2a -
the walk from 0x1e is exactly six enqueued-and-consumed keystrokes, with the
last residue word 0x50e0 = an E0-prefixed down-arrow scancode. The operator
read the highlight as stuck because only two entries exist: the first arrow
moved it, the rest clamped.

But with the GL.iNet moved to the QEMU COMMAND LINE (present at SeaBIOS init),
the menu FROZE - countdown never expired, ntldr spinning in 16-bit code at
0x20000:0x3e1, the BDA intact, the tick advancing. Detaching the device at
runtime did not unblock (a transfer already submitted to the emulated UHCI for
the removed device stays stuck). Reverting to the monitor-based late attach
(+1.5s - after SeaBIOS's USB scan) unfroze the menu on the next boot: 170
loads, clean boot.

LAW: the GL.iNet must attach AFTER SeaBIOS's USB scan. At the BIOS phase the
physical keyboard is dead by design (it would hang the menu); QEMU sendkey
drives the menu. From Windows on, the GL.iNet hot-plugs and the HID stack
claims it.

Also measured this boot: /safeboot edit must not use a $ anchor (CRLF lines);
the boot stall reproduces every boot at the same era (last print = the DAL
registry error, then silence); hold-all now freezes every break for manual
inspection - the frozen specimen at the stall is the next session's starting
point (GetContext + stack read names the waiter; prime suspect: a display
interrupt/vblank wait that never completes under vfio).

### The debugger eliminated as the hang cause (2026-10-12 night)

Booted the normal entry with /debug REMOVED from boot.ini entirely. Result:
identical - active 1280x1024 signal, black content, no cursor. The KD
interaction (print blocking, queueing, break stalls) is REFUTED as the
black-screen cause. The state is intrinsic to the ati2mtag display path.

Narrowed mechanism: the CRTC runs the EDID-native timings (the monitor syncs),
but the scanned surface is black - no cursor sprite, no logon, no desktop. The
candidate: GRPH_PRIMARY_SURFACE_ADDRESS scans a cleared region while GDI draws
into a different allocation (or the plane is disabled/blanked with timing
active). Measuring it needs the driver's BAR mapping VAs - the extension walk
(DriverObject -> DO -> vp extension done; the AMD miniport extension and its
MMIO/VRAM mapping fields are the remaining hop) and then read-vm of
GRPH_PRIMARY_SURFACE_ADDRESS vs the GDI surface.

Also confirmed: safe mode (vga.sys + framebuf) DISPLAYS through the same card -
the hardware and scanout path work; the black is specific to the ati2mtag
driver's surface/scanout arrangement in this VM.
