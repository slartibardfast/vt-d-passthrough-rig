#!/usr/bin/env python3
"""One before/after frame pair around one action, scored against the feed's noise floor.

The action is named by argv so the same measurement drives either input path:
  --monitor 'sendkey menu'     the QEMU emulated keyboard
  --hid $'\\t'                 the GLKVM's own HID
  --click FX FY                the GLKVM mouse through the page

Frames are cropped to the video element before comparison, so the browser
chrome's own animation cannot be mistaken for the guest changing state.

Usage: glkvm-probe.py <tag> --monitor CMD | --hid TEXT | --click FX FY
"""
import argparse
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Cdp, get_ws_url  # noqa: E402
from PIL import Image, ImageChops  # noqa: E402

RIG = os.path.dirname(os.path.abspath(__file__))
COOKIES = "/tmp/opencode/glkvm.cookies"
GLKVM = os.environ.get("GLKVM_URL", "https://192.168.21.99")


def video_box(c):
    return json.loads(c.ev("JSON.stringify((v=>{const q=v.getBoundingClientRect();"
                           "return {x:q.x,y:q.y,w:q.width,h:q.height}})"
                           "(document.querySelector('video')))")["result"]["value"])


def grab(c, box, path):
    c.shot(path + ".full.png")
    im = Image.open(path + ".full.png")
    im.crop((int(box["x"]), int(box["y"]),
             int(box["x"] + box["w"]), int(box["y"] + box["h"]))).save(path + ".png")


def score(a, b):
    ca, cb = Image.open(a).convert("L"), Image.open(b).convert("L")
    diff = ImageChops.difference(ca, cb)
    hist = diff.histogram()
    total = sum(hist)
    mean = sum(i * n for i, n in enumerate(hist)) / total if total else 0.0
    return mean, diff.getextrema()[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tag")
    ap.add_argument("--monitor")
    ap.add_argument("--hid")
    ap.add_argument("--click", nargs=2, type=float, metavar=("FX", "FY"))
    ap.add_argument("--settle", type=float, default=4.0)
    a = ap.parse_args()

    outdir = os.path.join(RIG, "work", "runs", "glkvm-probe", a.tag)
    os.makedirs(outdir, exist_ok=True)
    c = Cdp(get_ws_url())
    box = video_box(c)

    # Noise floor first: two frames of nothing, same gap as the test.
    grab(c, box, os.path.join(outdir, "n0"))
    time.sleep(a.settle)
    grab(c, box, os.path.join(outdir, "n1"))
    floor = score(os.path.join(outdir, "n0.png"), os.path.join(outdir, "n1.png"))

    grab(c, box, os.path.join(outdir, "b"))
    if a.monitor:
        subprocess.run([os.path.join(RIG, "qmon.py"), os.path.join(RIG, "mon.sock"),
                        a.monitor], capture_output=True, timeout=30)
        acted = "monitor %s" % a.monitor
    elif a.hid is not None:
        subprocess.run(["curl", "-ksS", "-b", COOKIES, "-X", "POST",
                        GLKVM + "/api/hid/print?limit=0&keymap=en-us",
                        "-H", "Content-Type: text/plain", "--data-binary", a.hid],
                       capture_output=True, timeout=30)
        acted = "hid print %r" % a.hid
    else:
        fx, fy = a.click
        subprocess.run([sys.executable, os.path.join(RIG, "glkvm-click.py"),
                        str(fx), str(fy), "click"], capture_output=True, timeout=60)
        acted = "click (%.3f, %.3f)" % (fx, fy)
    time.sleep(a.settle)
    grab(c, box, os.path.join(outdir, "a"))
    after = score(os.path.join(outdir, "b.png"), os.path.join(outdir, "a.png"))

    print("action: %s" % acted)
    print("noise floor: mean=%.3f peak=%d" % floor)
    print("after action : mean=%.3f peak=%d" % after)
    changed = after[0] > 4 * max(floor[0], 0.01) and after[1] > floor[1]
    print("verdict: %s" % ("the guest screen changed" if changed else
                           "no change beyond the noise floor"))
    print("frames: %s" % outdir)
    return 0 if changed else 3


if __name__ == "__main__":
    sys.exit(main())
