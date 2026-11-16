#!/usr/bin/env bash
# Copy one file out of a channel image, read-only, and say what its bytes hash to.
#
# Why this exists. The display driver that the registry names lives on the install channel, and a run
# that installs it into the overlay has to name where the bytes came from. Referring to a hand-copied
# file in a scratch directory would leave the run's record citing bytes no later session can
# re-derive, which is the defect this rig keeps paying for. The channel image has a manifest beside it
# holding its own digest, so a file read out of it is attributable to that image.
#
# The image is attached read-only. Nothing here writes to a channel.
#
# Usage: channel-file.sh --image <channel.img> --name <file> --out <path>
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
IMG="" NAME="" OUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '1,18p' "$0"; exit 0;;
    --image) IMG=${2:-}; shift 2;;
    --name) NAME=${2:-}; shift 2;;
    --out) OUT=${2:-}; shift 2;;
    *) printf 'unknown argument: %s\nusage: %s\n' "$1" \
         'channel-file.sh --image <img> --name <file> --out <path>' >&2; exit 2;;
  esac
done
[ -n "$IMG" ] && [ -n "$NAME" ] && [ -n "$OUT" ] || {
  printf 'name the image, the file, and where to put it\nusage: %s\n' \
    'channel-file.sh --image <img> --name <file> --out <path>' >&2; exit 2; }
test -f "$IMG" || { printf 'no such channel image: %s\n' "$IMG" >&2; exit 2; }
case "$NAME" in */*|*..*) printf 'name a file, not a path: %s\n' "$NAME" >&2; exit 2;; esac

if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'a QEMU is running and may hold these images; refusing to read a volume under it\n' >&2
  exit 2
fi

OFFSET=$(python3 "$RIG/partition-offset.py" "$IMG") || exit 1
M=$(mktemp -d /tmp/opencode/channel-file-XXXX)
cleanup() { sudo umount "$M" >/dev/null 2>&1; rmdir "$M" >/dev/null 2>&1 || true; }
trap cleanup EXIT

sudo mount -o ro,loop,offset="$OFFSET" "$IMG" "$M" || {
  printf 'the channel image has no mountable first partition at byte %s\n' "$OFFSET" >&2; exit 1; }

FOUND=$(find "$M" -type f -iname "$NAME" -print -quit)
[ -n "$FOUND" ] || { printf '%s holds no %s\n' "$IMG" "$NAME" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
sudo cp "$FOUND" "$OUT" || exit 1
sudo chown "$(id -u):$(id -g)" "$OUT" 2>/dev/null || true
sudo chmod 644 "$OUT" 2>/dev/null || true
sync

printf 'source:   %s at %s\n' "$(basename "$IMG")" "${FOUND#"$M"}"
printf 'extracted: %s (%s bytes)\n' "$OUT" "$(stat -c %s "$OUT")"
printf 'sha256:    %s\n' "$(sha256sum "$OUT" | cut -d' ' -f1)"
