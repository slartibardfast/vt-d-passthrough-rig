#!/usr/bin/env bash
# The paired register evidence, asserted rather than asserted-at.
#
# Two boots of the same golden image differ only in whether the grafted display package drives the mode.
# In the boot that withholds it the card's GRBM_STATUS (BAR 5, offset 0x8010) reads one value in every
# sample; in the boot that loads it the same register reads another. If either series drifts inside
# itself, or the pair differs by anything other than GUI_ACTIVE and CP_BUSY, this fails.
set -euo pipefail
cd "$(dirname "$0")"

read_one() {
  grep -o '+0x8010  0x[0-9a-fA-F]*' "$1" | awk '{print $2}' | sort -u
}

idle=$(read_one work/evidence/bar5-idle-series.log)
hung=$(read_one work/evidence/bar5-hung-series.log)

[ "$idle" = "0x00003028" ] || { printf 'idle series reads %s, not one value 0x00003028\n' "${idle:-nothing}" >&2; exit 1; }
[ "$hung" = "0xA0003028" ] || { printf 'grafted series reads %s, not one value 0xA0003028\n' "${hung:-nothing}" >&2; exit 1; }

[ $(( hung ^ idle )) -eq $(( (1 << 31) | (1 << 29) )) ] ||
  { printf 'the pair differs by %#x, which is not GUI_ACTIVE|CP_BUSY\n' $(( hung ^ idle )) >&2; exit 1; }

printf 'register pair: idle %s, grafted %s, differing by GUI_ACTIVE|CP_BUSY\n' "$idle" "$hung"
