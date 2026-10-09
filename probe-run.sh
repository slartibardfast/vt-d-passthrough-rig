#!/usr/bin/env bash
# Boot one point of the 2x2 (firmware x console) and measure it identically.
#
# The problem this exists to solve: every measurement so far was taken downstream of
# CSMWrap, so "CSMWrap broke it" and "XP x64 will not run in this VM at all" could
# not be told apart. Each corner here is measured by this one script, so the corners
# are comparable.
#
# Two axes, and only two:
#
#   firmware   CSMWrap+OVMF (two if=pflash drives)  |  stock SeaBIOS (no pflash)
#   console    the passed-through 290X               |  -vga std
#
#   corner            firmware   console    question it answers
#   ----------------  ---------  ---------  -----------------------------------
#   csmwrap-card      CSMWrap    card       the failing configuration
#   csmwrap-stdvga    CSMWrap    std VGA    firmware alone
#   seabios-card      SeaBIOS    card       console alone
#   seabios-stdvga    SeaBIOS    std VGA    can XP x64 boot here at all?
#
# Everything else is byte-identical to the failing config in run-install.sh: the
# machine, cpu, smp, memory, hpet, -nodefaults, the USB controller and devices,
# if=ide for disk and cdrom, the floppy, the NIC, the same stock ISO, the same
# answer floppy and the same card ROM. Do not add a flag here without recording why,
# because the value of each corner is that its only difference is its axis.
#
# Usage: probe-run.sh <corner> [--keep] [--no-gdb]
#   --keep     leave the VM running at the end (for interactive poking)
#   --no-gdb   skip the gdbstub phase (used when the guest never reaches long mode,
#              where there is nothing to read)
set -uo pipefail

RIG=/home/dconnolly/xp64-rig
ISO=/home/dconnolly/xp64-corpus/iso/AX2PXVOL_EN.iso
CORNER=${1:?usage: probe-run.sh <corner> [--keep] [--no-gdb]}
shift || true

KEEP=0
USE_GDB=1
GDB_OK=-1
TRACE=0
for a in "$@"; do
  case "$a" in
    --keep) KEEP=1 ;;
    --no-gdb) USE_GDB=0 ;;
    --trace) TRACE=1 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

case "$CORNER" in
  csmwrap-card|csmwrap-stdvga|seabios-card|seabios-stdvga) ;;
  *) echo "unknown corner: $CORNER" >&2
     echo "valid: csmwrap-card csmwrap-stdvga seabios-card seabios-stdvga" >&2
     exit 2 ;;
esac

case "$CORNER" in
  csmwrap-*)  FIRMWARE=csmwrap ;;
  seabios-*)  FIRMWARE=seabios ;;
esac
case "$CORNER" in
  *-card)     CONSOLE=card ;;
  *-stdvga)   CONSOLE=stdvga ;;
esac

# Per-run scratch, so runs never contaminate each other.
# Per-run scratch. The disk copy MUST sit on the same filesystem as the source:
# /tmp/opencode is a different mount, and `cp --reflink` fails across devices
# ("Invalid cross-device link"), which is exactly the failure this comment prevents.
RUN=$RIG/probe/$CORNER
rm -rf "$RUN"; mkdir -p "$RUN"
DISK="$RUN/target.raw"
LOG="$RUN/serial.log"
MON="$RUN/mon.sock"

for f in "$ISO" "$RIG/xp64-ansfloppy.img" "$RIG/290x-vbios.rom"; do
  [ -f "$f" ] || { echo "missing input: $f" >&2; exit 1; }
done

# A fresh target per run. Reflink keeps this at ~0 cost on xfs; setup rewrites the
# MBR, so sharing one disk across corners would confound every run after the first.
cp --reflink=always "$RIG/xp64-scratch.raw" "$DISK" || exit 1

ARGS=(
  -name "probe-$CORNER"
  -machine pc,accel=kvm,hpet=off
  -cpu host,-x2apic
  -smp 2
  -m 4096
  -rtc base=localtime
  -nodefaults
  -display none
  -device piix3-usb-uhci,id=usb,addr=0x5
  -device usb-kbd,bus=usb.0
  -device usb-tablet,bus=usb.0
  -monitor "unix:$MON,server,nowait"
  -serial "file:$LOG"
  -drive file="$DISK",format=raw,if=ide,index=0,media=disk
  -drive file="$ISO",format=raw,if=ide,index=2,media=cdrom
  -drive file="$RIG/xp64-ansfloppy.img",format=raw,if=floppy,index=0
  -netdev user,id=net0
  -device e1000,netdev=net0,bus=pci.0,addr=0x3
  -device "vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,romfile=$RIG/290x-vbios.rom"
  # SeaBIOS has no serial backend of its own; with no CSM in the corner nothing
  # would report anything at all, so every corner gets a debugcon on the same
  # serial file. Stock SeaBIOS writes its trace there when -debugcon is given.
  -chardev file,id=seabiosdbg,path="$LOG"
  -device isa-debugcon,iobase=0x402,chardev=seabiosdbg
)

# Full CPU execution trace. `-d exec,nochain` prints every executed translation
# block with chaining disabled, so the log is the instruction path rather than a
# set of jump targets; `int` adds exceptions and interrupts. This is the
# instrument that answers "what did the kernel actually do", where sampling only
# answers "where is it now". The log is large, so it is opt-in and written to the
# run directory rather than to stdout.
if [ "$TRACE" = 1 ]; then
  ARGS+=( -d "exec,nochain,int,cpu" -D "$RUN/qemu-trace.log" )
  echo "  trace: exec,nochain,int,cpu -> $RUN/qemu-trace.log"
fi

# AXIS 1: firmware
if [ "$FIRMWARE" = csmwrap ]; then
  ARGS+=(
    -drive if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/edk2/x64/OVMF_CODE.4m.fd
    -drive if=pflash,format=raw,unit=1,file="$RUN/OVMF_VARS.fd"
  )
  cp -f /usr/share/edk2/x64/OVMF_VARS.4m.fd "$RUN/OVMF_VARS.fd"
fi

# AXIS 2: console
if [ "$CONSOLE" = stdvga ]; then
  ARGS+=( -vga std )
fi

echo "=== corner $CORNER : firmware=$FIRMWARE console=$CONSOLE ==="
echo "  disk: $DISK"
echo "  log : $LOG"

# The 2x2's premise is that the card works, so refuse to run on an occupied device
# rather than silently measuring a degraded run.
if sudo -n fuser -v "$DISK" 2>&1 | grep -q qemu; then
  echo "  refusing: another VM holds $DISK" >&2; exit 1
fi

qemu-system-x86_64 "${ARGS[@]}" > "$RUN/qemu-stdout.log" 2>&1 &
QEMU_PID=$!

# Without this, any exit after the launch - a failed gdb phase, a Ctrl-C, an
# unexpected error - leaves the VM holding the vfio device, and the next run then
# either fails to take the card or silently measures a degraded machine. That trap
# was hit by hand once already this session. Same location, same job as the rest of
# the rig's cleanup: the monitor socket first so the guest gets to power down, then
# the process. --keep is the one way out, and it says so where it is honoured.
cleanup() {
  local rc=$?
  [ "$KEEP" = 1 ] && return $rc
  python3 - "$MON" <<'PY' 2>/dev/null
import socket, sys
try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.settimeout(8)
    s.connect(sys.argv[1]); s.recv(65536); s.sendall(b"quit\n"); s.close()
except Exception:
    pass
PY
  sleep 3
  if kill -0 "$QEMU_PID" 2>/dev/null; then
    kill "$QEMU_PID" 2>/dev/null
    sleep 2
    kill -0 "$QEMU_PID" 2>/dev/null && kill -9 "$QEMU_PID" 2>/dev/null
  fi
  return $rc
}
trap cleanup EXIT INT TERM

sleep 8
if ! kill -0 "$QEMU_PID" 2>/dev/null; then
  echo "  VM exited during startup:"
  sed 's/^/    /' "$RUN/qemu-stdout.log"
  exit 1
fi
echo "  qemu pid $QEMU_PID"

# Sampling: five short-lived monitor queries via qmon.py, one process each.
# No backgrounded sampler and no long-lived connection, which is what made the
# earlier version hang with an empty log and an alive PID.
SAMPLES="$RUN/samples.json"
: > "$RUN/samples.tsv"
echo "  sampling: 5 rounds of (registers, lapic)"
for i in 0 1 2 3 4; do
  sleep 15
  QMON="$RIG/qmon.py"
  python3 "$QMON" "$MON" "info registers" 2.0 >> "$RUN/registers.txt"
  python3 "$QMON" "$MON" "info lapic"      2.0 >> "$RUN/lapic.txt"
  printf 'round %d done\n' "$i" >&2
done

python3 - "$RUN" <<'SAMPLEEOF'
import glob, json, os, re, sys
run = sys.argv[1]

def grab(path, pattern, cast=lambda m: m.group(1)):
    try:
        text = open(path, errors="replace").read()
    except OSError:
        return None
    m = re.search(pattern, text)
    return cast(m) if m else None

# The monitor echoes each command back character by character, so a naive regex
# can match the echo. Take the LAST match in the file, which is a real reply.
def last(path, pattern, cast=lambda m: m.group(1)):
    try:
        text = open(path, errors="replace").read()
    except OSError:
        return None
    hits = re.findall(pattern, text)
    return cast(hits[-1]) if hits else None

regs = open(f"{run}/registers.txt", errors="replace").read()
lapic = open(f"{run}/lapic.txt", errors="replace").read()

rounds = []
# registers.txt holds 5 responses back to back; split on the echoed command.
chunks = re.split(r"info registers", regs)[1:]
for i, chunk in enumerate(chunks):
    rip = re.search(r"\brip=([0-9a-f]+)", chunk)
    rfl = re.search(r"\brflags=([0-9a-f]+)", chunk)
    cr2 = re.search(r"\bcr2=([0-9a-f]+)", chunk)
    rounds.append({
        "round": i,
        "rip": int(rip.group(1), 16) if rip else 0,
        "rflags": int(rfl.group(1), 16) if rfl else 0,
        "if_enabled": bool((int(rfl.group(1), 16) >> 9) & 1) if rfl else False,
        "cr2": int(cr2.group(1), 16) if cr2 else 0,
    })

lchunks = re.split(r"info lapic", lapic)[1:]
for i, chunk in enumerate(lchunks[:len(rounds)]):
    rounds[i]["lapic_irr"] = "none" if re.search(r"IRR\s+\(none\)", chunk) else "pending"
    rounds[i]["lapic_isr"] = "none" if re.search(r"ISR\s+\(none\)", chunk) else "servicing"
    rounds[i]["lapic_lvtt_masked"] = bool(re.search(r"LVTT.*masked", chunk))

json.dump(rounds, open(f"{run}/samples.json", "w"), indent=1)
print(f"  sampled {len(rounds)} rounds", file=sys.stderr)
SAMPLEEOF

# Start the gdbstub so the gdb phase can attach and dump guest memory.
if [ "$USE_GDB" = 1 ]; then
  python3 "$RIG/qmon.py" "$MON" "gdbserver tcp::1234" 2.0 >/dev/null 2>&1
fi

# The gdb phase reads guest memory the monitor cannot (xp fails on this guest in long
# mode), so attach through the stub for the dump work.
if [ "$USE_GDB" = 1 ]; then
  sleep 5
  timeout 120 gdb -batch \
    -ex 'set pagination off' -ex 'set confirm off' \
    -ex 'target remote :1234' -ex 'set architecture i386:x86-64' \
    -ex "dump memory $RUN/lowmem.bin 0x0 0x400000" \
    -ex 'info registers rip rsp rflags cr0 cr2 cr3' \
    -ex 'detach' > "$RUN/gdb.log" 2>&1
  if grep -qE 'could not connect|Connection (refused|timed out)' "$RUN/gdb.log"; then
    GDB_OK=0
    echo "  gdb phase: FAILED to connect -> $RUN/gdb.log"
    echo "    (an unconnected debugger must not be read as a guest fact)"
  else
    GDB_OK=1
    echo "  gdb phase: ok -> $RUN/gdb.log"
  fi
else
  GDB_OK=-1
  echo "  gdb phase: skipped (--no-gdb)"
fi

# Verdict metrics: did the kernel start at all, and did setup write anything?
SERIAL_BYTES=$(wc -c < "$LOG" 2>/dev/null || echo 0)
DISK_NONZERO=$(sudo -n python3 - "$DISK" <<'PY'
import sys
with open(sys.argv[1],'rb') as f:
    f.seek(41943040)          # p1 region: where setup would create its partition
    d=f.read(60*1024*1024)
print(sum(1 for b in d if b))
PY
)

summarise_samples() {
  python3 - "$1" <<'PYEOF'
import json,sys
s=json.load(open(sys.argv[1]))
rips=[x.get("rip",0) for x in s]
lo,hi=min(rips),max(rips)
print(f"  samples      : {len(s)}")
print(f"  RIP range    : 0x{lo:x} .. 0x{hi:x}")
print(f"  RIP constant : {lo==hi}")
if lo==0 and hi==0:      print("  -> RIP is zero: the CPU was at the reset vector, not in any OS")
elif lo>>48==0xfffff800: print("  -> in the XP kernel (fffff800...)")
elif lo>>48==0xffff0000: print("  -> in the kernel proper, pre-handoff (ffff0000...)")
else:                     print(f"  -> top of RIP says 0x{lo>>48:x}: real mode or firmware")
if len(set(rips))>1: print("  -> RIP moves between samples: still progressing or looping")
print(f"  IF enabled   : {[x['if_enabled'] for x in s]}")
print(f"  LAPIC IRR    : {[x['lapic_irr'] for x in s]}")
print(f"  LAPIC ISR    : {[x['lapic_isr'] for x in s]}")
print(f"  LVTT masked  : {[x['lapic_lvtt_masked'] for x in s]}")
PYEOF
}

print_verdict() {
  echo "=== VERDICT: $CORNER (firmware=$FIRMWARE console=$CONSOLE) ==="
  if [ "$GDB_OK" -lt 0 ]; then
    echo "  GUEST STATE: not measured (gdb phase skipped) - no claim is made here."
  elif [ "$GDB_OK" -eq 0 ]; then
    echo "  GUEST STATE: NOT MEASURED. The gdbstub did not answer, so RIP, RFLAGS,"
    echo "               CR2 and the LAPIC lines below are NOT evidence about the"
    echo "               guest. Treat this run as 'no observation', not as a fault."
  else
    summarise_samples "$RUN/samples.json"
  fi
  echo "  disk p1 nonzero bytes: $DISK_NONZERO   (setup's first write; 0 means it never began)"
  echo "  --- serial log ($SERIAL_BYTES bytes) ---"
  if [ "$SERIAL_BYTES" -gt 0 ]; then
    grep -nE 'Booting from|set VGA mode|Found FB|GOP mode|SeaVGABIOS|triple|halting|Starting' \
      "$LOG" 2>/dev/null | tail -12 | sed 's/^/    /'
  else
    echo "    (empty: stock SeaBIOS ships with CONFIG_DEBUG_LEVEL=0 and writes no trace"
    echo "     to port 0x402 or serial, and the guest speaks neither. A silent log is"
    echo "     expected in the seabios corners and says nothing on its own.)"
  fi
}

print_verdict | tee "$RUN/verdict.txt"

if [ "$KEEP" = 1 ]; then
  echo "  left running: qemu pid $QEMU_PID"
  echo "    monitor : $MON   (single-client; one connection at a time)"
  echo "    gdbstub : tcp::1234 (gdbserver is issued by this harness)"
  echo "    disk    : $DISK"
  echo "    release : printf 'quit\\n' | socat - UNIX-CONNECT:$MON"
  exit 0
fi

# Everything else falls through to the EXIT trap, which owns the shutdown.
echo "  done: $RUN/verdict.txt"
