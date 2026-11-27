#!/usr/bin/env bash
# Extract the 32-bit ati2cqag from the pinned Catalyst XP pack, expand it, and check its digest.
#
# The compare task needs the same-era x86 build of the DLL. It lives inside the corpus pack as an SZDD
# compressed member, so the extraction is recorded here rather than left as a binary in an ignored tree.
set -euo pipefail
cd "$(dirname "$0")"

PACK=../../xp64-driver-corpus/main/drivers/14-4-xp32-64-dd-ccc-pack1.exe
PACK_SHA=76f8b5db65d36c8da84111fd55301f6dfb931dee5c12677de9ea24b3b75a1943
MEMBER='Packages/Drivers/Display/XP_INF/B166777/ati2cqag.dl_'
OUT=work/x86-twin
DLL_SHA=4e343ea6b54e54a99dcf959989796640dbc1dc3d6ba558ddc00816ca2f6ea40b

got=$(sha256sum "$PACK" | cut -d' ' -f1)
[ "$got" = "$PACK_SHA" ] || { printf 'pack digest %s, expected %s\n' "$got" "$PACK_SHA" >&2; exit 1; }

mkdir -p "$OUT"
if [ ! -s "$OUT/ati2cqag.dll" ] || [ "$(sha256sum "$OUT/ati2cqag.dll" | cut -d' ' -f1)" != "$DLL_SHA" ]; then
  rm -f "$OUT/ati2cqag.dl_" "$OUT/ati2cqag.dll"
  7z e -y -o"$OUT" "$PACK" "$MEMBER" >/dev/null
  ./venv/bin/python szdd.py "$OUT/ati2cqag.dl_" "$OUT/ati2cqag.dll" >/dev/null
fi

got=$(sha256sum "$OUT/ati2cqag.dll" | cut -d' ' -f1)
[ "$got" = "$DLL_SHA" ] || { printf 'x86 twin digest %s, expected %s\n' "$got" "$DLL_SHA" >&2; exit 1; }
printf 'x86 twin: %s sha256 %s\n' "$OUT/ati2cqag.dll" "$got"
