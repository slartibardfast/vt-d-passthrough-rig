"""The record beside a golden: what it is, what it came from, and how it came to be."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_a_manifest_can_be_amended_without_touching_the_image(tmp_path):
    """Provenance belongs in the record, and the record is written by the tool.

    The campaign's first golden was converted from a disk that had been through two aborted boots and
    one clean shutdown. Saying so later must not mean editing a manifest by hand, and it must not
    change a byte of the golden, because every run's `golden_sha256` rests on that file staying put.
    """
    golden = tmp_path / "os.qcow2"
    golden.write_bytes(b"qcow2-shaped bytes, not a real image: the amend reads and hashes only")
    manifest = tmp_path / "os.json"
    manifest.write_text(json.dumps({"golden": str(golden), "sha256": "0" * 64, "bytes": 1,
                                    "source": str(tmp_path / "os.raw")}))
    source = tmp_path / "os.raw"
    source.write_bytes(b"a source disk is also only a fixture here")

    r = subprocess.run([sys.executable, str(ROOT / "golden_amend.py"), str(golden), str(manifest), str(source),
                        "converted after two aborted boots and one clean shutdown"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    data = json.loads(manifest.read_text())
    before = hashlib.sha256(golden.read_bytes()).hexdigest()
    assert data["sha256"] == before, "a stale hash in a record the tool just re-derived"
    assert data["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert data["provenance"].startswith("converted after")
    assert data["provenance_recorded"].endswith("Z")

    r = subprocess.run([sys.executable, str(ROOT / "golden_amend.py"), str(golden), str(manifest),
                        str(tmp_path / "absent.raw")], capture_output=True, text=True)
    assert r.returncode == 2 and "no such source image" in r.stderr, r.stderr


def test_the_build_records_the_source_digest_it_too(tmp_path):
    src = (ROOT / "golden-build.sh").read_text()
    assert '"source_sha256": sha256_stream(source)' in src, \
        "a manifest that names a source it cannot be checked against asserts rather than records"
    assert "--amend" in src and "golden_amend.py" in src
