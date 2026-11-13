"""Non-hardware checks for the rig's disk and Wine safety boundaries."""

import ast
import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_wine_prefix_has_no_host_root_drive():
    dosdevices = ROOT / "wineprefix" / "dosdevices"
    assert not (dosdevices / "z:").exists()
    for drive in dosdevices.glob("*:"):
        if drive.is_symlink():
            assert os.readlink(drive) != "/", drive


def test_debugger_alias_stays_inside_the_rig():
    alias = ROOT / "wineprefix" / "drive_c" / "dtw"
    assert os.readlink(alias) == "../../dtw"
    assert alias.resolve() == (ROOT / "dtw").resolve()
    assert "C:\\dtw\\kd.exe" in (ROOT / "kdrun.sh").read_text()


def test_offline_audit_uses_the_working_disk():
    tree = ast.parse((ROOT / "audit-offline.py").read_text())
    assignment = next(
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "SYS_DISK"
                for target in node.targets)
    )
    assert assignment.value.args[-1].value == "xp64-gpu.raw"


def test_answer_file_template_contains_no_operational_values():
    text = (ROOT / "winnt.sif.example").read_text()
    assert "AdminPassword=REDACTED" in text
    assert "ProductKey=XXXXX-XXXXX-XXXXX-XXXXX-XXXXX" in text
    assert "/winnt.sif" in (ROOT / ".gitignore").read_text()


def make_executable(path: Path, text: str):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def kdrun_fixture(tmp_path: Path, root_mapping: bool):
    shutil.copy2(ROOT / "kdrun.sh", tmp_path / "kdrun.sh")
    prefix = tmp_path / "wineprefix"
    (prefix / "drive_c").mkdir(parents=True)
    (prefix / "dosdevices").mkdir()
    if root_mapping:
        (prefix / "dosdevices" / "z:").symlink_to("/")
    marker = tmp_path / "wine-called"
    make_executable(
        tmp_path / "wine",
        "#!/bin/sh\nprintf '%s\\n' \"$PWD $*\" > \"$WINE_MARKER\"\n",
    )
    return prefix, marker


def run_kdrun(tmp_path: Path, prefix: Path, marker: Path):
    kdin = tmp_path / "kdin"
    kdin.write_text("")
    env = os.environ.copy()
    env.update({
        "WINEPREFIX": str(prefix),
        "KDIN": str(kdin),
        "KDOUT": str(tmp_path / "kdout"),
        "WINE_MARKER": str(marker),
        "PATH": f"{tmp_path}:{env['PATH']}",
    })
    return subprocess.run(
        ["bash", str(tmp_path / "kdrun.sh")],
        env=env, capture_output=True, text=True,
    )


def test_kdrun_refuses_host_root_before_wine(tmp_path):
    prefix, marker = kdrun_fixture(tmp_path, True)
    result = run_kdrun(tmp_path, prefix, marker)
    assert result.returncode != 0
    assert "refusing host-root Wine drive mapping" in result.stderr
    assert not marker.exists()


def test_kdrun_launches_the_aliased_debugger_without_z(tmp_path):
    prefix, marker = kdrun_fixture(tmp_path, False)
    result = run_kdrun(tmp_path, prefix, marker)
    assert result.returncode == 0, result.stderr
    assert "C:\\dtw\\kd.exe" in marker.read_text()


def driver_fixture(tmp_path: Path):
    shutil.copy2(ROOT / "run-driver.sh", tmp_path / "run-driver.sh")
    (tmp_path / "rollback").mkdir()
    for name in ("xp64-gpu.raw", "gpudrv.img", "290x-vbios.rom"):
        (tmp_path / name).write_bytes(b"fixture")
    marker = tmp_path / "qemu-args"
    make_executable(
        tmp_path / "qemu-system-x86_64",
        "#!/bin/sh\nprintf '%s\\n' \"$*\" > \"$QEMU_ARGS\"\n",
    )
    make_executable(tmp_path / "python3", "#!/bin/sh\nexit 0\n")
    make_executable(tmp_path / "sleep", "#!/bin/sh\nexit 0\n")
    return marker


def run_driver(tmp_path: Path, marker: Path, work=None):
    env = os.environ.copy()
    env.update({
        "QEMU_ARGS": str(marker),
        "PATH": f"{tmp_path}:{env['PATH']}",
    })
    if work is not None:
        env["WORK"] = str(work)
    return subprocess.run(
        ["bash", str(tmp_path / "run-driver.sh")],
        env=env, capture_output=True, text=True,
    )


def test_driver_defaults_to_the_working_disk(tmp_path):
    marker = driver_fixture(tmp_path)
    result = run_driver(tmp_path, marker)
    assert result.returncode == 0, result.stderr
    args = marker.read_text()
    assert f"file={tmp_path / 'xp64-gpu.raw'}" in args
    assert "install-blank.raw" not in args


@pytest.mark.parametrize("alias_kind", ["direct", "symlink", "hardlink"])
def test_driver_refuses_every_rollback_alias(tmp_path, alias_kind):
    marker = driver_fixture(tmp_path)
    rollback = tmp_path / "rollback" / "xp64-disk-good.raw"
    rollback.write_bytes(b"pristine")
    if alias_kind == "direct":
        work = rollback
    elif alias_kind == "symlink":
        work = tmp_path / "rollback-alias.raw"
        work.symlink_to(rollback)
    else:
        work = tmp_path / "rollback-alias.raw"
        os.link(rollback, work)
    result = run_driver(tmp_path, marker, work)
    assert result.returncode != 0
    assert "refusing wrong or rollback disk" in result.stderr
    assert not marker.exists()


def test_driver_refuses_the_rejected_install_disk(tmp_path):
    marker = driver_fixture(tmp_path)
    work = tmp_path / "install-blank.raw"
    work.write_bytes(b"wrong disk")
    result = run_driver(tmp_path, marker, work)
    assert result.returncode != 0
    assert "refusing wrong or rollback disk" in result.stderr
    assert not marker.exists()
