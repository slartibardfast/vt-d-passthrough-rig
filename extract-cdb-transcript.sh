#!/usr/bin/env bash
# Extract C:\cdb-out.txt from a finished run's overlay, read-only, and check its digest.
#
# The cdb1 run left the guest's debugger transcript inside its overlay. The overlay is a qcow2 with the
# golden disk as its backing file, so the transcript can be read without booting the guest: export the
# image over nbd, mount the NTFS partition read-only, copy the one file, and detach.
set -euo pipefail
cd "$(dirname "$0")"

RUN=${1:-cdb1}
IMG=work/runs/$RUN/overlay.qcow2
OUT=work/evidence/cdb-out.txt
WANT=2fc9c0d8cbe57c37d248532c189e26861481f71ea0b90c81fca1d618872f0399

[ -s "$IMG" ] || { printf 'no overlay at %s\n' "$IMG" >&2; exit 1; }

MNT=$(mktemp -d)
cleanup() {
  sudo umount "$MNT" 2>/dev/null || true
  sudo qemu-nbd -d /dev/nbd0 >/dev/null 2>&1 || true
  rmdir "$MNT" 2>/dev/null || true
}
trap cleanup EXIT

sudo qemu-nbd -r -c /dev/nbd0 "$IMG"
for _ in $(seq 1 20); do [ -b /dev/nbd0p1 ] && break; sleep 1; done
[ -b /dev/nbd0p1 ] || { printf 'the overlay has no NTFS partition at p1\n' >&2; exit 1; }
sudo ntfs-3g -o ro /dev/nbd0p1 "$MNT"
[ -s "$MNT/cdb-out.txt" ] || { printf 'cdb-out.txt is not on the NTFS partition\n' >&2; exit 1; }
cp "$MNT/cdb-out.txt" "$OUT"

got=$(sha256sum "$OUT" | cut -d' ' -f1)
[ "$got" = "$WANT" ] || { printf 'transcript digest %s, expected %s\n' "$got" "$WANT" >&2; exit 1; }
printf 'cdb-out.txt: %s bytes sha256 %s\n' "$(wc -c <"$OUT")" "$got"
