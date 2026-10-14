"""The graft builder's test suite (specs/patch.allium's discharges).

The fixture in fixtures/graft is the committed oracle: a synthetic 1 KiB
base, a 256-byte donor, a two-op manifest, and the expected output with its
sha256. The campaign grafts (the SMC slot fill, the descriptor redirect,
the dispatch fix) all run through this one builder.
"""
import hashlib
import json
import os
import struct

import pytest
from hypothesis import given, strategies as st

import graft

FIX = os.path.join(os.path.dirname(__file__), "..", "fixtures", "graft")


@pytest.fixture
def base():
    return open(os.path.join(FIX, "base.bin"), "rb").read()


@pytest.fixture
def donor():
    return open(os.path.join(FIX, "donor.bin"), "rb").read()


@pytest.fixture
def manifest():
    return json.load(open(os.path.join(FIX, "manifest.json")))


def test_committed_oracle_byte_exact(base, donor, manifest):
    """The builder's output equals the committed expected-out.bin, and the
    recorded sha256 binds it (the plan/0006 #graft-builder verify)."""
    out = graft.apply_manifest(base, manifest, {"donor.bin": donor})
    expected = open(os.path.join(FIX, "expected-out.bin"), "rb").read()
    recorded = open(os.path.join(FIX, "expected-out.bin.sha256")).read().strip()
    assert out == expected
    assert graft.digest_of(out) == recorded


def test_donor_fill_byte_exact(base, donor, manifest):
    """The SMC slot law: the filled region equals the donor bytes exactly."""
    out = graft.apply_manifest(base, manifest, {"donor.bin": donor})
    fill = manifest["ops"][0]
    assert out[fill["at"]:fill["at"] + fill["size"]] == \
        donor[fill["donor_at"]:fill["donor_at"] + fill["size"]]


def test_size_preserved(base, donor, manifest):
    """A graft never resizes the image."""
    out = graft.apply_manifest(base, manifest, {"donor.bin": donor})
    assert len(out) == len(base)


@given(st.data())
def test_size_preserved_fuzzed(data):
    """Any manifest over any base preserves the size (hypothesis)."""
    n = data.draw(st.integers(min_value=64, max_value=4096))
    base = bytes(data.draw(st.binary(min_size=n, max_size=n)))
    at = data.draw(st.integers(min_value=0, max_value=n - 1))
    span = data.draw(st.integers(min_value=1, max_value=n - at))
    patch = bytes(data.draw(st.binary(min_size=span, max_size=span)))
    out = graft.apply_manifest(
        base, {"ops": [{"op": "patch", "at": at, "bytes": patch.hex()}]}, {})
    assert len(out) == len(base)
    assert out[at:at + span] == patch


def test_rejects_range_outside_base(base, donor):
    """An op past the base is a loud rejection with no output."""
    with pytest.raises(graft.GraftRangeError):
        graft.apply_manifest(
            base,
            {"ops": [{"op": "patch", "at": len(base) - 2,
                      "bytes": "aabbcc"}]},
            {"donor.bin": donor})


def test_rejects_donor_range_outside(base, donor):
    """A fill reading past its donor is a loud rejection."""
    with pytest.raises(graft.GraftRangeError):
        graft.apply_manifest(
            base,
            {"ops": [{"op": "fill", "at": 0, "size": 0x40,
                      "donor": "donor.bin", "donor_at": 0xF0}]},
            {"donor.bin": donor})


def test_rejects_empty_manifest(base, donor):
    """A manifest with no operations is rejected, not silently applied."""
    with pytest.raises(graft.GraftEmptyManifestError):
        graft.apply_manifest(base, {"ops": []}, {"donor.bin": donor})


def test_patch_applied(base, donor):
    """A patch writes its bytes at the recorded offset."""
    out = graft.apply_manifest(
        base,
        {"ops": [{"op": "patch", "at": 0x200, "bytes": "b801000000c3"}]},
        {"donor.bin": donor})
    assert out[0x200:0x206] == bytes.fromhex("b801000000c3")


def test_order_respected(base, donor):
    """Ops apply in manifest order: a later op sees earlier ops' bytes."""
    out = graft.apply_manifest(
        base,
        {"ops": [
            {"op": "fill", "at": 0x200, "size": 4,
             "donor": "donor.bin", "donor_at": 0x20},
            {"op": "patch", "at": 0x200, "bytes": "aabb"},
        ]},
        {"donor.bin": donor})
    # the patch (second) overwrote the fill's first two bytes
    assert out[0x200:0x204] == bytes.fromhex("aabb") + donor[0x22:0x24]


def test_digest_binds(base, donor, manifest):
    """The recorded digest is sha256 of exactly the emitted bytes."""
    out = graft.apply_manifest(base, manifest, {"donor.bin": donor})
    assert graft.digest_of(out) == hashlib.sha256(out).hexdigest()
    assert len(bytes.fromhex(graft.digest_of(out))) == 32  # config.digest_length


def test_cli_roundtrip(tmp_path):
    """The CLI end-to-end over the committed fixture: exit 0, the output
    byte-exact against the oracle, and the .sha256 sidecar binding it."""
    import subprocess
    out = str(tmp_path / "out.bin")
    r = subprocess.run(
        [os.path.join(os.path.dirname(__file__), "..", "venv", "bin", "python"),
         os.path.join(os.path.dirname(__file__), "..", "graft.py"),
         os.path.join(FIX, "base.bin"),
         os.path.join(FIX, "manifest.json"),
         out],
        capture_output=True, text=True,
        cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert r.returncode == 0, r.stderr
    got = open(out, "rb").read()
    assert got == open(os.path.join(FIX, "expected-out.bin"), "rb").read()
    assert open(out + ".sha256").read().strip() == \
        open(os.path.join(FIX, "expected-out.bin.sha256")).read().strip()


def test_rejects_unknown_op(base, donor):
    """An unknown op kind is a loud rejection: the manifest never completes."""
    with pytest.raises(graft.GraftRangeError):
        graft.apply_manifest(
            base,
            {"ops": [{"op": "bananas", "at": 0}]},
            {"donor.bin": donor})


def test_no_state_carryover(base, donor):
    """The builder is stateless per call: a rejected application leaves no
    residue, and a fresh valid call applies completely."""
    with pytest.raises(graft.GraftRangeError):
        graft.apply_manifest(
            base,
            {"ops": [{"op": "patch", "at": len(base), "bytes": "ff"}]},
            {"donor.bin": donor})
    out = graft.apply_manifest(
        base,
        {"ops": [{"op": "patch", "at": 0x200, "bytes": "90"}]},
        {"donor.bin": donor})
    assert len(out) == len(base) and out[0x200] == 0x90
