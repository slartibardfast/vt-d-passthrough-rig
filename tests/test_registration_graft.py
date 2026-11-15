"""The registration grader and its INF reads, with no hive and no hardware.

The point of these tests is the distinction the campaign spent a month missing: a
class key that names the ATI driver and a per-adapter key that names the vga one. A
tool that only looked at the class key called that state ready. These tests hold the
line that it is not.
"""

import textwrap
from types import SimpleNamespace

import pytest

import registration_graft as rg

REAL_INF = textwrap.dedent(r"""
    [Version]
    Class=Display

    [Strings]
    REG_SZ         ="0x00000000"
    REG_MULTI_SZ   ="0x00010000"
    REG_DWORD      ="0x00010001"

    [ati2mtag_SoftwareDeviceSettings]
    HKR,, DDC2Disabled,                         %REG_DWORD%,    0
    HKR,, InstalledDisplayDrivers,              %REG_MULTI_SZ%, ati2dvag
    HKR,, MultiFunctionSupported,               %REG_DWORD%,    1
    HKR,, VgaCompatible,                        %REG_DWORD%,    0
    HKR,,"Adaptive De-interlacing",             %REG_DWORD%,    1

    [some_other_section]
    HKR,Settings,InstalledDisplayDrivers,       %REG_MULTI_SZ%, vga, framebuf
    HKLM,Software\Foo,Bar,                      %REG_SZ%,       baz
    """).lstrip()


def inf(tmp_path, text=REAL_INF, name="CA178340.inf"):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


SRC = {
    "control_set": "ControlSet001",
    "enum_path": r"ControlSet001\Enum\PCI\VEN_1002&DEV_67B0&SUBSYS_E285174B&REV_00\3&267a616a&0&20",
    "class_key": "0001",
    "videoid": "9AEF0D0F-2356-4E54-9833-53DAB38FD7CC",
}

VGA_SET = ["vga", "framebuf", "vga256", "vga64k"]


def facts(**over):
    base = {
        "control_set": "ControlSet001",
        "enum_service": "vga",
        "class_key": "0001",
        "videoid": "9AEF0D0F-2356-4E54-9833-53DAB38FD7CC",
        "class_infpath": "display.inf",
        "class_root_drivers": None,
        "class_root_vgacompat": None,
        "class_settings_drivers": list(VGA_SET),
        "class_settings_vgacompat": 1,
        "dev_drivers": list(VGA_SET),
        "dev_vgacompat": 1,
        "dev_chip_type": "AMD ATOMBIOS",
        "dev_service": "vga",
        "svc_start": None,
        "svc_imagepath": None,
    }
    base.update(over)
    return base


def test_inf_reader_maps_the_vendors_data_styles(tmp_path):
    got = rg.inf_settings(inf(tmp_path), "ati2mtag_SoftwareDeviceSettings")
    assert got["InstalledDisplayDrivers"][1] == ["ati2dvag"]
    assert got["VgaCompatible"][1] == 0
    assert got["InstalledDisplayDrivers"][0] == rg.REG_MULTI_SZ
    assert got["VgaCompatible"][0] == rg.REG_DWORD


def test_inf_reader_provenance_names_the_line(tmp_path):
    got = rg.inf_settings(inf(tmp_path), "ati2mtag_SoftwareDeviceSettings")
    path, lineno = got["InstalledDisplayDrivers"][2][len("inf:"):].rsplit(":", 1)
    line = open(path).read().splitlines()[int(lineno) - 1]
    assert "InstalledDisplayDrivers" in line and "ati2dvag" in line


def test_inf_reader_ignores_a_subkey_qualified_twin(tmp_path):
    # The vga set in the fixture is written with a Settings subkey by another section.
    # A root-level read must not pick it up: conflating the two is the defect itself.
    got = rg.inf_settings(inf(tmp_path), "ati2mtag_SoftwareDeviceSettings")
    assert got["InstalledDisplayDrivers"][1] == ["ati2dvag"]
    with pytest.raises(rg.Bad, match="no section"):
        rg.inf_settings(inf(tmp_path), "ati2mtag_Not_There")


def test_inf_reader_refuses_to_guess_a_missing_value(tmp_path):
    thin = REAL_INF.replace("HKR,, VgaCompatible,                        %REG_DWORD%,    0\n", "")
    with pytest.raises(rg.Bad, match="VgaCompatible"):
        rg.inf_settings(inf(tmp_path, thin), "ati2mtag_SoftwareDeviceSettings")


def test_todays_disk_is_vga_only():
    verdict, why = rg.grade(facts())
    assert verdict == "VGA-ONLY"
    assert any("ati2mtag is absent" in w or "is absent" in w for w in why)
    assert any("never ati2mtag" in w for w in why)


def test_octobers_bound_state_is_not_ready_and_says_why():
    # October: the service and the devnode were pointed at the miniport, and the miniport
    # attached (its ChipType is in the key), yet the per-adapter key still named vga.
    f = facts(enum_service="ati2mtag", svc_start=1,
              svc_imagepath=r"\??\D:\drv\ati2mtag.sys",
              class_infpath="CA178340.inf", class_root_drivers=["ati2dvag"],
              class_root_vgacompat=0, dev_chip_type="AMD Radeon Graphics Processor (0x67B0)")
    verdict, why = rg.grade(f)
    assert verdict == "MINIPORT-ONLY"
    assert any("per-adapter key" in w for w in why)
    assert not any("absent" in w for w in why)


def test_a_service_that_is_not_boot_start_is_not_ready():
    verdict, _ = rg.grade(facts(enum_service="ati2mtag", svc_start=3))
    assert verdict == "VGA-ONLY"


def test_the_grafted_state_is_ready():
    f = facts(enum_service="ati2mtag", svc_start=1, svc_imagepath=r"\??\D:\drv\ati2mtag.sys",
              class_infpath="CA178340.inf", class_root_drivers=["ati2dvag"],
              class_root_vgacompat=0, class_settings_drivers=["ati2dvag"],
              class_settings_vgacompat=0, dev_drivers=["ati2dvag"], dev_vgacompat=0,
              dev_service="ati2mtag")
    verdict, why = rg.grade(f)
    assert verdict == "BOUND-READY"
    assert why == []


def test_writes_cover_both_channels_and_each_says_why():
    a = SimpleNamespace(image_path=r"\??\D:\drv\ati2mtag.sys",
                        device_description="AMD Radeon R9 290X / 390X")
    drivers = (rg.REG_MULTI_SZ, ["ati2dvag"], "inf:x:1")
    compat = (rg.REG_DWORD, 0, "inf:x:2")
    writes = rg.build_writes(SRC, a, drivers, compat)
    targets = {path for path, _name, _t, _v, _w in writes}
    vid = SRC["videoid"]
    assert rf"{SRC['control_set']}\Control\Video\{{{vid}}}\0000" in targets
    assert rf"{SRC['control_set']}\Control\Video\{{{vid}}}\Video" in targets
    assert rf"{SRC['control_set']}\Control\Class\{rg.DISPLAY_CLASS}\{SRC['class_key']}\Settings" in targets
    assert SRC["enum_path"] in targets
    assert all(why.strip() for _p, _n, _t, _v, why in writes)


def test_writes_refuse_without_a_video_id():
    src = dict(SRC, videoid=None)
    a = SimpleNamespace(image_path="x", device_description="y")
    with pytest.raises(rg.Bad, match="VideoID"):
        rg.build_writes(src, a, (rg.REG_MULTI_SZ, ["ati2dvag"], ""), (rg.REG_DWORD, 0, ""))


def test_multi_string_encoding_is_what_the_registry_holds():
    raw = rg.encode(rg.REG_MULTI_SZ, ["ati2dvag"])
    assert raw == "ati2dvag".encode("utf-16-le") + b"\0\0"
    assert rg.encode(rg.REG_MULTI_SZ, ["ati2dvag", "ati2dvag2"]).count(b"\0\0") >= 2
    assert rg.encode(rg.REG_DWORD, 0) == b"\0\0\0\0"
    assert rg.encode(rg.REG_SZ, "Video").endswith(b"\0\0")
