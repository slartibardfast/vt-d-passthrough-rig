"""The feed capture tool's rules, tested without a browser or a guest.

A photographed frame is evidence only if a reader can tell a live session from a held picture, and
only if the link number is not mistaken for the guest's mode. Those two rules are the whole reason
the tool exists, so they are the things pinned here.
"""
import importlib.util
from pathlib import Path

RIG = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("feed_capture", (RIG / "feed-capture.py").as_posix())
fc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fc)

LIVE = {"w": 1280, "h": 1024, "rs": 4, "paused": False, "t": 14.6, "text": "Session"}
NO_SIGNAL = {"w": 0, "h": 0, "rs": 0, "paused": True, "t": 0.0, "text": "No HDMI signal detected"}


def test_a_record_carries_the_pair_that_separates_a_stream_from_a_held_picture():
    rec = fc.sidecar("feed-000000.png", "2026-11-17T22:55:00Z", LIVE)
    assert rec["paused"] is False, "paused is half of the liveness evidence"
    assert rec["current_time"] == 14.6, "currentTime is the other half"
    assert rec["captured_utc"].endswith("Z"), "the distance between two frames has to be readable"
    assert rec["signal"] is True


def test_the_kvm_no_signal_card_is_not_counted_as_output():
    rec = fc.sidecar("feed-000001.png", "2026-11-17T22:55:20Z", NO_SIGNAL)
    assert rec["signal"] is False, "the KVM's own card is not the guest's desktop"
    assert not fc.has_signal(NO_SIGNAL)
    assert fc.has_signal(LIVE)


def test_a_stream_that_reports_pixels_but_is_paused_is_not_signal():
    frozen = dict(LIVE, paused=True)
    assert not fc.has_signal(frozen), ("a paused element with a full timing is exactly a held "
                                       "frame, which is the failure this tool has to name")


def test_the_link_timing_is_recorded_and_named_as_the_link():
    rec = fc.sidecar("feed-000002.png", "2026-11-17T22:55:40Z", LIVE)
    assert rec["video_width"] == 1280 and rec["video_height"] == 1024
    assert "width" not in rec and "height" not in rec, ("a bare `width` here would be read as the "
                                                        "guest's mode, which is the mistake on record")
    text = (RIG / "feed-capture.py").read_text()
    assert "not the guest's desktop mode" in text, ("the tool has to say out loud what the number "
                                                   "is not, because a bare 1280x1024 reads as a mode")


def test_the_snapshot_lands_where_the_verdict_producer_looks(tmp_path):
    frame = tmp_path / "feed" / "feed-225500.png"
    frame.parent.mkdir(parents=True)
    frame.write_bytes(b"\x89PNG fake")
    dest = fc.place_snapshot(tmp_path, frame)
    assert dest.parent == tmp_path, "desktop-evidence.py globs the run top level, not feed/"
    assert dest.read_bytes() == b"\x89PNG fake"


def test_the_login_is_reused_rather_than_restated():
    src = (RIG / "feed-capture.py").read_text()
    assert "glkvm-eyes.py" in src, "the login and its error meanings live in one file"
    assert "api/auth/login" not in src, "so the endpoint must not appear twice in the rig"
