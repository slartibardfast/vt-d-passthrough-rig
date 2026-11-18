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
import hashlib
import json
import os
from pathlib import Path
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
        return hivex.Hivex(str(path), write=write)   # the binding takes a string, and a Path
        #                                            reaching it fails only when a caller passes one
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
        "the `graft` subcommand changes the values the runtime reads and leaves the class node's "
        "install provenance alone, so a node that still names display.inf re-seeds Settings from that "
        "INF on a later install pass. `classnode` is the subcommand that rewrites provenance, and it "
        "reads every value from the INF rather than typing them.",
    ]


def inf_binding(inf_path, device_id=DEVICE_ID):
    """The class-node values this device's own INF states, read out and never typed in.

    Why a reader should trust the result: every field is tied to a line the function quotes back in
    `provenance`, and a missing piece raises. A guessed `InfSection` would produce a device that boots
    into a driver Windows cannot name, which is worse than no graft at all because the failure looks like
    hardware.
    """
    lines = open(inf_path, encoding="utf-8", errors="replace").read().splitlines()
    sections, current = {}, None
    for n, raw in enumerate(lines, 1):
        s = raw.strip()
        if not s or s.startswith(";"):
            continue
        m = re.match(r"^\[(.+)\]\s*$", s)
        if m:
            current = m.group(1)
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append((n, raw))

    strings = {}
    for n, line in sections.get("Strings", []) + sections.get("strings", []):
        if "=" in line:
            key, value = line.split("=", 1)
            strings[key.strip()] = value.strip().strip('"')

    def subst(text):
        return re.sub(r"%([^%]+)%", lambda mm: strings.get(mm.group(1), mm.group(0)), text)

    version = {k.lower(): v for k, _, v in
               ((lambda kv: (kv[0], None, kv[1]))(line.split("=", 1))
                for _, line in sections.get("Version", []) if "=" in line)}
    want_guid = "{4D36E968-E325-11CE-BFC1-08002BE10318}".upper()
    if version.get("classguid", "").upper() != want_guid:
        raise Bad(f"{inf_path} declares ClassGUID={version.get('classguid')}, not the Display class "
                  f"{want_guid}, so its values belong on no Display class node")

    model, model_line = None, None
    for name, entries in sections.items():
        for n, line in entries:
            if line.lstrip().startswith(";") or "=" not in line:
                continue
            left, _, right = line.partition("=")
            parts = [f.strip() for f in right.split(",")]
            ids = [f for f in parts[1:] if f.upper().startswith("PCI\\")]
            if not ids or not any(device_id.upper() in i.upper() for i in ids):
                continue
            model = [left.strip(), parts[0]]
            model_line = (name, n, line.strip())
            break
        if model:
            break
    if not model:
        raise Bad(f"{inf_path} carries no model entry for {device_id}, so nothing here is derivable")

    section = model[1].split(";")[0].strip()
    if section not in sections:
        raise Bad(f"{inf_path}'s model entry names [{section}], which the file does not contain")
    driver_desc = subst(model[0].strip().strip('"'))
    provider = subst(version.get("provider", ""))
    driver_ver = version.get("driverver", "")
    date, _, ver = driver_ver.partition(",")
    if not provider or not driver_desc or not date.strip():
        raise Bad(f"{inf_path} is missing Provider, the model description, or DriverVer, so the class "
                  "node cannot be written from it")
    unresolved = {k: v for k, v in (("ProviderName", provider), ("DriverDesc", driver_desc),
                                    ("InfSection", section)) if "%" in v}
    if unresolved:
        raise Bad(f"{inf_path} leaves a string unresolved: {unresolved}. Add the [Strings] entry or "
                  "bind by hand; writing a literal %name% into a class node would show up in the "
                  "guest as a device Windows cannot name")

    return {
        "InfPath": Path(inf_path).name,
        "InfSection": section,
        "ProviderName": provider,
        "DriverDesc": driver_desc,
        "MatchingDeviceId": ("pci\\" + device_id).lower(),
        "Service": SERVICE,
        "Class": version.get("class", "Display"),
        "DriverDate": date.strip().strip('"'),
        "DriverVersion": ver.strip().strip('"'),
        "provenance": {
            "model entry": "%s line %d: %s" % (model_line[0], model_line[1], model_line[2]),
            "Version lines": [l.strip() for _, l in sections.get("Version", [])
                              if l.split("=")[0].strip().lower()
                              in ("provider", "class", "classguid", "driverver")],
            "string for the description": "%s = %r" % (
                [k for k, v in strings.items() if v == driver_desc][0] if driver_desc in strings.values()
                else driver_desc, driver_desc),
        },
    }


def free_class_key(h, class_guid):
    """The next unused `NNNN` under a class, the number SetupAPI would have taken."""
    node = descend(h, ["ControlSet001", "Control", "Class", class_guid]) or descend(
        h, [current_control_set(h), "Control", "Class", class_guid])
    if node is None:
        raise Bad(f"this hive has no class key {class_guid}")
    used = set()
    for child_node in (h.node_children(node) or []):
        if re.fullmatch(r"\d{4}", h.node_name(child_node)):
            used.add(int(h.node_name(child_node)))
    for n in range(len(used) + 2):
        if n not in used:
            return "%04d" % n
    raise Bad(f"class {class_guid} has no free four-digit subkey below {len(used) + 2}")


SETTINGS_SECTION = "ati2mtag_SoftwareDeviceSettings"


def classnode_writes(src, binding, class_key, settings=None):
    cls = f"{src['control_set']}\\Control\\Class\\{DISPLAY_CLASS}\\{class_key}"
    writes = [
        (cls, "InfPath", REG_SZ, binding["InfPath"], "read from the INF's own file name"),
        (cls, "InfSection", REG_SZ, binding["InfSection"], "the section the INF's model entry names"),
        (cls, "ProviderName", REG_SZ, binding["ProviderName"], "the INF's Version Provider, resolved"),
        (cls, "DriverDesc", REG_SZ, binding["DriverDesc"], "the INF's model description, resolved"),
        (cls, "MatchingDeviceId", REG_SZ, binding["MatchingDeviceId"],
         "the hardware id the INF matched, lower-cased as SetupAPI stores it"),
        (cls, "Service", REG_SZ, SERVICE, "the miniport PnP must start for this device"),
        (cls, "Class", REG_SZ, "Display", "the class both the INF and the device name"),
        (cls, "DriverDate", REG_SZ, binding["DriverDate"], "from the INF's DriverVer"),
        (cls, "DriverVersion", REG_SZ, binding["DriverVersion"], "from the INF's DriverVer"),
        (cls, "ConfigFlags", REG_DWORD, 0, "no install-time flags; the binding is deliberate"),
        (src["enum_path"], "Driver", REG_SZ, f"{DISPLAY_CLASS}\\{class_key}",
         "the one value that decides which class node the runtime consults for this devnode"),
    ]
    if settings is None:
        return writes
    # A Display class node carries two duties, not one. It records where the package came from, and
    # it is where the runtime reads which user-mode display driver to load and whether the device is
    # the VGA-compatible one. A node written with provenance alone was measured to make the display
    # path worse than the vga node it replaced, so the complete node is the default and an incomplete
    # one is a flag a caller has to ask for by name.
    for name in ("InstalledDisplayDrivers", "VgaCompatible"):
        if name not in settings:
            raise Bad(f"the INF's [{SETTINGS_SECTION}] chain carries no {name}, so the class node "
                      f"cannot be completed from {binding['InfPath']}")
        typ, value, provenance = settings[name]
        cls_path = f"{src['control_set']}\\Control\\Class\\{DISPLAY_CLASS}\\{class_key}"
        writes.append((cls_path, name, typ, value,
                       f"read from the INF's {name} line at {provenance}"))
    # `Settings` is the install-time snapshot the per-adapter key is seeded from, so a fresh node
    # without it seeds nothing and the runtime falls back to the driver the old node named. The
    # project measured that law on the graft path and never applied it here, which is one reason a
    # complete-looking node can still change nothing.
    for name in ("InstalledDisplayDrivers", "VgaCompatible"):
        typ, value, provenance = settings[name]
        writes.append((f"{cls_path}\\Settings", name, typ, value,
                       f"the same value in the install-time snapshot, from {provenance}"))
    return writes


def cmd_classnode(a):
    """Point the card's class node at its own driver package, or report where it stands."""
    binding = inf_binding(a.inf, a.device_id)
    h = open_hive(a.hive)
    src = facts(h, a.device_id)
    bound_to = src["class_key"]
    cls_now = f"{src['control_set']}\\Control\\Class\\{DISPLAY_CLASS}\\{bound_to}"
    now = value_map(h, descend(h, cls_now.split("\\")))

    def shown(node_values, name):
        v = node_values.get(name.lower())
        return "(absent)" if v is None else str(decode(h, v))

    print("device:      %s" % src["enum_path"])
    print("Driver now:  %s\\%s" % (DISPLAY_CLASS, bound_to))
    for name in ("InfPath", "InfSection", "DriverDesc", "ProviderName", "Service"):
        print("  %-15s %s" % (name, shown(now, name)))
    print("the INF the graft would bind:")
    for name in ("InfPath", "InfSection", "DriverDesc", "ProviderName", "DriverVersion",
                 "MatchingDeviceId"):
        print("  %-15s %s" % (name, binding[name]))
    for label, line in binding["provenance"].items():
        print("  from: %-13s %s" % (label, line))

    # Where the values go. A devnode already pointing at a node that carries this INF stays there, so
    # `--check` grades the binding it finds rather than inventing a new subkey to be disappointed by.
    already = shown(now, "InfPath") == binding["InfPath"]
    target = a.class_key or (bound_to if already else free_class_key(h, DISPLAY_CLASS))
    settings = None
    if a.provenance_only:
        print("provenance-only: %s and %s will NOT be written. Measured at this pin, a class node "
              "that carries provenance alone mapped less than the vga node it replaced."
              % ("InstalledDisplayDrivers", "VgaCompatible"))
    else:
        settings = inf_settings(a.inf, a.settings_section)
        print("the values that make the node complete, read from the same INF:")
        for name in ("InstalledDisplayDrivers", "VgaCompatible"):
            if name in settings:
                typ, value, provenance = settings[name]
                print("  %-24s %r  [%s]  %s" % (name, value, TYPE_NAMES[typ], provenance))
    del h
    writes = classnode_writes(src, binding, target, settings)
    print("class subkey: %s (%s)" % (target, "reusing the node it already points at"
                                     if target == bound_to else "a fresh node, so the old one survives"))
    print("writes:")
    for path, name, typ, value, why in writes:
        print("  %s\\%s = %r  [%s]  %s" % (path, name, value, TYPE_NAMES[typ], why))
    if a.check:
        h2 = open_hive(a.hive)
        node = descend(h2, f"{src['control_set']}\\Control\\Class\\{DISPLAY_CLASS}"
                           f"\\{target}".split("\\"))
        vals = value_map(h2, node)
        inst = value_map(h2, descend(h2, src["enum_path"].split("\\")))
        driver = inst.get("driver")
        pointed = driver is not None and str(decode(h2, driver)).endswith("\\" + target)
        named = "infpath" in vals and str(decode(h2, vals["infpath"])) == binding["InfPath"]
        del h2
        if pointed and named:
            print("verdict: BOUND (the devnode's Driver names a class node that carries this INF)")
            return 0
        print("verdict: NOT-BOUND")
        print("  remedy: registration_graft.py classnode %s --inf %s --apply" % (a.hive, a.inf))
        return 1
    if not a.apply:
        print("print-only: the hive is unchanged. Pass --apply to write, or --check to grade.")
        return 0

    before = {"enum_path": src["enum_path"], "previous_Driver": "%s\\%s" % (DISPLAY_CLASS, bound_to),
              "class_key": target, "InfPath": binding["InfPath"]}
    h = open_hive(a.hive, write=True)
    for path, name, typ, value, _ in writes:
        node = descend(h, path.split("\\"), create=True)
        h.node_set_value(node, {"key": name, "t": typ, "length": len(encode(typ, value)),
                                "value": encode(typ, value)})
    h.commit(a.hive)
    Path(a.record).parent.mkdir(parents=True, exist_ok=True)
    Path(a.record).write_text(json.dumps(before, indent=2, sort_keys=True) + "\n")
    print("read back:")
    h = open_hive(a.hive)
    vals = value_map(h, descend(h, f"{src['control_set']}\\Control\\Class\\{DISPLAY_CLASS}"
                                   f"\\{target}".split("\\")))
    for name in ("InfPath", "InfSection", "Service", "DriverDesc",
                 "InstalledDisplayDrivers", "VgaCompatible"):
        print("  %-24s %s" % (name, shown(vals, name)))
    driver = value_map(h, descend(h, src["enum_path"].split("\\"))).get("driver")
    print("  Driver          %s" % ("(absent)" if driver is None else decode(h, driver)))
    print("rollback record: %s" % a.record)
    return 0



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


# ---------- the mode the desktop is asked for ----------

# The hardware profile is where the mode a desktop opens at lives, and this is the only writer in the
# rig that names it. `Control\Video\{GUID}` says which user-mode driver the session uses; the profile
# says whether that device opens on the desktop and at what size. A guest whose attached adapter asks
# for a mode the path cannot make answers by falling back to the VGA-compatible console, and the
# fallback is silent: the desktop simply appears at 640x480 with the wrong driver mapped.
MODE_RE = re.compile(r"^(\d{3,5})x(\d{3,5})x(8|16|32)@(50|56|60|70|72|75|85)$")
MODE_VALUES = ("DefaultSettings.XResolution", "DefaultSettings.YResolution",
               "DefaultSettings.BitsPerPel", "DefaultSettings.VRefresh")


def parse_mode(text):
    m = MODE_RE.fullmatch(text or "")
    if not m:
        raise Bad(f"{text!r} is not a display mode; write it WIDTHxHEIGHTxBITS@REFRESH, for example "
                  f"1024x768x32@60")
    return tuple(int(x) for x in m.groups())


def current_profile(h, cs):
    cfg = descend(h, [cs, "Control", "IDConfigDB", "CurrentConfig"])
    m = value_map(h, cfg)
    if "default" in m:
        return "%04d" % decode(h, m["default"])
    return "0001"


def video_profile_path(cs, profile, vid):
    return (f"{cs}\\Hardware Profiles\\{profile}\\System\\CurrentControlSet\\Control\\VIDEO"
            f"\\{{{vid}}}\\0000")


def mode_writes(base, mode, monitors):
    width, height, bpp, refresh = mode
    values = dict(zip(MODE_VALUES, (width, height, bpp, refresh)))
    writes = [(base, "Attach.ToDesktop", REG_DWORD, 1,
               "the device has to open on the desktop for its mode to matter")]
    for name in MODE_VALUES:
        writes.append((base, name, REG_DWORD, values[name],
                       "the mode win32k asks this adapter for"))
    for mon in monitors:
        for name in MODE_VALUES:
            writes.append((base + "\\" + mon, name, REG_DWORD, values[name],
                           "the monitor entry repeats the mode, and a slot whose two reads disagree "
                           "is one win32k may refuse"))
    return writes


def read_mode(h, base):
    node = descend(h, base.split("\\"))
    if node is None:
        return None
    m = value_map(h, node)
    return {name: (decode(h, m[name.lower()]) if name.lower() in m else None)
            for name in MODE_VALUES + ("Attach.ToDesktop",)}


def cmd_display_mode(a):
    mode = parse_mode(a.mode)
    h = open_hive(a.hive, write=a.apply)
    d = find_device(h, DEVICE_ID)
    cs, vid = d["control_set"], d["videoid"]
    if not vid:
        raise Bad("the devnode has no Device Parameters\\VideoID, so the profile slot the desktop "
                  "reads cannot be named")
    profile = current_profile(h, cs)
    base = video_profile_path(cs, profile, vid)
    node = descend(h, base.split("\\"))
    monitors = []
    if node is not None:
        monitors = [h.node_name(c) for c in (h.node_children(node) or [])
                    if h.node_name(c).lower().startswith("mon")]
    print(f"device:        {d['enum_path']}")
    print(f"video slot:    {{{vid}}}")
    print(f"profile:       {profile}")
    print(f"reads now:     {read_mode(h, base)}")
    print(f"mode asked for: {mode[0]}x{mode[1]}x{mode[2]}@{mode[3]}")
    writes = mode_writes(base, mode, monitors)
    for path, name, typ, value, why in writes:
        print(f"  {path}\\{name} = {value!r}  ({why})")
    want = {"DefaultSettings.XResolution": mode[0], "DefaultSettings.YResolution": mode[1],
            "DefaultSettings.BitsPerPel": mode[2], "DefaultSettings.VRefresh": mode[3]}
    now = read_mode(h, base) or {}
    matches = all(now.get(k) == v for k, v in want.items()) and now.get("Attach.ToDesktop") == 1
    if not a.apply:
        if a.check:
            print("verdict: %s" % ("MODE-SET" if matches else "MODE-NOT-SET"))
            return 0 if matches else 1
        print("print-only: the hive is unchanged")
        return 0
    for path, name, typ, value, _ in writes:
        n = descend(h, path.split("\\"), create=True)
        h.node_set_value(n, {"key": name, "t": typ, "length": len(encode(typ, value)),
                             "value": encode(typ, value)})
    h.commit(a.hive)
    after = read_mode(open_hive(a.hive), base)
    ok = bool(after) and after["DefaultSettings.XResolution"] == mode[0] \
        and after["DefaultSettings.YResolution"] == mode[1] \
        and after["DefaultSettings.BitsPerPel"] == mode[2] \
        and after["DefaultSettings.VRefresh"] == mode[3]
    print(f"read back:     {after}")
    print("verdict: %s" % ("MODE-SET" if ok else "MODE-NOT-SET"))
    return 0 if ok else 1


# ---------- the package the INF says belongs in system32 ----------

# A real display install copies a set of user-mode files into system32, and the list is in the INF's
# own `CopyFiles` groups with `DestinationDirs` naming where each goes. Placing only the display DLL
# gives a driver that loads and then declines, because the components it pulls at enable time are not
# there. This reads that list from the INF rather than from anybody's memory of the package.
DEST_NAMES = {"10": "Windows", "11": "system32", "12": "system32\\drivers"}


def sha_of(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def volume_path(root, dest_id, name):
    """Where a destination id lands on a mounted XP volume, as real path components.

    A destination id is not a path fragment: joining "WINDOWS" and "system32" with a backslash builds
    one component whose name contains a backslash on this platform, and the files land nowhere the
    read-back looks. The first version of this did exactly that and proved 0 of 21 files.
    """
    parts = ["WINDOWS"] + (["system32", "drivers"] if dest_id == "12"
                           else [] if dest_id == "10" else ["system32"])
    return Path(root).joinpath(*parts, name)


def inf_section(text, name):
    out, on = [], False
    for raw in text.splitlines():
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            on = s[1:-1].lower() == name.lower()
            continue
        if on and s and not s.startswith(";"):
            out.append(s)
    return out


def inf_destinations(text):
    dirs = {}
    for row in inf_section(text, "DestinationDirs"):
        if "=" in row:
            key, value = row.split("=", 1)
            dirs[key.strip().lower()] = value.split(";")[0].strip()
    return dirs


def inf_copy_plan(inf_path, section):
    """[(source name, destination name, destination id)] for one install section's CopyFiles."""
    text = Path(inf_path).read_text(errors="replace")
    dirs = inf_destinations(text)
    copy = next((r.split("=", 1)[1] for r in inf_section(text, section)
                 if r.lower().startswith("copyfiles=")), "")
    plan = []
    for group in [g.strip().rstrip(";") for g in copy.split(",") if g.strip()]:
        dest_id = dirs.get(group.lower())
        if dest_id is None:
            continue
        for line in inf_section(text, group):
            source, _, renamed = line.partition(",")
            plan.append((source.strip(), (renamed.strip() or source.strip()), dest_id))
    return plan


def cmd_package(a):
    """Read the INF's placement list, then check it, print it, or place it.

    The three phases are separate on purpose. An earlier version wrote while it was building the
    list, gated on whether it had a volume root, so a `--check` against a root wrote the files it was
    supposed to be checking and then reported them present. A check that can create what it checks is
    not a check.
    """
    section = a.section or inf_binding(a.inf)["InfSection"]
    plan = inf_copy_plan(a.inf, section)
    if not plan:
        raise Bad(f"the INF's [{section}] names no CopyFiles group this tool can resolve, so the "
                  f"package it wants cannot be placed")
    package = Path(a.package)
    available = {q.name.lower(): q for q in package.iterdir() if q.is_file()}
    omit = {name.lower() for name in (getattr(a, "omit", None) or [])}
    entries, skipped, missing, seen = [], [], [], set()
    for source, dest_name, dest_id in plan:
        if dest_id == "12" and not a.with_drivers:
            skipped.append((source, "the miniport and its reminder belong on the driver volume"))
            continue
        if dest_id not in ("10", "11", "12"):
            skipped.append((dest_name, "destination %s is not this session's business" % dest_id))
            continue
        if dest_name.lower() in seen:
            continue
        if dest_name.lower() in omit:
            # Withholding a file the INF names is a deliberate experiment, so it is named in the
            # output rather than passed over: a package that is short a file by accident and one that
            # is short by request look identical in the volume otherwise.
            skipped.append((dest_name, "omitted by request"))
            continue
        seen.add(dest_name.lower())
        src = available.get(source.lower()) or available.get(dest_name.lower())
        if src is None:
            missing.append(source)
            continue
        entries.append((dest_name, dest_id, src, src.stat().st_size, sha_of(src)))
    print(f"the INF's [{section}] wants {len(plan)} file(s); {len(entries)} are placeable here")
    for name in missing:
        print("  MISSING from the package: %s" % name, file=sys.stderr)
    if missing:
        raise Bad("the package does not carry %s, and a display install that omits part of its own "
                  "set is the failure this step exists to prevent" % ", ".join(sorted(set(missing))))

    root = Path(a.root) if a.root else None
    if a.check:
        if root is None:
            raise Bad("--check needs the mounted volume root as its argument")
        short = [name for name, dest_id, _src, _size, digest in entries
                 if not volume_path(root, dest_id, name).is_file()
                 or sha_of(volume_path(root, dest_id, name)) != digest]
        print("checked %d of %d file(s) against the volume" % (len(entries) - len(short), len(entries)))
        for name in short:
            print("  not on the volume, or not the same bytes: %s" % name)
        print("verdict: %s" % ("PACKAGE-PLACED" if not short else "PACKAGE-INCOMPLETE"))
        return 0 if not short else 1

    if not a.apply:
        for name, dest_id, _src, size, digest in entries:
            print("  %-22s -> %-16s %9d  %s"
                  % (name, DEST_NAMES.get(dest_id, dest_id), size, digest[:12]))
        for name, why in skipped:
            print("  skipped %-14s (%s)" % (name, why))
        print("print-only: nothing was copied; pass --apply --root <mounted volume>, or --check")
        return 0

    if root is None:
        raise Bad("--apply needs the mounted volume root as its argument")
    for name, dest_id, src, size, digest in entries:
        target = volume_path(root, dest_id, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(src.read_bytes())
        print("  placed %-22s -> %-16s %9d  %s"
              % (name, DEST_NAMES.get(dest_id, dest_id), size, digest[:12]))
    for name, why in skipped:
        print("  skipped %-14s (%s)" % (name, why))
    bad = [name for name, dest_id, _src, _size, digest in entries
           if not volume_path(root, dest_id, name).is_file()
           or sha_of(volume_path(root, dest_id, name)) != digest]
    print("read back: %d of %d files match their source digest" % (len(entries) - len(bad), len(entries)))
    if bad:
        raise Bad("these files did not read back identical: %s" % ", ".join(bad))
    return 0


# ---------- the phantom monitors ----------

# A display driver attaches a head to a monitor, and the modes it may offer come from that monitor's
# EDID. This guest carries a `Default_Monitor` under `Enum\DISPLAY` whose instances hold
# `bad_edid = 0`, beside two Plug and Play monitors that do advertise modes. A driver that attaches to
# the monitor with no modes has nothing to set, and the campaign's own record names that shape the
# phantom-head defect. Removing the phantom entries leaves PnP one family of monitors to bind.
PHANTOM = "Default_Monitor"


def phantom_monitors(h):
    """[(instance name, bad_edid value)] for the Default_Monitor devices in this hive."""
    cs = current_control_set(h)
    key = descend(h, [cs, "Enum", "DISPLAY", PHANTOM])
    found = []
    for inst in (h.node_children(key) or []) if key is not None else []:
        dp = child(h, inst, "Device Parameters")
        bad = None
        if dp is not None:
            v = value_map(h, dp).get("bad_edid")
            if v is not None:
                bad = decode(h, v)
        found.append((h.node_name(inst), bad))
    return found


def cmd_phantom_monitors(a):
    h = open_hive(a.hive, write=a.apply)
    cs = current_control_set(h)
    found = phantom_monitors(h)
    place = "%s\\Enum\\DISPLAY\\%s" % (cs, PHANTOM)
    print("%s: %d instance(s)" % (place, len(found)))
    for name, bad in found:
        print("   %-34s bad_edid=%s" % (name, bad))
    parent = descend(h, [cs, "Enum", "DISPLAY"])
    present = child(h, parent, PHANTOM) is not None
    if a.check:
        print("verdict: %s" % ("PHANTOM-PRESENT" if present else "PHANTOM-ABSENT"))
        return 0 if not present else 1
    if not a.apply:
        print("print-only: nothing removed; pass --apply, or --check to grade the hive")
        return 0
    if present:
        h.node_delete_child(child(h, parent, PHANTOM))
        h.commit(a.hive)
        print("removed %s" % place)
    after = phantom_monitors(open_hive(a.hive))
    print("read back: %d phantom instance(s) remain" % len(after))
    print("verdict: %s" % ("PHANTOM-ABSENT" if not after else "PHANTOM-PRESENT"))
    return 0 if not after else 1


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
    n = sub.add_parser("classnode",
                       help="point the card's Display class node at its own INF, values read from it")
    n.add_argument("hive")
    n.add_argument("--inf", required=True, help="the driver INF the values are read from")
    n.add_argument("--device-id", default=DEVICE_ID)
    n.add_argument("--class-key", default=None,
                   help="four-digit class subkey; the default takes the next unused one")
    n.add_argument("--record", default="classnode-before.json",
                   help="where the previous Driver value is kept, so the change is one write to undo")
    n.add_argument("--settings-section", default=SETTINGS_SECTION,
                   help="the INF section whose AddReg chain carries the display selection values")
    n.add_argument("--provenance-only", action="store_true",
                   help="write the install provenance and omit the display selection values; "
                        "measured to be a downgrade, so it has to be asked for by name")
    n.add_argument("--check", action="store_true", help="grade the hive, change nothing")
    n.add_argument("--apply", action="store_true", help="write the class node and repoint Driver")
    m = sub.add_parser("display-mode",
                       help="set the mode an attached display opens the desktop at")
    m.add_argument("hive")
    m.add_argument("--mode", required=True, help="WIDTHxHEIGHTxBITS@REFRESH, e.g. 1024x768x32@60")
    m.add_argument("--check", action="store_true", help="report the reads and change nothing")
    m.add_argument("--apply", action="store_true", help="write the profile slot")
    pkg = sub.add_parser("package",
                         help="place the user-mode files the INF's CopyFiles groups name")
    pkg.add_argument("root", nargs="?", default=None,
                     help="the mounted volume root, for --apply")
    pkg.add_argument("--inf", required=True, type=Path)
    pkg.add_argument("--section", default=None,
                     help="the INF's install section; default is the one its model entry names")
    pkg.add_argument("--package", required=True, type=Path,
                     help="the directory holding the package's files")
    pkg.add_argument("--omit", action="append", default=[], metavar="NAME",
                     help="withhold one of the files the INF names, by destination name; the omission "
                          "is reported, because a package short a file by request and one short by "
                          "accident are the same volume otherwise")
    pkg.add_argument("--with-drivers", action="store_true",
                     help="also place the destination-12 files, which this rig keeps on the channel")
    pkg.add_argument("--apply", action="store_true")
    pkg.add_argument("--check", action="store_true",
                     help="compare the volume against the package and answer PLACED or INCOMPLETE")
    ph = sub.add_parser("phantom-monitors",
                        help="remove the Default_Monitor devices that carry no EDID")
    ph.add_argument("hive")
    ph.add_argument("--check", action="store_true", help="grade the hive, change nothing")
    ph.add_argument("--apply", action="store_true", help="delete the Default_Monitor key")
    a = ap.parse_args()
    if a.cmd == "check":
        return cmd_check(a)
    if a.cmd == "graft":
        return cmd_graft(a)
    if a.cmd == "classnode":
        return cmd_classnode(a)
    if a.cmd == "display-mode":
        return cmd_display_mode(a)
    if a.cmd == "package":
        return cmd_package(a)
    if a.cmd == "phantom-monitors":
        return cmd_phantom_monitors(a)
    ap.print_help()
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Bad as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
