#!/usr/bin/env bash
# One experiment: a fresh overlay on a read-only golden, a private copy of the channel, one
# boot through the launcher, and the evidence read back off the overlay after the guest ends
# the run.
#
# Why this shape. call/0010 puts the layering at the disk because QEMU refuses machine
# snapshots while a VFIO device is assigned, and call/0011 puts the variant on the channel
# because the OS image is identical across variants. The two together mean a run is: golden
# read-only, overlay disposable, channel copied, one boot, no install, no OS copy. The guest
# ends the run with its own shutdown, so the host waits on QEMU exiting instead of polling a
# FAT volume that can lag a minute behind the guest.
#
# What it refuses, and why each refusal exists:
#   - a running QEMU, because two guests on this card and disk pair produce evidence L cannot
#     attribute to either;
#   - a golden handed to it as the thing to boot, because the golden is shared read-only state
#     and writing to it destroys every later run's parent;
#   - a channel that is the golden, because a variant that shares its mutable artifact with
#     read-only state is not a variant;
#   - a raw disk as the golden, because a qcow2 overlay cannot back onto a raw file, and a
#     runner that "helpfully" converted one would be silently making a second golden.
#
# It never names a QEMU command line of its own. The plan it prints comes from run-driver.sh
# with PRINT_ONLY=1, so a dry run cannot drift from a real boot, and the boot itself goes
# through start-boot-detached.sh, which is the only launcher that survives the caller's
# process group.
#
# Usage: run-experiment.sh --golden <qcow2> --channel <img> --name NAME
#                          [--runs DIR] [--timeout SECONDS] [--prepare-only] [--dry-run]
#        run-experiment.sh --list-evidence <mounted-system-volume>
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
GOLDEN="" CHANNEL="" NAME="" RUNS="$RIG/work/runs" TIMEOUT=600 PREPARE="" DRY="" TREE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --golden) GOLDEN=${2:-}; shift 2;;
    --channel) CHANNEL=${2:-}; shift 2;;
    --name) NAME=${2:-}; shift 2;;
    --runs) RUNS=${2:-}; shift 2;;
    --timeout) TIMEOUT=${2:-}; shift 2;;
    --prepare-only) PREPARE=1; shift;;
    --dry-run) DRY=1; shift;;
    --list-evidence) TREE=${2:-}; shift 2;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done

# The files a run owes, in one place. The volatile video device map is here because
# HARDWARE is built at boot and exists on no disk: an offline read of an overlay cannot
# supply it, so the guest writes it during the run and the host only ever collects it.
EVIDENCE=( "WINDOWS/devicemap.txt" "letters-hook.txt" )

if [ -n "$TREE" ]; then
  for rel in "${EVIDENCE[@]}"; do
    if [ -f "$TREE/$rel" ]; then
      printf 'evidence: %s present\n' "$rel"
    else
      printf 'evidence: %s absent (the guest writes it during the run)\n' "$rel"
    fi
  done
  exit 0
fi

# A missing tool is named here, before anything else runs. The first time this script met a
# runner without qemu-utils it went on to `qemu-img info`, took an empty string as JSON, and
# failed as a traceback about a golden that was never built.
for need in qemu-img python3 realpath date; do
  command -v "$need" >/dev/null || {
    printf '%s is required; on Ubuntu and Debian %s comes from the package %s
' "$need" "$need" \
      "$([ "$need" = qemu-img ] && echo qemu-utils || echo coreutils)" >&2
    exit 2; }
done

[ -n "$GOLDEN" ] && [ -n "$CHANNEL" ] && [ -n "$NAME" ] || {
  printf 'usage: run-experiment.sh --golden <qcow2> --channel <img> --name NAME [--timeout S] [--prepare-only] [--dry-run]\n' >&2
  exit 2; }
# The guest check comes from guest-running.sh, which holds the rule and the reason for it:
# a command-line match was measured catching shells that only mention the binary.
if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'a QEMU is already running; one guest at a time on this GPU and disk pair\n' >&2
  exit 2
fi
test -f "$GOLDEN" || { printf 'no golden: %s\n' "$GOLDEN" >&2; exit 2; }
test -f "$CHANNEL" || { printf 'no channel image: %s\n' "$CHANNEL" >&2; exit 2; }

GOLDEN_INFO=$(qemu-img info --output=json "$GOLDEN")
GOLDEN_FMT=$(printf '%s' "$GOLDEN_INFO" | python3 -c 'import json,sys; print(json.load(sys.stdin)["format"])')
if [ "$GOLDEN_FMT" != "qcow2" ]; then
  printf 'the golden must be qcow2 so an overlay can back onto it; %s is %s\n' "$GOLDEN" "$GOLDEN_FMT" >&2
  exit 2
fi
if [ "$(realpath "$CHANNEL")" = "$(realpath "$GOLDEN")" ]; then
  printf 'the channel image must not be the golden: the golden is read-only shared state\n' >&2
  exit 2
fi

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN="$RUNS/$NAME-$STAMP"
OVERLAY="$RUN/overlay.qcow2"
RUN_CHANNEL="$RUN/gpudrv.img"
RUN_JSON="$RUN/run.json"

prepare() {
  mkdir -p "$RUN"
  qemu-img create -f qcow2 -b "$GOLDEN" -F qcow2 "$OVERLAY" >/dev/null || return 2
  cp "$CHANNEL" "$RUN_CHANNEL" || return 2
  python3 - "$RUN_JSON" "$GOLDEN" "$OVERLAY" "$RUN_CHANNEL" "$NAME" "$TIMEOUT" <<'PY'
import datetime, hashlib, json, os, sys
out, golden, overlay, channel, name, timeout = sys.argv[1:7]
json.dump({
    "name": name,
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "golden": os.path.abspath(golden),
    "golden_sha256": hashlib.sha256(open(golden, "rb").read()).hexdigest(),
    "overlay": os.path.abspath(overlay),
    "channel": os.path.abspath(channel),
    "channel_sha256": hashlib.sha256(open(channel, "rb").read()).hexdigest(),
    "timeout_seconds": int(timeout),
    "outcome": "prepared",
}, open(out, "w"), indent=2, sort_keys=True)
PY
}

amend() {
  python3 - "$RUN_JSON" "$@" <<'PY'
import json, sys
path, *pairs = sys.argv[1:]
data = json.load(open(path))
for pair in pairs:
    key, _, value = pair.partition("=")
    try:
        data[key] = json.loads(value)
    except json.JSONDecodeError:
        data[key] = value
json.dump(data, open(path, "w"), indent=2, sort_keys=True)
PY
}

prepare || { printf 'prepare failed; nothing was booted\n' >&2; exit 2; }
printf 'run directory: %s\n' "$RUN"

printf 'plan: boot the overlay, never the golden\n'
printf 'plan: overlay %s backs onto %s\n' "$OVERLAY" "$GOLDEN"
printf 'plan: channel copy %s\n' "$RUN_CHANNEL"
PRINT_ONLY=1 WORK="$OVERLAY" CHANNEL="$RUN_CHANNEL" FORMAT=qcow2 CACHE=writeback GLINET=none \
  LOG="$RUN/driver-stage.log" "$RIG/run-driver.sh" || { printf 'the launcher refused the plan\n' >&2; exit 2; }

if [ -n "$DRY" ]; then
  printf 'dry run: prepared and planned, and no QEMU was started\n'
  exit 0
fi
if [ -n "$PREPARE" ]; then
  printf 'prepared only: no boot was started\n'
  exit 0
fi

WORK="$OVERLAY" CHANNEL="$RUN_CHANNEL" FORMAT=qcow2 CACHE=writeback GLINET=none \
  LOG="$RUN/driver-stage.log" "$RIG/start-boot-detached.sh" || {
    printf 'the launcher refused to start; the overlay and the channel copy stay for inspection\n' >&2
    exit 2; }

amend "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "outcome=booting"
# Whether the guest ended its own run, and how to stop one that will not, belong to
# guest-running.sh, so the rig holds one answer rather than a per-script copy.
if "$RIG/guest-running.sh" wait "$TIMEOUT"; then
  OUTCOME=clean-shutdown
else
  printf 'timeout after %ss: a guest that bugchecked cannot end its own run\n' "$TIMEOUT"
  "$RIG/guest-running.sh" stop
  OUTCOME=timeout-killed
fi
amend "ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "outcome=$OUTCOME" "crash_suspected=$([ "$OUTCOME" = timeout-killed ] && echo true || echo false)"

# The read-back: the overlay holds everything the guest wrote, and nothing else needs the
# disk. qemu-nbd exposes it as a block device so the system volume can be mounted read-only.
collect() {
  local M=/tmp/opencode/experiment-evidence
  sudo modprobe nbd max_part=8 || return 1
  sudo qemu-nbd -c /dev/nbd0 "$OVERLAY" || return 1
  sudo mkdir -p "$M"
  if ! sudo mount -o ro /dev/nbd0p1 "$M"; then
    printf 'the overlay has no first partition to read evidence from\n' >&2
    sudo qemu-nbd -d /dev/nbd0 || true
    return 1
  fi
  TREE="$M" "$0" --list-evidence "$M" | tee "$RUN/evidence-list.txt"
  mkdir -p "$RUN/evidence"
  while read -r _ _ rel _; do
    [ -f "$M/$rel" ] || continue
    mkdir -p "$RUN/evidence/$(dirname "$rel")"
    sudo cp "$M/$rel" "$RUN/evidence/$rel"
    sudo chown "$(id -u):$(id -g)" "$RUN/evidence/$rel"
    printf 'collected %s\n' "$rel"
  done < "$RUN/evidence-list.txt"
  sync
  sudo umount "$M"
  sudo qemu-nbd -d /dev/nbd0
}

if collect; then
  amend "evidence=collected"
else
  printf 'evidence collection failed; the overlay is kept for inspection\n' >&2
  amend "evidence=collection-failed"
fi

printf 'run directory: %s\n' "$RUN"
printf 'outcome: %s\n' "$OUTCOME"
