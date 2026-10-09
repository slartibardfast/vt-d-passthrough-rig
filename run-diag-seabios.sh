#!/usr/bin/env bash
set -euo pipefail
RIG=/home/dconnolly/xp64-rig
cd "$RIG"
exec qemu-system-x86_64 \
  -name xp-diag -machine pc,accel=kvm -cpu host,-x2apic -smp 4 -m 4096 \
  -rtc base=localtime -nodefaults -display none \
  -monitor unix:"$RIG/mon.sock",server,nowait \
  -serial file:"$RIG/diag-serial.log" \
  -bios "$RIG/SeaBIOS-256k.bin" \
  -drive file="$RIG/xp64-gpu.raw",format=raw,if=ide,index=0,media=disk \
  -drive file="$RIG/gpudrv.img",format=raw,if=ide,index=1 \
  -netdev user,id=net0 -device e1000,netdev=net0,bus=pci.0,addr=0x3 \
  -device vfio-pci,host=02:00.0,id=radeon290x,bus=pci.0,addr=0x4,\
rombar=0 \
  -boot order=c,menu=off "$@"
