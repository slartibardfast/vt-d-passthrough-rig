"""Unique, range-checked packed-to-live anchor mappings."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import anchor_map


ROOT = Path(__file__).resolve().parents[1]


def images():
    packed = b"".join(hashlib.sha256(bytes([index])).digest() for index in range(32))
    live = bytearray(0x2000)
    live[0x1100:0x1100 + len(packed)] = packed
    return bytes(live), packed


def test_unique_mapping_ignores_header_page():
    live, packed = images()
    live = packed + live
    position, delta, width = anchor_map.map_offset(64, live, packed)
    assert (position, delta, width) == (0x1100 + 64 + len(packed), 0x1100 + len(packed), 48)


def test_ambiguous_48_byte_anchor_is_rejected():
    live, packed = images()
    live = bytearray(live)
    live[0x1500:0x1500 + len(packed)] = packed
    with pytest.raises(anchor_map.AnchorMapError, match="ambiguous"):
        anchor_map.map_offset(64, bytes(live), packed)


def test_unique_16_byte_fallback():
    live, packed = images()
    live = bytearray(live)
    start = 0x1100 + 64
    live[start + 16:start + 48] = bytes(32)
    position, delta, width = anchor_map.map_offset(64, bytes(live), packed)
    assert (position, width) == (start, 16)
    assert delta == start - 64


def test_no_match_and_bad_ranges():
    live, packed = images()
    changed = bytearray(live)
    changed[0x1100:0x1100 + len(packed)] = bytes(len(packed))
    assert anchor_map.map_offset(64, bytes(changed), packed) == (None, None, None)
    with pytest.raises(anchor_map.AnchorMapError):
        anchor_map.map_offset(len(packed), live, packed)
    with pytest.raises(anchor_map.AnchorMapError):
        anchor_map.map_offset(len(packed) - 16, live, packed)


def test_cli_uses_explicit_paths_from_any_cwd(tmp_path):
    live, packed = images()
    (tmp_path / "live.bin").write_bytes(live)
    (tmp_path / "packed.bin").write_bytes(packed)
    result = subprocess.run([
        sys.executable, str(ROOT / "anchor_map.py"),
        "--packed", str(tmp_path / "packed.bin"),
        "--live", str(tmp_path / "live.bin"), "64",
    ], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)[0]["live"] == 0x1140
