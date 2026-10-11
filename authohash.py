#!/usr/bin/env python3
"""The Authenticode PE hash, implemented so the catalog's per-file entries can be checked.

AMD's catalog stores a plain SHA-1 for the INF and an Authenticode hash for every PE. That
was not obvious from the file: both are 20 bytes and both sit under the same attribute, and
comparing the PE entries against `sha1sum` fails on all of them. The catalog is the ground
truth, so the algorithm is taken from the Authenticode specification and then verified
against AMD's own entries rather than assumed correct.

Excluded regions, per the PE specification: the optional header checksum, the
IMAGE_DIRECTORY_ENTRY_SECURITY data-directory entry, the certificate table itself, and the
padding between sections. The file size contribution is folded in as a little-endian DWORD
so a file cannot be extended without changing the hash.
"""
import hashlib
import struct
import sys


def authenticode_hash(data, alg="sha1"):
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
    dd = opt + (112 if magic == 0x20B else 96)      # first data directory
    checksum_off = opt + 64
    sizeofheaders = struct.unpack_from("<I", data, opt + 60)[0]
    sec_dir_off = dd + 4 * 8                        # index 4 is the certificate table
    cert_off, cert_size = struct.unpack_from("<II", data, sec_dir_off)

    h = hashlib.new(alg)
    h.update(data[:checksum_off])                   # up to the checksum
    h.update(data[checksum_off + 4:sec_dir_off])    # checksum is excluded
    h.update(data[sec_dir_off + 8:sizeofheaders])   # the directory entry is excluded

    sections = []
    table = opt + opt_size
    for i in range(n_sections):
        base = table + 40 * i
        raw_size = struct.unpack_from("<I", data, base + 16)[0]
        raw_ptr = struct.unpack_from("<I", data, base + 20)[0]
        if raw_size:
            sections.append((raw_ptr, raw_size))
    sections.sort()
    for i, (ptr, size) in enumerate(sections):
        h.update(data[ptr:ptr + size])
        end = ptr + size
        if i + 1 < len(sections):
            nxt = sections[i + 1][0]
        else:
            nxt = cert_off if cert_size else len(data)
        h.update(data[end:nxt])                     # the inter-section padding counts

    total = len(data) - sizeofheaders
    if cert_size:
        total += 8 + cert_size
    h.update(struct.pack("<I", total))
    return h.digest()


if __name__ == "__main__":
    for p in sys.argv[1:]:
        d = open(p, "rb").read()
        print(f"{p}\n  sha1      = {hashlib.sha1(d).hexdigest().upper()}\n"
              f"  authenticode = {authenticode_hash(d).hex().upper()}")