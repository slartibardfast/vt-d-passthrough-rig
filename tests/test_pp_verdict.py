"""PowerPlay evidence classification and fail-closed behavior."""

import hashlib
import json
from pathlib import Path

import pp_verdict


CANDIDATE = "a" * 64


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path: Path, kdc="PowerPlay initialization completed\n", captor=""):
    run = tmp_path / "run"
    run.mkdir()
    (run / "kdc-live.log").write_text(kdc)
    (run / "captor-catches.log").write_text(captor)
    metadata = {
        "candidate_sha256": CANDIDATE,
        "boot_id": "test-boot",
        "session_ready": True,
        "abandoned": False,
        "desktop_reached": True,
        "manual_resume": False,
        "expected_captor": [],
        "artifacts": {
            "kdc-live.log": sha(run / "kdc-live.log"),
            "captor-catches.log": sha(run / "captor-catches.log"),
        },
    }
    (run / "run.json").write_text(json.dumps(metadata))
    markers = tmp_path / "markers.json"
    markers.write_text(json.dumps({
        "init_completed": ["PowerPlay initialization completed"],
        "assert_patterns": ["(?i)(?:PowerPlay|\\bPP\\b).*assert|assert.*(?:PowerPlay|\\bPP\\b)"],
        "failure_patterns": ["(?i)Invalid ASIC family"],
    }))
    return run, markers


def status(run, markers):
    return pp_verdict.evaluate(run, markers)["status"]


def test_clean_requires_positive_marker(tmp_path):
    run, markers = fixture(tmp_path)
    assert status(run, markers) == pp_verdict.CLEAN
    markers.write_text(json.dumps({"init_completed": []}))
    assert status(run, markers) == pp_verdict.INCONCLUSIVE


def test_missing_or_stale_log_is_inconclusive(tmp_path):
    run, markers = fixture(tmp_path)
    (run / "kdc-live.log").write_text("stale")
    assert status(run, markers) == pp_verdict.INCONCLUSIVE


def test_unexpected_assert_fails_even_with_later_success(tmp_path):
    run, markers = fixture(
        tmp_path,
        "junk PowerPlay assert at site-A\nPowerPlay initialization completed\n")
    assert status(run, markers) == pp_verdict.ASSERT_FAIL


def test_known_failure_output_fails(tmp_path):
    run, markers = fixture(tmp_path, "Invalid ASIC family\n")
    assert status(run, markers) == pp_verdict.ASSERT_FAIL


def test_unknown_captor_output_is_inconclusive(tmp_path):
    run, markers = fixture(tmp_path, captor="unknown catch\n")
    assert status(run, markers) == pp_verdict.INCONCLUSIVE


def declare_expected(run, markers, include_dump=True):
    fingerprint = "ati2mtag+0x1234"
    kdc = f"PowerPlay assert {fingerprint}\nPowerPlay initialization completed\n"
    captor = f"caught {fingerprint}\n"
    (run / "kdc-live.log").write_text(kdc)
    (run / "captor-catches.log").write_text(captor)
    (run / "context.txt").write_text("context")
    if include_dump:
        (run / "catch.bin").write_bytes(b"dump")
    metadata = json.loads((run / "run.json").read_text())
    metadata["expected_assert"] = fingerprint
    metadata["manual_resume"] = True
    metadata["artifacts"] = {
        "kdc-live.log": sha(run / "kdc-live.log"),
        "captor-catches.log": sha(run / "captor-catches.log"),
        "context.txt": sha(run / "context.txt"),
    }
    if include_dump:
        metadata["artifacts"]["catch.bin"] = sha(run / "catch.bin")
    (run / "run.json").write_text(json.dumps(metadata))
    event = {
        "fingerprint": fingerprint,
        "first_chance": True,
        "second_chance": False,
        "resumed": True,
        "context": "context.txt",
        "dump": "catch.bin",
    }
    (run / "pp-events.json").write_text(json.dumps([event]))


def test_predeclared_experimental_catch_is_not_clean(tmp_path):
    run, markers = fixture(tmp_path)
    declare_expected(run, markers)
    assert status(run, markers) == pp_verdict.EXPECTED


def test_experimental_catch_without_dump_is_inconclusive(tmp_path):
    run, markers = fixture(tmp_path)
    declare_expected(run, markers, include_dump=False)
    assert status(run, markers) == pp_verdict.INCONCLUSIVE


def test_expected_catch_that_does_not_occur_is_inconclusive(tmp_path):
    run, markers = fixture(tmp_path)
    metadata = json.loads((run / "run.json").read_text())
    metadata["expected_assert"] = "ati2mtag+0x1234"
    (run / "run.json").write_text(json.dumps(metadata))
    assert status(run, markers) == pp_verdict.INCONCLUSIVE


def test_wrapper_returns_zero_for_expected_only_when_allowed(tmp_path):
    run, markers = fixture(tmp_path)
    declare_expected(run, markers)
    argv = ["--run", str(run), "--markers", str(markers)]
    assert pp_verdict.main(argv) == 10
    assert pp_verdict.main(argv + ["--allow-expected"]) == 0
    assert json.loads((run / "pp-verdict.json").read_text())["status"] == pp_verdict.EXPECTED
