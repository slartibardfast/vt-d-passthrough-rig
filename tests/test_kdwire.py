"""The kernel-debugger wire contract suite.

Discharges the obligations of specs/kdwire.allium (see specs/kdwire.obligations). The oracle is the
measured wire: every value asserted here was read off a captured packet or off the engine's decoded
source during the campaign, and the constants live in `kdc.py` because the engine fixes them.

Two things about importing the subject are worth knowing before reading the tests. `kdc.py` opens its
log at import time and takes both its log and its module-map path from the environment, so the
environment is arranged before the import rather than patched after it. And the hold is a loop that
waits for a file, so a test that drives it creates that file first and removes it in a finally block,
because a stray `hold-release` in the worktree is a real input to the next boot.
"""

import importlib
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

import walk  # the reader that owns the engine's read limit; see the read-limit law below

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

RIG_STATE = (ROOT / "hold-release", ROOT / "hold-tight", ROOT / "hold-all",
             ROOT / "do-knock", ROOT / "send-reset", ROOT / "cmd")


@pytest.fixture
def kdc(tmp_path, monkeypatch):
    """The collector module, with every path it reads from the environment pointed at a scratch dir.

    The rig-state files a run keys on are asserted absent first. A campaign leftover there would make
    a pass or a fail depend on what the last run left behind, which is the defect this rig keeps paying
    for.
    """
    for path in RIG_STATE:
        if path.exists():
            pytest.skip("%s is present in the worktree, so the wire cannot be graded in isolation"
                        % path.name)
    monkeypatch.setenv("KDC_LOG", str(tmp_path / "kdc-live.log"))
    monkeypatch.setenv("KDC_MODULES", str(tmp_path / "modules.txt"))
    monkeypatch.setenv("KDC_CATCHES", str(tmp_path / "captor-catches.log"))
    sys.modules.pop("kdc", None)
    module = importlib.import_module("kdc")
    yield module
    sys.modules.pop("kdc", None)


def pair():
    """A connected collector and its peer, so the wire itself is what the test reads."""
    left, right = socket.socketpair()
    return left, right


def collector(module, sock):
    return module.Kd(sock)


def drain(sock, seconds=0.4):
    """Everything the collector sent, read off the other end of the socket."""
    sock.settimeout(seconds)
    out = b""
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            out += chunk
    except socket.timeout:
        pass
    return out


def frames(raw):
    """Split a byte stream into wire frames: (leader, type, byte_count, id, checksum, payload)."""
    out, i = [], 0
    while i + 16 <= len(raw):
        leader, ptype, bcount, pid, cksum = struct.unpack_from("<IHHII", raw, i)
        end = i + 16 + bcount
        if len(raw) < end + 1:
            break
        out.append((leader, ptype, bcount, pid, cksum, raw[i + 16:end]))
        i = end + 1
    return out


def frame(leader, ptype, pid, data=b""):
    header = struct.pack("<IHHII", leader, ptype, len(data), pid,
                         (sum(data) & 0xFFFFFFFF) if data else 0)
    return header + data + b"\xaa"


def state_change(module, new_state, base=None, exception_code=None, address=None, tail=b""):
    """A STATE_CHANGE64 payload laid out the way the engine lays it out."""
    data = bytearray(0x40)
    struct.pack_into("<I", data, 0x00, new_state)
    if base is not None:
        struct.pack_into("<Q", data, 0x28, base)
    if exception_code is not None:
        struct.pack_into("<I", data, 0x20, exception_code)
    if address is not None:
        struct.pack_into("<Q", data, 0x30, address)
    return bytes(data) + tail


def log_text(tmp_path):
    path = tmp_path / "kdc-live.log"
    return path.read_text(errors="replace") if path.is_file() else ""


# ---------------------------------------------------------------------------
# The header law: framing, the trailer, and the checksum width
# ---------------------------------------------------------------------------

def test_a_data_packet_carries_the_measured_header_and_trailer(kdc, tmp_path):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80801234
        body = b"\x01\x02\x03"
        kd.send_data(kdc.T_MANIPULATE, body)
        leader, ptype, bcount, pid, cksum, data = frames(drain(peer))[0]
        assert leader == kdc.DATA_LEADER
        assert ptype == kdc.T_MANIPULATE
        assert bcount == len(body)
        assert pid == 0x80801234, "the id is the engine's expectation, not the collector's counter"
        assert data == body
        assert drain(peer) == b"", "one frame per send: the payload is not sent twice"
    finally:
        sock.close()
        peer.close()
    assert "TX data" in log_text(tmp_path)


def test_the_checksum_is_the_full_sum_and_not_a_sixteen_bit_field(kdc):
    """The measured law: a 16-bit checksum passed for small payloads and broke every larger one.

    The payload here sums past 0xFFFF on purpose. Truncating the field reproduces the failure the
    campaign hit on the SetContext of a step-over, where the engine refused a packet and named nothing.
    """
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        body = b"\xff" * 300
        assert sum(body) > 0xFFFF
        kd.send_data(kdc.T_MANIPULATE, body)
        _, _, _, _, cksum, data = frames(drain(peer))[0]
        assert cksum == sum(body) & 0xFFFFFFFF
        assert cksum != sum(body) & 0xFFFF, "a truncated checksum is the bug, not the law"
        assert data == body
    finally:
        sock.close()
        peer.close()


def test_a_control_packet_carries_no_checksum_and_no_payload(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.send_control(kdc.T_ACK, 0x80800001)
        raw = drain(peer)
        leader, ptype, bcount, pid, cksum, data = frames(raw)[0]
        assert leader == kdc.CTRL_LEADER
        assert ptype == kdc.T_ACK
        assert bcount == 0 and data == b""
        assert cksum == 0
        assert raw[-1] == kdc.TRAILER[0] and len(raw) == 16 + 1
    finally:
        sock.close()
        peer.close()


def test_every_frame_on_the_wire_ends_with_the_trailer(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.send_control(kdc.T_ACK, 1)
        kd.send_data(kdc.T_MANIPULATE, b"\x10\x20")
        raw = drain(peer)
        assert len(frames(raw)) == 2
        assert raw.count(kdc.TRAILER) == 2, "one trailer per frame, and one per frame"
        assert raw[-1] == kdc.TRAILER[0]
        assert struct.unpack_from("<I", raw, 0)[0] == kdc.CTRL_LEADER
    finally:
        sock.close()
        peer.close()


# ---------------------------------------------------------------------------
# The ID LAW
# ---------------------------------------------------------------------------

def test_issuing_requests_never_advances_the_id(kdc):
    """The sender's half of the law: the id comes from the engine's expectation and stays there."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800000
        kd.send_data(kdc.T_MANIPULATE, b"\x01")
        kd.send_data(kdc.T_MANIPULATE, b"\x02")
        issued = [f[3] for f in frames(drain(peer))]
        assert issued == [0x80800000, 0x80800000], "issuing twice is not two ids"
        assert kd.kd_expected == 0x80800000, "the flip belongs to the acknowledgement"
        assert kd.last_sent_id == 0x80800000
    finally:
        sock.close()
        peer.close()


def test_only_an_ack_of_the_id_last_issued_flips_the_pair(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800000
        kd.last_sent_id = 0x80800000
        kdc.handle(kd, kdc.T_ACK, 0, 0x80800001, 0, b"")
        assert kd.kd_expected == 0x80800000, "a stale acknowledgement must not move the pair"
        kdc.handle(kd, kdc.T_ACK, 0, 0x80800000, 0, b"")
        assert kd.kd_expected == 0x80800001, "acceptance flips it, once"
        kd.last_sent_id = 0x80800001
        kdc.handle(kd, kdc.T_ACK, 0, 0x80800001, 0, b"")
        assert kd.kd_expected == 0x80800000, "and the pair alternates rather than drifting"
        for value in (kd.kd_expected, kd.kd_expected + 1):
            assert value in (0x80800000, 0x80800001)
    finally:
        sock.close()
        peer.close()


def test_a_refusal_retransmits_the_same_packet_under_the_same_id(kdc):
    """A refusal is not a new request. A fresh id on a retry is a duplicate, refused forever."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800000
        body = b"\x3c\x00\x00\x00"
        kd.send_data(kdc.T_MANIPULATE, body)
        drain(peer)
        kdc.handle(kd, kdc.T_RESEND, 0, 0x80800000, 0, b"")
        leader, ptype, bcount, pid, cksum, data = frames(drain(peer))[0]
        assert leader == kdc.DATA_LEADER
        assert pid == 0x80800000, "the same id, not the next one"
        assert data == body and cksum == sum(body) & 0xFFFFFFFF
        assert kd.last_tx == (kdc.T_MANIPULATE, body, 0x80800000)
    finally:
        sock.close()
        peer.close()


def test_a_reset_restarts_both_id_counters(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800001
        kd.my_pid = kdc.INITIAL_PID
        kdc.handle(kd, kdc.T_RESET, 0, 0x80800000, 0, b"")
        assert kd.kd_expected == 0x80800000, "the engine's expectation restarts at its base"
        assert kd.my_pid == kdc.POST_RESET_PID, "and so does ours"
        leader, ptype, _, pid, _, _ = frames(drain(peer))[0]
        assert leader == kdc.CTRL_LEADER and ptype == kdc.T_ACK and pid == 0x80800000
    finally:
        sock.close()
        peer.close()


# ---------------------------------------------------------------------------
# The PARITY LAW and the DRAIN-ACK LAW
# ---------------------------------------------------------------------------

def test_a_reply_carries_the_id_of_the_packet_it_answers(kdc, tmp_path):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800001
        incoming = 0x80800000
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, incoming, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0xfffff800,
                                tail=b"\\WINDOWS\\System32\\ati2mtag.sys"))
        leader, ptype, _, pid, _, _ = frames(drain(peer))[0]
        assert leader == kdc.CTRL_LEADER and ptype == kdc.T_ACK
        assert pid == incoming, "the ack echoes the answered packet, not our own counter"
        assert kd.kd_expected == 0x80800001, "and answering does not disturb the pair"
    finally:
        sock.close()
        peer.close()


def test_two_requests_across_an_ack_carry_the_other_id(kdc):
    """THE ALTERNATION LAW, from the sender's side, which is what makes a retrigger worth sending.

    The reader asks twice when the engine says nothing. That second ask is only worth sending because
    the acknowledgement of the first flipped the pair, so the retry reaches the engine under an id it
    has not just refused. Two requests with no ack between them repeat the id, which is the other half
    of the same law.
    """
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.kd_expected = 0x80800000
        kd.last_sent_id = 0x80800000
        kd.send_data(kdc.T_MANIPULATE, b"\x30\x00\x00\x00")
        kd.send_data(kdc.T_MANIPULATE, b"\x30\x00\x00\x00")
        before = [f[3] for f in frames(drain(peer))]
        assert before == [0x80800000, 0x80800000], "an unacknowledged repeat is the same id"
        kdc.handle(kd, kdc.T_ACK, 0, 0x80800000, 0, b"")
        kd.send_data(kdc.T_MANIPULATE, b"\x30\x00\x00\x00")
        after = [f[3] for f in frames(drain(peer))]
        assert after == [0x80800001], "after the acceptance the next request is on the other id"
        assert after != before, "a retry that repeats a refused id is a retry that never lands"
    finally:
        sock.close()
        peer.close()


def test_a_module_load_is_recorded_with_its_base_and_name(kdc, tmp_path):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        name = b"\\WINDOWS\\System32\\drivers\\ati2mtag.sys"
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0xfffff700_1000_0000,
                                tail=name + b"\x00"))
        drain(peer)
        text = log_text(tmp_path)
        assert "MODULE LOAD" in text and "ati2mtag.sys" in text
        assert "base=0xfffff70010000000" in text
        recorded = (tmp_path / "modules.txt").read_text()
        assert recorded.split() == ["fffff70010000000",
                                    "\\WINDOWS\\System32\\drivers\\ati2mtag.sys"]
        assert kdc.parse_module_name(name) == "\\WINDOWS\\System32\\drivers\\ati2mtag.sys"
    finally:
        sock.close()
        peer.close()


def test_a_frame_is_a_sixteen_byte_header_then_payload_then_trailer(kdc):
    """The size is not a detail: a collector that reads nine bytes loses the session."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        body = b"\x30\x31\x00\x00"
        peer.sendall(frame(kdc.DATA_LEADER, kdc.T_MANIPULATE, 0x80800001, body))
        leader, ptype, bcount, pid, cksum, data = kd.read_packet()
        assert leader == kdc.DATA_LEADER and ptype == kdc.T_MANIPULATE
        assert bcount == len(body) and data == body
        assert cksum == sum(body)
        assert struct.calcsize("<IHHII") == 16, "the header the reader asks for is the header sent"
    finally:
        sock.close()
        peer.close()


def test_a_closed_link_is_reported_as_lost_and_redialled(kdc, tmp_path):
    """A run's collector is the only witness of its boot, so the link is rebuilt, not restarted.

    This drives the real process: `connect_wait()` is what dials again after the loss, and the log is
    opened once, so the reconnect carries on into the same file rather than starting a new one.
    """
    path = tmp_path / "kd.sock"
    log = tmp_path / "kdc-live.log"
    env = dict(os.environ, KD_SOCK=str(path), KDC_LOG=str(log),
               KDC_MODULES=str(tmp_path / "modules.txt"))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    server.settimeout(25)
    proc = subprocess.Popen([sys.executable, str(ROOT / "kdc.py")], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def waited_for(needles, seconds=25):
        deadline = time.time() + seconds
        seen = ""
        while time.time() < deadline:
            seen = log.read_text(errors="replace") if log.is_file() else ""
            if all(needle in seen for needle in needles):
                return seen
            time.sleep(0.25)
        raise AssertionError("never saw %r in the log, which ended with: %s"
                             % (needles, seen[-400:]))

    try:
        conn, _ = server.accept()
        conn.sendall(frame(kdc.CTRL_LEADER, kdc.T_ACK, 0x80800001))
        waited_for(["connected to"])
        conn.close()
        server.close()
        waited_for(["link lost"])
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(path))
        server.listen(1)
        server.settimeout(25)
        redial, _ = server.accept()
        redial.close()
        seen = waited_for(["connected to"])
        assert seen.count("connected to") >= 2, "the same collector, dialing again"
        assert "link lost" in seen
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        server.close()
        path.unlink(missing_ok=True)


def test_a_module_load_is_answered_with_a_resume_not_a_version_request(kdc):
    """The engine's fallback depends on this reply. Answering a load with GetVersion was tried."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0xfffff800,
                                tail=b"\\WINDOWS\\System32\\win32k.sys"))
        control, data = frames(drain(peer))[:2]
        assert control[1] == kdc.T_ACK
        assert data[0] == kdc.DATA_LEADER and data[1] == kdc.T_MANIPULATE
        assert struct.unpack_from("<I", data[5], 0)[0] == kdc.API_CONTINUE2, \
            "the resume is Continue2, the variant that carries the control set"
        assert struct.unpack_from("<I", data[5], 0x10)[0] == kdc.DBG_CONTINUE
    finally:
        sock.close()
        peer.close()


def test_the_first_kernel_load_sets_the_base_later_breaks_are_measured_against(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0x400000,
                                tail=b"\\WINDOWS\\System32\\ntoskrnl.exe"))
        assert kd.ntbase == 0x400000
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0x900000,
                                tail=b"\\WINDOWS\\System32\\ntoskrnl.exe"))
        assert kd.ntbase == 0x400000, "the first kernel load of a session owns the base"
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, kdc.STATE_LOAD_SYMBOLS, base=0x100000,
                                tail=b"\\WINDOWS\\System32\\hal.dll"))
        assert kd.ntbase == 0x400000, "a driver load is not a kernel load"
        drain(peer)
    finally:
        sock.close()
        peer.close()


def test_printed_driver_text_is_captured_and_answered(kdc, tmp_path):
    """The only route by which the driver's own assertion text reaches the host."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        text = b"eError[PP_PowerPlay]: cislands_hwmgr.c[6696]  -- Assertion failed\r\n"
        kdc.handle(kd, 3, 8 + len(text), 0x80800000, 0, b"\x00" * 8 + text)
        assert "PRINT" in log_text(tmp_path) and "cislands_hwmgr.c[6696]" in log_text(tmp_path)
        assert frames(drain(peer))[0][1] == kdc.T_ACK
    finally:
        sock.close()
        peer.close()


# ---------------------------------------------------------------------------
# Framing resilience
# ---------------------------------------------------------------------------

def test_bytes_before_a_leader_are_stepped_over(kdc):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        peer.sendall(b"\x00\x11\x22" + frame(kdc.CTRL_LEADER, kdc.T_ACK, 0x80800001))
        leader, ptype, bcount, pid, _, data = kd.read_packet()
        assert leader == kdc.CTRL_LEADER and ptype == kdc.T_ACK and pid == 0x80800001
        assert bcount == 0 and data == b""
    finally:
        sock.close()
        peer.close()


def test_a_control_packet_that_claims_a_payload_does_not_desync_the_stream(kdc):
    """A control leader carrying bytes means the stream lost its place; the length is dropped."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        bogus = struct.pack("<IHHII", kdc.CTRL_LEADER, kdc.T_ACK, 5, 0x80800000, 0)
        peer.sendall(bogus + b"\x01\x02\x03\x04\x05"
                     + frame(kdc.CTRL_LEADER, kdc.T_ACK, 0x80800001))
        first = kd.read_packet()
        assert first[2] == 0, "the impossible payload length is dropped, not trusted"
        second = kd.read_packet()
        assert second[1] == kdc.T_ACK and second[3] == 0x80800001, "and the stream resynchronises"
    finally:
        sock.close()
        peer.close()


# ---------------------------------------------------------------------------
# The hold, and what the machine looks like while frozen
# ---------------------------------------------------------------------------

def test_an_unmodelled_state_change_holds_the_machine_and_still_acks(kdc, tmp_path):
    """The freeze is the feature, and the DRAIN-ACK LAW keeps running inside it."""
    sock, peer = pair()
    release = Path(kdc.HOLD_RELEASE)
    release.write_text("")
    try:
        kd = collector(kdc, sock)
        kd.s.settimeout(0.05)
        # A reply nobody has read in hours would otherwise be parsed as if it answered this hold.
        peer.sendall(b"stale reply bytes")
        kdc.handle(kd, kdc.T_STATE_CHANGE64, 0x40, 0x80800000, 0,
                   state_change(kdc, 0x3032, exception_code=0x80000003, address=0xfffff800_004b_3a5))
        text = log_text(tmp_path)
        assert "HOLDING" in text
        assert "1 stale socket chunks flushed" in text, \
            "the backlog is discarded raw until the link is quiet, and the count is logged"
        catches = (tmp_path / "captor-catches.log").read_text()
        assert "exc=0x80000003" in catches and "addr=0xfffff800004b3a5" in catches
        assert "at ???" in catches, "an unowned address is named as unowned, never guessed"
        assert frames(drain(peer))[0][1] == kdc.T_ACK, "held or not, an awaited packet is answered"
        assert not release.exists(), "the hold consumes its release, so the next one is deliberate"
    finally:
        release.unlink(missing_ok=True)
        sock.close()
        peer.close()


def test_an_unrequested_reply_is_still_answered(kdc):
    """A reply that arrives with nothing pending is still a packet the engine awaits."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        body = struct.pack("<IHHI4x", kdc.API_GET_VERSION, 0, 0, 0)
        kdc.handle(kd, kdc.T_MANIPULATE, len(body), 0x80800000, 0, body)
        assert frames(drain(peer))[0][1] == kdc.T_ACK
        assert getattr(kd, "pending", None) is None, "answering an unsolicited reply invents no work"
    finally:
        sock.close()
        peer.close()


def test_an_exchange_this_spec_does_not_model_is_still_answered(kdc, tmp_path):
    """Refusing to answer an unknown exchange costs the run its only witness."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kdc.handle(kd, 99, 4, 0x80800001, 0, b"\x01\x02\x03\x04")
        assert "RX type=99" in log_text(tmp_path)
        _, ptype, _, pid, _, _ = frames(drain(peer))[0]
        assert ptype == kdc.T_ACK and pid == 0x80800001
    finally:
        sock.close()
        peer.close()


def test_the_owner_of_an_address_is_the_highest_base_not_above_it(kdc):
    """A first match over the ascending list lets a gap swallow a later module's range."""
    mods = [(0x1000, "\\a.sys"), (0x9000, "\\b.sys")]
    assert kdc.resolve_addr(None, mods, 0x9500) == "b.sys+0x500"
    assert kdc.resolve_addr(None, mods, 0x1200) == "a.sys+0x200"
    assert kdc.resolve_addr(None, mods, 0x500) == "???", "below every base is unnamed, not guessed"
    assert kdc.resolve_addr(0x400000, mods, 0x400010) == "ntoskrnl+0x10"


def test_a_captor_catch_is_written_even_when_the_module_is_unknown(kdc, tmp_path):
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        kd.ntbase = 0x400000
        payload = state_change(kdc, 0x3032, exception_code=0x80000003, address=0x44b3a5)
        kdc.captor_catch(kd, 0x3032, 0x80800000, payload)
        written = (tmp_path / "captor-catches.log").read_text()
        assert "at ntoskrnl+0x4b3a5" in written, "a kernel address is named against the kernel"
        kdc.captor_catch(kd, 0x3032, 0x80800000, state_change(kdc, 0x3032, address=0))
        assert (tmp_path / "captor-catches.log").read_text() == written, \
            "a zero address is no break at all, so it is not recorded as one"
        drain(peer)
    finally:
        sock.close()
        peer.close()


# ---------------------------------------------------------------------------
# What the engine refuses, and what the collector must not ask for
# ---------------------------------------------------------------------------

def test_a_file_open_is_declined_with_the_documented_status(kdc):
    """The collector has no debugger-file role, so the guest keeps its own bytes."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        payload = struct.pack("<I", 0x3430) + b"\x00" * 60
        payload += "\\WINDOWS\\System32\\ati2mtag.sys".encode("utf-16-le")
        kdc.handle(kd, 11, len(payload), 0x80800000, 0, payload)
        control, data = frames(drain(peer))[:2]
        assert control[1] == kdc.T_ACK
        assert data[0] == kdc.DATA_LEADER
        assert struct.unpack_from("<I", data[5], 0)[0] == 0x3430
        assert struct.unpack_from("<I", data[5], 4)[0] == kdc.FILE_OPEN_DECLINE
    finally:
        sock.close()
        peer.close()


def test_a_read_reply_reports_what_arrived_and_clears_what_was_asked(kdc, tmp_path):
    """The reply's own length field is the answer; the reader must not trust the request."""
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        body = bytearray(0x38 + 16)
        struct.pack_into("<I", body, 0x00, kdc.API_READ_VM)
        struct.pack_into("<i", body, 0x08, 0)
        struct.pack_into("<I", body, 0x1C, 7)
        body[0x38:0x38 + 7] = b"abcdefg"
        kd.pending = "read-dump"
        kdc.handle(kd, kdc.T_MANIPULATE, len(body), 0x80800000, 0, bytes(body))
        text = log_text(tmp_path)
        assert "READ ret=0x0 actual=0x7" in text, text
        assert "abcdefg" in text
        assert kd.pending is None, "a delivered result is not still pending"
        assert frames(drain(peer))[0][1] == kdc.T_ACK
    finally:
        sock.close()
        peer.close()


def test_the_collector_forwards_a_read_it_was_asked_for_and_the_reader_owns_the_clamp(kdc, tmp_path):
    """Where the read limit is enforced, stated once.

    The collector is a faithful courier: a command asking for more than the engine accepts goes out as
    asked and comes back refused. The clamp belongs to the reader, which is why walk.allium specifies
    it and why the constant is `walk.MAXRD` rather than a wire constant.
    """
    sock, peer = pair()
    try:
        kd = collector(kdc, sock)
        cmd = tmp_path / "cmd"
        kdc.poll_cmd.__globals__["RIG"] = str(tmp_path)
        cmd.write_text("read 0x400000 20\n")
        kdc.poll_cmd(kd)
        _, _, _, _, _, body = frames(drain(peer))[0]
        assert struct.unpack_from("<Q", body, 0x10)[0] == 0x400000
        assert struct.unpack_from("<I", body, 0x18)[0] == 0x20, \
            "no clamp here: the courier that silently resized a request would hide the reader's bug"
        cmd.write_text("read notahex 4\n")
        kdc.poll_cmd(kd)
        assert "bad read command" in log_text(tmp_path)
        assert drain(peer) == b"", "a malformed command is refused before anything is sent"
        cmd.write_text("pread 0x400000 4\n")
        kdc.poll_cmd(kd)
        _, _, _, _, _, physical = frames(drain(peer))[0]
        assert struct.unpack_from("<I", physical, 0)[0] == kdc.API_READ_PHYS, \
            "a physical read is a different request, not a flag on the same one"
        assert walk.MAXRD == 16, \
            "the engine's limit lives in the reader, and this is the law it obeys"
    finally:
        sock.close()
        peer.close()


def test_the_manipulate_body_is_the_size_the_loader_reads(kdc):
    """sizeof(DBGKD_MANIPULATE_STATE64) on XP x64: a short body is refused, a long one misaligns."""
    body = kdc.manip_body(kdc.API_GET_VERSION)
    assert len(body) == kdc.MANIPULATE_SIZE
    assert struct.unpack_from("<I", body, 0)[0] == kdc.API_GET_VERSION
    filled = kdc.manip_body(kdc.API_READ_VM, struct.pack("<QII", 0x400000, 4, 0))
    assert len(filled) == kdc.MANIPULATE_SIZE
    assert struct.unpack_from("<Q", filled, 0x10)[0] == 0x400000
    assert struct.unpack_from("<I", filled, 0x18)[0] == 4


# ---------------------------------------------------------------------------
# The one transport limit that decides whether a session can start at all
# ---------------------------------------------------------------------------

def test_an_endpoint_too_long_to_bind_is_refused_before_the_boot(kdc):
    """Naming the limit is the point: the transport's own failure says nothing about length."""
    long_path = "/tmp/opencode/" + "k" * 120 + ".sock"
    assert len(long_path) > kdc.MAX_ENDPOINT_PATH
    env = dict(os.environ, KD_SOCK=long_path,
               KDC_LOG="/tmp/opencode/refuse-test.log",
               KDC_MODULES="/tmp/opencode/refuse-test-modules.txt")
    result = subprocess.run([sys.executable, str(ROOT / "kdc.py")],
                            capture_output=True, text=True, env=env, timeout=30)
    assert result.returncode != 0
    merged = result.stdout + result.stderr
    assert "over this rig's limit of %d" % kdc.MAX_ENDPOINT_PATH in merged, merged
    assert "the kernel caps AF_UNIX paths at 108" in merged, merged


def test_a_run_socket_within_the_limit_is_not_refused_by_that_check(kdc, tmp_path):
    """The guard has to bind the law and nothing else, or a per-run name is silently rejected."""
    short = tmp_path / "kd-run.sock"
    assert len(str(short)) <= 108
    env = dict(os.environ, KD_SOCK=str(short),
               KDC_LOG=str(tmp_path / "kdc-live.log"),
               KDC_MODULES=str(tmp_path / "modules.txt"))
    # The bound that governs this is kdc.MAX_ENDPOINT_PATH, the enforced margin; the kernel's 108 is
    # the reason the margin exists, and the test above is where 108 is asserted.
    inner = ("import sys; sys.path.insert(0, {path!r}); import kdc; "
             "print('limit=' + str(kdc.MAX_ENDPOINT_PATH) + ' sock=' + str(len(kdc.SOCK)))")
    probe = subprocess.run([sys.executable, "-c", inner.format(path=str(ROOT))],
                           capture_output=True, text=True, env=env, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "limit=%d sock=%d" % (kdc.MAX_ENDPOINT_PATH, len(str(short))), \
        probe.stdout
