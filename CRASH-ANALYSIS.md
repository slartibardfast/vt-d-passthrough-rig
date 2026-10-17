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

### The KD protocol decoded (2026-10-13, night): the storm, the resets, the parity

The examination's instrument work cracked the KD behavior laws:

- **The first-load storm is kdcom's report-retry.** The loader announces
  ntoskrnl and waits for the debugger's response; kdcom re-breaks at ~1/s
  while a reply is pending. On the healthy boots the storm lasted ~14
  retries (sub-second, one second wall) and the boot proceeded; replying
  GetVersion instead of Continue2 makes the storm PERMANENT (kdcom waits
  forever for a proper continue - measured across four boots today).
  The proven reply per load: Continue2 + DBG_CONTINUE (300+ modules
  loaded clean with it on the 02:08 boot).
- **kdcom ignores breakins while in the report loop.** The escape is a
  RESET control packet: kdcom restarts its state machine and the boot
  flows (new `reset` cmd in the daemon; one reset unwedged a live storm).
- **The one non-load state change is 0x3030 = EXCEPTION** (0x3031 =
  load symbols); the breakin exception carries 0x80000003 and the full
  CONTEXT in the packet (the 09:36/11:55 dumps).
- **hold-all forces the hold branch** on any break (otherwise a break
  landing inside nt is auto-stepped and the machine resumes).
- **The parity/alternation law (partially mapped):** every debugger
  packet's id alternates 0x80800000/0x80800001; a repeated id is silently
  dropped (ack-less). The daemon's flip drifts out of phase after failed
  exchanges; one read DID land with the correct phase
  (PsInitialSystemProcess -> the System EPROCESS at 0xfffffab58b5040,
  16 bytes read clean). walk.py has the parity-retry logic; the phase
  tracking needs one careful session.
- **The read size cap:** a 672-byte read returns STATUS_UNSUCCESSFUL;
  16 bytes works (the cap sits between - bisect when needed).

Display facts banked this session:
- The registration is COMPLETE (Enum -> Class\{4D36E968}\0001 with
  InstalledDisplayDrivers=ati2dvag); the legacy Video\{guid} absence is
  not a defect.
- ati2dvag.dll has NEVER loaded (zero module records across all boots);
  the driver-enabled boots load vga.dll + framebuf.dll (VgaSave) - the
  session always runs on VGA Save.
- The PowerPlay ASIC-family failure (cislands_hwmgr.c[6179] Invalid ASIC
  family, the powertune and thermal cascade) reproduces every driver boot
  at miniport-init time; it is the miniport's init failing, before the
  display DLL would ever load.
- This boot (driver enabled, the KD flowing): the boot COMPLETED, the
  session ran VGA Save, and the feed STILL showed black at 1280x1024.
  The mode/content split exists even on the VGA session.
- The rig state left: the guest FROZEN at the 11:55 break (hold-all set,
  hold-release absent - the state is preserved), the daemon polling, the
  registers parked at nt+0xcd053. Do not reboot casually: the frozen
  session is the walk's starting point.

### The KD engine decoded from the target's own code (2026-10-14, night 2)

REA (Ghidra) on the LOCAL ntoskrnl.exe + kdcom.dll - the wire laws are no
longer inferred, they are read:

- **The manipulate dispatcher**: nt PAGEKD FUN_007ab520 (va 0x7ab520) -
  the complete api switch (0x3130..0x315c). The read case (0x3130):
  the cap is 0xf68 bytes/request (not 16 - the 16-byte success was a
  different failure); the copy is FUN_00402ed0 (MmDbgCopyMemory-class,
  flags 4); any copy failure => status 0xc0000001 with the partial
  count. The wait loop re-sends the state change whenever the received
  packet type != 2 (the storm mechanics, in the engine itself).
- **kdcom KdReceivePacket - THE ID LAW**: kdcom holds expected_id
  (DAT, init 0x80800000, restored to it by any RESET control). A data
  packet is accepted ONLY if its id EQUALS expected_id; acceptance
  flips it (^1) and ACKs with the accepted id. A wrong id gets a
  RESEND control (type 5). Checksum = header+data sums vs the packet
  field; a checksum failure returns SILENTLY (no ack, no nak) - the
  exact silent-drop we chased for a day.
- **The daemon now implements the law** (kd_expected): data packets
  always carry kd_expected; the flip happens ONLY on an ack matching
  the sent id; RESEND retransmits the same packet; RESET restores the
  base. The old flip-on-send diverged from the engine after every
  dropped packet - the root of all parity flakiness.
- **The remaining blocker**: mid-break, kdcom silently drops even
  well-formed, correctly-id packets - no ack, no nak, no re-break.
  The suspect is kdcom's byte-read function FUN_7ff6a181cd0 (its
  timeout/polling state after a break). The one session where reads
  worked (12:04/22:51) had hours-old engine state; every fresh break
  since refuses. Decompile FUN_7ff6a181cd0/FUN_7ff6a181d00 next
  session - it is the last box between us and the thread walk.
- The frozen boot was sacrificed for the clean-boot experiment; the
  current boot: Windows up (168 modules flowed clean after the
  14-retry storm self-healed - the pure Continue2 flow CONFIRMED),
  the black 1280x1024 reproduced, broken and held, reads pending the
  kdcom byte-read decode.

### THE WALK WORKS - the session-verdict rewrite (2026-10-14, night 3)

The end-to-end tooling is DONE and proven on real hardware:

- The root cause of "reads silently fail" was NEVER kdcom refusing: the
  daemon's hold loop never READ THE SOCKET. Replies queued in the socket
  backlog for hours (a raw byte-tap between the daemon and QEMU proved
  it - the read replies sat there, complete and correct). The hold loop
  now drains and dispatches them; the "1B escapes" in the first tap
  analysis were the tap's own chunk-length field misread as bytes - the
  wire is clean, unescaped frames.
- The alternation law + the acceptance-driven kd_expected + the 16-byte
  read cap (MmDbgCopyMemory refuses more) + the hold-loop drain = the
  working read protocol. PsInitialSystemProcess reads reliably; the
  EPROCESS walk runs at ~1.8s/chunk.
- THE WALK RESULTS (the 23:48 clean boot, broken and held):
  ActiveProcessLinks at EPROCESS+0xa8, ImageFileName at +0x268. The
  full process list: System, smss, csrss, WINLOGON, services, lsass,
  the svchost fleet, spoolsv - THE SESSION IS FULLY ALIVE on driver
  boots. The earlier "session never starts" theory is DEAD: winlogon
  runs, services run, everything runs.
- So the black screen is purely the DISPLAY PATH: ati2dvag.dll never
  loads (zero module records; vga.dll+framebuf+TSDDD instead = VGA Save
  is the active display), while the files exist in System32 (ati2dvag
  342016 bytes, the whole ATI stack present) and the registration is
  correct. The 1280x1024 mode on the wire proves the miniport RAN and
  set a mode; the display-DLL attach did not happen; VGA Save renders
  into a framebuffer the card's scanout does not show.
- The next diagnostic (this state is preserved: the machine is HELD,
  hold-release absent): the System eventlog for this boot (readable
  next boot offline - the display-service errors), the ATI device's
  ConfigFlags (FAILEDINSTALL?), and a blind input probe: the session
  runs, so the GLINET keyboard should work - a D: sentinel via the Run
  dialog would prove the session renders-to-memory correctly.
- The rig state: frozen at the break (the daemon polling, hold-all set),
  the tap corpus in /tmp/opencode/tap.log, the donor packages in
  /tmp/opencode/donors. The read driver: walk.py (chunked, 16B law).

### The degradation discovery - the walk differential (2026-10-14, night 4)

The process-walk, repeated across hold/probe cycles, is a degradation
detector - and it caught the build dying by inches:

- 23:53 walk: 17 processes (the full session: winlogon, services,
  lsass, the svchost fleet, spoolsv, wmiprvse, alg, explorer, wscntfy).
- 00:4x walk: 9 processes (alg, explorer, wscntfy GONE).
- 00:5x walk: 6 processes (the svchost fleet collapsed to one, spoolsv
  and wmiprvse gone).

The black-screen build is not a stable system with a display problem:
its failure CASCADES - services die progressively while the machine
runs. explorer.exe itself died during the probe window. The input
probe (ctrl-shift-esc -> taskmgr) produced nothing: either the input
does not reach winlogon or winlogon is too degraded to answer.

Also measured: the read cap refined - the engine CLAMPS every read to
16 bytes (no error, just min(requested,16)); the old c0000001 errors
were the poisoned session's state, not a size refusal. The hold loop's
drain works under load (the reads flowed straight through the
state-change re-send spam).

Tooling debts queued: a readbatch command (N reads per cmd-file round
trip), the walk-diff as a first-class command, the boot orchestrator.

The rig state: held at the 00:49 break, the system degraded to 6
processes. The System eventlog on the next boot is now the primary
evidence: the service-failure records will name what killed the fleet.

### The protocol laws consolidated + the SetContext mystery defined (2026-10-14, night 5)

The end-to-end tooling session hammered the last protocol walls:

- **The checksum law, CORRECTED**: kdcom compares the FULL 32-bit sum.
  Our 16-bit truncation broke every packet whose sum exceeded 0xffff -
  the SetContext (1280-byte body) NAK-looped forever while small
  packets matched by luck. Fixed in the daemon (sum & 0xFFFFFFFF).
- **The read cap refined again**: on a healthy session the engine
  CLAMPS reads to 16 bytes (min(requested,16), no error). The old
  STATUS_UNSUCCESSFUL responses were the poisoned session, not a law.
- **The hold-drain ack law**: an unacked state change is re-sent by
  kdcom forever; the hold loop's drain MUST ack (fixed - the drain
  now acks and logs).
- **The boot-breakpoint law**: nt fires a chain of int3s during init
  (measured at nt+0x4b3a5, nt+0x81416c, nt+0x2e974). Resuming an int3
  without RIP+1 re-executes it forever - the boot-era holds must STEP
  (GetContext, RIP+1, SetContext, continue). The daemon has a `step`
  cmd; boot.py steps boot-era holds automatically. ALSO: holding the
  boot bp for 20 minutes POISONS the storage init (the crcdisk hang,
  measured) - boot-era holds must be released within seconds.
- **The orchestration laws**: the VFIO group teardown lags the QEMU
  exit (wait for fuser to clear or the relaunch fails "resource
  busy"); the clean boot (daemon from t=0, hands off, no hold-all
  during the boot) flows 117 modules to the session era, every time.
- **The degradation cascade measured**: the black-screen build loses
  processes progressively (17 -> 9 -> 6 across probe cycles; explorer
  itself exited). The System eventlog (next clean stop) names the
  killer.

**The one remaining protocol mystery**: kdcom silently rejects the
SetContext manipulate (1296 bytes) while accepting the GetContext (64
bytes) and the reads - same id, same framing, the 32-bit checksum
correct. The candidate: kdcom's large-manipulate framing split (the
block reader FUN_7ff6a185060's exact header/data lengths for
bytecounts > 0x40). One focused decode of that path finishes the
tooling; then the walk, the verdict, and the repair ladder run.

### The cascade RETRACTED - the eventlog vindicates the system (2026-10-14, night 6)

The degraded boot's System eventlog (read offline, the frozen boot's
flushed records): BOTH boots show identical, HEALTHY service starts -
NLA, ALG, Terminal Services running, no failures. The "17 -> 9 -> 6
process death spiral" was an ARTIFACT: my process walks spanned the
daemon's broken eras (the hold-loop read failures), and the shrinking
lists were READ FAILURES, not process exits. Retracted.

The corrected picture, now consistent across every instrument:
- Every boot: the full session starts cleanly (the eventlog, the
  17-process walk on the fresh boot, explorer included).
- The black screen: the ONLY defect. The display output path.
- The input probe (ctrl-shift-esc -> taskmgr): taskmgr never appeared -
  but the probe was sent across mixed frozen/running eras; it needs a
  redo on a clean held state before "the input is dead" is claimed.

**The next instrument, already built**: cap.exe (the GDI screen
capture, compiled this campaign) staged through the proven D: channel
with a boot trigger - the framebuffer content readback. If the capture
shows a rendered desktop while the feed shows black: the render/scanout
split is convicted at the software level, and the repair is the
display-path bring-up (the vga-save-to-ATI handoff), not PP, not the
session, not the firmware.

### The capture blocked in the driver path - the render probe's verdict (2026-10-14, night 7)

The staged capture fired on the healthy boot: the Run key ran
install.bat, cmd created cap-result.txt, and cap.exe launched. Four
minutes later the BMP still did not exist, cap-result.txt was still
zero bytes, and the vcpu was alive and executing the whole time. The
GDI capture blocks inside the display driver path - framebuf/vga down
to the miniport IOCTL - and the IOCTL never returns. The machine
survives; the capture thread does not.

Combined with the feed reading signal-black (a live 1280x1024 timing,
black pixels) and the NX fault loop at ati2mtag .data+0x1CC8D4 (the
corrupted runtime-computed pointer, the faulting instruction the resume
point), the display picture is: the fallback stack renders into a
surface the scanner does not read, and any path that drives the
miniport's GPU interaction - the capture's IOCTLs, the PP task dispatch
- either stalls forever or jumps through the corrupted pointer.

The KTHREAD stack walk (queued tooling) names cap.exe's exact blocking
frame next session. The graft thread (the builder, the SMC slot, the
dispatch fix, now also the corrupted-pointer repair) is the unblock
for everything display-side.

### The live-interrupt chain works: the int3 hold, the flag write, the assert (2026-10-14, night 8)

The full instrument chain is measured working end to end:

1. The deployed driver loads from D:\drv\ati2mtag.sys on the FAT channel -
   the graft deployment is a FAT file copy, no system-disk surgery.
2. The kernel ENFORCES the PE checksum for this driver: an int3 edit
   without the amd_checksum recompute is refused silently (no PP prints,
   no minport cascade). The builder's pe-checksum op is mandatory on
   every image change.
3. A planted int3 at the powertune init entry (file 0x119d3c, va
   +0x11a73c) breaks the boot and the captor names it - the hold, the
   flag write (api 0x3131), and the continue all work.
4. The upload-path conviction is live-confirmed: an int3 planted at
   TF_PhwCIslands_UploadMCFirmware's entry sat through a whole boot
   untouched - the task dispatch never routes family 0x78 there.
5. The break flag DAT_005df230 (rva 0x5cf230) gates the PP asserts'
   INT3s. With it live, the asserts break; without claiming the handler,
   an unhandled kernel int3 bugchecks 0x7e immediately.
6. THE KEY DISCOVERY: the drain (the hold loop's state-change handler)
   was acking-and-silencing assert-breaks mid-hold. Fixed: the drain now
   acks AND captures. The powertune assert is caught by name on the
   fixed build.
7. The write manipulate claims success but does not land while the
   engine sits in the state-change wait (the read refusal has the same
   shape). MOOT: the break flag is a static .data byte - bake it to 1 in
   the image (file offset 0x5cd030 per the rva math) and every boot is
   born with the asserts live. No writes needed.
8. The orchestrator's storm-reset escape was killing intentional holds;
   removed. The harness kills process trees of timed-out commands -
   launch boot chains with setsid.
9. The SMC firmware structure: five per-ASIC copies sharing a header at
   0x2a8fbb..0x2e28ab; Hawaii is the fifth, exactly where the peer
   grafted. The peer's offset table is valid for the icafe build.

### THE DESKTOP IS VISIBLE - the black-screen mechanism verified live (2026-10-14, night 9)

The flag-baked boot abandoned the debugger at the storm (kdcom flipped
to its 0x80800800 ids - the retry-exhaustion abandon) and continued
without the debugger. On that boot:

- The PP asserts could not INT3 (the kernel debugger is gone), so the
  powertune/hwmgr asserts returned failure and the PP init stopped
  BEFORE the miniport's display mode set.
- The feed shows a LIVE DESKTOP: Luna wallpaper, Recycle Bin, taskbar
  with clock, a mouse cursor, an open cmd window - at 640x480, the
  VGA Save default, confirmed by the dongle's own status readout.
- The visible error dialog names cap.exe's real failure at last:
  api-ms-win-crt-environment-l1-1-0.dll missing - cap.exe was built
  against the UCRT and never ran at all on XP. The "blocked capture"
  verdict of the previous session is retracted; the tool was refused
  by the loader before main.

The mechanism, now verified live: VGA Save renders the desktop into
its framebuffer and the scanout shows it at 640x480. The miniport's
mode set (1280x1024, measured on every prior boot) switches the
display engine to the ATI CRTC, and the session's surface is orphaned
- the scanner reads a surface nothing draws into. That is the whole
black screen. Every boot with a working miniport mode set showed black;
this boot, where the mode set never happened, shows the desktop.

Consequences:
- The display verdict is rendered-but-unshown, with the orphaning at
  the mode-set handoff.
- The repair is the XDDM stack binding (ati2dvag loading, which owns
  render and scanout coherently) or a surface/CRTC re-anchor.
- cap.exe must be rebuilt XP-clean (no UCRT imports) to be a capture
  instrument again.
- The debugger-attached boots cannot show the desktop: the KD dies at
  the storm abandon on most boots, and on boots where it lives, the
  flag-baked asserts break the PP init before the mode set. Instrument
  boots and desktop boots are now provably different worlds.

### The capture instrument works and the verdict is triple-sourced (2026-10-14, night 10)

cap2.exe (the CRT-free rewrite: Win32-only imports, a raw entry point,
a local memset) ran on XP x64 with errorlevel 0 and wrote a valid
640x480x32 BMP through the Run-key channel. The capture, the feed, and
the live registry now agree:

- The GDI capture shows the RENDERED desktop: classic-blue title bars,
  teal, the XP cream dialog face, red - real content, non-uniform.
- The feed shows the SAME desktop scanned out at 640x480.
- The live Video registry shows the fallback's rewrite again
  (VgaCompatible=1, Standard VGA descriptions over the ATI key) - the
  XDDM refusal is the boot-time constant it has always been.

So the session renders, the scanout works, and the desktop is visible
at the VGA Save default. The remaining gap to the campaign goal is
narrow and precise: the XDDM stack (ati2dvag) does not bind, so the
miniport's 1280x1024 mode set orphans the scanned surface whenever it
runs. The next instrument is an int3 at the miniport's IOCTL dispatch
on a debugger-alive boot (the retry loop makes those reachable): each
VIDEO_ request in the enable chain gets caught and named, and the
failing one identifies the bind blocker for the graft manifests.

### The read-refusal law and the packed binary (2026-10-14, night 11)

Two laws close the static route and define the dynamic one:

- The read manipulate only serves NON-PAGED kernel memory. Pool
  structures (EPROCESS, ETHREAD) read fine; the driver's paged image
  (.text/.data) reads as STATUS_UNSUCCESSFUL at every address tried,
  held or running. The loaded image is unreachable through the debugger.
- ati2mtag.sys is PACKED. The candidate HW_INITIALIZATION_DATA pointers
  in .data point at .text whose bytes are ciphertext (read_bytes shows
  no code), and the section names are non-standard. Static analysis of
  the packed regions is dead; the decrypted image exists only in
  runtime memory - which the debugger cannot read - and in crash dumps,
  which this guest is not writing even when the crash happens with the
  debugger detached (the pagefile stays zeroed after an 0x7e with
  CrashDumpEnabled=2 and a 2GB pagefile).

The crash-dump stack itself is the next suspect: crashdmp.sys,
dump_diskdump.sys and friends may be stripped from the icafe image.
If the dumps are restored (the files staged from a clean XP x64
source and the CrashControl registry verified), the bugcheck route
becomes the master instrument: a full memory snapshot - the decrypted
driver image included - read offline, no read-manipulate limits.

### The abandon wall: the flag works, the boots quit the debugger (2026-10-14, night 12)

The flag bake works as designed - on a debugger-alive boot, the PP
asserts break into the debugger natively. The wall: since ~06:12 every
boot abandons the debugger at the first-announce storm (4+4+
consecutive, two daemon generations, the revert test included). On an
abandoned boot KdDebuggerNotPresent=true makes every INT3 a no-op, so
the asserts neutralize themselves and the boot completes undegugged -
which is why the desktop was visible.

The abandon's shape: kdcom's send-retry window on the first announce
expires before our 64-byte Continue2 reply drains through the guest's
1-byte-per-tick UART poll. It is a bandwidth/timing borderline, not a
protocol error - which is why it tracks host load. The flipped boots
were the ones where the reply won the race.

Laws added:
- The write manipulate claims success (ret=0) but does not land while
  the engine sits in the state-change wait. Writes only work in the
  exception wait.
- A stale probe packet is silently lost to UART overrun on a running
  machine; the daemon's pending state then misparses the next packet.
  Restart the daemon to clear pending after any unanswered command.
- boot.py's storm-reset escape was removed earlier and must stay
  removed: the reset killed intentional holds.

The path forward is one of: (a) shrink or accelerate the reply path
(QEMU serial tuning, or accept the coin flip and retry more), (b) patch
kdcom's retry counter in the image (a graft - the counter is known from
the decompile), or (c) run the assert investigation on a debugger-alive
retry when the coin lands. The dump stack (dump_atapi.sys) is now
fixed and untested - any future bugcheck should write MEMORY.DMP.

## Night 2026-10-14 (late): the wall was three defects, not kdcom

Measured this session, each one the hard way:

1. **Deploy law (violated twice): kdcom deploys go to `xp64-gpu.raw`** —
   the disk `run-gpu-vm.sh` actually boots. `install-blank.raw` is a
   different file (distinct inode). Both earlier kdcom patches (the
   retry-count and the first gate build) were deployed to the wrong
   disk and never ran in the guest. The driver channel (`gpudrv.img`,
   the FAT image) was always correct; only system32 surgery was wrong.
2. **0x80800800 is kdcom's STATIC not-present id, not an abandon
   marker.** Every boot announces ntoskrnl with it first; the daemon's
   passive acks flip the pair to 0x80800000/0x80800001 about 10s in.
   boot.py's detector grepped for "ever seen" and executed healthy
   boots at 50s. Fixed: alive = "RX ack pid=0x8080000" in the log.
3. **The knocker was never the poison.** Knocker-off boots flip and go
   alive without any reset. The "reset spam poisons" law describes a
   different failure (mid-hold spam), not the boot-start flip.
4. **The kdcom send-loop abandon gate, instruction-exact** (Ghidra base
   0x7ff6a1800000, PAGEKD raw 0x1a00): `85 c0 75 6c` at file 0x21d0 =
   TEST EAX,EAX; JNZ short +0x6c to the send path at va ...85840; the
   fall-through is the state-checked abandon (state 7 subcode 0x3031 →
   KdDebuggerNotPresent=1, ids to 0x80800800/0x80800000). The count is
   re-armed from nt's *param_4 every send, so .data patches are dead.
   Graft: `eb 6c 90 90` (unconditional jump over the gate) + PE
   checksum, sha256 acb2c38a3bd5dc63..., deployed to the boot disk.
5. **The knock flips and the captor holds.** Fresh daemon + one
   `knock` cmd → breakin lands (exc=0x80000003 at 0xfffff8000104b350),
   state change 0x3030, HOLDING, kernel reads flowing (paged-pool VAs
   in the hexdump). The wall is broken end to end.
6. **pmemsave quirks**: HMP size arg is 32-bit (4GB exact overflows —
   dump two 2GB halves); relative filenames only (the expression
   parser eats a leading `/` as division); files land in QEMU's cwd
   (the rig dir). **Always sync + size-verify after a dump** — a host
   reboot mid-dump silently shrinks both halves (page-cache tails
   lost): measured h1 2GB→1.27GB, h2 2.1GB→0.69GB.
7. **ctx result packet never follows the ack** (daemon issue,
   uninvestigated) — the QEMU dump route makes it non-blocking.

## Night 2026-10-15: the decrypted image is carved

The master instrument exists: `work/dumps/ati2mtag-live.sys` (8,896,512
bytes, sha256 eabadb3a61355125..., 18 real ATI sections: .text,
.rdata, .data, PAGE_DAL/CPC/COM/INI/DDC/RO/RW). "ati2mtag" at image
offset 0x180bc5, "Radeon" at 0x17d328. Carved from
`dump-hold1.bin` (3GB pmemsave taken at a QEMU stop, PA == file
offset) through the live page tables.

Laws of the carve (each bought with an hour):

1. **The live base comes from the announce log.** The MODULE LOAD line
   `ati2mtag.sys base=0xfffffadfaaf89000` was in the log all along;
   a `head -2` on the grep hid it behind the FILE_IO lines. Read the
   whole grep before theorizing.
2. **x64 PTE masks**: frame = bits 12-51, mask 0x000FFFFFFFFFF000. The
   NX bit (63) left in the mask corrupts every pa from a non-executable
   page (measured: base PTE = 0x80000000ba104000).
3. **Page tables have 512 entries.** A range(0x100) walk sees only the
   user half of every table - the kernel half silently vanishes.
4. **Big pool allocations ride 2MB PDEs.** Handle the PS bit at PDE
   level or 8MB images evaporate.
5. **Packed-file offsets do NOT carry into the live image**: the
   packer rewrites the section table; 0x2E28AB (SMC) and 0x5cd030
   (flag) are file-layout offsets for graft.py on the packed file only.
   The live SMC slot is runtime-modified anyway.
6. **Ghost copies exist**: freed image copies stay in RAM as unmapped
   pages and pass the physical PE scan. Validate a candidate by
   translating its VA, not by its bytes.
7. **The x-vga wall was a module parameter, not firmware.** vfio-pci
   loaded with disable_vga=Y (measured in
   /sys/module/vfio_pci/parameters/disable_vga) despite modprobe.d
   saying =0; has_vga never gets set and QEMU rejects x-vga. Fix:
   unbind both 290x functions, `modprobe -r vfio_pci`,
   `modprobe vfio_pci disable_vga=0 ids=1002:67b0,1002:aac8`, rebind -
   no reboot. The earlier NVRAM/primary-display theory is RETIRED.
   Provenance of the Y: still open - inspect the initramfs copy of the
   conf and the zen kernel's parameter default.
8. **Open issue**: after the boot completed (102 modules, ati2mtag +
   win32k + dxg loaded), the KD channel went deaf - knock breakins and
   a pread got zero response, while the guest stayed alive at an idle
   pool RIP (QEMU monitor). Unexplained; investigate before the next
   assert session.

## Durable fix (2026-10-15): vfio_pci.disable_vga at the kernel cmdline

The boot path is a UKI: systemd-boot boots
EFI/Linux/<machine-id>-<ver>.efi, whose cmdline is baked from
/etc/kernel/cmdline at UKI build time, plus the sidecar
EFI/Linux/arch-linux.cmdline that systemd-stub appends. Both now carry
`vfio_pci.disable_vga=0`:

- /etc/kernel/cmdline - the source every kernel upgrade re-bakes.
- /boot/EFI/Linux/arch-linux.cmdline - covers the current UKI without a
  rebuild. Backup: arch-linux.cmdline.bak-20261015.

The kernel applies module parameters from the cmdline itself at load,
bypassing every userspace modprobe path (busybox early userspace,
initramfs conf copies, udev timing) - which is where the =0 conf was
being lost. The mystery of HOW it was lost stays open (the baked
initramfs conf reads =0 and correct); the cmdline closes the hole
regardless of the answer. Boot-side, boot.py now self-heals: the
preflight reads the parameter, and on Y reloads vfio_pci with
disable_vga=0 (unbind both 290x functions, modprobe -r, modprobe with
ids=1002:67b0,1002:aac8) and re-verifies before QEMU launches.

## Correction (2026-10-16): the sidecar was never read; the UKI is re-baked

The reboot test failed with disable_vga=Y and /proc/cmdline missing the
param: L had appended to EFI/Linux/arch-linux.cmdline, but the boot
entry is the machine-id-named UKI
a9d7f029...-7.2.8-zen1-2-zen.efi - systemd-stub only reads addons named
after the UKI itself, and systemd 260 dropped plain-text .cmdline
sidecars anyway. The live cmdline matched /etc/kernel/cmdline exactly,
which identified the baked-cmdline path.

The durable fix, verified in the artifact: `sudo kernel-install add-all`
rebuilds the UKI from /etc/kernel/cmdline (uki.conf carries no CmdLine
override; sbctl signs). The rebuilt UKI's baked cmdline now ends
`... vfio_pci.disable_vga=0 systemd.machine_id=...` - read back from
the .efi with strings. Kernel upgrades re-run kernel-install, so the
param survives upgrades by construction.

## Night 2026-10-16: the reboot test, the preflight's first save

- The reboot exposed L's own sidecar error (wrong filename - the UKI is
  machine-id-named; arch-linux.cmdline is a different image's addon).
  Corrected the DURABLE layer: kernel-install add-all re-baked the UKI
  from /etc/kernel/cmdline, sbctl signed, and the param is verifiable in
  the artifact itself (strings on the .efi shows
  `... vfio_pci.disable_vga=0 systemd.machine_id=...`). That UKI has
  NOT been boot-tested yet - the next host reboot is its test.
- The preflight's first save: this boot started with disable_vga=Y, the
  preflight detected it, reloaded the module, verified N, and the boot
  went green (session_ready=True abandoned=False, 113 modules, ati2mtag
  at 0xfffffadfaaf49000). Self-healing works as designed.
- **The deafness correlates with boot completeness**: yesterday's 102-
  module boot went deaf to breakins; today's 113-module boot (full
  set) caught the knock instantly at +33s post-storm. The other
  suspect from yesterday remains the mid-session daemon kill
  (kdread.sh). Open question, narrower now.
- The hold-dump procedure ran clean end to end: stop -> info registers
  (CR3=0x372000) -> pmemsave 0 0xc0000000 -> sync -> size-verified
  3221225472 -> cont -> go. dump-hold2.bin holds the frozen catch state
  (RIP=0xfffff8000104e7c0, the break handler).
- /tmp is tmpfs: a host reboot wipes /tmp/opencode (daemon logs, the
  QEMU socket dir). boot.py now creates it at boot start.

## Upgrade-survival audit (2026-10-16): pacman -Syu cannot take the work

Requirement from the operator: the work must survive pacman -Syu.
Audited piece by piece; findings:

- /etc/kernel/cmdline, /etc/kernel/uki.conf, the vfio modprobe conf,
  and the ESP sidecar are all UNOWNED by pacman - upgrades never touch
  them (checked with pacman -Qo).
- The UKI rebuild path: 60-ukify.install sets uname from the
  kernel-install arguments (line 243) and the cmdline from
  /etc/kernel/cmdline (line 252) - the uki.conf Uname pin is inert for
  kernel-install builds. A kernel upgrade re-runs kernel-install add
  via the alpm hook, re-bakes the cmdline (with vfio_pci.disable_vga=0)
  into the new UKI, and sbctl signs it. Survives by construction.
- MODULES=(vfio_pci vfio_iommu_type1) in mkinitcpio.conf: the early
  vfio load survives kernel upgrades (modconf bakes the modprobe conf
  into every new initramfs).
- sudo is passwordless (sudo -n OK) - boot.py's unattended preflight
  works.
- **Fixed the loader default**: loader.conf said
  `default arch-linux-lts.conf` while the machine runs zen - the zen
  boot depended on a manual menu pick inside 3 seconds (the accidental
  working the operator called out). Now `default @saved`: the last
  manual selection persists across upgrades. Backup:
  loader.conf.bak-20261016.
- Accepted upstream risks (not ours to pin): QEMU updates could change
  vfio/x-vga behavior; systemd updates could change stub addon rules.
  Both are caught by boot.py's preflight + the operator's next boot.
- Still open: the writer of the observed disable_vga=Y. All identified
  mechanisms force N; three independent layers now force N regardless.

## 2026-10-17: the cmdline fix verified natively

Fresh host boot on the re-baked UKI: /proc/cmdline carries
vfio_pci.disable_vga=0, /sys/module/vfio_pci/parameters/disable_vga
reads N with NO preflight run (boot.py never launched) - the kernel
applied the parameter at module load, exactly as the cmdline mechanism
promises. The disable_vga anomaly is now fully contained: kernel
default N, conf N, cmdline N, preflight N. Layer 2 proven by
construction. The rig boot + REA session follows.

## REA session opened (2026-10-17): the packed->live anchor map

The carved image ingested into Ghidra (4,406 functions) via an analysis
copy with the EXCEPTION data-directory entry zeroed - the loader chokes
on the snapshot's .pdata markup ("Function entryPoint may not be created
on defined data"). The evidence copy is untouched.

The packer re-lays sections, so packed-file offsets shift per region in
the live image. Measured anchor map (packed -> live, delta):

- device-ID switch  0x119D4F -> 0x11A74F  (+0xA00)   plaintext
- crash family      0x1A9554 -> 0x1AAD54  (+0x1800)  plaintext
- dispatch blob     0x353BB0 -> 0x355DB0  (+0x2200)  plaintext
- SMC slots         0x2E28AB -> 0x2AB1BB (+3 copies at 0x2B956B,
                    0x2C7A5B, 0x2D618B) - the per-ASIC slot copies;
                    the Hawaii (5th) slot is the one whose content
                    differs (the graft target)
- flag byte         0x5CD030 -> NOT FOUND - encrypted region

work/anchor-map.py maps any packed offset by content search. The graft
offsets stay valid against the PACKED file (graft.py's world); the live
mapping is for reading the real code.
