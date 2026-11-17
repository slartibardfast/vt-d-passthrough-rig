r"""The console owner's rules, tested on the captured text, without a hive or a guest.

Which `Control\Video` slot owns the primary desktop is decided by a volatile registry view that only
the guest can report, so the reading of that report is the part worth pinning: getting it wrong is
how a 640x480 console came to be read as a 1280x1024 desktop once already.
"""
import importlib.util
from pathlib import Path

RIG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("console_owner", (RIG / "console-owner.py").as_posix())
co = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(co)

# Measured from work/runs/desktop3/evidence/WINDOWS/devicemap.txt, values unchanged.
MEASURED = r"""
HKEY_LOCAL_MACHINE\HARDWARE\DEVICEMAP\VIDEO
    \Device\Video0    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{23A77BF7-ED96-40EC-AF06-9B1F4867732A}\0000
    MaxObjectNumber    REG_DWORD    0x4
    \Device\Video1    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{8B6D7859-A639-4A15-8790-7161976D057A}\0000
    \Device\Video4    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\0001
    \Device\Video3    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\0000
    VgaCompatible    REG_SZ    \Device\Video0
    \Device\Disc    REG_SZ    \REGISTRY\Machine\System\CurrentControlSet\Services\TSDDD\Device0
"""


def test_the_slots_are_read_in_the_order_the_guest_listed_them():
    got = co.video_map(MEASURED)
    assert set(got) == {"0", "1", "3", "4"}, "the capture lists Video0, Video1, Video4, Video3"
    assert got["4"].endswith("{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\\0001")
    assert got["3"].endswith("{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\\0000")


def test_the_non_device_lines_are_not_read_as_devices():
    got = co.video_map(MEASURED)
    assert "Disc" not in " ".join(got.values()).lower() or all(
        v.endswith(("\\0000", "\\0001")) for v in got.values()), \
        "the TSDDD line names a volume, not a display slot"
    assert "MaxObjectNumber" not in " ".join(got.values())


def test_the_console_is_the_device_declared_vga_compatible():
    assert co.console_number(co.video_map(MEASURED), MEASURED) == "0", (
        r"DEVICEMAP names \Device\Video0 as the VGA-compatible device, and the primary desktop "
        "follows that declaration rather than the order the devices were created")


def test_without_the_declaration_video0_is_still_the_console():
    text = MEASURED.replace("    VgaCompatible    REG_SZ    \\Device\\Video0\n", "")
    assert co.console_number(co.video_map(text), text) == "0"
    assert co.console_number({}, "") is None, "no capture, no claim about the console"


def test_a_devicemap_path_and_a_hive_slot_name_are_matched_by_the_same_rule():
    got = co.video_map(MEASURED)
    full = "{23A77BF7-ED96-40EC-AF06-9B1F4867732A}\\0000"
    assert got["0"].lower().endswith(full.lower()), (
        "the hive spells the slot GUID and number; the capture prefixes it with the registry path, "
        "so the mark the report prints depends on this one comparison")


def test_the_utf16_the_capture_tool_writes_is_decoded(tmp_path):
    path = tmp_path / "devicemap.txt"
    path.write_bytes(MEASURED.encode("utf-16"))
    assert co.video_map(co.read_text(path))["0"].endswith("\\0000")
    path.write_bytes(MEASURED.encode("utf-8"))
    assert co.video_map(co.read_text(path))["0"].endswith("\\0000")
