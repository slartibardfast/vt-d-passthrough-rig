"""Naming frames by the image that actually contains them, tested where the ranges are close.

The boot that produced this tool mapped win32k, the display driver and the 2D component into
neighbouring session-space ranges, and a pointer at the driver's base plus 0xA8882 is past the
driver's own SizeOfImage and inside the next image. A range one image too wide is how a frame gets
attributed to the wrong module, so the boundary is the thing tested.
"""
import importlib.util
from pathlib import Path

RIG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kd_stack_map", (RIG / "kd-stack-map.py").as_posix())
ks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ks)


def test_a_frame_is_named_by_the_image_whose_size_covers_it(tmp_path):
    stack = bytearray(0x40)
    values = [0xFFFFF97FFF000010,   # inside win32k
              0xFFFFF97FFF44FFFF,   # last byte of win32k
              0xFFFFF97FFF52B882,   # past the driver, inside the 2D component
              0xDEADBEEFDEADBEEF]   # not an image at all
    for i, v in enumerate(values):
        stack[i * 8:i * 8 + 8] = v.to_bytes(8, "little")
    table = {"win32k.sys": (0xFFFFF97FFF000000, 0x45C000),
             "ati2dvag.dll": (0xFFFFF97FFF483000, 0x84000),
             "ati2cqag.dll": (0xFFFFF97FFF507000, 0xDC000)}
    got = ks.frames(bytes(stack), 0xFFFFFADFAC95F000, table)
    named = [(name, off) for _, name, off in got]
    assert ("win32k.sys", 0x10) in named
    assert ("win32k.sys", 0x44FFFF) in named, "the last byte before the image ends is still its own"
    assert ("ati2cqag.dll", 0x24882) in named, "past the driver's end, so the next image's frame"
    assert len(got) == 3, "an address in no image is reported as no frame rather than guessed"


def test_the_size_comes_from_the_image_not_from_the_caller(tmp_path):
    """A caller supplies a base; the mapped extent is the file's own business."""
    pe = bytearray(0x400)
    pe[0:2] = b"MZ"
    pe[0x3C:0x40] = (0x80).to_bytes(4, "little")
    pe[0x80:0x84] = b"PE\0\0"
    pe[0x80 + 0x18 + 0x38:0x80 + 0x18 + 0x3C] = (0x84000).to_bytes(4, "little")
    path = tmp_path / "image.dll"
    path.write_bytes(bytes(pe))
    assert ks.size_of_image(path) == 0x84000
    table = ks.image_table(["0xFFFFF97FFF483000=%s" % path])
    assert table["image.dll"] == (0xFFFFF97FFF483000, 0x84000)
