"""Grading and image-setting checks for the control load, with no hardware involved.

These tests pin what `load-verify.py` is for: a control run either proves the six things the
plan names, or the grader says which one it did not prove and names the command that would
produce it. Every fixture is synthetic except the registry hive, which is the tracked
post-graft hive this campaign wrote, read by the same reader that wrote it.

The reason for that choice is the defect this file exists to prevent: a grader that reports an
absent claim as a pass, or stays silent about a fact it could not read, is a hollow green run
with a timestamp on it. Three tests here are therefore about failure text.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LOADER = ROOT / "golden-settings.py"
VERIFIER = ROOT / "load-verify.py"
POST_GRAFT_HIVE = ROOT / "work" / "evidence" / "system-hive-after-the-graft"

NO_HIVEX = "reading a hive needs the system Python's hivex, not the rig venv"

KD_CLEAN = r"""[18:40:04] FILE_IO api=0x3430 \??\D:\drv\ati2mtag.sys
[18:40:04] FILE_IO api=0x3430 \SystemRoot\system32\DRIVERS\videoprt.sys
[18:40:09] MODULE LOAD pid=0x80800000 base=0xfffffadfaae79000 name=ati2mtag.sys
[18:40:10] FILE_IO api=0x3430 \SystemRoot\System32\ati2dvag.dll
[18:40:15] RX ack pid=0x80800001
"""
KD_NO_MINIPORT = r"""[18:40:04] FILE_IO api=0x3430 \SystemRoot\system32\DRIVERS\videoprt.sys
[18:40:05] MODULE LOAD pid=0x80800000 base=0xfffffadfabb72000 name=\WINDOWS\System32\drivers\VIDEOPRT.SYS
"""
KD_BUGCHECK = KD_CLEAN + "\n[18:41:00] SYSTEM_SERVICE_EXCEPTION\n"

NTBTLOG = r"""Windows XP boot log
Loaded at 0x00000000,0x00000000 - \SystemRoot\System32\drivers\videoprt.sys
Loaded at 0x00000000,0x00000000 - \??\D:\drv\ati2mtag.sys
Loaded at 0x00000000,0x00000000 - \SystemRoot\system32\ati2dvag.dll
"""
DEVICEMAP = r"""HKEY_LOCAL_MACHINE\HARDWARE\DEVICEMAP\VIDEO
    \Device\Video0    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\0000
"""

# `wmic` writes UTF-16 when its output is redirected, which is not corruption and has already
# been misread once in this campaign as spaced-out CJK. The fixture mirrors the real encoding so
# the grader is tested against the bytes the guest actually leaves behind.
DEVNODE_OK = r"""
instance of Win32_PnPEntity
Caption="PCI device"
ClassGuid={4D36E964-E325-11CE-BFC1-08002BE10318}
ConfigManagerErrorCode=0
CreationClassName=Win32_PnPEntity
DeviceID="PCI\\VEN_1002&DEV_67B0&SUBSYS_E285174B&REV_00\\3&267A616A&0&20"
Name="AMD Radeon Graphics Processor (0x67B0)"
PNPDeviceID="PCI\\VEN_1002&DEV_67B0&SUBSYS_E285174B&REV_00\\3&267A616A&0&20"
Status="OK"
"""
DEVNODE_REFUSED = DEVNODE_OK.replace("ConfigManagerErrorCode=0", "ConfigManagerErrorCode=28")


def _run(*args, **env):
    environ = dict(os.environ)
    environ.update(env.pop("env", {}))
    return subprocess.run([sys.executable, str(VERIFIER), *[str(x) for x in args]],
                          capture_output=True, text=True, cwd=str(ROOT), env=environ)


def fixture_run(tmp_path, kd=KD_CLEAN, outcome="measured-shutdown", crash=False,
                ntbtlog=NTBTLOG, hive=None, devnode=DEVNODE_OK):
    run = tmp_path / "baseline-20261115T190000Z"
    (run / "evidence" / "WINDOWS" / "system32" / "config").mkdir(parents=True)
    (run / "kdc-detached.log").write_text(kd)
    (run / "evidence" / "WINDOWS" / "ntbtlog.txt").write_text(ntbtlog)
    (run / "evidence" / "WINDOWS" / "devicemap.txt").write_text(DEVICEMAP)
    if devnode is not None:
        (run / "evidence" / "WINDOWS" / "devnode.txt").write_text(devnode, encoding="utf-16")
    if hive:
        (run / "evidence" / "WINDOWS" / "system32" / "config" / "system").write_bytes(
            Path(hive).read_bytes())
    (run / "run.json").write_text(json.dumps({
        "name": "baseline", "outcome": outcome, "crash_suspected": crash,
        "golden": str(tmp_path / "os.qcow2"), "golden_sha256": "0" * 64,
        "overlay": str(run / "overlay.qcow2"), "channel": str(tmp_path / "gpudrv.img"),
        "channel_sha256": "1" * 64, "timeout_seconds": 900}))
    return run


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_control_that_proves_every_claim_passes(tmp_path):
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    run = fixture_run(tmp_path, hive=POST_GRAFT_HIVE)
    r = _run("--run", run)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PASS   image-path" in r.stdout, r.stdout
    assert "PASS   no-bugcheck" in r.stdout, r.stdout
    assert "PASS   per-adapter-key" in r.stdout, r.stdout
    assert "ConfigManagerErrorCode=0 from the running guest" in r.stdout, r.stdout
    assert "PASS   bootlog" in r.stdout, r.stdout
    assert "verdict: PASS" in r.stdout, r.stdout
    assert "reported, not required" in r.stdout, "PowerPlay must not be demanded of the control"


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_run_where_the_miniport_never_mapped_fails_and_names_where_to_look(tmp_path):
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    run = fixture_run(tmp_path, kd=KD_NO_MINIPORT, hive=POST_GRAFT_HIVE)
    r = _run("--run", run)
    assert r.returncode == 1, r.stdout
    assert "FAIL   image-path" in r.stdout, r.stdout
    assert "registration_graft.py check" in r.stdout, r.stdout
    assert "verdict: FAIL (image-path)" in r.stdout, r.stdout


def test_a_missing_hive_fails_the_claims_that_need_it_rather_than_skipping_them(tmp_path):
    run = fixture_run(tmp_path)          # no collected hive at all
    r = _run("--run", run)
    assert r.returncode == 1, r.stdout
    assert "FAIL   per-adapter-key" in r.stdout and "FAIL   devnode-code" in r.stdout, r.stdout
    assert "remedy:" in r.stdout, "a failure without a named next command is not a report"


def test_a_run_killed_by_the_host_is_not_read_as_the_absence_of_a_crash(tmp_path):
    run = fixture_run(tmp_path, outcome="timeout-killed", crash=True)
    r = _run("--run", run)
    assert r.returncode == 1, r.stdout
    assert "FAIL   no-bugcheck" in r.stdout, r.stdout
    assert "did not end on its own" in r.stdout, r.stdout


def test_a_bugcheck_in_the_log_is_named(tmp_path):
    run = fixture_run(tmp_path, kd=KD_BUGCHECK)
    r = _run("--run", run)
    assert "FAIL   no-bugcheck" in r.stdout, r.stdout
    assert "SYSTEM_SERVICE_EXCEPTION" in r.stdout, r.stdout


def test_a_run_without_the_loader_log_cannot_claim_what_the_loader_did(tmp_path):
    run = fixture_run(tmp_path, ntbtlog="")
    r = _run("--run", run)
    assert "FAIL   bootlog" in r.stdout, r.stdout
    assert "golden-settings.py" in r.stdout, "the remedy must name the tool that sets the flag"


def test_powerplay_is_demanded_only_of_a_run_that_claims_it(tmp_path):
    quiet = fixture_run(tmp_path, kd=KD_CLEAN)
    r = _run("--run", quiet)
    assert "MEASURED powerplay" in r.stdout, r.stdout
    assert "FAIL   powerplay" not in r.stdout, r.stdout
    r = _run("--run", quiet, "--expect-powerplay")
    assert r.returncode == 1, r.stdout
    assert "FAIL   powerplay" in r.stdout, r.stdout
    assert "produced none" in r.stdout, r.stdout


def test_a_run_directory_that_is_not_there_says_how_to_make_one(tmp_path):
    r = _run("--run", tmp_path / "no-such-run")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "run-experiment.sh" in (r.stdout + r.stderr), r.stderr


def test_the_feed_is_reported_with_the_limit_of_what_it_witnesses(tmp_path):
    run = fixture_run(tmp_path)
    (run / "feed-60.ppm").write_bytes(b"P6\n2 2\n255\n" + bytes([0, 0, 0] * 4))
    r = _run("--run", run)
    assert "feed-60=100.0% near-black" in r.stdout, r.stdout
    assert "not what the 290X drove on the monitor" in r.stdout, r.stdout


def test_the_ppm_reader_counts_pixels_and_not_bytes(tmp_path):
    """The module name carries a hyphen, so it is loaded by path rather than imported by name."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("load_verify", VERIFIER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    frame = tmp_path / "half-black.ppm"
    frame.write_bytes(b"P6\n2 1\n255\n" + bytes([0, 0, 0, 255, 255, 255]))
    assert mod.ppm_near_black(frame) == 50.0
    frame.write_bytes(b"P6\n# a comment line\n1 1\n255\n" + bytes([9, 9, 9]))
    assert mod.ppm_near_black(frame) == 100.0
    frame.write_bytes(b"P1\n1 1\n255\n0\n")
    assert mod.ppm_near_black(frame) is None, "a format this reader does not parse is not a black frame"


def test_the_evidence_list_reads_as_the_collector_reads_it(tmp_path):
    tree = tmp_path / "vol" / "WINDOWS"
    (tree / "system32" / "config").mkdir(parents=True)
    (tree / "devicemap.txt").write_text(DEVICEMAP)
    r = subprocess.run([str(ROOT / "run-experiment.sh"), "--list-evidence", str(tmp_path / "vol")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [l.split("\t") for l in r.stdout.strip().splitlines()]
    states = {row[0]: row[1] for row in rows}
    assert "WINDOWS/devicemap.txt" in states and "WINDOWS/ntbtlog.txt" in states, r.stdout
    assert "WINDOWS/devnode.txt" in states, "the live devnode answer has to be collected too"
    assert states["WINDOWS/devicemap.txt"] == "present"
    assert set(states.values()) <= {"present", "absent (the guest writes it during the run)"}, r.stdout


def test_a_missing_tool_is_still_one_line(tmp_path):
    """The refusal that names the package must not wrap: a two-line error reads as two failures."""
    empty = tmp_path / "empty"
    empty.mkdir()
    r = subprocess.run(["/bin/bash", str(ROOT / "run-experiment.sh"), "--golden", "g", "--channel",
                        "c", "--name", "x", "--dry-run"], capture_output=True, text=True,
                       cwd=str(tmp_path), env={"PATH": str(empty)})
    assert r.returncode == 2, r.stdout + r.stderr
    named = [l for l in r.stderr.splitlines() if "qemu-img" in l]
    assert len(named) == 1, r.stderr
    assert "qemu-utils" in named[0], r.stderr


def test_the_runner_sets_the_loader_flag_on_the_overlay_and_never_on_the_golden():
    src = (ROOT / "run-experiment.sh").read_text()
    assert '--image "$OVERLAY" --bootlog' in src, "the flag must go on the image that boots"
    assert "refusing to boot a run that records nothing" in src
    assert 'LOGDIR="$RUN"' in src, "a KD log outside the run directory belongs to no run"
    assert "system_powerdown" in src, "the run is closed by a request the guest can honour"
    assert "OUTCOME=measured-shutdown" in src


def test_golden_settings_says_what_is_missing_rather_than_tracebacking(tmp_path):
    r = subprocess.run([sys.executable, str(LOADER), "--image", str(tmp_path / "nope.qcow2"),
                        "--bootlog"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.startswith("golden-settings:"), r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    r = subprocess.run([sys.executable, str(LOADER), "--image", str(ROOT / "run-experiment.sh")],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "--bootlog" in r.stderr, r.stderr


def test_golden_settings_refuses_a_live_guest_and_a_verbless_call():
    src = LOADER.read_text()
    assert "guest-running.sh" in src, "the liveness rule has one home"
    assert '"sudo", "qemu-nbd"' in src and '"sudo", "modprobe", "nbd"' in src
    assert "refusing to guess which one boots" in src, "two entries and no default is a guess"


def test_the_devnode_answer_is_read_from_the_running_guest(tmp_path):
    """The premise of this test is measured below: no banked hive carries the value."""
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    run = fixture_run(tmp_path, hive=POST_GRAFT_HIVE, devnode=DEVNODE_REFUSED)
    r = _run("--run", run)
    assert "FAIL   devnode-code" in r.stdout, r.stdout
    assert "ConfigManagerErrorCode=[28] from the running guest" in r.stdout, r.stdout


def test_a_run_without_the_live_capture_names_the_hook_that_produces_it(tmp_path):
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    run = fixture_run(tmp_path, devnode=None, hive=POST_GRAFT_HIVE)
    r = _run("--run", run)
    assert "FAIL   devnode-code" in r.stdout, r.stdout
    assert "startup-hook.bat" in r.stdout, r.stdout


def test_an_empty_capture_is_reported_as_a_query_that_returned_nothing(tmp_path):
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    run = fixture_run(tmp_path, devnode="No Instance(s) Available.\n", hive=POST_GRAFT_HIVE)
    r = _run("--run", run)
    assert "FAIL   devnode-code" in r.stdout, r.stdout
    assert "PNPDeviceID pattern" in r.stdout, r.stdout


def test_no_banked_hive_carries_the_value_the_plan_asks_for():
    """The premise, kept as a test so the remedy above is never argued from memory.

    `plan/0007#baseline-load` names the devnode's ConfigManagerErrorCode as a grading fact. It is
    a real fact and it is not in a hive: the instance key in the October audit hive, taken from a
    booted system, holds no such value either, so the only honest reader asks the running guest.
    If a future Windows ever writes it offline, this test says so out loud.
    """
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)
    sys.path.insert(0, str(ROOT))
    import registration_graft as rg

    read = 0
    for hive in sorted((ROOT / "work" / "evidence").glob("system-hive-*")):
        if not hive.is_file():
            continue
        h = rg.open_hive(str(hive))
        f = rg.facts(h)
        inst = rg.descend(h, f["enum_path"].split("\\"))
        names = sorted(rg.value_map(h, inst))
        assert "configmanagererrorcode" not in names, "%s contradicts the premise" % hive
        for child in (h.node_children(inst) or []):
            assert "configmanagererrorcode" not in sorted(rg.value_map(h, child)), \
                "%s has it in a subkey" % hive
        read += 1
    assert read >= 2, "the premise needs both banked hives to be present to be a premise"


def test_the_hook_asks_setupapi_for_the_card_it_cannot_read_offline():
    hook = (ROOT / "install-channel" / "startup-hook.bat").read_text()
    assert "Win32_PnPEntity" in hook and "ConfigManagerErrorCode" in hook
    assert "'%%VEN_1002&DEV_67B0%%'" in hook, "a single percent is a variable to cmd, not a wildcard"
    assert "devnode.txt" in hook
    sys.path.insert(0, str(ROOT))
    import channel_lint

    assert channel_lint.offending_letters(hook) == [], "the channel may name no letter but C"
    assert channel_lint.lines_that_are_not_batch(hook) == [], "every line has to be batch"


def test_golden_settings_writes_the_flag_and_proves_it_from_a_second_mount(tmp_path):
    """The end-to-end edit, on a real filesystem in a real qcow2.

    This is the only test here that mounts a block device, so it runs where the rig runs and
    says plainly when it could not run rather than counting as a pass.
    """
    import shutil
    import time

    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip("mounting an image needs passwordless sudo, which this run does not have")
    for tool in ("qemu-nbd", "mkfs.vfat", "sfdisk"):
        if not shutil.which(tool):
            pytest.skip("%s is not installed, so no image can be built for the edit" % tool)
    image = tmp_path / "d.raw"
    subprocess.run(["dd", "if=/dev/zero", "of=%s" % image, "bs=1M", "count=32"], check=True,
                   capture_output=True)
    script = "label: dos\nstart=2048, size=20000, type=c, bootable\n"
    subprocess.run(["sudo", "sfdisk", "--quiet", str(image)], input=script, text=True, check=True)
    subprocess.run(["sudo", "modprobe", "nbd", "max_part=8"], check=True)
    subprocess.run(["sudo", "qemu-nbd", "-f", "raw", "-c", "/dev/nbd0", str(image)], check=True)
    try:
        for _ in range(30):
            if Path("/dev/nbd0p1").exists():
                break
            time.sleep(0.5)
        subprocess.run(["sudo", "mkfs.vfat", "/dev/nbd0p1"], check=True, capture_output=True)
        mnt = tmp_path / "mnt"
        mnt.mkdir()
        subprocess.run(["sudo", "mount", "/dev/nbd0p1", str(mnt)], check=True)
        ini = ('[boot loader]\ntimeout=30\n'
               'default=multi(0)disk(0)rdisk(0)partition(1)\\WINDOWS\n'
               '[operating systems]\n'
               'multi(0)disk(0)rdisk(0)partition(1)\\WINDOWS="Windows XP Professional x64 Edition"'
               ' /noexecute=optin /fastdetect\n')
        subprocess.run(["sudo", "tee", str(mnt / "boot.ini")], input=ini, text=True, check=True,
                       capture_output=True)
        subprocess.run(["sudo", "umount", str(mnt)], check=True)
    finally:
        subprocess.run(["sudo", "qemu-nbd", "-d", "/dev/nbd0"], check=False)
    qcow = tmp_path / "d.qcow2"
    subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(image), str(qcow)],
                   check=True)

    r = subprocess.run([sys.executable, str(LOADER), "--image", qcow, "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 1 and "no" in r.stdout, r.stdout + r.stderr
    r = subprocess.run([sys.executable, str(LOADER), "--image", qcow, "--bootlog",
                        "--record", tmp_path / "rec"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "/bootlog" in r.stdout, r.stdout
    r = subprocess.run([sys.executable, str(LOADER), "--image", qcow, "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0 and "yes" in r.stdout, r.stdout + r.stderr
    assert (tmp_path / "rec" / "boot.ini.before").read_text().count("/bootlog") == 0
    assert (tmp_path / "rec" / "boot.ini.after").read_text().count("/bootlog") == 1
