"""The one rule about drive letters in the install channel, in one place.

A staged batch file may name the system drive and no other volume letter, because
every other letter is decided by Windows at boot from whatever else is attached.
Two shapes must not be counted as letters, and both were mis-counted the first
time this was written:

  - `%%d:\\...`, a loop variable that stands for whichever letter is being probed;
  - a remark that recounts the letter the rig used to assume.

The shell tool that stages the volume and the test suite that holds it both call
this, so the check cannot quietly become two different rules.
"""
import re

SYSTEM_DRIVE = "C"

LETTER_REF = re.compile(r"(?<![A-Za-z0-9%])[A-Za-z]:\\")

BATCH_HEADS = {"@echo", "rem", "set", "for", "if", "copy", "regedit", "shutdown", "start",
               "call", "goto", "echo", "type", "reg", "dir", "vol", "ping", "fsutil", "wmic",
               # Added with the live captures: the hook starts the WMI service and asks the
               # service control manager for the miniport's state. Both are commands, and a linter
               # that freezes its command list turns a legitimate line into prose.
               "sc", "net", "setlocal", "endlocal", "pushd", "popd", "del", "move", "mkdir",
               # Added with the dump analysis: the hook changes to the debug directory and runs
               # the guest's own debugger inside an `if exist` block. Both are commands, and the
               # block's closing paren is structure rather than prose.
               "cd", "cdb.exe"}


def _lines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def offending_letters(text):
    """Drive-letter references outside remarks, excluding the system drive."""
    found = set()
    for line in _lines(text):
        if not line.strip() or line.strip().lower().startswith("rem"):
            continue
        for match in LETTER_REF.finditer(line):
            if not match.group(0).startswith(SYSTEM_DRIVE + ":\\"):
                found.add(match.group(0))
    return sorted(found)


def lines_that_are_not_batch(text):
    """Lines that are neither a command, a remark, a label, nor an expansion."""
    bad = []
    for number, line in enumerate(_lines(text), 1):
        text_stripped = line.strip()
        if not text_stripped or text_stripped.startswith((":", "%")):
            continue
        if text_stripped in ("(", ")"):
            continue
        head = text_stripped.split(" ")[0].lower().rstrip(":")
        if head not in BATCH_HEADS:
            bad.append((number, text_stripped[:60]))
    return bad


def letters_named_anywhere(text):
    """Every letter reference, remarks included: for reporting, not for gating."""
    return sorted({m.group(0) for m in LETTER_REF.finditer(text)})


def main(argv):
    """Lint the named batch files: exit non-zero if any names a boot-chosen letter."""
    if len(argv) < 2:
        print("usage: channel_lint.py <file.bat> [...]", file=__import__("sys").stderr)
        return 2
    bad = 0
    for path in argv[1:]:
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8", "replace")
        offenders = offending_letters(text)
        prose = lines_that_are_not_batch(text)
        if offenders or prose:
            bad = 1
        for offence in offenders:
            print("%s: names a volume letter the guest picks at boot: %s" % (path, offence))
        for number, line in prose:
            print("%s:%d: not a command, a remark, or a label: %r" % (path, number, line))
        if not offenders and not prose:
            print("%s: ok" % path)
    return bad


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
