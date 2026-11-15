#!/usr/bin/env python3
"""Re-derive a golden's own hash and add what its manifest should have said about its source.

Split out from golden-build.sh so the loop over a 20 GiB source image is a function with a name
rather than a heredoc, and so a test can call it directly.
"""
import datetime
import hashlib
import json
import os
import sys


def sha256_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv):
    golden, manifest = argv[1], argv[2]
    source = argv[3] if len(argv) > 3 else ""
    provenance = argv[4] if len(argv) > 4 else ""
    data = json.load(open(manifest))
    data["sha256"] = sha256_stream(golden)
    data["bytes"] = os.path.getsize(golden)
    if source:
        if not os.path.isfile(source):
            print("golden-build: no such source image: %s" % source, file=sys.stderr)
            return 2
        data["source"] = os.path.abspath(source)
        data["source_sha256"] = sha256_stream(source)
    if provenance:
        data["provenance"] = provenance
        data["provenance_recorded"] = datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    json.dump(data, open(manifest, "w"), indent=2, sort_keys=True)
    print("amended %s" % manifest)
    print("golden sha256 %s" % data["sha256"])
    if "source_sha256" in data:
        print("source sha256 %s" % data["source_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
