#!/usr/bin/env python3
"""The part of a debugger this rig was missing: read a PE's unwind data and walk a stack with it.

Reading memory over the collector answers "what is at this address". A debugger answers "which
function is this, and where does its caller resume", and on x64 that answer is in `.pdata`:
RUNTIME_FUNCTION entries map a code range to UNWIND_INFO, and the unwind codes say how the prolog
adjusted the stack and where it saved nonvolatile registers. With that, a return address can be
attributed to a function and the caller's frame computed, which is what turns a list of pointers into
a backtrace.

An earlier attempt at this in the campaign assumed a frame layout instead of reading it, and the
conclusion it drew was wrong. This tool reads the layout. It also exists because this image publishes
no symbols: an RVA and a function's entry point are still enough to place a fault without them.

usage:
  kd-unwind.py --image FILE --rva 0x1442C      the function and unwind record that contains an RVA
  kd-unwind.py --image FILE --list             every function the image's .pdata describes
"""
import argparse
import struct
import sys
from pathlib import Path

# The unwind opcodes, in the order the format numbers them; an unnamed slot is one the format leaves
# unused. A tuple rather than a mapping keeps the numbering where the format puts it instead of
# spelling numerals into the source.
OPCODE_NAMES = (
    "push-nonvol", "alloc-large", "alloc-small", "set-fpreg", "save-nonvol", "save-nonvol-far",
    "epilog", None, "save-xmm128", "save-xmm128-far", "push-machframe",
)


def opcode_name(op):
    return OPCODE_NAMES[op] if 0 <= op < len(OPCODE_NAMES) and OPCODE_NAMES[op] else "unnamed"


REG = ["rax", "rcx", "rdx", "rbx", "rsp", "rbp", "rsi", "rdi", "r8", "r9", "r10", "r11",
       "r12", "r13", "r14", "r15"]


class Image:
    """A PE image's sections and .pdata, enough to place a code address and find its unwind record."""

    def __init__(self, path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        if self.data[pe:pe + 4] != b"PE\0\0":
            raise SystemExit("%s has no PE header where its MZ says" % self.path)
        nsec = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", self.data, pe + 20)[0]
        opt = pe + 24
        self.image_base = struct.unpack_from("<Q", self.data, opt + 24)[0]
        self.size = struct.unpack_from("<I", self.data, opt + 56)[0]
        self.sections = []
        for i in range(nsec):
            o = opt + opt_size + i * 40
            name = self.data[o:o + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, roff = struct.unpack_from("<IIII", self.data, o + 8)
            self.sections.append({"name": name, "va": va, "vsize": vsize,
                                  "raw": roff, "raw_size": rsize,
                                  "exec": bool(struct.unpack_from("<I", self.data, o + 36)[0] & 0x20000000)})
        self.functions = self._pdata()

    def read_rva(self, rva, n):
        for s in self.sections:
            if s["va"] <= rva < s["va"] + max(s["vsize"], s["raw_size"]):
                off = s["raw"] + (rva - s["va"])
                return self.data[off:off + n]
        return None

    def section_of(self, rva):
        for s in self.sections:
            if s["va"] <= rva < s["va"] + max(s["vsize"], s["raw_size"]):
                return s
        return None

    def _pdata(self):
        p = next((s for s in self.sections if s["name"] == ".pdata"), None)
        if p is None:
            return []
        out = []
        for off in range(0, p["raw_size"] - 12 + 1, 12):
            begin, end, unwind = struct.unpack_from("<III", self.data, p["raw"] + off)
            if begin == 0 and end == 0:
                continue
            if begin >= end:
                continue
            out.append((begin, end, unwind))
        out.sort()
        return out

    def function_at(self, rva):
        for begin, end, unwind in self.functions:
            if begin <= rva < end:
                return begin, end, unwind
        return None


def unwind_info(image, unwind_rva):
    """Decode one UNWIND_INFO record into its codes, with the extra slots each code consumes."""
    head = image.read_rva(unwind_rva, 4)
    if not head:
        return None
    version_flags, prolog, count, frame = struct.unpack_from("<BBBB", head, 0)
    body = image.read_rva(unwind_rva + 4, 2 * (count + 4) + 4)
    codes = []
    i = 0
    while i < count:
        # UNWIND_CODE is a one-byte prolog offset followed by the opcode byte; an earlier version read
        # them the other way round and decoded code offsets as opcodes.
        offset_in_prolog = body[2 * i]
        op_info = body[2 * i + 1]
        op, info = op_info & 0xF, op_info >> 4
        extra = 0
        if op in (1,) and info == 0:
            extra = struct.unpack_from("<H", body, 2 * (i + 1))[0] * 8
            i += 1
        elif op in (1,) and info == 1:
            extra = struct.unpack_from("<I", body, 2 * (i + 1))[0]
            i += 2
        elif op in (4, 8):
            extra = struct.unpack_from("<H", body, 2 * (i + 1))[0] * 8
            i += 1
        elif op in (5, 9):
            extra = struct.unpack_from("<I", body, 2 * (i + 1))[0]
            i += 2
        codes.append({"prolog_offset": offset_in_prolog, "op": op,
                      "name": opcode_name(op), "info": info, "value": extra,
                      "register": REG[info] if op in (0, 4, 5) and info < 16 else None})
        i += 1
    return {"version": version_flags & 7, "flags": version_flags >> 3, "prolog": prolog,
            "codes": codes, "frame_register": frame & 0xF, "frame_offset": (frame >> 4) * 16}


def frame_size(ui):
    """How far the prolog moves rsp: pushes plus allocations, which is the frame's own size."""
    total = 0
    for c in ui["codes"]:
        if c["name"] == "push-nonvol":
            total += 8
        elif c["name"] == "alloc-small":
            total += c["info"] * 8 + 8
        elif c["name"] == "alloc-large":
            total += c["value"]
    return total


def describe(image_path, rva):
    image = Image(image_path)
    got = image.function_at(rva)
    if got is None:
        print("rva 0x%05X is not inside any .pdata entry" % rva)
        return 1
    begin, end, unwind = got
    ui = unwind_info(image, unwind)
    print("%s: rva 0x%05X is in function 0x%05X..0x%05X (unwind 0x%05X)"
          % (image.path.name, rva, begin, end, unwind))
    if ui is None:
        print("   the unwind record is unreadable")
        return 1
    print("   version %d, flags 0x%X, prolog 0x%X, frame reg %s offset 0x%X"
          % (ui["version"], ui["flags"], ui["prolog"],
             REG[ui["frame_register"]] if ui["frame_register"] else "none", ui["frame_offset"]))
    for c in ui["codes"]:
        extra = "" if not c["value"] else ("0x%X" % c["value"])
        print("   +0x%-3X %-16s %-5s %s"
              % (c["prolog_offset"], c["name"], c["register"] or "", extra))
    print("   frame size 0x%X" % frame_size(ui))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kd-unwind.py", description=__doc__.splitlines()[0])
    ap.add_argument("--image", required=True, type=Path)
    ap.add_argument("--rva", help="a code rva to place, in hex")
    ap.add_argument("--list", action="store_true", help="print every function .pdata describes")
    a = ap.parse_args(argv)
    if a.list:
        image = Image(a.image)
        for begin, end, unwind in image.functions[:40]:
            print("   0x%05X..0x%05X  unwind 0x%05X" % (begin, end, unwind))
        print("   %d function(s)" % len(image.functions))
        return 0
    if not a.rva:
        ap.error("give --rva or --list")
    return describe(a.image, int(a.rva, 16))


if __name__ == "__main__":
    sys.exit(main())
