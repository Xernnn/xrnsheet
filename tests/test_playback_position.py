"""What playback tells the page about where things are: the bars as they're played, and each note's place."""

from fractions import Fraction

from pipeline import edit, structure
from pipeline.playback import playback


def score(bars: int = 2, **settings):
    root = structure.blank_score(measures=bars, **settings)
    edit.ensure_note_ids(root)
    return root


def test_bars_are_listed_as_they_are_played_with_their_repeats():
    root = score(3)
    structure.toggle_repeat(root, 1, "backward")
    bars = playback(root)["bars"]
    assert [bar["index"] for bar in bars] == [0, 1, 0, 1, 2]
    assert [bar["start"] for bar in bars] == [0, 4, 8, 12, 16]
    assert all((bar["beats"], bar["beatType"], bar["length"], bar["full"]) == (4, 4, 4, 4) for bar in bars)


def test_a_pickup_bar_says_it_is_short():
    root = score(3)
    structure.set_pickup(root, Fraction(1))
    first = playback(root)["bars"][0]
    assert (first["length"], first["full"]) == (1, 4)


def test_a_compound_bar_keeps_its_time_signature():
    root = score(1, beats=6, beat_type=8)
    assert (playback(root)["bars"][0]["beats"], playback(root)["bars"][0]["beatType"]) == (6, 8)


def test_every_note_and_rest_says_where_it_is_in_its_bar():
    root = score(2)
    first = root.find("part/measure/note").get("id")
    written = edit.write_note(root, first, "half", 0, "C")
    edit.ensure_note_ids(root)
    events = playback(root)["events"]
    assert events[written["written"]] == [0, 0.0, 2.0, 72, 0, 1], "its bar, place, length, written pitch, part and staff"
    assert events[written["cursor"]] == [0, 2.0, 2.0, None, 0, 1], "the rest after it"
    second_bar = root.findall("part/measure")[1].find("note").get("id")
    assert events[second_bar] == [1, 0.0, 4.0, None, 0, 1]


def test_a_piano_note_says_which_hand_plays_it():
    root = score(1, clef="piano")
    lower = next(note for note in root.iter("note") if note.findtext("staff") == "2").get("id")
    written = edit.write_note(root, lower, "quarter", 0, "C")
    edit.ensure_note_ids(root)
    data = playback(root)
    assert data["events"][written["written"]][5] == 2
    assert [note["staff"] for note in data["notes"]] == [2]


def test_each_part_says_how_far_it_sounds_from_how_it_is_written():
    root = score(1, clef="trumpet")
    structure.add_part(root, "bass-guitar")
    assert playback(root)["transpositions"] == [-2, -12]


def test_each_note_says_which_staff_of_its_part_it_is_on():
    root = score(1, clef="piano")
    structure.add_part(root, "violin")
    edit.ensure_note_ids(root)
    notes = root.find("part/measure").findall("note")
    right = next(note.get("id") for note in notes if (note.findtext("staff") or "1") == "1")
    left = next(note.get("id") for note in notes if note.findtext("staff") == "2")
    violin = root.findall("part")[1].find("measure/note").get("id")
    edit.write_note(root, right, "whole", 0, "E")
    edit.write_note(root, left, "whole", 0, "C")
    edit.write_note(root, violin, "whole", 0, "G")
    edit.ensure_note_ids(root)
    played = sorted((note["part"], note["staff"], note["midi"] % 12) for note in playback(root)["notes"])
    assert played == [(0, 1, 4), (0, 2, 0), (1, 1, 7)], "the right hand, the left hand and the violin"
