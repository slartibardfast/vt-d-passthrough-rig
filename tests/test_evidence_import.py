"""Hash-verified import of operator-supplied real evidence."""

import hashlib
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_import_checks_hashes_and_copies(tmp_path):
    rig = tmp_path / "rig"
    source = tmp_path / "source"
    (rig / "fixtures" / "graft").mkdir(parents=True)
    (source / "work" / "stageE").mkdir(parents=True)
    shutil.copy2(ROOT / "evidence-import.sh", rig / "evidence-import.sh")
    data = b"real evidence"
    target = source / "work" / "stageE" / "driver.sys"
    target.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    (rig / "fixtures" / "graft" / "real-inputs.tsv").write_text(
        f"work/stageE/driver.sys\t{len(data)}\t{digest}\n")
    result = subprocess.run(
        ["bash", str(rig / "evidence-import.sh"), "--source", str(source)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (rig / "work" / "stageE" / "driver.sys").read_bytes() == data


def test_import_rejects_mismatch_without_replacing_target(tmp_path):
    rig = tmp_path / "rig"
    source = tmp_path / "source"
    (rig / "fixtures" / "graft").mkdir(parents=True)
    (source / "work").mkdir(parents=True)
    (rig / "work").mkdir()
    shutil.copy2(ROOT / "evidence-import.sh", rig / "evidence-import.sh")
    (source / "work" / "input.bin").write_bytes(b"actual")
    existing = rig / "work" / "input.bin"
    existing.write_bytes(b"existing")
    (rig / "fixtures" / "graft" / "real-inputs.tsv").write_text(
        f"work/input.bin\t6\t{'0' * 64}\n")
    result = subprocess.run(
        ["bash", str(rig / "evidence-import.sh"), "--source", str(source)],
        capture_output=True, text=True)
    assert result.returncode == 30
    assert existing.read_bytes() == b"existing"
