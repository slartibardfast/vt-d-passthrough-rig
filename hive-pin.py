#!/usr/bin/env python3
"""Edit a guest's registry hive offline, for the two facts a running guest
cannot be trusted to get right on its own: which volume owns which drive letter,
and whether the machine logs itself in.

Why offline: the letters a session shows are decided at boot from
SYSTEM\\MountedDevices, and the only deterministic fix is to write the entry for
the volume that should own the letter. The identity comes from the disk images
themselves, never from a transcription: for a partition on an MBR disk Windows
stores struct.pack("<I", disk signature) + struct.pack("<Q", partition byte
offset), and that is the value a \\DosDevices\\X: name points at. Matching on
those bytes is what turns "D: is the CSMWrap partition, E: is the driver volume"
from a reading of a screenshot into a measurement.

Both subcommands fail closed. `letters` refuses to invent an identity: the
volume must already appear in the hive, because a wrong guess silently retargets
a drive letter onto the wrong volume, which is the defect this tool exists to
remove. `autologon` refuses a Winlogon key that is missing Shell or Userinit, and
prints only the password's length.

The hive binding exposes no single-value delete, so MountedDevices is rewritten
whole: every value not being pinned is carried through with its original type
and bytes.

Usage:
  hive-pin.py letters   <system-hive> <driver-image> [--letter D] [--print-only]
  hive-pin.py autologon <software-hive> <password> [--user NAME] [--print-only]
"""
import argparse
import struct
import sys

import hivex

REG_SZ = 1
REG_BINARY = 3


def mbr_disk_signature(image):
    with open(image, "rb") as f:
        f.seek(0x1B8)
        return struct.unpack("<I", f.read(4))[0]


def first_partition_offset(image):
    """Byte offset of the first MBR partition, read from the partition table."""
    with open(image, "rb") as f:
        f.seek(0x1BE)
        entry = f.read(16)
    start = struct.unpack("<I", entry[8:12])[0]
    if start == 0:
        raise SystemExit("%s has no first partition entry; refusing to guess an offset" % image)
    return start * 512


def identity(image):
    return struct.pack("<I", mbr_disk_signature(image)) + struct.pack("<Q", first_partition_offset(image))


def utf16(value):
    return value.encode("utf-16-le") + b"\x00\x00"


def open_node(path, parts, write=False):
    h = hivex.Hivex(path, write=write)
    node = h.root()
    for part in parts:
        node = h.node_get_child(node, part)
        if node == -1:
            raise SystemExit("%s has no %s node" % (path, "\\".join(parts)))
    return h, node


def values_of(h, node):
    """Every value under a node, as {name: (type, bytes)}."""
    return {h.value_key(vh): h.value_value(vh) for vh in h.node_values(node)}


def letter_table(values):
    """{letter: blob} for the \\DosDevices\\X: entries that carry a volume identity.

    Every MountedDevices value is stored as REG_BINARY, including the ones whose
    bytes are a UTF-16 device path, so the type does not tell them apart: a
    volume identity is 12 bytes (4-byte disk signature plus an 8-byte partition
    offset) and a device path is not.
    """
    prefix, width = "\\DosDevices\\", len("\\DosDevices\\X:")
    table = {}
    for name, (typ, data) in values.items():
        if not (name.startswith(prefix) and len(name) == width and name.endswith(":") and data):
            continue
        letter = name[-2]
        if len(data) == 12:
            table[letter] = data
    return table


def describe(values, name):
    """A printable form of one value, for the run record."""
    typ, data = values[name]
    if typ == REG_BINARY:
        return "binary " + data.hex()
    try:
        text = data.decode("utf-16-le").strip("\x00")
        if all(32 <= ord(c) < 127 for c in text):
            return "text " + text
    except Exception:
        pass
    return "type %d, %d bytes" % (typ, len(data))


def cmd_letters(a):
    h, node = open_node(a.hive, ["MountedDevices"], write=True)
    values = values_of(h, node)
    want = a.letter.upper()
    target = identity(a.driver_image)
    table = letter_table(values)

    print("driver volume identity: disk signature %#010x, partition at byte %d" %
          (struct.unpack("<I", target[:4])[0], struct.unpack("<Q", target[4:12])[0]))
    print("drive letters this hive pins now:")
    for letter in sorted(table):
        print("  %s: %s%s" % (letter, table[letter].hex(),
                              "   <- the driver volume" if table[letter] == target else ""))

    holders = sorted(l for l, blob in table.items() if blob == target)
    if not holders:
        raise SystemExit("the driver volume is not in this hive; refusing to pin a letter onto a "
                         "volume Windows has never mounted, because a wrong identity here retargets "
                         "the letter silently")
    if holders == [want]:
        print("already pinned: %s: is the driver volume and no other letter claims it" % want)
        return 0

    # A plan entry is (action, value name, bytes to write or None, why). drop and
    # set both remove the name's old entry from the rebuilt list; set adds bytes.
    plan = []
    for letter in holders:
        if letter != want:
            plan.append(("drop", "\\DosDevices\\%s:" % letter, None,
                         "same volume as %s:, and one volume with two letters is how the wrong "
                         "one gets used" % want))
    if want in table:
        plan.append(("set", "\\DosDevices\\%s:" % want, target,
                     "replaces %s (that volume keeps no pinned letter, so Windows gives it any "
                     "free one at boot)" % table[want].hex()))
    else:
        plan.append(("set", "\\DosDevices\\%s:" % want, target, "newly pinned"))

    for action, name, data, why in plan:
        current = describe(values, name) if name in values else "absent"
        shown = "removed" if action == "drop" else data.hex()
        print("%-5s %-24s %s -> %s\n      %s" % (action, name, current, shown, why))
    if a.print_only:
        print("print-only: nothing written")
        return 0

    removed = {name for _, name, _, _ in plan}
    rebuilt = [{"key": name, "t": typ, "length": len(data), "value": data}
               for name, (typ, data) in values.items() if name not in removed]
    for _, name, data, _ in plan:
        if data is not None:
            rebuilt.append({"key": name, "t": REG_BINARY, "length": len(data), "value": data})
    h.node_set_values(node, rebuilt)
    h.commit(a.hive)

    h2, node2 = open_node(a.hive, ["MountedDevices"])
    after = letter_table(values_of(h2, node2))
    print("read-back: %s" % ", ".join("%s:=%s" % (l, after[l].hex()) for l in sorted(after)))
    if after.get(want) != target:
        raise SystemExit("read-back failed: %s: is not the driver volume" % want)
    dupes = [l for l, blob in after.items() if blob == target]
    if dupes != [want]:
        raise SystemExit("read-back failed: the driver volume is pinned at %s" % ", ".join(dupes))
    print("pinned: %s: is the driver volume, and it is the only letter that claims it" % want)
    return 0


def cmd_autologon(a):
    parts = ["Microsoft", "Windows NT", "CurrentVersion", "Winlogon"]
    h, node = open_node(a.hive, parts, write=True)
    values = values_of(h, node)
    if "Shell" not in values or "Userinit" not in values:
        raise SystemExit("Winlogon is missing Shell or Userinit; refusing to enable autologon on a "
                         "logon path that is already broken")

    plan = [("AutoAdminLogon", utf16("1"), "1"),
            ("DefaultUserName", utf16(a.user), a.user),
            ("DefaultPassword", utf16(a.password), "%d chars (not printed)" % len(a.password))]
    for name, _, shown in plan:
        print("%-16s %s -> %s" % (name, "absent" if name not in values else "replaced", shown))
    if a.print_only:
        print("print-only: nothing written")
        return 0

    for name, blob, _ in plan:
        h.node_set_value(node, {"key": name, "t": REG_SZ, "length": len(blob), "value": blob})
    h.commit(a.hive)

    h2, node2 = open_node(a.hive, parts)
    again = values_of(h2, node2)
    for name, blob, shown in plan:
        if name not in again or again[name][1] != blob:
            raise SystemExit("read-back failed for %s" % name)
    print("read-back: AutoAdminLogon=%s DefaultUserName=%s DefaultPassword set" %
          (again["AutoAdminLogon"][1].decode("utf-16-le").strip("\x00"),
           again["DefaultUserName"][1].decode("utf-16-le").strip("\x00")))
    return 0


def cmd_harderror(a):
    """Stop Windows raising a modal dialog for a drive that has no media.

    Measured on 2026-11-14: the KVM's empty removable made cmd answer any probe
    of that letter with a hard-error dialog, "There is no disk in the drive", and
    that dialog is what stalled every version of the letter witness: the batch,
    the console, and the survey all waited on a button nobody was watching. The
    documented setting is HKLM\\SYSTEM\\...\\Control\\Windows\\ErrorMode, and 2
    means do not raise the dialog. An earlier revision wrote HardErrorMode, which
    is not that value: a boot with HardErrorMode=2 and ErrorMode=0 still raised
    the modal on 2026-11-14, so ErrorMode is what this writes now.

    It is written into every ControlSet in the hive rather than a chosen one:
    CurrentControlSet is a link that only exists in a booted system, and the
    honest offline move is to make the setting agree across the copies.
    """
    h = hivex.Hivex(a.hive, write=True)
    root = h.root()
    written = []
    for child in h.node_children(root):
        child_name = h.node_name(child)
        if not child_name.startswith("ControlSet"):
            continue
        control = h.node_get_child(child, "Control")
        node = h.node_get_child(control, "Windows") if control != -1 else -1
        if node == -1:
            print("%s has no Control\\Windows; skipping it" % child_name)
            continue
        value = {"key": "ErrorMode", "t": 4, "length": 4, "value": struct.pack("<I", 2)}
        h.node_set_value(node, value)
        written.append(child_name)
    if not written:
        raise SystemExit("no ControlSet*\\Control\\Windows found; refusing to claim the "
                         "setting was made")
    h.commit(a.hive)
    check = hivex.Hivex(a.hive)
    for child_name in written:
        control = check.node_get_child(check.root(), child_name)
        control = check.node_get_child(control, "Control")
        node = check.node_get_child(control, "Windows")
        vh = check.node_get_value(node, "ErrorMode")
        got = struct.unpack("<I", check.value_value(vh)[1])[0]
        if got != 2:
            raise SystemExit("read-back failed for %s: ErrorMode=%d" % (child_name, got))
        print("%s\\Control\\Windows ErrorMode = 2 (no disk dialog)" % child_name)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    l = sub.add_parser("letters")
    l.add_argument("hive")
    l.add_argument("driver_image")
    l.add_argument("--letter", default="D")
    l.add_argument("--print-only", action="store_true")
    e = sub.add_parser("harderror")
    e.add_argument("hive")
    g = sub.add_parser("autologon")
    g.add_argument("hive")
    g.add_argument("password")
    g.add_argument("--user", default="Administrator")
    g.add_argument("--print-only", action="store_true")
    a = ap.parse_args()
    if a.cmd == "letters":
        return cmd_letters(a)
    if a.cmd == "harderror":
        return cmd_harderror(a)
    return cmd_autologon(a)


if __name__ == "__main__":
    sys.exit(main())
