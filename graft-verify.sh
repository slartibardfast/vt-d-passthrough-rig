#!/usr/bin/env bash
# Final graft acceptance: only CLEAN exits zero.
set -euo pipefail
RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$RIG/pp_verdict.py" "$@"
