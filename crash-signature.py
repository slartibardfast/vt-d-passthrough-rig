#!/usr/bin/env python3
"""Report the kernel's own crash signature from a collector log, if it holds one.

The run contract inferred a crash from how the run *ended*, so a guest that double-faulted inside
the display driver and rebooted before the hold expired read as `measured-shutdown` with
`crash_suspected=false`. Two boots of this campaign did exactly that while their collector log held
`*** Fatal System Error: 0x0000007F`. The kernel's last words on the debugger wire are the better
witness, and this prints them so the runner can record them.

usage: crash-signature.py <kdc log>
Prints one line, for example `0x0000007F double-fault`, and exits 0. Prints nothing and exits 1
when the log holds no crash.
"""
import re
import sys

# The bugcheck codes this rig has actually seen, with what the first parameter means for each. A code
# not in this table is still a crash and is reported by number, because a table that decides whether
# something is a crash is a table that can lose one.
CODE_NAMES = {
    "0x0000007F": "unexpected-kernel-mode-trap",
    "0x000000D1": "driver-irql-not-less-or-equal",
    "0x0000000A": "irql-not-less-or-equal",
    "0x0000001E": "kmode-exception-not-handled",
    "0x0000003B": "system-service-exception",
    "0x00000050": "page-fault-in-nonpaged-area",
    "0x0000007E": "system-thread-exception-not-handled",
    "0x0000008E": "kernel-mode-exception-not-handled",
    "0x0000009F": "driver-power-state-failure",
    "0x000000C2": "bad-pool-caller",
    "0x000000C5": "driver-corrupted-process",
    "0x000000CE": "driver-unloaded-without-cancelling",
    "0x000000D5": "driver-page-fault-in-freed-special-pool",
    "0x000000EA": "thread-stuck-in-device-driver",
}
TRAP_PARAM0 = {"0x00000008": "double-fault"}

FATAL = re.compile(r"Fatal System Error:\s*(0x[0-9A-Fa-f]{8})")
BUGCHECK = re.compile(r"\bBugCheck\s+(0x[0-9A-Fa-f]{8})", re.I)
WATCHDOG = re.compile(r"Watchdog: Timeout in ([\w.]+)")


def signature(text):
    """`(code, name, extra)` for the first crash in the log, or None."""
    m = FATAL.search(text) or BUGCHECK.search(text)
    if not m:
        return None
    for entry in signatures(text):
        code, _, rest = entry.partition(" ")
        name, _, extra = rest.partition(" ")
        return code, name, " " + extra if extra else ""
    return None


def watchdog_driver(text):
    """The driver win32k's watchdog timed out in, if it did. A hang is not a bugcheck, and both
    matter: one says the driver never returned, the other says it returned into a fault."""
    m = WATCHDOG.search(text)
    return m.group(1) if m else None


def signatures(text):
    """Every distinct crash code in the log, in the order each first appears, with its trap if any."""
    out, seen = [], set()
    for m in list(FATAL.finditer(text)) + list(BUGCHECK.finditer(text)):
        code = "0x%08X" % int(m.group(1), 16)
        if code in seen:
            continue
        seen.add(code)
        name = CODE_NAMES.get(code, "bugcheck")
        extra = ""
        if code == "0x0000007F":
            after = text[m.end():m.end() + 120]
            param = re.search(r"(0x[0-9A-Fa-f]{16})", after)
            if param and TRAP_PARAM0.get("0x%08X" % int(param.group(1), 16)):
                extra = " " + TRAP_PARAM0["0x%08X" % int(param.group(1), 16)]
        out.append("%s %s%s" % (code, name, extra))
    return out


def describe(text):
    bits = signatures(text)
    hang = watchdog_driver(text)
    if not bits and hang is None:
        return None
    if hang:
        bits.append("watchdog-timeout-in-%s" % hang)
    return "; ".join(bits)


def main(argv):
    if len(argv) != 2:
        print("usage: crash-signature.py <kdc log>", file=sys.stderr)
        return 2
    try:
        text = open(argv[1], "r", errors="replace").read()
    except OSError as exc:
        print("cannot read %s: %s" % (argv[1], exc), file=sys.stderr)
        return 2
    line = describe(text)
    if line is None:
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
