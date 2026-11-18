"""The crash signature the run contract was missing, tested on the logs that hold one.

A guest that double-faults inside a display driver and reboots before the hold expires used to read
as a clean shutdown. These tests hold the two halves of that: a fatal error in the log must be found
and named, and a log without one must stay silent so a quiet run is not labelled a crash.
"""
import importlib.util
from pathlib import Path

RIG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("crash_signature", (RIG / "crash-signature.py").as_posix())
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

FATAL = """[00:18:16] PRINT 'Watchdog: Timeout in ati2dvag.'
[00:18:36] PRINT '\\n*** Fatal System Error: 0x0000007f
                       (0x0000000000000008,0x0000000080050031,0x00000000000006F8,0x0000000000000200)\\n\\n'
"""

QUIET = """[00:07:51] MODULE LOAD pid=0x80800000 base=0xfffffadfaaf49000 name=ati2mtag.sys
[00:07:55] MODULE LOAD pid=0x80800001 base=0xfffff97fff483000 name=\\WINDOWS\\System32\\ati2dvag.dll
"""


def test_a_double_fault_is_named_with_the_trap_that_fired():
    code, name, extra = cs.signature(FATAL)
    assert code == "0x0000007F"
    assert name == "unexpected-kernel-mode-trap"
    assert extra.strip() == "double-fault", "parameter 8 is the double fault, and it is the point"


def test_a_hang_is_reported_even_without_a_bugcheck():
    assert cs.watchdog_driver(FATAL) == "ati2dvag."
    assert "watchdog-timeout-in-ati2dvag." in cs.describe(FATAL)


def test_a_log_with_no_crash_says_nothing():
    assert cs.signature(QUIET) is None
    assert cs.describe(QUIET) is None, "a quiet run must not be labelled a crash"


def test_an_unknown_code_is_still_a_crash():
    line = cs.describe("*** Fatal System Error: 0x000000DE\n")
    assert line and line.startswith("0x000000DE"), line
    assert "bugcheck" in line, "an unlisted code is named as a bugcheck by number, never dropped"
