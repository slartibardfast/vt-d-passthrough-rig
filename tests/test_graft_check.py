"""Exact image and single-delta checks for graft-check.py."""

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

import graft


ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "graft"
SPEC = importlib.util.spec_from_file_location("graft_check", ROOT / "graft-check.py")
graft_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(graft_check)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def synthetic_case(tmp_path, deployable=False):
    base_bytes = (FIX / "base.bin").read_bytes()
    base = tmp_path / "base.bin"
    base.write_bytes(base_bytes)
    operations = [{"op": "patch", "at": 512, "bytes": "b801000000c3"}]
    if deployable:
        operations.append({"op": "pe-checksum"})
    manifest_data = {"ops": operations}
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_data))
    output = graft.apply_manifest(base_bytes, manifest_data, {})
    expectation = {
        "delta_id": "synthetic",
        "base_size": len(base_bytes),
        "base_sha256": sha(base_bytes),
        "output_sha256": sha(output),
        "deployable": deployable,
        "changed_ranges": [[512, 518]],
        "changed_bytes": 6,
    }
    expect = tmp_path / "expect.json"
    expect.write_text(json.dumps(expectation))
    return base, manifest, expect, output, expectation


def test_exact_synthetic_graft_passes(tmp_path):
    base, manifest, expect, _, _ = synthetic_case(tmp_path)
    output, _ = graft_check.verify(base, manifest, expect)
    assert output[512:518] == bytes.fromhex("b801000000c3")


def test_base_and_output_digests_are_enforced(tmp_path):
    base, manifest, expect, _, expectation = synthetic_case(tmp_path)
    base.write_bytes(b"corrupt" + base.read_bytes()[7:])
    with pytest.raises(Exception, match="base image digest mismatch"):
        graft_check.verify(base, manifest, expect)
    base.write_bytes((FIX / "base.bin").read_bytes())
    expectation["output_sha256"] = "0" * 64
    expect.write_text(json.dumps(expectation))
    with pytest.raises(Exception, match="output digest mismatch"):
        graft_check.verify(base, manifest, expect)


def test_unexpected_behavioral_diff_is_rejected(tmp_path):
    base, manifest, expect, _, expectation = synthetic_case(tmp_path)
    expectation["changed_ranges"] = [[0, 1]]
    expect.write_text(json.dumps(expectation))
    with pytest.raises(Exception, match="behavioral diff mismatch"):
        graft_check.verify(base, manifest, expect)


def test_deployable_requires_final_checksum(tmp_path):
    base, manifest, expect, _, expectation = synthetic_case(tmp_path, True)
    data = json.loads(manifest.read_text())
    data["ops"].insert(0, data["ops"].pop())
    manifest.write_text(json.dumps(data))
    expectation["output_sha256"] = sha(graft.apply_manifest(
        base.read_bytes(), data, {}))
    expect.write_text(json.dumps(expectation))
    with pytest.raises(Exception, match="final pe-checksum"):
        graft_check.verify(base, manifest, expect, require_deployable=True)


def test_output_is_written_only_after_a_pass(tmp_path):
    base, manifest, expect, expected, _ = synthetic_case(tmp_path)
    output = tmp_path / "out.bin"
    result = subprocess.run([
        sys.executable, str(ROOT / "graft-check.py"),
        "--base", str(base), "--manifest", str(manifest),
        "--expect", str(expect), "--output", str(output),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_bytes() == expected
    again = subprocess.run([
        sys.executable, str(ROOT / "graft-check.py"),
        "--base", str(base), "--manifest", str(manifest),
        "--expect", str(expect), "--output", str(output),
    ], capture_output=True, text=True)
    assert again.returncode == 2


REAL_BASE = ROOT / "work" / "stageE" / "ati2mtag-s5_accessor_blob.sys"


@pytest.mark.skipif(not REAL_BASE.exists(), reason="real proprietary base not imported")
def test_real_descriptor_and_smc_candidates_match_their_banked_digests():
    check = graft_check
    for manifest, expectation in (
            ("manifest-descriptor-redirect-deploy.json", "descriptor-redirect-deploy.expect.json"),
            ("manifest-smc-fill-deploy.json", "smc-fill-deploy.expect.json")):
        output, _ = check.verify(
            REAL_BASE, FIX / manifest, FIX / expectation, require_deployable=True)
        assert graft.digest_of(output) == json.loads(
            (FIX / expectation).read_text())["output_sha256"]
