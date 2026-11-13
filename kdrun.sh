#!/usr/bin/env bash
set -euo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export WINEPREFIX=${WINEPREFIX:-$RIG/wineprefix}
export WINEDEBUG=-all
export LD_PRELOAD=$RIG/ttyshim.so
KDIN=${KDIN:-/tmp/opencode/kdin}
KDOUT=${KDOUT:-/tmp/opencode/kdout.log}

for drive in "$WINEPREFIX"/dosdevices/*:; do
  [ -L "$drive" ] || continue
  if [ "$(readlink -- "$drive")" = / ]; then
    printf 'refusing host-root Wine drive mapping: %s\n' "$drive" >&2
    exit 1
  fi
done

cd "$WINEPREFIX/drive_c"
wine 'C:\dtw\kd.exe' -k 'com:port=\\.\COM1,baud=115200' < "$KDIN" > "$KDOUT" 2>&1
