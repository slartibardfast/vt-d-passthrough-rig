#!/usr/bin/env python3
"""Read the settled offline state out of a golden, read-only, and say whether a run can rely on it.

Why this exists. Every run that boots the golden pays for the offline settings once instead of every
boot, because they are baked into the disk image: the drive letter pinned to the driver volume, the
hard-error suppression that keeps the `Windows - No Disk` modal from blocking an instrument, the
boot-start service key that names the channel path, and the per-adapter key that names the display
driver. `plan/0007` claims the OS golden carries them. A claim of that shape is worth exactly the
check behind it, and until now the check was that an earlier session wrote the sentence.

What is read, and from where. `ConfigManagerErrorCode` and the volatile video map exist only in
memory, so they cannot be had from an image and are not asked for here; `call/0014` settles that and
says the running guest is the only witness. What can be had from an image is what Windows writes to
a hive file, and that is the list here: the service key, the per-adapter selection, the letter pin
in `MountedDevices`, `ErrorMode`, and the autologon values, which live in the SOFTWARE hive rather
than the SYSTEM hive where the name might suggest.

The image is attached read-only with the format named rather than probed, the hives are copied out,
and the attachment is dropped before any parsing, so a bug in this reader cannot corrupt a golden.

Usage: golden-state.py --golden <qcow2> [--record]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RIG = Path(__file__).resolve().parent
sys.path.insert(0, str(RIG))
import registration_graft as rg

SERVICE = "ati2mtag"
SERVICE_IMAGE = r"\??\D:\drv\ati2mtag.sys"
WANT = {
    "service_start": 1,
    "service_type": 1,
    "errormode": 2,
    "dev_vgacompat": 0,
    "dev_drivers": ["ati2dvag"],
    "dev_service": SERVICE,
    "service_imagepath": SERVICE_IMAGE,
    "letters_pinned": True,
}


class Bad(Exception):
    pass


def sh(*cmd, check=True):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True)
    if check and r.returncode != 0:
        raise Bad("%s failed: %s" % (" ".join(cmd), (r.stdout + r.stderr).strip() or "no output"))
    return r


def a_quiet_host():
    if sh(RIG / "guest-running.sh", "check", check=False).returncode == 0:
        raise Bad("a QEMU is running: a golden read from under a live guest is a golden of no state "
                  "anyone can name, because the writeback cache holds writes the file does not")


def require_tools():
    for tool in ("qemu-nbd", "mount", "qemu-img"):
        if not shutil.which(tool):
            pkg = "qemu-utils" if tool.startswith("qemu") else "util-linux"
            raise Bad("%s is required; on Ubuntu and Debian it comes from the package %s" % (tool, pkg))


def free_device():
    """An unused nbd device, so a check never sits on one another run is using."""
    sh("sudo", "modprobe", "nbd", "max_part=8", check=False)
    for n in range(16):
        dev = "/dev/nbd%d" % n
        if not Path(dev).exists():
            continue
        if sh("sudo", "fuser", "-s", dev, check=False).returncode != 0:
            return dev
    raise Bad("every nbd device is busy, so nothing was attached")


def wait_partition(dev):
    for _ in range(40):
        parts = sorted(Path("/dev").glob(Path(dev).name + "p*"))
        if parts:
            return parts[0]
        time.sleep(0.5)
    raise Bad("%s appeared with no partition, so %s has no filesystem to read" % (dev, dev))


def locate(mount, rel):
    """Find a path whatever case the volume stores it in, or None."""
    here = Path(mount)
    for piece in rel.split("/"):
        if not here.exists():
            return None
        exact = here / piece
        if exact.exists():
            here = exact
            continue
        try:
            named = [c for c in here.iterdir() if c.name.lower() == piece.lower()]
        except PermissionError:
            return None
        if len(named) != 1:
            return None
        here = named[0]
    return here


class attached:
    """Mount a golden read-only for the duration of a block, and always let go afterwards."""

    def __init__(self, golden):
        self.golden = golden
        self.dev = None
        self.dir = None

    def __enter__(self):
        self.dev = free_device()
        sh("sudo", "qemu-nbd", "-r", "-f", "qcow2", "-c", self.dev, str(self.golden))
        part = wait_partition(self.dev)
        self.dir = tempfile.mkdtemp(prefix="golden-state-", dir="/tmp/opencode")
        sh("sudo", "mount", "-o", "ro", str(part), self.dir)
        return self

    def __exit__(self, *exc):
        if self.dir:
            sh("sudo", "umount", self.dir, check=False)
            shutil.rmtree(self.dir, ignore_errors=True)
        if self.dev:
            sh("sudo", "qemu-nbd", "-d", self.dev, check=False)
        return False

    def read_to(self, rel, dest):
        src = locate(self.dir, rel)
        if src is None:
            return None
        sh("sudo", "cp", "-p", str(src), str(dest))
        sh("sudo", "chown", "%d:%d" % (os.getuid(), os.getgid()), str(dest), check=False)
        return dest

    def text(self, rel):
        src = locate(self.dir, rel)
        return None if src is None else sh("sudo", "cat", str(src)).stdout


def copy_hives(golden, into):
    with attached(golden) as img:
        return {name: img.read_to(rel, into / name)
                for name, rel in (("system", "WINDOWS/system32/config/system"),
                                  ("software", "WINDOWS/system32/config/software"))}


def boot_ini_default(golden):
    """The default entry line, read from the image, plus whether it carries /bootlog."""
    with attached(golden) as img:
        text = img.text("boot.ini")
    if text is None:
        raise Bad("%s has no boot.ini at its root, so NTLDR has nothing to boot from" % golden)
    lines = [l for l in text.splitlines() if l.strip()]
    default = next((re.match(r"\s*default\s*=\s*(.+)", l, re.I).group(1).strip().lower()
                    for l in lines if re.match(r"\s*default\s*=", l, re.I)), None)
    if default is None:
        raise Bad("boot.ini carries no default= line, so which entry boots cannot be determined")
    entries = [l for l in lines if l.split("=", 1)[0].strip().lower() == default]
    if len(entries) != 1:
        raise Bad("the default entry matched %d lines, so which one boots would be a guess"
                  % len(entries))
    return entries[0], "/bootlog" in entries[0].lower()


def letter_state(system_hive):
    """What the hive pins the driver volume to, by the rig's own reader, not a second reading."""
    r = sh("python3", RIG / "hive-pin.py", "letters", system_hive, RIG / "gpudrv.img", check=False)
    out = (r.stdout + r.stderr).strip().splitlines()
    return any("already pinned" in line for line in out), out[-1].strip() if out else "(unreadable)"


def autologon(software_hive):
    if software_hive is None:
        return None
    h = rg.open_hive(str(software_hive))
    try:
        node = rg.descend(h, "Microsoft\\Windows NT\\CurrentVersion\\Winlogon".split("\\"))
        vals = rg.value_map(h, node) if node else {}
        return {k: rg.decode(h, v) for k, v in vals.items()
                if k.startswith("auto") or k.startswith("defaultuser") or k.startswith("defaultdom")}
    finally:
        del h


def read_state(system_hive, software_hive):
    """Every settled fact that a hive file can carry, from the hives themselves."""
    h = rg.open_hive(str(system_hive))
    try:
        f = rg.facts(h)
        cs = f["control_set"]

        def value(path, name):
            node = rg.descend(h, path)
            vals = rg.value_map(h, node) if node else {}
            return None if name.lower() not in vals else rg.decode(h, vals[name.lower()])

        got = {
            "control_set": cs,
            "enum_path": f.get("enum_path"),
            "videoid": f.get("videoid"),
            "service_imagepath": value([cs, "Services", SERVICE], "ImagePath"),
            "service_start": value([cs, "Services", SERVICE], "Start"),
            "service_type": value([cs, "Services", SERVICE], "Type"),
            "errormode": value([cs, "Control", "Windows"], "ErrorMode"),
            "harderrormode": value([cs, "Control", "Windows"], "HardErrorMode"),
            "dev_drivers": f.get("dev_drivers"),
            "dev_vgacompat": f.get("dev_vgacompat"),
            "dev_service": f.get("dev_service"),
        }
    finally:
        del h
    got["letters_pinned"], got["letters_line"] = letter_state(system_hive)
    got["autologon"] = autologon(software_hive)
    return got


LABELS = [
    ("service ImagePath", "service_imagepath", "write it with ./registration_graft.py apply <hive>"),
    ("service Start", "service_start", "a Start of 3 makes the miniport a PnP-time load, not boot-start"),
    ("service Type", "service_type", "0x1 is SERVICE_KERNEL_DRIVER"),
    ("ErrorMode", "errormode", "call/0013: the value Windows reads is ErrorMode, not HardErrorMode"),
    ("per-adapter InstalledDisplayDrivers", "dev_drivers", "./registration_graft.py apply <hive>"),
    ("per-adapter VgaCompatible", "dev_vgacompat", "1 leaves the adapter treated as VGA"),
    ("per-adapter Video Service", "dev_service", "./registration_graft.py apply <hive>"),
    ("drive letter pinned", "letters_pinned", "./hive-pin.py letters <hive> <channel> reads it; "
                                               "./hive-pin.py write pins it"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True, type=Path)
    ap.add_argument("--record", action="store_true", help="merge the reading into the golden's manifest")
    a = ap.parse_args()
    if not a.golden.is_file():
        print("golden-state: no such golden: %s" % a.golden, file=sys.stderr)
        return 2
    a_quiet_host()
    require_tools()

    entry, has_bootlog = boot_ini_default(a.golden)
    with tempfile.TemporaryDirectory(dir="/tmp/opencode") as tmp:
        copies = copy_hives(a.golden, Path(tmp))
        if copies["system"] is None:
            print("golden-state: %s has no WINDOWS/system32/config/system, so it is not a system "
                  "volume NTLDR can boot" % a.golden, file=sys.stderr)
            return 1
        got = read_state(copies["system"], copies["software"])

    print("golden %s" % a.golden)
    bad = []
    for label, key, remedy in LABELS:
        have, want = got[key], WANT[key]
        ok = have == want
        print("  %-7s %-34s %s%s" % ("ok" if ok else "FAIL", label, have,
                                     "" if ok else "  (want %s)" % want))
        if not ok:
            bad.append("%s is %s; %s" % (label, have, remedy))
    if not ok and label == "drive letter pinned":
        print("          %-34s %s" % ("", got["letters_line"]))

    auto = got["autologon"]
    if auto is None:
        print("  %-7s %-34s %s" % ("FAIL", "autologon", "no SOFTWARE hive in the image"))
        bad.append("autologon is unreadable; the image has no SOFTWARE hive")
    else:
        for label, key, want in (("autologon enabled", "autoadminlogon", "1"),
                                 ("autologon names a user", "defaultusername", None)):
            have = auto.get(key)
            good = (have is not None and str(have) != "") if want is None else str(have) == want
            print("  %-7s %-34s %s%s" % ("ok" if good else "FAIL", label, have,
                                         "" if good else "  (want %s)" % (want or "a name")))
            if not good:
                bad.append("%s is %s; AutoAdminLogon and DefaultUserName live in the SOFTWARE hive's "
                           "Winlogon key" % (label, have))
    print("  %-7s %-34s %s" % ("ok", "ControlSet", got["control_set"]))
    print("  %-7s %-34s %s" % ("ok", "enum path", got["enum_path"]))
    print("  %-7s %-34s %s" % ("ok" if has_bootlog else "MEASURED", "boot.ini /bootlog",
                               "yes" if has_bootlog else
                               "no, and that is deliberate: the runner sets it per run so the golden "
                               "stays a byte-exact conversion of the disk"))
    print("          %-34s %s" % ("default entry", entry))

    if bad:
        print("verdict: FAIL")
        for line in bad:
            print("  %s" % line)
        print("  remedy: the settings are written offline by ./offline-guest-settings.sh, "
              "./hive-pin.py and ./registration_graft.py, and witnessed by a boot through "
              "./run-experiment.sh")
        return 1
    print("verdict: PASS (a run on this golden starts from the settled state)")

    if a.record:
        manifest = a.golden.with_suffix(".json")
        if not manifest.is_file():
            print("golden-state: --record needs the manifest beside the golden and %s has none"
                  % manifest, file=sys.stderr)
            return 2
        record = json.loads(manifest.read_text())
        record["settled"] = dict(got, bootlog_in_golden=has_bootlog, boot_ini_entry=entry)
        record["settled_recorded"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        temporary = manifest.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
        temporary.replace(manifest)
        print("recorded the settled state in %s" % manifest)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Bad, rg.Bad, OSError, subprocess.SubprocessError) as exc:
        print("golden-state: %s" % exc, file=sys.stderr)
        sys.exit(2)
