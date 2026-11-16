#!/usr/bin/env python3
"""Assemble the desktop verdict record from what a run's instruments actually recorded.

Why this exists. `plan/0007#desktop-verdict` is graded by `acceptance.sh desktop`, which reads
`<run>/evidence.json`, and nothing in the rig ever wrote that file. So the task's verify line could
only ever answer `INCONCLUSIVE: cannot read ... evidence.json`, which tells a reader nothing about
which clause of the bar a boot fails. A grader with no producer is a grader that cannot be argued
with.

The rule this tool holds itself to is the one the campaign learned the hard way
(call/0014: a run's evidence is witnessed by the run). It writes only what an instrument recorded,
and where an instrument is silent it writes the honest negative together with the reason, in
`unevidenced`, so a later reader sees that the claim was not measured rather than that it was
measured and failed. It never sets `operator_physical_monitor`: that flag is an observation a person
makes at the machine, and a tool that could set it would be a tool that could fake the acceptance.

Usage: desktop-evidence.py --run DIR [--force]
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

# Every clause the desktop bar asks for, in the order `acceptance.py` checks them. Kept here so the
# order of the report matches the order of the gate that reads it.
CLAUSES = ("desktop_reached", "ati_rendered", "driver_loaded", "operator_physical_monitor",
           "keyboard_response", "pointer_response")
# The collector logs a load either with the full path (`name=\WINDOWS\System32\ati2dvag.dll`) or
# with the bare module name (`name=ati2mtag.sys`), depending on how the packet reached it, so both
# are read and the basename is what counts. A pattern that required the backslash silently lost the
# miniport, and the verdict said the driver had not loaded.
MODULE = re.compile(r"\bMODULE LOAD\b[^\n]*?\bname=(\S+)")


class Bad(Exception):
    pass


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def need(run, rel):
    path = run / rel
    if not path.is_file():
        raise Bad("the run carries no %s, so the desktop verdict has nothing to read" % rel)
    return path


def decode(path):
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    return raw.decode("latin-1", "replace")


def video_report(run):
    """The guest's own `Win32_VideoController` dump, as name=value pairs."""
    text = decode(need(run, "evidence/WINDOWS/videoctl.txt"))
    found = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().lstrip("\ufeff").strip()
        if key:
            found[key] = value.strip()
    return found


def loaded_modules(run):
    names = set()
    for line in need(run, "kdc-detached.log").read_text(errors="replace").splitlines():
        match = MODULE.search(line)
        if match:
            names.add(match.group(1).replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].lower())
    return names


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build(run):
    """The verdict record, with every field traced to the artifact it came from."""
    meta_path = need(run, "run.json")
    meta = json.loads(meta_path.read_text())
    video = video_report(run)
    modules = loaded_modules(run)
    sources = {rel: sha256(run / rel) for rel in
               ("run.json", "kdc-detached.log", "evidence/WINDOWS/videoctl.txt")}

    width = as_int(video.get("CurrentHorizontalResolution"))
    height = as_int(video.get("CurrentVerticalResolution"))
    display_dll = "ati2dvag.dll" in modules
    miniport = "ati2mtag.sys" in modules

    unevidenced = {}
    if not meta.get("contract", {}).get("desktop_reached", False) \
            and not meta.get("desktop_reached", False):
        unevidenced["desktop_reached"] = "the run contract does not report a reached desktop"

    evidence = {
        "nonce": meta.get("nonce"),
        "candidate_sha256": meta.get("candidate_sha256"),
        "golden_sha256": meta.get("golden_sha256"),
        "channel_sha256": meta.get("channel_sha256"),
        "desktop_reached": bool(meta.get("desktop_reached",
                                         meta.get("contract", {}).get("desktop_reached", False))),
        # The display driver mapped into win32k is as far as "ATI rendered" gets from the host side.
        # The session it drove is reported separately, by width and height, below.
        "ati_rendered": display_dll,
        "driver_loaded": display_dll and miniport,
        "driver_identity": "ati2mtag.sys miniport, %s display driver"
                           % (video.get("InstalledDisplayDrivers") or "unreported"),
        "width": width if width is not None else 0,
        "height": height if height is not None else 0,
        "bits_per_pixel": as_int(video.get("CurrentBitsPerPixel")) or 0,
        "video_processor": video.get("VideoProcessor", ""),
        "adapter_name": video.get("Name", ""),
        "operator_physical_monitor": False,
        "keyboard_response": False,
        "pointer_response": False,
        "unevidenced": unevidenced,
        "derived_from": sources,
        "producer": "desktop-evidence.py",
    }
    if not display_dll:
        evidence["unevidenced"]["ati_rendered"] = \
            "no MODULE LOAD record for ati2dvag.dll in kdc-detached.log"
    if not miniport:
        evidence["unevidenced"]["driver_loaded"] = \
            "no MODULE LOAD record for ati2mtag.sys in kdc-detached.log"
    evidence["unevidenced"]["operator_physical_monitor"] = \
        "only an observer at the machine can set this, and this tool must not be able to"
    evidence["unevidenced"]["keyboard_response"] = \
        "this run's captures record that the guest answered its capture scripts, not that a keystroke " \
        "moved anything on screen"
    evidence["unevidenced"]["pointer_response"] = \
        "no pointer instrument ran in this boot"
    frames = sorted(path.name for path in
                    list(run.glob("*.ppm")) + list(run.glob("*.png")))
    if frames:
        evidence["snapshot"] = {"path": frames[0], "sha256": sha256(run / frames[0])}
    else:
        attempted = sorted(path.name for path in run.glob("feed-*.out"))
        evidence["unevidenced"]["snapshot"] = (
            "no frame artifact to bind%s; this argv attaches no display device, and the view that can "
            "see the monitor is the operator's"
            % (" (the capture attempts are in %s)" % ", ".join(attempted) if attempted else ""))
    if width is None or height is None:
        evidence["unevidenced"]["resolution"] = "the guest reported no mode at all"
    return evidence


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, type=Path)
    ap.add_argument("--force", action="store_true",
                    help="replace an existing evidence.json; without it, a written record stands")
    a = ap.parse_args()
    if not a.run.is_dir():
        raise Bad("no such run directory: %s" % a.run)
    out = a.run / "evidence.json"
    if out.exists() and not a.force:
        raise Bad("%s already exists; the record a run wrote stands unless --force says otherwise"
                  % out)
    evidence = build(a.run)
    out.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")

    print("wrote %s" % out)
    print("  candidate   %s" % str(evidence["candidate_sha256"])[:12])
    print("  session     %sx%sx%s as %s" % (evidence["width"], evidence["height"],
                                            evidence["bits_per_pixel"],
                                            evidence["adapter_name"] or "unreported"))
    print("  driver      %s" % evidence["driver_identity"])
    for clause in CLAUSES:
        print("  %-26s %s" % (clause, evidence[clause]))
    for clause, reason in sorted(evidence["unevidenced"].items()):
        print("  unevidenced %-24s %s" % (clause + ":", reason))
    print("  inputs      %s" % ", ".join("%s=%s" % (name.split("/")[-1], digest[:12])
                                         for name, digest in sorted(evidence["derived_from"].items())))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Bad as error:
        sys.stderr.write("desktop-evidence: %s\n" % error)
        sys.exit(2)
