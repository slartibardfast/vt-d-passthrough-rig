#!/usr/bin/env python3
"""audit-offline.py - the guest's own records, read with QEMU down.

Mounts the system disk and the FAT channel read-only, pulls setupapi.log and
the SYSTEM/SOFTWARE hives, and answers the offline-audit questions:

  1. why the XDDM stack never binds (setupapi + the display keys)
  2. the FAT channel's guest drive letter (MountedDevices)
  3. which boot-time mechanism runs staged binaries (Run/RunOnce/services)

Everything lands in work/audit/ and the findings in work/audit-findings.md.
"""
import os
import subprocess
import sys

RIG = os.path.dirname(os.path.abspath(__file__))
AUDIT = os.path.join(RIG, "work", "audit")
MNT_SYS = "/mnt/xp-ro"
MNT_FAT = "/mnt/d-ro"
SYS_DISK = os.path.join(RIG, "install-blank.raw")
FAT_DISK = os.path.join(RIG, "gpudrv.img")
FINDINGS = os.path.join(RIG, "work", "audit-findings.md")

out = []


def say(s=""):
    print(s)
    out.append(s)


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def mount_ro():
    os.makedirs(MNT_SYS, exist_ok=True)
    os.makedirs(MNT_FAT, exist_ok=True)
    loops = []
    for img, mnt, fstype in ((SYS_DISK, MNT_SYS, "ntfs-3g"), (FAT_DISK, MNT_FAT, "vfat")):
        r = sh("sudo", "losetup", "--show", "-fP", "--partscan", img)
        if r.returncode != 0:
            say(f"  losetup failed for {img}: {r.stderr.strip()}")
            return None
        loop = r.stdout.strip()
        part = f"{loop}p1"
        r = sh("sudo", "mount", "-o", "ro", "-t", fstype, part, mnt)
        if r.returncode != 0:
            r = sh("sudo", "mount", "-o", "ro", "-t", fstype, loop, mnt)
            part = loop
        if r.returncode != 0:
            say(f"  mount failed for {img}: {r.stderr.strip()}")
            sh("sudo", "losetup", "-d", loop)
            return None
        loops.append((loop, mnt))
    return loops


def unmount(loops):
    for loop, mnt in loops:
        sh("sudo", "umount", mnt)
        sh("sudo", "losetup", "-d", loop)


import hivex

_HIVES = {}


def _hive(path):
    if path not in _HIVES:
        _HIVES[path] = hivex.Hivex(path)
    return _HIVES[path]


def _value_str(v):
    t, val = v
    if t == 1 or t == 2:                      # sz / expand_sz
        try:
            return val.decode("utf-16-le").rstrip("\x00")
        except UnicodeDecodeError:
            return repr(val)
    if t == 4:
        return str(int.from_bytes(val, "little"))
    if t == 5:
        return str(int.from_bytes(val, "little"))
    if t in (3, 11):                          # binary / none
        return val.hex(" ")[:160] + ("..." if len(val) > 80 else "")
    return repr(val)


def hivexsh_dump(hive, path):
    """Subkeys and values of a key, rendered."""
    h = _hive(hive)
    node = 0
    try:
        cur = h.root()
        ok = True
        for part in path.split("\\"):
            if not part:
                continue
            nxt = 0
            for c in h.node_children(cur):
                if h.node_name(c).lower() == part.lower():
                    nxt = c
                    break
            if not nxt:
                ok = False
                break
            cur = nxt
        node = cur if ok else 0
    except Exception:
        return "(absent)"
    if not node:
        return "(absent)"
    lines = []
    kids = h.node_children(node)
    if kids:
        lines.append("subkeys: " + ", ".join(sorted(h.node_name(k) for k in kids)))
    for v in h.node_values(node):
        name = h.value_key(v) or "(default)"
        lines.append(f"  {name} = {_value_str(h.value_value(v))}")
    return "\n".join(lines) if lines else "(empty key)"


def hivexget(hive, key, val=None):
    h = _hive(hive)
    node = 0
    try:
        cur = h.root()
        ok = True
        for part in key.split("\\"):
            if not part:
                continue
            nxt = 0
            for c in h.node_children(cur):
                if h.node_name(c).lower() == part.lower():
                    nxt = c
                    break
            if not nxt:
                ok = False
                break
            cur = nxt
        node = cur if ok else 0
    except Exception:
        return ""
    if not node:
        return ""
    for v in h.node_values(node):
        if (h.value_key(v) or "(default)") == val:
            return _value_str(h.value_value(v))
    return ""


def main():
    os.makedirs(AUDIT, exist_ok=True)
    loops = mount_ro()
    if not loops:
        return 1

    try:
        # ---- raw pulls
        pulls = [
            (f"{MNT_SYS}/WINDOWS/setupapi.log", "setupapi.log"),
            (f"{MNT_SYS}/WINDOWS/setupapi.old", "setupapi.old"),
            (f"{MNT_SYS}/WINDOWS/system32/config/system", "SYSTEM"),
            (f"{MNT_SYS}/WINDOWS/system32/config/software", "SOFTWARE"),
            (f"{MNT_SYS}/AUTOEXEC.BAT", "AUTOEXEC.BAT"),
        ]
        for src, name in pulls:
            if os.path.exists(src):
                r = sh("sudo", "cp", src, os.path.join(AUDIT, name))
                sh("sudo", "chmod", "644", os.path.join(AUDIT, name))
                say(f"  pulled {name}: {os.path.getsize(os.path.join(AUDIT, name))} bytes")
            else:
                say(f"  {name}: not present")
        # the ATI inf, whichever oem slot it took
        r = sh("sudo", "grep", "-l", "-i", "ati2mtag", f"{MNT_SYS}/WINDOWS/inf/oem*.inf")
        if r.stdout.strip():
            for f in r.stdout.split():
                name = "INF_" + os.path.basename(f)
                sh("sudo", "cp", f, os.path.join(AUDIT, name))
                sh("sudo", "chmod", "644", os.path.join(AUDIT, name))
                say(f"  pulled {name}")
        # the FAT channel listing (the staged files and write-backs)
        r = sh("sudo", "ls", "-la", MNT_FAT)
        say("  FAT channel listing:\n" +
            "".join("    " + l + "\n" for l in r.stdout.strip().split("\n")))
        open(os.path.join(AUDIT, "fat-listing.txt"), "w").write(r.stdout)
    finally:
        unmount(loops)

    sys_hive = os.path.join(AUDIT, "SYSTEM")
    sw_hive = os.path.join(AUDIT, "SOFTWARE")

    say("\n== MountedDevices (the drive letters) ==")
    say(hivexsh_dump(sys_hive, "MountedDevices"))

    say("\n== Select (the live ControlSet) ==")
    say("Current: " + hivexget(sys_hive, "Select", "Current"))

    for cs in ("ControlSet001", "ControlSet002"):
        say(f"\n== Services\\ati2mtag ({cs}) ==")
        say(hivexsh_dump(sys_hive, cs + "\\Services\\ati2mtag"))
        say(f"\n== Services\\VgaSave ({cs}) ==")
        say(hivexsh_dump(sys_hive, cs + "\\Services\\VgaSave"))

    say("\n== Display class instances ==")
    disp = "{4D36E968-E325-11CE-BFC1-08002BE10318}"
    for cs in ("ControlSet001", "ControlSet002"):
        base = f"{cs}\\Control\\Class\\{disp}"
        say(f"-- {base} --")
        say(hivexsh_dump(sys_hive, base))
        for inst in ("0000", "0001", "0002"):
            say(f"-- {base}\\{inst} --")
            say(hivexsh_dump(sys_hive, base + "\\" + inst))

    say("\n== PCI enumeration of the GPU ==")
    for cs in ("ControlSet001", "ControlSet002"):
        base = f"{cs}\\Enum\\PCI"
        txt = hivexsh_dump(sys_hive, base)
        say(f"-- {base} (ven_1002 lines) --")
        say("\n".join(l for l in txt.split("\n") if "VEN_1002" in l.upper()))
        for dev in ("VEN_1002&DEV_67B0", "VEN_1002&DEV_67B1"):
            for inst in ("", "&SUBSYS_00000000", "3&13C0&0&00"):
                key = f"{base}\\{dev}{inst}"
                vals = hivexget(sys_hive, key)
                if vals:
                    say(f"-- {key} --")
                    say(vals)

    say("\n== Control\\Video (the win32k binding) ==")
    for cs in ("ControlSet001", "ControlSet002"):
        base = f"{cs}\\Control\\Video"
        subs = hivexsh_dump(sys_hive, base)
        say(f"-- {base} --")
        say(subs)
        for line in subs.split("\n"):
            k = line.strip().rstrip("*").strip()
            if k and k not in ("Properties",):
                say(f"-- {base}\\{k}\\0000 --")
                say(hivexsh_dump(sys_hive, f"{base}\\{k}\\0000"))

    say("\n== Boot trigger candidates ==")
    say("-- SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run --")
    say(hivexsh_dump(sw_hive, "Microsoft\\Windows\\CurrentVersion\\Run"))
    say("-- RunOnce --")
    say(hivexsh_dump(sw_hive, "Microsoft\\Windows\\CurrentVersion\\RunOnce"))
    say("-- services whose ImagePath leaves WINDOWS --")
    for cs in ("ControlSet001", "ControlSet002"):
        txt = hivexsh_dump(sys_hive, cs + "\\Services")
        keep, capture = [], False
        for l in txt.split("\n"):
            if "ImagePath" in l and ("b:\\" in l.lower() or "d:\\" in l.lower()):
                keep.append(l)
        say(f"-- {cs} ImagePath hits --")
        say("\n".join(keep))

    with open(FINDINGS, "w") as f:
        f.write("# Offline audit findings\n\nGenerated by rig/audit-offline.py.\n\n```\n")
        f.write("\n".join(out))
        f.write("\n```\n")
    say(f"\n  findings written to {FINDINGS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
