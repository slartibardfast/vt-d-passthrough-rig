"""Non-hardware checks for the rig's disk and Wine safety boundaries."""

import ast
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import time

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


# --- how the rig answers "is a guest alive" -------------------------------------------
#
# A command-line match for the QEMU binary was measured catching shells that merely mention
# the name, which makes a guard refuse work that has no conflicting guest and makes a signal
# reach processes the rig does not own. The name form is the only safe one, and it lives in
# one file so no script can drift back.

GUARD_CALLERS = ["boot.py", "start-boot-detached.sh", "offline-guest-settings.sh", "stage-install-channel.sh",
                 "offline-registration.sh", "golden-build.sh", "run-experiment.sh"]


def test_no_script_matches_a_guest_by_its_command_line():
    offenders = []
    for script in sorted(list(ROOT.glob("*.sh")) + list(ROOT.glob("*.py"))):
        if script.name == "guest-running.sh":
            continue
        for number, line in enumerate(script.read_text(errors="replace").splitlines(), 1):
            lowered = line.lower()
            if "emu-system" in lowered and ("pgrep" in lowered or "pkill" in lowered) \
                    and (" -f" in lowered or " -xf" in lowered or "--full" in lowered):
                offenders.append("%s:%d: %s" % (script.name, number, line.strip()))
    assert offenders == [], "match the guest through guest-running.sh:\n" + "\n".join(offenders)


def test_every_guard_of_a_live_disk_routes_through_the_helper():
    for name in GUARD_CALLERS:
        assert "guest-running.sh" in (ROOT / name).read_text(), "%s decides on its own" % name
    assert "qemu-system" not in (ROOT / "boot.py").read_text(), "the process name belongs to the helper"


def _await(predicate, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def _standin_guest(tmp_path):
    """A live process whose kernel name is what a running QEMU's comm truncates to.

    Two runner facts made this harder than it looks. `/bin/sleep` on the Ubuntu image is a
    symlink into the coreutils multicall binary, so a copy of it inspects its own argv[0],
    reports `coreutils: unknown program 'qemu-system-x86_64'`, and exits at once. And a shell
    started with `-c` and a single command is replaced by that command, so the process turns
    into `sleep` and the name the guard is looking for disappears. A copy of /bin/sh running
    `sleep 60 & wait` keeps the name, stays alive, and dies on TERM. It gets its own process
    group so the cleanup can take the child with it rather than orphaning a sleep.
    """
    fake = tmp_path / "qemu-system-x86_64"
    shutil.copy("/bin/sh", fake)
    proc = subprocess.Popen([str(fake), "-c", "sleep 60 & wait"], start_new_session=True)
    time.sleep(0.4)
    assert proc.poll() is None, "the stand-in died at once, so it proves nothing about the guard"
    return proc


def test_the_helper_sees_the_process_name_and_nothing_else(tmp_path):
    helper = ROOT / "guest-running.sh"
    assert _await(lambda: subprocess.run([str(helper), "check"],
                                         capture_output=True).returncode == 1), "no guest may be assumed"

    guest = _standin_guest(tmp_path)
    mention = subprocess.Popen(["/bin/bash", "-c", "exec sleep 60 # qemu-system-x86_64 -name xp64-driver"])
    try:
        assert _await(lambda: subprocess.run([str(helper), "check"],
                                             capture_output=True).returncode == 0), "a live guest must be seen"
        pids = subprocess.run([str(helper), "pids"], capture_output=True, text=True).stdout.split()
        assert str(guest.pid) in pids
        assert str(mention.pid) not in pids, "a command line that mentions the name is not a guest"

        assert subprocess.run([str(helper), "wait", "1"], capture_output=True).returncode == 1

        assert subprocess.run([str(helper), "stop", "6"], capture_output=True).returncode == 0
        assert _await(lambda: subprocess.run([str(helper), "check"],
                                             capture_output=True).returncode == 1), "stop must actually stop it"
    finally:
        os.killpg(os.getpgid(guest.pid), signal.SIGKILL)
        guest.wait()
        if mention.poll() is None:
            mention.kill()
        mention.wait()


# --- how the rig's entry points answer a question -----------------------------------------
#
# These scripts do their work by running: one of them boots a guest. So the mode bit and the
# treatment of a flag are both part of the interface, and each was wrong here on 2026-11-15.
# `./offline-guest-settings.sh` failed with `Permission denied` while `bash` of the same file
# worked, because the file carried a shebang and mode 644, and every test that shells through
# bash stayed green. And `./start-boot-detached.sh --help` started a KDC and a QEMU on the real
# GPU, because the script took no arguments and did nothing with the ones it was given.


def test_every_shebang_script_in_the_rig_is_executable():
    unrunnable = []
    for script in sorted(ROOT.glob("*.sh")):
        if script.read_text(errors="replace").startswith("#!") and not os.access(script, os.X_OK):
            unrunnable.append(script.name)
    assert unrunnable == [], "chmod +x, then git add, so the mode is recorded: " + ", ".join(unrunnable)


ENTRY_POINTS = ["offline-guest-settings.sh", "stage-install-channel.sh", "start-boot-detached.sh"]


def _ask(script, flag, tmp_path):
    """Ask an entry point a question in a sandbox where it cannot do its work.

    The point of the sandbox is that a regression in the guard cannot boot a guest from inside
    the test run: PATH holds nothing, so `sudo`, `mount` and `python3 kdc.py` all fail, and
    `WORK` names a disk image that does not exist, which is the check the boot script makes
    before it reaches for a machine.
    """
    empty = tmp_path / "empty-path"
    empty.mkdir()
    env = {"PATH": str(empty), "HOME": str(tmp_path), "WORK": str(tmp_path / "no-such-disk.raw")}
    return subprocess.run(["/bin/bash", str(ROOT / script), flag], capture_output=True,
                          text=True, cwd=str(tmp_path), env=env)


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_help_answers_with_usage_and_nothing_else(script, tmp_path):
    r = _ask(script, "--help", tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "usage:" in r.stderr.lower(), r.stderr
    assert "kdc pid" not in r.stdout and "launcher pid" not in r.stdout, "a flag started a machine"


@pytest.mark.parametrize("script", ENTRY_POINTS)
def test_an_unknown_flag_is_refused_rather_than_ignored(script, tmp_path):
    r = _ask(script, "--no-such-flag", tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unknown argument" in r.stderr, r.stderr


def test_the_probes_left_no_machine_running():
    helper = ROOT / "guest-running.sh"
    assert subprocess.run([str(helper), "check"], capture_output=True).returncode == 1, (
        "asking an entry point for usage must not leave a guest alive")
