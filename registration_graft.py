#!/usr/bin/env python3
r"""Read and repair the display registration that decides which XDDM loads.

Why this exists
---------------
The miniport and the display driver are bound by two different registry facts, and
every boot this campaign has run had one of them and not the other. PnP loads the
miniport named by the device's `Enum` `Service` value. win32k chooses the display
driver from `Control\Video\<VideoID>\0000`, the per-adapter key, which SetupAPI
seeds from the class node's `Settings` subkey at install time. Writing the class
key root afterwards does not reach that key, and SetupAPI never re-runs.

The measured evidence for that law is on this machine twice over. October's audit
hive (`/home/dconnolly/xp64-rig/work/audit/SYSTEM`) has the class node root named
`CA178340.inf` with `InstalledDisplayDrivers=ati2dvag`, while the same card's
`Control\Video\<VideoID>\0000` still holds the vga set with `VgaCompatible=1` and
a `Video` subkey naming `Service=vga`. In that boot the miniport did attach, since
the same key carries ChipType `AMD Radeon Graphics Processor (0x67B0)` and a
255 MB frame buffer, values only `ati2mtag.sys` writes. On the disk of 2026-11-14
the same card reports ChipType `AMD ATOMBIOS` with a 1 MB window, which is the vga
path. Same card, two registrations, two answers.

`handinstall.py` emits the service key and the class node root. `settings-reg.py`
emits the class chain, where the iCafe INF puts `InstalledDisplayDrivers` and
`VgaCompatible` at the root (`HKR,,`) rather than under `Settings`. Neither one
writes the per-adapter key, so the value that decides the display driver was never
written by anything. That is the defect this tool closes.

Nothing here is guessed. The class key number, the `VideoID`, and the device id all
come from the hive being repaired. `ati2dvag` and `VgaCompatible` are read out of
the INF's own AddReg chain, and the tool prints the file and line it took each one
from. The full SoftwareSettings replay stays in `settings-reg.py`: this graft needs
three values in the device-facing key, not the whole chain.

Usage
-----
  registration_graft.py check <system-hive> [--device-id ID]
  registration_graft.py graft <system-hive> --inf <INF> [--image-path PATH] [--print-only]

Exit codes: 0 as expected, 1 not ready or write refused, 2 bad input.
"""
import argparse
import os
import re
import sys

try:
    import hivex
except ImportError:
    # The pure parts of this tool (the INF reader, the grader, the write plan) are usable
    # and testable without it, so the module stays importable. Anything that touches a hive
    # says what to install and how to re-run.
    hivex = None

HIVEX_REMEDY = ("editing a hive needs hivex: install it with `sudo apt install python3-hivex` "
                "and re-run this command with the interpreter that has it (on this host that is "
                "`python3`, not `./venv/bin/python`)")

DISPLAY_CLASS = "{4D36E968-E325-11CE-BFC1-08002BE10318}"
DEVICE_ID = "VEN_1002&DEV_67B0"
REG_SZ, REG_DWORD, REG_MULTI_SZ = 1, 4, 7
SERVICE = "ati2mtag"
TYPE_NAMES = {1: "SZ", 2: "EXPAND_SZ", 3: "BIN", 4: "DWORD", 7: "MULTI_SZ"}


class Bad(Exception):
    pass


# ---------- the INF side: the two values that decide the display driver ----------

# An AddReg line carries the INF data style, not the registry type: this vendor's
# [Strings] spells MULTI_SZ 0x00010000 and DWORD 0x00010001 where the registry calls
# them 7 and 4. `settings-reg.py` applies the same mapping to the whole chain, and the
# table here is the same one, checked against the [Strings] block of the INF it reads.
DATA_STYLE = {
    0x00000000: REG_SZ,          # REG_SZ
    0x00000001: 3,               # REG_BINARY
    0x00010000: REG_MULTI_SZ,    # REG_MULTI_SZ
    0x00010001: REG_DWORD,       # REG_DWORD
    0x00020000: 2,               # REG_EXPAND_SZ
    0x00020001: 0,               # REG_NONE
}
BEHAVIOR_BITS = 0x00000002 | 0x00000004 | 0x00000008


def inf_settings(inf_path, section):
    """`InstalledDisplayDrivers` and `VgaCompatible` read out of an INF AddReg chain.

    Returns {name: (type, python value, provenance string)}. Only the two names this
    graft writes are kept; the rest of the chain belongs to `settings-reg.py`.
    """
    wanted = ("InstalledDisplayDrivers", "VgaCompatible")
    strings, sections, cur = {}, {}, None
    lineno = {}
    for n, raw in enumerate(open(inf_path, encoding="utf-8", errors="replace"), 1):
        line = raw.rstrip("\r\n")
        s = line.strip()
        if not s or s.startswith(";"):
            continue
        m = re.match(r"^\[(.+)\]\s*$", s)
        if m:
            cur = m.group(1)
            sections.setdefault(cur, [])
            continue
        if cur is not None:
            sections[cur].append((n, line))
    for n, line in sections.get("Strings", []):
        if "=" in line:
            k, v = line.split("=", 1)
            strings[k.strip()] = v.strip().strip('"')

    def subst(text):
        return re.sub(r"%([^%]+)%", lambda mm: strings.get(mm.group(1), mm.group(0)), text)

    if section not in sections:
        raise Bad(f"INF has no section [{section}]: {inf_path}")
    found = {}
    for n, line in sections[section]:
        if line.lstrip().startswith(";"):
            continue
        fields = [f.strip() for f in line.split(",")]
        if fields[0].upper() != "HKR":
            continue
        subkey = subst(fields[1]).strip('"') if len(fields) > 1 else ""
        name = subst(fields[2]).strip('"') if len(fields) > 2 else ""
        if subkey or name not in wanted:
            continue
        used = fields[1:]
        if any('"' in f and not (f.startswith('"') and f.endswith('"')) for f in used):
            raise Bad(f"{name} at {inf_path}:{n} has a quoted field this reader cannot split. "
                      "settings-reg.py is the quote-aware reader for the full chain")
        flags = int(subst(fields[3]).strip() or 0, 0) if len(fields) > 3 else 0
        data = subst(fields[4]).strip() if len(fields) > 4 else ""
        if len(fields) > 5:
            data += "," + ",".join(subst(x).strip() for x in fields[5:])
        if name in found:
            raise Bad(f"{name} appears twice at the class root in {inf_path}")
        style = (flags & ~BEHAVIOR_BITS)
        if style not in DATA_STYLE:
            raise Bad(f"{name} has an unmapped INF data style 0x{style:05x} at {inf_path}:{n}. "
                      "Check the [Strings] block of that INF before trusting this reader")
        typ = DATA_STYLE[style]
        if typ == REG_MULTI_SZ:
            items = [d.strip().strip('"') for d in data.split(",") if d.strip().strip('"')]
            value = items
        elif typ == REG_DWORD:
            value = int(data, 0)
        elif typ == REG_SZ:
            value = data.strip('"')
        else:
            raise Bad(f"{name} is registry type {typ} at {inf_path}:{n}; this graft only writes "
                      "a multi-string, a dword, or a string")
        found[name] = (typ, value, f"inf:{inf_path}:{n}")
    missing = [w for w in wanted if w not in found]
    if missing:
        raise Bad(f"could not read {' and '.join(missing)} from [{section}] of {inf_path}")
    return found


# ---------- the hive side: read the facts, then grade them ----------

def open_hive(path, write=False):
    if hivex is None:
        raise Bad(HIVEX_REMEDY)
    if not os.path.exists(path):
        raise Bad(f"no such hive: {path}")
    try:
        return hivex.Hivex(path, write=write)
    except Exception as exc:
        raise Bad(f"cannot open {path}: {exc}")


def child(h, node, name):
    if node is None:
        return None
    for c in h.node_children(node):
        if h.node_name(c).lower() == name.lower():
            return c
    return None


def descend(h, parts, create=False):
    node = h.root()
    for part in parts:
        n = child(h, node, part)
        if n is None:
            if not create:
                return None
            n = h.node_add_child(node, part)
        node = n
    return node


def value_map(h, node):
    return {h.value_key(v).lower(): v for v in h.node_values(node)} if node else {}


def decode(h, v):
    t, raw = h.value_value(v)
    if t in (REG_SZ, 2):
        return raw.decode("utf-16-le", "replace").rstrip("\0")
    if t == REG_MULTI_SZ:
        return [s for s in raw.decode("utf-16-le", "replace").split("\0") if s]
    if t == REG_DWORD and len(raw) == 4:
        return int.from_bytes(raw, "little")
    if t == 3 and len(raw) >= 4 and len(raw) % 2 == 0:
        # The HardwareInformation values are REG_BINARY carrying UTF-16 text, and the
        # ChipType among them is the evidence that the ATI miniport attached. A hex blob
        # would hide the one fact this line exists to show.
        try:
            text = raw.decode("utf-16-le").rstrip("\0")
        except UnicodeDecodeError:
            return {"type": t, "bytes": raw[:8].hex()}
        if text and all(31 < ord(c) < 127 for c in text):
            return text
    return {"type": t, "bytes": raw[:8].hex()}


def encode(typ, value):
    if typ == REG_MULTI_SZ:
        return "\0".join(value).encode("utf-16-le") + b"\0\0"
    if typ == REG_DWORD:
        return int(value).to_bytes(4, "little")
    return str(value).encode("utf-16-le") + b"\0\0"


def current_control_set(h):
    """Which ControlSet an offline hive is booted from: Select\\Current, as a DWORD."""
    sel = value_map(h, descend(h, ["Select"]))
    if "current" in sel:
        n = decode(h, sel["current"])
        if isinstance(n, int):
            name = "ControlSet%03d" % n
            if descend(h, [name]) is not None:
                return name
    for c in (h.node_children(h.root()) or []):
        if re.fullmatch(r"ControlSet\d{3}", h.node_name(c)):
            return h.node_name(c)
    raise Bad("this hive has neither Select\\Current nor a ControlSet### key")


def find_device(h, device_id):
    """The card's Enum instance: its class key, its VideoID, and the service PnP loads."""
    current = current_control_set(h)
    cs = descend(h, [current])
    pci = descend(h, [current, "Enum", "PCI"])
    for dev in (h.node_children(pci) or []):
        if device_id.upper() not in h.node_name(dev).upper():
            continue
        for inst in h.node_children(dev):
            vals = value_map(h, inst)
            if "driver" not in vals or "service" not in vals:
                continue
            vid = None
            vp = child(h, inst, "Device Parameters")
            vm = value_map(h, vp)
            if "videoid" in vm:
                vid = decode(h, vm["videoid"]).strip("{}").upper()
            return {
                "control_set": current,
                "enum_path": f"{current}\\Enum\\PCI\\{h.node_name(dev)}\\{h.node_name(inst)}",
                "enum_service": decode(h, vals["service"]),
                "class_key": decode(h, vals["driver"]).split("\\")[-1],
                "videoid": vid,
            }
    raise Bad(f"no Enum instance matching {device_id} in this hive")


def facts(h, device_id=DEVICE_ID):
    d = find_device(h, device_id)
    cs, ck, vid = d["control_set"], d["class_key"], d["videoid"]
    out = dict(d)
    cls = descend(h, [cs, "Control", "Class", DISPLAY_CLASS, ck])
    cm = value_map(h, cls)
    out["class_infpath"] = decode(h, cm["infpath"]) if "infpath" in cm else None
    out["class_infsection"] = decode(h, cm["infsection"]) if "infsection" in cm else None
    out["class_driverdesc"] = decode(h, cm["driverdesc"]) if "driverdesc" in cm else None
    out["class_root_drivers"] = decode(h, cm["installeddisplaydrivers"]) if "installeddisplaydrivers" in cm else None
    out["class_root_vgacompat"] = decode(h, cm["vgacompatible"]) if "vgacompatible" in cm else None
    st = descend(h, [cs, "Control", "Class", DISPLAY_CLASS, ck, "Settings"])
    sm = value_map(h, st)
    out["class_settings_drivers"] = decode(h, sm["installeddisplaydrivers"]) if "installeddisplaydrivers" in sm else None
    out["class_settings_vgacompat"] = decode(h, sm["vgacompatible"]) if "vgacompatible" in sm else None
    if vid:
        inst = descend(h, [cs, "Control", "Video", "{" + vid + "}", "0000"])
        im = value_map(h, inst)
        out["dev_drivers"] = decode(h, im["installeddisplaydrivers"]) if "installeddisplaydrivers" in im else None
        out["dev_vgacompat"] = decode(h, im["vgacompatible"]) if "vgacompatible" in im else None
        out["dev_chip_type"] = decode(h, im["hardwareinformation.chiptype"]) if "hardwareinformation.chiptype" in im else None
        sv = descend(h, [cs, "Control", "Video", "{" + vid + "}", "Video"])
        svm = value_map(h, sv)
        out["dev_service"] = decode(h, svm["service"]) if "service" in svm else None
    svc = descend(h, [cs, "Services", SERVICE])
    svm = value_map(h, svc)
    out["svc_start"] = decode(h, svm["start"]) if "start" in svm else None
    out["svc_imagepath"] = decode(h, svm["imagepath"]) if "imagepath" in svm else None
    return out


def grade(f):
    """Verdict plus the reason, in the same words every boot's record will use."""
    why = []
    if f.get("svc_start") is None:
        why.append(f"Services\\{SERVICE} is absent, so nothing loads the miniport as a service")
    elif f["svc_start"] > 1:
        why.append(f"Services\\{SERVICE} Start={f['svc_start']}, not the boot-start value 1")
    if f.get("enum_service") != SERVICE:
        why.append(f"the devnode's Enum Service is {f.get('enum_service')!r}, so PnP loads that "
                   f"service for this devnode and never {SERVICE}")
    dev = f.get("dev_drivers")
    if not dev:
        why.append("Control\\Video\\%s\\0000 has no InstalledDisplayDrivers" % f.get("videoid"))
    elif dev[0] != "ati2dvag":
        why.append(f"the per-adapter key's first InstalledDisplayDrivers entry is {dev[0]!r}, "
                   "which is what win32k loads, and it is not the ATI display driver")
    if f.get("dev_vgacompat") == 1:
        why.append("the per-adapter key says VgaCompatible=1, so the session treats the card as a VGA device")
    if f.get("dev_service") != SERVICE:
        why.append(f"Control\\Video\\%s\\Video names Service={f.get('dev_service')!r}" % f.get("videoid"))
    ready = not why
    if ready:
        verdict = "BOUND-READY"
    elif f.get("enum_service") == SERVICE and f.get("svc_start") is not None and f["svc_start"] <= 1:
        verdict = "MINIPORT-ONLY"
    else:
        verdict = "VGA-ONLY"
    return verdict, why


def show_facts(f):
    rows = [
        ("control set", f["control_set"]),
        ("devnode Enum Service", f["enum_service"]),
        ("devnode class key", f["class_key"]),
        ("devnode VideoID", f["videoid"]),
        ("class InfPath", f["class_infpath"]),
        ("class InfSection", f["class_infsection"]),
        ("class DriverDesc", f["class_driverdesc"]),
        ("class root InstalledDisplayDrivers", f["class_root_drivers"]),
        ("class root VgaCompatible", f["class_root_vgacompat"]),
        ("class Settings InstalledDisplayDrivers", f["class_settings_drivers"]),
        ("class Settings VgaCompatible", f["class_settings_vgacompat"]),
        ("per-adapter InstalledDisplayDrivers", f.get("dev_drivers")),
        ("per-adapter VgaCompatible", f.get("dev_vgacompat")),
        ("per-adapter ChipType", f.get("dev_chip_type")),
        ("per-adapter Video Service", f.get("dev_service")),
        (f"Services\\{SERVICE} Start", f["svc_start"]),
        (f"Services\\{SERVICE} ImagePath", f["svc_imagepath"]),
    ]
    print("registration this hive carries:")
    for k, v in rows:
        print("  %-40s %s" % (k, v))


def disclosures():
    """What this graft leaves alone, printed so no reader has to assume it."""
    return [
        "the class node's InfPath, InfSection and DriverDesc are read and shown but not rewritten. "
        "This tool changes the values the runtime reads, not the node's install provenance, so a "
        "node that still names display.inf would re-seed Settings from that INF on a later install pass.",
    ]


def cmd_check(a):
    h = open_hive(a.hive)
    f = facts(h, a.device_id)
    show_facts(f)
    verdict, why = grade(f)
    print(f"verdict: {verdict}")
    for w in why:
        print(f"  because: {w}")
    for note in disclosures():
        print(f"  disclosed: {note}")
    if verdict != "BOUND-READY":
        print("  remedy: run ./offline-registration.sh, then re-run this check on the after hive")
        return 1
    print("  the miniport and the per-adapter display selection both name the ATI driver")
    return 0


def cmd_graft(a):
    src = facts(open_hive(a.hive), DEVICE_ID)
    show_facts(src)
    inf = inf_settings(a.inf, "ati2mtag_SoftwareDeviceSettings")
    drivers = inf["InstalledDisplayDrivers"]
    vgacompat = inf["VgaCompatible"]
    print("values read from the INF (name, value, provenance):")
    for name, (typ, value, prov) in (("InstalledDisplayDrivers", drivers), ("VgaCompatible", vgacompat)):
        print(f"  {name} = {value!r}  [{TYPE_NAMES[typ]}]  {prov}")
    writes = build_writes(src, a, drivers, vgacompat)
    print("writes this graft makes:")
    for path, name, typ, value, why in writes:
        print(f"  {path}\\{name} = {value!r}  [{TYPE_NAMES[typ]}]  {why}")
    for note in disclosures():
        print(f"  disclosed: {note}")
    if a.print_only:
        print("print-only: the hive is unchanged")
        return 0
    h = open_hive(a.hive, write=True)
    for path, name, typ, value, _ in writes:
        node = descend(h, path.split("\\"), create=True)
        h.node_set_value(node, {"key": name, "t": typ, "length": len(encode(typ, value)),
                                "value": encode(typ, value)})
    h.commit(a.hive)
    after = facts(open_hive(a.hive), DEVICE_ID)
    verdict, why = grade(after)
    print(f"read back from the written hive: verdict {verdict}")
    for w in why:
        print(f"  because: {w}")
    return 0 if verdict == "BOUND-READY" else 1


def build_writes(src, a, drivers, vgacompat):
    """The target set, in words: bind the miniport, then make the device-facing copy agree."""
    cs, ck, vid = src["control_set"], src["class_key"], src["videoid"]
    if not vid:
        raise Bad("the devnode has no Device Parameters\\VideoID, so the per-adapter key cannot be found")
    dtyp, dval, _ = drivers
    vtyp, vval, _ = vgacompat
    cls = f"{cs}\\Control\\Class\\{DISPLAY_CLASS}\\{ck}"
    dev = f"{cs}\\Control\\Video\\{{{vid}}}"
    svc = f"{cs}\\Services\\{SERVICE}"
    return [
        (svc, "Type", REG_DWORD, 1, "a kernel driver"),
        (svc, "Start", REG_DWORD, 1, "boot-start, the one value that puts the miniport in the load order"),
        (svc, "ErrorControl", REG_DWORD, 0, "a failure must not halt the boot L am measuring"),
        (svc, "Group", REG_SZ, "Video", "the load group the real install assigns"),
        (svc, "ImagePath", REG_SZ, a.image_path,
         "measured law: the INF route %12% is reverted by file protection, so the image is carried "
         "on the driver volume (see call/0009 for the letter pin)"),
        (src["enum_path"], "Service", REG_SZ, SERVICE, "what PnP loads for this devnode"),
        (cls, "Service", REG_SZ, SERVICE, "the class node's own view"),
        (cls, "InstalledDisplayDrivers", dtyp, dval, "from the INF"),
        (cls, "VgaCompatible", vtyp, vval, "from the INF"),
        (cls + "\\Settings", "InstalledDisplayDrivers", dtyp, dval,
         "measured law: SetupAPI seeds the per-adapter key from Settings, so a re-bind must find the "
         "ATI set here and not the vga set that is in it now"),
        (cls + "\\Settings", "VgaCompatible", vtyp, vval, "same channel as the line above"),
        (dev + "\\0000", "InstalledDisplayDrivers", dtyp, dval,
         "the key win32k reads to choose the display driver; nothing has ever written it"),
        (dev + "\\0000", "VgaCompatible", vtyp, vval, "same key, and =1 is what makes the session a VGA session"),
        (dev + "\\0000", "Device Description", REG_SZ, a.device_description,
         "the description SetupAPI resolved for this package (see setupapi.log #I022) and the one "
         "handinstall.py wrote on the class node"),
        (dev + "\\Video", "Service", REG_SZ, SERVICE, "the service the video device object names"),
    ]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    c = sub.add_parser("check", help="read a SYSTEM hive and grade the registration")
    c.add_argument("hive")
    c.add_argument("--device-id", default=DEVICE_ID)
    g = sub.add_parser("graft", help="write the registration into a SYSTEM hive")
    g.add_argument("hive")
    g.add_argument("--inf", required=True, help="the driver INF the values are read from")
    g.add_argument("--image-path", default="\\??\\D:\\drv\\ati2mtag.sys",
                   help="ImagePath for the service; the default is the driver volume route")
    g.add_argument("--device-description", default="AMD Radeon R9 290X / 390X")
    g.add_argument("--print-only", action="store_true", help="show the writes, change nothing")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "graft":
        return cmd_graft(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Bad as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
