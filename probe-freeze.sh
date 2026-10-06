#!/usr/bin/env bash
# Localise where Windows XP x64's loader stops under CSMWrap.
#
# The screen freezes somewhere in the NT loader's hardware enumeration and nothing
# on the serial log says where, because SeaBIOS is built at CONFIG_DEBUG_LEVEL=1
# and XP writes nothing to serial. So this freezes the machine at the hang and asks
# the monitor what the CPU is actually doing: the last RIP, the faulting address in
# CR2, and whether we are spinning, halted, or wedged in a triple fault.
#
# Two things make a previous attempt unreliable and are handled here. The QEMU
# monitor echoes every character as it is typed, so mon.py's read stops mid-echo and
# truncates; this drains the socket until it goes quiet, as monq.py does. And a
# screendump target in single quotes keeps the shell variable literal, so the dumps
# are named by this script rather than by QEMU.
#
# Usage: probe-freeze.sh [settle_seconds] [samples]
# EXTRA_QEMU holds extra QEMU arguments, word-split on purpose so that a sweep can
# vary one flag at a time (for example EXTRA_QEMU="-smp 1").
set -uo pipefail

RIG=/home/dconnolly/xp64-rig
MON=$RIG/mon.sock
TAG=${TAG:-probe}
SETTLE=${1:-150}
SAMPLES=${2:-6}
EXTRA_QEMU=${EXTRA_QEMU:-}

# run-gpu-vm.sh hardcodes its own -serial target, so the log lands at this path
# whatever TAG says. Copying it under the tag keeps each run's log with its dumps.
SERIAL=$RIG/csmwrap-serial.log

cd "$RIG"

# A stale QEMU holds the image lock, and a failed start then reads as a disk fault.
sudo -n fuser -k "$RIG/xp64-gpu.raw" >/dev/null 2>&1 || true
sleep 1
rm -f "$MON"

# EXTRA_QEMU goes last so it overrides run-gpu-vm.sh's own settings, which is what
# lets one flag at a time be varied against an otherwise identical machine.
# shellcheck disable=SC2086
"$RIG/run-gpu-vm.sh" $EXTRA_QEMU >"$RIG/$TAG-stdout.log" 2>&1 &
QEMU_PID=$!

# Wait for the monitor socket; without it every later step is guesswork.
for _ in $(seq 1 40); do
  [ -S "$MON" ] && break
  sleep 0.5
done
if [ ! -S "$MON" ]; then
  echo "monitor socket never appeared; QEMU said:" >&2
  cat "$RIG/$TAG-stdout.log" >&2
  kill "$QEMU_PID" 2>/dev/null
  exit 1
fi

# mon is a shell function rather than a call to monq.py so the drain happens on a
# socket that has already been drained once, which is what keeps the echo out.
mon() {
  python3 - "$@" <<'PY'
import socket, sys, time
SOCK = "/home/dconnolly/xp64-rig/mon.sock"

def drain(s, quiet=1.2, limit=15.0):
    buf = b""; start = last = time.time()
    while time.time() - start < limit:
        s.settimeout(0.4)
        try:
            d = s.recv(65536)
        except socket.timeout:
            if time.time() - last > quiet:
                break
            continue
        if not d:
            break
        buf += d; last = time.time()
    return buf

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.connect(SOCK)
drain(s)
out = []
for c in sys.argv[1:]:
    s.sendall((c + "\n").encode())
    time.sleep(0.6)
    out.append("===== %s =====\n" % c + drain(s).decode(errors="replace"))
s.close()
print("".join(out), end="")
PY
}

echo "== settling ${SETTLE}s before sampling =="
sleep "$SETTLE"

# Sample the framebuffer until it stops changing. Identical consecutive dumps mean
# the guest has stopped repainting, which is the definition of frozen here.
prev=""
for i in $(seq 1 "$SAMPLES"); do
  mon "screendump $RIG/$TAG-$i.ppm" >/dev/null 2>&1
  sleep 6
  h=$(sha256sum "$RIG/$TAG-$i.ppm" 2>/dev/null | cut -c1-16)
  echo "  sample $i: $h"
  if [ "$h" = "$prev" ] && [ -n "$prev" ]; then
    echo "  framebuffer stable across two samples; treating as frozen"
    break
  fi
  prev="$h"
done

echo
echo "== per-CPU state at the freeze =="
# Every CPU is read, not just the BSP. A vCPU that triple-faulted is reset by QEMU
# and shows CR0=0x11 with CR3/CR4/EFER zero and no RIP at all, and that is the
# difference between "the OS is spinning" and "the OS lost a CPU mid-bring-up".
for n in 0 1 2 3; do
  mon "cpu $n" >/dev/null
  echo "  -- CPU$n"
  mon "info registers" | grep -E 'RIP=|RSP=|CR0=|CR3=|CR4=|EFER=|HLT=|SMM=|CPL=' | sed 's/^/    /'
done

echo
echo "== disassembly at the BSP's RIP =="
mon "cpu 0" >/dev/null
RIPV=$(mon "info registers" | grep -oE 'RIP=[0-9a-f]+' | head -1 | cut -d= -f2)
if [ -n "$RIPV" ]; then
  echo "  RIP = $RIPV"
  # The monitor's expression parser wants the 0x form; a bare address reads as an
  # undefined symbol and answers "invalid char 'f' in expression".
  mon "x/12i 0x$RIPV" | grep -E '^\s+0x' | sed 's/^/  /'
else
  echo "  no RIP: the BSP itself is in reset state"
fi

echo
echo "== serial log tail =="
cp -f "$SERIAL" "$RIG/$TAG-serial.log" 2>/dev/null || true
tail -6 "$RIG/$TAG-serial.log" 2>/dev/null | cat -v | sed 's/^/  /'

echo
echo "== leaving the VM running for follow-up; kill with: sudo fuser -k xp64-gpu.raw =="
echo "   PID $QEMU_PID"