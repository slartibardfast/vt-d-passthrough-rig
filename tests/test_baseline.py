"""Grading and image-setting checks for the control load, with no hardware involved.

These tests pin what `load-verify.py` is for: a control run either proves the things the plan
names, or the grader says which one it did not prove and names the command that would produce it.
Every fixture is synthetic except the registry hive, which is the tracked post-graft hive this
campaign wrote, read by the same reader that wrote it.

The reason for that choice is the defect this file exists to prevent: a grader that reports an
absent claim as a pass, or stays silent about a fact it could not read, is a hollow green run with
a timestamp on it. Several tests here are therefore about failure text, and two are about the
decoder: this grader once reported "201 lines, 0 naming the ATI driver" for a UTF-16 boot log whose
real content was 100 lines including "Loaded driver" once and "Did not load driver" six times.
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
HOOK = ROOT / "install-channel" / "startup-hook.bat"
POST_GRAFT_HIVE = ROOT / "work" / "evidence" / "system-hive-after-the-graft"

NO_HIVEX = "reading a hive needs the system Python's hivex, not the rig venv"
NONCE = "baseline-20261115T190000Z"
SERVICE_IMAGE = r"\??\D:\drv\ati2mtag.sys"

KD_CLEAN = r"""[19:27:54] FILE_IO api=0x3430 \??\D:\drv\ati2mtag.sys
[19:27:54] FILE_IO api=0x3430 \??\D:\drv\ati2mtag.sys
[19:27:55] FILE_IO api=0x3430 \??\D:\drv\ati2mtag.sys
[19:27:55] MODULE LOAD pid=0x80800000 base=0xfffffadfaae79000 name=ati2mtag.sys
[19:27:57] Error[PP_PowerPlay]: cislands_powertune.c[209]  Assertion '0' has failed: Unknown C.I. Device ID.
[19:27:57] Error[PP_PowerPlay]: cislands_hwmgr.c[6696]  Assertion '*vddc < 0xff01' has failed: Voltage value looks like a Leakage ID but it's not patched
[19:27:57] FILE_IO api=0x3430 \SystemRoot\System32\ati2dvag.dll
[19:27:57] MODULE LOAD pid=0x80800000 base=0xfffff97fff483000 name=\WINDOWS\System32\vga.dll
"""
KD_NO_MINIPORT = r"""[19:27:54] FILE_IO api=0x3430 \SystemRoot\system32\DRIVERS\videoprt.sys
[19:27:55] MODULE LOAD pid=0x80800000 base=0xfffffadfabb72000 name=\WINDOWS\System32\drivers\VIDEOPRT.SYS
"""
KD_BUGCHECK = KD_CLEAN + "[19:31:00] SYSTEM_SERVICE_EXCEPTION\n"
KD_ATI_DISPLAY = KD_CLEAN + "[19:27:58] MODULE LOAD pid=0x80800001 base=0xfffff97fff111000 name=\\WINDOWS\\System32\\ati2dvag.dll\n"

# The boot log is UTF-16LE with a BOM, because that is what the kernel writes. The fixture mirrors
# the real encoding on purpose: the bug this file caught was a reader that decoded it as single
# bytes, doubled every character with a NUL, and then reported that the driver was never loaded.
NTBTLOG = "\r\n".join([
    r"Windows XP System Boot Log",
    r"System boot time: 4.75 s",
    r"Loaded driver \SystemRoot\System32\ntoskrnl.exe",
    r"Loaded driver \SystemRoot\System32\hal.dll",
    r"Did not load driver " + SERVICE_IMAGE,
    r"Did not load driver " + SERVICE_IMAGE,
    r"Did not load driver " + SERVICE_IMAGE,
    r"Loaded driver " + SERVICE_IMAGE,
    r"Loaded driver \SystemRoot\System32\Drivers\HTTP.sys",
    r"Loaded driver \SystemRoot\system32\DRIVERS\srv.sys",
    r"Did not load driver \SystemRoot\system32\DRIVERS\ipnat.sys",
    "",
])
NTBTLOG_NO_LOAD = NTBTLOG.replace(r"Loaded driver " + SERVICE_IMAGE, r"Did not load driver " + SERVICE_IMAGE)

CAPTURE = "\r\n".join([
    "capture-start %s Sun 11/15/2026 19:28:05.68" % NONCE,
    "devicemap rc=0 nonce=%s" % NONCE,
    "The Winmgmt service is already running.",
    "pnpentity rc=0 nonce=%s" % NONCE,
    "videocontroller rc=0 nonce=%s" % NONCE,
    "enum-pci rc=0 nonce=%s" % NONCE,
    "service-query rc=1060 nonce=%s" % NONCE,
    "capture-end %s Sun 11/15/2026 19:28:07.11" % NONCE,
    "",
])
DEVNODE_OK = "\r\n".join([
    "Name=AMD Radeon Graphics Processor (0x67B0)",
    "PNPDeviceID=PCI\\VEN_1002&DEV_67B0&SUBSYS_E285174B&REV_00\\3&267A616A&0&20",
    "ConfigManagerErrorCode=0",
    "Status=OK",
    "",
])
DEVNODE_REFUSED = DEVNODE_OK.replace("ConfigManagerErrorCode=0", "ConfigManagerErrorCode=28")
VIDEOCTL = "\r\n".join([
    "Name=Radeon (R300) Video Controller",
    "AdapterRAM=268435456",
    "InstalledDisplayDrivers=ati2dvag",
    "CurrentHorizontalResolution=",
    "CurrentVerticalResolution=",
    "CurrentBitsPerPixel=",
    "ConfigManagerErrorCode=0",
    "Status=OK",
    "",
])
DEVICEMAP = r"""HKEY_LOCAL_MACHINE\HARDWARE\DEVICEMAP\VIDEO
    \Device\Video0    REG_SZ    \Registry\Machine\System\CurrentControlSet\Control\Video\{9AEF0D0F-2356-4E54-9833-53DAB38FD7CC}\0000
"""


def _run(*args, **env):
    environ = dict(os.environ)
    environ.update(env.pop("env", {}))
    return subprocess.run([sys.executable, str(VERIFIER), *[str(x) for x in args]],
                          capture_output=True, text=True, cwd=str(ROOT), env=environ)


def fixture_run(tmp_path, kd=KD_CLEAN, outcome="measured-shutdown", crash=False,
                ntbtlog=NTBTLOG, hive=None, devnode=DEVNODE_OK, capture=CAPTURE,
                videoctl=VIDEOCTL, nonce=NONCE):
    run = tmp_path / NONCE
    # A test may build two fixtures in one tmp_path to compare them, and the second must be
    # allowed to land on the first.
    (run / "evidence" / "WINDOWS" / "system32" / "config").mkdir(parents=True, exist_ok=True)
    (run / "kdc-detached.log").write_text(kd)

    def utf16(rel, text):
        (run / "evidence" / rel).write_text(text, encoding="utf-16")

    if ntbtlog is not None:
        utf16("WINDOWS/ntbtlog.txt", ntbtlog)
    if capture is not None:
        utf16("WINDOWS/capture.txt", capture)
    if devnode is not None:
        utf16("WINDOWS/devnode.txt", devnode)
    if videoctl is not None:
        utf16("WINDOWS/videoctl.txt", videoctl)
    (run / "evidence" / "WINDOWS" / "devicemap.txt").write_text(DEVICEMAP)
    if hive:
        (run / "evidence" / "WINDOWS" / "system32" / "config" / "system").write_bytes(
            Path(hive).read_bytes())
    (run / "run.json").write_text(json.dumps({
        "name": "baseline", "nonce": nonce, "outcome": outcome, "crash_suspected": crash,
        "golden": str(tmp_path / "os.qcow2"), "golden_sha256": "0" * 64,
        "overlay": str(run / "overlay.qcow2"), "channel": str(tmp_path / "gpudrv.img"),
        "channel_sha256": "1" * 64, "startup_hook_sha256": "2" * 64, "timeout_seconds": 900}))
    return run


def needs_hivex():
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip(NO_HIVEX)


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_control_that_proves_everything_it_can_passes(tmp_path):
    needs_hivex()
    r = _run("--run", fixture_run(tmp_path, hive=POST_GRAFT_HIVE))
    assert r.returncode == 0, r.stdout + r.stderr
    for line in ("PASS        image-path", "PASS        no-bugcheck", "PASS        capture",
                 "PASS        bootlog", "PASS        per-adapter-key", "PASS        devnode-code"):
        assert line in r.stdout, (line, r.stdout)
    assert "1 loaded, 3 refused" in r.stdout, r.stdout
    assert "verdict: PASS" in r.stdout, r.stdout
    assert "reported, not required" in r.stdout, "PowerPlay must not be demanded of a control"


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_run_where_the_miniport_never_mapped_fails_and_names_where_to_look(tmp_path):
    needs_hivex()
    r = _run("--run", fixture_run(tmp_path, kd=KD_NO_MINIPORT, hive=POST_GRAFT_HIVE))
    assert r.returncode == 1, r.stdout
    assert "FAIL        image-path" in r.stdout, r.stdout
    assert "registration_graft.py check" in r.stdout, r.stdout
    assert "verdict: FAIL (image-path)" in r.stdout, r.stdout


def test_a_missing_hive_fails_the_claims_that_need_it_rather_than_skipping_them(tmp_path):
    r = _run("--run", fixture_run(tmp_path))
    assert r.returncode == 1, r.stdout
    assert "FAIL        per-adapter-key" in r.stdout, r.stdout
    assert "remedy:" in r.stdout, "a failure without a named next command is not a report"


def test_a_run_killed_by_the_host_is_not_read_as_the_absence_of_a_crash(tmp_path):
    r = _run("--run", fixture_run(tmp_path, outcome="timeout-killed", crash=True))
    assert r.returncode == 1, r.stdout
    assert "FAIL        no-bugcheck" in r.stdout, r.stdout
    assert "did not end on its own" in r.stdout, r.stdout


def test_a_bugcheck_in_the_log_is_named(tmp_path):
    r = _run("--run", fixture_run(tmp_path, kd=KD_BUGCHECK))
    assert "FAIL        no-bugcheck" in r.stdout, r.stdout
    assert "SYSTEM_SERVICE_EXCEPTION" in r.stdout, r.stdout


def test_the_boot_log_is_decoded_as_utf16_which_is_what_the_kernel_writes(tmp_path):
    """The premise of this test is the bug: a single-byte reader saw 201 lines and no driver.

    Decoded correctly the same bytes say the loader refused the registered path six times and then
    loaded it, which is a finding about boot ordering rather than an absence of evidence.
    """
    r = _run("--run", fixture_run(tmp_path))
    assert "PASS        bootlog" in r.stdout, r.stdout
    assert "3 refused" in r.stdout, r.stdout
    assert "known fragility of loading off" in r.stdout, r.stdout


def test_a_boot_log_that_refused_the_path_never_passes(tmp_path):
    r = _run("--run", fixture_run(tmp_path, ntbtlog=NTBTLOG_NO_LOAD))
    assert "FAIL        bootlog" in r.stdout, r.stdout
    assert "never recorded a successful load" in r.stdout, r.stdout


def test_a_run_without_the_loader_log_cannot_claim_what_the_loader_did(tmp_path):
    r = _run("--run", fixture_run(tmp_path, ntbtlog=None))
    assert "FAIL        bootlog" in r.stdout, r.stdout
    assert "golden-settings.py" in r.stdout, "the remedy must name the tool that sets the flag"


def test_a_missing_capture_is_blamed_on_the_rig_not_on_the_device(tmp_path):
    """UNWITNESSED is a third state for a reason.

    A control run failed with `devnode-code FAIL` when the truth was that the Startup trigger in the
    golden predated the capture. Reading an absent answer as a negative answer points the next
    session at PnP when it should be looking at staging.
    """
    r = _run("--run", fixture_run(tmp_path, capture=None, devnode=None))
    assert "UNWITNESSED capture" in r.stdout, r.stdout
    assert "UNWITNESSED devnode-code" in r.stdout, r.stdout
    assert "Absence here says the capture did not happen" in r.stdout, r.stdout
    assert "startup-hook.bat" in r.stdout, r.stdout
    # The hive is missing in this fixture too, so per-adapter-key is on the list as well. What the
    # test is about is that the two capture claims blame the rig rather than the device.
    verdict = [l for l in r.stdout.splitlines() if l.startswith("verdict:")][0]
    assert "capture" in verdict and "devnode-code" in verdict, verdict


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_capture_belonging_to_another_run_cannot_witness_this_one(tmp_path):
    """The hardening that matters most: a file inherited from the golden grades as evidence.

    Nothing else in the rig can tell a capture this boot wrote from one the image handed over, so
    the hook stamps the run's nonce and the grader refuses a mismatch. Without it, a staging
    mistake produces a silent pass on every later run.
    """
    needs_hivex()
    other = CAPTURE.replace(NONCE, "baseline-20261114T000000Z")
    r = _run("--run", fixture_run(tmp_path, hive=POST_GRAFT_HIVE, capture=other))
    assert r.returncode == 1, r.stdout
    assert "FAIL        capture" in r.stdout, r.stdout
    assert "name run baseline-20261114T000000Z, not this run" in r.stdout, r.stdout


def test_a_capture_that_never_finished_is_unwitnessed(tmp_path):
    truncated = "\n".join(l for l in CAPTURE.splitlines() if not l.startswith("capture-end"))
    r = _run("--run", fixture_run(tmp_path, capture=truncated + "\n"))
    assert "UNWITNESSED capture" in r.stdout, r.stdout
    assert "began but did not finish" in r.stdout, r.stdout


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_a_refused_devnode_names_the_code(tmp_path):
    needs_hivex()
    r = _run("--run", fixture_run(tmp_path, hive=POST_GRAFT_HIVE, devnode=DEVNODE_REFUSED))
    assert "FAIL        devnode-code" in r.stdout, r.stdout
    assert "ConfigManagerErrorCode=[28]" in r.stdout, r.stdout


@pytest.mark.skipif(not POST_GRAFT_HIVE.is_file(), reason="the tracked post-graft hive is not present")
def test_an_empty_devnode_answer_is_a_query_failure_not_a_refusal(tmp_path):
    needs_hivex()
    r = _run("--run", fixture_run(tmp_path, hive=POST_GRAFT_HIVE,
                                  devnode="No Instance(s) Available.\n"))
    assert "FAIL        devnode-code" in r.stdout, r.stdout
    assert "PNPDeviceID pattern" in r.stdout, r.stdout


def test_powerplay_is_reported_as_its_baseline_fingerprint(tmp_path):
    r = _run("--run", fixture_run(tmp_path))
    assert "MEASURED    powerplay" in r.stdout, r.stdout
    assert "distinct sites" in r.stdout, r.stdout
    assert "cislands_" in r.stdout, r.stdout
    assert "FAIL        powerplay" not in r.stdout, r.stdout


def test_powerplay_is_demanded_only_of_a_run_that_claims_it(tmp_path):
    r = _run("--run", fixture_run(tmp_path), "--expect-powerplay")
    assert "PASS        powerplay" in r.stdout, r.stdout
    r = _run("--run", fixture_run(tmp_path, kd=KD_NO_MINIPORT), "--expect-powerplay")
    assert "FAIL        powerplay" in r.stdout, r.stdout
    assert "produced none" in r.stdout, r.stdout


def test_the_display_path_is_resolved_from_the_module_stream(tmp_path):
    """The frame was always a proxy. This is the fact the proxy stood for.

    With the registration correct, win32k opens `ati2dvag.dll`, never maps it, and maps `vga.dll`.
    That is the control's measured answer to the campaign's question, and no screen capture is
    involved in reaching it.
    """
    r = _run("--run", fixture_run(tmp_path))
    assert "MEASURED    display-path" in r.stdout, r.stdout
    assert "ati2dvag.dll opened, never mapped, and vga.dll mapped instead" in r.stdout, r.stdout
    assert "AdapterRAM=268435456" in r.stdout, r.stdout


def test_a_run_that_claims_the_ati_display_driver_is_held_to_it(tmp_path):
    r = _run("--run", fixture_run(tmp_path), "--expect-display", "ati2dvag")
    assert r.returncode == 1, r.stdout
    assert "FAIL        display-path" in r.stdout, r.stdout

    good = _run("--run", fixture_run(tmp_path, kd=KD_ATI_DISPLAY), "--expect-display", "ati2dvag")
    assert "PASS        display-path" in good.stdout, good.stdout


def test_a_frame_is_reported_as_unattempted_and_not_as_a_missing_file(tmp_path):
    """Both sentences are true of the rig, and they mean different things.

    There is no console to dump on this argv, and the GLKVM feed that can see the monitor needs an
    operator credential. Saying "no frame was captured" hides which of those is the reason.
    """
    r = _run("--run", fixture_run(tmp_path))
    assert "MEASURED    frame" in r.stdout, r.stdout
    assert "no display device" in r.stdout and "GLKVM" in r.stdout, r.stdout


def test_a_run_directory_that_is_not_there_says_how_to_make_one(tmp_path):
    r = _run("--run", tmp_path / "no-such-run")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "run-experiment.sh" in (r.stdout + r.stderr), r.stderr


def test_the_evidence_list_reads_as_the_collector_reads_it(tmp_path):
    tree = tmp_path / "vol" / "WINDOWS"
    (tree / "system32" / "config").mkdir(parents=True)
    (tree / "devicemap.txt").write_text(DEVICEMAP)
    (tree / "capture.txt").write_text(CAPTURE, encoding="utf-16")
    r = subprocess.run([str(ROOT / "run-experiment.sh"), "--list-evidence", str(tmp_path / "vol")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    states = {row[0]: row[1] for row in (l.split("\t") for l in r.stdout.strip().splitlines())}
    for owed in ("WINDOWS/capture.txt", "WINDOWS/devnode.txt", "WINDOWS/videoctl.txt",
                 "WINDOWS/enum-pci.txt", "WINDOWS/setupapi.log", "WINDOWS/ntbtlog.txt"):
        assert owed in states, "%s is not owed by the run" % owed
    assert states["WINDOWS/capture.txt"] == "present"
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


def test_the_runner_configures_the_overlay_and_leaves_no_shared_state_behind():
    src = (ROOT / "run-experiment.sh").read_text()
    # The settings are assembled into one array now, because a run may also be asked to install a
    # display DLL, and a second invocation line would be a second thing to keep honest. What the
    # assertion protects is unchanged: the settings land on the image that boots, the overlay.
    assert 'SETTINGS=(--image "$OVERLAY"' in src, "the settings must go on the image that boots"
    assert "SETTINGS+=(--bootlog" in src, "the loader flag is a per-run setting"
    assert "--startup-hook" in src and '--runid "$NONCE"' in src, \
        "the trigger and the nonce are per-run inputs, not baked state"
    assert 'SETTINGS+=(--display-dll "$DISPLAY_DLL")' in src, \
        "a run that installs a file into the overlay has to say which file"
    assert "display_dll_sha256=" in src, \
        "the bytes that were installed belong in the run record, or a later reader cannot tell"
    assert "refusing to boot a run that records nothing" in src
    assert 'LOGDIR="$RUN"' in src, "a KD log outside the run directory belongs to no run"
    assert 'KD_SOCK="/tmp/opencode/kd-$NONCE.sock"' in src, \
        "a shared KD socket is an accept race, and a per-run socket must stay under 108 bytes"
    assert 'SEABIOS_LOG="$RUN/seabios.log"' in src, "firmware output per run or not at all"
    assert "qemu-nbd -r -f qcow2" in src, "reading evidence out of a run must not be able to write it"
    assert "sudo cp -p" in src, "mtimes are how an inherited file is spotted"
    assert "MANIFEST.tsv" in src
    assert "system_powerdown" in src, "the run is closed by a request the guest can honour"
    assert '"$RIG/kdc-running.sh" stop' in src, "a collector left alive poisons the next run"
    assert "feed: none at" in src, "a refused frame must be printed as refused"


def test_the_trigger_uses_a_formatter_this_guest_has():
    """`wmic /format:list` does not exist on this install, and failed silently.

    The XSL inventory in the overlay's `WINDOWS\\system32\\wbem\\` is csv, hform, htable, mof,
    rawxml, texttable and textvaluelist. There is no `list.xsl`, so the query that was supposed to
    answer the devnode question would have written `Invalid XSL format (or) file name.` even with a
    freshly staged hook.
    """
    hook = HOOK.read_text()
    assert "textvaluelist" in hook, "the formatter must be one of the seven the guest ships"
    assert "/format:list" not in hook, "list.xsl is not on this volume"
    assert "Win32_PnPEntity" in hook and "Win32_VideoController" in hook
    assert "net start winmgmt" in hook, "a cold repository makes the first query fail"
    assert "capture-start" in hook and "capture-end" in hook and "rc=%ERRORLEVEL%" in hook
    assert "runid.txt" in hook, "the hook must read the nonce the runner wrote"
    sys.path.insert(0, str(ROOT))
    import channel_lint

    assert channel_lint.offending_letters(hook) == [], "the channel may name no letter but C"
    assert channel_lint.lines_that_are_not_batch(hook) == [], "every line has to be batch"


def test_the_linter_knows_the_commands_the_captures_need():
    sys.path.insert(0, str(ROOT))
    import channel_lint

    for head in ("sc", "net", "wmic", "reg"):
        assert head in channel_lint.BATCH_HEADS, "%s is a command, not prose" % head
    assert channel_lint.lines_that_are_not_batch("sc query ati2mtag > out.txt\r\n") == []


def test_no_banked_hive_carries_the_value_the_plan_asks_for():
    """The premise, kept as a test so the remedy is never argued from memory.

    `plan/0007#baseline-load` names the devnode's ConfigManagerErrorCode as a grading fact. It is a
    real fact and it is not in a hive: the PnP manager creates the instance's Control subkey with
    REG_OPTION_VOLATILE, so it is never written to the file, and the two banked SYSTEM hives, one of
    them taken from a booted system, agree. If a future Windows ever writes it offline, this test
    says so out loud.
    """
    needs_hivex()
    sys.path.insert(0, str(ROOT))
    import registration_graft as rg

    read = 0
    for hive in sorted((ROOT / "work" / "evidence").glob("system-hive-*")):
        if not hive.is_file():
            continue
        h = rg.open_hive(str(hive))
        inst = rg.descend(h, rg.facts(h)["enum_path"].split("\\"))
        assert "configmanagererrorcode" not in sorted(rg.value_map(h, inst)), hive
        for child in (h.node_children(inst) or []):
            assert "configmanagererrorcode" not in sorted(rg.value_map(h, child)), hive
        read += 1
    assert read >= 2, "the premise needs both banked hives present to be a premise"


def test_a_monitor_refusal_is_a_failure_and_not_a_zero():
    """`monq.py` used to exit 0 whatever the monitor answered, so the runner printed filenames for
    screendumps that never happened. There is no console to dump on this argv, and that is a fact
    the grader now states, not a hole it papers over."""
    sys.path.insert(0, str(ROOT))
    import monq

    assert monq.looks_like_a_refusal("There is no console to take a screendump from")
    assert monq.looks_like_a_refusal("Invalid parameter")
    assert not monq.looks_like_a_refusal("VM status: running")


def test_the_collector_is_found_by_its_argv_and_not_by_a_substring():
    """A mention of the script name in some other process must not count as a collector."""
    helper = ROOT / "kdc-running.sh"
    assert helper.is_file() and os.access(helper, os.X_OK)
    before = subprocess.run([str(helper), "pids"], capture_output=True, text=True).stdout.split()
    decoy = subprocess.Popen(["/bin/bash", "-c", "exec sleep 20 # kdc.py would match a substring"])
    try:
        during = subprocess.run([str(helper), "pids"], capture_output=True, text=True).stdout.split()
        assert during == before, "a process that only mentions kdc.py was counted as a collector"
        assert subprocess.run([str(helper), "check"], capture_output=True).returncode == 1
    finally:
        decoy.kill()
        decoy.wait()
    assert subprocess.run([str(helper), "nonsense"], capture_output=True).returncode == 2


def test_golden_settings_says_what_is_missing_rather_than_tracebacking(tmp_path):
    r = subprocess.run([sys.executable, str(LOADER), "--image", str(tmp_path / "nope.qcow2"),
                        "--bootlog"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert r.stderr.startswith("golden-settings:"), r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_a_bare_check_refuses_instead_of_reporting_nothing(tmp_path):
    """An empty report with exit 0 is the shape of a check that checked nothing."""
    qcow = tmp_path / "empty.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(qcow), "1M"], check=True,
                   capture_output=True)
    r = subprocess.run([sys.executable, str(LOADER), "--image", qcow, "--check"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "say which setting" in r.stderr, r.stderr


def test_golden_settings_refuses_a_live_guest_a_bad_nonce_and_a_missing_template(tmp_path):
    src = LOADER.read_text()
    assert "guest-running.sh" in src, "the liveness rule has one home"
    assert '"sudo", "qemu-nbd", "-f", "qcow2"' in src, "the format is named, not probed"
    assert "refusing to guess which one boots" in src, "two entries and no default is a guess"

    qcow = tmp_path / "g.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(qcow), "1M"], check=True,
                   capture_output=True)
    r = subprocess.run([sys.executable, str(LOADER), "--image", str(qcow), "--runid",
                        "two words"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "plain token" in r.stderr, r.stderr
    r = subprocess.run([sys.executable, str(LOADER), "--image", str(qcow), "--startup-hook",
                        str(tmp_path / "absent.bat")], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "no such startup hook template" in r.stderr, r.stderr


def test_the_collector_guard_is_in_the_launcher_that_starts_a_boot():
    """Refusing a second QEMU without refusing a second collector would be half a guard."""
    src = (ROOT / "start-boot-detached.sh").read_text()
    assert "kdc-running.sh" in src and "race this boot for the KD socket" in src
    assert "KD_SOCK=" in src and "SEABIOS_LOG=" in src, "the per-run paths must reach the children"


def test_a_relative_golden_still_yields_an_overlay_that_backs_onto_it(tmp_path):
    """Covered in test_iteration_loop.py too; kept here because the control run is what found it."""
    golden = tmp_path / "g.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(golden), "64M"], check=True,
                   capture_output=True)
    channel = tmp_path / "gpudrv.img"
    channel.write_bytes(b"channel")
    r = subprocess.run([str(ROOT / "run-experiment.sh"), "--golden", str(golden),
                        "--channel", str(channel), "--name", "relcheck", "--runs",
                        str(tmp_path / "runs"), "--prepare-only", "--no-bootlog"],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nonce:" in r.stdout, r.stdout
    manifest = json.loads(next((tmp_path / "runs").glob("relcheck-*/run.json")).read_text())
    assert manifest["nonce"].startswith("relcheck-")


def test_golden_settings_sets_all_three_and_proves_each_from_a_second_mount(tmp_path):
    """The end-to-end edit, on a real filesystem in a real qcow2.

    This is the only test here that mounts a block device, so it runs where the rig runs and says
    plainly when it cannot rather than counting as a pass.
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
        # A vfat volume is case-insensitive, so the Startup path that matters on NTFS is reachable
        # here by any case. The fixture tests the write and the read-back, not the case folding.
        startup = (mnt / "Documents and Settings" / "All Users" / "Start Menu" / "Programs"
                   / "Startup")
        subprocess.run(["sudo", "mkdir", "-p", str(startup), str(mnt / "WINDOWS")], check=True)
        subprocess.run(["sudo", "chmod", "-R", "777", str(mnt / "Documents and Settings"),
                        str(mnt / "WINDOWS")], check=False)
        subprocess.run(["sudo", "tee", str(startup / "letters.bat")],
                       input="@echo off\r\nrem an inherited trigger\r\n", text=True, check=True,
                       capture_output=True)
        subprocess.run(["sudo", "umount", str(mnt)], check=True)
    finally:
        subprocess.run(["sudo", "qemu-nbd", "-d", "/dev/nbd0"], check=False)
    qcow = tmp_path / "d.qcow2"
    subprocess.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(image), str(qcow)],
                   check=True)

    def ask(*flags):
        return subprocess.run([sys.executable, str(LOADER), "--image", qcow, *flags],
                              capture_output=True, text=True, cwd=str(ROOT))

    r = ask("--bootlog", "--startup-hook", str(HOOK), "--runid", NONCE, "--check")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "not set: bootlog, startup-hook, runid" in r.stdout, r.stdout

    r = ask("--bootlog", "--startup-hook", str(HOOK), "--runid", NONCE, "--record", tmp_path / "rec")
    assert r.returncode == 0, r.stdout + r.stderr
    for line in ("set startup-hook", "set runid", "read back startup-hook", "read back runid"):
        assert line in r.stdout, (line, r.stdout)

    r = ask("--bootlog", "--startup-hook", str(HOOK), "--runid", NONCE, "--check")
    assert r.returncode == 0, r.stdout + r.stderr

    rec = tmp_path / "rec"
    assert (rec / "boot.ini.before").read_text().count("/bootlog") == 0
    assert (rec / "boot.ini.after").read_text().count("/bootlog") == 1
    assert "inherited trigger" in (rec / "letters.bat.before").read_text()
    assert (rec / "letters.bat.after").read_bytes() == HOOK.read_bytes()


def test_the_kd_socket_stays_inside_the_kernels_path_limit(tmp_path):
    """A per-run socket is the rule; the run directory is longer than an AF_UNIX path may be.

    The first run after the socket moved per-run died at once with "UNIX socket path ... is too
    long". The guard is here so the next long --runs path is named before a boot is attempted.
    """
    src = (ROOT / "run-experiment.sh").read_text()
    assert "KD_SOCK=\"/tmp/opencode/kd-$NONCE.sock\"" in src, \
        "the socket must stay short, so it lives beside the run rather than inside it"
    assert "-gt 100" in src and "AF_UNIX at 108" in src, "the limit has to be stated, not obeyed silently"


def test_an_early_vanishing_is_not_recorded_as_a_measured_shutdown():
    """"The process is gone" and "the guest closed itself" are different claims.

    Labelling a ten-second disappearance as a shutdown hands the grader a clean outcome for a run
    that never reached a session, and the grader's no-bugcheck claim then passes on nothing.
    """
    src = (ROOT / "run-experiment.sh").read_text()
    assert "MIN_LIVE=120" in src
    assert "OUTCOME=ended-early" in src
    assert '"lived_seconds=$ELAPSED"' in src, "how long the guest lived belongs in the run record"
    assert "$([ \"$OUTCOME\" = timeout-killed ] || [ \"$OUTCOME\" = ended-early ] && echo true" in src


def test_ended_early_fails_the_no_bugcheck_claim(tmp_path):
    r = _run("--run", fixture_run(tmp_path, outcome="ended-early", crash=True))
    assert "FAIL        no-bugcheck" in r.stdout, r.stdout
    assert "did not end on its own" in r.stdout, r.stdout


def test_the_capture_claim_states_the_guest_clock_offset(tmp_path):
    """Collected mtimes are guest-written and run eight hours ahead on this guest.

    `runid.txt` is written by the host into the overlay and `capture.txt` by the guest, both inside
    the same evidence set, so their mtime difference is the offset. Stating it on the witness line is
    cheaper than letting a reader order two events by a timestamp that belongs to another clock.
    """
    run = fixture_run(tmp_path)
    host_written = run / "evidence" / "WINDOWS" / "runid.txt"
    host_written.write_text(NONCE + "\r\n", encoding="latin-1")
    os.utime(host_written, (1_700_000_000, 1_700_000_000))
    guest_written = run / "evidence" / "WINDOWS" / "capture.txt"
    later = 1_700_000_000 + 8 * 3600
    os.utime(guest_written, (later, later))
    r = _run("--run", run)
    assert "guest mtimes read +8.0 h" in r.stdout, r.stdout
