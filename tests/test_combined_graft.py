"""The combined graft: a union whose every byte belongs to one of the three banked deltas.

The milestone's claim is narrow and checkable: the combined candidate's diff contains exactly the
banked descriptor, dispatch and SMC changes and no additional behavioural bytes. These tests build the
union into a scratch directory, grade it with the rig's own checker, and then break it in the two ways
that matter: a byte that belongs to no banked delta, and a note edited after the record bound it.

The proprietary base is needed for the real numbers, so the tests that read it are declared skips
rather than silent ones.
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "combined-graft.py"
CHECKER = ROOT / "graft-check.py"
BASE = ROOT / "work" / "stageE" / "ati2mtag-s5_accessor_blob.sys"
NO_BASE = "the proprietary base blob is not imported on this machine"
BANKED = ("descriptor-redirect", "dispatch-routing", "smc-fill")


def build(tmp_path, base=BASE):
    """Generate the three records into a scratch directory.

    A graft manifest names its donor by relative path, resolved against the manifest's own directory,
    which is how the committed record stays portable. A scratch manifest therefore needs a copy of the
    donor beside it: not a symlink and not a rewritten absolute path, either of which would let the
    test pass on a arrangement the real record could never have.
    """
    import shutil

    shutil.copy2(ROOT / "fixtures" / "graft" / "donor-smc-win7-14.4.bin", tmp_path)
    manifest = tmp_path / "manifest-combined-deploy.json"
    expect = tmp_path / "combined-deploy.expect.json"
    notes = tmp_path / "graft-manifest.md"
    r = subprocess.run([sys.executable, str(TOOL), "--base", str(base), "--manifest", str(manifest),
                        "--expect", str(expect), "--notes", str(notes)],
                       capture_output=True, text=True, cwd=str(ROOT))
    return r, manifest, expect, notes


def grade(manifest, expect, notes, base=BASE):
    return subprocess.run([sys.executable, str(CHECKER), "--base", str(base), "--manifest",
                           str(manifest), "--expect", str(expect), "--notes", str(notes),
                           "--dry-run", "--require-deployable"],
                          capture_output=True, text=True, cwd=str(ROOT))


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_the_union_is_the_sum_of_its_parts(tmp_path):
    r, manifest, expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no two banked deltas share a single behavioural position" in r.stdout, r.stdout
    record = json.loads(expect.read_text())
    assert record["sum_of_components"] == record["union_positions"] == record["changed_bytes"]
    assert record["components"], "the record names no components to compare against"
    assert len(record["components"]) == 3
    for name in BANKED:
        assert record["components"][name]["output_sha256"], name
    assert record["diff_mode"] == "within"
    assert len(record["changed_positions_sha256"]) == 64
    assert len(record["notes_sha256"]) == 64
    # The regions the union may touch are exactly the banked regions, and no fourth region appears.
    assert len(record["changed_ranges"]) == 5, json.dumps(record["changed_ranges"])


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_the_rigs_own_checker_grades_the_union_it_produced(tmp_path):
    """The proof has to hold under the command the milestone names, not under a private reading."""
    r, manifest, expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    graded = grade(manifest, expect, notes)
    assert graded.returncode == 0, graded.stdout + graded.stderr
    assert "PASS combined" in graded.stdout, graded.stdout
    assert "behavioral changes 47589" in graded.stdout, graded.stdout


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_a_position_digest_that_does_not_match_the_diff_is_refused(tmp_path):
    """The field exists to make `within` exact. A checker that ignored it would not."""
    r, manifest, expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    record = json.loads(expect.read_text())
    record["changed_positions_sha256"] = "0" * 64
    expect.write_text(json.dumps(record, indent=2))
    graded = grade(manifest, expect, notes)
    assert graded.returncode == 30, graded.stdout + graded.stderr
    assert "behavioral positions differ from the bound digest" in graded.stderr, graded.stderr


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_a_note_edited_after_the_record_bound_it_is_refused(tmp_path):
    r, manifest, expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    notes.write_text(notes.read_text() + "\nA later session added a sentence.\n")
    graded = grade(manifest, expect, notes)
    assert graded.returncode == 30, graded.stdout + graded.stderr
    assert "notes digest mismatch" in graded.stderr, graded.stderr


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_an_empty_note_is_refused_rather_than_treated_as_absent(tmp_path):
    r, manifest, expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    notes.write_text("   \n")
    graded = grade(manifest, expect, notes)
    assert graded.returncode == 30, graded.stderr
    assert "notes are empty" in graded.stderr, graded.stderr


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_a_base_that_is_not_the_banked_one_stops_the_build(tmp_path):
    other = tmp_path / "other.sys"
    other.write_bytes(BASE.read_bytes()[:4096])
    r, _manifest, _expect, _notes = build(tmp_path, base=other)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "base blob is not the one" in (r.stdout + r.stderr), r.stdout + r.stderr


@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_the_records_in_the_tree_match_a_fresh_regeneration(tmp_path):
    """What the lane runs is a record, so it has to be what the tool produces from the banked three."""
    committed_manifest = ROOT / "fixtures" / "graft" / "manifest-combined-deploy.json"
    committed_expect = ROOT / "fixtures" / "graft" / "combined-deploy.expect.json"
    if not (committed_manifest.is_file() and committed_expect.is_file()):
        pytest.skip("the combined graft records have not been generated on this machine")
    r, manifest, expect, _notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(manifest.read_text()) == json.loads(committed_manifest.read_text())
    rebuilt, committed = json.loads(expect.read_text()), json.loads(committed_expect.read_text())
    for key in ("output_sha256", "changed_bytes", "changed_positions_sha256", "changed_ranges",
                "diff_mode", "sum_of_components", "union_positions"):
        assert rebuilt[key] == committed[key], "%s drifted: %s vs %s" % (key, rebuilt[key], committed[key])


def test_the_tool_refuses_a_donor_that_is_not_the_one_the_expectation_binds():
    """A donor is a 60 KB of someone else's binary; its identity is the whole provenance claim."""
    src = TOOL.read_text()
    assert "is not the one the expectation binds" in src
    assert "not the one" in src and "banked against" in src, "a wrong base must be named, not shrugged at"
    assert "the union is not the sum of its parts" in src, "the extra-byte case has to be a refusal"
    assert "--require-deployable" in src or "deployable" in src


def test_ranges_merge_adjacent_positions_into_one_span():
    sys.path.insert(0, str(ROOT))
    import importlib.util

    spec = importlib.util.spec_from_file_location("combined_graft", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.ranges_of({5, 6, 7, 10}) == [(5, 8), (10, 11)]
    assert module.ranges_of(set()) == []
    assert module.ranges_of({0, 2}) == [(0, 1), (2, 3)]


def test_the_built_candidate_is_rejected_if_the_union_moves_anything_extra(tmp_path):
    """The refusal text is what a future session reads, so it has to count both directions."""
    src = TOOL.read_text()
    assert "beyond the banked deltas" in src and "banked byte(s) absent" in src

@pytest.mark.skipif(not BASE.is_file(), reason=NO_BASE)
def test_the_note_the_milestones_command_reads_is_tracked_and_matches_a_regeneration(tmp_path):
    """`work/` is gitignored, and this file must survive a fresh clone.

    The milestone's verify command passes `--notes work/graft-manifest.md`, and the expectation binds
    that file's digest, so an untracked note means a fresh clone cannot run the check at all. It is
    force-tracked for that reason, and this test is what keeps the tracked copy from quietly drifting
    away from the tool that writes it.
    """
    tracked = ROOT / "work" / "graft-manifest.md"
    listed = subprocess.run(["git", "ls-files", "--error-unmatch", str(tracked.relative_to(ROOT))],
                            capture_output=True, text=True, cwd=str(ROOT))
    assert listed.returncode == 0, "the note the verify command reads is not tracked: " + listed.stderr
    r, _manifest, _expect, notes = build(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert notes.read_bytes() == tracked.read_bytes(), \
        "regenerate with ./combined-graft.py; do not hand-edit the note"
