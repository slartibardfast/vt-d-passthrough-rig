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

### The GLKVM eyes: the display instrument (2026-10-12 night)

The chromium CDP driver (cdp.py) logs into the GLKVM web UI and opens the KVM
feed. First catch: the SAFE-MODE DESKTOP live through the 290X - "Safe Mode"
corners, Build 3790 SP1, the safe-mode notice dialog with Yes/No, streaming
640x480 H.264 60fps. The display pipeline under Windows is CONFIRMED visually.

Input findings: QEMU sendkey (PS2/i8042) does not reach the Windows session.
The CDP events forward through the GLKVM session into the guest, but the
forwarding GATES after the first batch (the local cursor sprite tracked the
first sweep only). The enter key through the CDP path DID dismiss the safe-mode
notice once (the splash followed - proof of input), then the gating returned.
Fix needed: a persistent CDP connection with the focus/enable sequence
(mouseMoved over the video + the container focus) re-issued per event, or the
GLKVM UI's own input toggles driven. Until then the input is one-shot per
connection.

Also measured: the safe boot loads ati2mtag.sys from D:\drv even in safe mode
(the service is system-start pointing at D:\drv) - now disabled offline
(hivexregedit, ControlSet001\Services\ati2mtag Start=4, verified by read-back).
The safe boot with the driver disabled reaches the same notice dialog - the
AMD driver is not the blocker for reaching the dialog; whether the desktop
loads past it without the driver is the next test. The safe boot's NTBTLOG
ends at the ipnat.sys skip (srv.sys loaded just before) - the unflushed tail
died with the hang.

The machine state left: safe mode, the notice dialog up, the AMD driver
disabled in the registry, the eyes live, input one-shot per CDP connection.

### The safe-mode splash stall and the KVM-side research (2026-10-13)

The safe boot (ati2mtag disabled offline, Start=4 verified) reached the same
notice dialog, then cycled the XP splash logo for over an hour - the desktop
never arrived. The AMD driver is therefore CLEARED as the safe-mode splash
blocker. The "screensaver" the operator reported is this sliding splash: safe
mode has never reached a desktop in any observed boot.

KVM-side research (the operator's hypothesis, confirmed as a real issue class):
the GL.iNet forum and docs document Comet RM1 input-forwarding failures
("Mouse and keyboard don't work after upgrade", "Comet KVM: USB Input Fails
Post-Login + Phantom Display Issue in Windows"), the Relative-mouse-mode fix,
the Virtual-Media disable fix (some targets disable all USB input when the
KVM's virtual storage device appears - matching "Windows did not manage to
claim USB devices"), the device-identity change, and the firmware-upgrade path.
The docs' Display Issues section covers "only displays the desktop wallpaper"
(Extend-display mode) - and the forum thread reports a Windows PHANTOM DISPLAY
on this exact device class: Windows renders to a display head the KVM does not
capture. The phantom-head theory is now the leading candidate for the
normal-mode black screen: the ati2mtag driver enumerates several outputs, the
desktop may extend onto a head the GLKVM never sees, while the visible head
scans timing only.

Also measured: QEMU sendkey does not reach the Windows session (the i8042 path
is dead in-Windows; the BDA proof only covers the BIOS era). All in-Windows
input goes through the GLKVM session via CDP-injected events.

The GLKVM input law: CDP-injected events forward once per page load, then the
forwarding gates; a persistent connection with a per-event enable sequence is
the pending fix (cdp.py is single-shot per invocation today).

State left: the safe boot frozen at the splash, the AMD service disabled
(Start=4), boot.ini dual-entry with BOTH entries currently safeboot (the next
session must restore entry 1 to the normal /debug configuration before any
normal-mode experiment). The GLKVM eyes and cdp.py are working instruments.

### The stall loop identified in the kernel binary (2026-10-13)

The frozen boot's RIP (nt+0x7192e, measured via the QEMU monitor during the
stall) decodes in the extracted ntoskrnl.exe (ImageBase 0x400000, .text at
VMA 0x401000) as the kernel IDLE LOOP: rbx = gs:0x20 (the PRCB), CR8 raised,
then the poll sequence `pause / sti / nop / cli` reading the PRCB pending-work
fields (+0x1f18, +0x1f60, +0xd88) and dispatching to 0x455470 only when they
are set. The registers sampled in the stall match this loop exactly.

Conclusion: the CPU is truly idle - no pending DPCs, no timers, no deferred
work. The pre-logon hang is a thread blocked on something that is never
signaled (an event/semaphore wait), invisible to the idle loop. Naming it
requires the thread-list walk (the ntoskrnl offsets task) or the guest's own
logs once input works.

### The workshop session (2026-10-13, the desktop reachable)

The normal /debug boot with ati2mtag disabled REACHED THE DESKTOP (vgasave,
640x480, visible on the GLKVM feed). The pre-logon hang did not reproduce with
the driver disabled. New measured laws:

- sendkey WORKS in-Windows now: the emulated usb-kbd (-device usb-kbd) carries
  it. The old "sendkey dead in-Windows" law is falsified for the current config.
  sendkey key names: spc (not sp), slash, minus, backslash, shift-semicolon,
  shift-dot, shift-7; uppercase names are silently dropped.
- D: (AMDINSTALL, gpudrv.img FAT32) round-trips live: guest writes flush
  fast enough for rig-side RO mounts.
- ati2mtag in-guest: Start=4, ImagePath \??\D:\drv\ati2mtag.sys (the driver
  loads off the channel disk), Enum=PCI\VEN_1002&DEV_67B0.
- The GLKVM input chain is broken at the usbfs layer: QEMU (running as
  dconnolly) could never open /dev/bus/usb (root:root 0664) - the lazy open
  failed EPERM, the guest addressed but never configured the device (UDC stuck
  at "addressed"), no endpoint polls, /dev/hidg0 writes blocked forever. Fixed
  by MODE=0666 in 91-glinet-passthrough.rules; the chain still needs the guest
  to configure the device on a fresh attach (untested after the fix - the
  desktop is driven by sendkey meanwhile).
- ntbtlog always ends at the "ipnat skip" line on every boot (services-phase
  loads are not logged); it is not a hang marker.

### The driver-enabled boot with the fixed USB chain (2026-10-13, late)

With ati2mtag Start=1 and the GLKVM usbfs EPERM fixed (MODE=0666):

- The boot COMPLETED: services phase done by T+20s (SysEvent.Evt: Terminal
  Services running), auto-logon of Administrator at T+22s (SecEvent 528 type 2,
  AutoAdminLogon - the desktop comes up by itself).
- The patched driver LOADED CLEANLY (ntbtlog final block: "Loaded
  \??\D:\drv\ati2mtag.sys" after PnP retries - it loads off the FAT channel
  disk, which is why the early attempts fail). No bugcheck. No pre-logon hang.
  The multi-session pre-logon hang is RETIRED: it was plausibly the GLKVM
  device wedging USB enumeration, not the driver and not the display stack.
- The driver mode-sets the CAPTURED connector (the feed locks 1280x1024) but
  the pixels are black: Windows renders to a surface no visible head scans
  out. This is the phantom-head defect, now isolated as THE remaining display
  problem. The intelppm halt observed during this state is the NORMAL XP x64
  idle path (the processor driver owns the idle HLT), not a wedge.
- The GLKVM chain CONFIRMED fixed: usbccgp.sys loaded (the composite parent
  configured), kbdhid bound the keyboard interface, the guest popped the
  "perform faster" balloon. The dongle input path is alive at the USB level.
- Open: sendkey input did not execute anything on this boot (the prefetch
  witness shows no cmd/tasklist runs despite the blind Run-dialog dance).
  The keyboard mystery on the driver-era desktop is the first question for
  the next session; the screen-capture tool (a tiny GDI exe via D:) is the
  missing eye for the phantom-head work.
- KD lesson: killing the kdc daemon mid-connection deafens the target's KD
  engine (the reset/breakin churn wedges it). Restart the daemon without
  touching send-reset unless a fresh boot is planned.

### The phantom-head session: the registration audit and the ati2dvag theory (2026-10-13, night)

Offline hive forensics (hivex) settled the display-registration question:

- The Enum binding is CORRECT: PCI\\VEN_1002&DEV_67B0 Driver =
  {4D36E968...}\\0001, the ATI driver key, which carries
  InstalledDisplayDrivers=ati2dvag (MULTI_SZ), VgaCompatible=0,
  MultiFunctionSupported=1 and the settings.reg DAL tuning. The earlier
  "the ATI adapter key is missing" reading was about the LEGACY
  Control\\Video\\{guid} keys (only vga/VgaSave/mnmd/rdpdd exist there) -
  those are NT4-compat keys, not required by XP's win32k, which reads the
  DRIVER key (Class\\{4D36E968}\\000N). The install is registered correctly.
- Revived belief, now measured three times: input (sendkey through the
  emulated usb-kbd) works on vgasave-era boots and executes NOTHING on
  driver-era boots (the Run-dialog dance leaves no file and no prefetch).
  The GLKVM usbfs fix is orthogonal and confirmed working (usbccgp).
- THE PRIME THEORY: the session graphics init hangs in ati2dvag.dll at the
  desktop switch, BEFORE userinit launches. This one hang explains: the black
  screen (the session desktop never renders; the 1280x1024 the feed locks is
  the mode the stack set before/while hanging), the dead input (queued to a
  desktop with no shell), the absent userinit/explorer (their prefetch went
  stale on 10-11 while the 528 logon events still fire - winlogon completes
  the authentication, then waits on the display handoff), and the idle CPU
  (everyone waits). Prediction: the thread walk at a driver-era stall finds
  the winlogon/session thread blocked inside the ati2dvag attach path.

Instrument state banked for the next session:

- The KD daemon is connected to the CURRENT driver-era hung boot (the
  09:36:04 break state change with the CPU0 context is in kdc-live.log; the
  target resumed via the daemon's ACK+Continue2). Later knockings go
  unanswered - the KD goes half-deaf after the first break on this boot; a
  fresh boot restores the full handshake. The thread walk starts here.
- ServicesPipeTimeout=300000 is staged in the SYSTEM hive (the SCM event
  7009s confirm the value reads). The P1 auto-start service is staged with
  ImagePath C:\\probe\\probe.exe (probe.exe = the GDI screen capture +
  registry dump, service-safe WinSta switch, StartServiceCtrlDispatcher
  skeleton) - but the service NEVER executes its code (7009 every boot, no
  prefetch entry, no log on d: or c:): the SCM creates the process and it
  never connects nor writes. Child processes of the boot-time service cmd
  (tasklist, driverquery) also hang; reg query runs but writes empty files.
  Do NOT iterate further on this path - it consumed four boot cycles. The
  same cap.exe run through the RUN DIALOG on a vgasave-era (working input)
  boot is the cheap way to the screen capture.
- mingw-w64-gcc is installed on the host (cap.exe and probe.exe build with
  x86_64-w64-mingw32-gcc, PE32+ for 5.02, both proven to load: cap.exe ran
  on the guest per the prefetch).
- The D: channel files only reach the rig reliably when the writing process
  closes them; a boot-time service's single-unclosed file reads stale.
