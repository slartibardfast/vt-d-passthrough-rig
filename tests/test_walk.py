"""The memory-reader suite: the clamp, the single retrigger, and the chunked walk.

Discharges the obligations of specs/walk.allium (see specs/walk.obligations). The subject is
`walk.py`, the client that reads guest memory through the collector's command file. It is a small
module and one of its laws had been silently disabled, so these tests are written against the
behaviour the campaign measured rather than against whatever the file happens to define.

Two notes for a reader. The engine's refusal is a *definitive* answer, so the client must not retry
it; that law had been dead because a second definition of the same function shadowed the first and
did not record the return status. And the wait is the only thing that distinguishes "the engine
refused" from "the engine is slow", so a test that needs one or the other says which by replacing the
wait and counting its calls, never by sleeping on a hope. Every replacement goes through
`monkeypatch`, so no test can pass because of the one that ran before it.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import walk  # noqa: E402


class Reader:
    """The reader module with every path it touches pointed at a scratch dir."""

    def __init__(self, tmp_path, monkeypatch):
        self.module = walk
        self.monkeypatch = monkeypatch
        self.log = tmp_path / "kdc-live.log"
        self.log.write_bytes(b"")
        self.cmd = tmp_path / "cmd"
        self.patch("LOG", str(self.log))
        self.patch("CMD", str(self.cmd))
        self.patch("_last_ret", [None])

    def patch(self, name, value):
        return self.monkeypatch.setattr(walk, name, value)

    def __getattr__(self, name):
        return getattr(self.module, name)


@pytest.fixture
def reader(tmp_path, monkeypatch):
    return Reader(tmp_path, monkeypatch)


class Waiting:
    """A stand-in for the wait, so a test states which outcome it is about and how many tries took."""

    def __init__(self, module, results):
        self.results = list(results)
        self.calls = []
        self.ret = module._last_ret

    def __call__(self, mark, timeout=12):
        self.calls.append(mark)
        index = min(len(self.calls) - 1, len(self.results) - 1)
        data, actual, status = self.results[index]
        self.ret[0] = status
        return (data, actual) if data is not None else (None, 0)


def read_dump(payload, actual, status=0):
    """Render a reply the way the collector writes one, so it is the parser being tested.

    The ascii column at the end of a dump line is not decoration for the reader: its pattern ends a
    byte pair at a space, so a line that stops after the last hex byte loses that byte. The real
    writer always puts the ascii there, and a fixture that omits it tests a shape the wire never
    produces.
    """
    lines = ["[00:00:01] READ ret=%#x actual=%#x" % (status, actual)]
    for offset in range(0, len(payload), 16):
        chunk = payload[offset:offset + 16]
        ascii_column = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append("[00:00:01] %04x  %s  %s"
                     % (offset, " ".join("%02x" % b for b in chunk), ascii_column))
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------------------
# The engine's limit, and the client's clamp
# ---------------------------------------------------------------------------

def test_a_read_is_clamped_to_the_length_the_engine_answers(reader):
    """Measured: the engine refuses a request longer than sixteen bytes, so the client never asks."""
    reader.patch("_wait_read", lambda mark, timeout=12: (b"\x00" * 16, 16))
    reader.module.kd_read(0x400000, 64)
    assert reader.cmd.read_text().split() == ["read", "400000", "10"]
    assert reader.module.MAXRD == 16, "the clamp is the engine's limit, held in the reader"
    reader.module.kd_read(0x400000, 4)
    assert reader.cmd.read_text().split() == ["read", "400000", "4"]


def test_a_zero_length_read_is_never_issued(reader):
    asked = []
    reader.patch("_wait_read", lambda mark, timeout=12: asked.append(mark) or (b"", 0))
    assert reader.module.read_block(0x400000, 0) == (b"", 0)
    assert asked == [], "an empty request is a defect, not a no-op the engine must tolerate"
    assert not reader.cmd.exists()


def test_no_request_asks_for_more_than_the_limit_across_a_whole_walk(reader):
    """The clamp is read off the command file, so the real `kd_read` is what is under test here."""
    asked = []

    def wait(mark, timeout=12):
        asked.append(reader.cmd.read_text().split()[2])
        return b"\x00" * 16, 16

    reader.patch("_wait_read", wait)
    reader.module.read_block(0x400000, 100)
    assert len(asked) == 7, asked
    assert all(int(value, 16) <= reader.module.MAXRD for value in asked), asked


# ---------------------------------------------------------------------------
# The definitive answer, and the one retrigger
# ---------------------------------------------------------------------------

def test_an_answered_refusal_is_not_retried(reader):
    """The law that was dead: a refusal is an answer, so asking twice only burns the window.

    A second definition of `_wait_read` had shadowed the one that recorded the return status, so
    `kd_read` saw no status, took the silence branch, and retried every refused read anyway.
    """
    waited = Waiting(reader.module, [(None, 0, 0xC0000001)])
    reader.patch("_wait_read", waited)
    assert reader.module.kd_read(0x400000, 16) == (b"", 0)
    assert len(waited.calls) == 1, "a refusal is definitive: the read is not asked a second time"


def test_silence_retriggers_the_read_exactly_once(reader):
    waited = Waiting(reader.module, [(None, 0, None)])
    reader.patch("_wait_read", waited)
    assert reader.module.kd_read(0x400000, 16) == (None, 0)
    assert len(waited.calls) == 2, "silence is retried once, and a third ask is a stall"


def test_a_read_that_arrives_is_not_retried_at_all(reader):
    waited = Waiting(reader.module, [(b"abcdef", 6, 0)])
    reader.patch("_wait_read", waited)
    assert reader.module.kd_read(0x400000, 16) == (b"abcdef", 6)
    assert len(waited.calls) == 1


def test_the_second_try_starts_after_the_first_ones_own_view_of_the_log(reader):
    """A retrigger must not re-read the bytes the first wait already looked past."""
    marks = []

    def wait(mark, timeout=12):
        marks.append(mark)
        return (None, 0) if len(marks) == 1 else (b"\x01", 1)

    reader.patch("_wait_read", wait)
    reader.log.write_bytes(b"[00:00:01] READ ret=0x0 actual=0x0\n")
    assert reader.module.kd_read(0x400000, 16) == (b"\x01", 1)
    assert len(marks) == 2 and marks[1] >= marks[0], "the second wait sees a later log, not the same one"


# ---------------------------------------------------------------------------
# Parsing the answer the collector wrote
# ---------------------------------------------------------------------------

def test_the_reply_is_read_from_the_log_and_cut_to_what_arrived(reader):
    """The collector writes the bytes and an `actual` count; the count is what bounds the read."""
    payload = bytes(range(7))
    reader.log.write_bytes(read_dump(payload, 7))
    assert reader.module._wait_read(0, timeout=1) == (payload, 7)
    assert reader.module._last_ret[0] == 0


def test_a_refusal_is_recorded_so_the_caller_can_stop(reader):
    reader.log.write_bytes(read_dump(b"", 0, status=0xC0000001))
    assert reader.module._wait_read(0, timeout=1) == (b"", 0)
    assert reader.module._last_ret[0] == 0xC0000001, "the status is the reason not to ask again"


def test_a_reply_longer_than_its_count_is_discarded_not_trusted(reader):
    reader.log.write_bytes(read_dump(bytes(range(20)), 4))
    data, actual = reader.module._wait_read(0, timeout=1)
    assert actual == 4 and data == bytes(range(4)), \
        "the count, not the hex lines, is the length of the answer"


def test_a_log_with_no_reply_yields_nothing_and_no_status(reader):
    reader.log.write_bytes(b"[00:00:01] PRINT 'unrelated'\n")
    assert reader.module._wait_read(0, timeout=1) == (None, 0)
    assert reader.module._last_ret[0] is None, "no answer is not the same fact as a refusal"


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------

def test_a_block_walk_advances_by_the_bytes_actually_returned(reader):
    served = {0x1000: b"A" * 16, 0x1010: b"BBBB", 0x1014: b"C" * 12}

    def serve(address, length):
        payload = served.get(address, b"")
        return payload, len(payload)

    reader.patch("kd_read", serve)
    data, got = reader.module.read_block(0x1000, 32)
    assert data == b"A" * 16 + b"BBBB" + b"C" * 12 and got == 32, \
        "a short answer is stepped over by its own length, never assumed to be full"


def test_a_stalled_walk_reports_where_it_stopped(reader):
    served = {0x2000: b"1234567890123456"}

    def serve(address, length):
        payload = served.get(address, b"")
        return payload, len(payload)

    reader.patch("kd_read", serve)
    data, got = reader.module.read_block(0x2000, 32)
    assert data == b"1234567890123456"
    assert got == 16, "the count is how far the walk got, so a partial read is never read as full"


def test_a_walk_of_exactly_the_limit_is_one_step(reader):
    steps = []

    def serve(address, length):
        steps.append((address, length))
        return b"\x00" * 16, 16

    reader.patch("kd_read", serve)
    assert reader.module.read_block(0x3000, 16) == (b"\x00" * 16, 16)
    assert steps == [(0x3000, 16)]
