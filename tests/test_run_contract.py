"""The run's own contract: every field derived from evidence, and each derivation reachable by a test.

`pp_verdict.py`, the campaign's strict PowerPlay grader, will not answer unless a run record carries
eight named fields. That shape invites the wrong fix: type constants into the script, watch the lane
go green, and never learn what the boot did. These tests are what makes the wrong fix impossible, by
driving the writer over fixtures where each claim is false in isolation.

The general rule they hold: a checker that cannot report a false claim is not checking the claim, it
is restating it.
"""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "run_contract.py"
BASE_BLOB = ROOT / "work" / "stageE" / "ati2mtag-s5_accessor_blob.sys"
NONCE = "descriptor-20261115T210000Z"
CANDIDATE = "2" * 64
OTHER = "8" * 64

CAPTURE = "\r\n".join([
    "capture-start %s Sun 11/15/2026 21:00:05.68" % NONCE,
    "devicemap rc=0 nonce=%s" % NONCE,
    "pnpentity rc=0 nonce=%s" % NONCE,
    "videocontroller rc=0 nonce=%s" % NONCE,
    "capture-end %s Sun 11/15/2026 21:00:07.11" % NONCE,
    "",
])
VIDEOCTL_RESOLVED = "\r\n".join(["Name=Radeon (R300) Video Controller",
                                 "CurrentHorizontalResolution=1280",
                                 "CurrentVerticalResolution=1024", "", ""])
VIDEOCTL_NO_MODE = "\r\n".join(["Name=Standard VGA Graphics Adapter",
                                "CurrentHorizontalResolution=", "Status=OK", "", ""])


def fixture_run(tmp_path, capture=CAPTURE, videoctl=VIDEOCTL_RESOLVED, kd="", live="", catches="",
                deployment=None, channel_manifest=None, nonce=NONCE):
    run = tmp_path / NONCE
    (run / "evidence" / "WINDOWS" / "system32" / "config").mkdir(parents=True)
    for name, text in (("WINDOWS/capture.txt", capture), ("WINDOWS/videoctl.txt", videoctl)):
        if text is not None:
            (run / "evidence" / name).write_text(text, encoding="utf-16")
    if kd:
        (run / "kdc-detached.log").write_text(kd)
    (run / "captor-catches.log").write_text(catches)
    # The strict grader reads the collector's own log for its markers. Both collector files are here
    # because a real boot writes the driver's prints to both, and a fixture that filled only one would
    # pass a test that could not tell which file a verdict came from.
    (run / "kdc-live.log").write_text(live or kd or "live collector output\n")
    if deployment:
        (run / "deployment.json").write_text(json.dumps(deployment))
    if channel_manifest:
        (run / "channel-manifest.json").write_text(json.dumps(channel_manifest))
    record = {"name": "descriptor", "nonce": nonce}
    if channel_manifest:
        record["channel_manifest"] = str(run / "channel-manifest.json")
    (run / "run.json").write_text(json.dumps(record))
    return run


def ask(run, nonce=NONCE, outcome="measured-shutdown"):
    return subprocess.run([sys.executable, str(CONTRACT), str(run / "run.json"), str(run), nonce,
                           outcome], capture_output=True, text=True, cwd=str(ROOT))


def read(run):
    return json.loads((run / "run.json").read_text())


def test_a_signed_witness_makes_the_session_claims_true(tmp_path):
    run = fixture_run(tmp_path)
    r = ask(run)
    assert r.returncode == 0, r.stdout + r.stderr
    data = read(run)
    assert data["session_ready"] is True and data["desktop_reached"] is True
    assert data["boot_id"] == NONCE
    assert data["abandoned"] is False and data["manual_resume"] is False
    assert data["expected_assert"] is None and data["expected_captor"] == []
    assert "session_rule" in data and "desktop_rule" in data, "a claim with no stated rule is a guess"


def test_a_capture_from_another_run_never_marks_this_session_ready(tmp_path):
    stale = CAPTURE.replace(NONCE, "descriptor-20261114T000000Z")
    run = fixture_run(tmp_path, capture=stale)
    ask(run)
    data = read(run)
    assert data["session_ready"] is False and data["desktop_reached"] is False


def test_a_capture_that_never_finished_is_not_a_session(tmp_path):
    truncated = "\r\n".join(l for l in CAPTURE.splitlines() if not l.startswith("capture-end"))
    run = fixture_run(tmp_path, capture=truncated + "\r\n")
    ask(run)
    assert read(run)["session_ready"] is False


def test_a_run_with_no_capture_at_all_is_not_a_session(tmp_path):
    run = fixture_run(tmp_path, capture=None)
    ask(run)
    assert read(run)["session_ready"] is False


def test_a_session_without_a_reported_resolution_has_not_reached_a_desktop(tmp_path):
    """The two claims are different, and collapsing them is how a VGA session reads as a desktop."""
    run = fixture_run(tmp_path, videoctl=VIDEOCTL_NO_MODE)
    ask(run)
    data = read(run)
    assert data["session_ready"] is True, "the trigger still ran, so the session was real"
    assert data["desktop_reached"] is False
    assert "none reported" in data["desktop_rule"], data["desktop_rule"]


def test_a_break_in_or_a_catch_is_the_only_way_manual_resume_becomes_true(tmp_path):
    for label, kd, catches in (("break-in", "[21:00:01] breakin sent\n", ""),
                               ("catch", "", "PP first chance at cislands_hwmgr.c[6696]\n")):
        run = fixture_run(tmp_path / label, kd=kd, catches=catches)
        ask(run)
        assert read(run)["manual_resume"] is True, label
    quiet = fixture_run(tmp_path / "quiet")
    ask(quiet)
    assert read(quiet)["manual_resume"] is False


def test_a_run_the_host_killed_is_marked_abandoned(tmp_path):
    run = fixture_run(tmp_path)
    ask(run, outcome="timeout-killed")
    data = read(run)
    assert data["abandoned"] is True
    assert read(run)["abandoned"] is True, "the record was rewritten in place"


def test_the_candidate_comes_from_the_channel_manifest_when_there_is_one(tmp_path):
    run = fixture_run(tmp_path, channel_manifest={"candidate_sha256": CANDIDATE, "variant": "descriptor"},
                      deployment={"built_sha256": OTHER, "deployed_sha256": OTHER,
                                  "rollback_sha256": "9" * 64})
    ask(run)
    data = read(run)
    assert data["candidate_sha256"] == CANDIDATE
    assert "manifest" in data["candidate_rule"], data["candidate_rule"]


def test_a_run_with_no_manifest_still_names_what_the_volume_held(tmp_path):
    """An older run, or a control boot off an unlabelled channel, has a deployment record to lean on."""
    run = fixture_run(tmp_path, deployment={"built_sha256": OTHER, "deployed_sha256": OTHER,
                                            "rollback_sha256": "9" * 64})
    ask(run)
    data = read(run)
    assert data["candidate_sha256"] == OTHER
    assert "deployment record" in data["candidate_rule"], data["candidate_rule"]


def test_a_run_with_neither_record_claims_no_candidate_rather_than_inventing_one(tmp_path):
    run = fixture_run(tmp_path)
    ask(run)
    data = read(run)
    assert data["candidate_sha256"] is None
    assert "no channel manifest" in data["candidate_rule"], data["candidate_rule"]


def test_the_artifact_map_carries_the_digest_of_every_byte_the_run_kept(tmp_path):
    run = fixture_run(tmp_path)
    (run / "evidence" / "MANIFEST.tsv").write_text(
        "path\tsha256\tmtime_utc\n"
        "WINDOWS/capture.txt\t%s\t2026-11-16T04:00:00Z\n"
        "WINDOWS/absent.txt\t%s\t2026-11-16T04:00:00Z\n"
        % (hashlib.sha256((run / "evidence" / "WINDOWS/capture.txt").read_bytes()).hexdigest(),
           "0" * 64))
    ask(run)
    artifacts = read(run)["artifacts"]
    assert artifacts["evidence/WINDOWS/capture.txt"] == hashlib.sha256(
        (run / "evidence" / "WINDOWS/capture.txt").read_bytes()).hexdigest()
    assert "evidence/WINDOWS/absent.txt" not in artifacts, \
        "a ledger line for a file the run does not have must not become a claim about it"
    assert artifacts["kdc-live.log"] == hashlib.sha256((run / "kdc-live.log").read_bytes()).hexdigest()
    assert "captor-catches.log" in artifacts, "an empty catch log is still the answer to a question"


def test_the_two_artifacts_the_strict_grader_demands_are_both_present(tmp_path):
    """pp_verdict refuses to answer without these two names in the map."""
    run = fixture_run(tmp_path)
    ask(run)
    artifacts = read(run)["artifacts"]
    for name in ("kdc-live.log", "captor-catches.log"):
        assert name in artifacts, "%s is not in the artifact map" % name


def test_the_strict_grader_answers_on_a_contract_this_writer_produced(tmp_path):
    """The contract is only real if the grader that demanded it can actually read it.

    Before this, every variant boot came back INCONCLUSIVE on a missing field, which is the shape of
    a gate that never judged anything.
    """
    verdict_tool = ROOT / "pp_verdict.py"
    if not verdict_tool.is_file():
        pytest.skip("the PowerPlay grader is not present")
    # A candidate has to be on offer, because a run that names none genuinely cannot be graded: the
    # grader's whole point is that an assert belongs to a named build. The deployment record is the
    # older shape of that evidence, so the fixture carries one.
    # The line is shaped like the real ones, because the grader's patterns key on the PowerPlay tag
    # the driver prints; an assert line without it is a different thing and would be read as absence.
    assert_line = ('[21:00:00] PRINT "Error[PP_PowerPlay]: cislands_hwmgr.c[6696]  -- '
                   "Assertion '*vddc < 0xff01' has failed\\n")
    run = fixture_run(tmp_path, kd=assert_line, live=assert_line,
                      deployment={"built_sha256": CANDIDATE, "deployed_sha256": CANDIDATE,
                                  "rollback_sha256": OTHER})
    assert ask(run).returncode == 0
    r = subprocess.run([sys.executable, str(verdict_tool), "--run", str(run), "--allow-expected"],
                       capture_output=True, text=True, cwd=str(ROOT))
    verdict = json.loads((run / "pp-verdict.json").read_text())
    assert "lacks" not in " ".join(verdict["reasons"]), \
        "a missing field, not the boot, decided the verdict: %s" % verdict
    assert verdict["status"] == "ASSERT_FAIL", verdict
    assert r.returncode == 20, (r.returncode, r.stdout, r.stderr)


def test_the_writer_refuses_a_record_that_is_not_there_and_says_the_usage(tmp_path):
    r = subprocess.run([sys.executable, str(CONTRACT), str(tmp_path / "nope.json"), str(tmp_path),
                        NONCE, "measured-shutdown"], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "no run record" in r.stderr, r.stderr
    r = subprocess.run([sys.executable, str(CONTRACT)], capture_output=True, text=True, cwd=str(ROOT))
    assert r.returncode == 2 and "usage" in r.stderr, r.stderr


def test_the_loop_writes_the_contract_rather_than_leaving_it_owed():
    """A runner that collected evidence and never recorded what it means is half a loop."""
    src = (ROOT / "run-experiment.sh").read_text()
    assert "run_contract.py" in src
    assert "COLLECTOR_CATCHES" in src and "touch \"$COLLECTOR_CATCHES\"" in src, \
        "an uncaught boot has to record that nothing was caught"


@pytest.mark.skipif(not BASE_BLOB.is_file(), reason="the proprietary base blob is not imported")
def test_the_identity_gate_names_every_binding_that_broke(tmp_path):
    """A verdict a reader cannot act on is not a report.

    This is the text a later session reads when a variant's boot cannot be attributed to its build,
    so each line has to say which link failed and what would repair it.
    """
    # No candidate file in the directory, so the verifier re-derives it from the base and the op
    # manifest and gets the true digest; every other record is deliberately wrong, and each wrongness
    # has to be its own readable line. A file that merely disagreed byte for byte would be caught by
    # the re-derivation before the bindings were ever read, which is the right order of tests.
    run = fixture_run(tmp_path, channel_manifest={"candidate_sha256": CANDIDATE},
                      deployment={"built_sha256": OTHER, "deployed_sha256": OTHER,
                                  "rollback_sha256": "9" * 64})
    assert ask(run).returncode == 0
    r = subprocess.run([str(ROOT / "delta-verify.sh"), "--delta", "descriptor", "--base",
                        str(BASE_BLOB), "--run", str(run)], capture_output=True, text=True,
                       cwd=str(ROOT))
    assert r.returncode == 30, r.stdout + r.stderr
    assert "the identity bindings do not all hold" in r.stderr, r.stderr
    for fragment in ("the build is", "the volume holds", "the boot ran a channel whose candidate is"):
        assert fragment in r.stderr, "%s is missing from the failure text\n%s" % (fragment, r.stderr)
    for line in r.stderr.splitlines():
        if line.strip().startswith("- "):
            assert len(line) > 60, "a failure line too short to act on: %s" % line
    assert (run / "ati2mtag.sys").is_file(), "the verifier re-derives the candidate it needs"
    for line in r.stderr.splitlines():
        if line.strip().startswith("- "):
            assert len(line) > 60, "a failure line too short to act on: %s" % line


def test_a_bugcheck_in_the_collector_log_is_recorded_as_a_crash():
    """Two boots bugchecked inside the display driver and their run records said the shutdown was clean.

    The contract inferred a crash from how the run *ended*, and a guest that faults and reboots inside
    the hold ends the same way a quiet one does. The detector reads the kernel's own last words, so
    the runner has to call it and the signature has to land in the record beside the flag.
    """
    src = (ROOT / "run-experiment.sh").read_text()
    assert 'python3 "$RIG/crash-signature.py"' in src, (
        "the detector has to be called through the interpreter: the first live use called it directly "
        "and the file's missing execute bit silenced it, so a crashed boot still read as clean")
    assert 'crash_suspected=true' in src, "a bugcheck has to flip the flag"
    assert 'crash_signature=' in src, "and the signature is recorded so a reader can check the claim"

