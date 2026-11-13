#!/usr/bin/env bash
# Import the real, proprietary analysis inputs from an operator-supplied tree.
# Every source is measured before it replaces a local ignored work file.
set -euo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MANIFEST=$RIG/fixtures/graft/real-inputs.tsv
SOURCE=

if [ "$#" -ne 2 ] || [ "$1" != --source ]; then
  echo "usage: evidence-import.sh --source EVIDENCE_ROOT" >&2
  exit 2
fi
SOURCE=$2
[ -d "$SOURCE" ] || { echo "missing evidence directory: $SOURCE" >&2; exit 30; }
[ ! -L "$RIG/work" ] || { echo "refusing symlinked work directory: $RIG/work" >&2; exit 30; }

while IFS=$'\t' read -r relative expected_size expected_sha; do
  case "$relative" in ''|\#*) continue ;; esac
  src=$SOURCE/$relative
  dst=$RIG/$relative
  [ -f "$src" ] || { echo "INCONCLUSIVE: missing $src" >&2; exit 30; }
  actual_size=$(stat -c %s -- "$src")
  actual_sha=$(sha256sum -- "$src" | cut -d' ' -f1)
  if [ "$actual_size" != "$expected_size" ] || [ "$actual_sha" != "$expected_sha" ]; then
    printf 'INCONCLUSIVE: input mismatch: %s\nexpected %s bytes %s\nactual   %s bytes %s\n' \
      "$src" "$expected_size" "$expected_sha" "$actual_size" "$actual_sha" >&2
    exit 30
  fi
  mkdir -p "$(dirname -- "$dst")"
  tmp=$dst.import-tmp
  [ ! -e "$tmp" ] || { echo "stale import temporary exists: $tmp" >&2; exit 30; }
  cp -- "$src" "$tmp"
  printf '%s  %s\n' "$expected_sha" "$tmp" | sha256sum -c - >/dev/null
  mv -- "$tmp" "$dst"
  printf 'imported %s (%s bytes, sha256 %s)\n' "$relative" "$expected_size" "$expected_sha"
done < "$MANIFEST"
