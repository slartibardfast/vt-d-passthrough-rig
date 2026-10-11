#!/usr/bin/env bash
# Capture the ACPI tables a live guest was actually handed, from outside the guest.
#
# usage: acpidump.sh <run-dir>
#
# The guest must be in long mode and a gdbstub must be listening on :1234. The
# RSDP is found by scanning physical memory for its signature, then its pointers
# are followed to the MADT/FADT/DSDT. This is the comparison that matters for this
# project because CSMWrap rewrites the MADT in place - it publishes a *measured*
# LAPIC timer bus clock into it - and Windows XP x64's single HAL takes that number
# as truth.
#
# Why the guest must be in long mode: CR3-based translation is only identity-mapped
# for the kernel's own low addresses in a booted guest. A guest still in real mode
# has no such map, and a physical-memory read returns whatever is at those
# addresses, which is not what the kernel sees. Check CS before reading anything.
set -uo pipefail

RUN=${1:?usage: acpidump.sh <run-dir>}
PORT=${PORT:-1234}
HERE=$(cd "$(dirname "$0")" && pwd)

python3 - "$RUN" "$PORT" <<'PY'
import os, re, subprocess, sys

run, port = sys.argv[1], sys.argv[2]

def gdb(cmds, timeout=240):
    args = ["gdb", "-batch",
            "-ex", "set pagination off",
            "-ex", f"target remote :{port}",
            "-ex", "set architecture i386:x86-64"]
    for c in cmds:
        args += ["-ex", c]
    args += ["-ex", "detach"]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", "timeout"
    out = r.stdout + r.stderr
    if "could not connect" in out:
        return "", "no stub"
    return out, None

# 1. Is the guest even in a state where a physical read means anything?
regs, err = gdb(["info registers rip cs cr0 cr3"])
if err:
    print(f"  gdb unavailable ({err}); cannot collect ACPI tables")
    sys.exit(1)
def grab(name):
    m = re.search(rf"^\s*{name}\s+(0x[0-9a-f]+)", regs, re.M)
    return int(m.group(1), 16) if m else 0
rip, cs, cr0, cr3 = grab("rip"), grab("cs"), grab("cr0"), grab("cr3")
print(f"  guest: rip=0x{rip:x} cs=0x{cs:x} cr0=0x{cr0:x} cr3=0x{cr3:x}")
paging = bool(cr0 & 0x80000000)
print(f"  paging: {'on' if paging else 'OFF'}")
if not paging:
    print("  -> the guest has not enabled paging. It is in real mode or early boot,")
    print("     so a physical-memory read is NOT what the guest sees. Stopping here")
    print("     rather than reporting a table dump that would be meaningless.")
    sys.exit(2)

# 2. Scan for the RSDP. Low 1 MiB is where SeaBIOS and CSMWrap both place it.
low = os.path.join(run, "acpi-low.bin")
out, err = gdb([f"dump memory {low} 0x0 0x100000"])
if err:
    print(f"  cannot dump low memory ({err})")
    sys.exit(1)
data = open(low, "rb").read()
off = data.find(b"RSD PTR ")
if off < 0:
    print("  no RSDP signature in the first 1 MiB")
    sys.exit(1)
print(f"  RSDP at physical 0x{off:x}")
rev = data[off + 15]
oem = data[off + 10:off + 16].rstrip(b"\0").decode("ascii", "replace")
rsdt = int.from_bytes(data[off + 24:off + 28], "little")
xsdt = int.from_bytes(data[off + 32:off + 40], "little")
print(f"  revision {rev}, OEM {oem!r}")
print(f"  RSDT -> 0x{rsdt:x}   XSDT -> 0x{xsdt:x}")

# 3. Dump the region holding the tables (all below 4 GiB by spec).
tab = os.path.join(run, "acpi-tables.bin")
gdb([f"dump memory {tab} 0x90000 0xb00000"])
tables = open(tab, "rb").read()
base = 0x90000

def locate(sig):
    i = tables.find(sig)
    return (base + i, i) if i >= 0 else (None, None)

results = {}
for sig in (b"APIC", b"FACP", b"DSDT", b"SSDT", b"HPET", b"MCFG"):
    addr, off = locate(sig)
    if addr is None:
        print(f"  {sig.decode():5s} NOT FOUND in 0x90000-0xb00000")
        continue
    length = int.from_bytes(tables[off + 4:off + 8], "little")
    rev8 = tables[off + 8]
    print(f"  {sig.decode():5s} at 0x{addr:x}  length {length}  revision {rev8}")
    results[sig.decode()] = (addr, off, length)

if "APIC" in results:
    addr, off, length = results["APIC"]
    flags = int.from_bytes(tables[off + 44:off + 48], "little")
    # MADT header: sig(4) len(4) rev(1) oem(6) oemid(8) oemtbl(8) oemrev(4) creator(4) creatorrev(4) = 44
    print()
    print("    MADT detail:")
    print(f"      flags          = 0x{flags:x}")
    print(f"      PCAT_COMPAT    = {(flags >> 10) & 1}   (bit 10: dual 8259s present)")
    print(f"      LocalAPICAddr  = {int.from_bytes(tables[off+44-4:off+44],'little')}")
    ovr = int.from_bytes(tables[off + 40:off + 44], "little")
    print(f"      LAPIC timer override = {ovr} (0x{ovr:x})")
    if ovr:
        hz = 10000000 / ovr
        print(f"        -> a bus clock of {hz:.2f} Hz ({hz/1e6:.4f} MHz)")
        print(f"        -> a timer period of {ovr} ticks")
    # walk the entries to count local APICs and count the ISO/IO interrupts
    p = off + 44
    end = off + length
    lapics = 0
    ints = 0
    overrides = 0
    while p + 2 <= end:
        etype = tables[p]
        elen = tables[p + 1]
        if elen == 0:
            break
        if etype == 0:
            lapics += 1
        elif etype == 1:
            ints += 1
            if int.from_bytes(tables[p+2:p+4], "little") == 0:
                overrides += 1
        p += elen
    print(f"      local APIC entries = {lapics}")
    print(f"      interrupt entries  = {ints} (of which {overrides} override the PIC)")

if "FACP" in results:
    addr, off, length = results["FACP"]
    print()
    print("    FADT detail (the legacy 32-bit fields XP reads):")
    # FADT: sig(4) len(4) rev(1) oem(6) oemid(8) rev(4) creator(4) creatorrev(4)
    #       = 36, then PM1a_EVT_LEN(2) PM1a_CNT_LEN(2) PM1b_EVT(4) PM1b_CNT(4) PM1c(4)
    #       PM2(4) PM_TIMER(4) GPE0(4) GPE1(4)
    for name, o, w in (("PM1a_EVT_LEN", 36, 2), ("PM1a_CNT_LEN", 38, 2),
                       ("PM1b_EVT", 40, 4), ("PM1b_CNT", 44, 4), ("PM1c", 48, 4),
                       ("PM2", 52, 4), ("PM_TIMER", 56, 4), ("GPE0", 60, 4),
                       ("GPE1", 64, 4)):
        v = int.from_bytes(tables[off + o:off + o + w], "little")
        flag = "   <-- ZERO: an XP/2003 HAL reads only these and would find nothing" if v == 0 else ""
        print(f"      {name:12s} = 0x{v:x}{flag}")

open(os.path.join(run, "acpi-summary.txt"), "w").write(
    f"rip=0x{rip:x} cs=0x{cs:x} cr0=0x{cr0:x} rsdp=0x{off:x} rev={rev}\n"
    f"tables={sorted(results)}\n")
print()
print(f"  written: {run}/acpi-summary.txt")
PY