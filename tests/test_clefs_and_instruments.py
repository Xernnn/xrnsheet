"""MuseScore's clefs and common instruments: each reads, draws and transposes as it should."""

import re
import xml.etree.ElementTree as ET

import pytest

from pipeline import concert, edit, structure
from pipeline.engrave import render_pages
from pipeline.musicxml import to_text
from pipeline.playback import playback

# The note on each clef's middle line, as MuseScore puts it there.
MIDDLE_LINE = {
    "treble": ("B", 4), "treble8va": ("B", 5), "treble15ma": ("B", 6), "treble8": ("B", 3), "treble15mb": ("B", 2),
    "french": ("D", 5), "soprano": ("G", 4), "mezzo": ("E", 4), "alto": ("C", 4), "tenor": ("A", 3),
    "baritoneC": ("F", 3), "baritoneF": ("F", 3), "bass": ("D", 3), "bass8va": ("D", 4), "bass15ma": ("D", 5),
    "bass8vb": ("D", 2), "bass15mb": ("D", 1), "subbass": ("B", 2), "percussion": ("B", 4),
}


def test_every_clef_is_listed():
    assert set(MIDDLE_LINE) == set(structure.CLEF_SHAPES)


@pytest.mark.parametrize("name", sorted(MIDDLE_LINE))
def test_a_click_on_the_middle_line_is_the_clefs_middle_note_and_is_drawn_there(name):
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    structure.set_clef(root, 0, 0, "1", name)
    note = edit.find_note(root, edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, None, line=4)["written"])
    assert (note.findtext("pitch/step"), int(note.findtext("pitch/octave"))) == MIDDLE_LINE[name]
    page = render_pages(to_text(root))[0]
    lines = [int(found) for found in re.findall(r'<path d="M\d+ (\d+) L\d+ \d+" stroke-width="\d+" />', page[page.find('class="staff"'):])[:5]]
    head = int(re.search(r'class="notehead">\s*<use xlink:href="#E0A4[^"]*" transform="translate\(\d+, (\d+)\)', page).group(1))
    assert abs(head - lines[2]) < 10, "Verovio draws it on the middle line too"


def test_a_percussion_clef_has_no_line_and_reads_like_a_treble_clef():
    root = structure.blank_score(measures=1)
    structure.set_clef(root, 0, 0, "1", "percussion")
    clef = root.find(".//clef")
    assert clef.findtext("sign") == "percussion" and clef.find("line") is None


@pytest.mark.parametrize("name", sorted(structure.INSTRUMENTS))
def test_every_instrument_starts_a_score_that_draws_and_plays(name):
    root = structure.blank_score(clef=name, measures=1)
    edit.ensure_note_ids(root)
    instrument = structure.INSTRUMENTS[name]
    edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, "C")
    assert render_pages(to_text(root))
    assert playback(root)["programs"] == [instrument.program]
    entry = root.find("part-list/score-part/score-instrument")
    assert [child.tag for child in entry] == ["instrument-name", "instrument-sound"] if instrument.sound else ["instrument-name"]
    assert len(root.findall("part/measure/attributes/clef")) == len(instrument.clefs)


@pytest.mark.parametrize(("name", "sounds"), [
    ("piccolo", 12), ("alto-flute", -5), ("english-horn", -7), ("eb-clarinet", 3), ("a-clarinet", -3), ("bass-clarinet", -14),
    ("contrabassoon", -12), ("soprano-sax", -2), ("cornet", -2), ("flugelhorn", -2), ("glockenspiel", 24), ("xylophone", 12),
    ("celesta", 12), ("electric-bass", -12), ("c-trumpet", 0), ("soprano-recorder", 0),
])
def test_an_instrument_sounds_where_musescore_says(name, sounds):
    root = structure.blank_score(clef=name, measures=1)
    edit.ensure_note_ids(root)
    note = edit.find_note(root, edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, None, line=4)["written"])
    written = (int(note.findtext("pitch/octave")) + 1) * 12 + {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[note.findtext("pitch/step")] + int(note.findtext("pitch/alter") or 0)
    assert playback(root)["notes"][0]["midi"] - written == sounds


def test_a_soprano_recorder_is_written_with_an_8va_clef_at_its_pitch():
    root = structure.blank_score(clef="soprano-recorder", measures=1)
    assert root.findtext(".//clef/clef-octave-change") == "1" and root.find(".//transpose") is None


def test_a_baritone_saxophone_reads_in_a_bass_clef_at_concert_pitch_as_in_musescore():
    root = structure.blank_score(clef="baritone-sax", measures=1)
    edit.ensure_note_ids(root)
    edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, "E")
    concert.set_concert_pitch(root, True)
    shown = concert.shown(root)
    clef = shown.find(".//clef")
    assert (clef.findtext("sign"), clef.findtext("line"), clef.find("clef-octave-change")) == ("F", "4", None)
    note = next(note for note in shown.iter("note") if note.find("pitch") is not None)
    assert (note.findtext("pitch/step"), note.findtext("pitch/octave")) == ("G", "3"), "a written E5 sounds a thirteenth lower"


def test_a_glockenspiel_takes_a_15ma_clef_at_concert_pitch():
    root = structure.blank_score(clef="glockenspiel", measures=1)
    concert.set_concert_pitch(root, True)
    assert concert.shown(root).findtext(".//clef/clef-octave-change") == "2"


def test_a_baritone_saxophone_edited_at_concert_pitch_keeps_its_treble_clef():
    root = structure.blank_score(clef="baritone-sax", measures=2)
    edit.ensure_note_ids(root)
    concert.set_concert_pitch(root, True)
    with concert.editing(root):
        edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, None, line=8)  # G2 on a bass clef.
    clef = root.find(".//clef")
    assert (clef.findtext("sign"), clef.findtext("line")) == ("G", "2")
    note = next(note for note in root.iter("note") if note.find("pitch") is not None)
    assert (note.findtext("pitch/step"), note.findtext("pitch/octave")) == ("E", "4")


def test_the_instrument_list_groups_instruments_as_musescore_does():
    families = {item["family"] for item in structure.instrument_list()}
    assert families == {"Woodwinds", "Free Reed", "Brass", "Percussion - Pitched", "Vocals", "Keyboards", "Strings - Plucked", "Strings - Bowed", "Plain staves"}


def test_an_instrument_is_known_by_its_musicxml_sound():
    root = structure.blank_score(clef="tuba", measures=1)
    assert structure.instrument_of(root.find("part-list/score-part")).label == "Tuba"
    assert structure.instrument_of(ET.Element("score-part")) is None
