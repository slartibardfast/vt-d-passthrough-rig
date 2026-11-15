#!/usr/bin/env bash
# Verify one isolated build, its mounted deployment record, and its research boot.
set -euo pipefail
RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELTA=
BASE=
RUN=
STRICT_PP=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --delta) DELTA=$2; shift 2 ;;
    --base) BASE=$2; shift 2 ;;
    --run) RUN=$2; shift 2 ;;
    --strict-pp) STRICT_PP=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DELTA" ] && [ -n "$BASE" ] && [ -n "$RUN" ] || {
  echo "usage: delta-verify.sh --delta descriptor|dispatch|smc --base IMAGE --run RUN_DIR [--strict-pp]" >&2
  exit 2
}
# The table lives in delta-paths.sh so the builder and the verifier cannot disagree about which
# manifest defines a variant.
. "$RIG/delta-paths.sh"
delta_paths "$DELTA" || { echo "unknown delta: $DELTA" >&2; exit 2; }

BUILT=$RUN/ati2mtag.sys
if [ -e "$BUILT" ]; then
  python3 "$RIG/graft-check.py" --base "$BASE" --manifest "$MANIFEST" \
    --expect "$EXPECT" --candidate "$BUILT" --require-deployable
else
  python3 "$RIG/graft-check.py" --base "$BASE" --manifest "$MANIFEST" \
    --expect "$EXPECT" --output "$BUILT" --require-deployable
fi

python3 - "$RUN" "$EXPECT" "$DELTA" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

run, delta = Path(sys.argv[1]), sys.argv[3]
expectation = json.loads(Path(sys.argv[2]).read_text())
expected = expectation.get("output_sha256")
if not expected:
    print("INCONCLUSIVE: the expectation file names no output_sha256, so nothing can be bound to it",
          file=sys.stderr)
    sys.exit(30)

# Each binding is named, because a bare AssertionError prints nothing to act on. Text that tells a
# reader what to do next has to say which link broke and what would repair it: an empty message
# sends the next session to read the script instead of the run.
failures = []
try:
    deployment = json.loads((run / "deployment.json").read_text())
except (OSError, json.JSONDecodeError) as error:
    deployment = None
    failures.append("deployment.json cannot be read (%s); rebuild the channel image with "
                    "./channel-image.sh --variant %s --run %s" % (delta, run))

if deployment is not None:
    for key in ("built_sha256", "deployed_sha256", "rollback_sha256"):
        have = deployment.get(key)
        if not isinstance(have, str) or len(have) != 64:
            failures.append("deployment.json %s is %r, not a 64-digit digest" % (key, have))
    if deployment.get("built_sha256") != expected:
        failures.append("the build is %s, the expectation is %s; the candidate in this run directory "
                        "is not the file the manifest declares"
                        % (str(deployment.get("built_sha256"))[:12], expected[:12]))
    if deployment.get("deployed_sha256") != expected:
        failures.append("the volume holds %s, not %s; the deployment moved a different file than the "
                        "build produced" % (str(deployment.get("deployed_sha256"))[:12], expected[:12]))

try:
    metadata = json.loads((run / "run.json").read_text())
except (OSError, json.JSONDecodeError) as error:
    metadata = None
    failures.append("run.json cannot be read (%s); the boot has no record to bind to" % error)

if metadata is not None:
    if metadata.get("candidate_sha256") != expected:
        failures.append("the boot ran a channel whose candidate is %s, not %s; this verdict belongs "
                        "to a different build" % (str(metadata.get("candidate_sha256"))[:12],
                                                  expected[:12]))
    # Two shapes, checked differently. A boot id is a name, so the test is that it is a non-empty
    # string; the session claims are verdicts, so the test is that they are true. Reading a name as
    # a boolean produces a remedy that is wrong about the failure, which is worse than no remedy.
    if not isinstance(metadata.get("boot_id"), str) or not metadata["boot_id"]:
        failures.append("run.json carries no boot id, so this boot cannot be named; re-run "
                        "./run-experiment.sh, which writes the nonce into the record")
    for key, remedy in (("session_ready", "the guest never witnessed a session, so a logon-time "
                                          "capture cannot be trusted; check the capture claim in "
                                          "./load-verify.py --run %s" % run),
                        ("desktop_reached", "no session resolution was reported; see the display-path "
                                            "claim in ./load-verify.py --run %s" % run)):
        if key in metadata and metadata[key] is not True:
            failures.append("run.json says %s is %r; %s" % (key, metadata[key], remedy))

built = run / "ati2mtag.sys"
if not built.is_file():
    failures.append("%s is absent; ./channel-image.sh --variant %s --run %s builds it"
                    % (built, delta, run))
elif hashlib.sha256(built.read_bytes()).hexdigest() != expected:
    failures.append("the candidate file on disk hashes to %s, not %s"
                    % (hashlib.sha256(built.read_bytes()).hexdigest()[:12], expected[:12]))

if failures:
    print("INCONCLUSIVE: the identity bindings do not all hold (%d)" % len(failures), file=sys.stderr)
    for line in failures:
        print("  - " + line, file=sys.stderr)
    sys.exit(30)
print("identity: build, deployment, boot record and candidate file all name %s" % expected[:20])
PY
# Two different verdicts come out of this script and they must not be read as one.
#
# The first is identity: did the build produce the bytes the expectation names, did the deployment
# record the file the volume held, and did the run bind itself to that candidate. That is a gate, and
# a failure there means the experiment cannot be attributed to anything, so it exits non-zero.
#
# The second is the PowerPlay verdict, which is a research finding rather than a property of the
# plumbing. The control boot is ASSERT_FAIL at baseline (`Invalid ASIC family!` and `PowerPlay
# uninitialization failed`), so a variant that changes nothing about PowerPlay is ASSERT_FAIL too,
# and grading that as a failed verify would hide the identity result a reader came for. It is
# therefore reported here with its own exit code and written to the run, and `--strict-pp` promotes
# it to a gate for whoever wants that.
if [ -n "$STRICT_PP" ]; then
  python3 "$RIG/pp_verdict.py" --run "$RUN" --allow-expected
else
  # delta-verify.sh runs under `set -e`, so a non-zero verifier has to be captured rather than left
  # to end the script: the identity result further up is what the reader came for.
  PP_EXIT=0
  python3 "$RIG/pp_verdict.py" --run "$RUN" --allow-expected >"$RUN/pp-verdict-line.txt" 2>&1     || PP_EXIT=$?
  # The verdict's full reason list is kept inside the run and summarised here. A real boot carries
  # forty-odd assertion lines, and reciting them at the terminal buries the one fact a reader came
  # for, which is the status, and buries the identity line under it, which is the part that gates.
  python3 - "$RUN" <<'SUMMARY'
import json
import re
import sys
from collections import Counter
from pathlib import Path

run = Path(sys.argv[1])
verdict = json.loads((run / "pp-verdict.json").read_text())
reasons = [line for line in verdict.get("reasons", []) if isinstance(line, str)]
head = [line for line in reasons if not re.search(r"\.c\[\d+\]", line)]
sites = Counter()
for line in reasons:
    match = re.search(r"([a-z_]+\.c)\[(\d+)\]", line)
    if match:
        sites[match.group(0)] += 1
print("pp: %s  %s" % (verdict["status"], ("; ".join(head) or (reasons[0][:80] if reasons else "")).strip()[:140]))
if sites:
    print("pp: %d assertion lines over %d sites: %s"
          % (sum(sites.values()), len(sites),
             ", ".join("%s x%d" % pair for pair in sites.most_common(4))))
print("pp: the full reason list is in %s/pp-verdict.json" % run)
SUMMARY
  python3 - "$RUN" "$PP_EXIT" <<'PY'
import json
from pathlib import Path
import sys

run, code = Path(sys.argv[1]), int(sys.argv[2])
verdict = json.loads((run / "pp-verdict.json").read_text())
verdict["exit_code"] = code
verdict["graded_by"] = ("identity is the gate; the PowerPlay verdict is reported because the control "
                        "is ASSERT_FAIL at baseline, so a variant is judged against that and not "
                        "against a clean run it was never expected to be")
temporary = run / "pp-verdict.json.tmp"
temporary.write_text(json.dumps(verdict, indent=2) + "\n")
temporary.replace(run / "pp-verdict.json")
print("pp: verdict %s recorded in the run (exit %s, reported not gated; --strict-pp gates it)"
      % (verdict["status"], code))
PY
fi
