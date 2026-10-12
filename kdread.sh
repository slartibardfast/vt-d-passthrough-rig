#!/usr/bin/env bash
# kdread.sh <hexaddr> <hexlen> - send a read-vm, flush via reconnect, print the data
set -u
cd /home/dconnolly/xp64-rig
ADDR=$1; LEN=$2
for p in $(pgrep -f "kdc[.]py"); do kill -9 "$p" 2>/dev/null; done
sleep 1
rm -f cmd
setsid nohup python3 kdc.py >> /tmp/opencode/kdc-stderr.log 2>&1 &
sleep 10
echo "read $ADDR $LEN" > cmd
sleep 6
for p in $(pgrep -f "kdc[.]py"); do kill -9 "$p" 2>/dev/null; done
sleep 1
rm -f cmd
setsid nohup python3 kdc.py >> /tmp/opencode/kdc-stderr.log 2>&1 &
sleep 10
python3 - "$ADDR" <<'PY'
import re, struct, sys
lines = open("kdc-live.log").read().splitlines()
addr = int(sys.argv[1], 16)
best = None
for i, ln in enumerate(lines):
    m = re.match(r"\[\S+\] RX manipulate api=0x3130 ret=(0x[0-9a-f]+) len=(\d+)", ln)
    if not m:
        continue
    data = bytearray()
    for l2 in lines[i+1:]:
        hm = re.match(r"\[\S+\]\s+(?:[0-9a-f]{4}  )?((?:[0-9a-f]{2} )+[0-9a-f]{2})\s*$", l2)
        if not hm:
            break
        data += bytes.fromhex(hm.group(1).replace(" ", ""))
    if len(data) >= 0x40 and struct.unpack_from("<Q", data, 0x10)[0] == addr:
        best = (m.group(1), data)
if best:
    ret, data = best
    print("ret=%s data: %s" % (ret, data[0x38:].hex(" ")))
else:
    print("no response found for %#x" % addr)
PY
