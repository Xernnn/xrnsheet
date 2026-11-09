import pytest
from helpers import harmony, note, parsed, rest, tied_note

from pipeline import edit, playback, rhythm


def with_ids(root):
    edit.ensure_note_ids(root)
    return root


def pitch(root, note_id="note0"):
    element = edit.find_note(root, note_id)
    found = element.find("pitch")
    return found.findtext("step"), found.findtext("alter"), found.findtext("octave"), element.findtext("accidental")


def test_ids_are_unique_and_kept():
    root = with_ids(parsed(note() + note("D")))
    before = [element.get("id") for element in root.iter("note")]
    edit.ensure_note_ids(root)
    assert len(set(before)) == 2 and before == [element.get("id") for element in root.iter("note")]


def test_moving_up_crosses_the_octave():
    root = with_ids(parsed(note("B", 4)))
    edit.move_note(root, "note0", 1)
    assert pitch(root)[0::2] == ("C", "5")


def test_moving_a_note_follows_the_key_signature():
    root = with_ids(parsed(note("E"), fifths=2))  # D major has F sharp.
    edit.move_note(root, "note0", 1)
    assert pitch(root) == ("F", "1", "4", None)


def test_accidental_against_the_key_is_shown():
    root = with_ids(parsed(note("F"), fifths=0))
    edit.set_accidental(root, "note0", 1)
    assert pitch(root) == ("F", "1", "4", "sharp")


def test_natural_against_the_key_is_shown():
    root = with_ids(parsed(note("B"), fifths=-1))  # F major has B flat.
    edit.set_accidental(root, "note0", 0)
    assert pitch(root) == ("B", None, "4", "natural")


def test_accidental_stays_in_valid_position():
    root = with_ids(parsed(note("F").replace("</note>", "<stem>up</stem></note>")))
    edit.set_accidental(root, "note0", 1)
    tags = [child.tag for child in edit.find_note(root, "note0")]
    assert tags.index("accidental") < tags.index("stem")


def test_deleting_a_lone_note_leaves_a_rest_of_the_same_length():
    root = with_ids(parsed(note() + note("D")))
    edit.delete_note(root, "note0")
    element = edit.find_note(root, "note0")
    assert element.find("rest") is not None and element.findtext("duration") == "2"


def test_deleting_the_top_of_a_stack_keeps_the_rest_of_it():
    root = with_ids(parsed(note("C") + note("E", extra="<chord/>")))
    edit.delete_note(root, "note0")
    remaining = list(root.iter("note"))
    assert len(remaining) == 1 and remaining[0].find("chord") is None


def test_deleting_inside_a_stack_removes_only_that_note():
    root = with_ids(parsed(note("C") + note("E", extra="<chord/>")))
    edit.delete_note(root, "note1")
    assert len(list(root.iter("note"))) == 1


def test_rests_cannot_be_pitched_or_deleted():
    root = with_ids(parsed(rest()))
    with pytest.raises(edit.EditError):
        edit.move_note(root, "note0", 1)
    with pytest.raises(edit.EditError):
        edit.delete_note(root, "note0")


def test_clearing_marks_keeps_slurs():
    notations = '<notations><fermata/><slur type="start"/><articulations><accent/></articulations></notations>'
    root = with_ids(parsed(note().replace("</note>", notations + "</note>")))
    edit.clear_marks(root, "note0")
    assert [child.tag for child in edit.find_note(root, "note0").find("notations")] == ["slur"]


def test_clearing_a_note_without_marks_is_refused():
    root = with_ids(parsed(note()))
    with pytest.raises(edit.EditError):
        edit.clear_marks(root, "note0")


def test_chords_are_listed_with_their_position():
    root = parsed(note() + harmony("F", "", "major") + note(), harmony("G") + note(duration=8))
    assert [(c["text"], c["measure"], c["onset"]) for c in edit.list_chords(root)] == [("F", 0, 1.0), ("G7", 1, 0.0)]


def test_changing_a_chord_keeps_its_position():
    root = parsed(harmony("F", "", "major") + note())
    edit.set_chord(root, 0, "Bbmaj7")
    chords = edit.list_chords(root)
    assert chords[0]["text"] == "Bbmaj7" and chords[0]["onset"] == 0


def test_empty_text_removes_a_chord():
    root = parsed(harmony() + note())
    edit.set_chord(root, 0, " ")
    assert edit.list_chords(root) == []


def test_invalid_chord_changes_nothing():
    root = parsed(harmony() + note())
    with pytest.raises(edit.EditError):
        edit.set_chord(root, 0, "Hello")
    assert edit.list_chords(root)[0]["text"] == "C7"


def test_adding_to_a_stack_goes_in_front_of_the_whole_stack():
    root = with_ids(parsed(note("C") + note("E", extra="<chord/>")))
    edit.add_chord(root, "note1", "Am7")
    assert [child.tag for child in root.find(".//measure") if child.tag != "attributes"][0] == "harmony"


def test_respelling_a_tied_note_respells_every_note_tied_to_it():
    """They sound as one note. Splitting the tie would play it again at each barline."""
    root = with_ids(parsed(*(tied_note("C", 1, 8, "whole", ties) for ties in (["start"], ["stop", "start"], ["stop"]))))
    edit.respell_note(root, "note1")
    assert [pitch(root, f"note{number}")[:2] for number in range(3)] == [("D", "-1")] * 3
    assert [rhythm.is_tied(edit.find_note(root, "note1"), kind) for kind in ("start", "stop")] == [True, True]
    assert [(item["midi"], item["length"]) for item in playback.playback(root)["notes"]] == [(61, 12.0)]
