#!/usr/bin/env python3
"""Write the run's own contract into its record, derived from the evidence this boot left behind.

Why this is a file and not a shell heredoc: the fields below are claims about a boot, and a claim
that cannot be called by a test is a claim that cannot be checked. `pp_verdict.py`, the campaign's
strict PowerPlay grader, refuses to answer at all unless a run record carries eight named fields, so
there is a strong temptation to type constants into a script and move on. That is the hollow-green
defect with a timestamp on it, and it is the one thing this rig exists to avoid.

So each field is read off evidence, and where the evidence does not support a claim, the field says
so and the grader's verdict reflects that:

  boot_id            the run's nonce, which is also stamped into every capture the guest wrote
  session_ready      the logon-time Startup trigger ran to completion and signed this run's nonce;
                     that is what this rig can actually witness of a session, so nothing stronger
                     (a compositor, a shell) is claimed here
  desktop_reached    session_ready, plus a horizontal resolution the guest reported for the session
  manual_resume      the KD stream carries a break-in, or the collector recorded a catch: something
                     stopped the guest and something let it go again
  abandoned          the run did not end by the guest shutting itself down
  expected_assert    always null here: no run of this loop is designed around a named catch, and the
                     grader reads a null as "any assert in this log is unexplained", which is honest
  expected_captor    empty for the same reason
  artifacts          every file the run collected, mapped to the sha256 of the bytes on disk

Usage: run_contract.py <run.json> <run-dir> <nonce> <outcome>
"""
import hashlib
import json
import re
import sys
from pathlib import Path

LOG_NAMES = ("kdc-detached.log", "kdc-live.log", "captor-catches.log", "seabios.log",
             "run-driver-detached.log", "feed-output", "evidence-list.txt")
NOTE = ("artifacts maps a path inside the run directory to the sha256 of the bytes this run "
        "collected; session_ready is the Startup trigger's own signed witness, desktop_reached adds "
        "a reported horizontal resolution from Win32_VideoController, and manual_resume is the KD "
        "stream's own break-in or catch record. No field here is a default.")


def read(path):
    """Decode a guest-written file as the bytes it is, since Windows writes two different ones."""
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    return raw.decode("latin-1", "replace")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def session_witness(capture_text, nonce):
    """Whether the guest's own logon-time capture names this run and finished."""
    return bool(capture_text) and nonce in capture_text \
        and "capture-start" in capture_text and "capture-end" in capture_text


def reported_resolution(video_text):
    match = re.search(r"(?im)^\s*CurrentHorizontalResolution\s*=\s*(\d+)", video_text)
    return int(match.group(1)) if match else 0


def broke_in_or_was_caught(kd_text, catches_text):
    return bool(re.search(r"(?i)breakin sent|Breakin", kd_text)) or bool(catches_text.strip())


def collect_artifacts(run):
    """Every file this run left, by path relative to the run directory, with the bytes' digest.

    The evidence manifest is the authoritative list of what was read out of the overlay, and its
    digests were taken at collection time; the host-written logs are hashed now. A ledger line whose
    file is not on disk is skipped, because a record that names a file the run does not have would
    make the grader fail for a reason that has nothing to do with the boot.
    """
    artifacts = {}
    evidence = run / "evidence"
    ledger = evidence / "MANIFEST.tsv"
    if ledger.is_file():
        for line in ledger.read_text().splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) == 3 and (evidence / parts[0]).is_file():
                artifacts["evidence/" + parts[0]] = parts[1]
        if ledger.is_file():
            artifacts["evidence/MANIFEST.tsv"] = digest(ledger)
    for name in LOG_NAMES:
        path = run / name
        if path.is_file():
            artifacts[name] = digest(path)
    for path in sorted(run.glob("feed-*.out")):
        artifacts[path.name] = digest(path)
    for name in ("boot.ini.before", "boot.ini.after", "letters.bat.before", "letters.bat.after"):
        path = run / name
        if path.is_file():
            artifacts[name] = digest(path)
    return artifacts


def contract(run, nonce, outcome):
    evidence = run / "evidence"
    kd = read(run / "kdc-detached.log")
    catches = read(run / "captor-catches.log")
    ready = session_witness(read(evidence / "WINDOWS/capture.txt"), nonce)
    resolution = reported_resolution(read(evidence / "WINDOWS/videoctl.txt"))
    return {
        "boot_id": nonce,
        "session_ready": ready,
        "desktop_reached": bool(ready and resolution > 0),
        "manual_resume": broke_in_or_was_caught(kd, catches),
        "abandoned": outcome != "measured-shutdown",
        "expected_assert": None,
        "expected_captor": [],
        "artifacts": collect_artifacts(run),
        "artifacts_note": NOTE,
        "session_rule": "the logon-time Startup trigger ran to completion and signed this run's nonce",
        "desktop_rule": "session_ready and a session resolution the guest itself reported (%s)"
                        % (resolution or "none reported"),
    }


def candidate_from_channel_or_deployment(run, data):
    """The driver this boot actually ran, from whichever record the run directory holds.

    A variant channel built by `channel-image.sh` carries a manifest naming its candidate, and the
    runner copies it into the run. An older run, or a control boot off a channel with no manifest,
    still has a deployment record beside it, and that record's deployed hash is the digest of the
    file the volume held. Neither is guessed: if a run has neither, it has no candidate claim, and the
    grader says so.
    """
    if data.get("channel_manifest") and Path(data["channel_manifest"]).is_file():
        record = json.loads(Path(data["channel_manifest"]).read_text())
        if "candidate_sha256" in record:
            return record["candidate_sha256"], "the channel manifest copied into this run"
    deployment = run / "deployment.json"
    if deployment.is_file():
        record = json.loads(deployment.read_text())
        if "deployed_sha256" in record:
            return record["deployed_sha256"], \
                "the deployment record's hash of the file the volume held"
    return None, "no channel manifest and no deployment record, so this run claims no candidate"


def main(argv):
    if len(argv) != 5:
        print("usage: run_contract.py <run.json> <run-dir> <nonce> <outcome>", file=sys.stderr)
        return 2
    path, run, nonce, outcome = Path(argv[1]), Path(argv[2]), argv[3], argv[4]
    if not path.is_file():
        print("run_contract: no run record to amend: %s" % path, file=sys.stderr)
        return 2
    data = json.loads(path.read_text())
    data.update(contract(run, nonce, outcome))
    candidate, rule = candidate_from_channel_or_deployment(run, data)
    if candidate:
        data["candidate_sha256"] = candidate
    elif "candidate_sha256" not in data:
        data["candidate_sha256"] = None
    data["candidate_rule"] = rule
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True))
    temporary.replace(path)
    print("contract: session_ready=%s desktop_reached=%s manual_resume=%s abandoned=%s "
          "candidate=%s artifacts=%d"
          % (data["session_ready"], data["desktop_reached"], data["manual_resume"],
             data["abandoned"], str(data["candidate_sha256"])[:12], len(data["artifacts"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
