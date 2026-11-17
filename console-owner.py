#!/usr/bin/env python3
r"""Say which display device owns the console, and what each candidate slot claims.

A photographed desktop and a `videoctl.txt` line can both be true and still disagree, because the
picture is the console device's framebuffer and several devices can exist. `HARDWARE\DEVICEMAP\VIDEO`
is the arbiter: its `\Device\Video0` names the `Control\Video\{GUID}\NNNN` slot that owns the primary
desktop, and each slot says which user-mode display driver it loaded and whether it is the
VGA-compatible one. That mapping is volatile, so it is never in the hive file on disk and has to come
from the in-guest capture the startup hook exported.

The tool prints three things and marks the console: the class nodes the devnode is bound to, every
`Control\Video` slot with its drivers, and which slot `\Device\Video0` names. Read together they turn
"the desktop is 640x480" into "the console was given to this slot and not that one".

usage: console-owner.py --system <SYSTEM hive> --devicemap <devicemap.txt>
Needs hivex: `sudo apt install python3-hivex`, or /tmp/opencode/venv-hivex/bin/python.
"""
import argparse
import importlib.util
import re
import sys
from pathlib import Path

RIG = Path(__file__).resolve().parent
sys.path.insert(0, str(RIG))

try:
    import hivex
except ImportError:  # the parse below is testable without it
    hivex = None

_spec = importlib.util.spec_from_file_location("rg", (RIG / "registration_graft.py").as_posix())
rg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rg)

CLASS_WANT = ("DriverDesc", "InfPath", "InfSection", "Service", "Class", "MatchingDeviceId",
              "InstalledDisplayDrivers", "VgaCompatible")
SLOT_WANT = ("DriverDesc", "InstalledDisplayDrivers", "VgaCompatible",
             "DefaultSettings.XResolution", "DefaultSettings.YResolution",
             "DefaultSettings.BitsPerPixel")


def read_text(path):
    """`reg.exe` writes UTF-16 with a byte-order mark, the startup hook's other captures do not."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    return raw.decode("utf-8", "replace")


def video_map(devicemap_text):
    """`{device number: slot path}` from a `reg.exe` export of DEVICEMAP\\VIDEO."""
    found = {}
    for line in devicemap_text.splitlines():
        m = re.match(r"\s*\\Device\\Video(\d+)\s+REG_SZ\s+(.*Video\\\{[0-9A-Fa-f-]+\}\\\d+)", line)
        if m:
            found[m.group(1)] = m.group(2).strip()
    return found


def console_number(video_map_, devicemap_text):
    r"""Which `\Device\VideoN` the system declared VGA-compatible, if it declared one.

    DEVICEMAP carries a `VgaCompatible` string naming the device, and the primary desktop follows
    that device, so which one is named is the whole answer to who owns the console.
    """
    for line in devicemap_text.splitlines():
        m = re.match(r"\s*VgaCompatible\s+REG_SZ\s+(\\Device\\Video\d+)", line)
        if m:
            return m.group(1)[-1]
    return "0" if "0" in video_map_ else None


def values(h, node, only=None):
    out = {}
    for v in h.node_values(node) or []:
        name = h.value_key(v)
        if only and name not in only:
            continue
        out[name] = rg.decode(h, v)
    return out


def report(system, devicemap):
    """Print the three views and the console mark. Returns the console slot, or None."""
    if hivex is None:
        print("hivex is not importable: `sudo apt install python3-hivex`, or run this with "
              "/tmp/opencode/venv-hivex/bin/python", file=sys.stderr)
        return None
    h = hivex.Hivex(str(system))
    cs = rg.current_control_set(h)
    text = read_text(devicemap)
    vmap = video_map(text)
    who = console_number(vmap, text)

    print("control set: %s" % cs)
    print("\n== DEVICEMAP\\VIDEO ==")
    for number in sorted(vmap, key=int):
        mark = "   <== declared VGA-compatible, and the console" if number == who else ""
        print("   \\Device\\Video%s -> %s%s" % (number, vmap[number], mark))

    print("\n== the 290X devnode's class binding ==")
    pci = rg.descend(h, [cs, "Enum", "PCI"])
    bound = None
    for dev in h.node_children(pci) or []:
        if "VEN_1002&DEV_67B0" not in h.node_name(dev).upper():
            continue
        for inst in h.node_children(dev) or []:
            got = values(h, inst, ("Driver",))
            if "Driver" in got:
                bound = got["Driver"]
                print("   %s\\%s\n     Driver = %s"
                      % (h.node_name(dev), h.node_name(inst), bound))

    print("\n== the display class nodes ==")
    cls = rg.descend(h, [cs, "Control", "Class", rg.DISPLAY_CLASS])
    for sub in sorted(h.node_children(cls) or [], key=lambda s: h.node_name(s)):
        print("   %s" % h.node_name(sub))
        for key, got in sorted(values(h, sub, CLASS_WANT).items()):
            print("       %-26s %s" % (key, got))
        if bound and h.node_name(sub).lower() == str(bound).lower().split("\\")[-1]:
            print("       <== the node the devnode is bound to")

    print("\n== every Control\\Video slot, and what it claims ==")
    console = None
    video = rg.descend(h, [cs, "Control", "Video"])
    for guid in sorted(h.node_children(video) or [], key=lambda g: h.node_name(g)):
        for slot in h.node_children(guid) or []:
            if h.node_name(slot) == "Video":
                continue
            full = "%s\\%s" % (h.node_name(guid), h.node_name(slot))
            got = values(h, slot, SLOT_WANT)
            is_console = vmap.get(who, "").lower().endswith(full.lower())
            if is_console:
                console = full
            print("   %s%s" % (full, "   <== THE CONSOLE" if is_console else ""))
            for key in SLOT_WANT:
                if key in got:
                    print("       %-30s %s" % (key, got[key]))
    if console is None:
        print("   no slot matched the console device; the capture may be from another boot")
    return console


def main():
    p = argparse.ArgumentParser(prog="console-owner.py", description=__doc__.splitlines()[0])
    p.add_argument("--system", required=True, type=Path,
                   help="the SYSTEM hive read back from the run, evidence/WINDOWS/system32/config/system")
    p.add_argument("--devicemap", required=True, type=Path,
                   help="the in-guest DEVICEMAP\\VIDEO capture, evidence/WINDOWS/devicemap.txt")
    a = p.parse_args()
    for path in (a.system, a.devicemap):
        if not path.is_file():
            print("%s is not a file; run ./run-experiment.sh for the run first" % path,
                  file=sys.stderr)
            return 2
    return 0 if report(a.system, a.devicemap) else 1


if __name__ == "__main__":
    sys.exit(main())
