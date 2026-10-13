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
