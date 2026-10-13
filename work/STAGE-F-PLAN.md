# Stage-F: the PowerPlay family-check patch (planned, not yet built)

## The conviction (REA, 2026-10-14)

The icafe PowerPlay module does not know Hawaii. Measured in the shipped
binary (work/stageE/ati2mtag-s5_accessor_blob.sys, sha256 8a77818c...):

- `TF_PhwCIslands_UploadMCFirmware` (FUN_0010f8b4, cislands_hwmgr.c:6179
  = 0x1823 - the exact line the runtime prints) asserts
  "Invalid ASIC family!" when `hwmgr->chip_family (+8) not in [0x14, 0x28)`
  and RETURNS 2 (failure). This is the fatal site.
- `PhwCIslands_InitializePowerTuneD..` (FUN_0012a73c,
  cislands_powertune.c:0xd1) switches on the device ID and knows ONLY:
  0x6640, 0x6641, 0x6650, 0x6651, 0x6658, 0x665c, 0x665d - all Sea
  Islands. Hawaii's 0x67B0 is absent; it falls back to the default table
  and continues (noisy, non-fatal).
- The cascade the boot logs (powertune 209, voltage 6696, thermal 433,
  powerplay.c:191 "uninitialization failed") all follow from the family/
  device-ID mismatch: the icafe PP build predates or omits Hawaii support.

## The patch (4 bytes, on top of the stageE output)

Virtual (ImageBase 0x10000) -> file (.text ro 0x600, va 0x1000):

  0x10f8c1: 72 64   JC  0x10f929   -> file 0xFEEC1  (NOP NOP)
  0x10f8c7: 73 5e   JNC 0x10f929   -> file 0xFEEC7  (NOP NOP)

Effect: UploadMCFirmware always takes the valid-family path
(FUN_000e8358(param_1, 0xa32) then the MC upload FUN_0010f63c with the
CI table). Risk: CI MC-firmware tables on Hawaii SMC; mitigations: the
0xa32 pre-check gates the upload, and with the KD attached every assert
INT3s into the debugger (DAT_005df230 checked before INT3) - the asserts
become live instrumentation on the next KD-connected boot.

## The experiment ladder (one variable per boot)

1. KD-connected boot, UNPATCHED: the asserts should INT3 into our
   debugger - confirms the flag and gives live stacks at each failing
   init. No patch needed for this boot.
2. Stage-F v1 (the 4-byte patch): does PowerPlay complete? Does the DAL
   then load ati2dvag? Does the desktop render?
3. If PP still fails elsewhere: the assert-break stacks name the next
   site; patch and repeat.

## The operator's correction (2026-10-14): firmware, not guards

Agreed and measured: a widened guard is not acceptable. The [0x14,0x28)
bucket serves ONE blob table (va 0x353bb0: descriptor 0x34bf50/0x24,
payload 0x34c070/0x1ecf = 7887 bytes) - CI-format MC ucode. Forcing
Hawaii through it uploads Sea Islands microcode to a Hawaii SMC.

New facts measured since:

- The adapter-level device table KNOWS Hawaii: entries {ptr1, 0, ptr2,
  0, family=0x78, did=0x67b0/0x67b1, flags, val} at file 0x1f13c0/
  0x1f13e0 with per-device config pointers. Family 0x78 has NO hwmgr
  route (BTC: pre-SI; SIslands: SI ranges; CIslands: [0x14,0x28)) - the
  driver lacks a Hawaii PP path, not just a narrow guard.
- Only three UploadMCFirmware task functions exist in the binary.
- The host's /lib/firmware/radeon has the Hawaii reference set
  (HAWAII_mc.bin 31732B, HAWAII_mc2.bin, HAWAII_smc.bin 130540B,
  decompressed in /tmp/opencode). The driver's CI blob shares only a
  generic register-address prefix with them; the payloads differ and
  the radeon payloads do not appear in the driver. The splice therefore
  needs the uploader's format decoded first: FUN_0010f63c defines what
  the 7887-byte blob must look like.

## The decision framework

1. Falsification first (cheap): the KD-connected boot makes every PP
   assert INT3 with a live stack; the thread walk shows what waits. If
   the display block is NOT on the PP path, no firmware surgery at all.
2. If PP is convicted: decode FUN_0010f63c's blob format via REA and
   splice HAWAII_mc(+smc) from the local linux-firmware set into a
   Stage-F build - valid Hawaii microcode in the driver's own format.
3. The pivot that dominates if available: a mainstream Catalyst XP x64
   package with native Hawaii support retires the whole patch lineage
   (the Stage-A..E bugchecks were this build's Hawaii mismatch). An
   operator decision - it changes the driver package.

## The peer notes + the bracket drivers (2026-10-14, late)

The peer notes (host plan/0004) answer "how did they solve it on 32-bit":
Catalyst 14.4 XP x86 + the full patch chain - device records, tile tables
(P4->P16), the ASIC capability struct (Bonaire->Hawaii values from
amdgpu's live gfx.config), PA_SC_RASTER_CONFIG (the SE half only), and
"Test H": clocks/power solved early via a rewritten EVV parser PLUS the
SMC firmware graft - 60,574 bytes copied from Windows 7's atikmdag.sys
(14.100.0.0000) at file 0xBFFA13, with a descriptor-read redirect at
x86 0xB1AC8. The x64 slot is byte-identical in length (0x2E28AB-
0x2F1549) - the graft is a straight copy; the redirect was the blocker,
and our live-KD crash analysis (the +0x8F805 table walk) is in exactly
that code family.

OUR deployed x64 binary already carries the peer's Part-2 data fixes
(measured): every tile-table slot has PIPE_CONFIG=17 (P16),
PA_SC_RASTER_CONFIG=0x3a000012 (the Test-AA SE-half value), and
GB_ADDR_CONFIG=0x12011003 (the amdgpu-verified value). What x64 still
lacks is the Test-H equivalent: the SMC graft + the descriptor redirect
- i.e., precisely the PowerPlay path convicted above.

The bracket drivers (archive.org, in /tmp/opencode/donors):
- 14-4-xp32-64-dd-ccc-pack1.exe (194MB; x64 driver B166777 - our base
  family, one revision later than the peer's pack3 B166713)
- 14-4-win7-win8-win8.1-64-dd-ccc-whql.exe (295MB; WB6A_INF/B171094
  atikmdag - the x64 Win7 SMC donor)
- AMD Catalyst 15.6 Beta for Windows Vista (260MB; W76A_INF/B185615
  atikmdag, 19.3MB expanded)
- adrenalin 22.6.1 legacy (486MB; WT6A_INF/B381983/amdkmdag.sys, 83MB)

Format conservation (the operator's question): CONFIRMED across the
whole bracket. The same TF_ task architecture (UploadMCFirmware x3/x5/x4),
the same assert strings (Invalid ASIC family / Unknown C.I. Device ID in
all three; PowerPlay uninitialization failed in both 2014 builds), the
same {ptr,0,ptr,0,family,did,flags,val} device-table record - the Vista
15.6 Hawaii record is IDENTICAL to ours (family 0x78, val 0x28, only the
config pointers differ).

The decisive new finding: NO driver generation accepts family 0x78 in
any UploadMCFirmware (ours [0x14,0x28); Vista 15.6 CIslands
[0x14,0x3C), SIslands [0x14,0x46), Iceland <0x14, Tonga [0x14,0x28)).
On WORKING Vista/Win7 Hawaii systems these tasks are simply never
scheduled for family 0x78 - the memory-controller setup happens under
the SMC-firmware path instead. The fix is therefore in the TASK
DISPATCH (the registration/mask layer), not in any family check. The
name strings are registration call sites (lea rcx, [name]); the next
step is decoding the task-registration/dispatch conditions in our
binary via REA xrefs on the TF_ name strings, and comparing against the
Vista driver's conditions for family 0x78.
