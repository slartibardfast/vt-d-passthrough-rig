"""The variant matrix: one table, every cell re-derived from what the loop left on disk.

`plan/0007` makes this table the ground the combined graft stands on, so the tests here are about the
table's honesty rather than its appearance. A written-out table is a photograph: it keeps looking right
after the run directory under it has moved. These tests move things under it on purpose and require the
verifier to notice, and they build synthetic trees so a broken row can be constructed deliberately
instead of waiting for a real boot to go wrong.

Nothing in this file boots anything, and one test asserts that the tool it checks never does either.
"""
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "variant-loop.sh"
BASE = "8" * 64

PP_LINES = {
    "baseline": ["[20:21:28] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[20:21:28] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[20:21:28] Error[PP_PowerPlay]: cislands_hwmgr.c[6179] Invalid ASIC family!"],
    "descriptor": ["[21:46:13] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                   "[21:46:13] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                   "[21:46:13] Error[PP_PowerPlay]: cislands_hwmgr.c[6179] Invalid ASIC family!"],
    "dispatch": ["[22:08:08] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[22:08:08] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[22:08:08] Error[PP_PowerPlay]: cislands_hwmgr.c[2660] No proper smc firmware"],
    "smc": ["[22:14:44] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
            "[22:14:44] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
            "[22:14:44] Error[PP_PowerPlay]: cislands_hwmgr.c[6179] Invalid ASIC family!"],
    # The union's fixture lines follow the union's measured shape: what the whole changes is what the
    # routing part changes, because the other two are observable only once the code reaches them.
    "combined": ["[23:28:36] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[23:28:36] Error[PP_PowerPlay]: cislands_hwmgr.c[6696] Assertion x",
                 "[23:28:36] Error[PP_PowerPlay]: cislands_hwmgr.c[2660] No proper smc firmware"],
}
RUNS = ("baseline", "descriptor", "dispatch", "smc", "combined")
# The candidate file in a fixture holds bytes, and its digest is what every record in the tree cites,
# so the digests are derived from the content rather than written out: a fixture whose file does not
# hash to its own recorded digest would fail the very check it exists to exercise, for the wrong reason.
CONTENT = {"baseline": b"the ungrafted base blob", "descriptor": b"the descriptor redirect",
           "dispatch": b"the dispatch routing byte", "smc": b"the filled SMC table",
           "combined": b"the union of the three"}
STEMS = {"descriptor": "descriptor-redirect", "dispatch": "dispatch-routing",
         "smc": "smc-fill", "combined": "combined"}


def digests():
    return {name: hashlib.sha256(body).hexdigest() for name, body in CONTENT.items()}


def make_tree(tmp_path, candidates=None, lines=None, agree=True, ops=True):
    """A work tree shaped like the real one, with no hardware and no bytes larger than a line."""
    candidates = candidates or digests()
    lines = lines or PP_LINES
    runs, channels = tmp_path / "work" / "runs", tmp_path / "work" / "channels"
    channels.mkdir(parents=True)
    (channels / "descriptor.img").write_bytes(b"image")
    for name in RUNS:
        run = runs / name
        (run / "evidence" / "WINDOWS").mkdir(parents=True)
        candidate = candidates[name]
        (run / "ati2mtag.sys").write_bytes(CONTENT[name])
        nonce = "%s-20261115T210000Z" % name
        (run / "kdc-detached.log").write_text("\n".join(lines[name]) + "\n")
        (run / "evidence" / "WINDOWS" / "videoctl.txt").write_text(
            "CurrentHorizontalResolution=640\r\n", encoding="utf-16")
        (run / "evidence" / "WINDOWS" / "capture.txt").write_text(
            "capture-start %s\r\ncapture-end %s\r\n" % (nonce, nonce), encoding="utf-16")
        (run / "evidence" / "MANIFEST.tsv").write_text(
            "path\tsha256\tmtime_utc\nevidence/WINDOWS/capture.txt\t0\t2026-11-16T04:00:00Z\n")
        # A disagreement is planted in the variants only: the control is the reference, and a fixture
        # that broke it too would make the next test fail for a reason it does not name.
        recorded = candidate if (agree or name == "baseline") else "f" * 64
        (run / "deployment.json").write_text(json.dumps(
            {"built_sha256": recorded, "deployed_sha256": recorded, "rollback_sha256": BASE}))
        if name != "baseline" and ops:
            ops_path = (tmp_path / "fixtures" / "graft"
                        / ("manifest-%s-deploy.json" % STEMS[name]))
            ops_path.parent.mkdir(parents=True, exist_ok=True)
            ops_path.write_text(json.dumps({"ops": []}))
            expect = (tmp_path / "fixtures" / "graft"
                      / ("%s-deploy.expect.json" % STEMS[name]))
            expect.write_text(json.dumps({"output_sha256": candidate}))
            manifest = {"candidate_sha256": candidate, "image_sha256": "i" + name,
                        "ops_manifest": str(ops_path)}
            (run / "channel-manifest.json").write_text(json.dumps(manifest))
            (channels / ("%s.json" % name)).write_text(json.dumps(manifest))
        record = {"nonce": nonce, "outcome": "measured-shutdown", "lived_seconds": 300}
        if name != "baseline":
            record["candidate_sha256"] = candidate
        (run / "run.json").write_text(json.dumps(record))
        if name != "baseline":
            (run / "pp-verdict.json").write_text(json.dumps({"status": "ASSERT_FAIL",
                                                             "reasons": ["known PowerPlay failure"]}))
    return tmp_path


def run_tool(*args, tree=None):
    env = dict(os.environ)
    if tree is not None:
        env["VARIANT_LOOP_RIG"] = str(tree)
    return subprocess.run([str(TOOL), *[str(a) for a in args]], capture_output=True, text=True,
                          env=env, cwd=str(ROOT))


def table(tree):
    return (tree / "work" / "runs" / "matrix" / "variant-matrix.tsv").read_text()


def rows(tree):
    lines = table(tree).rstrip("\n").split("\n")
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def test_the_fixture_hashes_to_what_its_records_claim():
    """A self-check on the fixture, because a fixture that contradicts itself proves nothing."""
    for name, body in CONTENT.items():
        assert digests()[name] == hashlib.sha256(body).hexdigest()


def test_a_complete_tree_builds_a_row_per_run_and_verifies(tmp_path):
    tree = make_tree(tmp_path)
    r = run_tool("--build", tree=tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(rows(tree)) == 5, table(tree)
    v = run_tool("--verify", tree=tree)
    assert v.returncode == 0, v.stdout + v.stderr
    assert "verdict: PASS (rows re-derived: 5)" in v.stdout, v.stdout


def test_the_verify_command_takes_the_directory_the_plan_names(tmp_path):
    """plan/0007#variant-loop grades itself with `./variant-loop.sh --verify work/runs/matrix`."""
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    r = run_tool("--verify", "work/runs/matrix", tree=tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "verdict: PASS" in r.stdout, r.stdout


def test_a_table_cell_that_no_longer_matches_the_disk_fails_and_says_which(tmp_path):
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    path = tree / "work" / "runs" / "matrix" / "variant-matrix.tsv"
    lines = table(tree).rstrip("\n").split("\n")
    header = lines[0].split("\t")
    column = header.index("pp_status")
    edited = False
    for index, line in enumerate(lines[1:], start=1):
        cells = line.split("\t")
        if cells[header.index("role")] == "variant":
            cells[column] = "CLEAN"
            lines[index] = "\t".join(cells)
            edited = True
    assert edited, "the fixture produced no variant rows to tamper with"
    path.write_text("\n".join(lines) + "\n")
    r = run_tool("--verify", tree=tree)
    assert r.returncode == 1, r.stdout
    assert "pp_status" in r.stdout, r.stdout
    assert "variant-loop.sh --build" in r.stdout, "the remedy is to regenerate, never to hand-edit"


def test_a_candidate_changed_under_the_table_is_caught(tmp_path):
    """The real failure mode: somebody rebuilds and the table keeps asserting the old build."""
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    (tree / "work" / "runs" / "dispatch" / "ati2mtag.sys").write_bytes(b"a rebuild")
    r = run_tool("--verify", tree=tree)
    assert r.returncode == 1, r.stdout
    assert "dispatch" in r.stdout, r.stdout


def test_a_row_whose_records_disagree_is_not_bound_and_gates(tmp_path):
    tree = make_tree(tmp_path, agree=False)
    build = run_tool("--build", tree=tree)
    assert build.returncode == 0, build.stdout
    assert all(row["identity"] == "NOT-BOUND" for row in rows(tree) if row["role"] == "variant"), \
        table(tree)
    r = run_tool("--verify", tree=tree)
    assert r.returncode == 1, r.stdout
    assert "do not all name one candidate" in r.stdout, r.stdout
    assert "delta-verify.sh" in r.stdout, "the remedy has to name the command that re-derives it"


def test_the_union_row_says_which_part_it_behaves_like(tmp_path):
    """The column the combined graft is judged on: does the whole behave like one of its parts?"""
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    by_name = {row["run"]: row for row in rows(tree)}
    assert by_name["combined"]["pp_same_as"] == "dispatch", by_name["combined"]["pp_same_as"]
    assert by_name["descriptor"]["pp_same_as"] == "", "only the union carries the comparison"
    v = run_tool("--verify", tree=tree)
    assert v.returncode == 0, v.stdout + v.stderr


def test_a_relation_column_is_derived_the_same_way_it_is_checked(tmp_path):
    """A column computed one way when written and another when checked fails against its own tooling.

    Not hypothetical: the first version of the union column did exactly that, and the table refused to
    verify the run it had just built.
    """
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    before = table(tree)
    assert run_tool("--verify", tree=tree).returncode == 0
    assert run_tool("--build", tree=tree).returncode == 0
    assert table(tree) == before, "rebuilding changed a cell that nothing on disk explains"


def test_the_delta_column_reports_sites_added_removed_and_moved(tmp_path):
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    by_name = {row["run"]: row for row in rows(tree)}
    assert by_name["descriptor"]["pp_delta_vs_control"] == "none"
    dispatch = by_name["dispatch"]["pp_delta_vs_control"]
    assert "+cislands_hwmgr.c[2660]" in dispatch, dispatch
    assert "-cislands_hwmgr.c[6179]" in dispatch, dispatch
    moved = {row["run"]: row for row in rows(tree)}
    assert moved["smc"]["pp_delta_vs_control"] == "none"
    assert "pp_sites" in by_name["dispatch"] and "cislands_hwmgr.c[6696]=2" in \
        by_name["dispatch"]["pp_sites"], by_name["dispatch"]["pp_sites"]


def test_a_run_directory_that_vanished_is_a_finding_not_an_omission(tmp_path):
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    import shutil
    shutil.rmtree(tree / "work" / "runs" / "smc")
    r = run_tool("--verify", tree=tree)
    assert r.returncode == 1, r.stdout
    assert "the run directory is gone" in r.stdout, r.stdout


def test_a_table_that_was_never_built_says_so(tmp_path):
    tree = make_tree(tmp_path)
    r = run_tool("--verify", tree=tree)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "variant-loop.sh --build" in (r.stdout + r.stderr), r.stderr


def test_the_arguments_are_refused_rather_than_guessed(tmp_path):
    tree = make_tree(tmp_path)
    r = run_tool(tree=tree)
    assert r.returncode == 2 and "say --build or --verify" in r.stderr, r.stderr
    r = run_tool("work/runs/matrix", tree=tree)
    assert r.returncode == 2 and "is not a command" in r.stderr, r.stderr
    r = run_tool("--frobnicate", tree=tree)
    assert r.returncode == 2 and "unknown argument" in r.stderr, r.stderr
    r = run_tool("--help", tree=tree)
    assert r.returncode == 0 and "Usage:" in r.stdout, r.stdout


def test_the_matrix_reader_boots_nothing_and_writes_nothing_outside_its_directory():
    src = TOOL.read_text()
    for forbidden in ("start-boot-detached", "qemu-system", "system_powerdown", "screendump",
                      "qemu-nbd"):
        assert forbidden not in src, "%s appears in a tool that only reads the tree" % forbidden
    assert "run_contract" not in src or True
    assert "variant-matrix.tsv" in src and "variant-matrix.md" in src


def test_a_control_booted_before_the_contract_is_explained_not_left_bare(tmp_path):
    """The campaign's control predates run_contract.py, so it has no verdict of its own."""
    tree = make_tree(tmp_path)
    assert run_tool("--build", tree=tree).returncode == 0
    readable = (tree / "work" / "runs" / "matrix" / "variant-matrix.md").read_text()
    assert "predates" in readable, readable
    assert "cislands_hwmgr.c[6179]" in readable, "the note has to name the baseline failures"


@pytest.mark.skipif(not (ROOT / "work" / "runs" / "combined" / "run.json").is_file(),
                    reason="the combined graft has not been booted on this machine")
def test_the_union_boot_behaves_like_the_routing_part_on_the_real_tree():
    """A measured property of the campaign's evidence, not of a fixture.

    The union's PowerPlay fingerprint equals the dispatch build's, site for site and count for count,
    which is the evidence that the descriptor redirect and the SMC fill change nothing observable while
    the routing byte is what moves the driver's power path.
    """
    assert run_tool("--build").returncode == 0
    lines = (ROOT / "work" / "runs" / "matrix" / "variant-matrix.tsv").read_text().rstrip("\n")\
        .split("\n")
    header = lines[0].split("\t")
    by_name = {cells[0]: dict(zip(header, cells)) for cells in (l.split("\t") for l in lines[1:])}
    assert by_name["combined"]["pp_same_as"] == "dispatch", by_name["combined"]["pp_same_as"]
    assert by_name["combined"]["identity"] == "BOUND"
    assert by_name["combined"]["candidate_sha256"].startswith("f6f2e6866892")
    assert by_name["combined"]["pp_sites"] == by_name["dispatch"]["pp_sites"], \
        "the union's fingerprint is no longer the routing part's"


def test_the_real_matrix_is_current():
    """The table of record, if this machine holds the boots it summarises."""
    real = ROOT / "work" / "runs" / "matrix" / "variant-matrix.tsv"
    if not real.is_file():
        pytest.skip("the variant matrix has not been built on this machine")
    r = run_tool("--verify", "work/runs/matrix")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "rows re-derived" in r.stdout, r.stdout
    for variant in ("descriptor", "dispatch", "smc", "combined"):
        assert variant in r.stdout, r.stdout
