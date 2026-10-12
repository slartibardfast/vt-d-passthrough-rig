#!/usr/bin/env bash
# kddump.sh <hexaddr> <hexlen> - dump guest memory via the KD read-vm
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
python3 - "$ADDR" "$LEN" <<'PY'
import re, struct, sys
addr, ln = int(sys.argv[1], 16), int(sys.argv[2], 16)
lines = open("kdc-live.log").read().splitlines()
best = None
for i, l in enumerate(lines):
    m = re.match(r"\[\S+\] RX manipulate api=0x3130 ret=(0x[0-9a-f]+) len=(\d+)", l)
    if not m: continue
    data = bytearray()
    for l2 in lines[i+1:]:
        hm = re.match(r"\[\S+\]\s+(?:[0-9a-f]{4}  )?((?:[0-9a-f]{2} )+[0-9a-f]{2})\s*$", l2)
        if not hm: break
        data += bytes.fromhex(hm.group(1).replace(" ", ""))
    if len(data) >= 0x38 and struct.unpack_from("<Q", data, 0x10)[0] == addr and int(m.group(2), 16) >= 0x38 + 8:
        best = (m.group(1), data)
if best:
    ret, data = best
    payload = data[0x38:0x38+ln]
    print("ret=%s bytes=%d" % (ret, len(payload)))
    for j in range(0, len(payload), 16):
        chunk = payload[j:j+16]
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print("%06x  %s  %s" % (j, (" ".join("%02x" % b for b in chunk)).ljust(47), asc))
    open("/tmp/opencode/kddump.bin","wb").write(payload)
else:
    print("no response")
PY
