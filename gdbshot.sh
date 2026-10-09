#!/usr/bin/env bash
# Attach gdb to the running VM's gdbstub and print what each vCPU is doing.
#
# QEMU's gdbstub is started from the monitor with `gdbserver tcp::1234`, which
# means the debugger can attach to a VM that is already hung and preserve the
# state that made the diagnosis possible in the first place. Restarting to get a
# stub would throw that away.
#
# The stub exposes one gdb "thread" per vCPU, so the halted BSP and the dead
# reserved processor are visible in the same session. Without that, reading only
# the BSP looks like a machine with one slow processor rather than one that lost
# a processor.
#
# The stub is single-threaded and stops the VM while attached, so every attach
# pauses the guest. That is deliberate here: the point is to inspect a stopped
# machine, and detaching resumes it.
#
# Usage: gdbshot.sh [gdb command ...]
set -uo pipefail

PORT=${PORT:-1234}

if [ $# -gt 0 ]; then
  CMDS=("$@")
else
  CMDS=(
    'info threads'
    'info registers'
    'info registers rip rsp cr0 cr3 cr4 efer rflags'
    'x/32gx $rsp'
    'info all-registers rip'
  )
fi

{
  printf 'set pagination off\n'
  printf 'set confirm off\n'
  printf 'target remote :%s\n' "$PORT"
  for c in "${CMDS[@]}"; do
    printf 'echo \\n===== %s =====\\n\n' "$c"
    printf '%s\n' "$c"
  done
  printf 'detach\n'
  printf 'quit\n'
} | gdb -q -nx -batch 2>&1 | grep -vE '^\[|^warning:|^$'