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


def test_a_mode_is_read_only_in_the_form_the_tool_can_write():
    import importlib

    module = importlib.import_module("registration_graft")
    assert module.parse_mode("1024x768x32@60") == (1024, 768, 32, 60)
    for bad in ("1024x768", "1024X768x32@60", "1024x768x24@60", "1024x768x32@59", "", None):
        with pytest.raises(module.Bad) as raised:
            module.parse_mode(bad)
        assert "WIDTHxHEIGHTxBITS@REFRESH" in str(raised.value), str(raised.value)


def test_the_mode_is_written_where_the_desktop_reads_it_and_to_both_entries():
    """The slot and its monitor child both carry the mode, and a mismatch between them is refused."""
    import importlib

    module = importlib.import_module("registration_graft")
    base = ("ControlSet001\\Hardware Profiles\\0001\\System\\CurrentControlSet\\Control"
            "\\VIDEO\\{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\\0000")
    writes = module.mode_writes(base, (1024, 768, 32, 60), ["Mon12345678"])
    names = [w[1] for w in writes]
    assert names.count("Attach.ToDesktop") == 1, "the desktop attachment is stated, not assumed"
    assert names.count("DefaultSettings.XResolution") == 2, "the slot and its monitor both carry it"
    slot = {w[1]: w[3] for w in writes if w[0] == base}
    assert (slot["DefaultSettings.XResolution"], slot["DefaultSettings.YResolution"],
            slot["DefaultSettings.BitsPerPel"], slot["DefaultSettings.VRefresh"]) == (1024, 768, 32, 60)
    mon = {w[1]: w[3] for w in writes if w[0].endswith("Mon12345678")}
    assert mon["DefaultSettings.YResolution"] == 768, "so a reader of either entry sees one mode"


PACKAGE_INF = textwrap.dedent("""
    [DestinationDirs]
    DefaultDestDir      = 11
    ati2mtag.Miniport   = 12  ; drivers
    ati2mtag.Display    = 11  ; system32
    ati2mtag.OGL        = 10  ; Windows

    [ati2mtag_Kaveri_Desktop]
    CopyFiles=ati2mtag.Miniport, ati2mtag.Display, ati2mtag.OGL

    [ati2mtag.Miniport]
    ati2mtag.sys
    ati2erec.dll

    [ati2mtag.Display]
    ati2dvag.dll
    ati2cqag.dll
    atiicdxx.dat
    amdpcom64.dll,atimpc64.dll

    [ati2mtag.OGL]
    atiogl.xml
""")


def test_the_package_list_is_read_from_the_inf_and_names_the_companions(tmp_path):
    """The hand install copied the display DLL and nothing else, which is a partial package."""
    inf = tmp_path / "package.inf"
    inf.write_text(PACKAGE_INF)
    plan = rg.inf_copy_plan(inf, "ati2mtag_Kaveri_Desktop")
    dests = {(src.lower(), dest.lower(), dst) for src, dest, dst in plan}
    assert ("ati2dvag.dll", "ati2dvag.dll", "11") in dests
    assert ("ati2cqag.dll", "ati2cqag.dll", "11") in dests, "the 2D component the driver pulls"
    assert ("atiicdxx.dat", "atiicdxx.dat", "11") in dests
    assert ("ati2mtag.sys", "ati2mtag.sys", "12") in dests, "the miniport goes to drivers"
    assert ("atiogl.xml", "atiogl.xml", "10") in dests, "a Windows-destination file is not system32"
    assert ("amdpcom64.dll", "atimpc64.dll", "11") in dests, "a renamed copy is still a destination"


def test_a_destination_id_is_a_place_on_the_volume_not_a_path_fragment():
    from pathlib import Path as P

    root = P("/mnt/vol")
    assert rg.volume_path(root, "11", "ati2cqag.dll") == root / "WINDOWS" / "system32" / "ati2cqag.dll"
    assert rg.volume_path(root, "10", "atiogl.xml") == root / "WINDOWS" / "atiogl.xml"
    assert rg.volume_path(root, "12", "ati2mtag.sys") == \
        root / "WINDOWS" / "system32" / "drivers" / "ati2mtag.sys", \
        "a backslash inside one component lands nowhere the read-back looks"


def test_a_check_writes_nothing_and_a_placement_is_then_placed(tmp_path):
    """A check that can create what it checks is not a check, and one did here."""
    from types import SimpleNamespace

    inf = tmp_path / "package.inf"
    inf.write_text(PACKAGE_INF)
    package = tmp_path / "package"
    package.mkdir()
    (package / "ati2dvag.dll").write_bytes(b"display driver")
    (package / "ati2cqag.dll").write_bytes(b"the 2d component")
    (package / "atiicdxx.dat").write_bytes(b"data")
    (package / "atimpc64.dll").write_bytes(b"monitor component")
    (package / "atiogl.xml").write_bytes(b"gl config")
    (package / "ati2mtag.sys").write_bytes(b"miniport")
    (package / "ati2erec.dll").write_bytes(b"recorder")
    root = tmp_path / "volume"
    (root / "WINDOWS").mkdir(parents=True)

    def call(**kw):
        args = SimpleNamespace(root=str(root), inf=inf, section="ati2mtag_Kaveri_Desktop",
                               package=str(package), with_drivers=False, check=False, apply=False)
        for k, v in kw.items():
            setattr(args, k, v)
        return rg.cmd_package(args)

    assert call(check=True) == 1, "an empty volume is not placed"
    assert not list((root / "WINDOWS" / "system32").glob("*")) if (root / "WINDOWS" / "system32").is_dir() else True, \
        "the check must not have written the files it was checking"
    assert call(apply=True) == 0
    assert (root / "WINDOWS" / "system32" / "ati2cqag.dll").read_bytes() == b"the 2d component"
    assert (root / "WINDOWS" / "atiogl.xml").is_file(), "a Windows-destination file is not in system32"
    assert not (root / "WINDOWS" / "system32" / "ati2mtag.sys").exists(), \
        "the miniport stays on the driver volume"
    assert call(check=True) == 0
