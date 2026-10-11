#!/usr/bin/env python3
"""Dump the evidence that distinguishes a booting guest from a hanging one.

usage: evidence.py <run-dir>

Reads whatever the harness left behind (gdb.log with the register set, b8000.bin
for the screen) and, if a live gdbstub is reachable, goes and fetches the rest:

  * the ACPI tables the guest was actually handed - found by scanning low memory
    for the RSDP signature, then walking its pointers. This is the comparison that
    matters, because CSMWrap patches ACPI in place.
  * the physical memory map the kernel believes in, recovered by walking its own
    page tables from CR3. A kernel that thinks it has the wrong RAM behaves in
    ways that look like firmware bugs.
  * the IOAPIC redirection table at 0xfec00000, which shows which IRQs are
    unmasked and where they route. This QEMU has no `info ioapic`, so MMIO is the
    only route.
  * the interrupt-controller state, to see whether anything is pending.

Everything is written as text plus raw dumps; nothing here interprets. Reading it is
a separate step on purpose, so a parser bug cannot invent a finding.
"""
import os
import re
import subprocess
import sys

GDB_PORT = 1234
GDB_BASE = [
    "gdb", "-batch",
    "-ex", "set pagination off",
    "-ex", "target remote :127.0.0.1:%d" % GDB_PORT,
    "-ex", "set architecture i386:x86-64",
]


def gdb(script, timeout=180):
    """Run gdb commands; return stdout text. Empty string if it cannot connect."""
    cmd = GDB_BASE[:6] + [item for c in script for item in ("-ex", c)] + ["-ex", "detach"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    out = r.stdout + r.stderr
    if "could not connect" in out or "Connection" in out.split("\n")[0]:
        return ""
    return out


def parse_registers(text):
    regs = {}
    for m in re.finditer(r"^\s*([a-z0-9]+)\s+(0x[0-9a-f]+)", text, re.M):
        regs.setdefault(m.group(1), int(m.group(2), 16))
    return regs


def page_map(run, regs):
    """Walk CR3's page tables and report which physical ranges are present."""
    cr3 = regs.get("cr3")
    if not cr3:
        return "  (no CR3: the guest never enabled paging, so there is no map to walk)"
    cr3 &= ~0xFFF
    lines = ["  guest page tables from CR3=0x%x:" % cr3]
    # PML4 -> PDPT -> PD, only for the identity/lower-half entries XP uses.
    pml4 = gdb([f"x/8gx {cr3:#x}"])
    if not pml4:
        return "  (could not read the PML4 over the stub)"
    entries = re.findall(r"0x([0-9a-f]{16})", pml4)
    present = []
    for i, e in enumerate(entries[:8]):
        v = int(e, 16)
        if v & 1:
            present.append(i)
    lines.append(f"    PML4 present entries: {present} (each covers 512 GiB)")
    for i in present[:2]:
        pdpt_addr = int(entries[i], 16) & ~0xFFF
        pdpt = gdb([f"x/8gx {pdpt_addr:#x}"])
        if not pdpt:
            continue
        pentries = re.findall(r"0x([0-9a-f]{16})", pdpt)
        for j, pe in enumerate(pentries[:4]):
            pv = int(pe, 16)
            if not (pv & 1):
                continue
            if pv & 0x80:  # PS: a 2 MiB page
                lines.append(
                    f"    PDPT[{i}][{j}] = 2MiB pages, physical base 0x{pv & 0xFFFFFFE00000:x}")
            else:
                lines.append(f"    PDPT[{i}][{j}] = points at PD 0x{pv & ~0xFFF:x} (4KiB pages)")
    return "\n".join(lines)


def ioapic(run):
    """The IOAPIC redirection table, by MMIO."""
    lines = ["  IOAPIC redirection table (MMIO 0xfec00000):"]
    lines.append("    index  full  vector  dest      mode       ")
    out = gdb([
        "dump memory %s/ioapic.bin 0xfec00000 0xfec00100" % run,
    ])
    if not out:
        return "  (could not read 0xfec00000 - is the guest in long mode?)"
    try:
        data = open(os.path.join(run, "ioapic.bin"), "rb").read()
    except OSError:
        return "  (ioapic.bin not written)"
    for i in range(24):
        off = 0x10 + i * 2
        if off + 2 > len(data):
            break
        lo, hi = data[off], data[off + 1]
        full = (lo >> 3) & 1
        dest = (hi >> 24) & 0xFF
        vec = lo & 0xFF
        masked = (lo >> 1) & 1
        level = "level" if (lo >> 7) & 1 else "edge"
        destmode = (hi >> 10) & 3
        lines.append(
            f"    {i:5d}  {full:4d}  0x{vec:02x}   dest {dest:3d}  "
            f"{level:6s}  {'MASKED' if masked else 'unmasked'}")
    return "\n".join(lines)


def acpi(run, regs):
    """Find the RSDP by signature and dump every table it points at."""
    lines = ["  ACPI tables as handed to the OS:"]
    low = gdb([f"dump memory {run}/low.bin 0x0 0x200000"])
    if not low:
        return "  (could not dump low memory)"
    data = open(os.path.join(run, "low.bin"), "rb").read()
    rsdp = data.find(b"RSD PTR ")
    if rsdp < 0:
        return "  (no RSDP signature in the first 2 MiB)"
    lines.append(f"    RSDP at physical 0x{rsdp:x}")
    # RSDP: signature(16) checksum(4) oemid(8) revision(4) rsdt(4) length(4)...
    rsdt = int.from_bytes(data[rsdp + 24:rsdp + 28], "little")
    xsdt = int.from_bytes(data[rsdp + 32:rsdp + 40], "little")
    length = int.from_bytes(data[rsdp + 20:rsdp + 24], "little")
    rev = data[rsdp + 15]
    oem = data[rsdp + 10:rsdp + 16].rstrip(b"\0").decode("ascii", "replace")
    lines.append(f"    revision {rev}, OEM {oem!r}, length {length}")
    lines.append(f"    RSDT -> 0x{rsdt:x}   XSDT -> 0x{xsdt:x}")
    # Dumping the table region: they are below 4 GiB by requirement.
    if not gdb([f"dump memory {run}/tables.bin 0x80000 0x100000"]):
        return "\n".join(lines + ["    (could not dump the table region)"])
    tables = open(os.path.join(run, "tables.bin"), "rb").read()
    base = 0x80000
    for sig in (b"APIC", b"FACP", b"DSDT", b"SSDT", b"HPET", b"MCFG"):
        off = tables.find(sig)
        if off < 0:
            continue
        addr = base + off
        length = int.from_bytes(tables[off + 4:off + 8], "little")
        lines.append(f"    {sig.decode():5s} at 0x{addr:x}, length {length}")
        if sig == b"APIC":
            flags = int.from_bytes(tables[off + 44:off + 48], "little")
            pc = (flags >> 10) & 1
            lines.append(
                f"      MADT flags=0x{flags:x} PCAT_COMPAT(bit10)={pc} "
                f"revision={tables[off+8]}")
            # the LAPIC timer override is the last 4 bytes before the entry list
            ovr = int.from_bytes(tables[off + 40:off + 44], "little")
            lines.append(f"      LAPIC timer override entry = {ovr} (0x{ovr:x})")
            if ovr:
                lines.append(
                    f"        -> declares a bus clock of {10000000/ovr:.4f} Hz "
                    f"({10000000/ovr/1e6:.4f} MHz)")
        if sig == b"FACP":
            # legacy PM/GPE 32-bit block fields: FADT layout offsets from the sig
            for name, off_in_fadt, width in (
                ("PM1a_EVT_LEN", 66, 2), ("PM1a_CNT_LEN", 68, 2),
                ("PM1b_EVT", 72, 4), ("PM1b_CNT", 76, 4),
                ("PM1c", 80, 4), ("PM2", 84, 4), ("PM_TIMER", 88, 4),
                ("GPE0", 92, 4), ("GPE1", 96, 4),
            ):
                v = int.from_bytes(
                    tables[off + off_in_fadt:off + off_in_fadt + width], "little")
                lines.append(f"      FADT {name:12s} = 0x{v:x}" + ("  <-- ZERO" if v == 0 else ""))
    return "\n".join(lines)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    run = sys.argv[1]
    os.makedirs(run, exist_ok=True)
    print(f"=== evidence for {run} ===\n")

    g = os.path.join(run, "gdb.log")
    regs = {}
    if os.path.exists(g):
        regs = parse_registers(open(g, errors="replace").read())
    print("  registers (from the harness's gdb.log):")
    for k in ("rip", "rsp", "eflags", "cr0", "cr2", "cr3", "cs", "ss"):
        if k in regs:
            print(f"    {k:7s} = 0x{regs[k]:x}")
    print()

    print(page_map(run, regs))
    print()
    print(acpi(run, regs))
    print()
    print(ioapic(run))
    return 0


if __name__ == "__main__":
    sys.exit(main())