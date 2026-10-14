#!/usr/bin/env bash
# signal-state.sh - the feed signal verdict: no-signal, signal-black,
# signal-content. Reads the GLKVM feed through cdp.py and classifies the
# canvas region (the UI chrome cropped away).
set -u
RIG=/home/dconnolly/xp64-rig
OUT=$RIG/work/signal-state.txt
mkdir -p "$RIG/work"
rm -f /tmp/opencode/signal-state.png
shot=$(timeout 90 python3 "$RIG/cdp.py" shot /tmp/opencode/signal-state.png 2>&1)
if [ ! -f /tmp/opencode/signal-state.png ]; then
  echo "no-signal (no feed capture: $shot)" > "$OUT"
  cat "$OUT"
  exit 0
fi
python3 - "$OUT" <<'PY'
import sys
from PIL import Image
from collections import Counter
im = Image.open("/tmp/opencode/signal-state.png").convert("RGB")
w, h = im.size
px = im.load()
# the feed canvas occupies roughly x in [0.15,0.85], y in [0.05,0.96]
# of the browser page; sample strictly inside it.
c = Counter(px[x, y] for x in range(int(w*0.20), int(w*0.80), 5)
            for y in range(int(h*0.10), int(h*0.90), 5))
top = c.most_common(1)[0]
variety = len(c)
if variety <= 2 and top[0] == (0, 0, 0):
    verdict = "signal-black"
elif variety <= 2:
    verdict = "uniform-%s" % (top[0],)
elif variety > 50:
    verdict = "signal-content"
else:
    verdict = "low-variety-%d" % variety
with open(sys.argv[1], "w") as f:
    f.write("%s canvas=%dx%d sampled-colors=%d dominant=%s\n" % (verdict, w, h, variety, top[0]))
print(verdict)
PY
