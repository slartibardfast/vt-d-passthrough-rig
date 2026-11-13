#!/usr/bin/env python3
"""Validate desktop evidence and collect clean research acceptance artifacts."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

import pp_verdict


class AcceptanceError(Exception):
    """Required user or run evidence is absent or contradictory."""


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise AcceptanceError(f"cannot read {path}: {error}")


def require(condition, message):
    if not condition:
        raise AcceptanceError(message)


def require_bool(record, key):
    require(record.get(key) is True, f"{key} is not true")


def require_candidate(record):
    candidate = record.get("candidate_sha256")
    require(isinstance(candidate, str) and len(candidate) == 64,
            "candidate_sha256 is missing or malformed")
    require(all(c in "0123456789abcdef" for c in candidate),
            "candidate_sha256 is not lowercase hexadecimal")
    return candidate


def validate_artifact(run, record):
    require(isinstance(record, dict), "artifact record must be an object")
    try:
        return pp_verdict.safe_artifact(run, record["path"], record["sha256"])
    except (KeyError, OSError, ValueError) as error:
        raise AcceptanceError(str(error))


def validate_desktop(run):
    evidence = load_json(run / "evidence.json")
    candidate = require_candidate(evidence)
    require_bool(evidence, "desktop_reached")
    require_bool(evidence, "ati_rendered")
    require_bool(evidence, "driver_loaded")
    require_bool(evidence, "operator_physical_monitor")
    identity = evidence.get("driver_identity", "")
    require("ati2mtag" in identity.lower() and "ati2dvag" in identity.lower(),
            "driver_identity must name the ATI miniport and display driver")
    require(isinstance(evidence.get("width"), int) and evidence["width"] >= 1024,
            "desktop width is below 1024")
    require(isinstance(evidence.get("height"), int) and evidence["height"] >= 768,
            "desktop height is below 768")
    snapshot = validate_artifact(run, evidence.get("snapshot"))
    return evidence, candidate, snapshot


def validate_code_and_vram(evidence):
    require(evidence.get("config_manager_error_code") == 0,
            "ConfigManagerErrorCode is not zero")
    expected = evidence.get("vram_expected")
    observed = evidence.get("vram_observed")
    require(isinstance(expected, int) and expected > 0,
            "vram_expected is not a positive integer")
    require(observed == expected, "observed VRAM differs from the expected value")
    require(isinstance(evidence.get("vram_source"), str) and evidence["vram_source"],
            "vram_source is missing")


def validate_user(run):
    evidence, candidate, snapshot = validate_desktop(run)
    validate_code_and_vram(evidence)
    require_bool(evidence, "keyboard_response")
    require_bool(evidence, "pointer_response")
    resolution = evidence.get("resolution_change")
    require(isinstance(resolution, dict), "resolution_change is missing")
    before = resolution.get("before")
    after = resolution.get("after")
    require(isinstance(before, list) and len(before) == 2 and
            all(isinstance(value, int) and value > 0 for value in before),
            "resolution before is invalid")
    require(isinstance(after, list) and len(after) == 2 and
            all(isinstance(value, int) and value > 0 for value in after),
            "resolution after is invalid")
    require(before != after, "resolution did not change")
    require(resolution.get("returned_to_known_good") is True,
            "resolution did not return to a known-good mode")
    acceleration = evidence.get("acceleration")
    require(isinstance(acceleration, dict) and acceleration.get("passed") is True and
            isinstance(acceleration.get("probe"), str) and acceleration["probe"],
            "a named acceleration probe has not passed")
    require(isinstance(evidence.get("soak_seconds"), int) and
            evidence["soak_seconds"] >= 1800, "soak is shorter than 30 minutes")
    require_bool(evidence, "developer_rollback_tested")

    proofs = evidence.get("proofs")
    require(isinstance(proofs, dict), "proofs must be an object")
    proof_paths = {}
    for name in ("input", "resolution", "acceleration", "soak", "rollback"):
        require(name in proofs, f"proofs lacks {name}")
        proof_paths[name] = validate_artifact(run, proofs[name])
    return evidence, candidate, {"desktop": snapshot, **proof_paths}


def validate_reboot(run, candidate):
    evidence, reboot_candidate, snapshot = validate_desktop(run)
    require(reboot_candidate == candidate, "reboot candidate differs from the user boot")
    require_bool(evidence, "warm_reboot")
    require_bool(evidence, "keyboard_response")
    require_bool(evidence, "pointer_response")
    validate_code_and_vram(evidence)
    proofs = evidence.get("proofs")
    require(isinstance(proofs, dict) and "warm_reboot" in proofs,
            "proofs lacks warm_reboot")
    proof = validate_artifact(run, proofs["warm_reboot"])
    return evidence, {"desktop": snapshot, "warm_reboot": proof}


def copy_record(source, destination, name):
    path = source / name
    require(path.is_file() and not path.is_symlink(), f"missing record {path}")
    target = destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)


def collect(instrument, user, reboot, output, markers):
    require(not output.exists(), f"output already exists: {output}")
    metadata = load_json(instrument / "run.json")
    candidate = require_candidate(metadata)
    verdict = pp_verdict.evaluate(instrument, markers)
    require(verdict["status"] == pp_verdict.CLEAN,
            f"instrument PP verdict is {verdict['status']}, not CLEAN")
    user_evidence, user_candidate, user_files = validate_user(user)
    require(user_candidate == candidate, "user candidate differs from the instrument boot")
    reboot_evidence, reboot_files = validate_reboot(reboot, candidate)

    # Validation finishes before the new output directory is created. Only named
    # evidence is copied; dumps and unrelated work files never enter the bundle.
    output.mkdir(parents=True)
    copied = []
    for name in ("run.json", "kdc-live.log", "captor-catches.log"):
        copy_record(instrument, output / "instrument", name)
        copied.append(output / "instrument" / name)
    if (instrument / "pp-events.json").is_file():
        copy_record(instrument, output / "instrument", "pp-events.json")
        copied.append(output / "instrument" / "pp-events.json")
    for source, destination, files in (
            (user, output / "user", user_files),
            (reboot, output / "reboot", reboot_files)):
        copy_record(source, destination, "evidence.json")
        copied.append(destination / "evidence.json")
        for path in files.values():
            relative = path.relative_to(source)
            copy_record(source, destination, str(relative))
            copied.append(destination / relative)

    summary = {
        "status": pp_verdict.CLEAN,
        "candidate_sha256": candidate,
        "pp": verdict,
        "user": user_evidence,
        "reboot": reboot_evidence,
    }
    (output / "acceptance.json").write_text(json.dumps(summary, indent=2) + "\n")
    copied.append(output / "acceptance.json")
    sums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(output)}\n"
        for path in sorted(copied))
    (output / "SHA256SUMS").write_text(sums)
    print(f"CLEAN: acceptance evidence collected at {output}")
    return 0


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    desktop = commands.add_parser("desktop")
    desktop.add_argument("--run", required=True, type=Path)
    collect_parser = commands.add_parser("collect")
    collect_parser.add_argument("--instrument", required=True, type=Path)
    collect_parser.add_argument("--user", required=True, type=Path)
    collect_parser.add_argument("--reboot", required=True, type=Path)
    collect_parser.add_argument("--out", required=True, type=Path)
    collect_parser.add_argument("--markers", type=Path,
                                default=root / "fixtures" / "graft" / "pp-markers.json")
    args = parser.parse_args(argv)

    if args.command == "desktop":
        _, candidate, _ = validate_desktop(args.run)
        verdict = {"status": "ATI_DESKTOP_VERIFIED", "candidate_sha256": candidate}
        (args.run / "desktop-verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
        print(f"ATI_DESKTOP_VERIFIED: {candidate}")
        return 0
    return collect(args.instrument, args.user, args.reboot, args.out, args.markers)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AcceptanceError as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        sys.exit(30)
    except Exception as error:
        print(f"VERIFIER ERROR: {error}", file=sys.stderr)
        sys.exit(70)
