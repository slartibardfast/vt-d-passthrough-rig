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
