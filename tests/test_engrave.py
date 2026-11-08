import re
import threading
import xml.etree.ElementTree as ET

import pytest

from helpers import note, score

from chords.vocab import Chord
from pipeline import edit, marks, structure
from pipeline.engrave import add_beams, drop_spelled_degrees, render_pages, replace_private_glyphs
from pipeline.musicxml import harmony_element, to_text

PRIVATE_SYMBOL = re.compile("[\ue000-\uf8ff]")


def eighth(step="C"):
    return note(step, duration=1, note_type="eighth")


def beam_values(root):
    return [[beam.text for beam in element.findall("beam")] for element in root.iter("note")]


def test_chord_details_are_kept_in_the_file_but_not_printed_twice():
    root = ET.fromstring(score(note(duration=8)))
    root.find(".//measure").insert(1, harmony_element(Chord("A", "7b9")))
    text = to_text(root)
    assert "degree" in text
    assert "degree" not in drop_spelled_degrees(text)


def test_private_music_glyphs_become_unicode():
    assert replace_private_glyphs("D7") == "D♭7"


def test_eighths_in_one_group_are_beamed():
    root = ET.fromstring(score(eighth() + eighth() + note() + note(duration=4)))
    add_beams(root)
    assert beam_values(root)[:2] == [["begin"], ["end"]]


def test_a_rest_breaks_a_beam():
    rest = "<note><rest/><duration>1</duration></note>"
    root = ET.fromstring(score(eighth() + rest + eighth() + note(duration=5)))
    add_beams(root)
    assert beam_values(root)[0] == [] and beam_values(root)[2] == []


def test_beams_break_at_the_middle_of_a_4_4_bar():
    root = ET.fromstring(score(eighth() * 8))
    add_beams(root)
    assert [values[0] for values in beam_values(root)] == ["begin", "continue", "continue", "end"] * 2


def test_beaming_twice_changes_nothing():
    root = ET.fromstring(score(eighth() * 8))
    add_beams(root)
    first = ET.tostring(root)
    add_beams(root)
    assert ET.tostring(root) == first


def test_rendering_works_outside_the_main_thread():
    """Verovio keeps its font path per thread, which once broke every server request."""
    results = []
    thread = threading.Thread(target=lambda: results.append(render_pages(score(note(duration=8)))))
    thread.start()
    thread.join()
    assert len(results) == 1 and "<svg" in results[0][0]


def tempo_page(unit, dots):
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    marks.set_tempo(root, next(root.iter("note")).get("id"), "Allegro 96", "quarter", dots)
    root.find(".//beat-unit").text = unit
    return render_pages(to_text(root))[0]


@pytest.mark.parametrize("dots", [0, 1])
@pytest.mark.parametrize("unit", ["breve", "whole", "half", "quarter", "eighth", "16th", "32nd"])
def test_no_private_symbol_reaches_a_tempo_mark(unit, dots):
    """A file from another program can name any of these, and an unmapped one prints as an empty box."""
    assert not PRIVATE_SYMBOL.search(tempo_page(unit, dots))


def test_a_dotted_tempo_prints_its_dot():
    plain, dotted = tempo_page("quarter", 0), tempo_page("quarter", 1)
    assert "♩." in re.sub(r"<[^>]+>", "", dotted).replace(" ", "").replace("\n", "")
    assert "♩." not in re.sub(r"<[^>]+>", "", plain).replace(" ", "").replace("\n", "")
