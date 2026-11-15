"""Checks on the install channel's text and on the letter verifier.

Two things are held here. The staged batch files may name the system drive and
nothing else, because every other letter is decided by Windows at boot from
whatever is attached. And verify-letters.py must fail when the witness is wrong,
incomplete, or absent, since its whole job is to turn "the scripts assume a
letter" into a checked fact of a run.

The witnesses in this file are synthetic fixtures for the verifier's own logic:
they are not evidence about any machine.
"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from channel_lint import lines_that_are_not_batch, offending_letters  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = ROOT / "install-channel"


GOOD_WITNESS = """letters-start Fri 11/14/2026 18:31:02.10
== C: ==
 Volume in drive C is XP64
 Volume Serial Number is 2A05-0504
AMD
Documents and Settings
WINDOWS
== D: ==
 Volume in drive D has no label.
 Volume Serial Number is 3A5E-2281
B175198
drv
probe
RECYCLER
== E: ==
 Volume in drive E has no label.
 Volume Serial Number is 76A8-2C1C
EFI
csmwrap.ini
== F: ==
 Volume in drive F has no label.
 The device is not ready.
letters-done
"""



def witness_dir(tmp_path: Path, text: str, name: str = "letters.txt"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text(text.replace("\n", "\r\n"), newline="")
    return tmp_path


def run_verifier(directory):
    return subprocess.run(
        [sys.executable, str(ROOT / "verify-letters.py"), str(directory)],
        capture_output=True, text=True,
    )


@pytest.mark.parametrize("name", sorted(p.name for p in CHANNEL.glob("*.bat")))
def test_staged_batch_names_only_the_system_drive(name):
    offenders = offending_letters((CHANNEL / name).read_bytes().decode("utf-8"))
    assert not offenders, "%s names a volume Windows picks at boot: %s" % (name, offenders)


@pytest.mark.parametrize("name", sorted(p.name for p in CHANNEL.glob("*.bat")))
def test_every_batch_line_is_a_command_a_remark_or_a_label(name):
    bad = lines_that_are_not_batch((CHANNEL / name).read_bytes().decode("utf-8"))
    assert not bad, "%s leaks prose into the script body: %s" % (name, bad)


def test_the_sources_are_named_relative_to_their_own_volume():
    for name in ("drv.bat", "phantom.bat"):
        text = (CHANNEL / name).read_text()
        assert "%~dp0" in text, "%s must name its sources from where it lives" % name


def test_verifier_accepts_the_driver_volume_on_the_expected_letter(tmp_path):
    result = run_verifier(witness_dir(tmp_path, GOOD_WITNESS))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_verifier_names_the_letter_the_volume_actually_got(tmp_path):
    text = GOOD_WITNESS.replace("== D: ==", "== Z: ==", 1)
    result = run_verifier(witness_dir(tmp_path, text))
    assert result.returncode == 1
    assert "the driver volume is on Z" in result.stdout


def test_verifier_refuses_a_witness_that_stopped_early(tmp_path):
    text = GOOD_WITNESS.replace("letters-done\n", "")
    result = run_verifier(witness_dir(tmp_path, text))
    assert result.returncode == 1
    assert "incomplete" in result.stdout


def test_verifier_tells_an_absent_witness_from_a_hook_that_failed(tmp_path):
    hook_only = tmp_path / "hook-failed"
    hook_only.mkdir()
    (hook_only / "letters-hook.txt").write_text("letters-hook-failed no D:\\letters.bat \r\n")
    result = run_verifier(hook_only)
    assert result.returncode == 1
    assert "the hook ran and the survey never reached the volume" in result.stdout

    nothing = tmp_path / "silent"
    nothing.mkdir()
    result = run_verifier(nothing)
    assert result.returncode == 2
    assert "never ran" in result.stdout

def test_verifier_reads_a_witness_that_probes_instead_of_listing(tmp_path):
    """The current witness answers two existence questions per letter rather than
    enumerating a volume, because enumerating the KVM's empty removable stalled
    the survey. Both forms must give the same verdict."""
    text = """letters-start Sat 11/14/2026 19:24:52
== c: ==
 Volume in drive C is XP64
 Volume Serial Number is 2A05-0504
== d: ==
 Volume in drive D is AMDINSTALL
 Volume Serial Number is 2B3E-647B
driver-marker drv\\ati2mtag.sys found on d:
driver-marker B175198\\CA178340.inf found on d:
== e: ==
 Volume in drive E is CSMWRAP
 Volume Serial Number is B6FD-F321
letters-done
"""
    result = run_verifier(witness_dir(tmp_path, text))
    assert result.returncode == 0, result.stdout
    assert "PASS" in result.stdout



SYSTEM_PYTHON = "/usr/bin/python3"
BANKED_HIVE = ROOT / "work" / "evidence" / "system-hive-before-the-graft"


def system_python_has_hivex():
    probe = subprocess.run([SYSTEM_PYTHON, "-I", "-c", "import hivex"], capture_output=True)
    return probe.returncode == 0


def error_mode_values(hive):
    read = subprocess.run(
        [SYSTEM_PYTHON, "-I", "-c",
         "import hivex, struct, sys\n"
         "h = hivex.Hivex(sys.argv[1])\n"
         "for cs in ('ControlSet001', 'ControlSet002'):\n"
         "    n = h.node_get_child(h.node_get_child(h.node_get_child(h.root(), cs), 'Control'), 'Windows')\n"
         "    print(cs, struct.unpack('<I', h.value_value(h.node_get_value(n, 'ErrorMode'))[1])[0])\n",
         str(hive)],
        capture_output=True, text=True, check=True,
    )
    return dict(line.split() for line in read.stdout.splitlines())


def test_harderror_writes_the_documented_error_mode_and_reads_it_back(tmp_path):
    # The value Windows reads for the No Disk dialog is Control\Windows\ErrorMode.
    # The banked pre-graft hive carries ErrorMode=0 beside HardErrorMode=2, which is
    # the state that still raised the modal, so this starts from that real state.
    if not system_python_has_hivex():
        pytest.skip("the hive edit needs python3-hivex in the system Python, which this runner lacks")
    hive = tmp_path / "system"
    hive.write_bytes(BANKED_HIVE.read_bytes())
    assert error_mode_values(hive) == {"ControlSet001": "0", "ControlSet002": "0"}
    run = subprocess.run([SYSTEM_PYTHON, "-I", str(ROOT / "hive-pin.py"), "harderror", str(hive)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert "ControlSet001\\Control\\Windows ErrorMode = 2" in run.stdout
    assert "ControlSet002\\Control\\Windows ErrorMode = 2" in run.stdout
    assert error_mode_values(hive) == {"ControlSet001": "2", "ControlSet002": "2"}
