#!/usr/bin/env python3
"""Verify a mounted graft destination and record its rollback evidence."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity(path):
    stat = path.stat()
    return {"device": stat.st_dev, "inode": stat.st_ino, "size": stat.st_size}


def verify_files(built, mount, rollback):
    for path in (built, rollback):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"not a regular file: {path}")
    destination = mount / "drv" / "ati2mtag.sys"
    if not destination.is_file() or destination.is_symlink():
        raise ValueError(f"missing regular deployed driver: {destination}")
    built_hash = sha256(built)
    deployed_hash = sha256(destination)
    if built_hash != deployed_hash:
        raise ValueError("deployed driver hash differs from the built candidate")
    return {
        "destination": "drv/ati2mtag.sys",
        "built": str(built),
        "built_sha256": built_hash,
        "built_identity": identity(built),
        "deployed_sha256": deployed_hash,
        "deployed_identity": identity(destination),
        "rollback": str(rollback),
        "rollback_sha256": sha256(rollback),
        "rollback_identity": identity(rollback),
        "mount": str(mount),
        "mount_identity": identity(mount),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-mounted", action="store_true", required=True)
    parser.add_argument("--built", required=True, type=Path)
    parser.add_argument("--mount", required=True, type=Path)
    parser.add_argument("--record", required=True, type=Path)
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args(argv)
    rollback = args.rollback or args.record.parent / "rollback-ati2mtag.sys"
    mounted = subprocess.run(
        ["mountpoint", "-q", str(args.mount)], capture_output=True).returncode == 0
    if not mounted:
        raise ValueError(f"not a mounted filesystem: {args.mount}")
    record = verify_files(args.built, args.mount, rollback)
    args.record.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.record.with_name(args.record.name + ".tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.replace(args.record)
    print(f"PASS deployment: sha256 {record['deployed_sha256']}; rollback recorded")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        sys.exit(30)
