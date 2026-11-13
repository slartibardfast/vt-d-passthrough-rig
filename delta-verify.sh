#!/usr/bin/env bash
# Verify one isolated build, its mounted deployment record, and its research boot.
set -euo pipefail
RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DELTA=
BASE=
RUN=
while [ "$#" -gt 0 ]; do
  case "$1" in
    --delta) DELTA=$2; shift 2 ;;
    --base) BASE=$2; shift 2 ;;
    --run) RUN=$2; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DELTA" ] && [ -n "$BASE" ] && [ -n "$RUN" ] || {
  echo "usage: delta-verify.sh --delta descriptor|dispatch|smc --base IMAGE --run RUN_DIR" >&2
  exit 2
}
case "$DELTA" in
  descriptor)
    MANIFEST=$RIG/fixtures/graft/manifest-descriptor-redirect-deploy.json
    EXPECT=$RIG/fixtures/graft/descriptor-redirect-deploy.expect.json
    ;;
  dispatch)
    MANIFEST=$RIG/fixtures/graft/manifest-dispatch-routing-deploy.json
    EXPECT=$RIG/fixtures/graft/dispatch-routing-deploy.expect.json
    ;;
  smc)
    MANIFEST=$RIG/fixtures/graft/manifest-smc-fill-deploy.json
    EXPECT=$RIG/fixtures/graft/smc-fill-deploy.expect.json
    ;;
  *) echo "unknown delta: $DELTA" >&2; exit 2 ;;
esac

BUILT=$RUN/ati2mtag.sys
if [ -e "$BUILT" ]; then
  python3 "$RIG/graft-check.py" --base "$BASE" --manifest "$MANIFEST" \
    --expect "$EXPECT" --candidate "$BUILT" --require-deployable
else
  python3 "$RIG/graft-check.py" --base "$BASE" --manifest "$MANIFEST" \
    --expect "$EXPECT" --output "$BUILT" --require-deployable
fi

python3 - "$RUN" "$EXPECT" <<'PY'
import hashlib
import json
from pathlib import Path
import sys

run = Path(sys.argv[1])
expectation = json.loads(Path(sys.argv[2]).read_text())
try:
    deployment = json.loads((run / "deployment.json").read_text())
    metadata = json.loads((run / "run.json").read_text())
    expected = expectation["output_sha256"]
    assert deployment["built_sha256"] == expected
    assert deployment["deployed_sha256"] == expected
    assert len(deployment["rollback_sha256"]) == 64
    assert metadata["candidate_sha256"] == expected
    built = run / "ati2mtag.sys"
    assert hashlib.sha256(built.read_bytes()).hexdigest() == expected
except (OSError, KeyError, AssertionError, json.JSONDecodeError) as error:
    print(f"INCONCLUSIVE: deployment or run identity mismatch: {error}", file=sys.stderr)
    sys.exit(30)
PY
python3 "$RIG/pp_verdict.py" --run "$RUN" --allow-expected
