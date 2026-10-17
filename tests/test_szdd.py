"""The SZDD codec contract suite.

Discharges the obligations of specs/szdd.allium (see
specs/szdd.allium.obligations). The oracle: real captured pairs
(the Vista donor .sy_ + its expanded driver) and Microsoft's own
format semantics.
"""

import os
import sys

import hypothesis
from hypothesis import given, settings, strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import szdd  # noqa: E402

DONOR_SY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "amdave32.dl_")
DONOR_SYS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "fixtures", "amdave32.dll")


def _has_donor():
    return os.path.exists(DONOR_SY) and os.path.exists(DONOR_SYS)


# --- the reference-pair oracle (ExactExpandCompatibility) ---

def test_reference_pair_byte_exact():
    """The decoder reproduces Microsoft expand.exe's output byte for byte."""
    assert _has_donor(), "donor pair missing"
    comp = open(DONOR_SY, "rb").read()
    real = open(DONOR_SYS, "rb").read()
    assert szdd.szdd_expand(comp) == real


# --- rejection (RejectForeignFormat: success, failure, creation) ---

@given(st.binary(min_size=1, max_size=4096).filter(lambda b: b[:4] != b"SZDD"))
def test_rejects_foreign_stream(data):
    """Any stream without the SZDD signature is rejected loudly, with no
    partial output (the rule's success, failure and creation paths)."""
    try:
        szdd.szdd_expand(data)
        raised = False
    except ValueError:
        raised = True
    assert raised


# --- round-trip identity (the Roundtrip invariant) ---

@given(st.binary(max_size=65536))
@settings(deadline=None, max_examples=60)
def test_roundtrip_identity(payload):
    """compress -> expand reproduces the payload byte for byte."""
    assert szdd.szdd_expand(szdd.szdd_compress(payload)) == payload


# --- termination (the Termination invariant) ---

@given(st.binary(min_size=4, max_size=2048).map(
    lambda b: b"SZDD\x88\xf0\x27\x33\x41\x00\x00\x00\x00\x00" + b))
@settings(deadline=None, max_examples=60)
def test_termination_on_fuzzed_streams(stream):
    """Any stream carrying the signature decodes to termination - the
    decoder never hangs, whatever the token stream says."""
    szdd.szdd_expand(stream)  # may raise on a truncated code; never hangs


# --- the format constants (the config-default obligations) ---

def test_format_constants():
    """The implementation's constants equal the spec's declared defaults."""
    assert szdd.WINDOW_SIZE == 4096
    assert szdd.WINDOW_START == 4080
    assert szdd.MIN_REFERENCE_LENGTH == 3
    assert szdd.MAX_REFERENCE_LENGTH == 18
    assert szdd.GROUP_ITEMS == 8


# --- the header law (the payload starts at byte 14) ---

def test_header_is_fourteen_bytes():
    """A one-literal payload compresses to a 14-byte header + 2 bytes."""
    out = szdd.szdd_compress(b"A")
    assert len(out) == 16
    assert out[:8] == szdd.SZDD_MAGIC
    assert szdd.szdd_expand(out) == b"A"
