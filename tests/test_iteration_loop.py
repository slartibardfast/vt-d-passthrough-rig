"""The iteration loop's contract, checked without booting anything.

What is testable here is the shape of a run: what `golden-build.sh` records about a golden,
what `run-experiment.sh` refuses, and what the command line it builds must and must not
contain. The boot itself is not in scope for a unit suite, and a test that launched QEMU
would be a second, unauthorized instrument boot disguised as a check.

Two laws are asserted rather than trusted, because both were broken in this campaign and
each breakage cost a measurement. The first: the seven input tools must drive the monitor
socket the launcher actually creates, not one from a checkout that no longer runs, since a
keystroke that reaches nothing reads exactly like a guest that is not responding. The
second: the volatile video device map must be written by the guest during the run, because
no offline read of an overlay can supply a key that never existed on disk.
"""

import json
import re
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DEAD_SOCKET = "/home/dconnolly/xp64-rig/mon.sock"
SOCKET_TOOLS = ["sendkeys.py", "monq.py", "mon.py", "logon.py", "pickmenu.py", "typekey.py",
                "typepass.py"]

sys.path.insert(0, str(ROOT))
import channel_lint  # noqa: E402


def synthetic_raw(path, signature=0x28152ec4, partition_sectors=2048, size=1 << 20):
    """A raw image with just enough of an MBR to be identified, and nothing else."""
    body = bytearray(size)
    body[0x1B8:0x1BC] = struct.pack("<I", signature)
    entry = bytearray(16)
    entry[4:8] = struct.pack("<I", partition_sectors)
    entry[8:12] = struct.pack("<I", partition_sectors)
    body[0x1BE:0x1CE] = entry
    path.write_bytes(bytes(body))
    return signature, partition_sectors * 512


def run(cmd, *args, **kw):
    return subprocess.run([str(c) for c in cmd] + [str(a) for a in args],
                          capture_output=True, text=True, **kw)


@pytest.fixture
def golden_dir(tmp_path):
    raw = tmp_path / "os.raw"
    sig, offset = synthetic_raw(raw)
    out = tmp_path / "goldens"
    r = run([ROOT / "golden-build.sh", "--from", raw, "--to", out, "--name", "os"])
    assert r.returncode == 0, r.stderr
    manifest = json.loads((out / "os.json").read_text())
    return out, manifest, raw, sig, offset


def test_golden_build_produces_a_standalone_qcow2_and_records_its_identity(golden_dir):
    out, manifest, raw, sig, offset = golden_dir
    golden = Path(manifest["golden"])
    assert golden.exists()
    info = run(["qemu-img", "info", "--output=json", golden])
    assert json.loads(info.stdout)["format"] == "qcow2"
    # Standalone: a golden with a backing file is a pointer, and a pointer is not an image.
    assert json.loads(info.stdout).get("backing-filename") in (None, "")
    assert manifest["source"] == str(raw)
    assert manifest["source_identity"]["disk_signature"] == "0x%08x" % sig
    assert manifest["source_identity"]["partition_offset"] == offset
    assert re.fullmatch(r"[0-9a-f]{64}", manifest["sha256"])
    assert manifest["bytes"] == golden.stat().st_size > 0


def test_golden_build_refuses_to_replace_an_existing_golden(golden_dir):
    out, _, raw, _, _ = golden_dir
    r = run([ROOT / "golden-build.sh", "--from", raw, "--to", out, "--name", "os"])
    assert r.returncode == 2
    assert "exists" in r.stderr.lower() or "exists" in r.stdout.lower()


def test_golden_build_refuses_a_missing_source(tmp_path):
    r = run([ROOT / "golden-build.sh", "--from", tmp_path / "nope.raw", "--to",
             tmp_path / "g", "--name", "os"])
    assert r.returncode == 2


def test_run_refuses_to_boot_a_golden(golden_dir, tmp_path):
    """The golden is read-only shared state. Booting it is the defect the layering exists to stop."""
    out, manifest, _, _, _ = golden_dir
    golden = Path(manifest["golden"])
    r = run([ROOT / "run-experiment.sh", "--golden", golden, "--channel", golden,
             "--name", "x", "--dry-run"])
    assert r.returncode == 2
    assert "golden" in (r.stdout + r.stderr).lower()


def test_run_refuses_a_channel_that_is_the_golden(golden_dir):
    out, manifest, _, _, _ = golden_dir
    golden = Path(manifest["golden"])
    r = run([ROOT / "run-experiment.sh", "--golden", golden, "--channel", golden,
             "--name", "x", "--dry-run"])
    assert r.returncode == 2


def test_prepare_makes_an_overlay_and_a_private_channel_copy(golden_dir, tmp_path):
    out, manifest, _, _, _ = golden_dir
    channel = tmp_path / "gpudrv.img"
    synthetic_raw(channel, signature=0x0000aaaa, partition_sectors=2048, size=1 << 16)
    r = run([ROOT / "run-experiment.sh", "--golden", manifest["golden"], "--channel", channel,
             "--name", "probe", "--runs", tmp_path / "runs", "--prepare-only"])
    assert r.returncode == 0, r.stdout + r.stderr
    run_dir = Path(re.search(r"run directory: (\S+)", r.stdout).group(1))
    record = json.loads((run_dir / "run.json").read_text())
    overlay = Path(record["overlay"])
    assert overlay.exists()
    info = json.loads(run(["qemu-img", "info", "--output=json", overlay]).stdout)
    assert info["format"] == "qcow2"
    assert Path(info["backing-filename"]) == Path(manifest["golden"])
    # A run must not share a mutable channel with the next run.
    copy = Path(record["channel"])
    assert copy.exists() and copy != Path(channel)
    assert record["channel_sha256"] == run(["sha256sum", copy]).stdout.split()[0]
    assert record["golden_sha256"] == manifest["sha256"]
    assert record["outcome"] == "prepared"


def test_the_command_line_carries_writeback_and_never_the_forbidden_tools(golden_dir, tmp_path):
    out, manifest, _, _, _ = golden_dir
    channel = tmp_path / "gpudrv.img"
    synthetic_raw(channel, signature=0x0000aaaa, partition_sectors=2048, size=1 << 16)
    r = run([ROOT / "run-experiment.sh", "--golden", manifest["golden"], "--channel", channel,
             "--name", "shape", "--runs", tmp_path / "runs", "--dry-run"])
    assert r.returncode == 0, r.stdout + r.stderr
    plan = r.stdout
    assert "cache=writeback" in plan
    assert "format=qcow2" in plan
    assert str(tmp_path / "runs") in plan
    assert "mon.sock" in plan
    for forbidden in ("cache=unsafe", "savevm", "loadvm", "system_reset", "usb-host"):
        assert forbidden not in plan, "%s must not appear in a run's plan" % forbidden


def test_the_launch_goes_through_the_one_launcher(golden_dir, tmp_path):
    """Rule: boots come from start-boot-detached.sh. A second launch path is a second QEMU."""
    src = (ROOT / "run-experiment.sh").read_text()
    assert "start-boot-detached.sh" in src
    assert "qemu-system-x86_64" not in src, "run-experiment.sh must not build its own QEMU line"


def test_evidence_collection_names_the_files_a_run_owes(golden_dir, tmp_path):
    """The guest writes its evidence into %SystemRoot%; the host reads it off the overlay."""
    tree = tmp_path / "tree"
    (tree / "WINDOWS").mkdir(parents=True)
    (tree / "WINDOWS" / "devicemap.txt").write_text("HKEY_LOCAL_MACHINE\\HARDWARE\\DEVICEMAP\\VIDEO\n")
    r = run([ROOT / "run-experiment.sh", "--list-evidence", tree])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WINDOWS/devicemap.txt" in r.stdout


def test_evidence_collection_reports_an_absent_capture_as_owed(golden_dir, tmp_path):
    tree = tmp_path / "tree"
    (tree / "WINDOWS").mkdir(parents=True)
    r = run([ROOT / "run-experiment.sh", "--list-evidence", tree])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "devicemap.txt" in r.stdout
    assert "absent" in r.stdout.lower()


@pytest.mark.parametrize("tool", SOCKET_TOOLS)
def test_an_input_tool_drives_the_socket_the_launcher_creates(tool):
    src = (ROOT / tool).read_text()
    assert DEAD_SOCKET not in src, "%s still points at a checkout that no longer runs" % tool
    assert "mon.sock" in src
    # The launcher's path is this directory's mon.sock: start-boot-detached.sh removes
    # "$RIG/mon.sock" and run-driver.sh binds MON to it by default.
    assert re.search(r"__file__.*mon\.sock", src, re.S), "%s must derive the socket from its own tree" % tool


def test_monq_reports_the_resolved_socket(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT))
    import importlib
    monq = importlib.import_module("monq")
    assert Path(monq.SOCK) == ROOT / "mon.sock"


def test_the_launcher_holds_the_socket_path_both_sides_agree_on():
    launcher = (ROOT / "start-boot-detached.sh").read_text()
    driver = (ROOT / "run-driver.sh").read_text()
    assert 'rm -f "$RIG/mon.sock"' in launcher
    assert 'MON=${MON:-$RIG/mon.sock}' in driver


def test_the_guest_writes_the_volatile_video_map_during_the_run():
    """HARDWARE is volatile: an offline read of an overlay cannot produce it, so the guest must."""
    hook = (ROOT / "install-channel" / "startup-hook.bat").read_text(encoding="utf-8", errors="replace")
    assert re.search(r"DEVICEMAP\\VIDEO", hook), "the logon hook must capture the video device map"
    assert "%SystemRoot%" in hook, "it must land on the system volume, which is inside the overlay"
    offenders = channel_lint.offending_letters(hook)
    assert offenders == [], "the hook names boot-chosen letters: %s" % offenders
    assert channel_lint.lines_that_are_not_batch(hook) == []


def test_the_runner_waits_for_the_guest_to_end_the_run():
    """The host waits on the guest exiting, never on a FAT volume lagging behind it."""
    src = (ROOT / "run-experiment.sh").read_text()
    assert re.search(r'"\$RIG/guest-running\.sh" wait "\$TIMEOUT"', src)
    assert re.search(r'"\$RIG/guest-running\.sh" stop', src), "a guest that cannot shut down must be stopped and recorded"
    assert "timeout-killed" in src


def _fake_named_like_qemu(tmp_path):
    """A process whose kernel name is what a live QEMU's name truncates to.

    A Linux comm field holds 15 characters, so a running QEMU is named `qemu-system-x86`. The
    guard has to look for that name and for nothing else. Matching the full command line was
    measured refusing nine tests in one run, because a shell whose command line merely contains
    the binary's name is caught by it, and the same idiom aimed at a signal would reach outside
    the rig entirely.
    """
    import shutil
    fake = tmp_path / "qemu-system-x86_64"
    shutil.copy("/bin/sleep", fake)
    return subprocess.Popen([str(fake), "30"])


def test_a_guest_named_process_refuses_the_golden_build_and_a_mention_does_not(tmp_path):
    raw = tmp_path / "os.raw"
    synthetic_raw(raw)
    out = tmp_path / "goldens"

    proc = _fake_named_like_qemu(tmp_path)
    try:
        time.sleep(0.5)
        r = run([ROOT / "golden-build.sh", "--from", raw, "--to", out, "--name", "guard"])
        assert r.returncode == 2, "a QEMU-named process must stop a golden being taken from an open disk"
        assert "QEMU" in r.stdout + r.stderr
    finally:
        proc.terminate()
        proc.wait()

    mention = subprocess.Popen(["/bin/bash", "-c", "exec sleep 30 # qemu-system-x86_64 -name xp64-driver"])
    try:
        time.sleep(0.5)
        r = run([ROOT / "golden-build.sh", "--from", raw, "--to", out, "--name", "guard"])
        assert r.returncode == 0, "a command line that only mentions the name is not a running guest"
    finally:
        mention.terminate()
        mention.wait()
