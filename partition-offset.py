#!/usr/bin/env python3
"""Print the byte offset of an image's first partition, read from its MBR entry.

Split out of stage-install-channel.sh because channel-image.sh needs the same number, and two
copies of a 0x1BE seek is two ways to disagree. A partition that starts at sector zero is refused
rather than reported, since offset 0 would mount the MBR as the volume.
"""
import struct
import sys


def main(argv):
    if len(argv) < 2:
        print("usage: partition-offset.py <image>", file=sys.stderr)
        return 2
    with open(argv[1], "rb") as handle:
        handle.seek(0x1BE)
        entry = handle.read(16)
    start = struct.unpack("<I", entry[8:12])[0]
    if start == 0:
        print("partition-offset: no first partition entry in %s" % argv[1], file=sys.stderr)
        return 1
    print(start * 512)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
