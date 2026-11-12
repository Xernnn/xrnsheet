"""The timeline: what changes at each measure, and which measures each instrument plays in."""

from pipeline import concert, edit, marks, structure
from pipeline.timeline import timeline


def score():
    root = structure.blank_score(measures=5, fifths=2, clef="piano")
    structure.add_part(root, "trumpet")
    edit.ensure_note_ids(root)
    return root


def test_each_measure_says_what_changes_there():
    root = score()
    structure.set_time(root, 2, 3, 4)
    structure.set_time(root, 3, 2, 2, "cut")
    structure.set_key(root, 4, -1)
    structure.toggle_repeat(root, 1, "forward")
    structure.toggle_repeat(root, 2, "backward")
    structure.set_barline(root, 3, "double")
    first = root.find("part/measure/note").get("id")
    marks.set_tempo(root, first, "Allegro 120")
    marks.add_rehearsal(root, first)
    marks.set_jump(root, root.findall("part/measure")[3].find("note").get("id"), "dcfine")
    measures = timeline(root)["measures"]
    assert [measure["time"] for measure in measures] == ["4/4", "", "3/4", "¢", ""]
    assert [measure["key"] for measure in measures] == ["D", "", "", "", "F"]
    assert [measure["barline"] for measure in measures] == ["", "start repeat", "end repeat", "double", "final"]
    assert (measures[0]["tempo"], measures[0]["rehearsal"], measures[3]["jump"]) == ("Allegro 120", "A", "D.C. al Fine")
    assert [measure["number"] for measure in measures] == ["1", "2", "3", "4", "5"]


def test_each_instrument_says_which_measures_it_plays_in():
    root = score()
    edit.write_note(root, root.findall("part")[0].findall("measure")[1].find("note").get("id"), "quarter", 0, "C")
    lower = next(note.get("id") for note in root.findall("part")[0].findall("measure")[3].findall("note") if note.findtext("staff") == "2")
    edit.write_note(root, lower, "quarter", 0, "C")
    assert timeline(root)["parts"] == [[False, True, False, True, False], [False] * 5], "a note on either staff of the piano counts"


def test_at_concert_pitch_the_keys_are_the_concert_keys():
    root = structure.blank_score(measures=2, fifths=0, clef="trumpet")
    assert timeline(root)["measures"][0]["key"] == "D"
    concert.set_concert_pitch(root, True)
    assert timeline(concert.shown(root))["measures"][0]["key"] == "C"


def test_each_instrument_has_its_own_key():
    root = structure.blank_score(fifths=0, clef="trumpet", measures=2)
    structure.add_part(root, "piano")
    assert timeline(root)["keys"] == [[2, 2], [0, 0]], "a B flat trumpet in concert C reads D major, the piano C"
