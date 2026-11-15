"""The record beside a golden: what it is, what it came from, and how it came to be.

Two things are asserted about a golden in this campaign, and each needs its own check. The file is
what its manifest says it is, which is offline and cheap. And it carries the settled state every run
is written against, which means reading the registry inside the image. `golden_verify.py` does the
first, `golden-state.py` the second, and `golden-build.sh --verify` runs both.

The reason for being fussy: the golden is the read-only parent of every run. A manifest with no
check behind it is a promise, a silently edited golden looks identical to an untouched one, and a run
whose parent changed under it still prints a clean line.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "golden_verify.py"
STATE = ROOT / "golden-state.py"
CAMPAIGN_GOLDEN = ROOT / "work" / "goldens" / "os.qcow2"
NO_SUDO = "mounting an image needs passwordless sudo, which this run does not have"


def make_golden(tmp_path, name="g", backing=None):
    """A real qcow2, and a manifest that matches it, built the way the tool builds one."""
    golden = tmp_path / ("%s.qcow2" % name)
    cmd = ["qemu-img", "create", "-f", "qcow2"]
    if backing:
        cmd += ["-b", str(backing), "-F", "qcow2"]
    cmd += [str(golden), "64M"]
    subprocess.run(cmd, check=True, capture_output=True)
    meta = json.loads(subprocess.run(["qemu-img", "info", "--output=json", str(golden)],
                                     check=True, capture_output=True, text=True).stdout)
    manifest = golden.with_suffix(".json")
    manifest.write_text(json.dumps({
        "sha256": hashlib.sha256(golden.read_bytes()).hexdigest(),
        "bytes": golden.stat().st_size,
        "format": "qcow2",
        "virtual_bytes": meta["virtual-size"],
        "source_identity": {"disk_signature": "0x2a032a02", "partition_offset": 32256},
        "source": str(tmp_path / "os.raw"),
    }, indent=2))
    return golden, manifest


def ask(*args):
    return subprocess.run([sys.executable, str(VERIFY), *[str(a) for a in args]],
                          capture_output=True, text=True)


def test_a_golden_that_matches_its_manifest_passes(tmp_path):
    golden, manifest = make_golden(tmp_path)
    r = ask(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sha256" in r.stdout and "match" in r.stdout, r.stdout
    assert "verdict: PASS, every golden matches its manifest (1 checked)" in r.stdout, r.stdout
    assert "source %s absent on this machine, reported not failed" % (tmp_path / "os.raw") \
        in r.stdout, "a source on another machine is a fact about the machine, not a defect"


def test_an_image_edited_under_its_manifest_fails_and_says_which(tmp_path):
    """The whole point. Without this check, a golden that moved still prints a clean line."""
    golden, manifest = make_golden(tmp_path)
    golden.write_bytes(golden.read_bytes() + b"one more byte")
    r = ask(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "MISMATCH" in r.stdout, r.stdout
    assert "verdict: FAIL" in r.stdout, r.stdout
    assert "golden-build.sh --amend" in r.stdout, "the remedy has to name the tool, not a hand edit"


def test_a_golden_with_no_manifest_is_named_rather_than_skipped(tmp_path):
    golden, manifest = make_golden(tmp_path)
    manifest.unlink()
    r = ask(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "no manifest beside it" in r.stdout, r.stdout


def test_a_manifest_that_omits_a_field_is_a_hazard_not_a_shrug(tmp_path):
    golden, manifest = make_golden(tmp_path)
    data = json.loads(manifest.read_text())
    del data["source_identity"]
    manifest.write_text(json.dumps(data))
    r = ask(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "manifest omits source_identity" in r.stdout, r.stdout
    assert "cannot be re-derived" in r.stdout, "the reader needs the reason, not just the field name"


def test_a_copy_on_write_child_is_not_a_golden(tmp_path):
    """call/0010: a golden with a parent changes whenever the parent does, so it cannot be shared.

    `qemu-img convert` is what makes the campaign golden standalone, and this is the check that
    keeps a later session from building the wrong thing and calling it a golden.
    """
    parent = tmp_path / "parent.qcow2"
    subprocess.run(["qemu-img", "create", "-f", "qcow2", str(parent), "64M"], check=True,
                   capture_output=True)
    child, manifest = make_golden(tmp_path, name="child", backing=parent)
    r = ask(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "which makes it not a golden" in r.stdout, r.stdout


def test_a_truncated_manifest_field_fails_on_its_own_line(tmp_path):
    golden, manifest = make_golden(tmp_path)
    data = json.loads(manifest.read_text())
    data["bytes"] = data["bytes"] + 1
    manifest.write_text(json.dumps(data))
    r = ask(tmp_path)
    assert r.returncode == 1, r.stdout
    assert "bytes" in r.stdout and "manifest" in r.stdout, r.stdout


def test_a_directory_with_no_golden_says_so(tmp_path):
    r = ask(tmp_path)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "holds no golden" in (r.stdout + r.stderr), r.stderr


def test_a_missing_directory_is_named(tmp_path):
    r = ask(tmp_path / "nope")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no such directory" in (r.stdout + r.stderr), r.stderr


def test_the_amend_rewrites_the_record_without_touching_the_image(tmp_path):
    """Provenance belongs in the record, and the record is written by the tool.

    The campaign's first golden came from a disk that had been through two aborted boots and one
    clean shutdown. Saying so later must not mean editing a manifest by hand, and it must not change
    a byte of the golden, because every run's `golden_sha256` rests on that file staying put.
    """
    golden = tmp_path / "os.qcow2"
    golden.write_bytes(b"qcow2-shaped bytes: the amend reads and hashes only")
    manifest = tmp_path / "os.json"
    manifest.write_text(json.dumps({"golden": str(golden), "sha256": "0" * 64, "bytes": 1,
                                    "source": str(tmp_path / "os.raw")}))
    source = tmp_path / "os.raw"
    source.write_bytes(b"a source disk is also only a fixture here")
    before = golden.read_bytes()

    r = subprocess.run([sys.executable, str(ROOT / "golden_amend.py"), str(golden), str(manifest),
                        str(source), "converted after two aborted boots and one clean shutdown"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(manifest.read_text())
    assert data["sha256"] == hashlib.sha256(before).hexdigest(), "a stale hash in a fresh record"
    assert data["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert data["provenance"].startswith("converted after")
    assert data["provenance_recorded"].endswith("Z")
    assert golden.read_bytes() == before, "amending a record must not edit the image it describes"

    r = subprocess.run([sys.executable, str(ROOT / "golden_amend.py"), str(golden), str(manifest),
                        str(tmp_path / "absent.raw")], capture_output=True, text=True)
    assert r.returncode == 2 and "no such source image" in r.stderr, r.stderr


def test_the_build_records_the_source_digest_it_too():
    """A manifest that names a source it cannot be checked against asserts rather than records."""
    src = (ROOT / "golden-build.sh").read_text()
    assert '"source_sha256": sha256_stream(source)' in src
    assert "--amend" in src and "golden_amend.py" in src
    assert "--verify" in src and "golden_verify.py" in src
    assert "golden-state.py" in src, "the settled-state read has to be part of the verify line"


def test_the_verify_line_is_the_one_the_plan_calls():
    """plan/0007#state-goldens grades itself with `./golden-build.sh --verify work/goldens`."""
    src = (ROOT / "golden-build.sh").read_text()
    assert 'VERIFY="" ' in src or 'VERIFY=""' in src
    assert 'test -d "$VERIFY"' in src
    assert "|| status=1" in src, "one golden failing must not be swallowed by the loop"
    assert "exit $status" in src


def test_the_campaign_golden_and_its_manifest_agree():
    """Not a fixture: the file every run of this milestone backs onto."""
    if not CAMPAIGN_GOLDEN.is_file():
        pytest.skip("the campaign golden is not on this machine")
    r = ask(ROOT / "work" / "goldens")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verdict: PASS" in r.stdout, r.stdout
    assert "backing file none" in r.stdout, r.stdout


def needs_sudo_and_hivex():
    if subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode != 0:
        pytest.skip(NO_SUDO)
    for tool in ("qemu-nbd", "mount"):
        if not shutil.which(tool):
            pytest.skip("%s is not installed, so no image can be attached" % tool)
    try:
        import hivex  # noqa: F401
    except ImportError:
        pytest.skip("reading a hive needs the system Python's hivex, not the rig venv")


@pytest.mark.skipif(not CAMPAIGN_GOLDEN.is_file(), reason="the campaign golden is not on this machine")
def test_the_golden_carries_the_settled_state_a_run_is_written_against(tmp_path):
    """The plan's claim about the golden, checked against the image rather than taken on trust.

    Every run pays for these settings once because they are baked in: the boot-start service key
    naming the channel path, the hard-error suppression that keeps the `Windows - No Disk` modal from
    blocking an instrument, the per-adapter display selection, the letter pinned to the driver
    volume, and the unattended logon that lets a capture run at all. If any of these is absent, the
    reader says which and what to run, rather than letting a boot discover it.
    """
    needs_sudo_and_hivex()
    r = subprocess.run([sys.executable, str(STATE), "--golden", str(CAMPAIGN_GOLDEN)],
                       capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 0, r.stdout + r.stderr
    for line in ("service ImagePath", "service Start", "ErrorMode",
                 "per-adapter InstalledDisplayDrivers", "drive letter pinned",
                 "autologon enabled"):
        assert line in r.stdout, (line, r.stdout)
    assert "\\??\\D:\\drv\\ati2mtag.sys" in r.stdout, r.stdout
    assert "verdict: PASS" in r.stdout, r.stdout
    assert "no leftover" not in r.stdout


@pytest.mark.skipif(not CAMPAIGN_GOLDEN.is_file(), reason="the campaign golden is not on this machine")
def test_the_settled_state_is_recorded_in_the_manifest():
    """A fact read off an image belongs in that image's record, with the time it was read."""
    needs_sudo_and_hivex()
    manifest = json.loads((ROOT / "work" / "goldens" / "os.json").read_text())
    settled = manifest.get("settled")
    assert settled, "the golden's manifest carries no settled-state block; run golden-state --record"
    assert settled["service_imagepath"] == r"\??\D:\drv\ati2mtag.sys"
    assert settled["service_start"] == 1 and settled["errormode"] == 2
    assert settled["dev_drivers"] == ["ati2dvag"] and settled["dev_vgacompat"] == 0
    assert settled["letters_pinned"] is True
    assert str(settled["autologon"]["autoadminlogon"]) == "1"
    assert settled["bootlog_in_golden"] is False, \
        "boot logging is applied to the overlay per run, so the golden stays a byte-exact conversion"
    assert (ROOT / "work" / "goldens" / "os.json").read_text().count("settled_recorded") == 1


def test_the_state_reader_refuses_to_answer_while_a_guest_holds_the_images():
    """A golden read from under a live guest is a golden of no state anyone can name."""
    src = STATE.read_text()
    assert "guest-running.sh" in src, "the liveness rule has one home"
    assert '"-r", "-f", "qcow2"' in src, "the read is read-only and the format is named, not probed"
    assert "qemu-nbd\", \"-d" in src or "qemu-nbd\", \"-d\"" in src
    assert "free_device" in src, "sitting on a device another run is using is not a free read"


def test_a_state_reader_that_cannot_find_a_path_says_which_volume_was_wrong():
    src = STATE.read_text()
    assert "no boot.ini at its root" in src
    assert "matched %d lines" in src, "two entries and no default is a guess, and must be refused"
    assert "NTLDR can boot" in src, "a volume with no SYSTEM hive has to be named as such"
