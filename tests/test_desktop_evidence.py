"""The desktop verdict record: what it may claim, what it must refuse, and what it must disclose.

Discharges nothing in Allium terms (the producer is not specified by a spec of its own); its job is to
be the instrument `acceptance.sh desktop` has been reading a missing file for. The tests are written
around one fear: that a tool which assembles a verdict is the place a verdict gets invented.

A synthetic run directory is used throughout, laid out exactly as `run-experiment.sh` leaves a real one,
because the shape of the inputs is what the tool gets wrong. The one test that reads a real boot names
its skip.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "desktop-evidence.py"
DESKTOP_RUN = ROOT / "work" / "runs" / "desktop"


def load():
    spec = importlib.util.spec_from_file_location("desktop_evidence", TOOL.as_posix())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_run(tmp_path, *, videoctl=None, modules=None, contract=True, with_frame=False):
    run = tmp_path / "desktop"
    (run / "evidence" / "WINDOWS").mkdir(parents=True)
    default_video = "\r\n".join([
        "AdapterRAM=-1048576",
        "ConfigManagerErrorCode=0",
        "CurrentBitsPerPixel=4",
        "CurrentHorizontalResolution=640",
        "CurrentVerticalResolution=480",
        "DriverVersion=",
        "InstalledDisplayDrivers=ati2dvag.dll",
        "Name=Standard VGA Graphics Adapter",
        "Status=OK",
        "VideoModeDescription=640 x 480 x 2 colors",
        "VideoProcessor=ATI display adapter (0x67B0)",
        "",
    ])
    (run / "evidence" / "WINDOWS" / "videoctl.txt").write_text(
        videoctl if videoctl is not None else default_video, encoding="utf-8")
    loads = modules if modules is not None else [
        r"MODULE LOAD pid=0x80800001 base=0xfffff97fff483000 name=\WINDOWS\System32\ati2dvag.dll",
        "MODULE LOAD pid=0x80800000 base=0xfffffadfaaf34000 name=ati2mtag.sys",
    ]
    (run / "kdc-detached.log").write_text("\n".join(
        ["[00:58:08] FILE_IO api=0x3430 \\SystemRoot\\System32\\ati2dvag.dll"] + loads) + "\n")
    (run / "run.json").write_text(json.dumps({
        "nonce": "evidence-fixture-1",
        "candidate_sha256": "f" * 64,
        "golden_sha256": "a" * 64,
        "channel_sha256": "b" * 64,
        "desktop_reached": contract,
        "outcome": "measured-shutdown",
    }))
    if with_frame:
        (run / "feed-060.ppm").write_bytes(b"P6 fake frame\n")
    return run


def run_tool(*args):
    return subprocess.run([sys.executable, str(TOOL), *[str(a) for a in args]],
                          capture_output=True, text=True, cwd=str(ROOT))


def build(run):
    return load().build(run)


def test_the_session_and_the_driver_are_read_from_the_guests_own_report(tmp_path):
    module = build(make_run(tmp_path))
    assert module["width"] == 640 and module["height"] == 480
    assert module["bits_per_pixel"] == 4
    assert module["adapter_name"] == "Standard VGA Graphics Adapter"
    assert "ati2mtag" in module["driver_identity"] and "ati2dvag" in module["driver_identity"]


def test_a_load_logged_with_a_bare_name_counts_as_loaded(tmp_path):
    """The collector writes `name=ati2mtag.sys` with no path, and a reader that insisted on a path
    reported the miniport as never loaded, which is the verdict the campaign would have shipped."""
    module = build(make_run(tmp_path))
    assert module["ati_rendered"] is True
    assert module["driver_loaded"] is True


def test_a_bare_name_is_read_by_basename_and_a_path_by_its_basename(tmp_path):
    module = build(make_run(tmp_path, modules=[
        r"MODULE LOAD pid=1 base=0x0 name=\WINDOWS\System32\ati2dvag.dll",
        r"MODULE LOAD pid=1 base=0x0 name=ati2mtag.sys",
    ]))
    assert module["ati_rendered"] and module["driver_loaded"]


def test_no_display_driver_load_is_reported_as_such_and_says_why(tmp_path):
    module = build(make_run(tmp_path, modules=["MODULE LOAD pid=1 base=0x0 name=vga.dll"]))
    assert module["ati_rendered"] is False and module["driver_loaded"] is False
    assert "ati2dvag.dll" in module["unevidenced"]["ati_rendered"]


def test_the_tool_never_claims_the_monitor_was_observed(tmp_path):
    """The one flag that makes the acceptance an acceptance belongs to a person at the machine."""
    module = build(make_run(tmp_path, with_frame=True))
    assert module["operator_physical_monitor"] is False
    assert "must not be able to" in module["unevidenced"]["operator_physical_monitor"]
    source = TOOL.read_text()
    assert '"operator_physical_monitor": True' not in source, \
        "no path through this tool may set the operator's flag"


def test_a_claim_with_no_instrument_is_named_with_its_reason(tmp_path):
    module = build(make_run(tmp_path))
    assert module["keyboard_response"] is False and module["pointer_response"] is False
    assert set(module["unevidenced"]) >= {"keyboard_response", "pointer_response",
                                          "operator_physical_monitor", "snapshot"}


def test_a_frame_in_the_run_is_bound_by_path_and_digest(tmp_path):
    module = build(make_run(tmp_path, with_frame=True))
    assert module["snapshot"]["path"] == "feed-060.ppm"
    assert len(module["snapshot"]["sha256"]) == 64
    assert "snapshot" not in module["unevidenced"]


def test_the_record_names_every_artifact_it_read(tmp_path):
    module = build(make_run(tmp_path))
    assert sorted(Path(name).name for name in module["derived_from"]) == \
        ["kdc-detached.log", "run.json", "videoctl.txt"]
    assert all(len(digest) == 64 for digest in module["derived_from"].values())


def test_building_twice_from_the_same_run_writes_the_same_bytes(tmp_path):
    run = make_run(tmp_path)
    first = json.dumps(build(run), indent=2, sort_keys=True)
    second = json.dumps(build(run), indent=2, sort_keys=True)
    assert first == second, "a verdict that drifts between reads is not a record"


def test_a_run_with_no_guest_report_is_refused_loudly(tmp_path):
    run = make_run(tmp_path)
    (run / "evidence" / "WINDOWS" / "videoctl.txt").unlink()
    result = run_tool("--run", run)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "videoctl.txt" in result.stderr and "Traceback" not in result.stderr


def test_a_written_record_stands_unless_the_operator_says_otherwise(tmp_path):
    run = make_run(tmp_path)
    first = run_tool("--run", run)
    assert first.returncode == 0, first.stdout + first.stderr
    again = run_tool("--run", run)
    assert again.returncode == 2 and "already exists" in again.stderr, again.stderr
    forced = run_tool("--run", run, "--force")
    assert forced.returncode == 0, forced.stdout + forced.stderr


def test_the_record_is_written_where_the_grader_reads_it(tmp_path):
    run = make_run(tmp_path)
    assert run_tool("--run", run).returncode == 0
    written = json.loads((run / "evidence.json").read_text())
    assert written["producer"] == "desktop-evidence.py"
    assert written["candidate_sha256"] == "f" * 64


@pytest.mark.skipif(not DESKTOP_RUN.is_dir(), reason="the desktop boot is not on this machine")
def test_the_boot_that_loaded_the_display_driver_now_fails_on_the_operators_clause(tmp_path):
    """The point of the instrument: the gate stops complaining about a missing file and starts
    naming the clause that is actually unmet."""
    run = tmp_path / "desktop"
    run.mkdir()
    for name in ("run.json", "kdc-detached.log"):
        (run / name).write_bytes((DESKTOP_RUN / name).read_bytes())
    (run / "evidence" / "WINDOWS").mkdir(parents=True)
    (run / "evidence" / "WINDOWS" / "videoctl.txt").write_bytes(
        (DESKTOP_RUN / "evidence" / "WINDOWS" / "videoctl.txt").read_bytes())
    module = build(run)
    assert module["desktop_reached"] is True
    assert module["ati_rendered"] is True, "the boot that installed the DLL mapped it"
    assert module["driver_loaded"] is True
    assert module["width"] < 1024, "no boot of this card has held a session at the bar's width"
    assert module["operator_physical_monitor"] is False
