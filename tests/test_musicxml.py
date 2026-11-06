import pytest
from helpers import harmony, note, parsed, rest

from chords.vocab import Chord, QUALITIES
from pipeline.musicxml import QUALITY_TO_KIND, chord_from_harmony, harmony_element, insert_harmony, read_measures


def test_every_chord_meaning_can_be_written():
    assert set(QUALITY_TO_KIND) == set(QUALITIES)


@pytest.mark.parametrize("quality", list(QUALITIES))
def test_chords_survive_writing_and_reading(quality):
    chord = Chord("Bb", quality, "F")
    assert chord_from_harmony(harmony_element(chord)) == chord


def test_a_chord_without_printed_text_is_read_from_its_kind_and_degrees():
    element = harmony_element(Chord("C", "7b9"))
    del element.find("kind").attrib["text"]
    assert chord_from_harmony(element) == Chord("C", "7b9")


def test_measures_list_onsets_and_rests():
    root = parsed(note(duration=2) + rest(2) + note(duration=4))
    measure = read_measures(root.find("part"))[0]
    assert [(event.onset, event.is_rest) for event in measure.events] == [(0.0, False), (1.0, True), (2.0, False)]
    assert measure.duration == 4.0


def test_stacked_chord_notes_share_one_onset():
    root = parsed(note(duration=4) + note("E", duration=4, extra="<chord/>") + note(duration=4))
    assert [event.onset for event in read_measures(root.find("part"))[0].events] == [0.0, 2.0]


def test_system_breaks_are_read():
    second = '<print new-system="yes"/>' + note(duration=8)
    measures = read_measures(parsed(note(duration=8), note(duration=8), second).find("part"))
    assert [measure.starts_system for measure in measures] == [True, False, True]


def test_harmony_goes_in_front_of_the_note_at_its_onset():
    root = parsed(note("C") + note("D") + note("E") + note("F"))
    measure = read_measures(root.find("part"))[0]
    insert_harmony(measure, Chord("G", "7"), 2.0, divisions_hint=2)
    tags = [child.tag for child in measure.element if child.tag != "attributes"]
    assert tags == ["note", "note", "harmony", "note", "note"]


def test_existing_harmony_is_readable_with_text():
    assert chord_from_harmony(parsed(harmony("D", "m7", "minor-seventh") + note()).find(".//harmony")) == Chord("D", "m7")
