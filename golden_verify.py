#!/usr/bin/env python3
"""Check every golden in a directory against the manifest that claims to describe it.

Why this exists. A golden is the read-only parent of every run, and its manifest is the record
that makes a run attributable: the run stores the golden's hash, and a reader trusts it because the
manifest says what the file is. That trust is only worth what the check behind it is worth. Without
a verifier, a manifest is a promise, a silently edited golden looks identical to an untouched one,
and a run whose parent changed under it still prints a clean line.

What is checked, and why each line is here rather than being obvious:

  - the file's own sha256 against the manifest's, because that is the hash every run cites;
  - the byte count, because a hash match with a truncated file is not a thing that happens, but a
    byte-count mismatch alongside a matching hash means the manifest was written against a
    different file and that is worth naming out loud;
  - the format and the absence of a backing file, because a golden that inherits is not a golden:
    it changes whenever the file it points at changes, which is the defect `call/0010` exists to
    avoid;
  - the source identity pair, the MBR disk signature and first-partition offset, because the drive
    letters the runs rely on are pinned from those two numbers and a golden built from a
    re-partitioned disk would otherwise carry no sign of it;
  - that the named source still exists, reported rather than failed, since a source disk on a
    machine that is not this one is a fact about the machine and not a defect in the record.

Usage: golden_verify.py <dir>
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

FIELDS = ("sha256", "bytes", "format", "virtual_bytes", "source_identity")


def sha256_stream(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def info(path):
    out = subprocess.run(["qemu-img", "info", "--output=json", str(path)],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit("golden_verify: qemu-img cannot read %s: %s" % (path, out.stderr.strip()))
    return json.loads(out.stdout)


def verify(golden):
    """One golden. Returns a list of (ok, line) results."""
    manifest = golden.with_suffix(".json")
    results = []
    if not manifest.is_file():
        return [(False, "%s: no manifest beside it, so nothing records what it is" % golden.name)]
    try:
        record = json.loads(manifest.read_text())
    except json.JSONDecodeError as exc:
        return [(False, "%s: manifest does not parse (%s)" % (golden.name, exc))]

    missing = [f for f in FIELDS if f not in record]
    if missing:
        results.append((False, "%s: manifest omits %s" % (golden.name, ", ".join(missing))))

    digest = sha256_stream(golden)
    meta = info(golden)
    ok = digest == record.get("sha256")
    results.append((ok, "sha256 %s (%s)" % (digest[:16] + (" match" if ok else " MISMATCH"),
                                            "manifest " + str(record.get("sha256", ""))[:16])))
    size = golden.stat().st_size
    results.append((size == record.get("bytes"),
                    "bytes %d (%s)" % (size, "match" if size == record.get("bytes")
                                       else "manifest %s" % record.get("bytes"))))
    results.append((meta.get("format") == "qcow2" == record.get("format"),
                    "format %s" % meta.get("format")))
    backing = meta.get("backing-filename")
    results.append((backing is None,
                    "backing file %s" % ("none, so it is standalone" if backing is None
                                         else "%s, which makes it not a golden" % backing)))
    virtual = meta.get("virtual-size")
    results.append((virtual == record.get("virtual_bytes"),
                    "virtual %d (%s)" % (virtual, "match" if virtual == record.get("virtual_bytes")
                                         else "manifest %s" % record.get("virtual_bytes"))))
    ident = record.get("source_identity") or {}
    have_ident = {"disk_signature", "partition_offset"} <= set(ident)
    results.append((have_ident,
                    "source identity %s" % (ident if have_ident else
                                            "absent, so the pinned drive letters of a run cannot be "
                                            "re-derived from this record")))
    source = record.get("source")
    if source:
        results.append((True, "source %s %s" % (source, "present" if Path(source).is_file()
                                                else "absent on this machine, reported not failed")))
    if "provenance" in record:
        results.append((True, "provenance recorded (%s)" % record.get("provenance_recorded", "?")))
    else:
        results.append((True, "provenance not recorded, which the amend mode can add"))
    return results


def main(argv):
    if len(argv) < 2:
        print("usage: golden_verify.py <dir>", file=sys.stderr)
        return 2
    root = Path(argv[1])
    if not root.is_dir():
        print("golden_verify: no such directory: %s" % root, file=sys.stderr)
        return 2
    goldens = sorted(root.glob("*.qcow2"))
    if not goldens:
        print("golden_verify: %s holds no golden to check" % root, file=sys.stderr)
        return 2
    bad = []
    for golden in goldens:
        print("golden %s" % golden.name)
        for ok, line in verify(golden):
            print("  %-7s %s" % ("ok" if ok else "FAIL", line))
            if not ok:
                bad.append("%s: %s" % (golden.name, line))
    if bad:
        print("verdict: FAIL (%d problem(s))" % len(bad))
        for b in bad:
            print("  remedy: re-record with ./golden-build.sh --amend %s or rebuild the golden"
                  % (b.split(":")[0]))
        return 1
    print("verdict: PASS, every golden matches its manifest (%d checked)" % len(goldens))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
