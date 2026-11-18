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
#                          [--runs DIR] [--timeout SECONDS] [--hold SECONDS] [--no-bootlog]
#                          [--display-dll FILE] [--dest DIR] [--force]
#                          [--prepare-only] [--dry-run]
#        run-experiment.sh --list-evidence <mounted-system-volume>
#
# --hold is how a run ends. Nothing inside the guest closes it: the driver-install batch on
# the channel REBOOTS, so a runner that waited for a self-shutdown would wait until its
# timeout and record a crash that never happened. The host therefore holds the session open
# long enough to reach a desktop and capture a frame, asks the machine to close through the
# monitor (system_powerdown, an ACPI request, not a reset), and then waits for QEMU to exit
# on its own so the writeback cache and the hive logs land clean. --no-bootlog skips the
# loader flag; it exists for a synthetic image with no system volume, not for a real run.
#
# --display-dll installs one user-mode DLL into the overlay's system32. The registry has named
# `ati2dvag` as the display driver for every boot of this campaign, and the file has never been on the
# OS volume, so win32k opens the path, fails, and takes `vga.dll` instead. A run that is meant to
# settle the display question has to put the file there first, and it has to say so in its own record.
# The copy goes into the overlay and never into the golden, so the write stays disposable and every
# earlier run's cited `golden_sha256` keeps resolving.
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
GOLDEN="" CHANNEL="" NAME="" RUNS="$RIG/work/runs" TIMEOUT=600 PREPARE="" DRY="" TREE=""
HOLD=240 NO_BOOTLOG="" DEST="" FORCE="" DISPLAY_DLL="" CLASS_BINDING="" CLASS_KEY="" DISPLAY_MODE="" DISPLAY_PACKAGE="" DISPLAY_INF=""

while [ $# -gt 0 ]; do
  case "$1" in
    --golden) GOLDEN=${2:-}; shift 2;;
    --channel) CHANNEL=${2:-}; shift 2;;
    --name) NAME=${2:-}; shift 2;;
    --runs) RUNS=${2:-}; shift 2;;
    --timeout) TIMEOUT=${2:-}; shift 2;;
    --hold) HOLD=${2:-}; shift 2;;
    --dest) DEST=${2:-}; shift 2;;
    --force) FORCE=1; shift;;
    --display-dll) DISPLAY_DLL=${2:-}; shift 2;;
    --class-binding) CLASS_BINDING=${2:-}; shift 2;;
    --class-key) CLASS_KEY=${2:-}; shift 2;;
    --display-mode) DISPLAY_MODE=${2:-}; shift 2;;
    --display-package) DISPLAY_PACKAGE=${2:-}; shift 2;;
    --display-inf) DISPLAY_INF=${2:-}; shift 2;;
    --no-bootlog) NO_BOOTLOG=1; shift;;
    --prepare-only) PREPARE=1; shift;;
    --dry-run) DRY=1; shift;;
    --list-evidence) TREE=${2:-}; shift 2;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done

# The files a run owes, in one place. The volatile video device map is here because
# HARDWARE is built at boot and exists on no disk: an offline read of an overlay cannot
# supply it, so the guest writes it during the run and the host only ever collects it.
EVIDENCE=( "WINDOWS/capture.txt" "WINDOWS/devicemap.txt" "WINDOWS/devnode.txt"
           "WINDOWS/videoctl.txt" "WINDOWS/enum-pci.txt" "WINDOWS/svc-ati2mtag.txt"
           "WINDOWS/setupapi.log" "WINDOWS/runid.txt" "letters-hook.txt" "WINDOWS/ntbtlog.txt"
           "WINDOWS/ati-files.txt" "WINDOWS/enum-display.txt" "WINDOWS/profile-video.txt"
           "WINDOWS/SysEvent.Evt" "WINDOWS/system32/config/system" )

if [ -n "$TREE" ]; then
  for rel in "${EVIDENCE[@]}"; do
    if [ -f "$TREE/$rel" ]; then
      printf '%s\tpresent\n' "$rel"
    else
      printf '%s\tabsent (the guest writes it during the run)\n' "$rel"
    fi
  done
  exit 0
fi

# A missing tool is named here, before anything else runs. The first time this script met a
# runner without qemu-utils it went on to `qemu-img info`, took an empty string as JSON, and
# failed as a traceback about a golden that was never built.
for need in qemu-img python3 realpath date; do
  command -v "$need" >/dev/null || {
    printf '%s is required; on Ubuntu and Debian %s comes from the package %s\n' "$need" \
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
# Both paths become absolute here, and that is not cosmetic. `qemu-img create -b` resolves a
# relative backing path against the directory of the image being created, not the working
# directory, so a golden given as work/goldens/os.qcow2 was looked for inside the run directory
# and the first real control run died with "Could not open backing image" on a file that was
# plainly there. The comparison below needs absolute paths for the same reason.
GOLDEN=$(realpath "$GOLDEN")
CHANNEL=$(realpath "$CHANNEL")

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
NONCE="$NAME-$STAMP"
# A variant task's verify command names a stable directory (`work/runs/descriptor`), because a table
# that has to be re-derivable cannot cite a path whose name carries a clock. --dest places the run
# there. The nonce is not changed by it: the run still signs its captures with name-and-stamp, so
# two boots into one directory can never be confused for one another, and a second boot is refused
# until the record it would overwrite is deliberately replaced.
if [ -n "$DISPLAY_DLL" ]; then
  case "$DISPLAY_DLL" in /*) ;; *) DISPLAY_DLL="$RIG/$DISPLAY_DLL";; esac
  test -f "$DISPLAY_DLL" || { printf 'no display DLL to install at %s\n' "$DISPLAY_DLL" >&2; exit 2; }
fi

if [ -n "$CLASS_BINDING" ]; then
  case "$CLASS_BINDING" in /*) ;; *) CLASS_BINDING="$RIG/$CLASS_BINDING";; esac
  test -f "$CLASS_BINDING" || { printf 'no INF to bind the class node from at %s\n' "$CLASS_BINDING" >&2
                               exit 2; }
fi

if [ -n "$DEST" ]; then
  RUN="$DEST"
  case "$RUN" in
    /*) ;;
    *) RUN="$RIG/$RUN";;
  esac
  if [ -e "$RUN/run.json" ] && [ -z "$FORCE" ]; then
    printf '%s already holds a run; a second boot there would overwrite the record\n' "$RUN" >&2
    printf 'its nonce is %s. Pass --force to replace it, or let the runner name the directory\n' \
      "$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('nonce','?'))" \
        "$RUN/run.json" 2>/dev/null || printf 'unreadable')" >&2
    exit 2
  fi
else
  RUN="$RUNS/$NONCE"
fi
OVERLAY="$RUN/overlay.qcow2"
RUN_CHANNEL="$RUN/gpudrv.img"
RUN_JSON="$RUN/run.json"
# Every per-run artifact lives in the run directory. Three of them were fixed paths before, which
# meant a second run overwrote the first: the firmware log (`/tmp/opencode/seabios.log`), the KD
# socket the collector listens on, and the collector's own module log. A fixed KD socket is worse
# than a lost log, because kdc.py loops forever on it: a collector left over from an earlier boot
# races the fresh one to accept the guest, and the run cannot tell which stream it is reading.
# The socket stays short and lives beside the run rather than inside it: an AF_UNIX path is capped
# at 108 bytes, and the run directory name is already 106 of them. A run of this loop died at once
# with "UNIX socket path ... is too long" when the socket was placed where the rule said it should
# go, which is the shape of a good rule applied past a limit nobody had written down.
KD_SOCK="/tmp/opencode/kd-$NONCE.sock"
SEABIOS_LOG="$RUN/seabios.log"
# kdc.py appends to its catch log only when it catches something, so a quiet boot would leave no
# file at all and a grader would have to guess whether nothing was caught or nothing was collected.
# An empty file is the answer to that question, written down. It is created inside prepare, because
# the run directory does not exist before prepare makes it.
COLLECTOR_CATCHES="$RUN/captor-catches.log"

if [ "${#KD_SOCK}" -gt 100 ]; then
  printf 'the KD socket path is %s bytes and the kernel caps AF_UNIX at 108: %s\n' \
    "${#KD_SOCK}" "$KD_SOCK" >&2
  printf 'shorten --runs or the run name; QEMU will refuse to start otherwise\n' >&2
  exit 2
fi

prepare() {
  mkdir -p "$RUN"
  touch "$COLLECTOR_CATCHES" || return 2
  qemu-img create -f qcow2 -b "$GOLDEN" -F qcow2 "$OVERLAY" >/dev/null || return 2
  cp "$CHANNEL" "$RUN_CHANNEL" || return 2
  # The manifest travels with the image. A run that cites a manifest by absolute path is citing a
  # file that can be edited or removed later, and its record then cannot be re-read; a copy inside
  # the run directory is the same bytes forever, and the run is what a later session has.
  if [ -f "${CHANNEL%.img}.json" ]; then
    cp "${CHANNEL%.img}.json" "$RUN/channel-manifest.json" || return 2
  fi
  python3 - "$RUN_JSON" "$GOLDEN" "$OVERLAY" "$RUN_CHANNEL" "$NONCE" "$NAME" "$TIMEOUT" \
    "$CHANNEL" <<'PY'
import datetime, hashlib, json, os, sys
out, golden, overlay, channel, nonce, name, timeout, channel_source = sys.argv[1:9]
data = {
    "name": name,
    "nonce": nonce,
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "golden": os.path.abspath(golden),
    "golden_sha256": hashlib.sha256(open(golden, "rb").read()).hexdigest(),
    "overlay": os.path.abspath(overlay),
    "channel": os.path.abspath(channel),
    "channel_source": os.path.abspath(channel_source),
    "channel_sha256": hashlib.sha256(open(channel, "rb").read()).hexdigest(),
    "timeout_seconds": int(timeout),
    "outcome": "prepared",
}
# A channel image built by channel-image.sh has a manifest beside it naming the candidate driver it
# holds. Carrying that hash into the run record is what lets `delta-verify.sh` say this boot ran the
# file that build claimed, rather than whatever file happened to be on the volume. A manifest that
# names no candidate is a defect in the build, so it stops the run rather than passing quietly.
channel_manifest = os.path.splitext(os.path.abspath(channel_source))[0] + ".json"
if os.path.isfile(channel_manifest):
    try:
        record = json.load(open(channel_manifest))
    except json.JSONDecodeError as exc:
        raise SystemExit("the channel manifest %s does not parse: %s" % (channel_manifest, exc))
    if "candidate_sha256" not in record:
        raise SystemExit("%s names no candidate_sha256, so this boot cannot be bound to a build"
                         % channel_manifest)
    for key in ("candidate_sha256", "variant", "ops_manifest", "rollback_sha256"):
        if key in record:
            data[key] = record[key]
    data["channel_manifest"] = channel_manifest
    if os.path.isfile(os.path.join(os.path.dirname(os.path.abspath(channel)), "channel-manifest.json")):
        data["channel_manifest_copy"] = os.path.join(
            os.path.dirname(os.path.abspath(channel)), "channel-manifest.json")
        data["channel_manifest_sha256"] = hashlib.sha256(
            open(channel_manifest, "rb").read()).hexdigest()
json.dump(data, open(out, "w"), indent=2, sort_keys=True)
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

# The collector is single-occupancy on its socket, so a stale one is refused for the same reason a
# second QEMU is: not because it is untidy, but because the run's evidence would come from one of
# two possible writers.
if "$RIG/kdc-running.sh" check >/dev/null; then
  printf 'a kernel-debugger collector is already running: %s\n' \
    "$("$RIG/kdc-running.sh" pids | tr '\n' ' ')" >&2
  printf 'it would race this boot for the KD socket. Run: ./kdc-running.sh stop\n' >&2
  exit 2
fi

prepare || { printf 'prepare failed; nothing was booted\n' >&2; exit 2; }
printf 'run directory: %s\n' "$RUN"
printf 'nonce: %s\n' "$NONCE"

printf 'plan: boot the overlay, never the golden\n'
printf 'plan: overlay %s backs onto %s\n' "$OVERLAY" "$GOLDEN"
printf 'plan: channel copy %s\n' "$RUN_CHANNEL"
KD_SOCK="$KD_SOCK" SEABIOS_LOG="$SEABIOS_LOG" \
PRINT_ONLY=1 WORK="$OVERLAY" CHANNEL="$RUN_CHANNEL" FORMAT=qcow2 CACHE=writeback GLINET=none \
  LOGDIR="$RUN" LOG="$RUN/driver-stage.log" "$RIG/run-driver.sh" || { printf 'the launcher refused the plan\n' >&2; exit 2; }

if [ -n "$DRY" ]; then
  printf 'plan: the overlay is given /bootlog, the Startup trigger, and the nonce, unless --no-bootlog\n'
  [ -z "$DISPLAY_PACKAGE" ] || printf 'plan: the overlay is given the package at %s, by the INF list\n' \
    "$DISPLAY_PACKAGE"
  [ -z "$CLASS_KEY" ] || printf 'plan: the class binding rewrites node %s in place\n' "$CLASS_KEY"
  [ -z "$DISPLAY_MODE" ] || printf 'plan: the overlay asks the attached display for %s\n' \
    "$DISPLAY_MODE"
  [ -z "$DISPLAY_DLL" ] || printf 'plan: the overlay is given %s in system32\n' \
    "$(basename "$DISPLAY_DLL")"
  [ -z "$CLASS_BINDING" ] || printf 'plan: the class node on the overlay volume is bound from %s\n' \
    "$(basename "$CLASS_BINDING")"
  printf 'plan: the run is held %ss, then closed through the monitor, so the hives flush\n' "$HOLD"
  printf 'plan: evidence is read back from a read-only nbd attachment with its timestamps\n'
  printf 'dry run: prepared and planned, and no QEMU was started\n'
  exit 0
fi
if [ -n "$PREPARE" ]; then
  printf 'prepared only: no boot was started\n'
  exit 0
fi

if [ -z "$NO_BOOTLOG" ] || [ -n "$DISPLAY_DLL" ] || [ -n "$CLASS_BINDING" ]; then
  # Three settings, one image, one proof pass. The loader flag makes ntbtlog.txt exist; the trigger
  # is what the guest runs at logon to answer the questions no offline read can ask; and the nonce
  # is what lets the grader tell a file this run wrote from a file the golden handed over. Those
  # last two were the whole of one failed control: the golden carried a November-14 trigger, so the
  # captures L graded were inherited or missing, and nothing could say which.
  SETTINGS=(--image "$OVERLAY" --record "$RUN")
  if [ -z "$NO_BOOTLOG" ]; then
    SETTINGS+=(--bootlog --startup-hook "$RIG/install-channel/startup-hook.bat" --runid "$NONCE")
  fi
  [ -z "$DISPLAY_DLL" ] || SETTINGS+=(--display-dll "$DISPLAY_DLL")
  [ -z "$CLASS_BINDING" ] || SETTINGS+=(--class-binding "$CLASS_BINDING")
  [ -z "$CLASS_KEY" ] || SETTINGS+=(--class-key "$CLASS_KEY")
  [ -z "$DISPLAY_MODE" ] || SETTINGS+=(--display-mode "$DISPLAY_MODE")
  [ -z "$DISPLAY_PACKAGE" ] || SETTINGS+=(--display-package "$DISPLAY_PACKAGE")
  [ -z "$DISPLAY_INF" ] || SETTINGS+=(--display-inf "$DISPLAY_INF")
  printf 'setting: the overlay is configured by golden-settings.py, which proves each setting from a second mount\n'
  "$RIG/golden-settings.py" "${SETTINGS[@]}" || {
      printf 'the overlay could not be configured; refusing to boot a run that records nothing\n' >&2
      exit 2; }
  if [ -z "$NO_BOOTLOG" ]; then
    amend "startup_hook_sha256=$(sha256sum "$RIG/install-channel/startup-hook.bat" | cut -d' ' -f1)"
  fi
  if [ -n "$DISPLAY_DLL" ]; then
    amend "display_dll=$(basename "$DISPLAY_DLL")" \
      "display_dll_sha256=$(sha256sum "$DISPLAY_DLL" | cut -d' ' -f1)"
  fi
  if [ -n "$CLASS_BINDING" ]; then
    amend "class_binding_inf=$(basename "$CLASS_BINDING")" \
      "class_binding_sha256=$(sha256sum "$CLASS_BINDING" | cut -d' ' -f1)"
  fi
fi

KD_SOCK="$KD_SOCK" SEABIOS_LOG="$SEABIOS_LOG" KDC_LOG="$RUN/kdc-live.log" \
  KDC_MODULES="$RUN/modules.txt" KDC_CATCHES="$COLLECTOR_CATCHES" \
  WORK="$OVERLAY" CHANNEL="$RUN_CHANNEL" FORMAT=qcow2 CACHE=writeback GLINET=none \
  LOGDIR="$RUN" LOG="$RUN/driver-stage.log" "$RIG/start-boot-detached.sh" || {
    printf 'the launcher refused to start; the overlay and the channel copy stay for inspection\n' >&2
    exit 2; }

amend "started=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "outcome=booting"

# The measurement window. A frame is attempted and, on this argv, always refused: there is no
# display device attached, so QEMU answers that it has no console to dump. That answer is now
# surfaced rather than swallowed, and load-verify.py reports the frame claim as unattempted rather
# than passing or failing it. What does bear on the display question is the module stream plus the
# captures the guest writes, and both are collected.
printf 'hold: %ss before the run is closed\n' "$HOLD"
# MIN_LIVE is what stops "the process is gone" from being recorded as "the guest shut itself down".
# A boot that dies in ten seconds, for any reason at all, has told the rig nothing, and labelling it
# a measured shutdown hands the grader a clean outcome for a run that never reached a session.
MIN_LIVE=120
printf 'live: the guest must last %ss before its own exit counts as a shutdown\n' "$MIN_LIVE"
ELAPSED=0
ENDED_EARLY=""
while [ "$ELAPSED" -lt "$HOLD" ]; do
  sleep 10
  ELAPSED=$((ELAPSED + 10))
  if ! "$RIG/guest-running.sh" check >/dev/null; then
    if [ "$ELAPSED" -lt "$MIN_LIVE" ]; then
      ENDED_EARLY=1
      printf 'the guest vanished at %ss, before %ss: that is not a shutdown\n' "$ELAPSED" "$MIN_LIVE"
      printf 'the launcher said:\n'
      tail -6 "$RUN/run-driver-detached.log" 2>/dev/null | sed 's/^/  /'
    else
      printf 'the guest ended its own run at %ss\n' "$ELAPSED"
    fi
    break
  fi
  if [ "$((ELAPSED % 60))" -eq 0 ]; then
    if /usr/bin/python3 "$RIG/monq.py" "screendump $RUN/feed-$ELAPSED.ppm" >"$RUN/feed-$ELAPSED.out" 2>&1 \
       && [ -f "$RUN/feed-$ELAPSED.ppm" ]; then
      printf 'feed: %s written\n' "$RUN/feed-$ELAPSED.ppm"
    else
      printf 'feed: none at %ss (%s)\n' "$ELAPSED" \
        "$(grep -m1 -iE 'no console|error|cannot' "$RUN/feed-$ELAPSED.out" 2>/dev/null | tr -s ' ' | cut -c1-70)"
    fi
  fi
done
if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'close: asking the machine to shut down through the monitor\n'
  /usr/bin/python3 "$RIG/monq.py" system_powerdown >"$RUN/powerdown.out" 2>&1 || {
    printf 'the monitor refused the close request; the timeout will end the run instead\n' >&2
    cat "$RUN/powerdown.out" >&2; }
fi

# Whether the guest ended its own run, and how to stop one that will not, belong to
# guest-running.sh, so the rig holds one answer rather than a per-script copy.
if [ -n "$ENDED_EARLY" ]; then
  OUTCOME=ended-early
elif "$RIG/guest-running.sh" wait "$TIMEOUT"; then
  OUTCOME=measured-shutdown
else
  printf 'timeout after %ss: a guest that bugchecked cannot end its own run\n' "$TIMEOUT"
  "$RIG/guest-running.sh" stop
  OUTCOME=timeout-killed
fi
amend "ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)" "outcome=$OUTCOME" "lived_seconds=$ELAPSED" \
  "crash_suspected=$([ "$OUTCOME" = timeout-killed ] || [ "$OUTCOME" = ended-early ] && echo true || echo false)"

# The collector has nothing left to read once the guest is gone, and leaving it alive poisons the
# next run for the reason this script refuses one at the start.
"$RIG/kdc-running.sh" stop || true

# A bugcheck is a crash even when the machine later shuts down cleanly. The contract used to infer
# crashes from how the run ended, so two boots whose guests double-faulted inside the display driver
# and rebooted before the hold expired read as `measured-shutdown` with `crash_suspected=false`, while
# their own collector logs held `*** Fatal System Error`. The kernel's last words on the debugger wire
# decide it now, and the signature is recorded beside the flag so a reader can check it.
if CRASH=$("$RIG/crash-signature.py" "$RUN/kdc-detached.log" 2>/dev/null); then
  amend "crash_suspected=true" "crash_signature=$CRASH"
  printf 'crash: the guest crashed: %s\n' "$CRASH"
fi

# The read-back. The overlay is the run's record, so it is attached read-only, on a device that is
# free, with the format named rather than probed: reading evidence out of an image must not be able
# to change it. Timestamps are preserved, because "is this file from this boot or inherited from
# the golden" is a question answered by mtime, and it is the question that cost this campaign a
# diagnosis.
nbd_free() {
  local dev
  for dev in $(seq 0 15); do
    [ -b "/dev/nbd$dev" ] || continue
    if ! sudo fuser -s "/dev/nbd$dev" 2>/dev/null; then
      printf '/dev/nbd%s' "$dev"
      return 0
    fi
  done
  return 1
}

collect() {
  local M=/tmp/opencode/experiment-evidence NBD DEV
  NBD=$(nbd_free) || { printf 'every nbd device is busy\n' >&2; return 1; }
  sudo modprobe nbd max_part=8 || return 1
  sudo qemu-nbd -r -f qcow2 -c "$NBD" "$OVERLAY" || return 1
  DEV="$NBD"
  sudo mkdir -p "$M"
  if ! sudo mount -o ro "${NBD}p1" "$M"; then
    printf 'the overlay has no first partition to read evidence from\n' >&2
    sudo qemu-nbd -d "$DEV" || true
    return 1
  fi
  TREE="$M" "$0" --list-evidence "$M" | tee "$RUN/evidence-list.txt"
  mkdir -p "$RUN/evidence"
  : > "$RUN/evidence/MANIFEST.tsv"
  printf 'path\tsha256\tmtime_utc\n' >> "$RUN/evidence/MANIFEST.tsv"
  while read -r rel state _; do
    printf 'evidence: %s %s\n' "$rel" "$state"
    [ "$state" = present ] || continue
    [ -f "$M/$rel" ] || continue
    mkdir -p "$RUN/evidence/$(dirname "$rel")"
    sudo cp -p "$M/$rel" "$RUN/evidence/$rel"
    sudo chown "$(id -u):$(id -g)" "$RUN/evidence/$rel"
    printf '%s\t%s\t%s\n' "$rel" \
      "$(sha256sum "$RUN/evidence/$rel" | cut -d' ' -f1)" \
      "$(date -u -r "$RUN/evidence/$rel" +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN/evidence/MANIFEST.tsv"
    printf 'collected %s\n' "$rel"
  done < "$RUN/evidence-list.txt"
  sync
  sudo umount "$M"
  sudo qemu-nbd -d "$DEV"
  amend "evidence_manifest=$RUN/evidence/MANIFEST.tsv"
}

if collect; then
  amend "evidence=collected"
else
  printf 'evidence collection failed; the overlay is kept for inspection\n' >&2
  amend "evidence=collection-failed"
fi

printf 'run directory: %s\n' "$RUN"
printf 'outcome: %s\n' "$OUTCOME"

# The run's own contract, derived from the evidence this boot left behind. It is a file rather than
# a heredoc because every field in it is a claim, and a claim a test cannot call is a claim no later
# session can re-derive. `pp_verdict.py`, the campaign's strict PowerPlay grader, refuses to answer
# unless a run record carries eight named fields, which is exactly the shape of temptation that turns
# into typing constants and moving on.
python3 "$RIG/run_contract.py" "$RUN_JSON" "$RUN" "$NONCE" "$OUTCOME" ||
  printf 'the run could not write its own contract; its record is incomplete\n' >&2

printf 'grade it: ./load-verify.py --run %s\n' "$RUN"
