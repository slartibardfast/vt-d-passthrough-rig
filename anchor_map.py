#!/usr/bin/env python3
"""Map packed-driver offsets to unique content matches in a live image."""

import argparse
import json
from pathlib import Path


class AnchorMapError(ValueError):
    """The requested mapping is empty, out of range, or ambiguous."""


def _matches(haystack, needle, minimum=0):
    if not needle:
        raise AnchorMapError("cannot search for an empty window")
    positions = []
    start = 0
    while True:
        found = haystack.find(needle, start)
        if found < 0:
            return positions
        if found >= minimum:
            positions.append(found)
        start = found + 1


def map_offset(offset, live, packed):
    if not 0 <= offset < len(packed):
        raise AnchorMapError("packed offset outside the image")
    window = packed[offset:offset + 48]
    if len(window) < 48:
        raise AnchorMapError("fewer than 48 packed bytes remain")
    matches = _matches(live, window, minimum=0x1000)
    if len(matches) > 1:
        raise AnchorMapError("48-byte anchor is ambiguous")
    if len(matches) == 1:
        return matches[0], matches[0] - offset, 48
    matches = _matches(live, packed[offset:offset + 16])
    if len(matches) > 1:
        raise AnchorMapError("16-byte fallback anchor is ambiguous")
    if not matches:
        return None, None, None
    return matches[0], matches[0] - offset, 16


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packed", required=True, type=Path)
    parser.add_argument("--live", required=True, type=Path)
    parser.add_argument("offsets", nargs="+")
    args = parser.parse_args()
    packed = args.packed.read_bytes()
    live = args.live.read_bytes()
    results = []
    for text in args.offsets:
        offset = int(text, 0)
        position, delta, width = map_offset(offset, live, packed)
        results.append({
            "packed": offset,
            "live": position,
            "delta": delta,
            "anchor_bytes": width,
        })
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AnchorMapError, OSError, ValueError) as error:
        raise SystemExit(f"INCONCLUSIVE: {error}")
