"""Articulations, slurs, dynamics, hairpins, tempo, text, jumps, grace notes, voices and clefs."""

import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from pipeline import edit, engrave, marks, rhythm, structure
from pipeline.musicxml import to_text
from pipeline.rhythm import EditError, contexts, line_end, read_measure


def blank(**settings):
    root = structure.blank_score(**{"measures": 3} | settings)
    edit.ensure_note_ids(root)
    return root


def bars(root, part=0):
    return root.findall("part")[part].findall("measure")


def melody(root, *steps, length="quarter", start=None, voice=None):
    cursor = start or bars(root)[0].find("note").get("id")
    written = []
    for step in steps:
        done = edit.write_note(root, cursor, length, 0, step, voice=voice)
        written.append(done["written"])
        cursor = done["cursor"]
    return written, cursor


def tune():
    root = blank()
    ids, _ = melody(root, *"CDEFGABC")
    return root, ids


def direction_onsets(root, bar=0):
    return [mark.onset for mark in read_measure(bars(root)[bar], 96).marks if mark.element.tag == "direction"]


def drawn(root):
    """Everything drawn has to come out without an error, or the page can't be shown."""
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    return "".join(engrave.render_pages(to_text(root)))


@pytest.mark.parametrize(("name", "path"), [
    ("staccato", "notations/articulations/staccato"),
    ("marcato", "notations/articulations/strong-accent"),
    ("fermata", "notations/fermata"),
    ("trill", "notations/ornaments/trill-mark"),
    ("downbow", "notations/technical/down-bow"),
])
def test_a_mark_goes_on_a_note_and_comes_off(name, path):
    root, ids = tune()
    marks.toggle_mark(root, [ids[0]], name)
    note = edit.find_note(root, ids[0])
    assert note.find(path) is not None
    drawn(root)
    marks.toggle_mark(root, [ids[0]], name)
    assert edit.find_note(root, ids[0]).find("notations") is None


def test_marks_in_the_schema_order_of_a_note():
    root, ids = tune()
    edit.set_lyric(root, ids[0], "la")
    marks.toggle_mark(root, [ids[0]], "accent")
    names = [child.tag for child in edit.find_note(root, ids[0])]
    assert names.index("notations") < names.index("lyric")


def test_a_selection_all_gets_the_mark_before_any_loses_it():
    root, ids = tune()
    marks.toggle_mark(root, [ids[0]], "staccato")
    marks.toggle_mark(root, ids[:3], "staccato")
    assert all(marks.has_mark(edit.find_note(root, note), "staccato") for note in ids[:3])
    marks.toggle_mark(root, ids[:3], "staccato")
    assert not any(marks.has_mark(edit.find_note(root, note), "staccato") for note in ids[:3])


def test_a_chord_keeps_its_mark_on_its_first_note():
    root, ids = tune()
    second = edit.add_pitch(root, ids[0], "E")
    marks.toggle_mark(root, [second], "accent")
    assert marks.has_mark(edit.find_note(root, ids[0]), "accent")
    assert not marks.has_mark(edit.find_note(root, second), "accent")


def test_rests_refuse_articulations_but_take_a_fermata():
    root = blank()
    rest = bars(root)[0].find("note").get("id")
    with pytest.raises(EditError):
        marks.toggle_mark(root, [rest], "staccato")
    marks.toggle_mark(root, [rest], "fermata")
    assert edit.find_note(root, rest).find("notations/fermata") is not None


def test_clearing_takes_every_mark_but_not_slurs():
    root, ids = tune()
    marks.toggle_slur(root, ids[0], ids[2])
    marks.toggle_mark(root, [ids[0]], "accent")
    marks.toggle_mark(root, [ids[0]], "trill")
    edit.clear_marks(root, ids[0])
    assert [child.tag for child in edit.find_note(root, ids[0]).find("notations")] == ["slur"]


def slur_ends(root):
    return [(note.get("id"), slur.get("type"), slur.get("number")) for note in root.iter("note") for slur in note.findall("notations/slur")]


def test_a_slur_joins_a_stretch():
    root, ids = tune()
    marks.toggle_slur(root, ids[0], ids[3])
    assert slur_ends(root) == [(ids[0], "start", "1"), (ids[3], "stop", "1")]
    assert 'class="slur"' in drawn(root)


def test_a_slur_on_one_note_reaches_the_next():
    root, ids = tune()
    marks.toggle_slur(root, ids[2], ids[2])
    assert slur_ends(root) == [(ids[2], "start", "1"), (ids[3], "stop", "1")]


def test_a_slur_crosses_a_barline():
    root, ids = tune()
    marks.toggle_slur(root, ids[2], ids[5])
    assert slur_ends(root) == [(ids[2], "start", "1"), (ids[5], "stop", "1")]
    drawn(root)


def test_the_same_slur_again_comes_off():
    root, ids = tune()
    marks.toggle_slur(root, ids[0], ids[3])
    marks.toggle_slur(root, ids[0], ids[3])
    assert slur_ends(root) == []
    assert edit.find_note(root, ids[0]).find("notations") is None


def test_slurs_that_touch_or_overlap_take_different_numbers():
    root, ids = tune()
    marks.toggle_slur(root, ids[0], ids[2])
    marks.toggle_slur(root, ids[2], ids[4])
    numbers = [number for _, kind, number in slur_ends(root) if kind == "start"]
    assert len(set(numbers)) == 2
    rhythm.repair_slurs(root.find("part"))
    assert len(slur_ends(root)) == 4


def test_the_last_note_has_nothing_to_slur_to():
    root = blank(measures=1)
    ids, _ = melody(root, *"CDEF")
    with pytest.raises(EditError):
        marks.toggle_slur(root, ids[3], ids[3])


def test_overwriting_one_end_takes_the_slur_away():
    root, ids = tune()
    marks.toggle_slur(root, ids[0], ids[2])
    edit.write_note(root, ids[2], "quarter", 0, "G")
    assert slur_ends(root) == []


def test_deleting_a_bar_with_one_end_takes_the_slur_away():
    root, ids = tune()
    marks.toggle_slur(root, ids[2], ids[5])
    structure.delete_measure(root, 1)
    assert slur_ends(root) == []


def dynamics_in(root):
    return [(found.tag, measure.get("number")) for measure in bars(root) for direction in measure.findall("direction") for found in direction.iterfind("direction-type/dynamics/*")]


def test_a_dynamic_stands_at_its_note():
    root, ids = tune()
    marks.set_dynamic(root, ids[2], "mf")
    assert dynamics_in(root) == [("mf", "1")]
    measure = bars(root)[0]
    assert direction_onsets(root) == [Fraction(2)]
    assert float(measure.find("direction/sound").get("dynamics")) == pytest.approx(80 * 100 / 90, abs=0.01)
    assert 'class="dynam"' in drawn(root)


def test_the_same_dynamic_again_comes_off_and_another_replaces_it():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "p")
    marks.set_dynamic(root, ids[0], "f")
    assert dynamics_in(root) == [("f", "1")]
    marks.set_dynamic(root, ids[0], "f")
    assert dynamics_in(root) == []


def test_a_dynamic_stays_put_when_the_notes_change():
    root, ids = tune()
    marks.set_dynamic(root, ids[2], "ff")
    edit.change_length(root, ids[0], "whole", 0)
    assert direction_onsets(root) == [Fraction(2)]


def test_a_dynamic_can_stand_on_the_lower_staff():
    root = blank(clef="piano")
    lower = bars(root)[0].findall("note")[1].get("id")
    written = edit.write_note(root, lower, "quarter", 0, "C")["written"]
    marks.set_dynamic(root, written, "p")
    assert bars(root)[0].find("direction").findtext("staff") == "2"
    drawn(root)


def wedges(root):
    return [(wedge.get("type"), wedge.get("number")) for wedge in root.iter("wedge")]


def test_a_hairpin_has_a_start_and_a_stop():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[0], ids[3], "crescendo")
    assert wedges(root) == [("crescendo", "1"), ("stop", "1")]
    assert sorted(direction_onsets(root)) == [Fraction(0), Fraction(4)]
    assert 'class="hairpin"' in drawn(root)


def test_a_hairpin_crosses_a_barline():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[2], ids[5], "diminuendo")
    assert wedges(root) == [("diminuendo", "1"), ("stop", "1")]
    drawn(root)


def test_the_same_hairpin_again_comes_off():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[0], ids[3], "crescendo")
    marks.toggle_hairpin(root, ids[0], ids[3], "crescendo")
    assert wedges(root) == []


def test_a_hairpin_loses_both_ends_with_either():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[2], ids[5], "crescendo")
    structure.delete_measure(root, 1)
    assert wedges(root) == []


def test_hairpins_in_a_row_stay_whole():
    root, ids = tune()
    marks.toggle_hairpin(root, ids[0], ids[1], "crescendo")
    marks.toggle_hairpin(root, ids[2], ids[3], "diminuendo")
    edit.write_note(root, ids[0], "quarter", 0, "D")
    assert len(wedges(root)) == 4


@pytest.mark.parametrize(("text", "expected"), [
    ("120", ("", 120)),
    ("Allegro 132", ("Allegro", 132)),
    ("Allegro", ("Allegro", None)),
    ("Adagio q = 60", ("Adagio", 60)),
    ("♩=96", ("", 96)),
])
def test_tempo_text_is_understood(text, expected):
    assert marks.parse_tempo(text) == expected


def test_a_tempo_is_stored_for_the_page_and_for_playback():
    root, ids = tune()
    marks.set_tempo(root, ids[0], "Allegro 132")
    direction = bars(root)[0].find("direction")
    assert direction.findtext("direction-type/words") == "Allegro"
    assert direction.findtext("direction-type/metronome/per-minute") == "132"
    assert direction.find("sound").get("tempo") == "132.0"
    assert 'class="tempo"' in drawn(root)


def test_a_dotted_beat_is_counted_in_quarters():
    root, ids = tune()
    marks.set_tempo(root, ids[0], "80", "quarter", 1)
    assert bars(root)[0].find("direction/sound").get("tempo") == "120.0"
    drawn(root)


def test_a_new_tempo_replaces_the_one_there():
    root, ids = tune()
    marks.set_tempo(root, ids[0], "100")
    marks.set_tempo(root, ids[0], "60")
    assert [found.findtext("per-minute") for found in root.iter("metronome")] == ["60"]


@pytest.mark.parametrize("text", ["5", "401", ""])
def test_a_silly_tempo_is_refused(text):
    root, ids = tune()
    with pytest.raises(EditError):
        marks.set_tempo(root, ids[0], text)


def test_a_tempo_always_sits_on_the_top_part():
    root = blank(clef="piano")
    lower = bars(root)[0].findall("note")[1].get("id")
    written = edit.write_note(root, lower, "quarter", 0, "C")["written"]
    marks.set_tempo(root, written, "90")
    assert bars(root)[0].find("direction").findtext("staff") == "1"


def test_text_comes_in_two_kinds():
    root, ids = tune()
    marks.add_text(root, ids[0], "solo")
    marks.add_text(root, ids[1], "dolce", "expression")
    plain, italic = bars(root)[0].findall("direction")
    assert plain.get("placement") == "above"
    assert italic.get("placement") == "below"
    assert italic.find("direction-type/words").get("font-style") == "italic"
    drawn(root)


def test_empty_text_is_refused():
    root, ids = tune()
    with pytest.raises(EditError):
        marks.add_text(root, ids[0], "   ")


def test_rehearsal_letters_count_up():
    root, ids = tune()
    marks.add_rehearsal(root, ids[0])
    marks.add_rehearsal(root, ids[4])
    marks.add_rehearsal(root, ids[6], "Bridge")
    assert [found.text for found in root.iter("rehearsal")] == ["A", "B", "Bridge"]
    drawn(root)


def test_swing_starts_and_stops():
    root, ids = tune()
    marks.set_swing(root, ids[0], True)
    marks.set_swing(root, ids[4], False)
    first, second = root.iter("swing")
    assert first.findtext("first") == "2"
    assert second.find("straight") is not None
    drawn(root)


@pytest.mark.parametrize(("name", "text", "attribute"), [
    ("dc", "D.C.", "dacapo"),
    ("dcfine", "D.C. al Fine", "dacapo"),
    ("dscoda", "D.S. al Coda", "dalsegno"),
    ("fine", "Fine", "fine"),
    ("tocoda", "To Coda", "tocoda"),
])
def test_a_jump_stands_at_the_end_of_its_bar(name, text, attribute):
    root, ids = tune()
    marks.set_jump(root, ids[1], name)
    direction = bars(root)[0].find("direction")
    assert direction.findtext("direction-type/words") == text
    assert direction.find("sound").get(attribute)
    assert direction_onsets(root) == [Fraction(4)]
    drawn(root)


@pytest.mark.parametrize("name", ["segno", "coda"])
def test_a_sign_stands_at_the_start_of_its_bar(name):
    root, ids = tune()
    marks.set_jump(root, ids[1], name)
    assert bars(root)[0].find(f"direction/direction-type/{name}") is not None
    assert direction_onsets(root) == [Fraction(0)]
    drawn(root)


def test_the_same_jump_again_comes_off():
    root, ids = tune()
    marks.set_jump(root, ids[1], "dc")
    marks.set_jump(root, ids[1], "dc")
    assert bars(root)[0].find("direction") is None


def test_every_mark_can_be_listed_and_removed_by_id():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "p")
    marks.set_tempo(root, ids[0], "Adagio 60")
    marks.add_text(root, ids[1], "solo")
    marks.add_rehearsal(root, ids[1])
    marks.toggle_slur(root, ids[0], ids[2])
    marks.toggle_hairpin(root, ids[0], ids[3], "crescendo")
    marks.set_jump(root, ids[1], "fine")
    marks.ensure_mark_ids(root)
    found = marks.list_marks(root)
    assert {item["kind"] for item in found} == {"dynamic", "tempo", "text", "rehearsal", "slur", "hairpin", "jump"}
    assert {item["text"] for item in found} >= {"p", "Adagio 60", "solo", "A", "Fine"}
    for item in found:
        marks.remove_placed(root, item["id"])
    assert marks.list_marks(root) == []
    assert list(root.iter("wedge")) == []
    assert list(root.iter("direction")) == []


def test_text_on_a_mark_can_be_changed():
    root, ids = tune()
    marks.set_dynamic(root, ids[0], "p")
    marks.set_tempo(root, ids[1], "Adagio 60")
    marks.add_text(root, ids[2], "solo")
    marks.ensure_mark_ids(root)
    for item, new in zip(marks.list_marks(root), ("ff", "Presto 180", "tutti")):
        marks.set_label(root, item["id"], new)
    assert [item["text"] for item in marks.list_marks(root)] == ["ff", "Presto 180", "tutti"]
    assert [found.get("tempo") for found in root.iter("sound") if found.get("tempo")] == ["180.0"]


def test_clearing_a_marks_text_removes_it():
    root, ids = tune()
    marks.add_text(root, ids[0], "solo")
    marks.ensure_mark_ids(root)
    marks.set_label(root, marks.list_marks(root)[0]["id"], "  ")
    assert marks.list_marks(root) == []


def test_marks_keep_the_same_ids_on_a_score_saved_without_them():
    root, ids = tune()
    marks.add_text(root, ids[0], "solo")
    marks.toggle_slur(root, ids[0], ids[1])
    for element in root.iter():
        element.attrib.pop("id", None) if element.tag in ("direction", "slur") else None
    first = ET.fromstring(to_text(root))
    second = ET.fromstring(to_text(root))
    marks.ensure_mark_ids(first)
    marks.ensure_mark_ids(second)
    assert to_text(first) == to_text(second)


def test_a_grace_note_goes_before_its_note():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1])
    edit.ensure_note_ids(root)
    notes = bars(root)[0].findall("note")
    assert [note.get("id") for note in notes][:3] == [ids[0], grace, ids[1]]
    found = edit.find_note(root, grace)
    assert found.find("grace").get("slash") == "yes"
    assert found.find("duration") is None
    assert [child.tag for child in found] == sorted((child.tag for child in found), key=rhythm.NOTE_ORDER.index)
    assert read_measure(bars(root)[0], 96).lines[("1", "1")][1].grace == [found]
    drawn(root)


def test_a_grace_note_can_be_moved_and_deleted():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1])
    edit.move_note(root, grace, 1)
    assert edit.find_note(root, grace).findtext("pitch/step") == "E"
    edit.delete_note(root, grace)
    assert len(bars(root)[0].findall("note")) == 4


def test_a_grace_note_stays_when_its_bar_is_rewritten():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1])
    edit.change_length(root, ids[2], "half", 0)
    assert edit.find_note(root, grace) is not None
    assert [note.get("id") for note in bars(root)[0].findall("note")][:2] == [ids[0], grace]


def test_a_grace_note_goes_with_its_note():
    root, ids = tune()
    grace = marks.add_grace(root, ids[1])
    edit.delete_note(root, ids[1])
    with pytest.raises(EditError):
        edit.find_note(root, grace)


def test_a_rest_takes_no_grace_note():
    root = blank()
    with pytest.raises(EditError):
        marks.add_grace(root, bars(root)[0].find("note").get("id"))


def lines_of(root, index=0):
    return read_measure(bars(root)[index], 96).lines


def test_a_second_voice_opens_at_the_cursor():
    root, ids = tune()
    done = edit.write_note(root, ids[1], "quarter", 0, "A", voice=2)
    lines = lines_of(root)
    assert set(lines) == {("1", "1"), ("1", "2")}
    upper = sorted(lines[("1", "2")], key=lambda event: event.onset)
    assert [event.onset for event in upper] == [0, 1, 2]
    assert [event.is_rest for event in upper] == [True, False, True]
    assert line_end(upper) == 4
    assert structure.check_measures(root) == []
    assert done["written"] != ids[1]
    assert 'class="layer"' in drawn(root)


def test_the_first_voice_isnt_touched():
    root, ids = tune()
    edit.write_note(root, ids[1], "quarter", 0, "A", voice=2)
    assert [note.get("id") for note in bars(root)[0].findall("note") if note.findtext("voice") == "1"] == ids[:4]


def test_a_second_voice_carries_on_writing():
    root, ids = tune()
    written, cursor = melody(root, "A", "B", start=ids[0], voice=2)
    assert written == [note.get("id") for note in bars(root)[0].findall("note") if note.findtext("voice") == "2" and note.find("pitch") is not None]
    assert edit.locate(root, cursor).key == ("1", "2")


def test_a_second_voice_crosses_the_barline():
    root, ids = tune()
    written, _ = melody(root, *"ABCDEF", start=ids[0], voice=2)
    assert edit.locate(root, written[-1]).context.index == 1
    assert structure.check_measures(root) == []


def test_a_note_can_land_over_the_barline_in_a_second_voice():
    root, ids = tune()
    edit.write_note(root, ids[3], "half", 0, "A", voice=2)
    assert ("1", "2") in lines_of(root, 1)
    assert structure.check_measures(root) == []
    drawn(root)


def test_voices_stay_with_their_own_staff():
    root = blank(clef="piano")
    lower = bars(root)[0].findall("note")[1].get("id")
    assert edit.locate(root, lower).key == ("2", "5")
    edit.write_note(root, lower, "quarter", 0, "C", voice=2)
    assert ("2", "6") in lines_of(root)
    assert ("1", "2") not in lines_of(root)


def test_a_staff_has_four_voices():
    root, ids = tune()
    for number in (2, 3, 4):
        edit.write_note(root, ids[0], "quarter", 0, "A", voice=number)
    assert len(lines_of(root)) == 4
    with pytest.raises(EditError):
        edit.write_note(root, ids[0], "quarter", 0, "A", voice=5)


def test_a_voice_asked_for_out_of_turn_takes_the_next_number():
    root, ids = tune()
    edit.write_note(root, ids[0], "quarter", 0, "A", voice=3)
    assert ("1", "2") in lines_of(root)


def test_a_voice_mid_note_is_refused_rather_than_cut():
    root, ids = tune()
    edit.write_note(root, ids[0], "half", 0, "E", voice=2)  # Sounds through the first voice's second beat.
    with pytest.raises(EditError):
        edit.write_note(root, ids[1], "quarter", 0, "G", voice=2)


def test_deleting_the_last_note_of_a_voice_takes_the_voice_away():
    root, ids = tune()
    done = edit.write_note(root, ids[1], "quarter", 0, "A", voice=2)
    edit.delete_note(root, done["written"])
    assert set(lines_of(root)) == {("1", "1")}
    assert structure.check_measures(root) == []


def test_a_rest_in_an_extra_voice_can_be_removed():
    root, ids = tune()
    written = edit.write_note(root, ids[1], "quarter", 0, "A", voice=2)["written"]
    line = lines_of(root)[("1", "2")]
    rest = next(event for event in line if event.is_rest).notes[0].get("id")
    edit.delete_note(root, rest)
    edit.ensure_note_ids(root)
    assert len(lines_of(root)[("1", "2")]) == 2
    assert edit.find_note(root, written) is not None
    drawn(root)


def test_a_rest_in_the_first_voice_cant_be_removed():
    root = blank()
    with pytest.raises(EditError):
        edit.delete_note(root, bars(root)[0].find("note").get("id"))


def test_notes_move_to_another_voice_and_leave_rests():
    root, ids = tune()
    edit.move_to_voice(root, [ids[1], ids[2]], 2)
    lines = lines_of(root)
    assert [event.is_rest for event in sorted(lines[("1", "1")], key=lambda event: event.onset)] == [False, True, True, False]
    moved = [event.notes[0].get("id") for event in sorted(lines[("1", "2")], key=lambda event: event.onset) if not event.is_rest]
    assert moved == ids[1:3]
    assert structure.check_measures(root) == []
    drawn(root)


def test_notes_move_back_to_the_first_voice():
    root, ids = tune()
    edit.move_to_voice(root, [ids[1]], 2)
    edit.move_to_voice(root, [ids[1]], 1)
    assert set(lines_of(root)) == {("1", "1")}
    assert [note.get("id") for note in bars(root)[0].findall("note") if note.find("pitch") is not None] == ids[:4]


def test_moving_to_the_same_voice_says_so():
    root, ids = tune()
    with pytest.raises(EditError):
        edit.move_to_voice(root, [ids[1]], 1)


def test_a_clef_change_keeps_every_pitch():
    root, ids = tune()
    before = [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in root.iter("note")]
    structure.set_clef(root, 1, 0, "1", "bass")
    known = list(contexts(root.find("part")))
    assert [item.clefs["1"] for item in known] == ["G", "F", "F"]
    assert [(note.findtext("pitch/step"), note.findtext("pitch/octave")) for note in root.iter("note")] == before
    drawn(root)


@pytest.mark.parametrize("name", ["treble", "treble8", "bass", "alto", "tenor"])
def test_every_clef_is_drawn(name):
    root, _ = tune()
    structure.set_clef(root, 0, 0, "1", name)
    assert len(bars(root)[0].find("attributes").findall("clef")) == 1
    drawn(root)


def test_a_clef_changes_where_a_click_lands():
    root, ids = tune()
    structure.set_clef(root, 0, 0, "1", "alto")
    written = edit.write_note(root, ids[0], "quarter", 0, None, line=4)["written"]
    assert (edit.find_note(root, written).findtext("pitch/step"), edit.find_note(root, written).findtext("pitch/octave")) == ("C", "4")


def test_a_clef_on_one_staff_of_a_piano_score_leaves_the_other():
    root = blank(clef="piano")
    structure.set_clef(root, 1, 0, "2", "treble")
    known = list(contexts(root.find("part")))
    assert known[1].clefs == {"1": "G", "2": "G"}
    assert known[0].clefs == {"1": "G", "2": "F"}
    drawn(root)


def test_a_note_can_be_respelled():
    root, ids = tune()
    edit.set_accidental(root, ids[0], 1)  # C sharp.
    edit.respell_note(root, ids[0])
    note = edit.find_note(root, ids[0])
    assert (note.findtext("pitch/step"), note.findtext("pitch/alter")) == ("D", "-1")
    edit.respell_note(root, ids[0])
    assert (edit.find_note(root, ids[0]).findtext("pitch/step"), edit.find_note(root, ids[0]).findtext("pitch/alter")) == ("C", "1")


def test_a_stem_can_be_flipped():
    root, ids = tune()
    edit.flip_stem(root, ids[0])
    assert edit.find_note(root, ids[0]).findtext("stem") in ("up", "down")
    first = edit.find_note(root, ids[0]).findtext("stem")
    edit.flip_stem(root, ids[0])
    assert edit.find_note(root, ids[0]).findtext("stem") != first
