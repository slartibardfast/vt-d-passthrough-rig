#!/usr/bin/env python3
"""Take a picture of what the 290X is actually outputting, through the GLKVM.

The card's display goes to the GLKVM's HDMI input, so the GLKVM is the only
instrument that shows the real output: QEMU has no console for a passthrough
GPU (screendump answers "There is no console to take a screendump from"), and
reads of the guest's VGA aperture have not been shown to return the scanned
surface. This drives the GLKVM web UI over Chromium's DevTools protocol, the
way the October eyes evidence was taken, and saves the frame.

Login and credential come from the environment, never from this file:
  GLKVM_URL     default https://192.168.21.99
  GLKVM_USER    default admin
  GLKVM_PASS    required

The GLKVM's own snapshot endpoint (/api/streamer/snapshot) answers 503 unless
a session is already streaming, so the browser session is the capture path.

Usage: glkvm-eyes.py <out.png> [wait_seconds]
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cdp import Cdp, get_ws_url  # noqa: E402

URL = os.environ.get("GLKVM_URL", "https://192.168.21.99")
USER = os.environ.get("GLKVM_USER", "admin")
PASSWORD = os.environ.get("GLKVM_PASS")


def login(c):
    # The endpoint takes urlencoded form fields user/passwd (measured from the
    # app bundle: {user:..., passwd:...} with formData:true). A wrong field
    # name answers ValidatorError, a wrong password answers ForbiddenError, so
    # the reply distinguishes a malformed probe from a bad credential.
    expr = ("fetch('/api/auth/login',{method:'POST',"
            "headers:{'Content-Type':'application/x-www-form-urlencoded'},"
            "body:new URLSearchParams(%s).toString()})"
            ".then(r=>r.json()).then(j=>JSON.stringify(j))"
            % json.dumps({"user": USER, "passwd": PASSWORD}))
    r = c.ev(expr, await_promise=True)
    return r.get("result", {}).get("value", r)


def main():
    if not PASSWORD:
        print("GLKVM_PASS is not set", file=sys.stderr)
        return 2
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/opencode/glkvm-frame.png"
    settle = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0

    c = Cdp(get_ws_url())
    c.nav(URL + "/login")
    print("login:", login(c))
    # The app is a hash-router SPA: a bare /kvm 404s at nginx, and #/kvm opens the feed.
    c.nav(URL + "/#/kvm")
    time.sleep(settle)
    c.shot(out)
    state = c.ev("(() => {const v=document.querySelector('video');"
                 "return JSON.stringify({ready:document.readyState,"
                 "video: v ? {w:v.videoWidth,h:v.videoHeight,readyState:v.readyState,"
                 "paused:v.paused,currentTime:v.currentTime} : null,"
                 "text: document.body.innerText.slice(0,400)})})()")
    print(state.get("result", {}).get("value", state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
