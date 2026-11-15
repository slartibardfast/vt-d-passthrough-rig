#!/usr/bin/env python3
"""Set what a boot-time image must carry, and prove it from the file afterwards.

Why this exists. The control run has to say which driver the loader loaded, and the one
artifact that records that without the rig's cooperation is `ntbtlog.txt`, which Windows
only writes when the boot entry carries `/bootlog`. The load fact then lands on the run's
overlay, where the host reads it after the guest ends the run, instead of depending on the
captor being alive at the right moment.

Why it is aimed at an image rather than at the disk. `plan/0007#baseline-load` orders the
disk writes first and the `/bootlog` step after them, so the flag is not an OS-disk change:
it is applied to the disposable overlay that the run boots, and the golden stays the
byte-exact copy of the OS disk its manifest says it is. The same reader serves a golden if a
later milestone wants the flag baked once.

The edit is proved by re-reading the file after a remount, not by trusting the write: a
Windows `boot.ini` arrives read-only and hidden, so a silent failure looks exactly like a
successful edit.

Usage: golden-settings.py --image <qcow2> [--bootlog] [--check] [--record <dir>]
"""
import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import time

from pathlib import Path

RIG = Path(__file__).resolve().parent
NBD = "/dev/nbd0"
MOUNT = "/tmp/opencode/golden-settings-mnt"


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


def attach(image):
    fmt = subprocess.run(["qemu-img", "info", "--output=json", str(image)],
                         capture_output=True, text=True).stdout
    if '"format"' not in fmt:
        raise Bad("qemu-img cannot read %s" % image)
    sh("sudo", "modprobe", "nbd", "max_part=8")
    if os.path.exists(NBD):
        holders = sh("sudo", "fuser", "-v", NBD, check=False)
        if holders.stdout.strip() or holders.stderr.strip():
            raise Bad("%s is in use; disconnect it with `sudo qemu-nbd -d %s` first" % (NBD, NBD))
    sh("sudo", "qemu-nbd", "-c", NBD, str(image))
    for _ in range(40):
        if Path(NBD + "p1").exists():
            return
        time.sleep(0.5)
    sh("sudo", "qemu-nbd", "-d", NBD, check=False)
    raise Bad("%s appeared with no first partition; this image has nothing to set" % NBD)


def detach():
    sh("sudo", "qemu-nbd", "-d", NBD, check=False)


def entry_lines(text):
    """The [operating systems] section, verbatim, in order."""
    lines = text.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip().lower() == "[operating systems]")
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
    """Locate boot.ini whatever case it is stored in, and read it exactly."""
    for name in sorted(os.listdir(mount)):
        if name.lower() == "boot.ini":
            path = os.path.join(mount, name)
            try:
                raw = Path(path).read_bytes()
            except PermissionError:
                # A root-owned mount can hold a file the calling user may not open. The read
                # still has to be the file's bytes, not a guess about them.
                raw = sh("sudo", "cat", path).stdout.encode("latin-1", "replace")
            return name, raw.decode("latin-1")
    raise Bad("no boot.ini at the root of %s; this is not a system volume NTLDR reads" % mount)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--bootlog", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", type=Path, default=None)
    a = ap.parse_args()
    if not a.image.is_file():
        raise Bad("no such image: %s" % a.image)
    if not (a.bootlog or a.check):
        raise Bad("say what to set: --bootlog, or --check to read it back without writing")
    a_quiet_host()
    require_tools()
    if a.record:
        a.record.mkdir(parents=True, exist_ok=True)

    attach(a.image)
    try:
        sh("sudo", "mkdir", "-p", MOUNT)
        write = a.bootlog and not a.check
        sh("sudo", "mount", "-o", "ro" if not write else "rw", NBD + "p1", MOUNT)
        name, text = read_boot_ini(MOUNT)
        if a.record:
            (a.record / "boot.ini.before").write_text(text, encoding="latin-1")
        entries = entry_lines(text)
        want = default_path(text)
        hits = [e for e in entries if e.split("=", 1)[0].strip().lower() == want]
        if len(hits) != 1:
            raise Bad("the default entry matched %d lines; refusing to guess which one boots" % len(hits))
        if not write:
            report("check", entries)
            ok = "/bootlog" in hits[0].lower()
            print("bootlog on the default entry: %s" % ("yes" if ok else "no"))
            return 0 if ok else 1

        if "/bootlog" in hits[0].lower():
            report("already set", entries)
            return 0
        # The mount belongs to root and so does boot.ini, so the edit goes through sudo like
        # every other write in the rig. A chmod by the calling user on a root mount is refused,
        # which is what the first version of this script crashed on.
        target = os.path.join(MOUNT, name)
        mode = stat.S_IMODE(os.stat(target).st_mode)
        new_entry = hits[0].rstrip() + " /bootlog"
        updated = text.replace(hits[0], new_entry, 1)
        sh("sudo", "chmod", "u+w", target)
        sh("sudo", "tee", target, input=updated)
        sh("sudo", "chmod", "%o" % mode, target)
        sh("sync")
        sh("sudo", "umount", MOUNT)

        # The proof: a second mount, read-only, by a process that never held the write.
        sh("sudo", "mount", "-o", "ro", NBD + "p1", MOUNT)
        _, again = read_boot_ini(MOUNT)
        report("read back", entry_lines(again))
        if "/bootlog" not in default_line(again, want).lower():
            raise Bad("boot.ini does not carry /bootlog after the write; the edit did not land")
        if a.record:
            (a.record / "boot.ini.after").write_text(again, encoding="latin-1")
        return 0
    finally:
        sh("sudo", "umount", MOUNT, check=False)
        detach()


def default_line(text, want):
    for e in entry_lines(text):
        if e.split("=", 1)[0].strip().lower() == want:
            return e
    raise Bad("the default entry vanished from boot.ini")


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
