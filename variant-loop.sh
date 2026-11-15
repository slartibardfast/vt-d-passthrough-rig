#!/usr/bin/env bash
# One table that binds each single-delta verdict to the bytes it ran, and a verifier that re-derives it.
#
# Why this exists. `plan/0007` says the combined graft stands on this table: each delta it combines
# has to have a boot behind it. A table that was written out once is a photograph, and the thing about
# a photograph is that it keeps looking correct after the run directory under it moves. So the table is
# generated, and `--verify` recomputes every cell from the artifacts on disk and refuses the table if
# any cell disagrees with what it re-read. A stale row then fails the same gate a stale receipt fails.
#
# What each row binds. The candidate hash from the channel image's own manifest, the image hash, the
# ops manifest that produced the candidate, the run's nonce and outcome, the identity binding the
# deployment record and the run record and the candidate file all have to agree on, the PowerPlay
# verdict with its site-and-count fingerprint, and that fingerprint's difference from the control's.
# The last is the column that carries the research: two boots a byte apart are only a comparison if the
# observable difference between them is written down.
#
# Nothing here boots a machine. It reads what the loop already left behind.
#
# Usage: variant-loop.sh --build [--out DIR]
#        variant-loop.sh --verify [DIR]
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# The tree whose work/runs and work/channels are read, and where the table is written, is the
# script's own home by default. It can be named for a test: a table whose every cell is re-derived
# from disk is worth little if the only thing that can exercise it is a live rig with four boots in
# it, and a synthetic tree lets a broken row be built on purpose.
RIG=${VARIANT_LOOP_RIG:-$RIG}
MODE="" OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '1,26p' "$0"; exit 0;;
    --build) MODE=build; shift;;
    --verify) MODE=verify; shift;;
    --out) OUT=${2:-}; shift 2;;
    -*) printf 'unknown argument: %s\nusage: %s\n' "$1" \
         'variant-loop.sh --build [--out DIR] | --verify [DIR]' >&2; exit 2;;
    *) # A bare directory is accepted after --verify, because that is the shape the milestone's own
       # verify command uses: `./variant-loop.sh --verify work/runs/matrix`.
       if [ "$MODE" != verify ]; then
         printf '%s is not a command: say --build or --verify, then the directory\n' "$1" >&2
         exit 2
       fi
       OUT=${1}; shift;;
  esac
done
[ -n "$MODE" ] || { printf 'say --build or --verify\nusage: %s\n' \
  'variant-loop.sh --build|--verify [--out DIR]' >&2; exit 2; }
OUT=${OUT:-$RIG/work/runs/matrix}
case "$OUT" in /*) ;; *) OUT="$RIG/$OUT";; esac
test -d "$RIG" || { printf 'no rig tree at %s\n' "$RIG" >&2; exit 2; }

python3 - "$MODE" "$OUT" "$RIG" <<'PY'
import datetime
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

mode, out, rig = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
TABLE = out / "variant-matrix.tsv"
READABLE = out / "variant-matrix.md"
COLUMNS = ("run", "role", "nonce", "outcome", "lived_seconds", "candidate_sha256",
           "channel_image_sha256", "ops_manifest", "pp_status", "pp_sites", "pp_lines",
           "pp_delta_vs_control", "display_path", "identity", "evidence_manifest")
CONTROL = "baseline"
VARIANTS = ("descriptor", "dispatch", "smc")
PP = re.compile(r"(?i)(?:PP_PowerPlay|PowerPlay)")
SITE = re.compile(r"([a-z_]+\.c)\[(\d+)\]")
MODULE = re.compile(r"MODULE LOAD.*?name=\\?WINDOWS\\?System32\\?([A-Za-z0-9_]+\.dll)", re.I)
OPENED = re.compile(r"FILE_IO.*?\\((?i:[a-z0-9_]+\.dll))")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def read_utf16_or_text(path):
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    return raw.decode("latin-1", "replace")


def fingerprint(run):
    """Site-and-count over the PowerPlay lines, the shape the driver's own output has."""
    log = (run / "kdc-detached.log")
    text = log.read_text(errors="replace") if log.is_file() else ""
    lines = [l for l in text.splitlines() if PP.search(l)]
    counts = Counter()
    for line in lines:
        match = SITE.search(line)
        counts[match.group(0) if match else line[-48:]] += 1
    return counts, text


def display_path(text, run):
    """Which display driver took the session, from the module stream and the guest's own report."""
    mapped = {name.lower() for name in MODULE.findall(text)}
    opened = {name.lower() for name in OPENED.findall(text)}
    video = read_utf16_or_text(run / "evidence" / "WINDOWS" / "videoctl.txt")
    mode = re.search(r"(?im)^\s*CurrentHorizontalResolution\s*=\s*(\d+)", video)
    resolution = mode.group(1) if mode else "0"
    if "ati2dvag.dll" in mapped:
        what = "ati2dvag.dll mapped"
    elif "vga.dll" in mapped:
        what = "ati2dvag.dll opened-but-not-loaded and vga.dll mapped" if "ati2dvag.dll" in opened \
            else "vga.dll mapped"
    else:
        what = "neither ati2dvag.dll nor vga.dll mapped"
    return "%s at %sp" % (what, resolution) if resolution != "0" else what


def identity(run, expected=None):
    """Every record that names a candidate, and whether they all name the same one."""
    names = {}
    candidate = run / "ati2mtag.sys"
    if candidate.is_file():
        names["candidate-file"] = sha(candidate)
    deployment = run / "deployment.json"
    if deployment.is_file():
        record = json.loads(deployment.read_text())
        for key in ("built_sha256", "deployed_sha256"):
            if key in record:
                names["deployment-" + key.split("_")[0]] = record[key]
    manifest = run / "channel-manifest.json"
    if manifest.is_file():
        record = json.loads(manifest.read_text())
        if "candidate_sha256" in record:
            names["channel-manifest"] = record["candidate_sha256"]
    record_path = run / "run.json"
    if record_path.is_file():
        data = json.loads(record_path.read_text())
        if data.get("candidate_sha256"):
            names["run-record"] = data["candidate_sha256"]
    if expected:
        names["expectation"] = expected
    distinct = set(names.values())
    return ("BOUND" if len(distinct) == 1 and None not in distinct else "NOT-BOUND"), names


def row(name, role):
    run = rig / "work" / "runs" / name
    if not run.is_dir():
        return None
    data = json.loads((run / "run.json").read_text()) if (run / "run.json").is_file() else {}
    channel_manifest = run / "channel-manifest.json"
    candidate = channel_image = ops = ""
    if channel_manifest.is_file():
        record = json.loads(channel_manifest.read_text())
        candidate = record.get("candidate_sha256", "")
        channel_image = record.get("image_sha256", "")
        ops = Path(record.get("ops_manifest", "")).name
    else:
        candidate = data.get("candidate_sha256") or ""
        channel_image = data.get("channel_sha256", "")
    expectation = ""
    if ops:
        for stem in ("descriptor-redirect", "dispatch-routing", "smc-fill"):
            if stem in ops:
                path = rig / "fixtures" / "graft" / ("%s-deploy.expect.json" % stem)
                if path.is_file():
                    expectation = json.loads(path.read_text()).get("output_sha256", "")
    counts, text = fingerprint(run)
    verdict_path = run / "pp-verdict.json"
    pp_status = json.loads(verdict_path.read_text())["status"] if verdict_path.is_file() else "unjudged"
    bound, names = identity(run, expectation)
    manifest_path = run / "evidence" / "MANIFEST.tsv"
    evidence_rows = len(manifest_path.read_text().splitlines()) - 1 if manifest_path.is_file() else 0
    return {
        "run": name,
        "role": role,
        "nonce": data.get("nonce", ""),
        "outcome": data.get("outcome", ""),
        "lived_seconds": str(data.get("lived_seconds", "")),
        "candidate_sha256": candidate,
        "channel_image_sha256": channel_image,
        "ops_manifest": ops or "(the control's ungrafted base)",
        "pp_status": pp_status,
        "pp_sites": ";".join("%s=%d" % pair for pair in sorted(counts.items())),
        "pp_lines": str(len(list(counts.elements()))),
        "pp_delta_vs_control": "",
        "display_path": display_path(text, run),
        "identity": bound,
        "identity_names": names,
        "evidence_manifest": "%d file(s)" % evidence_rows,
    }


def build():
    rows = [row(CONTROL, "control")]
    rows += [row(v, "variant") for v in VARIANTS]
    rows = [r for r in rows if r]
    control = next((r for r in rows if r["role"] == "control"), None)
    base_counts = Counter()
    if control:
        for pair in control["pp_sites"].split(";"):
            if "=" in pair:
                site, count = pair.rsplit("=", 1)
                base_counts[site] = int(count)
    for current in rows:
        here = Counter()
        for pair in current["pp_sites"].split(";"):
            if "=" in pair:
                site, count = pair.rsplit("=", 1)
                here[site] = int(count)
        if current["role"] == "control":
            current["pp_delta_vs_control"] = "(the reference)"
            continue
        added = ["+%s x%d" % (s, n) for s, n in sorted(here.items()) if s not in base_counts]
        gone = ["-%s x%d" % (s, base_counts[s]) for s in sorted(base_counts) if s not in here]
        moved = ["~%s %d->%d" % (s, base_counts[s], here[s]) for s in sorted(here)
                 if s in base_counts and here[s] != base_counts[s]]
        notes = added + gone + moved
        current["pp_delta_vs_control"] = "; ".join(notes) if notes else "none"
    out.mkdir(parents=True, exist_ok=True)
    TABLE.write_text("\t".join(COLUMNS) + "\n"
                     + "".join("\t".join(str(r.get(c, "")) for c in COLUMNS) + "\n" for r in rows))
    READABLE.write_text(render(rows))
    print("built %s" % TABLE)
    print("built %s" % READABLE)
    for current in rows:
        print("  %-11s %-8s candidate=%s identity=%s pp=%s delta=%s"
              % (current["run"], current["role"], current["candidate_sha256"][:12] or "(none)",
                 current["identity"], current["pp_status"], current["pp_delta_vs_control"][:60]))
    return 0


def render(rows):
    lines = ["# The variant matrix",
             "",
             "Generated by `./variant-loop.sh --build`; checked by `./variant-loop.sh --verify`, which",
             "re-reads every cell from the run directory and the channel manifest and fails the table if",
             "a cell no longer matches. Do not edit this file: regenerate it.",
             "",
             "| run | role | candidate | channel image | PowerPlay | delta vs control | display path | identity |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows:
        lines.append("| `%s` | %s | `%s` | `%s` | %s lines, %s | %s | %s | %s |"
                     % (r["run"], r["role"], (r["candidate_sha256"] or "none")[:12],
                        (r["channel_image_sha256"] or "none")[:12], r["pp_lines"], r["pp_status"],
                        r["pp_delta_vs_control"] or "n/a", r["display_path"], r["identity"]))
    control = next((r for r in rows if r["role"] == "control"), None)
    if control and control["pp_status"] == "unjudged":
        lines += ["",
                  "The control row's PowerPlay verdict is `unjudged` because that boot predates",
                  "`run_contract.py`, so its `run.json` carries no contract to grade against and its",
                  "directory is left exactly as it was taken. Its fingerprint is still read from its own",
                  "KD log, which is what the delta column compares against, and grading a copy of that",
                  "run's record with the contract attached draws `ASSERT_FAIL` on the two known baseline",
                  "failures, `Invalid ASIC family!` at `cislands_hwmgr.c[6179]` and `powerplay.c[191]`",
                  "`PowerPlay uninitialization failed`. The variant rows are graded in place.", ""]
    lines += ["", "## Per-run detail", ""]
    for r in rows:
        lines += ["### `%s` (%s)" % (r["run"], r["role"]), "",
                  "- nonce `%s`, outcome `%s`, lived %s s, evidence %s"
                  % (r["nonce"], r["outcome"], r["lived_seconds"], r["evidence_manifest"]),
                  "- ops manifest: `%s`" % r["ops_manifest"],
                  "- PowerPlay sites: %s" % (r["pp_sites"].replace(";", ", ") or "none"),
                  "- records naming a candidate: %s" % ", ".join(
                      sorted(set("%s=%s" % (k, v[:12]) for k, v in r["identity_names"].items()))),
                  ""]
    return "\n".join(lines) + "\n"


def verify():
    if not TABLE.is_file():
        print("variant-loop: no table at %s; build it with ./variant-loop.sh --build" % TABLE,
              file=sys.stderr)
        return 2
    lines = TABLE.read_text().rstrip("\n").split("\n")
    header = lines[0].split("\t")
    if header != list(COLUMNS):
        print("variant-loop: the table's columns are %s, and this tool writes %s"
              % (header, list(COLUMNS)), file=sys.stderr)
        return 1
    stored = [dict(zip(header, l.split("\t"))) for l in lines[1:]]
    bad = []
    for old in stored:
        fresh = row(old["run"], old["role"])
        if fresh is None:
            bad.append("%s: the run directory is gone, so this row witnesses nothing" % old["run"])
            continue
        fresh.pop("identity_names", None)
        for column in COLUMNS:
            have, want = str(old.get(column, "")), str(fresh.get(column, ""))
            if column == "pp_delta_vs_control":
                here = Counter()
                for pair in fresh["pp_sites"].split(";"):
                    if "=" in pair:
                        site, count = pair.rsplit("=", 1)
                        here[site] = int(count)
                control = next((s for s in stored if s["role"] == "control"), None)
                base = Counter()
                if control:
                    for pair in control["pp_sites"].split(";"):
                        if "=" in pair:
                            site, count = pair.rsplit("=", 1)
                            base[site] = int(count)
                added = ["+%s x%d" % (s, n) for s, n in sorted(here.items()) if s not in base]
                gone = ["-%s x%d" % (s, base[s]) for s in sorted(base) if s not in here]
                moved = ["~%s %d->%d" % (s, base[s], here[s]) for s in sorted(here)
                         if s in base and here[s] != base[s]]
                want = "; ".join(added + gone + moved) or ("(the reference)"
                                                           if fresh["role"] == "control" else "none")
            if have != want:
                bad.append("%s: %s reads `%s` in the table and `%s` on disk"
                           % (old["run"], column, have[:60], want[:60]))
    if bad:
        print("verdict: FAIL (%d stale or broken cell(s))" % len(bad))
        for line in bad:
            print("  %s" % line)
        print("  remedy: ./variant-loop.sh --build regenerates the table from what is on disk now")
        return 1
    bound = [s for s in stored if s["identity"] != "BOUND"]
    if bound:
        print("verdict: FAIL (%d row(s) whose records do not all name one candidate: %s)"
              % (len(bound), ", ".join(s["run"] for s in bound)))
        for s in bound:
            print("  remedy: ./delta-verify.sh --delta %s --base work/stageE/"
                  "ati2mtag-s5_accessor_blob.sys --run work/runs/%s" % (s["run"], s["run"]))
        return 1
    print("verdict: PASS (rows re-derived: %d), every record naming one candidate" % len(stored))
    for s in stored:
        print("  %-11s %-8s candidate=%s identity=%s pp=%s delta=%s"
              % (s["run"], s["role"], (s["candidate_sha256"] or "none")[:12], s["identity"],
                 s["pp_status"], s["pp_delta_vs_control"][:70]))
    return 0


sys.exit(build() if mode == "build" else verify())
PY
