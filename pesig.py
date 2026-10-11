#!/usr/bin/env python3
"""Report the Authenticode signature state of PE files, with a self-check.

Why this exists: the patched INF invalidates its catalog, and XP x64 has no
boot-time signing opt-out (the F8 "Disable Driver Signature Enforcement" entry is
Vista and later). So the question that decides the install route is whether the
driver binaries carry their OWN embedded signatures. They are untouched AMD files
even though the INF is edited, so per-file Authenticode verification can succeed
on its own if the CatalogFile directive is removed.

The parser is validated against a file known to be signed before its verdict on
anything is believed: see selfcheck().
"""
import hashlib
import struct
import sys
import subprocess


def parse_pe(data):
    """Return (cert_table_offset, cert_table_size) from the PE security directory."""
    if data[:2] != b"MZ":
        raise ValueError("not an MZ image")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew:e_lfanew + 4] != b"PE\0\0":
        raise ValueError("no PE signature")
    coff = e_lfanew + 4
    n_sections = struct.unpack_from("<H", data, coff + 2)[0]
    opt_size = struct.unpack_from("<H", data, coff + 16)[0]
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic == 0x10B:            # PE32
        dd_off = opt + 96
    elif magic == 0x20B:          # PE32+
        dd_off = opt + 112
    else:
        raise ValueError(f"unknown optional header magic 0x{magic:04x}")
    # Data directory index 4 is IMAGE_DIRECTORY_ENTRY_SECURITY.
    sec_off, sec_size = struct.unpack_from("<II", data, dd_off + 4 * 8)
    return sec_off, sec_size


def certificate_chains(data):
    """Walk the WIN_CERTIFICATE table; yield (length, revision, type)."""
    sec_off, sec_size = parse_pe(data)
    if not sec_size:
        return []
    out = []
    pos = sec_off
    end = sec_off + sec_size
    while pos + 8 <= end:
        length, revision, cert_type = struct.unpack_from("<IHH", data, pos)
        if length < 8:
            break
        out.append((length, revision, cert_type))
        pos += (length + 7) & ~7      # entries are 8-byte aligned
    return out


def signer_names(data):
    """Best-effort: pull CN values out of the embedded PKCS#7 without a full ASN.1 walk."""
    names = []
    for needle in (b"\x55\x04\x03", b"\x55\x04\x0a", b"\x55\x04\x06"):
        i = 0
        while True:
            i = data.find(needle, i)
            if i < 0:
                break
            ln = data[i + 2]
            names.append(data[i + 3:i + 3 + ln].decode("latin-1", "replace"))
            i += 3 + ln
            if len(names) > 40:
                return sorted(set(names))
    return sorted(set(names))


def selfcheck():
    """Cross-check the security-directory offset against binutils, an independent decoder.

    The first version of this check asserted that the stock XP x64 kernel carries an
    embedded signature. It does not: XP inbox binaries are catalog-signed, not embedded
    signed, so the assertion was false and the check reported FAILED against a correct
    parser. A falsifier that can only fail for the wrong reason is no falsifier, so the
    check now compares two independent implementations instead of a remembered fact.
    """
    try:
        data = open("/tmp/opencode/iso/ntkrnlmp.exe", "rb").read()
    except OSError:
        return "SKIPPED (no reference image)"
    off, size = parse_pe(data)
    try:
        out = subprocess.run(["objdump", "-x", "/tmp/opencode/iso/ntkrnlmp.exe"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return f"objdump unavailable; parser alone says table=0x{off:x} size={size}"
    for line in out.splitlines():
        if "Security Directory" in line:
            fields = line.split()
            # objdump prints the security directory as "Entry 4 <vaddr> <size> Security Directory"
            try:
                obj_off = int(fields[2], 16) & 0xFFFFFFFFFFFFFFFF
                obj_size = int(fields[3], 16)
            except (IndexError, ValueError):
                return "could not parse objdump's security directory line"
            if obj_off != off or obj_size != size:
                return (f"FAILED (parser says 0x{off:x}/{size}, "
                        f"objdump says 0x{obj_off:x}/{obj_size})")
            entries = certificate_chains(data)
            return (f"OK (ntkrnlmp.exe: 0x{off:x}/{size}, parser agrees with objdump; "
                    f"{len(entries)} WIN_CERTIFICATE entries)")
    return "FAILED (objdump did not report a security directory)"


def report(path):
    data = open(path, "rb").read()
    try:
        off, size = parse_pe(data)
    except ValueError as e:
        return f"{path}: not a PE ({e})"
    if size == 0:
        return f"{path}: UNSIGNED (security directory empty, {len(data)} bytes)"
    entries = certificate_chains(data)
    kinds = {0x0001: "X509", 0x0002: "PKCS_SIGNED_DATA", 0x0003: "RESERVED", 0x0004: "TS_STACK"}
    desc = ", ".join(f"{kinds.get(t, hex(t))} rev=0x{r:x} len={l}" for l, r, t in entries)
    names = signer_names(data)
    who = "; ".join(n for n in names if n.strip())[:120]
    return f"{path}: SIGNED table=0x{off:x} size={size} [{desc}] signer={who or '(CN not found)'}"


if __name__ == "__main__":
    print(f"parser self-check: {selfcheck()}")
    for p in sys.argv[1:]:
        print(report(p))