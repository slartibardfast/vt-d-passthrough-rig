#!/usr/bin/env python3
"""Photograph the card's real output while a run is holding, and record whether it is live.

The rig has no console: QEMU answers "There is no console to take a screendump from" for a
passthrough GPU, so the GLKVM's HDMI input is the only instrument that sees what the 290X drives.
`glkvm-eyes.py` takes one frame after a fixed settle, and a boot reaches output at a time nobody
can predict, so a single frame is usually the KVM's own "No HDMI signal detected" card.

One frame is not enough anyway. A frame that never changes is equally well explained by the KVM
holding its last picture, so each frame is recorded with the `<video>` element's own `paused` and
`currentTime`, and two frames a known distance apart are what let a later reader say the stream ran
rather than that a picture sat there. `videoWidth` and `videoHeight` are recorded too, but they are
the timing the KVM's HDMI input detected, not the guest's desktop mode: a run whose guest reports
640x480 can sit on a 1280x1024 link, and `videoctl.txt` from inside the guest is what says which.

Needs an interpreter with `websocket` and `PIL` (on this machine, `/usr/bin/python3`), and
`GLKVM_PASS` in the environment. The credential is never read from a file and never written to one.

usage: feed-capture.py <run-dir> [max-seconds] [--snapshot]
  --snapshot  copy the last frame with signal to the run directory's top level, where
              desktop-evidence.py looks when it binds a frame to the verdict record
"""
import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

RIG = Path(__file__).resolve().parent
sys.path.insert(0, str(RIG))

# The browser bridge needs `websocket`, which the rig's test venv does not carry, so the import is
# guarded and the failure is named the way `registration_graft.py` names a missing hivex: the rules
# below are testable without a browser, and a run without the bridge has to say what to install.
NEEDS = ("the feed bridge needs the `websocket` package: run this with /usr/bin/python3, or add it "
         "with ./venv/bin/pip install websocket-client")
try:
    from cdp import Cdp, get_ws_url  # noqa: E402

    # The login lives in glkvm-eyes.py. Its file name has a hyphen, so it is loaded by path rather
    # than imported by name: the endpoint's field names and the meanings of its replies were
    # measured once, and restating them here would give the rig two places to be wrong about the
    # same thing.
    _spec = importlib.util.spec_from_file_location("glkvm_eyes", (RIG / "glkvm-eyes.py").as_posix())
    eyes = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(eyes)
except ImportError as exc:
    BRIDGE = "%s (%s)" % (NEEDS, exc)
else:
    BRIDGE = None

STATE = ("(() => {const v=document.querySelector('video');"
         "if(!v) return JSON.stringify({error:'no video element'});"
         "return JSON.stringify({w:v?v.videoWidth:0,h:v?v.videoHeight:0,"
         "rs:v.readyState,paused:v.paused,t:v.currentTime,"
         "text:document.body.innerText.slice(0,80)})})()")

def observe(c):
    """The `<video>` element's own report of the stream, as a dict."""
    raw = c.ev(STATE).get("result", {}).get("value", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "the page answered %r" % raw[:120]}


def sidecar(frame, when, state):
    """One recorded line per photographed frame.

    `paused` and `current_time` are kept even when they look redundant: they are the pair that
    separates a stream that ran from a picture that sat there, and a reader who has only the two
    PNG files cannot tell those apart.
    """
    return {"file": frame, "captured_utc": when,
            "video_width": state.get("w"), "video_height": state.get("h"),
            "readystate": state.get("rs"), "paused": state.get("paused"),
            "current_time": state.get("t"), "page_text": state.get("text"),
            "signal": has_signal(state)}


def has_signal(state):
    """True when the KVM is carrying pixels rather than its own no-signal card."""
    return bool(state and state.get("w") and state.get("rs", 0) >= 2 and not state.get("paused"))


def place_snapshot(run, frame):
    """Put a photographed frame where the verdict producer's snapshot glob finds it.

    `desktop-evidence.py` binds a frame from the run directory's top level, and the capture lands
    under `feed/`. Naming the copy here keeps the two paths from drifting apart by hand each time.
    """
    dest = Path(run) / ("snapshot-%s" % Path(frame).name)
    dest.write_bytes(Path(frame).read_bytes())
    return dest


def capture(c, out, limit, log=print):
    """Photograph frames until three carry signal or the limit passes. Returns the records."""
    out.mkdir(parents=True, exist_ok=True)
    ledger = out / "feed.jsonl"
    records = []
    with ledger.open("a") as fh:
        deadline = time.time() + limit
        with_signal = 0
        while time.time() < deadline and with_signal < 3:
            state = observe(c)
            when = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            name = "feed-%s.png" % time.strftime("%H%M%S", time.gmtime())
            frame = out / name
            c.shot(str(frame))
            rec = sidecar(name, when, state)
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            records.append(rec)
            log("  %s  link %sx%s readyState=%s paused=%s t=%.1f  %s"
                % (name, state.get("w"), state.get("h"), state.get("rs"), state.get("paused"),
                   state.get("t") or 0.0, str(state.get("text", ""))[:48].replace("\n", " ")))
            if has_signal(state):
                with_signal += 1
                time.sleep(20)
            else:
                time.sleep(25)
    return records


def main():
    p = argparse.ArgumentParser(prog="feed-capture.py", description=__doc__.splitlines()[0])
    p.add_argument("run", help="the run directory to photograph into")
    p.add_argument("limit", nargs="?", type=float, default=600.0,
                   help="seconds to keep watching for a signal (default 600)")
    p.add_argument("--snapshot", action="store_true",
                   help="copy the last frame with signal to the run top level for the verdict")
    a = p.parse_args()
    if BRIDGE:
        print(BRIDGE, file=sys.stderr)
        return 2
    if "GLKVM_PASS" not in os.environ:
        print("GLKVM_PASS is not set; the feed answers with its own login card without it",
              file=sys.stderr)
        return 2
    run = Path(a.run)
    if not run.is_dir():
        print("%s is not a run directory; %s" % (run, NEEDS), file=sys.stderr)
        return 2
    try:
        c = Cdp(get_ws_url())
    except OSError as exc:
        print("no browser on 127.0.0.1:9222 to show the feed: start the headless Chromium the rig "
              "documents, then retry (%s)" % exc, file=sys.stderr)
        return 2
    c.nav(eyes.URL + "/login")
    answer = eyes.login(c)
    print("login:", json.dumps(answer)[:120], flush=True)
    # The endpoint's reply arrives as the page's own JSON text, not as an object, and a guard that
    # expected a dict refused a working credential and stopped the capture before it took a frame.
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except json.JSONDecodeError:
            answer = {}
    if not (isinstance(answer, dict) and answer.get("ok")):
        print("the feed refused the credential; check GLKVM_USER and GLKVM_PASS", file=sys.stderr)
        return 2
    c.nav(eyes.URL + "/#/kvm")
    time.sleep(8)
    records = capture(c, run / "feed", a.limit)
    good = [r for r in records if r["signal"]]
    print("captured %d frame(s), %d carrying signal; ledger %s"
          % (len(records), len(good), run / "feed" / "feed.jsonl"))
    if a.snapshot and good:
        dest = place_snapshot(run, run / "feed" / good[-1]["file"])
        print("snapshot bound for the verdict: %s" % dest)
    if not good:
        print("no frame carried a signal; the guest had no output to give while this was watching")
        return 1
    print("remember: link %sx%s is the HDMI timing the KVM detected, not the guest's desktop mode; "
          "the guest's own number is in %s"
          % (records[-1]["video_width"], records[-1]["video_height"], run / "evidence/WINDOWS/videoctl.txt"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
