"""These use a fake reader that goes by each crop's ink, so they test the box logic alone."""

import numpy as np

from pipeline.align import SystemLayout
from pipeline.chord_boxes import Candidate, best_partition, find_chords, ink_runs, reject_stray_rows


def layouts():
    return [
        SystemLayout(left=0, right=1000, top=100, bottom=140, unit=10, bars=[], notes=[]),
        SystemLayout(left=0, right=1000, top=250, bottom=290, unit=10, bars=[], notes=[]),
    ]


def page_with(*blocks):
    """Paint dark blocks of given width, each encoding which text the fake reader returns."""
    page = np.full((320, 1000), 255, dtype=np.uint8)
    for left, top, right, bottom in blocks:
        page[top:bottom, left:right] = 0
    return page


def fake_reader(names):
    """Read a crop as the text registered for its total ink width."""

    def read(crops):
        readings = []
        for image in crops:
            width = int(np.count_nonzero((image < 128).any(axis=0)))
            readings.append((names.get(width, ""), 0.9))
        return readings

    return read


def test_ink_runs_are_found_at_wide_gaps_only():
    image = np.full((40, 100), 255, dtype=np.uint8)
    image[:, 5:15] = 0
    image[:, 18:25] = 0  # 3 pixels from the last stroke, so it's the same run.
    image[:, 60:70] = 0
    assert ink_runs(image, smallest_gap=8) == [(5, 24), (60, 69)]


def test_a_thin_line_across_a_box_does_not_hide_a_gap():
    image = np.full((40, 100), 255, dtype=np.uint8)
    image[:, 5:15] = 0
    image[:, 60:70] = 0
    image[20, :] = 0
    assert ink_runs(image, smallest_gap=8) == [(5, 14), (60, 69)]


def test_partition_prefers_covering_everything():
    spans = {(0, 1): True, (1, 3): True, (0, 2): True}
    assert best_partition(3, spans) == [(0, 1), (1, 3)]


def test_partition_keeps_one_chord_whole_rather_than_cutting_it():
    assert best_partition(2, {(0, 1): True, (1, 2): True, (0, 2): True}) == [(0, 2)]


def test_partition_can_skip_a_piece_that_is_not_a_chord():
    assert best_partition(3, {(0, 1): True, (2, 3): True}) == [(0, 1), (2, 3)]


def test_nothing_readable_gives_nothing():
    assert best_partition(2, {}) == []


def test_glued_chords_are_separated_and_keep_their_own_positions():
    page = page_with((100, 60, 130, 90), (160, 60, 200, 90))
    read = fake_reader({30: "C7", 40: "Dm7", 70: "C7Dm7"})
    found = find_chords(page, [(100.0, 60.0, 200.0, 90.0)], layouts()[:1], read)
    assert sorted((c.text, c.edges[0]) for c in found) == [("C7", 100.0), ("Dm7", 160.0)]


def test_a_box_that_reads_as_one_chord_is_never_cut():
    page = page_with((100, 60, 130, 90), (160, 60, 200, 90))
    read = fake_reader({30: "C", 40: "maj7", 70: "Cmaj7"})
    assert [c.text for c in find_chords(page, [(100.0, 60.0, 200.0, 90.0)], layouts()[:1], read)] == ["Cmaj7"]


def test_a_page_that_reads_cleanly_is_read_only_once():
    calls = []
    read = fake_reader({30: "C", 40: "maj7", 70: "Cmaj7"})
    page = page_with((100, 60, 130, 90), (160, 60, 200, 90))
    find_chords(page, [(100.0, 60.0, 200.0, 90.0)], layouts()[:1], lambda crops: calls.append(len(crops)) or read(crops))
    assert calls == [1]


def test_a_lone_extension_joins_the_chord_on_its_left():
    page = page_with((100, 60, 130, 90), (140, 50, 160, 80))
    read = fake_reader({30: "E", 20: "7", 50: "E7"})
    found = find_chords(page, [(100.0, 60.0, 130.0, 90.0), (140.0, 50.0, 160.0, 80.0)], layouts()[:1], read)
    assert [c.text for c in found] == ["E7"] and found[0].edges == (100.0, 50.0, 160.0, 90.0)


def test_a_fragment_too_far_away_stays_out():
    page = page_with((100, 60, 130, 90), (400, 60, 420, 90))
    read = fake_reader({30: "E", 20: "7"})
    found = find_chords(page, [(100.0, 60.0, 130.0, 90.0), (400.0, 60.0, 420.0, 90.0)], layouts()[:1], read)
    assert [c.text for c in found] == ["E"]


def below(text):
    return Candidate((0.0, 150.0, 40.0, 180.0), 1, True, text, 0.9)


def test_a_row_of_lyrics_is_dropped_even_when_a_word_looks_like_a_chord():
    row = [below("come"), below("on"), below("and"), below("hear"), below("Am"), below("the")]
    reject_stray_rows(row)
    assert not any(candidate.is_chord for candidate in row)


def test_a_row_of_chords_close_to_the_staff_above_is_kept():
    row = [below("Dm7"), below("G7"), below("C6"), below("")]
    reject_stray_rows(row)
    assert sum(candidate.is_chord for candidate in row) == 3


def test_a_box_out_of_line_with_its_row_is_dropped():
    row = [Candidate((x, 200.0, x + 40, 230.0), 1, False, "C7", 0.9) for x in (0.0, 100.0, 200.0, 300.0)]
    row.append(Candidate((400.0, 150.0, 440.0, 180.0), 1, False, "F", 0.9))
    reject_stray_rows(row)
    assert [candidate.text for candidate in row if candidate.is_chord] == ["C7"] * 4


def test_low_confidence_is_not_a_chord():
    assert not Candidate((0.0, 0.0, 9.0, 9.0), 0, False, "C7", 0.2).is_chord
