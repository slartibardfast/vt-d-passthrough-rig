#!/usr/bin/env bash
# Boot XP x64 through CSMWrap, log in, and capture the desktop.
#
# Reaching the logon screen only proves the loader and kernel started. The working
# SeaBIOS path reaches a usable desktop, and only a usable desktop makes the driver
# install worth attempting, so this drives the whole way through and captures the
# result for reading.
#
# The keystrokes are typed with typepass.py, which drains the monitor socket before
# sending. That matters: the QEMU monitor echoes each character as it is typed, so a
# writer that does not wait sends into a half-read prompt and the guest sees nothing,
# which is indistinguishable from a guest that ignored the input.
#
# Usage: probe-login.sh [settle_seconds]
set -uo pipefail

RIG=/home/dconnolly/xp64-rig
SETTLE=${1:-200}

cd "$RIG"
sudo -n fuser -k "$RIG/xp64-gpu.raw" >/dev/null 2>&1 || true
sleep 1
rm -f "$RIG/mon.sock"

# EXTRA_QEMU is honoured so a sweep can vary exactly one thing. The default is the
# two-processor configuration that boots; override it to test another count.
# The VM is launched directly rather than through probe-freeze.sh. That script drives
# the QEMU monitor on its own settle timer, and the monitor takes a single client, so
# running it in the background while this script logs in had the two of them fight over
# mon.sock. Whichever lost had its commands dropped, which is why the logon
# intermittently failed before the guest ever saw a keystroke.
# TAG must be set even though it only names files: the script runs under `set -u`, and
# referencing an unset TAG aborts it before QEMU is ever launched.
TAG=${TAG:-login}
# shellcheck disable=SC2086
EXTRA_QEMU=${EXTRA_QEMU:--smp 2} "$RIG/run-gpu-vm.sh" >"$RIG/$TAG-qemu.log" 2>&1 &
QEMU_PID=$!

for _ in $(seq 1 60); do
  [ -S "$RIG/mon.sock" ] && break
  sleep 1
done

echo "== settled ${SETTLE}s; capturing the logon screen =="
sleep 5

shot() {
  python3 - "$1" <<'PY'
import socket, sys, time
SOCK = "/home/dconnolly/xp64-rig/mon.sock"
out = sys.argv[1]
def drain(s, q=1.2, l=15.0):
    buf = b""; st = la = time.time()
    while time.time() - st < l:
        s.settimeout(0.4)
        try:
            d = s.recv(65536)
        except socket.timeout:
            if time.time() - la > q:
                break
            continue
        if not d:
            break
        buf += d; la = time.time()
    return buf
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(SOCK); drain(s)
s.sendall(("screendump %s\n" % out).encode()); time.sleep(1.5); drain(s); s.close()
print("  wrote", out)
PY
}

shot "$RIG/login-0-logon.ppm"

echo "== logging in =="
# logon.py clicks the Administrator tile before typing. Typing alone leaves the
# keystrokes in an unfocused screen, which looks exactly like a guest that ignored
# them, and that cost a whole run before it was written.
python3 "$RIG/logon.py" 2>&1 | tail -4 | sed 's/^/  /'

echo "== waiting for the desktop =="
sleep 45
shot "$RIG/login-1-desktop.ppm"
sleep 20
shot "$RIG/login-2-desktop.ppm"

for f in login-0-logon login-1-desktop login-2-desktop; do
  [ -f "$RIG/$f.ppm" ] && python3 "$RIG/ppm2png.py" "$RIG/$f.ppm" "$RIG/$f.png" 2
done

echo
echo "== hashes: identical 1 and 2 means the desktop is settled =="
sha256sum "$RIG"/login-*.ppm 2>/dev/null | cut -c1-20 | sed 's/^/  /'