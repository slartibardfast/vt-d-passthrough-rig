#!/usr/bin/env python3
"""Name the frames on a frozen thread's kernel stack, by the image each address falls in.

A KD freeze leaves the machine readable, and the stack is the one place the call chain survives. The
addresses alone are not enough: this boot mapped `win32k.sys` at 0xFFFFF97FFF000000 with
`SizeOfImage` 0x45C000, `ati2dvag.dll` at 0xFFFFF97FFF483000 with 0x84000, and `ati2cqag.dll` at
0xFFFFF97FFF507000 with 0xDC000, so a pointer that looks like "the display driver" because it is in
session space can belong to any of the three. The sizes come from the images themselves, never from a
guess, because a range that is one image too wide is how a frame gets misattributed.

Session-space images cannot be read over this link (the collector's read answers 0xC0000001), so the
bases are passed in and the sizes are read from the files.

usage: kd-stack-map.py --stack FILE --start HEXADDR --image base=path [--image base=path ...]
"""
import argparse
import struct
import sys
from pathlib import Path


def size_of_image(path):
    """The mapped size a PE announces, which is what bounds its range at run time."""
    data = Path(path).read_bytes()[:0x400]
    if data[:2] != b"MZ":
        raise SystemExit("%s is not a PE file" % path)
    off = struct.unpack_from("<I", data, 0x3C)[0]
    if data[off:off + 4] != b"PE\0\0":
        raise SystemExit("%s has no PE header where its MZ says" % path)
    return struct.unpack_from("<I", data, off + 0x18 + 0x38)[0]


def image_table(pairs):
    """{name: (base, size)} from the `base=path` arguments, sized from each file."""
    table = {}
    for pair in pairs:
        base, _, path = pair.partition("=")
        if not path:
            raise SystemExit("--image takes base=path, got %r" % pair)
        table[Path(path).name] = (int(base, 16), size_of_image(path))
    return table


def frames(stack, start, table):
    """[(stack address, image name, offset)] for every pointer inside a known image."""
    out = []
    for i in range(0, len(stack) - 8, 8):
        value = int.from_bytes(stack[i:i + 8], "little")
        for name, (base, size) in table.items():
            if base <= value < base + size:
                out.append((start + i, name, value - base))
                break
    return out


def load_unwinder(path):
    """The frame-layout reader, imported by path because the tool's name has a hyphen."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("kd_unwind", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kd-stack-map.py")
    ap.add_argument("--stack", required=True, type=Path, help="the dumped stack bytes")
    ap.add_argument("--start", required=True, help="the address the dump begins at")
    ap.add_argument("--image", action="append", default=[], metavar="BASE=PATH",
                    help="an image as the boot mapped it, with the file it came from")
    ap.add_argument("--functions", action="store_true",
                    help="name the function each frame falls in, using the image's own .pdata")
    a = ap.parse_args(argv)
    table = image_table(a.image)
    print("images, bounded by their own SizeOfImage:")
    for name, (base, size) in sorted(table.items(), key=lambda kv: kv[1][0]):
        print("   %-16s 0x%016X .. 0x%016X  (0x%X)" % (name, base, base + size, size))
    start = int(a.start, 16)
    got = frames(a.stack.read_bytes(), start, table)
    if not got:
        print("\nno pointer on this stack falls inside a named image")
        return 1
    unwinder = None
    if a.functions:
        unwinder = load_unwinder(Path(__file__).resolve().parent / "kd-unwind.py")
    print("\nframes, most recent first (a stack grows down, so the lowest address is the latest):")
    for addr, name, off in got:
        detail = ""
        if unwinder is not None:
            paths = {}
            for entry in a.image:
                _base, _, _path = entry.partition("=")
                paths[Path(_path).name] = _path
            image_path = paths[name]
            img = unwinder.Image(image_path)
            placed = img.function_at(off)
            if placed:
                begin, end, unwind = placed
                ui = unwinder.unwind_info(img, unwind)
                detail = "  in function 0x%05X (frame 0x%X)" % (
                    begin, unwinder.frame_size(ui) if ui else 0)
            else:
                detail = "  outside every .pdata entry"
        print("   stack+0x%05X  %s+0x%05X%s" % (addr - start, name, off, detail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
