#!/usr/bin/env python3
"""Emit the INF's SoftwareSettings AddReg chain as a .reg file for the driver key.

Why this exists
---------------
A normal display-driver install has the display class installer apply the INF's
`<install-section>.SoftwareSettings` AddReg chain into the device's driver key. The
hand-install (handinstall.py) bypasses SetupAPI entirely and so never ran those sections;
the miniport then starts without the registry configuration the vendor tuned for it.

What counts as "HKR"
--------------------
In a SoftwareSettings AddReg line the root `HKR` resolves to the device's driver key -
here the display class subkey named by the device's own Enum `Driver` value (0001).

Grammar handled
---------------
    reg-root,[subkey],[value-name],[flags],[data]
Flags follow the INF AddReg table: 0x00000000 SZ, 0x00000001 BINARY, 0x00010000 MULTI_SZ,
0x00010001 DWORD, 0x00020000 EXPAND_SZ, 0x00020001 NONE; NOCLOBBER (0x2) and APPEND (0x4)
are behavior bits and are stripped. Lines L cannot map are listed in skipped.txt rather
than silently dropped.
"""
import argparse
import os
import re
import sys

from handinstall import parse_inf, subst

CHAIN = [
    "ati2mtag_SoftwareDeviceSettings",
    "ati2mtag_Kaveri_Desktop_SoftwareDeviceSettings",
    "ati2mtag_Desktop_SoftwareDeviceSettings",
    "ati2mtag_Post_EG",
    "atiorca_OpenGLSoftwareSettings",
    "ati2mtag_LargeDesktopSettings",
]

TYPE_SZ = 0x00000000
TYPE_BINARY = 0x00000001
TYPE_MULTI = 0x00010000
TYPE_DWORD = 0x00010001
TYPE_EXPAND = 0x00020000
TYPE_NONE = 0x00020001


def split_fields(line):
    """Split an AddReg line on commas that are not inside double quotes."""
    fields, buf, inq = [], [], False
    for ch in line:
        if ch == '"':
            inq = not inq
            buf.append(ch)
        elif ch == ',' and not inq:
            fields.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    fields.append("".join(buf).strip())
    return fields


def expand_sz(s):
    return "hex(2):" + ",".join(f"{ord(c):02x},00" for c in s) + ",00,00"


def multi_sz(items):
    body = ",".join(f"{ord(c):02x},00" for it in items for c in it) + \
        ",00,00," + "00,00,".join([] for _ in ())  # placeholder, unused path
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inf", required=True)
    ap.add_argument("--class-key", default="0001")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sections, strings = parse_inf(args.inf)
    ck = (r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Class"
          r"\{4D36E968-E325-11CE-BFC1-08002BE10318}" + "\\" + args.class_key)

    out = ["Windows Registry Editor Version 5.00", ""]
    skipped = []
    applied = 0

    for sec in CHAIN:
        for raw in sections.get(sec, []):
            line = raw
            # strip trailing comment outside quotes
            fields = split_fields(line)
            fields = [f for f in fields]
            if not fields or fields[0] == "":
                continue
            root = fields[0].upper()
            if root != "HKR":
                skipped.append(f"{sec}: non-HKR root: {raw.strip()}")
                continue
            subkey = subst(fields[1], strings) if len(fields) > 1 else ""
            name = subst(fields[2], strings) if len(fields) > 2 else ""
            subkey = subkey.strip().strip('"')
            name = name.strip().strip('"')
            flags = 0
            data = None
            if len(fields) > 3:
                f = subst(fields[3], strings).strip()
                if f:
                    try:
                        flags = int(f, 0)
                    except ValueError:
                        skipped.append(f"{sec}: bad flags: {raw.strip()}")
                        continue
            if len(fields) > 4:
                data = subst(fields[4], strings).strip()
            if len(fields) > 5:
                # MULTI_SZ lists and BINARY blobs continue past the 5th field
                data = data + "," + ",".join(subst(x, strings).strip() for x in fields[5:])

            target = ck + ("\\" + subkey if subkey else "")
            keyline = f"[{target}]"
            if keyline not in out:
                out.append(keyline)

            noclobber = bool(flags & 0x00000002)
            typ = flags & ~0x00000002 & ~0x00000004 & ~0x00000008
            if name == "":
                # default value
                name = "@"

            try:
                if typ == TYPE_DWORD:
                    val = int(str(data), 0)
                    out.append(f'"{name}"=dword:{val & 0xFFFFFFFF:08x}')
                elif typ == TYPE_EXPAND:
                    out.append(f'"{name}"={expand_sz(data.strip())}')
                elif typ == TYPE_BINARY:
                    hexstr = re.sub(r"[^0-9a-fA-F]", "", str(data))
                    out.append(f'"{name}"=hex:{",".join(hexstr[i:i+2] for i in range(0, len(hexstr), 2))}'
                               if hexstr else f'"{name}"=hex:')
                elif typ == TYPE_NONE:
                    out.append(f'"{name}"=hex:')
                elif typ == TYPE_MULTI:
                    items = [p.strip().strip('"') for p in str(data).split(",") if p.strip()]
                    enc = []
                    for it in items:
                        enc.extend(f"{ord(c):02x},00" for c in it)
                        enc.append("00,00")
                    enc.append("00,00")
                    out.append(f'"{name}"=hex(7):{",".join(enc)}')
                elif typ in (TYPE_SZ,):
                    data = str(data)
                    if data.startswith('"') and data.endswith('"') and len(data) >= 2:
                        data = data[1:-1]
                    out.append(f'"{name}"="{data}"')
                else:
                    skipped.append(f"{sec}: unknown type {typ:#x}: {raw.strip()}")
                    continue
                applied += 1
            except Exception as e:  # noqa: BLE001 - report and continue
                skipped.append(f"{sec}: {raw.strip()} ({e})")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "settings.reg"), "w", newline="") as f:
        f.write("\r\n".join(out) + "\r\n")
    with open(os.path.join(args.out, "settings-skipped.txt"), "w") as f:
        f.write("\n".join(skipped) + ("\n" if skipped else ""))
    print(f"  values applied: {applied}   skipped: {len(skipped)}")
    for s in skipped[:12]:
        print(f"    SKIP {s}")


if __name__ == "__main__":
    main()
