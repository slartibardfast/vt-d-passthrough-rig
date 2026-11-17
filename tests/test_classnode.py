"""Rebinding a device's class node, and the refusals that keep a guess out of it.

The values on a Display class node decide which driver package Windows believes owns the card, so a
wrong one is not cosmetic: it re-seeds the runtime's settings from an INF that does not describe this
hardware. Every field is therefore read from the INF, with the line it came from printed, and the tool
refuses rather than filling a gap.

These tests use a synthetic INF with the same shape as the real one, so a derivation that breaks on a
line of the real package still shows up here. The real package is checked too, and names its skip.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "registration_graft.py"
REAL_INF = ROOT / "work" / "candidates" / "CA178340.inf"
NO_INF = "the real Catalyst INF is not extracted on this machine"

SYNTH = """\
[Version]
Signature="$Windows NT$"
Provider=%Mfg%
ClassGUID={4D36E968-E325-11CE-BFC1-08002BE10318}
Class=Display
DriverVer=02/03/2015, 9.000.300.9999

[ATI.Mfg.NTamd64]
%MyCard% = ati2mtag_Synthetic_Desktop, PCI\\VEN_1002&DEV_67B0
%Other% = ati2mtag_Other_Desktop, PCI\\VEN_1002&DEV_9999

[ati2mtag_Synthetic_Desktop]
HKR,,InfSection,,

[Strings]
Mfg = "A Test Vendor"
MyCard = "A Synthetic Card"
Other = "Some Other Card"
"""


def write_inf(tmp_path, text=SYNTH, name="synthetic.inf"):
    inf = tmp_path / name
    inf.write_text(text)
    return inf


def ask(hive, inf, *args):
    return subprocess.run([sys.executable, str(TOOL), "classnode", str(hive), "--inf", str(inf),
                           *[str(a) for a in args]], capture_output=True, text=True, cwd=str(ROOT))


def derive(tmp_path, text=SYNTH):
    """Call the derivation directly, so a wrong value is reported as a value rather than as a diff."""
    sys.path.insert(0, str(ROOT))
    import importlib

    module = importlib.import_module("registration_graft")
    importlib.reload(module)
    return module.inf_binding(write_inf(tmp_path, text))


def test_every_value_is_read_from_the_inf(tmp_path):
    got = derive(tmp_path)
    assert got["InfPath"] == "synthetic.inf", "the file's own name is what SetupAPI records"
    assert got["InfSection"] == "ati2mtag_Synthetic_Desktop", got
    assert got["DriverDesc"] == "A Synthetic Card", "the model entry's own string, resolved"
    assert got["ProviderName"] == "A Test Vendor"
    assert got["MatchingDeviceId"] == "pci\\ven_1002&dev_67b0"
    assert got["Service"] == "ati2mtag"
    assert got["Class"] == "Display"
    assert got["DriverDate"] == "02/03/2015" and got["DriverVersion"] == "9.000.300.9999"


def test_the_line_each_value_came_from_is_printed(tmp_path):
    got = derive(tmp_path)
    provenance = " ".join(str(v) for v in got["provenance"].values())
    assert "ATI.Mfg.NTamd64" in provenance and "line" in provenance, got["provenance"]
    assert "ClassGUID={4D36E968-E325-11CE-BFC1-08002BE10318}" in provenance, got["provenance"]


def test_a_model_entry_for_another_card_is_not_mistaken_for_this_one(tmp_path):
    """The file has two entries and one device id. Reading the first that parses binds the wrong one."""
    import importlib

    module = importlib.import_module("registration_graft")
    got = module.inf_binding(write_inf(tmp_path))
    assert got["InfSection"] == "ati2mtag_Synthetic_Desktop", "the entry for 67B0, not the one for 9999"
    assert got["DriverDesc"] == "A Synthetic Card"
    with pytest.raises(module.Bad) as raised:
        module.inf_binding(write_inf(tmp_path, SYNTH.replace("DEV_67B0", "DEV_8888")))
    assert "no model entry" in str(raised.value)


def test_an_inf_from_another_class_is_refused(tmp_path):
    import importlib

    module = importlib.import_module("registration_graft")
    text = SYNTH.replace("{4D36E968-E325-11CE-BFC1-08002BE10318}",
                         "{11111111-1111-1111-1111-111111111111}")
    with pytest.raises(module.Bad) as raised:
        module.inf_binding(write_inf(tmp_path, text))
    assert "not the Display class" in str(raised.value)


def test_an_unresolvable_string_is_refused_rather_than_left_as_a_percent(tmp_path):
    import importlib

    module = importlib.import_module("registration_graft")
    text = SYNTH.replace('MyCard = "A Synthetic Card"', "")
    with pytest.raises(module.Bad) as raised:
        module.inf_binding(write_inf(tmp_path, text))
    assert "leaves a string unresolved" in str(raised.value), raised.value
    assert "DriverDesc" in str(raised.value), "the refusal has to say which field"


def test_a_section_the_model_entry_names_but_the_file_lacks_is_refused(tmp_path):
    import importlib

    module = importlib.import_module("registration_graft")
    text = SYNTH.replace("[ati2mtag_Synthetic_Desktop]", "[some_other_section]")
    with pytest.raises(module.Bad) as raised:
        module.inf_binding(write_inf(tmp_path, text))
    assert "which the file does not contain" in str(raised.value)


def test_check_and_apply_are_refused_separately_and_independently_of_each_other(tmp_path):
    """A bare invocation must not write, and a bad argument must not look like a verdict."""
    r = subprocess.run([sys.executable, str(TOOL), "classnode"], capture_output=True, text=True,
                       cwd=str(ROOT))
    assert r.returncode == 2, r.stdout + r.stderr
    assert "--inf" in (r.stdout + r.stderr), r.stderr
    if subprocess.run([sys.executable, "-c", "import hivex"], capture_output=True).returncode != 0:
        pytest.skip("the hive reader needs the system Python's hivex to reach the missing-file check")
    r = ask(tmp_path / "absent-hive", write_inf(tmp_path))
    assert r.returncode == 2 and "no such hive" in r.stderr, r.stderr


@pytest.mark.skipif(not REAL_INF.is_file(), reason=NO_INF)
def test_the_real_package_yields_the_values_the_channel_states():
    """The package on the install channel, read by the same function the graft uses."""
    sys.path.insert(0, str(ROOT))
    import importlib

    module = importlib.import_module("registration_graft")
    got = module.inf_binding(REAL_INF)
    assert got["InfPath"] == "CA178340.inf"
    assert got["InfSection"] == "ati2mtag_Kaveri_Desktop"
    assert got["DriverDesc"] == "AMD Radeon R9 290X / 390X"
    assert got["ProviderName"] == "Advanced Micro Devices, Inc."
    assert got["DriverVersion"] == "9.000.300.3005"
    assert got["MatchingDeviceId"] == "pci\\ven_1002&dev_67b0"
    for key, value in got.items():
        if key == "provenance":
            continue   # provenance quotes the raw lines, and those carry the percent signs
        assert "%" not in str(value), "an unresolved %%string%% reached the output: %s=%s" % (key, value)


@pytest.mark.skipif(not REAL_INF.is_file(), reason=NO_INF)
def test_a_class_node_bound_from_the_real_inf_reads_back_the_same(tmp_path):
    """Write through the tool, read through the tool, on a copy of a hive the campaign booted."""
    needs = subprocess.run([sys.executable, "-c", "import hivex"], capture_output=True)
    if needs.returncode != 0:
        pytest.skip("writing a hive needs the system Python's hivex")
    source = ROOT / "work" / "runs" / "combined" / "evidence" / "WINDOWS" / "system32" / "config" / "system"
    if not source.is_file():
        pytest.skip("no captured SYSTEM hive on this machine")
    hive = tmp_path / "system"
    hive.write_bytes(source.read_bytes())

    before = ask(hive, REAL_INF, "--check")
    assert before.returncode == 1, before.stdout
    assert "NOT-BOUND" in before.stdout, before.stdout
    assert "registration_graft.py classnode" in before.stdout, "the remedy names the command to run"

    applied = ask(hive, REAL_INF, "--apply", "--record", tmp_path / "before.json")
    assert applied.returncode == 0, applied.stdout + applied.stderr
    record = json.loads((tmp_path / "before.json").read_text())
    assert record["previous_Driver"].endswith("\\0001"), record
    assert record["class_key"] == "0002", "a fresh subkey, so the binding the boot made survives"

    after = ask(hive, REAL_INF, "--check")
    assert after.returncode == 0, after.stdout
    assert "verdict: BOUND" in after.stdout, after.stdout


def test_a_class_node_is_written_complete_by_default():
    """The measured defect, pinned the other way round.

    The first `classnode` wrote install provenance and nothing else. Booted, that node mapped LESS
    than the vga node it replaced: `ati2dvag.dll` was opened and never mapped, and the guest reported
    no resolution at all (work/runs/desktop2). A Display class node carries two duties, the record of
    where the package came from and the runtime's answer to "which display driver, and is this the
    VGA one", so completing one and leaving the other empty is a downgrade that looks like progress
    because the value that changed is the one being audited. The complete node is now the default and
    the incomplete one is a flag.
    """
    src = TOOL.read_text()
    start = src.index("def classnode_writes")
    block = src[start:src.index("\ndef ", start + 10)]
    assert '"InstalledDisplayDrivers"' in block, "the display selection must be written"
    assert '"VgaCompatible"' in block, "and so must the vga-compatibility the runtime reads"
    assert "settings is None" in block, "the incomplete node has to be an explicit choice"
    assert "--provenance-only" in src, "and the name of that choice has to say what it omits"


def test_an_incomplete_chain_is_refused_rather_than_written_silently(tmp_path):
    """A caller who asks for the complete node and hands over an INF without it must be stopped."""
    import importlib

    module = importlib.import_module("registration_graft")
    src = {"control_set": "ControlSet001", "enum_path": "ControlSet001\\Enum\\PCI\\x",
           "class_key": "0001"}
    binding = {"InfPath": "x.inf", "InfSection": "s", "ProviderName": "P", "DriverDesc": "D",
               "MatchingDeviceId": "pci\\ven_1002&dev_67b0", "Service": "ati2mtag",
               "Class": "Display", "DriverDate": "01/01/2020", "DriverVersion": "1.0"}
    full = module.classnode_writes(src, binding, "0002",
                                   {"InstalledDisplayDrivers": (7, ["ati2dvag"], "inf:x.inf:1"),
                                    "VgaCompatible": (4, 0, "inf:x.inf:2")})
    names = [w[1] for w in full]
    assert "InstalledDisplayDrivers" in names and "VgaCompatible" in names
    assert len(names) == len(set(names)), "a value is written once onto one node"
    with pytest.raises(module.Bad) as raised:
        module.classnode_writes(src, binding, "0002", {"VgaCompatible": (4, 0, "inf:x.inf:2")})
    assert "InstalledDisplayDrivers" in str(raised.value), raised.value
    assert module.classnode_writes(src, binding, "0002", None), \
        "the provenance-only form still exists, but only when settings is passed as None on purpose"
