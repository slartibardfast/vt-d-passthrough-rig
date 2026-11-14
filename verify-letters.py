#!/usr/bin/env python3
"""Read the letter witness a boot left behind and say, in one line, whether the
driver volume is where the rig's scripts think it is.

The witness is `install-channel/letters.bat` running at logon. It is the only
machine-readable account of what the guest's drive letters actually are: the
letter Windows gives a volume depends on what else is attached, and this rig has
been wrong about it twice - a hard-coded B: in October, and D: stolen by the
CSMWrap partition on 2026-11-14.

A witness is a set of records and one of three shapes, because the survey has
been rewritten as each shape stalled on the KVM's empty removable:

  caption blocks   Caption=D: / VolumeName=... / VolumeSerialNumber=...   (wmic)
  survey blocks    "== d: ==" then a vol header and, for opened letters, names
  marker lines     driver-marker ... found on d:  or  driver-marker d:\path

The volume is identified by what it carries, not by its letter: both driver
markers (`drv` and `B175198`) must be present on exactly one letter, and that
letter must be the one the caller expects.

The verifier takes a directory of files rather than opening disk images, so it
runs anywhere, including under a test with a fixture witness. Fetching the files
is the caller's job (a read-only mount of the driver volume).

Usage: verify-letters.py <witness-dir> [--letter D]
Exit: 0 the expected letter is the driver volume; 1 it is not, or the witness is
incomplete; 2 there is no witness to read.
"""
import argparse
import os
import re
import sys

# What makes a volume the driver volume, independent of its letter: the patched
# payload and the driver package it was installed from.
MARKERS = ("drv", "b175198")

SURVEY_HEAD = re.compile(r"^==\s*([A-Za-z]):\s*==\s*$")
CAPTION = re.compile(r"^Caption=([A-Za-z]):\s*$")
FIELD = re.compile(r"^(VolumeName|VolumeSerialNumber|Description|VolumeName)\s*=\s*(.*)$")
SERIAL = re.compile(r"Volume Serial Number is\s+(\S+)", re.I)
LABEL = re.compile(r"Volume in drive [A-Za-z] (?:is\s+(.*)|has no label)", re.I)
MARKER_ON = re.compile(r"driver-marker\s+(\S+)\s+(?:found )?on\s+([A-Za-z]):", re.I)
MARKER_PATH = re.compile(r"driver-marker\s+([A-Za-z]):\\(\S+)", re.I)


def decode(raw):
    """cmd writes ANSI; wmic and fsutil write UTF-16 with a byte-order mark.

    Sniff the mark rather than trying UTF-16 first: an even-length ANSI buffer
    decodes as UTF-16 without raising and comes back as unmappable glyphs, which
    reads as "no volume matched" instead of as a broken parse.
    """
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", "replace")
    return raw.decode("cp1252", "replace")


def blank():
    return {"label": None, "serial": None, "dirs": [], "markers": [], "kind": None}


def parse(text):
    volumes = {}
    done = False
    letter = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.strip()
        if line == "letters-done":
            done = True
            continue
        if line.startswith("letters-failed") or line == "wmic-failed":
            return volumes, done, line

        m = SURVEY_HEAD.match(line)
        if m:
            letter = m.group(1).upper()
            volumes.setdefault(letter, blank())["kind"] = "survey"
            continue
        m = CAPTION.match(line)
        if m:
            letter = m.group(1).upper()
            volumes.setdefault(letter, blank())["kind"] = "caption"
            continue

        m = MARKER_ON.search(line)
        if m:
            volumes.setdefault(m.group(2).upper(), blank())["markers"].append(m.group(1).lower())
            continue
        m = MARKER_PATH.search(line)
        if m:
            volumes.setdefault(m.group(1).upper(), blank())["markers"].append(m.group(2).lower())
            continue

        if letter is None:
            continue
        info = volumes[letter]

        m = FIELD.match(line)
        if m:
            name, value = m.group(1), m.group(2).strip()
            if name == "VolumeName" and value:
                info["label"] = value
            elif name == "VolumeSerialNumber" and value:
                info["serial"] = value
            elif name == "Description" and value:
                info["description"] = value
            continue

        m = LABEL.search(line)
        if m:
            info["label"] = (m.group(1) or "no label").strip()
            continue
        m = SERIAL.search(line)
        if m:
            info["serial"] = m.group(1)
            continue
        if line.startswith(("letters-start", "== ", "The system cannot find", "File Not Found",
                            "Volume is", "No Instance(s)", "=== ")):
            continue
        if "not ready" in line.lower():
            info["note"] = line
            continue
        if line:
            info["dirs"].append(line)
    return volumes, done, None


def carries_the_payload(info):
    """Both markers, whether they came from a listing or an existence probe."""
    names = {d.lower() for d in info["dirs"]}
    markers = {m.lower() for m in info["markers"]}
    def holds(marker):
        return marker in names or any(v == marker or v.startswith(marker + "\\")
                                      or v.startswith(marker + "/") for v in markers)
    return all(holds(m) for m in MARKERS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("witness_dir")
    ap.add_argument("--letter", default="D")
    a = ap.parse_args()

    want = a.letter.upper()
    path = os.path.join(a.witness_dir, "letters.txt")
    if not os.path.exists(path):
        hook = os.path.join(a.witness_dir, "letters-hook.txt")
        print("no witness at %s" % path)
        if os.path.exists(hook):
            report = decode(open(hook, "rb").read()).strip()
            print("the Startup hook reported: %s" % report)
            print("verdict: FAIL - the hook ran and the survey never reached the volume, so the "
                  "witness this run needs does not exist")
            return 1
        print("and no hook marker either: the logon trigger never ran, so nothing is known")
        return 2

    volumes, done, fault = parse(decode(open(path, "rb").read()))
    census = os.path.join(a.witness_dir, "drives.txt")
    if os.path.exists(census):
        found = sorted({m.group(1).upper() + ":" for m in
                        re.finditer(r"([A-Za-z]):", decode(open(census, "rb").read()))})
        print("letters the boot reports: %s" % " ".join(found))
    print("letters the witness opened:")
    for letter in sorted(volumes):
        info = volumes[letter]
        what = ", ".join(sorted(set(info["dirs"])))[:90] or info.get("note", "-")
        marks = ",".join(sorted(set(info["markers"])))
        print("  %s:  label=%-12s serial=%-10s %s%s" %
              (letter, info["label"] or "?", info["serial"] or "?", what,
               ("  markers=" + marks) if marks else ""))

    if fault:
        print("verdict: FAIL - the witness reported %r, so its survey is not complete" % fault)
        return 1

    owners = sorted(l for l, info in volumes.items() if carries_the_payload(info))
    if not owners:
        print("verdict: FAIL - no letter carries the driver volume's markers (%s)" %
              ", ".join(MARKERS))
        return 1
    if owners != [want]:
        print("verdict: FAIL - the driver volume is on %s, not %s:" %
              (", ".join(owners), want))
        return 1
    if not done:
        print("verdict: FAIL - the witness is incomplete (no letters-done line); the survey "
              "stopped early and its absence of evidence is not evidence")
        return 1
    print("verdict: PASS - %s: is the driver volume, and it is the only letter holding it" % want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
