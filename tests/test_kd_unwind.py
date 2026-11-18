"""The unwinder, checked against functions whose prologues are already known by hand.

A stack walk is only as good as the frame layout it uses, and this campaign already paid for getting
that wrong: an assumption about a frame's size produced a confident, incorrect conclusion about where
a driver had stopped. So the tests here compare the decode with the instructions at the same addresses:
a `sub $0x58,%rsp` has to come back as an `alloc-small` frame of 0x58, and a `mov %rdi,0x78(%rsp)` as a
`save-nonvol` at that offset.
"""
import importlib.util
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("kd_unwind", (RIG / "kd-unwind.py").as_posix())
ku = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ku)

IMAGE = RIG / "work" / "package" / "ati2cqag.dll"


def image():
    if not IMAGE.is_file():
        pytest.skip("the placed ati2cqag.dll is not on this machine")
    return ku.Image(IMAGE)


def test_a_function_is_found_by_an_address_inside_it():
    img = image()
    begin, end, unwind = img.function_at(0x1442C)
    assert (begin, end) == (0x14380, 0x14434), "the return address must land in the caller, not past it"
    assert unwind != 0


def test_the_frame_size_matches_the_instruction_that_makes_it():
    img = image()
    begin, _end, unwind = img.function_at(0x26AD0)
    ui = ku.unwind_info(img, unwind)
    assert ui["prolog"] == 0x22, "the prolog the record claims"
    assert ku.frame_size(ui) == 0x58, "and the `sub $0x58,%rsp` found at the entry"
    assert any(c["register"] == "rdi" and c["value"] == 0x78 for c in ui["codes"]), \
        "the `mov %rdi,0x78(%rsp)` in the prolog has to appear as a save at that offset"


def test_a_large_allocation_is_read_from_the_extra_slot():
    img = image()
    begin, _end, unwind = img.function_at(0x14380)
    ui = ku.unwind_info(img, unwind)
    assert ku.frame_size(ui) == 0x88, "`sub $0x88,%rsp` is too big for the small form"


def test_an_address_outside_every_entry_is_reported_as_such():
    img = image()
    assert img.function_at(0x0) is None, "a header rva belongs to no function"


def test_the_opcode_byte_is_the_second_one():
    """The first version read the prolog offset as the opcode and produced opcodes like 0x0F."""
    img = image()
    begin, _end, unwind = img.function_at(0x24830)
    ui = ku.unwind_info(img, unwind)
    names = [c["name"] for c in ui["codes"]]
    assert "save-nonvol" in names
    assert not any(n.startswith("op") for n in names), names
    assert [c["register"] for c in ui["codes"] if c["name"] == "save-nonvol"] == ["rdi", "rsi", "rbx"]
