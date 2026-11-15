#!/usr/bin/env bash
# One home for the question "is a kernel-debugger collector already listening".
#
# Why this matters. kdc.py loops forever on a fixed socket, so a collector left over from an
# earlier session is still connected to /tmp/opencode/kd.sock when the next boot starts, and both
# are then racing to accept the guest. Measured on 2026-11-15: two collectors were alive, one of
# them nineteen hours old, and the older one reached the control run's socket within three seconds
# of the fresh one. That run survived because the newcomer accepted first, which is luck, not a
# design. A run whose KD evidence was written by the wrong collector would be unattributable and
# would still print a full log.
#
# The match is on the argv of each /proc entry, compared as a list, never on a substring of a
# command line: the rig has already been burned once by `pgrep -f`, which matched shells that
# merely mentioned a binary name.
#
# Usage: kdc-running.sh check|pids|stop
set -uo pipefail
MODE=${1:-check}

pids() {
  local dir pid
  for dir in /proc/[0-9]*; do
    pid=${dir#/proc/}
    [ -r "$dir/cmdline" ] || continue
    # argv, NUL-separated: python3 <path>/kdc.py
    mapfile -d '' -t argv < "$dir/cmdline" 2>/dev/null || continue
    [ "${#argv[@]}" -ge 2 ] || continue
    case "${argv[1]}" in
      */kdc.py) printf '%s\n' "$pid" ;;
    esac
  done
}

case "$MODE" in
  pids) pids ;;
  check)
    found=$(pids)
    if [ -n "$found" ]; then
      printf 'collector running: %s\n' "$(printf '%s' "$found" | tr '\n' ' ')"
      exit 0
    fi
    exit 1 ;;
  stop)
    found=$(pids)
    [ -n "$found" ] || { printf 'no collector to stop\n'; exit 0; }
    printf '%s\n' "$found" | xargs -r kill
    sleep 1
    found=$(pids)
    [ -n "$found" ] && { printf '%s\n' "$found" | xargs -r kill -9; sleep 1; }
    pids | grep -q . && { printf 'a collector survives KILL\n' >&2; exit 1; }
    printf 'collector stopped\n'
    ;;
  *) printf 'usage: kdc-running.sh check|pids|stop\n' >&2; exit 2 ;;
esac
