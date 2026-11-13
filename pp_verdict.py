#!/usr/bin/env python3
"""Classify PowerPlay boot evidence without turning missing evidence into a pass."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


CLEAN = "CLEAN"
EXPECTED = "EXPECTED_EXPERIMENTAL"
ASSERT_FAIL = "ASSERT_FAIL"
INCONCLUSIVE = "INCONCLUSIVE"
EXIT_CODES = {CLEAN: 0, EXPECTED: 10, ASSERT_FAIL: 20, INCONCLUSIVE: 30}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path):
    return json.loads(path.read_text())


def safe_artifact(run, name, expected_sha, allow_empty=False):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe artifact path: {name}")
    full = run / path
    if not full.is_file() or (full.stat().st_size == 0 and not allow_empty):
        raise ValueError(f"missing or empty artifact: {name}")
    resolved = full.resolve()
    if run.resolve() not in resolved.parents:
        raise ValueError(f"artifact escapes the run directory: {name}")
    if digest(full) != expected_sha:
        raise ValueError(f"artifact digest mismatch: {name}")
    return full


def evaluate(run, markers_path):
    """Return a verdict dictionary; every evidence error becomes INCONCLUSIVE."""
    reasons = []
    try:
        metadata = load_json(run / "run.json")
        markers = load_json(markers_path)
        for key in ("candidate_sha256", "boot_id"):
            if not isinstance(metadata.get(key), str) or not metadata[key]:
                raise ValueError(f"run.json lacks {key}")
        if not re.fullmatch(r"[0-9a-f]{64}", metadata["candidate_sha256"]):
            raise ValueError("candidate_sha256 is not a lowercase SHA-256")
        if metadata.get("session_ready") is not True:
            raise ValueError("session_ready is not true")
        if metadata.get("abandoned") is not False:
            raise ValueError("the run was abandoned")
        if metadata.get("desktop_reached") is not True:
            raise ValueError("desktop_reached is not true")
        if not isinstance(metadata.get("manual_resume"), bool):
            raise ValueError("manual_resume must be boolean")
        artifacts = metadata.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ValueError("artifacts must be an object")
        log_paths = {}
        for name in ("kdc-live.log", "captor-catches.log"):
            if name not in artifacts:
                raise ValueError(f"artifacts lacks {name}")
            log_paths[name] = safe_artifact(
                run, name, artifacts[name], allow_empty=name == "captor-catches.log")
        kdc = log_paths["kdc-live.log"].read_text(errors="replace")
        captor = log_paths["captor-catches.log"].read_text(errors="replace")
        combined = kdc + "\n" + captor
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {"status": INCONCLUSIVE, "reasons": [str(error)]}

    assert_patterns = [re.compile(pattern) for pattern in markers.get("assert_patterns", [])]
    failure_patterns = [re.compile(pattern) for pattern in markers.get("failure_patterns", [])]
    completion_patterns = [re.compile(pattern) for pattern in markers.get("init_completed", [])]
    assert_lines = [line for line in combined.splitlines()
                    if any(pattern.search(line) for pattern in assert_patterns)]
    failure_lines = [line for line in combined.splitlines()
                     if any(pattern.search(line) for pattern in failure_patterns)]
    completed = any(pattern.search(kdc) for pattern in completion_patterns)

    if failure_lines:
        return {"status": ASSERT_FAIL,
                "reasons": ["known PowerPlay failure output"] + failure_lines}

    expected_fingerprint = metadata.get("expected_assert")
    if expected_fingerprint is not None and not isinstance(expected_fingerprint, str):
        return {"status": INCONCLUSIVE,
                "reasons": ["expected_assert must be a string or null"]}

    # Captor output must be accounted for. Known boot breakpoints and the one
    # declared experiment are named by substrings in run.json.
    expected_captor = metadata.get("expected_captor", [])
    if not isinstance(expected_captor, list) or not all(
            isinstance(item, str) and item for item in expected_captor):
        return {"status": INCONCLUSIVE,
                "reasons": ["expected_captor must contain non-empty strings"]}
    unknown_captor = []
    for line in captor.splitlines():
        if not line.strip():
            continue
        if any(item in line for item in expected_captor):
            continue
        if expected_fingerprint and expected_fingerprint in line:
            continue
        unknown_captor.append(line)

    if not assert_lines:
        if expected_fingerprint:
            return {"status": INCONCLUSIVE,
                    "reasons": ["the predeclared assert did not occur"]}
        if unknown_captor:
            return {"status": INCONCLUSIVE,
                    "reasons": ["unclassified captor output"] + unknown_captor}
        if metadata["manual_resume"]:
            return {"status": INCONCLUSIVE,
                    "reasons": ["manual resume without a declared assert"]}
        if not completed:
            return {"status": INCONCLUSIVE,
                    "reasons": ["no measured positive PowerPlay completion marker"]}
        return {"status": CLEAN, "reasons": ["positive completion and no assert"]}

    fingerprints = set()
    for line in assert_lines:
        if expected_fingerprint and expected_fingerprint in line:
            fingerprints.add(expected_fingerprint)
        else:
            fingerprints.add(line.strip())
    if not expected_fingerprint or len(fingerprints) != 1:
        return {"status": ASSERT_FAIL,
                "reasons": ["unexpected or repeated PowerPlay assert"] + assert_lines}
    if fingerprints != {expected_fingerprint}:
        return {"status": ASSERT_FAIL,
                "reasons": ["assert fingerprint differs from the predeclaration"] + assert_lines}
    if unknown_captor:
        return {"status": ASSERT_FAIL,
                "reasons": ["unexpected captor output during an assert run"] + unknown_captor}

    try:
        events = load_json(run / "pp-events.json")
        if not isinstance(events, list) or len(events) != 1:
            raise ValueError("expected experiment needs exactly one PP event")
        event = events[0]
        if event.get("fingerprint") != expected_fingerprint:
            raise ValueError("PP event fingerprint mismatch")
        if event.get("first_chance") is not True or event.get("second_chance") is not False:
            raise ValueError("PP event is not a first-chance-only catch")
        if event.get("resumed") is not True or metadata["manual_resume"] is not True:
            raise ValueError("PP event lacks its recorded resume")
        for key in ("context", "dump"):
            name = event.get(key)
            if not isinstance(name, str) or name not in artifacts:
                raise ValueError(f"PP event lacks bound {key} evidence")
            safe_artifact(run, name, artifacts[name])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {"status": INCONCLUSIVE, "reasons": [str(error)]}
    if not completed:
        return {"status": INCONCLUSIVE,
                "reasons": ["caught assert lacks later positive PowerPlay completion"]}
    return {"status": EXPECTED,
            "reasons": ["one predeclared catch with bound evidence and completed resume"]}


def write_verdict(run, verdict):
    result = dict(verdict)
    result["run"] = run.name
    temporary = run / "pp-verdict.json.tmp"
    temporary.write_text(json.dumps(result, indent=2) + "\n")
    temporary.replace(run / "pp-verdict.json")


def main(argv=None):
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--markers", type=Path,
                        default=root / "fixtures" / "graft" / "pp-markers.json")
    parser.add_argument("--allow-expected", action="store_true")
    args = parser.parse_args(argv)
    verdict = evaluate(args.run, args.markers)
    write_verdict(args.run, verdict)
    print(f"{verdict['status']}: " + "; ".join(verdict["reasons"]))
    if verdict["status"] == EXPECTED and args.allow_expected:
        return 0
    return EXIT_CODES[verdict["status"]]


if __name__ == "__main__":
    sys.exit(main())
