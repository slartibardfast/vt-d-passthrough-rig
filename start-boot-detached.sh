#!/usr/bin/env bash
# Start the canonical descriptor instrument boot DETACHED from the caller's
# process group.
#
# Why this exists: a boot launched as a plain background child of a tool call
# is killed when that call is reaped (a harness timeout sends SIGTERM to the
# whole process group), so an instrument boot that took four minutes to reach
# the logon screen died mid-measurement with nothing to show for it. setsid
# puts the launcher and its QEMU in their own session, outside the caller's
# group, so the boot outlives the call that started it.
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
LOGDIR=${LOGDIR:-/tmp/opencode}
DISK=${WORK:-/home/dconnolly/xp64-rig/xp64-gpu.raw}

test -b "$DISK" 2>/dev/null || test -f "$DISK" || { printf 'no such disk: %s\n' "$DISK"; exit 2; }
if pgrep -f '[q]emu-system-x86_64' >/dev/null; then
  printf 'a QEMU is already running; refusing to start a second one on this GPU\n'
  exit 2
fi
rm -f "$RIG/mon.sock"
setsid nohup python3 "$RIG/kdc.py" >"$LOGDIR/kdc-detached.log" 2>&1 < /dev/null &
printf 'kdc pid %s\n' "$!"
sleep 1
setsid nohup env WORK="$DISK" "$RIG/run-driver.sh" >"$LOGDIR/run-driver-detached.log" 2>&1 < /dev/null &
printf 'launcher pid %s\n' "$!"
printf 'logs: %s/run-driver-detached.log %s/kdc-detached.log\n' "$LOGDIR" "$LOGDIR"
