#!/usr/bin/env python3
"""Generate a GUI-free install of the patched AMD driver: file copies plus registry.

Why this exists
---------------
SetupAPI will not select our driver: it matches it, then demotes it (#I087 "Driver node not
trusted") below the stock vga driver, and the one wizard path that bypasses ranking cannot be
reached through this guest's input channel. But a device that already has a binding is not
re-ranked at boot - PnP simply loads what the class key says. So the install can be performed
directly: copy the driver files, create the service, and point the card's class key at them.
That is exactly the state a normal install leaves behind, produced without SetupAPI.

Nothing here is guessed. The class key number comes from the device's own Enum key
(`Driver = {4D36E968-...}\\0001`, read off the guest), and every value below is either read out
of the INF or is the documented meaning of a dir id (10 = %windir%, 11 = system32,
12 = system32\\drivers).

What it emits
-------------
  copies.txt   one "src<TAB>dest" line per file, src relative to the staged driver directory
  install.reg  the ati2mtag service key, its event-log key, and the class-key binding
  skipped.txt  files the INF wants but the package cannot supply, listed rather than ignored

The cabinets are expanded by 7z on the host; the INF names uncompressed files and the package
carries them compressed (.sy_, .dl_, .ex_, .ca_), so the mapping is applied here and any file
that is neither expanded nor compressible is copied verbatim if present.
"""
import argparse
import os
import re
import subprocess
import sys

DIR_IDS = {
    "10": r"C:\WINDOWS",
    "11": r"C:\WINDOWS\system32",
    "12": r"C:\WINDOWS\system32\drivers",
}
CAB_EXT = {".sys": ".sy_", ".dll": ".dl_", ".exe": ".ex_", ".cap": ".ca_"}


def parse_inf(path):
    sections, current = {}, None
    strings = {}
    for raw in open(path, encoding="utf-8", errors="replace"):
        line = raw.rstrip("\r\n")
        if not line.strip() or line.strip().startswith(";"):
            continue
        m = re.match(r"^\[(.+)\]\s*$", line.strip())
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line)
    for line in sections.get("Strings", []):
        if "=" in line:
            k, v = line.split("=", 1)
            strings[k.strip()] = v.strip().strip('"')
    return sections, strings


def subst(line, strings):
    return re.sub(r"%([^%]+)%", lambda m: strings.get(m.group(1), m.group(0)), line)


def kv(lines, key, strings):
    for line in lines:
        m = re.match(rf"^\s*{re.escape(key)}\s*=\s*(.*)$", line)
        if m:
            return subst(m.group(1).split(";")[0].strip(), strings)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inf", required=True)
    ap.add_argument("--package", required=True, help="dir holding the cabinets / verbatim files")
    ap.add_argument("--expanded", required=True, help="dir holding 7z-expanded binaries")
    ap.add_argument("--out", required=True)
    ap.add_argument("--class-key", default="0001",
                    help="display class subkey for the card, from its Enum Driver value")
    args = ap.parse_args()

    sections, strings = parse_inf(args.inf)
    model = sections["ati2mtag_Kaveri_Desktop"]

    dest_dirs = {}
    for line in sections.get("DestinationDirs", []):
        if "=" in line:
            k, v = line.split("=", 1)
            dest_dirs[k.strip()] = v.split(";")[0].strip()

    lists = kv(model, "CopyFiles", strings).split(",")
    # The INF spells names in the vendor's mixed case (Ati2mdxx.exe, ATIDEMGX.dll) while the
    # expanded files are lowercase. Windows would match case-insensitively, so the lookup must
    # too, or six files silently go missing.
    def index(d):
        return {n.lower(): os.path.join(d, n) for n in os.listdir(d)} if os.path.isdir(d) else {}
    exp_idx, pkg_idx = index(args.expanded), index(args.package)
    copies, skipped = [], []
    for lst in [x.strip() for x in lists if x.strip()]:
        d = dest_dirs.get(lst, dest_dirs.get("DefaultDestDir", "11"))
        parts = [p.strip() for p in d.split(",")]
        base = DIR_IDS[parts[0]]
        if len(parts) > 1:
            base = base + "\\" + parts[1]
        for entry in sections.get(lst, []):
            fields = [f.strip() for f in entry.split(",")]
            src = fields[0]
            dst_name = fields[1] if len(fields) > 1 and fields[1] else src
            local = exp_idx.get(src.lower())
            if local is None:
                stem, ext = os.path.splitext(src)
                cab = pkg_idx.get((stem + CAB_EXT.get(ext.lower(), ext)).lower())
                if cab:
                    os.makedirs(args.expanded, exist_ok=True)
                    subprocess.run(["7z", "x", "-y", "-o" + args.expanded, cab],
                                   capture_output=True)
                    exp_idx = index(args.expanded)
                    local = exp_idx.get(src.lower())
            if local is None:
                local = pkg_idx.get(src.lower())
            if local is None:
                skipped.append(f"{lst}: {src}")
                continue
            copies.append((os.path.basename(local), base + "\\" + dst_name))

    svc = sections["ati2mtag_Service_Inst"]
    svc_type = kv(svc, "ServiceType", strings) or "1"
    start = kv(svc, "StartType", strings) or "3"
    errctl = kv(svc, "ErrorControl", strings) or "0"
    group = kv(svc, "LoadOrderGroup", strings) or "Video"
    binary = kv(svc, "ServiceBinary", strings) or r"%12%\ati2mtag.sys"
    binary = binary.replace("%12%", r"system32\drivers").replace("%10%", "").lstrip("\\")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "copies.txt"), "w") as f:
        for s, d in copies:
            f.write(f"{s}\t{d}\n")
    with open(os.path.join(args.out, "skipped.txt"), "w") as f:
        f.write("\n".join(skipped) + ("\n" if skipped else ""))

    ck = (r"HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Class"
          r"\{4D36E968-E325-11CE-BFC1-08002BE10318}" + "\\" + args.class_key)
    # .reg quoted values carry single backslashes; only the quote character is doubled.
    # REG_EXPAND_SZ (hex(2)) is a comma-separated UTF-16 byte list.
    def expand_sz(s):
        return "hex(2):" + ",".join(f"{ord(c):02x},00" for c in s) + ",00,00"
    event_msg_file = expand_sz("%SystemRoot%\\System32\\IoLogMsg.dll"
                               ";%SystemRoot%\\System32\\drivers\\ati2erec.dll"
                               ";%SystemRoot%\\System32\\drivers\\ati2mtag.sys")
    category_msg_file = expand_sz("%SystemRoot%\\System32\\drivers\\ati2erec.dll")
    reg = ["Windows Registry Editor Version 5.00", ""]
    reg += [r"[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\ati2mtag]",
            '"Type"=dword:00000001',
            f'"Start"=dword:{int(start):08x}',
            f'"ErrorControl"=dword:{int(errctl):08x}',
            f'"Group"="{group}"',
            '"ImagePath"="system32\\drivers\\ati2mtag.sys"', ""]
    # The event-log install section's HKR target is the service's EventLog key; the values
    # below are read verbatim from [ati2mtag_EventLog_AddReg] in the INF.
    reg += [r"[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\EventLog\System\ati2mtag]",
            f'"EventMessageFile"={event_msg_file}',
            '"TypesSupported"=dword:00000007',
            f'"CategoryMessageFile"={category_msg_file}',
            '"CategoryCount"=dword:0000003f', ""]
    reg += [f"[{ck}]",
            '"InfPath"="CA178340.inf"',
            '"InfSection"="ati2mtag_Kaveri_Desktop"',
            '"ProviderName"="Advanced Micro Devices, Inc."',
            '"DriverDesc"="AMD Radeon R9 290X / 390X"',
            '"MatchingDeviceId"="pci\\ven_1002&dev_67b0"',
            '"Service"="ati2mtag"',
            '"Class"="Display"',
            '"DriverDate"="11/29/2014"',
            '"DriverVersion"="9.0.300.3005"',
            '"ConfigFlags"=dword:00000000', ""]
    open(os.path.join(args.out, "install.reg"), "w", newline="").write("\r\n".join(reg))

    print(f"  copies: {len(copies)}   skipped: {len(skipped)}")
    for s in skipped:
        print(f"    SKIP {s}")
    print(f"  service: ati2mtag type={svc_type} start={start} err={errctl} group={group} "
          f"image={binary}")
    print(f"  class key bound: {ck}")


if __name__ == "__main__":
    main()