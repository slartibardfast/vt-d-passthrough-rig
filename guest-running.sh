#!/usr/bin/env bash
# One home for the three questions about a live guest: is it running, has it ended, and
# how to stop it.
#
# Why this file exists at all. Six scripts asked these questions themselves, and four of
# them asked by matching a command line with `pgrep -f '[q]emu-system-x86_64'`. That form
# was measured failing here, in both directions. It matches any process whose command line
# merely contains the binary's name, so a shell running a script that mentions the name is
# treated as a running guest: that made golden-build.sh refuse nine tests in one run, a
# refusal with nothing behind it but the caller's own text. And the same idiom aimed at
# `pkill` sends a signal to whatever mentioned the name, which can be the harness that
# invoked the rig. One measured case ended with the probe's own shell killed by its pkill.
#
# The name form is the one that cannot be fooled. A Linux comm field is truncated to 15
# characters, so a live QEMU answers to `qemu-system-x86`, and no amount of a shell
# mentioning the binary produces that name. boot.py already stopped guests this way, so
# this is the rig's existing idiom gathered into one place rather than a new invention.
#
# Usage:
#   guest-running.sh check            exit 0 when a guest is alive, 1 when not
#   guest-running.sh pids             the process ids, one per line
#   guest-running.sh wait SECONDS     exit 0 when it ends, 1 when the timeout passes
#   guest-running.sh stop [GRACE]     TERM, then KILL after the grace period
set -uo pipefail

GUEST_COMM=qemu-system-x86      # the 15-character comm of qemu-system-x86_64
MODE=${1:-check}

# A process that has exited but not been reaped is a zombie, and it keeps its name, so a bare
# pgrep calls it a live guest. A zombie holds no GPU, no disk, and no VFIO group: counting it
# alive would make the loop wait forever on a boot that already ended, and would make the
# guard refuse work for a guest that is not running. The test that found this killed a
# stand-in and the parent never reaped it, which is exactly the shape a child QEMU takes when
# its launcher is not its parent any more.
live_pids() {
  local pid state
  for pid in $(pgrep -x "$GUEST_COMM" 2>/dev/null); do
    state=$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')
    case "$state" in
      Z*|"") continue;;
      *) printf '%s\n' "$pid";;
    esac
  done
}

alive() { [ -n "$(live_pids)" ]; }

case "$MODE" in
  pids)
    live_pids
    ;;
  check)
    if alive; then
      printf 'guest running: %s\n' "$(live_pids | tr '\n' ' ')"
      exit 0
    fi
    exit 1
    ;;
  wait)
    TIMEOUT=${2:-}
    case "$TIMEOUT" in ''|*[!0-9]*) printf 'wait needs a whole-number timeout in seconds\n' >&2; exit 2;; esac
    START=$(date -u +%s)
    while alive; do
      if [ $(( $(date -u +%s) - START )) -gt "$TIMEOUT" ]; then
        printf 'guest still alive after %ss\n' "$TIMEOUT"
        exit 1
      fi
      sleep 5
    done
    exit 0
    ;;
  stop)
    GRACE=${2:-20}
    alive || { printf 'no guest to stop\n'; exit 0; }
    pkill -TERM -x "$GUEST_COMM" || true
    WAITED=0
    while alive && [ "$WAITED" -lt "$GRACE" ]; do
      sleep 2; WAITED=$((WAITED + 2))
    done
    if alive; then
      printf 'the guest ignored TERM for %ss; sending KILL\n' "$GRACE"
      pkill -KILL -x "$GUEST_COMM" || true
      sleep 2
    fi
    alive && { printf 'a guest survives KILL; this is not a rig problem\n' >&2; exit 1; }
    printf 'guest stopped\n'
    ;;
  *)
    printf 'usage: guest-running.sh check|pids|wait SECONDS|stop [GRACE]\n' >&2
    exit 2
    ;;
esac
