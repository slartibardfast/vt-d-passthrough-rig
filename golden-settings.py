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
import json
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
    # The retry count is an environment knob for one reason: a test has to reach the refusal without
    # waiting ten seconds for it. The default is what a real run uses.

    # `qemu-nbd -d` returns before the kernel has finished releasing the device, so the next
    # `qemu-img info` on the same file can fail for a few hundred milliseconds. The first time that
    # showed up was in a lane, on a check that ran immediately after a write, where it surfaced as
    # "qemu-img cannot read". Waiting is correct here and a bounded wait still fails loudly on an
    # image that is genuinely unreadable.
    tries = int(os.environ.get("GOLDEN_SETTINGS_ATTACH_TRIES", "40"))
    for attempt in range(tries):
        r = subprocess.run(["qemu-img", "info", "--output=json", str(image)],
                           capture_output=True, text=True)
        try:
            facts = json.loads(r.stdout)
        except json.JSONDecodeError:
            facts = None
        if facts and "format" in facts:
            break
        if attempt == 0:
            first = (r.stderr or r.stdout).strip()[:160]
        time.sleep(0.25)
    else:
        raise Bad("qemu-img cannot read %s after %d tries; the first error said: %s"
                  % (image, tries, first or "no output"))
    # A raw volume reads perfectly well and then fails inside qemu-nbd with a message about formats, so
    # the format is named here, where the remedy is knowable: an overlay has to back onto a qcow2.
    if facts["format"] != "qcow2":
        raise Bad("%s is a %s image, and a qcow2 overlay cannot back onto one; convert it with "
                  "`qemu-img convert -O qcow2 %s %s.qcow2`"
                  % (image, facts["format"], image, image))
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


# Where a user-mode display driver must sit. The registry names a module by name, and win32k
# resolves that name against system32, so a selection with no file there is a name pointing at
# nothing. The casing of the directory on disk is whatever the volume stores; find() settles it.
SYSTEM32 = "WINDOWS/system32"
SYSTEM_HIVE = "WINDOWS/system32/config/system"
DLLCACHE = "WINDOWS/system32/dllcache"
DLL_NAME_RE = re.compile(r"[A-Za-z0-9_]+\.(dll|DLL)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--bootlog", action="store_true")
    ap.add_argument("--startup-hook", type=Path, default=None)
    ap.add_argument("--runid", default=None)
    ap.add_argument("--display-dll", type=Path, default=None,
                    help="copy a driver's user-mode DLL into system32, the file the registry names "
                         "and no install step ever placed")
    ap.add_argument("--class-binding", type=Path, default=None, metavar="INF",
                    help="rebind the device's Display class node to the values in this INF, every one "
                         "of them read from the file rather than typed in")
    ap.add_argument("--display-mode", default=None, metavar="WxHxB@R",
                    help="the mode the attached display opens its desktop at, for example "
                         "1024x768x32@60; written to the hardware profile, which is where the mode a "
                         "desktop opens at lives")
    ap.add_argument("--display-inf", type=Path, default=None, metavar="INF",
                    help="the INF whose CopyFiles list names the package's files; defaults to the "
                         "--class-binding INF, and is separate so a package can be placed without "
                         "also rebinding the class node in the same boot")
    ap.add_argument("--display-package", type=Path, default=None, metavar="DIR",
                    help="place the user-mode files the INF's CopyFiles groups name, reading the list "
                         "from the INF given to --class-binding; a display install that copies only "
                         "the display DLL gives a driver that loads and declines")
    ap.add_argument("--class-key", default=None, metavar="NNNN",
                    help="the Display class subkey to write; without it the binding goes to a fresh "
                         "node, and with it the node the device already uses is rewritten in place")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--record", type=Path, default=None)
    a = ap.parse_args()
    if not a.image.is_file():
        raise Bad("no such image: %s" % a.image)
    actions = [x for x in (a.bootlog, a.startup_hook, a.runid, a.display_dll, a.class_binding,
                           a.class_key, a.display_mode, a.display_package, a.display_inf) if x]
    if not actions:
        # A bare --check would report nothing at all and exit 0, which reads as a clean answer to a
        # question nobody asked. Naming the setting is the question.
        raise Bad("say which setting: --bootlog, --startup-hook <file>, --runid <text>, "
                  "--display-dll <file>, --class-binding <inf>, or --display-mode WxHxB@R (add "
                  "--check to read it back without writing)")
    if a.startup_hook and not a.startup_hook.is_file():
        raise Bad("no such startup hook template: %s" % a.startup_hook)
    if a.display_dll:
        if not a.display_dll.is_file():
            raise Bad("no such display DLL to install: %s" % a.display_dll)
        if not DLL_NAME_RE.fullmatch(a.display_dll.name):
            raise Bad("the display DLL must be named like a driver DLL, got %s" % a.display_dll.name)
    if a.class_binding and not a.class_binding.is_file():
        raise Bad("no INF to bind the class node from: %s" % a.class_binding)
    if a.display_package:
        if not a.display_package.is_dir():
            raise Bad("no package directory at %s; it must hold the files the INF names" % a.display_package)
        if not (a.display_inf or a.class_binding):
            raise Bad("--display-package needs --display-inf <inf> (or --class-binding, which implies "
                      "it): the INF is where the list of files and their destinations is written, "
                      "and guessing it is how a partial install happens")
    if a.display_inf and not a.display_inf.is_file():
        raise Bad("no INF to read the package list from: %s" % a.display_inf)
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

        if a.display_dll:
            want = sha(a.display_dll.read_bytes())
            system32 = find(MOUNT, SYSTEM32)
            if system32 is None:
                raise Bad("no %s on the volume, so the display DLL has nowhere to go and a name in "
                          "the registry resolves to nothing" % SYSTEM32)
            dest = system32 / a.display_dll.name
            have = sha(read(dest)) if dest.exists() else None
            if a.check:
                print("display-dll %s is %s" % (a.display_dll.name,
                                                "present (%s)" % have[:12] if have == want else
                                                ("absent" if have is None else
                                                 "present but %s, not %s" % (have[:12], want[:12]))))
                if have != want:
                    missing.append("display-dll")
            elif have == want:
                print("already set display-dll: %s (%s)" % (a.display_dll.name, want[:12]))
            else:
                if have is not None:
                    record(a.record, "%s.before" % a.display_dll.name, read(dest))
                # The bytes go through cp, not the text-mode write() helper. A PE image is not a
                # latin-1 string, and a copy that round-trips it through a decoder produces a file
                # that is the right size and wrong in every byte, which a boot reports as a bad driver.
                sh("sudo", "cp", str(a.display_dll), str(dest))
                sh("sudo", "chmod", "755", str(dest), check=False)
                sh("sync")
                cache = find(MOUNT, DLLCACHE)
                if cache is not None and not (cache / a.display_dll.name).exists():
                    sh("sudo", "cp", str(a.display_dll), str(cache / a.display_dll.name))
                    sh("sudo", "chmod", "755", str(cache / a.display_dll.name), check=False)
                    print("also placed a copy in dllcache, so Windows File Protection has the same "
                          "bytes to restore from")
                print("set display-dll: %s (%s, %d bytes)"
                      % (a.display_dll.name, want[:12], a.display_dll.stat().st_size))
                expected["display-dll"] = ("sha", want, a.display_dll.name)
        if a.class_binding:
            hive = find(MOUNT, SYSTEM_HIVE)
            if hive is None:
                raise Bad("no %s on the volume, so there is no class node to bind and the boot would "
                          "report success while changing nothing" % SYSTEM_HIVE)
            grade = [sys.executable, str(RIG / "registration_graft.py"), "classnode", str(hive),
                     "--inf", str(a.class_binding)]
            if a.class_key:
                if not (a.class_key.isdigit() and len(a.class_key) == 4):
                    raise Bad("--class-key must be four digits, for example 0001")
                grade += ["--class-key", a.class_key]
            if a.check:
                r = subprocess.run([str(c) for c in ["sudo"] + grade + ["--check"]],
                                   capture_output=True, text=True)
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "verdict" in l]
                print("class-binding: %s" % (tail[-1] if tail else (r.stdout + r.stderr).strip()[:120]))
                if r.returncode != 0:
                    missing.append("class-binding")
            else:
                r = subprocess.run([str(c) for c in ["sudo"] + grade + ["--apply", "--record",
                                                                        str(Path(a.record or MOUNT)
                                                                            / "classnode-before.json")]],
                                   capture_output=True, text=True)
                keep = (" from:", "read back", "InfPath", "InfSection", "Driver  ")
                sys.stdout.write("".join(l + "\n" for l in
                                         (r.stdout + r.stderr).splitlines()
                                         if any(k in l for k in keep)))
                if r.returncode != 0:
                    raise Bad("the class-node binding failed: %s"
                              % ((r.stdout + r.stderr).strip()[:400] or "no output"))
                expected["class-binding"] = ("bound", str(a.class_binding), None)

        if a.display_package:
            hive_inf = a.display_inf or a.class_binding
            call = [sys.executable, str(RIG / "registration_graft.py"), "package", str(MOUNT),
                    "--inf", str(hive_inf), "--package", str(a.display_package)]
            if not a.check:
                call.append("--apply")
            r = subprocess.run([str(c) for c in ["sudo"] + call], capture_output=True, text=True)
            keep = ("wants", "->", "read back", "skipped", "MISSING")
            sys.stdout.write("".join(l + "\n" for l in (r.stdout + r.stderr).splitlines()
                                     if any(k in l for k in keep)))
            if r.returncode != 0:
                raise Bad("the display package was not placed: %s"
                          % ((r.stdout + r.stderr).strip()[:400] or "no output"))
            expected["display-package"] = ("package", str(a.display_package), None)

        if a.display_mode:
            hive = find(MOUNT, SYSTEM_HIVE)
            if hive is None:
                raise Bad("no %s on the volume, so there is no profile slot to set" % SYSTEM_HIVE)
            call = [sys.executable, str(RIG / "registration_graft.py"), "display-mode", str(hive),
                    "--mode", a.display_mode]
            if a.check:
                r = subprocess.run([str(c) for c in ["sudo"] + call + ["--check"]],
                                   capture_output=True, text=True)
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "verdict" in l]
                print("display-mode: %s" % (tail[-1] if tail else (r.stdout + r.stderr).strip()[:120]))
                if r.returncode != 0:
                    missing.append("display-mode")
            else:
                r = subprocess.run([str(c) for c in ["sudo"] + call + ["--apply"]],
                                   capture_output=True, text=True)
                keep = ("reads now:", "read back", "verdict", "video slot", "profile:")
                sys.stdout.write("".join(l + "\n" for l in (r.stdout + r.stderr).splitlines()
                                         if any(k in l for k in keep)))
                if r.returncode != 0:
                    raise Bad("the display mode was not set: %s"
                              % ((r.stdout + r.stderr).strip()[:400] or "no output"))
                expected["display-mode"] = ("mode", a.display_mode, None)
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
            elif name == "display-dll":
                placed = find(MOUNT, "%s/%s" % (SYSTEM32, context))
                if placed is None:
                    bad.append("the display DLL is not on the volume after the copy")
                else:
                    have = sha(read(placed))
                    if have != want:
                        bad.append("system32 holds %s, not the copied file's %s"
                                   % (have[:12], want[:12]))
                    else:
                        print("read back display-dll: %s (%s)" % (placed.name, have[:12]))
            elif name == "class-binding":
                hive = find(MOUNT, SYSTEM_HIVE)
                again = [sys.executable, str(RIG / "registration_graft.py"), "classnode",
                         str(hive), "--inf", want, "--check"]
                if a.class_key:
                    again += ["--class-key", a.class_key]
                r = subprocess.run([str(c) for c in ["sudo"] + again],
                                   capture_output=True, text=True)
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "verdict" in l]
                if r.returncode != 0:
                    bad.append("the class node is not bound after the write: %s"
                               % (tail[-1] if tail else (r.stdout + r.stderr).strip()[:160]))
                else:
                    print("read back class-binding: %s" % (tail[-1] if tail else "BOUND"))
            elif name == "display-package":
                call = [sys.executable, str(RIG / "registration_graft.py"), "package", str(MOUNT),
                        "--inf", str(a.display_inf or a.class_binding),
                        "--package", str(a.display_package)]
                r = subprocess.run([str(c) for c in ["sudo"] + call], capture_output=True, text=True)
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "read back" in l]
                if r.returncode != 0:
                    bad.append("the display package did not read back: %s"
                               % (tail[-1] if tail else (r.stdout + r.stderr).strip()[:160]))
                else:
                    print("read back display-package: %s" % (tail[-1] if tail else "placed"))
            elif name == "display-mode":
                hive = find(MOUNT, SYSTEM_HIVE)
                r = subprocess.run([str(c) for c in ["sudo", sys.executable,
                                                     str(RIG / "registration_graft.py"),
                                                     "display-mode", str(hive),
                                                     "--mode", want, "--check"]],
                                   capture_output=True, text=True)
                tail = [l for l in (r.stdout + r.stderr).splitlines() if "verdict" in l]
                if r.returncode != 0:
                    bad.append("the profile mode is not %s after the write: %s"
                               % (want, tail[-1] if tail else (r.stdout + r.stderr).strip()[:160]))
                else:
                    print("read back display-mode: %s" % (tail[-1] if tail else "MODE-SET"))
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
