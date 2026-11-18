import importlib.util
from pathlib import Path

RIG = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("mmio_read", RIG / "mmio-read.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BAR2 = "00000000f0000000-00000000f07fffff (prio 0, i/o): 0000:02:00.0 BAR 2"
BAR2_BASE = "00000000f0000000-00000000f07fffff (prio 1, i/o): 0000:02:00.0 base BAR 2"
BAR0 = "00000000e0000000-00000000efffffff (prio 0, i/o): 0000:02:00.0 BAR 0"


class FakeMon:
    def __init__(self, text):
        self.text = text

    def cmd(self, command, ceiling=60):
        return self.text


def test_the_map_repeats_a_bar_once_per_layer_and_the_address_is_what_repeats():
    mod = load()
    hits = mod.bar_range(FakeMon("\n".join([BAR2, BAR2_BASE, BAR2])), 2)
    assert len(hits) == 3
    assert len(mod.distinct_ranges(hits)) == 1
    assert mod.distinct_ranges(hits)[0][:2] == (0xF0000000, 0xF07FFFFF)


def test_a_bar_that_is_not_in_the_map_yields_nothing_rather_than_a_guess():
    mod = load()
    assert mod.bar_range(FakeMon("\n".join([BAR0, BAR2])), 3) == []


def test_the_bar_number_must_match_exactly():
    mod = load()
    hits = mod.bar_range(FakeMon("\n".join([BAR0, BAR2])), 0)
    assert len(mod.distinct_ranges(hits)) == 1
    assert mod.distinct_ranges(hits)[0][0] == 0xE0000000
