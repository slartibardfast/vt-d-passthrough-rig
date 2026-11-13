#!/usr/bin/env bash
set -euo pipefail
RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
exec python3 "$RIG/graft_deploy.py" "$@"
