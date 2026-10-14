#!/usr/bin/env python3
# kdc.py - minimal KD64 debugger client for the XP x64 rig.
#
# Connects directly to QEMU's KD serial unix socket (run-driver.sh serves it at
# /tmp/opencode/kd.sock) and speaks the wire protocol the target speaks, which the
# captured packets in ptyrelay.log pinned down exactly:
#
#   header 16 bytes: leader(4) type(2) bytecount(2) packetid(4) checksum(4)
#   checksum       : sum(data) & 0xFFFF, 0 for control packets
#   data leader    : 0x30303030   control leader: 0x69696969
#   STATE_CHANGE64 : type 7, NewState 0x3031 = load symbols (measured, packet #0)
#   ACK            : control, type 4, bytecount 0, echoes the target's packet id
#
# Behaviour: ACKs every valid packet; continues module-load breaks (logging name +
# base, which is how the rig learns ati2mtag.sys's load address); HOLDS on any
# other state change (a bugcheck under the debugger arrives as one) and dumps the
# full payload, freezing the machine with its state intact for offline analysis.
# The hold is released by touching hold-release in the rig directory.
#
# Everything is logged to kdc-live.log with full hex dumps.

import os
import re
import socket
import struct
import sys
import threading
import time

RIG  = os.path.dirname(os.path.abspath(__file__))
SOCK = "/tmp/opencode/kd.sock"
LOG  = os.path.join(RIG, "kdc-live.log")
HOLD_RELEASE = os.path.join(RIG, "hold-release")

DATA_LEADER  = 0x30303030
CTRL_LEADER  = 0x69696969
INITIAL_PID  = 0x80800800
POST_RESET_PID = 0x80800000          # measured: the target's id base after a reset
BREAKIN      = b"\x62\x62\x62\x62"   # debugger says hello; target answers with a state change

T_MANIPULATE, T_ACK, T_RESEND, T_RESET, T_STATE_CHANGE64 = 2, 4, 5, 6, 7
STATE_LOAD_SYMBOLS = 0x00003031      # measured from ptyrelay.log packet #0

API_CONTINUE     = 0x00003136
API_CONTINUE2    = 0x0000313C     # the exception-resume variant: carries the control set
API_GET_VERSION  = 0x00003146     # 0x313C is Continue2 - measured law from windbgkd.h
API_READ_VM      = 0x00003130
API_READ_PHYS    = 0x00003139
API_GET_CONTEXT  = 0x00003132
API_SET_CONTEXT  = 0x00003133
MANIPULATE_SIZE  = 0x40           # sizeof(DBGKD_MANIPULATE_STATE64) on XP x64
DBG_CONTINUE     = 0x00010002

logf = open(LOG, "a", buffering=1)

def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    logf.write(line + "\n")
    print(line, flush=True)

class Kd:
    def __init__(self, sock):
        self.s = sock
        self.my_pid = INITIAL_PID
        self.kd_expected = 0x80800000   # kdcom's expected-debugger-id (the ID LAW)
        self.last_tx = None
        self.last_sent_id = None

    def send_control(self, ptype, pid):
        hdr = struct.pack("<IHHII", CTRL_LEADER, ptype, 0, pid, 0)
        self.s.sendall(hdr + TRAILER)
        log("TX control type=%d pid=%#x" % (ptype, pid))

    def send_data(self, ptype, data, pid=None):
        # THE ID LAW (kdcom KdReceivePacket, decoded 2026-10-14): the engine
        # holds expected_id (init 0x80800000, reset to it on RESET), accepts
        # a data packet ONLY when its id EQUALS expected_id, flips it ONLY on
        # acceptance, and NAKs (RESEND type 5) anything else. So: send with
        # kd_expected; never flip on send - the ack handler flips on
        # acceptance.
        pid = self.kd_expected
        # the checksum is the FULL 32-bit sum of the data (kdcom compares a
        # 32-bit int - KdSendPacket stores the untruncated sum; a 16-bit
        # field broke every packet whose sum exceeded 0xffff, measured on
        # the SetContext of the boot-breakpoint step-over)
        hdr = struct.pack("<IHHII", DATA_LEADER, ptype, len(data), pid,
                          (sum(data) & 0xFFFFFFFF) if data else 0)
        self.s.sendall(hdr + data + TRAILER)
        self.last_tx = (ptype, data, pid)
        self.last_sent_id = pid
        log("TX data type=%d len=%d pid=%#x %s" %
            (ptype, len(data), pid, data[:24].hex(" ")))

    def readn(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf += chunk
        return buf

    def read_packet(self):
        hdr = self.readn(16)
        while struct.unpack_from("<I", hdr, 0)[0] not in (DATA_LEADER, CTRL_LEADER):
            log("SYNC skip byte %02x" % hdr[0])
            hdr = hdr[1:] + self.readn(1)
        leader, ptype, bcount, pid, cksum = struct.unpack("<IHHII", hdr)
        self.last_hdr = hdr.hex(" ")
        if leader == CTRL_LEADER and bcount:
            log("RX control with nonzero length %d - desync, dropping" % bcount)
            bcount = 0
        data = self.readn(bcount) if bcount else b""
        return leader, ptype, bcount, pid, cksum, data

    def continue_go(self, action=None):
        # DBGKD_MANIPULATE_STATE64 with u.Continue2. Two meanings share the
        # wire: for an EXCEPTION resume the field is u.ReturnStatus and takes
        # DBG_CONTINUE (measured - the int3 came back verbatim otherwise);
        # for a STATE-CHANGE resume it is u.ContinueAction, a small enum
        # (ContinueNormal=1); 0x10002 there is invalid and kdcom re-breaks
        # (measured - the one-second module-load storm).
        if action is None:
            action = DBG_CONTINUE
        self.send_data(T_MANIPULATE, manip_body(API_CONTINUE2,
                       struct.pack("<I", action)), self.my_pid)

    def step_over(self):
        # GetContext, then bump Rip past the int3, SetContext, continue.
        self.send_data(T_MANIPULATE, manip_body(API_GET_CONTEXT,
                       struct.pack("<I", 0x10003F)), self.my_pid)   # CONTEXT_ALL
        self.pending = "step-patch"

def connect_wait():
    while True:
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(SOCK)
            return s
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.2)

def parse_module_name(data):
    runs = re.findall(rb"[\x20-\x7e]{4,}", data)
    return runs[-1].decode("ascii", "replace") if runs else "?"

TRAILER       = b"\xaa"               # every packet on this wire ends with 0xAA

def handle(kd, ptype, bcount, pid, cksum, data):
    if ptype == T_STATE_CHANGE64:
        newstate = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else -1
        if newstate == STATE_LOAD_SYMBOLS:
            base = struct.unpack_from("<Q", data, 0x28)[0] if len(data) >= 0x30 else 0
            name = parse_module_name(data)
            log("MODULE LOAD pid=%#x base=%#x name=%s" % (pid, base, name))
            with open(os.path.join(RIG, "modules.txt"), "a") as mf:
                mf.write("%x %s\n" % (base, name))
            if not name.lower().endswith("ntoskrnl.exe"):
                pass
            elif getattr(kd, "ntbase", None) is None:
                kd.ntbase = base                     # for break classification
            kd.send_control(T_ACK, pid)
            if os.path.exists(os.path.join(RIG, "hold-tight")):
                pass                                  # probing the ack path only
            else:
                # The proven reply (the 02:08 boot loaded 300+ modules with
                # it): Continue2 + DBG_CONTINUE. The FIRST load of a boot
                # storms for about ten seconds - kdcom re-breaks at 1/s,
                # then its fallback proceeds and every later load accepts
                # the same reply first try. Replying GetVersion instead
                # (tried 10-13) removes the fallback: kdcom waits forever.
                # THE PARITY LAW (measured): the reply carries the id of
                # the packet being answered. A retry (the storm) repeats
                # its id, so the reply repeats it too; a fresh packet
                # alternates, and then the flip is right. The 02:09
                # boot's accepted reply had the target's own id.
                kd.continue_go()
        else:
            log("*** STATE CHANGE %#x pid=%#x - HOLDING (machine frozen) ***" % (newstate, pid))
            for j in range(0, len(data), 16):
                log("  %04x  %s" % (j, data[j:j+16].hex(" ")))
            if len(data) > 0x34 and struct.unpack_from("<I", data, 0x20)[0] == 0x80000003:
                exc_addr = struct.unpack_from("<Q", data, 0x30)[0]
                ntbase = getattr(kd, "ntbase", None)
                if (not os.path.exists(os.path.join(RIG, "hold-all"))
                        and ntbase and 0 <= exc_addr - ntbase < 0x500000):
                    # the kernel's boot breakpoint (int3 in ntoskrnl): step it
                    log("    boot breakpoint at nt+%#x - stepping over" % (exc_addr - ntbase))
                    kd.send_control(T_ACK, pid)
                    kd.step_over()
                    return
            kd.send_control(T_ACK, pid)
            poll_cmd(kd)     # same-call dispatch: this is the window that works
            while not os.path.exists(HOLD_RELEASE):
                poll_cmd(kd)                     # commands work while held
                # THE FIX (the tap proved it): read replies were arriving and
                # queueing in the socket forever - the main read_packet loop is
                # blocked in this very hold. Drain and dispatch them here.
                try:
                    pkt = kd.read_packet()
                    leader, ptype, bcount, pid, cksum, data = pkt
                    if ptype == T_STATE_CHANGE64:
                        # MUST ack: kdcom re-sends unacked state changes
                        # forever (measured - the re-send spam) and the
                        # engine's continue-wait sits behind the ack.
                        kd.send_control(T_ACK, pid)
                        log("hold: state-change re-send - acked")
                    else:
                        handle(kd, ptype, bcount, pid, cksum, data)
                except socket.timeout:
                    pass
                time.sleep(0.2)
            log("hold released - continuing")
            os.remove(HOLD_RELEASE)
            kd.continue_go()
    elif ptype == T_MANIPULATE:
        api = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        ret = struct.unpack_from("<i", data, 8)[0] if len(data) >= 12 else 0
        log("RX manipulate api=%#x ret=%#x len=%d" % (api, ret & 0xFFFFFFFF, len(data)))
        pending = getattr(kd, "pending", None)
        if pending in ("step-patch", "step-go", "ctx-dump", "read-dump"):
            pass
        elif len(data) > 56:
            for j in range(0, len(data), 16):
                chunk = data[j:j+16]
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                log("    %04x  %s  %s" % (j, chunk.hex(" "), asc))
        if pending == "step-patch" and len(data) > 56 + 0xF8:
            ctx = bytearray(data[56:])
            rip = struct.unpack_from("<Q", ctx, 0xF8)[0]
            rsp = struct.unpack_from("<Q", ctx, 0x98)[0]
            log("CTX rip=%#x rsp=%#x flags=%#x" % (
                rip, rsp, struct.unpack_from("<I", ctx, 0x30)[0]))
            struct.pack_into("<Q", ctx, 0xF8, rip + 1)      # step past the int3
            body = manip_body(API_SET_CONTEXT, struct.pack("<II", 0, len(ctx)))
            kd.send_data(T_MANIPULATE, body + bytes(ctx), kd.my_pid)
            kd.pending = "step-go"
            kd.send_control(T_ACK, pid)
            return
        if pending == "ctx-dump" and len(data) > 56 + 0xF8:
            ctx = data[56:]
            names = [("Rax",0x78),("Rcx",0x80),("Rdx",0x88),("Rbx",0x90),("Rsp",0x98),
                     ("Rbp",0xA0),("Rsi",0xA8),("Rdi",0xB0),("R8",0xB8),("R9",0xC0),
                     ("R10",0xC8),("R11",0xD0),("R12",0xD8),("R13",0xE0),("R14",0xE8),
                     ("R15",0xF0),("Rip",0xF8)]
            vals = {n: struct.unpack_from("<Q", ctx, o)[0] for n, o in names}
            log("CTX " + " ".join("%s=%#x" % (n, v) for n, v in vals.items()))
            log("CTX cs=%#x ds=%#x es=%#x fs=%#x gs=%#x ss=%#x eflags=%#x" % (
                struct.unpack_from("<H", ctx, 0x38)[0], struct.unpack_from("<H", ctx, 0x3A)[0],
                struct.unpack_from("<H", ctx, 0x3C)[0], struct.unpack_from("<H", ctx, 0x3E)[0],
                struct.unpack_from("<H", ctx, 0x40)[0], struct.unpack_from("<H", ctx, 0x42)[0],
                struct.unpack_from("<I", ctx, 0x44)[0]))
            kd.pending = None
            kd.send_control(T_ACK, pid)
            return
        if pending == "read-dump":
            got = struct.unpack_from("<I", data, 0x1C)[0] if len(data) >= 0x20 else 0
            log("READ ret=%#x actual=%#x" % (ret & 0xFFFFFFFF, got))
            for j in range(0x38, len(data), 16):
                chunk = data[j:j+16]
                asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                log("    %04x  %s  %s" % (j - 0x38, chunk.hex(" "), asc))
            kd.pending = None
            kd.send_control(T_ACK, pid)
            return
        if pending == "step-go":
            log("context set - continuing")
            kd.pending = None
            kd.send_control(T_ACK, pid)
            kd.continue_go()
            return
        kd.send_control(T_ACK, pid)
    elif ptype == 3:  # DEBUG_IO - DbgPrint text
        text = data[8:] if len(data) > 8 else b""
        printable = bytes(b for b in text if 32 <= b < 127 or b in (10, 13))
        log("PRINT %r" % printable.decode("ascii", "replace"))
        kd.send_control(T_ACK, pid)
    elif ptype == T_ACK:
        log("RX ack pid=%#x" % pid)
        if pid == getattr(kd, 'last_sent_id', None):
            kd.kd_expected ^= 1      # accepted: the engine flips, so do we
    elif ptype == T_RESEND:
        poll_cmd(kd)                          # commands must run even mid-storm
        log("RX resend pid=%#x - retransmitting" % pid)
        if kd.last_tx:
            p, d, orig_pid = kd.last_tx
            kd.send_data(p, d, orig_pid)      # same pid: a resend is not a new packet
    elif ptype == T_RESET:
        log("RX reset - restarting ids")
        kd.kd_expected = 0x80800000   # kdcom restarts BOTH ids to the base
        kd.my_pid = POST_RESET_PID
        kd.kd_expected = 0x80800000
        kd.send_control(T_ACK, pid)
    elif ptype == 11:  # FILE_IO - the .kdfiles mechanism: the kernel offers a file open
        api = struct.unpack_from("<I", data, 0)[0] if len(data) >= 4 else 0
        name = data[64:].decode("utf-16-le", "replace").split("\x00")[0] if len(data) > 64 else ""
        log("FILE_IO api=%#x %s" % (api, name))
        kd.send_control(T_ACK, pid)
        if api == 0x3430:  # CreateFile: decline - the kernel uses its local copy
            kd.send_data(11, struct.pack("<II", 0x3430, 0xC0000001) + b"\x00" * 56,
                         kd.my_pid)
    else:
        log("RX type=%d len=%d pid=%#x hdr=%s" % (ptype, len(data), pid, getattr(kd, "last_hdr", "?")))
        for j in range(0, len(data), 16):
            chunk = data[j:j+16]
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            log("    %04x  %s  %s" % (j, chunk.hex(" "), asc))
        if ptype not in (T_ACK, T_RESEND):
            kd.send_control(T_ACK, pid)

def manip_body(api, u_bytes=b""):
    # DBGKD_MANIPULATE_STATE64: api(4) level(2) cpu(2) status(4) pad(4) u(->0x40)
    body = struct.pack("<IHHI4x", api, 0, 0, 0)
    body += u_bytes + b"\x00" * max(0, MANIPULATE_SIZE - len(body) - len(u_bytes))
    return body

def poll_cmd(kd):
    # one-shot command file: "version" | "read <hexaddr> <hexlen>" | "go" ...
    path = os.path.join(RIG, "cmd")
    try:
        with open(path) as f:
            line = f.read().strip()
        os.remove(path)
    except OSError:
        return
    log("CMD %r" % line)
    try:
        _dispatch_cmd(kd, line)
    except Exception as e:
        log("  cmd error: %r" % (e,))

def _dispatch_cmd(kd, line):
    if line.startswith("pid "):
        kd.my_pid = int(line.split()[1], 16)
        log("my_pid set to %#x" % kd.my_pid)
    elif line == "break":
        kd.s.sendall(BREAKIN)
        log("TX breakin (cmd)")
    elif line == "step":
        kd.step_over()          # RIP+1 past an int3, SetContext, continue
        log("TX step-over (cmd)")
    elif line == "reset":
        hdr = struct.pack("<IHHII", CTRL_LEADER, T_RESET, 0, INITIAL_PID, 0)
        kd.s.sendall(hdr + TRAILER)
        log("TX reset (cmd)")
    elif line.startswith("version"):
        parts = line.split()
        pid = int(parts[1], 16) if len(parts) > 1 else None
        kd.send_data(T_MANIPULATE, manip_body(API_GET_VERSION),
                     pid if pid is not None else kd.my_pid)
    elif line.startswith("read"):
        try:
            _, a, n = line.split()
            addr, cnt = int(a, 16), int(n, 16)
        except ValueError:
            log("  bad read command")
            return
        u = struct.pack("<QII", addr, cnt, 0)   # u.ReadMemory
        kd.send_data(T_MANIPULATE, manip_body(API_READ_VM, u), kd.my_pid)
        kd.pending = "read-dump"
    elif line.startswith("pread"):
        try:
            _, a, n = line.split()
            addr, cnt = int(a, 16), int(n, 16)
        except ValueError:
            log("  bad pread command")
            return
        u = struct.pack("<QII", addr, cnt, 0)   # u.ReadPhysicalMemory
        kd.send_data(T_MANIPULATE, manip_body(API_READ_PHYS, u), kd.my_pid)
        kd.pending = "read-dump"
    elif line == "go":
        kd.continue_go()
    elif line == "step":
        kd.step_over()
    elif line == "knock":
        try:
            kd.s.sendall(BREAKIN)
            log("knock: breakin sent")
        except OSError as e:
            log("knock failed: %s" % e)
    elif line == "ctx":
        kd.send_data(T_MANIPULATE, manip_body(API_GET_CONTEXT,
                     struct.pack("<I", 0x10003F)), kd.my_pid)   # CONTEXT_ALL
        kd.pending = "ctx-dump"

def main():
    while True:
        s = connect_wait()
        kd = Kd(s)
        log("connected to %s" % SOCK)
        # The target's KD engine sits in a poll loop waiting for the debugger to
        # break in - it never transmits first (measured: the captured boot's packets
        # were responses to kd.exe's break-in). Knock every few seconds until the
        # target answers, then stop knocking.
        answered = threading.Event()

        def knocker():
            # kd.exe's connect flow: RESET control packets until the target
            # responds; the target's reset-ack restarts its KD state machine and
            # only then do ACKs of its state changes stick.
            while not answered.is_set():
                try:
                    if os.path.exists(os.path.join(RIG, "send-reset")):
                        hdr = struct.pack("<IHHII", CTRL_LEADER, T_RESET, 0,
                                          INITIAL_PID, 0)
                        s.sendall(hdr + TRAILER)
                        log("TX reset probe")
                    else:
                        s.sendall(BREAKIN)
                        log("TX breakin (0x62 x4)")
                except OSError:
                    s.close()          # force the main read to fail -> reconnect
                    return
                answered.wait(3.0)

        threading.Thread(target=knocker, daemon=True).start()
        try:
            s.settimeout(0.5)
            while True:
                try:
                    pkt = kd.read_packet()
                    answered.set()          # stop the knocker before long
                    handle(kd, *pkt[1:])    # handling: a hold must not
                    # receive breakin spam from the knocker thread.
                except socket.timeout:
                    poll_cmd(kd)
        except (ConnectionError, OSError) as e:
            log("link lost: %s - reconnecting" % e)
            answered.set()
            try:
                s.close()
            except OSError:
                pass
            time.sleep(0.5)

if __name__ == "__main__":
    main()
