#!/usr/bin/env bash
# Build one variant's complete channel image from the base channel and a graft manifest.
#
# Why this exists. The variant lives on the channel, per call/0011, so a comparison between two
# grafts has to move exactly one file and nothing else. Doing that by hand on a mounted FAT volume
# is how two variants end up sharing mutable state, or how a deployment gets recorded against a hash
# nobody re-read. This builds a whole image per variant, from the base channel it copies, and it
# writes the record `delta-verify.sh` already expects before it lets the image go.
#
# What it guarantees, in order:
#
#   - the base channel is copied, never edited, so the control's channel stays byte-identical;
#   - the candidate is built by graft-check from the base binary and the variant's op manifest, and
#     its digest is required to equal the pre-declared expectation, so a variant that drifted is
#     caught at build time rather than after a boot;
#   - the deployed file is read back out of the finished image and hashed there, so the record says
#     what the guest will actually open rather than what the build intended;
#   - the previous occupant of drv/ati2mtag.sys is kept as the rollback and its hash recorded,
#     because a deployment you cannot undo is not a deployment you control.
#
# Nothing here boots. A machine is not started by a build.
#
# Usage: channel-image.sh --variant descriptor|dispatch|smc [--run RUN_DIR] [--out IMAGE]
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
. "$RIG/delta-paths.sh"

VARIANT="" RUN="" OUT="" BASE_CHANNEL="$RIG/gpudrv.img"

while [ $# -gt 0 ]; do
  case "$1" in
    --variant) VARIANT=${2:-}; shift 2;;
    --run) RUN=${2:-}; shift 2;;
    --out) OUT=${2:-}; shift 2;;
    --base-channel) BASE_CHANNEL=${2:-}; shift 2;;
    --mount) MOUNT_POINT=${2:-}; shift 2;;
    --keep-mounted) KEEP_MOUNTED=1; shift;;
    -h|--help) sed -n '1,24p' "$0"; exit 0;;
    *) printf 'unknown argument: %s\nusage: %s\n' "$1" \
         'channel-image.sh --variant <name> [--run DIR] [--out IMAGE]' >&2; exit 2;;
  esac
done

[ -n "$VARIANT" ] || { printf 'name the variant: descriptor, dispatch or smc\n' >&2; exit 2; }
delta_paths "$VARIANT" || { printf 'unknown variant: %s\n' "$VARIANT" >&2; exit 2; }
test -f "$BASE_CHANNEL" || { printf 'no base channel image: %s\n' "$BASE_CHANNEL" >&2; exit 2; }
test -f "$BASE_BINARY" || { printf 'no base binary: %s\n' "$BASE_BINARY" >&2; exit 2; }

RUN=${RUN:-$RIG/work/runs/channel-$VARIANT-$(date -u +%Y%m%dT%H%M%SZ)}
OUT=${OUT:-$RIG/work/channels/$VARIANT.img}
mkdir -p "$RUN" "$(dirname "$OUT")" "$RIG/work/candidates"

if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'a QEMU is running and may hold this image open; refusing to write it\n' >&2
  exit 2
fi

printf 'variant: %s\n' "$VARIANT"
printf 'base channel: %s (%s)\n' "$BASE_CHANNEL" "$(sha256sum "$BASE_CHANNEL" | cut -c1-12)"
printf 'base binary: %s (%s)\n' "$BASE_BINARY" "$(sha256sum "$BASE_BINARY" | cut -c1-12)"

CANDIDATE="$RIG/work/candidates/$VARIANT-ati2mtag.sys"
printf '\n=== build the candidate from the op manifest ===\n'
python3 "$RIG/graft-check.py" --base "$BASE_BINARY" --manifest "$MANIFEST" --expect "$EXPECT" \
  --output "$CANDIDATE" --require-deployable || {
    printf 'the candidate did not build to the pre-declared expectation; nothing was deployed\n' >&2
    exit 1; }
printf 'candidate: %s\n' "$CANDIDATE"

printf '\n=== copy the base channel, then move one file inside the copy ===\n'
cp "$BASE_CHANNEL" "$OUT" || exit 1
printf 'channel image: %s\n' "$OUT"

# Where the image is mounted while it is written and then read back. `/mnt/gpudrv` is where the
# campaign's earlier deploys were recorded, so a deployment record made here names the same place a
# reader has already seen; the point can be moved for a test.
M=${MOUNT_POINT:-/mnt/gpudrv}
KEEP_MOUNTED=""
DOFF=$(python3 "$RIG/partition-offset.py" "$OUT") || exit 1
sudo mkdir -p "$M"
if ! sudo mount -o loop,offset="$DOFF" "$OUT" "$M"; then
  printf 'the channel image has no mountable first partition at byte %s\n' "$DOFF" >&2
  exit 1
fi
UMOUNTED=""
cleanup() {
  [ -n "$UMOUNTED" ] && return 0
  UMOUNTED=1
  [ -n "$KEEP_MOUNTED" ] && { printf 'left mounted read-only at %s (--keep-mounted); release it with ''sudo umount %s\n' "$M" "$M"; return 0; }
  sudo umount "$M" 2>/dev/null || true
}
trap cleanup EXIT

PREVIOUS="$RUN/rollback-ati2mtag.sys"
if [ -f "$M/drv/ati2mtag.sys" ]; then
  sudo cp "$M/drv/ati2mtag.sys" "$PREVIOUS" || exit 1
  printf 'rollback kept: %s (%s)\n' "$PREVIOUS" "$(sha256sum "$PREVIOUS" | cut -c1-12)"
else
  printf 'the channel holds no drv/ati2mtag.sys, so there is nothing to roll back to\n' >&2
  exit 1
fi

sudo cp "$CANDIDATE" "$M/drv/ati2mtag.sys" || exit 1
sync
printf 'deployed into the image\n'

printf '\n=== clean unmount, then a read-only re-attach, then read the hash from there ===\n'
# The task text asks for the deployed hash to be read back after a clean unmount and a read-only
# remount, and the difference is not ceremony. Verifying against the same writable attachment the
# copy went through can read bytes that were never flushed, so a record could name a hash the file
# on disk does not hold once the machine is restarted or the image is opened elsewhere.
sudo umount "$M" || { printf 'the writable attachment would not release; nothing was recorded\n' >&2; exit 1; }
UMOUNTED=1
sync
if ! sudo mount -o ro,loop,offset="$DOFF" "$OUT" "$M"; then
  printf 'the finished image will not mount read-only at %s\n' "$M" >&2
  exit 1
fi
printf 're-attached read-only at %s\n' "$M"

cp "$CANDIDATE" "$RUN/ati2mtag.sys" || exit 1
printf '\n=== verify the deployment against the read-only attachment, as the guest will read it ===\n'
python3 "$RIG/graft_deploy.py" --verify-mounted --built "$RUN/ati2mtag.sys" \
  --mount "$M" --record "$RUN/deployment.json" --rollback "$PREVIOUS" || exit 1
cleanup

printf '\n=== the manifest beside the image ===\n'
python3 - "$OUT" "$RUN" "$VARIANT" "$MANIFEST" "$EXPECT" "$BASE_CHANNEL" "$CANDIDATE" <<'PY' || exit 1
import datetime, hashlib, json, os, sys

image, run, variant, manifest, expect, base_channel, candidate = sys.argv[1:8]


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


deployment = json.load(open(os.path.join(run, "deployment.json")))
record = {
    "variant": variant,
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "image": os.path.abspath(image),
    "image_sha256": sha(image),
    "image_bytes": os.path.getsize(image),
    "base_channel": os.path.abspath(base_channel),
    "base_channel_sha256": sha(base_channel),
    "candidate": os.path.abspath(candidate),
    "candidate_sha256": sha(candidate),
    "deployed_sha256": deployment["deployed_sha256"],
    "rollback_sha256": deployment["rollback_sha256"],
    "deployment": os.path.abspath(os.path.join(run, "deployment.json")),
    "ops_manifest": os.path.abspath(manifest),
    "ops_manifest_sha256": sha(manifest),
    "expectation": os.path.abspath(expect),
    "expectation_sha256": sha(expect),
    "destination": "drv/ati2mtag.sys",
    "what_moves": "one file, drv/ati2mtag.sys; every other byte of the image is the base channel's",
}
path = os.path.splitext(image)[0] + ".json"
json.dump(record, open(path, "w"), indent=2, sort_keys=True)
print("manifest: %s" % path)
for key in ("candidate_sha256", "deployed_sha256", "rollback_sha256", "image_sha256"):
    print("  %-18s %s" % (key, record[key][:20]))
if record["candidate_sha256"] != record["deployed_sha256"]:
    raise SystemExit("the image does not hold the candidate the manifest names")
PY

printf '\nchannel image: %s\n' "$OUT"
printf 'record: %s/deployment.json\n' "$RUN"
printf 'then boot it: ./run-experiment.sh --golden work/goldens/os.qcow2 --channel %s --name %s\n' \
  "$OUT" "$VARIANT"
printf 'then grade it: ./delta-verify.sh --delta %s --base %s --run %s\n' \
  "$VARIANT" "$BASE_BINARY" "$RUN"
