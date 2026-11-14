#!/usr/bin/env python3
"""Point and click the GLKVM feed from a script.

The 290X's only visible output is the GLKVM's HDMI capture, and the only input
that reaches the running XP session is the GLKVM's own HID device, which
run-driver.sh passes through to the guest. The page turns DOM mouse events on
the video element into HID reports, so driving the page drives the guest.

Coordinates are fractions of the guest screen (0..1), never pixels: the feed
is letterboxed inside the page, and a pixel target measured on one screenshot
is wrong on the next window size.

Usage: glkvm-click.py <fx> <fy> [click|move|double]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Cdp, get_ws_url  # noqa: E402


def video_box(c):
    r = c.ev("(() => {const v=document.querySelector('video');"
             "if(!v) return JSON.stringify({error:'no video element'});"
             "const b=v.getBoundingClientRect();"
             "return JSON.stringify({x:b.x,y:b.y,w:b.width,h:b.height,"
             "vw:v.videoWidth,vh:v.videoHeight})})()")
    import json
    return json.loads(r["result"]["value"])


def main():
    fx, fy, what = float(sys.argv[1]), float(sys.argv[2]), (sys.argv[3] if len(sys.argv) > 3 else "click")
    c = Cdp(get_ws_url())
    box = video_box(c)
    if "error" in box:
        print(box["error"], file=sys.stderr)
        return 2
    print("video box:", box)

    # The element is letterboxed: map the guest fraction through the drawn
    # image area, not the element box.
    ar_img = box["vw"] / box["vh"]
    ar_box = box["w"] / box["h"]
    if ar_img > ar_box:
        dh = box["w"] / ar_img
        dy0 = (box["h"] - dh) / 2.0
        dw, dx0 = box["w"], 0.0
    else:
        dw = box["h"] * ar_img
        dx0 = (box["w"] - dw) / 2.0
        dh, dy0 = box["h"], 0.0
    px = box["x"] + dx0 + fx * dw
    py = box["y"] + dy0 + fy * dh
    print("page point for guest fraction (%.3f, %.3f): %.1f, %.1f" % (fx, fy, px, py))

    # The feed gates input until the pointer has moved over the video and the
    # container has focus (measured law from the October eyes work), so move
    # twice before the button goes down.
    for _ in range(2):
        c.cmd("Input.dispatchMouseEvent", type="mouseMoved", x=px, y=py, buttons=0,
              pointerType="mouse", clickCount=0)
        time.sleep(0.4)
    if what in ("click", "double"):
        c.cmd("Input.dispatchMouseEvent", type="mousePressed", x=px, y=py,
              button="left", buttons=1, pointerType="mouse", clickCount=1)
        time.sleep(0.12)
        c.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=px, y=py,
              button="left", buttons=0, pointerType="mouse", clickCount=1)
        if what == "double":
            time.sleep(0.1)
            c.cmd("Input.dispatchMouseEvent", type="mousePressed", x=px, y=py,
                  button="left", buttons=1, pointerType="mouse", clickCount=2)
            time.sleep(0.12)
            c.cmd("Input.dispatchMouseEvent", type="mouseReleased", x=px, y=py,
                  button="left", buttons=0, pointerType="mouse", clickCount=2)
    print("sent", what)
    return 0


if __name__ == "__main__":
    sys.exit(main())
