from helpers import note, parsed, rest

from pipeline.align import SystemLayout, closer_to_staff_above, find_system, measure_edges, note_columns, place_chord, split_into_systems
from pipeline.musicxml import read_measures


def layout(bars, notes):
    return SystemLayout(left=0, right=1000, top=200, bottom=240, unit=10, bars=bars, notes=notes)


def two_measures():
    return read_measures(parsed(note() + note() + note() + note(), note() + note() + note() + note()).find("part"))


def test_stacked_noteheads_merge_into_one_column():
    assert note_columns([100, 102, 300], unit=10) == [101, 300]


def test_barlines_set_measure_edges():
    assert measure_edges(layout([500], []), two_measures()) == [0, 500, 1000]


def test_a_double_barline_counts_once():
    assert measure_edges(layout([498, 505], []), two_measures()) == [0, 498, 1000]


def test_missing_barlines_fall_back_to_counting_notes():
    edges = measure_edges(layout([], [100, 200, 300, 400, 600, 700, 800, 900]), two_measures())
    assert edges == [0, 500, 1000]


def test_chord_lands_on_the_nearest_note():
    notes = [100, 200, 300, 400, 600, 700, 800, 900]
    assert place_chord(95, layout([500], notes), two_measures()) == (0, 0.0)
    assert place_chord(305, layout([500], notes), two_measures()) == (0, 2.0)
    assert place_chord(805, layout([500], notes), two_measures()) == (1, 2.0)


def test_chord_can_land_on_a_rest():
    measures = read_measures(parsed(note() + note() + rest() + note()).find("part"))
    system = SystemLayout(left=0, right=800, top=200, bottom=240, unit=10, bars=[], notes=[100, 300, 700])
    assert place_chord(500, system, measures) == (0, 2.0)


def test_systems_split_at_line_breaks():
    root = parsed(note(duration=8), '<print new-system="yes"/>' + note(duration=8), note(duration=8))
    assert [len(system) for system in split_into_systems(read_measures(root.find("part")))] == [1, 2]


def lines():
    return [
        SystemLayout(left=0, right=1000, top=200, bottom=240, unit=10, bars=[], notes=[]),
        SystemLayout(left=0, right=1000, top=500, bottom=540, unit=10, bars=[], notes=[]),
    ]


def test_box_just_above_a_staff_belongs_to_it():
    assert find_system((10, 150, 60, 190), lines()) == 0
    assert find_system((10, 450, 60, 490), lines()) == 1


def test_box_on_the_staff_or_far_away_is_rejected():
    assert find_system((10, 205, 30, 235), lines()[:1]) is None
    assert find_system((10, 20, 60, 50), lines()) is None


def test_distance_to_the_staff_above_is_reported():
    close = [
        SystemLayout(left=0, right=1000, top=200, bottom=240, unit=10, bars=[], notes=[]),
        SystemLayout(left=0, right=1000, top=330, bottom=370, unit=10, bars=[], notes=[]),
    ]
    assert closer_to_staff_above((10, 248, 60, 272), close, 1)
    assert not closer_to_staff_above((10, 296, 60, 320), close, 1)
    assert not closer_to_staff_above((10, 150, 60, 190), close, 0)
