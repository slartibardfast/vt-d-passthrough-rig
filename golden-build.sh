#!/usr/bin/env bash
# Turn the working raw disk into a standalone qcow2 golden, and record what it is.
#
# Why a golden at all: the iteration loop the plan is building must boot a variant without
# ever writing to the disk it came from. call/0010 settles that at the disk layer, since
# QEMU refuses machine snapshots while a VFIO device is assigned, so the layering has to
# be qcow2 backing files rather than savevm. A golden is the read-only shared base of that
# layering, and the record beside it is what makes a later run attributable: an overlay with
# no stated parent is just a file.
#
# Why standalone: an image that has a backing file inherits whatever that file holds now, so
# a golden built as a copy-on-write child of the raw disk would silently change when the raw
# disk changed. `qemu-img convert` writes the whole content, which is what makes the golden's
# hash mean something.
#
# Refuses a running QEMU: a golden taken from an open disk is a golden of no state L can name,
# because the writeback cache still holds writes the file does not.
#
# Usage: golden-build.sh --from <raw-or-image> --to <dir> [--name NAME] [--force]
#                        [--provenance <text>]
#        golden-build.sh --amend <golden> [--source <image>] [--provenance <text>]
#        golden-build.sh --verify <dir> [--no-state]
#
# Why --verify exists. A golden is the read-only parent of every run, and two things are asserted
# about it: that the file is what its manifest says, and that it carries the settled state every run
# is written against. The first is cheap and offline, so it always runs. The second has to read the
# registry inside the image, so it runs unless --no-state says otherwise, and it refuses to answer
# while a guest holds the image rather than guessing.
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
FROM="" TO="" NAME="os" FORCE="" AMEND="" SOURCE="" PROVENANCE="" VERIFY="" NO_STATE=""
#
# Why --amend exists. A manifest is the record of what a golden is, and the honest answer includes
# how its source disk came to hold those bytes. The first golden of this campaign was converted from
# a disk that had two signalled-off boots and then one clean shutdown, and that belongs in the
# manifest rather than only in a memory log. Amending through the tool keeps the rule that these
# records are never hand-edited, and re-derives the golden's own hash instead of trusting the one
# already written.
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
FROM="" TO="" NAME="os" FORCE="" AMEND="" SOURCE="" PROVENANCE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM=${2:-}; shift 2;;
    --to) TO=${2:-}; shift 2;;
    --name) NAME=${2:-}; shift 2;;
    --amend) AMEND=${2:-}; shift 2;;
    --source) SOURCE=${2:-}; shift 2;;
    --provenance) PROVENANCE=${2:-}; shift 2;;
    --verify) VERIFY=${2:-}; shift 2;;
    --no-state) NO_STATE=1; shift;;
    --force) FORCE=1; shift;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done

if [ -n "$VERIFY" ]; then
  test -d "$VERIFY" || { printf 'no such golden directory: %s\n' "$VERIFY" >&2; exit 2; }
  status=0
  printf '=== does each golden match its manifest? ===\n'
  python3 "$RIG/golden_verify.py" "$VERIFY" || status=1
  if [ -n "$NO_STATE" ]; then
    printf '\nskipped the settled-state read (--no-state): the digests above say the file is what the\n'
    printf 'manifest names, and say nothing about what the registry inside it holds\n'
    exit $status
  fi
  printf '\n=== does each golden carry the settled state a run is written against? ===\n'
  for golden in "$VERIFY"/*.qcow2; do
    [ -f "$golden" ] || continue
    python3 "$RIG/golden-state.py" --golden "$golden" || status=1
  done
  exit $status
fi

if [ -n "$AMEND" ]; then
  test -f "$AMEND" || { printf 'no such golden to amend: %s\n' "$AMEND" >&2; exit 2; }
  AMEND_MANIFEST="${AMEND%.qcow2}.json"
  test -f "$AMEND_MANIFEST" || {
    printf 'no manifest beside %s; a golden with no record is not a golden\n' "$AMEND" >&2
    exit 2; }
  python3 "$RIG/golden_amend.py" "$AMEND" "$AMEND_MANIFEST" "${SOURCE:-}" "$PROVENANCE"
  exit $?
fi

[ -n "$FROM" ] && [ -n "$TO" ] || { printf 'usage: golden-build.sh --from <image> --to <dir> [--name NAME] [--force]\n' >&2; exit 2; }
test -f "$FROM" || { printf 'no such source image: %s\n' "$FROM" >&2; exit 2; }
# The guest check comes from guest-running.sh, which holds the rule and the reason for it:
# a command-line match was measured catching shells that only mention the binary.
if "$RIG/guest-running.sh" check >/dev/null; then
  printf 'a QEMU is running: its cache holds writes this image does not. Shut it down first.\n' >&2
  exit 2
fi

mkdir -p "$TO"
GOLDEN="$TO/$NAME.qcow2"
MANIFEST="$TO/$NAME.json"
if [ -e "$GOLDEN" ] && [ -z "$FORCE" ]; then
  printf 'the golden %s already exists; a second conversion would change what past runs mean\n' "$GOLDEN" >&2
  printf 'pass --force only if the disk it came from is a different disk\n' >&2
  exit 2
fi

# The tools this script drives, checked before anything is written. A missing one must stop
# the run with the package that provides it, because a golden that was never built is worth
# more than a golden that was quietly not built: the first failure of this script on the CI
# runner was `qemu-img: command not found` followed by a JSON traceback and a half-written
# manifest, which reads like a broken conversion rather than a missing dependency.
for need in qemu-img python3; do
  command -v "$need" >/dev/null || {
    printf '%s is required; on Ubuntu and Debian it comes from the package %s\n' "$need" \
      "$([ "$need" = qemu-img ] && echo qemu-utils || echo python3)" >&2
    exit 2; }
done

SRC_FMT=$(qemu-img info --output=json "$FROM" | python3 -c 'import json,sys; print(json.load(sys.stdin)["format"])') \
  || { printf 'qemu-img cannot read %s; refusing to record a golden L cannot identify\n' "$FROM" >&2; exit 2; }
printf 'source %s is format %s\n' "$FROM" "$SRC_FMT"
tmp="$GOLDEN.partial"
if ! qemu-img convert -f "$SRC_FMT" -O qcow2 "$FROM" "$tmp"; then
  rm -f "$tmp"
  printf 'qemu-img convert failed; no golden was recorded\n' >&2
  exit 2
fi
mv "$tmp" "$GOLDEN"

# The identity is the same pair hive-pin.py pins a drive letter with: the MBR disk signature
# and the first partition's byte offset. Read from the source, never transcribed. This is the
# third copy of that reader in the rig; hive-pin.py holds the rule and cannot be imported under
# its hyphenated name. Deduplicating it is recorded as debt rather than done here.
IDENT=$(python3 - "$FROM" <<'PY'
import json, struct, sys
with open(sys.argv[1], "rb") as f:
    f.seek(0x1B8)
    sig = struct.unpack("<I", f.read(4))[0]
    f.seek(0x1BE)
    start = struct.unpack("<I", f.read(16)[8:12])[0]
print(json.dumps({"disk_signature": "0x%08x" % sig, "partition_offset": start * 512}))
PY
) || { printf 'cannot read an MBR partition table from %s; refusing to record an unknown golden\n' "$FROM" >&2; exit 2; }

python3 - "$GOLDEN" "$MANIFEST" "$FROM" "$IDENT" "$PROVENANCE" <<'PY'
import datetime, hashlib, json, os, subprocess, sys
golden, manifest, source, ident, provenance = sys.argv[1:6]


def sha256_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()

digest = hashlib.sha256(open(golden, "rb").read()).hexdigest()
info = json.loads(subprocess.run(["qemu-img", "info", "--output=json", golden],
                                 capture_output=True, text=True, check=True).stdout)
if info.get("backing-filename"):
    raise SystemExit("%s has a backing file, so it is not standalone" % golden)
record = {
    "golden": os.path.abspath(golden),
    "format": info["format"],
    "virtual_bytes": info["virtual-size"],
    "bytes": os.path.getsize(golden),
    "sha256": digest,
    "source": os.path.abspath(source),
    "source_sha256": sha256_stream(source),
    "source_identity": json.loads(ident),
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
if provenance:
    record["provenance"] = provenance
json.dump(record, open(manifest, "w"), indent=2, sort_keys=True)
print("recorded %s" % manifest)
PY
[ -s "$MANIFEST" ] || exit 1

printf 'golden: %s\n' "$GOLDEN"
printf 'manifest: %s\n' "$MANIFEST"
printf 'identity: %s\n' "$IDENT"
sha256sum "$GOLDEN"
