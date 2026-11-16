"""Installing the file the registry names, and the measurement that said it was missing.

`plan/0007#desktop-verdict` asks for an ATI-rendered desktop, and every boot of this campaign fell back
to VGA with `ati2dvag.dll` opened and never mapped. The cause turned out to be that the file is not on
the OS volume, so the fix is a copy and the question is whether the copy is done honestly: from a named
source, into the directory Windows resolves, verified by a reader that never held the write, and
recorded in the run.

The finding itself is pinned here as a measurement, because it is the kind of claim that gets repeated
as prose and then quietly stops being true.
"""
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SETTINGS = ROOT / "golden-settings.py"
RUNNER = ROOT / "run-experiment.sh"
CHANNEL_FILE = ROOT / "channel-file.sh"
BASE_CHANNEL = ROOT / "gpudrv.img"
GOLDEN = ROOT / "work" / "goldens" / "os.qcow2"
NO_SUDO = "reading a volume needs passwordless sudo, which this run does not have"


def needs_sudo():
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip(NO_SUDO)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_installer_copies_bytes_rather_than_writing_text():
    """A PE image is not a latin-1 string.

    The rig's existing `write()` helper round-trips through a decoder because its other subjects are
    boot.ini and a batch file. Routing 520 KB of machine code through it yields a file of the right
    size and wrong content, and the guest reports that as a broken driver rather than as a bad copy.
    """
    src = SETTINGS.read_text()
    assert "--display-dll" in src, "the setting has to be nameable"
    install = src[src.index('if a.display_dll:'):]
    assert 'sh("sudo", "cp", str(a.display_dll), str(dest))' in install, \
        "the copy has to be a copy of bytes"
    assert "write(dest" not in install, "the text-mode writer must not touch a PE image"


def test_the_installer_proves_the_copy_from_a_mount_that_never_wrote():
    """The module's own discipline, applied to the new setting: write, remount read-only, re-read."""
    src = SETTINGS.read_text()
    assert 'expected["display-dll"] = ("sha", want, a.display_dll.name)' in src, \
        "the copy has to enter the proof pass rather than trust the cp exit code"
    proof = src[src.index('elif name == "display-dll":'):]
    assert "bad.append" in proof and "have != want" in proof, \
        "a digest mismatch after the write has to fail the run"


def test_the_target_directory_is_found_by_name_and_not_assumed():
    src = SETTINGS.read_text()
    assert 'SYSTEM32 = "WINDOWS/system32"' in src
    assert "find(MOUNT, SYSTEM32)" in src, "the volume stores its own casing, and find() settles it"
    assert "DLLCACHE" in src, "Windows File Protection restores from dllcache, so it gets the same bytes"


def test_a_bad_dll_name_is_refused_before_anything_is_mounted(tmp_path):
    """Both files exist here on purpose.

    A refusal test that leans on an absent path proves only that the tool notices absent paths, and it
    passes or fails depending on whether this machine holds a proprietary image. Naming the refusal
    means arranging for that refusal to be the one reached.
    """
    image = tmp_path / "volume.qcow2"
    image.write_bytes(b"qcow2-shaped bytes: the name check must fire before any mount")
    not_a_dll = tmp_path / "notes.txt"
    not_a_dll.write_bytes(b"not a PE image")
    r = subprocess.run([sys.executable, str(SETTINGS), "--image", str(image),
                        "--display-dll", str(not_a_dll)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "named like a driver DLL" in (r.stdout + r.stderr), r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(SETTINGS), "--image", str(tmp_path / "absent.qcow2"),
                        "--display-dll", str(not_a_dll)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "no such image" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_the_extractor_names_its_arguments_and_refuses_a_path_as_a_name(tmp_path):
    r = subprocess.run([str(CHANNEL_FILE)], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "name the image, the file" in r.stderr, r.stderr
    image = tmp_path / "channel.img"
    image.write_bytes(b"an image that exists, so the name check is the one that fires")
    r = subprocess.run([str(CHANNEL_FILE), "--image", str(image), "--name", "drv/ati2dvag.dll",
                        "--out", str(tmp_path / "never")], capture_output=True, text=True,
                       cwd=str(ROOT))
    assert r.returncode == 2 and "name a file, not a path" in r.stderr, r.stderr
    r = subprocess.run([str(CHANNEL_FILE), "--image", str(ROOT / "absent.img"), "--name", "ati2dvag.dll",
                        "--out", "/tmp/opencode/never"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "no such channel image" in r.stderr, r.stderr
    assert "guest-running.sh" in CHANNEL_FILE.read_text(), \
        "reading a volume a guest may have open reads whatever the cache holds, not what the disk does"


@pytest.mark.skipif(not BASE_CHANNEL.is_file(), reason="the base channel image is not on this machine")
def test_the_display_dll_is_on_the_channel_the_guest_boots():
    """The source of the file the registry names, extracted by the tool a run would use."""
    needs_sudo()
    out = Path(tempfile.mkdtemp(prefix="dvag-", dir="/tmp/opencode")) / "ati2dvag.dll"
    r = subprocess.run([str(CHANNEL_FILE), "--image", str(BASE_CHANNEL), "--name", "ati2dvag.dll",
                        "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert out.is_file() and out.stat().st_size > 4096, r.stdout
    assert "ati2dvag.dll" in r.stdout and "sha256:" in r.stdout, r.stdout
    print(r.stdout)


@pytest.mark.skipif(not BASE_CHANNEL.is_file(), reason="the base channel image is not on this machine")
def test_the_channel_offers_the_display_driver_as_the_miniports_counterpart():
    """The measurement that ruled out the other explanation, kept as a test rather than a memory.

    A display driver of a different package from the miniport declines to attach, and from outside that
    looks exactly like a missing file. The bytes settle it: same machine, same fixed file version.
    """
    importlib.util.find_spec("pefile") or pytest.skip("pefile is not installed on this interpreter")
    needs_sudo()
    into = Path(tempfile.mkdtemp(prefix="dvag-pair-", dir="/tmp/opencode"))
    names = {}
    for name in ("ati2dvag.dll", "ati2mtag.sys"):
        out = into / name
        r = subprocess.run([str(CHANNEL_FILE), "--image", str(BASE_CHANNEL), "--name", name,
                            "--out", str(out)], capture_output=True, text=True, cwd=str(ROOT))
        assert r.returncode == 0, r.stderr
        names[name] = out

    import pefile

    read = {}
    for name, path in names.items():
        pe = pefile.PE(path.as_posix())
        fixed = next((entry for entry in (getattr(pe, "VS_FIXEDFILEINFO", None) or [])), None)
        assert pe.FILE_HEADER.Machine == 0x8664, "%s is not an AMD64 image" % name
        assert fixed is not None, "%s carries no version resource" % name
        read[name] = "%d.%d.%d.%d" % (fixed.FileVersionMS >> 16, fixed.FileVersionMS & 0xFFFF,
                                      fixed.FileVersionLS >> 16, fixed.FileVersionLS & 0xFFFF)
    assert read["ati2dvag.dll"] == read["ati2mtag.sys"], read
    assert read["ati2dvag.dll"].startswith("6.14."), read


@pytest.mark.skipif(not GOLDEN.is_file(), reason="the campaign golden is not on this machine")
def test_the_campaign_golden_does_not_carry_the_display_dll():
    """The finding, re-derived: the file the registry names is absent from the OS volume.

    `os.qcow2` stays exactly as it is, because every run record cites its digest, so this absence is a
    permanent property of that golden and a permanent reason a boot from it falls back to VGA. A run
    that wants the ATI path installs the file into its own overlay instead.
    """
    needs_sudo()
    spec = importlib.util.spec_from_file_location("golden_state", ROOT / "golden-state.py")
    gs = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gs)
    if subprocess.run([str(ROOT / "guest-running.sh"), "check"], capture_output=True).returncode == 0:
        pytest.skip("a QEMU is running, so the golden is not safely readable right now")
    with gs.attached(GOLDEN) as img:
        system32 = gs.locate(str(img.dir), "WINDOWS/system32")
        assert system32 is not None, "the golden has no system32, so it is not the volume assumed"
        present = sorted(c.name for c in Path(system32).iterdir() if c.name.lower() == "ati2dvag.dll")
        assert present == [], "the golden carries the display DLL, so the campaign's account is stale"
