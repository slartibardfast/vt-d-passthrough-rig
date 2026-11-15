#!/usr/bin/env python3
"""Set what a boot-time image must carry, and prove each setting from a second mount.

Why this file exists. A control run has to say which driver the loader loaded, and the one
artifact that records that without the rig's cooperation is `ntbtlog.txt`, which Windows only
writes when the boot entry carries `/bootlog`. The same argument applies to the in-guest
captures: the volatile video device map, the live devnode answer and the session's mode exist
only while the guest runs, so the guest has to write them, which means the Startup trigger that
writes them has to be inside the image that boots.

Why it is aimed at an image rather than at the OS disk. `plan/0007#baseline-load` orders the disk
writes first and these settings after them, so they belong to the disposable overlay each run
boots. The golden stays the byte-exact copy of the OS disk its manifest describes, and every
mutable input to a run is something the runner installs per run and can name a hash for. Baking
the trigger into shared read-only state is precisely the mistake that produced a control run with
two captures missing: the golden carried a November-14 copy of the hook.

Every action is proved by re-reading after a remount, not by trusting the write. `boot.ini`
arrives read-only and hidden, and a file written by root into a root-owned mount is not something
a permissive success code describes honestly.

Usage: golden-settings.py --image <qcow2> [--bootlog] [--startup-hook <file>] [--runid <text>]
                          [--check] [--record <dir>]
"""
import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

RIG = Path(__file__).resolve().parent
NBD = os.environ.get("SETTINGS_NBD", "/dev/nbd0")
MOUNT = "/tmp/opencode/golden-settings-mnt"
STARTUP_DIR = "Documents and Settings/All Users/Start Menu/Programs/Startup"
HOOK_TARGET = STARTUP_DIR + "/letters.bat"
RUNID_FILE = "WINDOWS/runid.txt"


class Bad(Exception):
    pass


def sh(*cmd, check=True, input=None):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, input=input)
    if check and r.returncode != 0:
        raise Bad("%s failed: %s" % (" ".join(cmd), (r.stdout + r.stderr).strip() or "no output"))
    return r


def a_quiet_host():
    r = subprocess.run([str(RIG / "guest-running.sh"), "check"], capture_output=True)
    if r.returncode == 0:
        raise Bad("a QEMU is running: its cache holds writes this image does not. End the guest first.")


def require_tools():
    for tool in ("qemu-img", "qemu-nbd", "mount", "umount"):
        if not shutil.which(tool):
            pkg = "qemu-utils" if tool.startswith("qemu") else "util-linux"
            raise Bad("%s is required; on Ubuntu and Debian it comes from the package %s" % (tool, pkg))


def first_partition():
    """The system partition of the attached image, whichever number the layout gives it."""
    parts = sorted(p for p in Path("/dev").glob("nbd0p*"))
    if not parts:
        raise Bad("%s appeared with no partition; this image has nothing to set" % NBD)
    return parts[0]


def attach(image):
    out = subprocess.run(["qemu-img", "info", "--output=json", str(image)],
                         capture_output=True, text=True).stdout
    if '"format"' not in out:
        raise Bad("qemu-img cannot read %s" % image)
    sh("sudo", "modprobe", "nbd", "max_part=8")
    if Path(NBD).exists():
        holders = sh("sudo", "fuser", "-v", NBD, check=False)
        if holders.stdout.strip() or holders.stderr.strip():
            raise Bad("%s is in use; disconnect it with `sudo qemu-nbd -d %s` first" % (NBD, NBD))
    sh("sudo", "qemu-nbd", "-f", "qcow2", "-c", NBD, str(image))
    for _ in range(40):
        try:
            first_partition()
            return
        except Bad:
            time.sleep(0.5)
    sh("sudo", "qemu-nbd", "-d", NBD, check=False)
    raise Bad("%s appeared with no partition; this image has nothing to set" % NBD)


def detach():
    sh("sudo", "qemu-nbd", "-d", NBD, check=False)


def mount(rw):
    sh("sudo", "mkdir", "-p", MOUNT)
    sh("sudo", "umount", MOUNT, check=False)
    sh("sudo", "mount", "-o", "rw" if rw else "ro", str(first_partition()), MOUNT)


def umount():
    sh("sudo", "umount", MOUNT, check=False)


def find(mount, rel):
    """Locate a path whatever case the volume stores it in, and return the real path."""
    parts = rel.split("/")
    here = Path(mount)
    for part in parts:
        if not here.exists():
            return None
        if (here / part).exists():
            here = here / part
            continue
        try:
            match = [c for c in here.iterdir() if c.name.lower() == part.lower()]
        except PermissionError:
            match = []
        if len(match) != 1:
            return None
        here = match[0]
    return here


def read(path):
    try:
        return path.read_bytes()
    except PermissionError:
        # A root-owned mount can hold a file the calling user may not open. The read still has to
        # be the file's bytes, not a guess about them.
        return sh("sudo", "cat", str(path)).stdout.encode("latin-1", "replace")


def write(path, data, keep_mode=True):
    """Write through sudo, since the mount and everything on it belong to root."""
    mode = stat.S_IMODE(os.stat(str(path)).st_mode) if path.exists() else 0o644
    sh("sudo", "chmod", "u+w", str(path), check=False)
    sh("sudo", "dd", "of=%s" % path, "bs=1M", input=data.decode("latin-1"), check=True)
    sh("sudo", "chmod", "%o" % (mode if keep_mode else (mode | 0o600)), str(path))


def entry_lines(text):
    """The [operating systems] section, verbatim, in order."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip().lower() == "[operating systems]")
    except StopIteration:
        raise Bad("boot.ini has no [operating systems] section")
    return [l for l in lines[start + 1:] if l.strip() and not l.strip().startswith("[")]


def default_path(text):
    for line in text.splitlines():
        m = re.match(r"\s*default\s*=\s*(.+?)\s*$", line, re.I)
        if m:
            return m.group(1).lower()
    raise Bad("boot.ini has no default= line, so which entry to set cannot be determined")


def read_boot_ini(mount):
    path = find(mount, "boot.ini")
    if path is None:
        raise Bad("no boot.ini at the root of %s; this is not a system volume NTLDR reads" % mount)
    return path, read(path).decode("latin-1")


def record(record_dir, name, data):
    if not record_dir:
        return
    Path(record_dir).mkdir(parents=True, exist_ok=True)
    (Path(record_dir) / name).write_bytes(data)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--bootlog", action="store_true")
    ap.add_argument("--startup-hook", type=Path, default=None)
    ap.add_argument("--runid", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", type=Path, default=None)
    a = ap.parse_args()
    if not a.image.is_file():
        raise Bad("no such image: %s" % a.image)
    actions = [x for x in (a.bootlog, a.startup_hook, a.runid) if x]
    if not actions:
        # A bare --check would report nothing at all and exit 0, which reads as a clean answer to a
        # question nobody asked. Naming the setting is the question.
        raise Bad("say which setting: --bootlog, --startup-hook <file>, or --runid <text> "
                  "(add --check to read it back without writing)")
    if a.startup_hook and not a.startup_hook.is_file():
        raise Bad("no such startup hook template: %s" % a.startup_hook)
    if a.runid and not re.fullmatch(r"[A-Za-z0-9._-]{4,64}", a.runid):
        raise Bad("the run id must be a plain token of letters, digits, dot, dash or underscore")
    a_quiet_host()
    require_tools()

    attach(a.image)
    try:
        mount(rw=(not a.check))
        expected = {}
        missing = []

        if a.bootlog:
            path, text = read_boot_ini(MOUNT)
            record(a.record, "boot.ini.before", text.encode("latin-1"))
            entries = entry_lines(text)
            want = default_path(text)
            hits = [e for e in entries if e.split("=", 1)[0].strip().lower() == want]
            if len(hits) != 1:
                raise Bad("the default entry matched %d lines; refusing to guess which one boots"
                          % len(hits))
            if a.check:
                report("check", entries)
                present = "/bootlog" in hits[0].lower()
                print("bootlog on the default entry: %s" % ("yes" if present else "no"))
                if not present:
                    missing.append("bootlog")
            elif "/bootlog" in hits[0].lower():
                report("already set", entries)
            else:
                updated = text.replace(hits[0], hits[0].rstrip() + " /bootlog", 1)
                write(path, updated.encode("latin-1"))
                expected["boot.ini"] = ("in", "/bootlog", None)

        if a.startup_hook:
            want_hook = a.startup_hook.read_bytes()
            path = find(MOUNT, HOOK_TARGET)
            if a.check:
                if path is None:
                    raise Bad("no startup trigger at %s" % HOOK_TARGET)
                have = read(path)
                same = sha(have) == sha(want_hook)
                print("startup-hook is %s (%s)" % ("the tracked template" if same
                                                   else "NOT the tracked template", sha(have)[:12]))
                if not same:
                    missing.append("startup-hook")
            elif path is not None and sha(read(path)) == sha(want_hook):
                print("already set startup-hook: %s" % sha(want_hook)[:12])
            else:
                if path is not None:
                    record(a.record, "letters.bat.before", read(path))
                    write(path, want_hook)
                else:
                    target = find(MOUNT, STARTUP_DIR)
                    if target is None:
                        raise Bad("no Startup folder at %s; refusing to invent one" % STARTUP_DIR)
                    sh("sudo", "dd", "of=%s/letters.bat" % target, input=want_hook.decode("latin-1"))
                print("set startup-hook: %s" % sha(want_hook)[:12])
                expected["startup-hook"] = ("sha", sha(want_hook), None)

        if a.runid:
            path = find(MOUNT, RUNID_FILE)
            data = (a.runid + "\r\n").encode("latin-1")
            if a.check:
                if path is None:
                    print("runid: absent")
                    missing.append("runid")
                else:
                    here = read(path) == data
                    print("runid is %s" % ("this run" if here else "not this run"))
                    if not here:
                        missing.append("runid")
            elif path is None:
                # This file is the rig's own, not Windows': the guest reads it to learn which run
                # it is in, so creating it is the setting, not an invention.
                windows = find(MOUNT, "WINDOWS")
                if windows is None:
                    raise Bad("no WINDOWS directory on the volume")
                created = windows / "runid.txt"
                sh("sudo", "dd", "of=%s" % created, input=data.decode("latin-1"))
                sh("sudo", "chmod", "644", str(created), check=False)
                print("set runid: %s" % a.runid)
                expected["runid"] = ("eq", data, None)
            elif read(path) != data:
                write(path, data)
                print("set runid: %s" % a.runid)
                expected["runid"] = ("eq", data, None)
            else:
                print("already set runid: %s" % a.runid)
        umount()
        if a.check:
            if missing:
                print("not set: %s" % ", ".join(missing))
                return 1
            return 0
        if not expected:
            return 0

        # The proof: a second mount, read-only, by a process that never held the write.
        mount(rw=False)
        bad = []
        for name, (kind, want, context) in expected.items():
            if name == "boot.ini":
                _, text = read_boot_ini(MOUNT)
                entries = entry_lines(text)
                line = next((e for e in entries
                             if e.split("=", 1)[0].strip().lower() == default_path(text)), None)
                if line is None or want not in line.lower():
                    bad.append("boot.ini does not carry %s after the write" % want)
                report("read back", entries)
                record(a.record, "boot.ini.after", text.encode("latin-1"))
            elif name == "startup-hook":
                have = sha(read(find(MOUNT, HOOK_TARGET)))
                if have != want:
                    bad.append("the startup trigger hashes to %s, not the template's %s"
                               % (have[:12], want[:12]))
                else:
                    print("read back startup-hook: %s" % have[:12])
                    record(a.record, "letters.bat.after", read(find(MOUNT, HOOK_TARGET)))
            elif name == "runid":
                rp = find(MOUNT, RUNID_FILE)
                if rp is None:
                    bad.append("runid.txt is absent after the write")
                    continue
                have = read(rp)
                if have != want:
                    bad.append("runid.txt does not read back as the run it was set to")
                else:
                    print("read back runid: %s" % have.decode("latin-1").strip())
        if bad:
            raise Bad("; ".join(bad))
        return 0
    finally:
        umount()
        detach()


def report(kind, entries):
    for e in entries:
        print("%s entry: %s" % (kind, e))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Bad, OSError, subprocess.SubprocessError) as exc:
        # A refused chmod and a refused edit are both foreseeable here, and a reader needs the
        # sentence, not a traceback.
        print("golden-settings: %s" % exc, file=sys.stderr)
        sys.exit(2)
