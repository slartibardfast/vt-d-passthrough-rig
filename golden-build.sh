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
set -uo pipefail

RIG=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
FROM="" TO="" NAME="os" FORCE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM=${2:-}; shift 2;;
    --to) TO=${2:-}; shift 2;;
    --name) NAME=${2:-}; shift 2;;
    --force) FORCE=1; shift;;
    *) printf 'unknown argument: %s\n' "$1" >&2; exit 2;;
  esac
done
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

python3 - "$GOLDEN" "$MANIFEST" "$FROM" "$IDENT" <<'PY'
import datetime, hashlib, json, os, subprocess, sys
golden, manifest, source, ident = sys.argv[1:5]
digest = hashlib.sha256(open(golden, "rb").read()).hexdigest()
info = json.loads(subprocess.run(["qemu-img", "info", "--output=json", golden],
                                 capture_output=True, text=True, check=True).stdout)
if info.get("backing-filename"):
    raise SystemExit("%s has a backing file, so it is not standalone" % golden)
json.dump({
    "golden": os.path.abspath(golden),
    "format": info["format"],
    "virtual_bytes": info["virtual-size"],
    "bytes": os.path.getsize(golden),
    "sha256": digest,
    "source": os.path.abspath(source),
    "source_identity": json.loads(ident),
    "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}, open(manifest, "w"), indent=2, sort_keys=True)
print("recorded %s" % manifest)
PY
[ -s "$MANIFEST" ] || exit 1

printf 'golden: %s\n' "$GOLDEN"
printf 'manifest: %s\n' "$MANIFEST"
printf 'identity: %s\n' "$IDENT"
sha256sum "$GOLDEN"
