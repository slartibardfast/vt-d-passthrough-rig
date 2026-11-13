#!/usr/bin/env python3
"""Re-derive one graft and bind it to an exact, single-delta expectation."""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

import graft


class CheckError(Exception):
    """Evidence is missing or does not satisfy the recorded expectation."""


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise CheckError(f"cannot read JSON {path}: {error}")


def expect_int(value, name):
    try:
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise CheckError(f"invalid integer for {name}: {value!r}")


def changed_ranges(positions):
    if not positions:
        return []
    ranges = []
    start = previous = positions[0]
    for position in positions[1:]:
        if position != previous + 1:
            ranges.append([start, previous + 1])
            start = position
        previous = position
    ranges.append([start, previous + 1])
    return ranges


def checksum_offset(image):
    try:
        pe = struct.unpack_from("<I", image, 0x3C)[0]
        offset = pe + 88
        if offset + 4 > len(image):
            raise ValueError("checksum field outside image")
        return offset
    except (struct.error, ValueError) as error:
        raise CheckError(f"invalid PE image: {error}")


def load_donors(manifest_path, manifest, expectation):
    donors = {}
    records = expectation.get("donors", {})
    for operation in manifest.get("ops", []):
        name = operation.get("donor")
        if not name or name in donors:
            continue
        path = Path(name)
        if not path.is_absolute():
            path = manifest_path.parent / path
        try:
            data = path.read_bytes()
        except OSError as error:
            raise CheckError(f"cannot read donor {path}: {error}")
        record = records.get(name)
        if record is None:
            raise CheckError(f"expectation does not bind donor {name}")
        if len(data) != expect_int(record["size"], f"donor {name} size"):
            raise CheckError(f"donor size mismatch: {name}")
        if sha256(data) != record["sha256"]:
            raise CheckError(f"donor digest mismatch: {name}")
        donors[name] = data
    return donors


def verify(base_path, manifest_path, expectation_path, notes_path=None,
           require_deployable=False, candidate_path=None):
    expectation = read_json(expectation_path)
    try:
        base = base_path.read_bytes()
    except OSError as error:
        raise CheckError(f"cannot read base {base_path}: {error}")
    if len(base) != expect_int(expectation["base_size"], "base_size"):
        raise CheckError("base image size mismatch")
    if sha256(base) != expectation["base_sha256"]:
        raise CheckError("base image digest mismatch")

    manifest = read_json(manifest_path)
    donors = load_donors(manifest_path, manifest, expectation)
    try:
        output = graft.apply_manifest(base, manifest, donors)
    except (graft.GraftRangeError, graft.GraftEmptyManifestError, KeyError,
            TypeError, ValueError) as error:
        raise CheckError(f"graft rejected: {error}")

    if len(output) != len(base):
        raise CheckError("graft changed the image size")
    if sha256(output) != expectation["output_sha256"]:
        raise CheckError(
            f"output digest mismatch: expected {expectation['output_sha256']}, "
            f"got {sha256(output)}")

    operations = manifest["ops"]
    deployable = bool(expectation.get("deployable", False) or require_deployable)
    checksum_operations = [op for op in operations if op.get("op") == "pe-checksum"]
    if deployable:
        if len(checksum_operations) != 1 or operations[-1].get("op") != "pe-checksum":
            raise CheckError("a deployable manifest needs exactly one final pe-checksum")
        checksum_at = checksum_offset(output)
        stored = struct.unpack_from("<I", output, checksum_at)[0]
        if stored != graft.pe_checksum(output):
            raise CheckError("stored PE checksum does not match the image")
    elif checksum_operations:
        raise CheckError("expectation does not permit a PE checksum operation")

    positions = [i for i, (before, after) in enumerate(zip(base, output))
                 if before != after]
    behavioral = positions
    if deployable:
        checksum_at = checksum_offset(output)
        checksum_field = set(range(checksum_at, checksum_at + 4))
        behavioral = [position for position in positions if position not in checksum_field]
    actual_ranges = changed_ranges(behavioral)
    expected_ranges = [
        [expect_int(item[0], "range start"), expect_int(item[1], "range end")]
        for item in expectation["changed_ranges"]
    ]
    if expectation.get("diff_mode", "exact") == "within":
        if not behavioral or any(
                not any(start <= position < end for start, end in expected_ranges)
                for position in behavioral):
            raise CheckError("behavioral diff escapes the allowed mutation ranges")
    elif actual_ranges != expected_ranges:
        raise CheckError(
            f"behavioral diff mismatch: expected {expected_ranges}, got {actual_ranges}")
    if "changed_bytes" in expectation:
        if len(behavioral) != expect_int(expectation["changed_bytes"], "changed_bytes"):
            raise CheckError("behavioral changed-byte count mismatch")

    if notes_path is not None:
        try:
            notes = notes_path.read_bytes()
        except OSError as error:
            raise CheckError(f"cannot read notes {notes_path}: {error}")
        if not notes.strip():
            raise CheckError("notes are empty")
        recorded = expectation.get("notes_sha256")
        if recorded is not None and sha256(notes) != recorded:
            raise CheckError("notes digest mismatch")

    if candidate_path is not None:
        try:
            candidate = candidate_path.read_bytes()
        except OSError as error:
            raise CheckError(f"cannot read candidate {candidate_path}: {error}")
        if candidate != output:
            raise CheckError("candidate bytes differ from the re-derived image")
    return output, expectation


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--expect", required=True, dest="expectation", type=Path)
    parser.add_argument("--notes", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-deployable", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run and (args.output or args.candidate):
        parser.error("--dry-run excludes --output and --candidate")
    if args.output and args.candidate:
        parser.error("choose either --output or --candidate")
    if args.output and args.output.exists():
        parser.error("output already exists; remove it explicitly before rebuilding")

    output, expectation = verify(
        args.base, args.manifest, args.expectation, args.notes,
        args.require_deployable, args.candidate)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(args.output.name + ".tmp")
        if temporary.exists():
            raise CheckError(f"stale temporary output exists: {temporary}")
        temporary.write_bytes(output)
        temporary.replace(args.output)
        args.output.with_name(args.output.name + ".sha256").write_text(
            sha256(output) + "\n")
    print(
        f"PASS {expectation['delta_id']}: {len(output)} bytes, "
        f"sha256 {sha256(output)}, behavioral changes "
        f"{expectation.get('changed_bytes', 'range-bound')}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CheckError as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        sys.exit(30)
    except Exception as error:
        print(f"VERIFIER ERROR: {error}", file=sys.stderr)
        sys.exit(70)
