#!/usr/bin/env python3
"""Minimal CDP driver for the GLKVM feed: navigate / eval / screenshot."""
import base64, json, sys, time
import urllib.request
import websocket

PORT = 9222

def get_ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json") as r:
        tabs = json.load(r)
    for t in tabs:
        if t.get("type") == "page":
            return t["webSocketDebuggerUrl"]
    raise RuntimeError("no page target")

class Cdp:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=30)
        self.mid = 0
    def cmd(self, method, **params):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.mid:
                return msg
    def nav(self, url):
        self.cmd("Page.enable")
        self.cmd("Page.navigate", url=url)
        time.sleep(3)
    def ev(self, expr, await_promise=False):
        r = self.cmd("Runtime.evaluate", expression=expr,
                     awaitPromise=await_promise, returnByValue=True,
                     userGesture=True)
        return r.get("result", {})
    def shot(self, out):
        r = self.cmd("Page.captureScreenshot", format="png")
        data = r["result"]["data"]
        open(out, "wb").write(base64.b64decode(data))
        print("saved", out)

def main():
    cmd = sys.argv[1]
    c = Cdp(get_ws_url())
    if cmd == "nav":
        c.nav(sys.argv[2])
    elif cmd == "eval":
        r = c.ev(sys.argv[2], await_promise=len(sys.argv) > 3)
        print(json.dumps(r.get("result", {}).get("value", r), default=str)[:2000])
    elif cmd == "shot":
        c.shot(sys.argv[2] if len(sys.argv) > 2 else "/tmp/opencode/frame.png")
    elif cmd == "sleep":
        time.sleep(float(sys.argv[2]))

if __name__ == "__main__":
    main()
