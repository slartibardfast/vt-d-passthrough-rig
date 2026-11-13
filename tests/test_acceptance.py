"""Hardware-backed acceptance schema tested with explicit synthetic evidence."""

import hashlib
import json
from pathlib import Path

import pytest

import acceptance
import pp_verdict


CANDIDATE = "b" * 64


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(run, name, data):
    path = run / name
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        path.write_text(data)
    else:
        path.write_bytes(data)
    return {"path": name, "sha256": sha(path)}


def instrument_fixture(root):
    run = root / "instrument"
    run.mkdir()
    kdc = artifact(run, "kdc-live.log", "PowerPlay initialization completed\n")
    captor = artifact(run, "captor-catches.log", "")
    metadata = {
        "candidate_sha256": CANDIDATE,
        "boot_id": "instrument-boot",
        "session_ready": True,
        "abandoned": False,
        "desktop_reached": True,
        "manual_resume": False,
        "expected_captor": [],
        "artifacts": {
            "kdc-live.log": kdc["sha256"],
            "captor-catches.log": captor["sha256"],
        },
    }
    (run / "run.json").write_text(json.dumps(metadata))
    return run


def user_fixture(root, name, reboot=False):
    run = root / name
    run.mkdir()
    evidence = {
        "candidate_sha256": CANDIDATE,
        "desktop_reached": True,
        "ati_rendered": True,
        "driver_loaded": True,
        "driver_identity": "ati2mtag.sys and ati2dvag.dll",
        "operator_physical_monitor": True,
        "width": 1280,
        "height": 1024,
        "config_manager_error_code": 0,
        "vram_expected": 4096,
        "vram_observed": 4096,
        "vram_source": "synthetic test declaration",
        "keyboard_response": True,
        "pointer_response": True,
        "resolution_change": {
            "before": [1024, 768],
            "after": [1280, 1024],
            "returned_to_known_good": True,
        },
        "acceleration": {"probe": "synthetic DirectDraw operation", "passed": True},
        "soak_seconds": 1800,
        "developer_rollback_tested": True,
        "snapshot": artifact(run, "desktop.png", b"PNG fixture"),
        "proofs": {},
    }
    if reboot:
        evidence["warm_reboot"] = True
        evidence["proofs"]["warm_reboot"] = artifact(
            run, "warm-reboot.txt", "warm reboot completed\n")
    else:
        for proof in ("input", "resolution", "acceleration", "soak", "rollback"):
            evidence["proofs"][proof] = artifact(
                run, f"{proof}.txt", f"{proof} passed\n")
    (run / "evidence.json").write_text(json.dumps(evidence))
    return run


def fixtures(tmp_path):
    instrument = instrument_fixture(tmp_path)
    user = user_fixture(tmp_path, "user")
    reboot = user_fixture(tmp_path, "reboot", reboot=True)
    markers = tmp_path / "markers.json"
    markers.write_text(json.dumps({
        "init_completed": ["PowerPlay initialization completed"],
        "assert_patterns": ["(?i)PowerPlay.*assert"],
        "failure_patterns": [],
    }))
    return instrument, user, reboot, markers


def test_desktop_requires_ati_identity_resolution_and_snapshot(tmp_path):
    _, user, _, _ = fixtures(tmp_path)
    evidence, candidate, _ = acceptance.validate_desktop(user)
    assert candidate == CANDIDATE
    evidence["ati_rendered"] = False
    (user / "evidence.json").write_text(json.dumps(evidence))
    with pytest.raises(acceptance.AcceptanceError, match="ati_rendered"):
        acceptance.validate_desktop(user)


def test_complete_synthetic_acceptance_collects_bound_evidence(tmp_path):
    instrument, user, reboot, markers = fixtures(tmp_path)
    output = tmp_path / "acceptance"
    assert acceptance.collect(instrument, user, reboot, output, markers) == 0
    summary = json.loads((output / "acceptance.json").read_text())
    assert summary["status"] == pp_verdict.CLEAN
    assert (output / "SHA256SUMS").is_file()
    assert (output / "user" / "desktop.png").is_file()
    assert (output / "reboot" / "warm-reboot.txt").is_file()


def test_acceptance_reclassifies_raw_pp_evidence(tmp_path):
    instrument, user, reboot, markers = fixtures(tmp_path)
    (instrument / "kdc-live.log").write_text(
        "PowerPlay assert unexpected\nPowerPlay initialization completed\n")
    metadata = json.loads((instrument / "run.json").read_text())
    metadata["artifacts"]["kdc-live.log"] = sha(instrument / "kdc-live.log")
    (instrument / "run.json").write_text(json.dumps(metadata))
    with pytest.raises(acceptance.AcceptanceError, match="ASSERT_FAIL"):
        acceptance.collect(instrument, user, reboot, markers=markers,
                           output=tmp_path / "out")


def test_acceptance_requires_user_probe_and_rollback(tmp_path):
    instrument, user, reboot, markers = fixtures(tmp_path)
    evidence = json.loads((user / "evidence.json").read_text())
    evidence["developer_rollback_tested"] = False
    (user / "evidence.json").write_text(json.dumps(evidence))
    with pytest.raises(acceptance.AcceptanceError, match="developer_rollback_tested"):
        acceptance.collect(instrument, user, reboot, tmp_path / "out", markers)


def test_acceptance_rejects_short_soak_and_candidate_mismatch(tmp_path):
    instrument, user, reboot, markers = fixtures(tmp_path)
    evidence = json.loads((user / "evidence.json").read_text())
    evidence["soak_seconds"] = 1799
    (user / "evidence.json").write_text(json.dumps(evidence))
    with pytest.raises(acceptance.AcceptanceError, match="soak"):
        acceptance.collect(instrument, user, reboot, tmp_path / "out", markers)
    evidence["soak_seconds"] = 1800
    evidence["candidate_sha256"] = "c" * 64
    (user / "evidence.json").write_text(json.dumps(evidence))
    with pytest.raises(acceptance.AcceptanceError, match="candidate differs"):
        acceptance.collect(instrument, user, reboot, tmp_path / "out", markers)


def test_acceptance_never_reuses_an_existing_output(tmp_path):
    instrument, user, reboot, markers = fixtures(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    with pytest.raises(acceptance.AcceptanceError, match="already exists"):
        acceptance.collect(instrument, user, reboot, output, markers)
