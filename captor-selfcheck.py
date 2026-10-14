#!/usr/bin/env python3
"""captor-selfcheck.py - plan/0006 #assert-captor's fixture test.

Pushes a real captured state-change payload through the captor's resolver
and asserts the module naming. Exit 0 = pass.
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kdc

# The fixture: the context header layout of a real state change seen this
# campaign - exception code 0x80000003 (int3) at data+0x20, the exception
# address at data+0x30, and the boot breakpoint's address in ntoskrnl.
NTBASE = 0xfffff80001000000
BOOT_BP = NTBASE + 0x18D500
data = bytearray(0x38)
struct.pack_into("<I", data, 0x20, 0x80000003)
struct.pack_into("<Q", data, 0x30, BOOT_BP)

mods = [
    (0xfffffadfacbfb000, "\\WINDOWS\\system32\\KDCOM.DLL"),
    (0xfffffadfaaf34000, "\\WINDOWS\\system32\\drivers\\ati2mtag.sys"),
]


class FakeKd:
    ntbase = NTBASE


kd = FakeKd()

# 1: the resolver names the nt offset
r1 = kdc.resolve_addr(NTBASE, mods, BOOT_BP)
assert r1 == "ntoskrnl+0x18d500", r1
print("  resolve nt boot bp:      %s" % r1)

# 2: the resolver names a module hit
a2 = 0xfffffadfaaf34000 + 0x1234
r2 = kdc.resolve_addr(None, mods, a2)
assert r2 == "ati2mtag.sys+0x1234", r2
print("  resolve ati2mtag hit:    %s" % r2)

# 3: the resolver names the kdcom hit (the wait-loop address measured this
# session - the accursed 'freeze' RIP was kdcom's own wait loop)
a3 = 0xfffffadfacbfb000 + 0x18d4
r3 = kdc.resolve_addr(None, mods, a3)
assert r3 == "KDCOM.DLL+0x18d4", r3
print("  resolve kdcom wait loop: %s" % r3)

# 4: captor_catch writes the catch line (to a temp file, not the rig's log)
kdc.RIG = "/tmp/opencode/captor-selfcheck"
os.makedirs(os.path.join(kdc.RIG, "work"), exist_ok=True)
if os.path.exists(os.path.join(kdc.RIG, "work", "captor-catches.log")):
    os.remove(os.path.join(kdc.RIG, "work", "captor-catches.log"))
kdc.captor_catch(kd, 0x3030, 0x80800000, bytes(data))
line = open(os.path.join(kdc.RIG, "work", "captor-catches.log")).read().strip()
assert "exc=0x80000003" in line and "ntoskrnl+0x18d500" in line, line
print("  catch line:              %s" % line)

# 5: a short payload is ignored without raising
kdc.captor_catch(kd, 0x3030, 0x80800000, b"\x00" * 16)
print("  short payload ignored:   ok")

print("  ALL CAPTOR CHECKS PASS")
