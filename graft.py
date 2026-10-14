#!/usr/bin/env python3
"""graft.py - the campaign's graft builder (plan/0006 #graft-builder).

Applies a recorded transformation manifest to a base driver image and emits
the patched image plus a sha256 digest that binds it. Spec: specs/patch.allium.

Usage:
    graft.py <base-image> <manifest.json> <output-image>

The manifest:
    {"ops": [
       {"op": "fill",  "at": N, "size": M, "donor": "path", "donor_at": K},
       {"op": "patch", "at": N, "bytes": "hex"}
    ]}

Ops apply in order. Fills copy donor bytes byte-exact (the SMC slot law).
The builder never resizes the image. Any range violation is a loud
rejection (GraftRangeError) with no output written.
"""
import hashlib
import os
import json
import sys


class GraftRangeError(Exception):
    """A manifest operation addresses memory outside its image."""


class GraftEmptyManifestError(Exception):
    """A manifest with no operations is rejected, not silently applied."""


def apply_manifest(base, manifest, donors):
    """Apply the manifest to base bytes; return the patched bytes.

    donors: {name: bytes}. Raises GraftRangeError / GraftEmptyManifestError.
    """
    ops = manifest.get("ops")
    if not ops:
        raise GraftEmptyManifestError("manifest carries no operations")
    out = bytearray(base)
    for i, op in enumerate(ops):
        at = op["at"]
        if op["op"] == "fill":
            size = op["size"]
            if at < 0 or size < 0 or at + size > len(out):
                raise GraftRangeError(
                    "op %d: fill range [%d, %d) outside base (%d bytes)"
                    % (i, at, at + size, len(out)))
            donor = donors[op["donor"]]
            donor_at = op["donor_at"]
            if donor_at < 0 or donor_at + size > len(donor):
                raise GraftRangeError(
                    "op %d: donor range [%d, %d) outside %s (%d bytes)"
                    % (i, donor_at, donor_at + size, op["donor"], len(donor)))
            out[at:at + size] = donor[donor_at:donor_at + size]
        elif op["op"] == "patch":
            patch = bytes.fromhex(op["bytes"])
            if at < 0 or at + len(patch) > len(out):
                raise GraftRangeError(
                    "op %d: patch range [%d, %d) outside base (%d bytes)"
                    % (i, at, at + len(patch), len(out)))
            out[at:at + len(patch)] = patch
        else:
            raise GraftRangeError("op %d: unknown op %r" % (i, op["op"]))
    return bytes(out)


def digest_of(data):
    return hashlib.sha256(data).hexdigest()


def main(argv):
    if len(argv) != 4:
        print("usage: graft.py <base-image> <manifest.json> <output-image>",
              file=sys.stderr)
        return 2
    base_path, manifest_path, out_path = argv[1:4]
    base = open(base_path, "rb").read()
    mdir = os.path.dirname(os.path.abspath(manifest_path))
    manifest = json.load(open(manifest_path))
    donors = {}
    for op in manifest.get("ops", []):
        name = op.get("donor")
        if name and name not in donors:
            p = name if os.path.isabs(name) else os.path.join(mdir, name)
            donors[name] = open(p, "rb").read()
    try:
        out = apply_manifest(base, manifest, donors)
    except (GraftRangeError, GraftEmptyManifestError) as e:
        print("REJECTED: %s" % e, file=sys.stderr)
        return 1
    with open(out_path, "wb") as f:
        f.write(out)
    dgst = digest_of(out)
    with open(out_path + ".sha256", "w") as f:
        f.write(dgst + "\n")
    print("applied %d ops -> %s (%d bytes, sha256 %s)"
          % (len(manifest["ops"]), out_path, len(out), dgst))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
