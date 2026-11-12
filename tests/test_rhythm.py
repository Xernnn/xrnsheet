"""Reading a bar into timed events, and writing it back, must not change the music."""

import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest
from helpers import FOOTER, HEADER, harmony, note, parsed, rest, tied_note

from pipeline import edit, playback, rhythm, structure
from pipeline.rhythm import EditError, contexts, fit_divisions, read_measure, write_measure

TRUTH = sorted((Path(__file__).parent.parent / "data" / "truth").glob("*.musicxml"))


def blank(measures=4, **options):
    root = structure.blank_score(measures=measures, **options)
    edit.ensure_note_ids(root)
    return root


def first_id(root, bar=0):
    part = root.find("part")
    return part.findall("measure")[bar].find("note").get("id")


def bar_events(root, bar=0):
    """Return a bar as (onset, length, what) in quarter notes, for readable assertions."""
    context = list(contexts(root.find("part")))[bar]
    timeline = read_measure(context.measure, context.divisions)
    found = []
    for event in timeline.events():
        what = "rest" if event.is_rest else "+".join(item.findtext("pitch/step") + item.findtext("pitch/alter", "") + item.findtext("pitch/octave") for item in event.notes)
        found.append((event.onset, event.length, what))
    return found


def write_melody(root, letters, type_name="quarter"):
    at = first_id(root)
    written = []
    for letter in letters:
        result = edit.write_note(root, at, type_name, 0, letter)
        written.append(result["written"])
        at = result["cursor"]
    return written, at


@pytest.mark.parametrize("path", TRUTH, ids=lambda path: path.stem)
def test_rewriting_every_bar_keeps_the_music(path):
    root = ET.parse(path).getroot()
    before = [(item.tag, item.findtext("pitch/step"), item.findtext("duration")) for item in root.iter("note")]
    fit_divisions(root)
    for part in root.findall("part"):
        for context in list(contexts(part)):
            write_measure(context.measure, read_measure(context.measure, context.divisions), context.divisions)
    after = [(item.tag, item.findtext("pitch/step"), item.findtext("duration")) for item in root.iter("note")]
    assert sorted(step or "" for _, step, _ in before) == sorted(step or "" for _, step, _ in after)
    assert structure.check_measures(root) == []


def test_fit_divisions_keeps_every_length():
    root = parsed(note(duration=1, note_type="eighth") + note(duration=3, note_type="quarter"))
    fit_divisions(root)
    assert root.findtext(".//divisions") == "96"
    assert [item.findtext("duration") for item in root.iter("note")] == ["48", "144"]


def test_fit_divisions_twice_changes_nothing():
    root = parsed(note())
    fit_divisions(root)
    first = ET.tostring(root)
    fit_divisions(root)
    assert ET.tostring(root) == first


def test_gap_is_split_on_the_beat():
    root = blank()
    context = next(contexts(root.find("part")))
    assert rhythm.rest_lengths(Fraction(1), Fraction(4), context) == [Fraction(1), Fraction(2)]
    assert rhythm.rest_lengths(Fraction(1, 2), Fraction(2), context) == [Fraction(1, 2), Fraction(1)]


def test_compound_time_uses_dotted_rests():
    root = blank(beats=6, beat_type=8)
    context = next(contexts(root.find("part")))
    assert rhythm.rest_lengths(Fraction(3, 2), Fraction(3), context) == [Fraction(3, 2)]


def test_note_written_into_an_empty_bar_leaves_rests():
    root = blank()
    edit.write_note(root, first_id(root), "quarter", 0, "C")
    assert bar_events(root) == [(0, 1, "C5"), (1, 1, "rest"), (2, 2, "rest")]


def test_letters_choose_the_nearest_octave():
    root = blank()
    write_melody(root, ["B", "C", "A"])
    assert [what for _, _, what in bar_events(root)[:3]] == ["B4", "C5", "A4"]


def test_bass_clef_starts_in_the_bass():
    root = blank(clef="bass")
    edit.write_note(root, first_id(root), "quarter", 0, "D")
    assert bar_events(root)[0][2] == "D3"


def test_written_notes_take_the_key_signature():
    root = blank(fifths=-2)  # B flat major.
    edit.write_note(root, first_id(root), "quarter", 0, "B")
    assert bar_events(root)[0][2] == "B-14"


def test_cursor_moves_on_and_crosses_the_barline():
    root = blank()
    _, at = write_melody(root, "CDEF")
    assert at == first_id(root, 1)


def test_filling_the_last_bar_adds_no_bar_until_more_is_written():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    done = edit.write_note(root, first_id(root), "whole", 0, "C")
    assert len(root.findall(".//measure")) == 1 and done["cursor"] == edit.AFTER + done["written"]
    edit.write_note(root, done["cursor"], "quarter", 0, "D")
    assert len(root.findall(".//measure")) == 2
    assert [item.findtext("pitch/step") for item in root.iter("note") if item.find("pitch") is not None] == ["C", "D"]


def test_a_note_too_long_for_the_bar_carries_over_tied():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "half", 1, "C")
    last = edit.write_note(root, root.findall(".//note")[1].get("id"), "half", 0, "G")
    bars = root.findall(".//measure")
    first, second = bars[0].findall("note")[1], bars[1].findall("note")[0]
    assert [first.findtext("type"), second.findtext("type")] == ["quarter", "quarter"]
    assert rhythm.is_tied(first, "start") and rhythm.is_tied(second, "stop")
    assert second.findtext("pitch/step") == "G" and last["written"] == first.get("id")
    assert structure.check_measures(root) == []


def test_a_long_note_can_cross_several_bars():
    root = structure.blank_score(measures=1, beats=2, beat_type=4)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "whole", 1, "C")  # Six beats in bars of two.
    bars = root.findall(".//measure")
    notes = [bar.find("note") for bar in bars]
    assert len(bars) == 3 and [note.findtext("type") for note in notes] == ["half"] * 3
    assert rhythm.is_tied(notes[0], "start") and rhythm.is_tied(notes[1], "stop") and rhythm.is_tied(notes[1], "start") and rhythm.is_tied(notes[2], "stop")


def test_a_gap_that_needs_two_notes_is_tied_inside_the_bar():
    root = structure.blank_score(measures=2, beats=5, beat_type=8)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "half", 0, "C")
    edit.change_length(root, first_id(root), "whole", 0)  # 2.5 beats of this bar, the rest in the next.
    bar = root.findall(".//measure")[0].findall("note")
    assert [note.findtext("type") for note in bar] == ["half", "eighth"] and rhythm.is_tied(bar[0], "start")


def test_a_lengthened_note_keeps_its_id_and_its_marks():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    written = edit.write_note(root, first_id(root), "quarter", 0, "C")["written"]
    edit.add_chord(root, written, "Dm7")
    edit.change_length(root, written, "whole", 1)
    assert edit.find_note(root, written).findtext("type") == "whole" and rhythm.is_tied(edit.find_note(root, written), "start")
    assert len(root.findall(".//harmony")) == 1


def test_a_chord_carries_over_with_every_note_tied():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "half", 1, "C")
    second = root.findall(".//note")[1].get("id")
    written = edit.write_note(root, second, "quarter", 0, "E")["written"]
    edit.add_pitch(root, written, "G")
    edit.change_length(root, written, "half", 0)
    tied = [note for note in root.findall(".//measure")[1].findall("note") if rhythm.is_tied(note, "stop")]
    assert sorted(note.findtext("pitch/step") for note in tied) == ["E", "G"]
    assert sum(note.find("chord") is not None for note in tied) == 1


def test_a_carried_note_stays_in_its_own_voice():
    other = "<backup><duration>8</duration></backup>"
    voice_two = "<note><pitch><step>E</step><octave>4</octave></pitch><duration>8</duration><voice>2</voice><type>whole</type></note>"
    whole = note(duration=8, note_type="whole").replace("<type>", "<voice>1</voice><type>")
    root = parsed(whole + other + voice_two, whole)
    edit.ensure_note_ids(root)
    edit.change_length(root, root.findall(".//measure")[0].findall("note")[1].get("id"), "whole", 1)
    second = root.findall(".//measure")[1].findall("note")
    voices = {item.findtext("voice"): item for item in second}
    assert voices["1"].findtext("pitch/step") == "C" and voices["1"].findtext("type") == "whole"
    assert voices["2"].findtext("pitch/step") == "E" and rhythm.is_tied(voices["2"], "stop")


def test_a_carried_rest_is_not_tied():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "half", 1, "C")
    edit.write_note(root, root.findall(".//note")[1].get("id"), "half", 0, None)
    assert not list(root.iter("tie"))


def test_shorter_note_leaves_a_rest():
    root = blank()
    written, _ = write_melody(root, ["C"], "half")
    edit.change_length(root, written[0], "quarter", 0)
    assert bar_events(root) == [(0, 1, "C5"), (1, 1, "rest"), (2, 2, "rest")]


def test_longer_note_overwrites_what_follows():
    root = blank()
    written, _ = write_melody(root, "CDEF")
    edit.change_length(root, written[0], "half", 0)
    assert bar_events(root) == [(0, 2, "C5"), (2, 1, "E5"), (3, 1, "F5")]


def test_partly_covered_note_becomes_a_rest():
    root = blank()
    written, _ = write_melody(root, "CD", "half")
    edit.change_length(root, written[0], "half", 1)
    assert bar_events(root) == [(0, 3, "C5"), (3, 1, "rest")]


def test_dot_changes_the_written_length():
    root = blank()
    written, _ = write_melody(root, ["C"])
    edit.toggle_dot(root, written[0])
    item = edit.find_note(root, written[0])
    assert item.findtext("type") == "quarter" and item.find("dot") is not None
    assert bar_events(root)[0][1] == Fraction(3, 2)
    edit.toggle_dot(root, written[0])
    assert edit.find_note(root, written[0]).find("dot") is None


def test_a_note_in_a_triplet_can_take_two_places():
    root = triplet_bar()
    edit.change_length(root, first_id(root), "quarter", 0)
    notes = root.findall(".//note")
    assert [item.findtext("type") for item in notes[:2]] == ["quarter", "eighth"]
    assert all(item.find("time-modification") is not None for item in notes[:2])
    assert structure.check_measures(root) == []


def test_chord_symbol_stays_on_its_beat_when_its_note_is_covered():
    root = parsed(note() + harmony("F", "7") + note() + note(duration=4, note_type="half"))
    edit.ensure_note_ids(root)
    edit.change_length(root, first_id(root), "half", 0)
    chord = edit.list_chords(root)[0]
    assert (chord["measure"], chord["onset"]) == (0, 1.0)
    edit.set_chord(root, 0, "G7")
    assert edit.list_chords(root)[0]["onset"] == 1.0


def test_accidental_is_only_printed_when_needed():
    root = blank()
    written, _ = write_melody(root, "FFF")
    edit.set_accidental(root, written[0], 1)
    edit.set_accidental(root, written[1], 1)
    accidentals = [edit.find_note(root, item).findtext("accidental") for item in written]
    assert accidentals == ["sharp", None, "natural"]


def test_natural_after_a_sharp_is_printed_when_a_note_moves_there():
    root = blank()
    written, _ = write_melody(root, "FE")
    edit.set_accidental(root, written[0], 1)
    edit.move_note(root, written[1], 1)
    assert edit.find_note(root, written[1]).findtext("accidental") == "natural"


def test_octave_keeps_the_accidental():
    root = blank()
    written, _ = write_melody(root, ["F"])
    edit.set_accidental(root, written[0], 1)
    edit.move_note(root, written[0], 7)
    assert bar_events(root)[0][2] == "F15"


def test_stacking_builds_a_chord_on_one_stem():
    root = blank()
    written, _ = write_melody(root, ["C"])
    edit.add_pitch(root, written[0], "E")
    edit.add_pitch(root, written[0], "G")
    assert bar_events(root)[0] == (0, 1, "C5+E5+G5")


def test_tie_joins_matching_notes():
    root = blank()
    written, _ = write_melody(root, "CC")
    edit.toggle_tie(root, written[0])
    assert rhythm.is_tied(edit.find_note(root, written[0]), "start")
    assert rhythm.is_tied(edit.find_note(root, written[1]), "stop")
    edit.toggle_tie(root, written[0])
    assert not rhythm.is_tied(edit.find_note(root, written[1]), "stop")


def test_tie_repeats_the_note_over_a_rest_in_the_next_bar():
    root = blank()
    written, _ = write_melody(root, "CDEF")
    edit.toggle_tie(root, written[3])
    assert bar_events(root, 1)[0] == (0, 1, "F5")
    assert rhythm.is_tied(root.find("part").findall("measure")[1].find("note"), "stop")


def test_tie_refuses_a_different_note():
    root = blank()
    written, _ = write_melody(root, "CD")
    with pytest.raises(EditError, match="same pitch"):
        edit.toggle_tie(root, written[0])


def test_covering_a_tied_note_removes_the_other_end():
    root = blank()
    written, at = write_melody(root, "CC")
    edit.toggle_tie(root, written[0])
    edit.write_note(root, written[1], "quarter", 0, "E")
    assert not rhythm.is_tied(edit.find_note(root, written[0]), "start")


def test_close_gap_fixes_a_bar_that_is_too_long():
    root = parsed(note() * 5)
    edit.ensure_note_ids(root)
    assert structure.check_measures(root)[0]["problem"] == "long"
    edit.close_gap(root, first_id(root))
    assert structure.check_measures(root) == []


def test_insert_opens_a_gap_in_a_short_bar():
    root = parsed(note() * 4, note("D") * 3, note() * 4)
    edit.ensure_note_ids(root)
    assert structure.check_measures(root)[0]["problem"] == "short"
    at = first_id(root, 1)
    gap = edit.insert_rest(root, at, "quarter", 0)
    assert structure.check_measures(root) == []
    assert edit.find_note(root, gap).find("rest") is not None
    assert [what for _, _, what in bar_events(root, 1)] == ["rest", "D4", "D4", "D4"]


def test_first_bar_stays_full_when_a_note_is_removed():
    root = blank()
    written, _ = write_melody(root, "CDEF")
    edit.close_gap(root, written[1])
    assert [what for _, _, what in bar_events(root)] == ["C5", "E5", "F5", "rest"]


def test_a_real_pickup_stays_short():
    root = parsed(note() * 2, note() * 4, note() * 4)
    edit.ensure_note_ids(root)
    edit.change_length(root, first_id(root), "eighth", 0)
    assert bar_events(root)[-1][0] + bar_events(root)[-1][1] == 2


def test_bar_check_ignores_a_pickup_and_its_matching_last_bar():
    root = parsed(note(), note() * 4, note() * 3)
    edit.ensure_note_ids(root)
    assert structure.check_measures(root) == []


def test_bar_check_reports_a_bar_once_across_parts():
    root = parsed(note(), note() * 5, note() * 4)
    part = root.find("part")
    root.append(ET.fromstring(ET.tostring(part)))
    edit.ensure_note_ids(root)
    assert [item["measure"] for item in structure.check_measures(root)] == [1]


def test_added_bar_is_empty_and_fits_the_time():
    root = blank(beats=3)
    structure.insert_measure(root, 2)
    edit.ensure_note_ids(root)
    assert bar_events(root, 2) == [(0, 3, "rest")]
    assert [bar.get("number") for bar in root.find("part").findall("measure")] == ["1", "2", "3", "4", "5"]
    assert structure.check_measures(root) == []


def test_bar_added_at_the_start_takes_over_the_signature():
    root = blank(fifths=3)
    structure.insert_measure(root, 0)
    measures = root.find("part").findall("measure")
    assert measures[0].findtext("attributes/key/fifths") == "3"
    assert measures[1].find("attributes") is None


def test_deleting_a_bar_keeps_its_key_for_later_bars():
    root = blank(fifths=3)
    structure.delete_measure(root, 0)
    assert root.find("part").find("measure").findtext("attributes/key/fifths") == "3"
    assert list(contexts(root.find("part")))[-1].fifths == 3


def test_the_last_bar_cannot_be_deleted():
    root = blank(measures=1)
    with pytest.raises(EditError):
        structure.delete_measure(root, 0)


def test_deleting_a_bar_keeps_the_final_barline():
    root = blank(measures=2)
    measure = root.find("part").findall("measure")[1]
    ET.SubElement(ET.SubElement(measure, "barline", location="right"), "bar-style").text = "light-heavy"
    structure.delete_measure(root, 1)
    assert root.find("part").find("measure").find("barline") is not None


def test_bars_keep_their_original_number_for_chord_boxes():
    root = parsed(note(), note(), note())
    structure.ensure_measure_ids(root)
    structure.insert_measure(root, 1)
    origins = [structure.origin_of(bar, index) for index, bar in enumerate(root.find("part").findall("measure"))]
    assert origins == [0, None, 1, 2]


def test_key_change_keeps_notes_on_their_lines():
    root = blank()
    written, _ = write_melody(root, ["B", "F"])
    structure.set_key(root, 0, -1)  # F major: the B was really a B flat.
    assert [what for _, _, what in bar_events(root)[:2]] == ["B-14", "F4"]
    assert edit.find_note(root, written[0]).findtext("accidental") is None


def test_key_change_keeps_a_printed_accidental():
    root = blank()
    written, _ = write_melody(root, ["F"])
    edit.set_accidental(root, written[0], 1)
    structure.set_key(root, 0, -1)
    assert bar_events(root)[0][2] == "F14"
    assert edit.find_note(root, written[0]).findtext("accidental") == "sharp"


def test_key_change_stops_at_the_next_key():
    root = blank(measures=3)
    structure.set_key(root, 1, 2)
    structure.set_key(root, 2, -3)
    structure.set_key(root, 1, 4)
    assert [context.fifths for context in contexts(root.find("part"))] == [0, 4, -3]


def test_same_key_as_before_prints_no_change():
    root = blank(measures=3)
    structure.set_key(root, 1, 2)
    structure.set_key(root, 1, 0)
    assert root.find("part").findall("measure")[1].find("attributes") is None


def test_time_change_resizes_empty_bars():
    root = blank(measures=2)
    structure.set_time(root, 0, 3, 4)
    assert bar_events(root, 1) == [(0, 3, "rest")]
    assert structure.check_measures(root) == []


def test_time_change_leaves_notes_where_they_are_and_flags_them():
    root = blank()
    write_melody(root, "CDEF")
    structure.set_time(root, 0, 3, 4)
    assert len(bar_events(root)) == 4
    assert structure.check_measures(root)[0]["problem"] == "long"


def test_bad_signatures_are_refused():
    with pytest.raises(EditError):
        structure.blank_score(beat_type=3)
    with pytest.raises(EditError):
        structure.set_key(blank(), 0, 9)


def test_piano_score_has_two_staves_of_rests():
    root = blank(clef="piano", measures=2)
    assert len(list(root.find("part").find("measure").iter("note"))) == 2
    assert structure.check_measures(root) == []


def test_writing_on_the_left_hand_stays_on_its_staff():
    root = blank(clef="piano", measures=1)
    left = root.find("part").find("measure").findall("note")[1].get("id")
    edit.write_note(root, left, "quarter", 0, "C")
    written = [item for item in root.iter("note") if item.find("pitch") is not None][0]
    assert written.findtext("staff") == "2" and written.findtext("pitch/octave") == "3"


def test_every_chord_quality_has_notes():
    from chords.vocab import QUALITIES

    assert set(playback.CHORD_TONES) == set(QUALITIES)


def test_playback_times_notes_and_holds_ties():
    root = blank()
    written, _ = write_melody(root, "CCDE")
    edit.toggle_tie(root, written[0])
    data = playback.playback(root)
    assert [(item["start"], item["length"], item["midi"]) for item in data["notes"]] == [(0, 2, 72), (2, 1, 74), (3, 1, 76)]


def test_playback_plays_chords_until_the_next_one():
    root = parsed(harmony("C", "7") + note() * 2 + harmony("F", "") + note() * 2)
    data = playback.playback(root)
    assert data["chords"][0]["midi"] == [48, 52, 55, 58]
    assert (data["chords"][0]["start"], data["chords"][0]["length"]) == (0.0, 2.0)


def test_playback_uses_the_written_tempo():
    root = blank()
    assert playback.playback(root)["tempo"] == 100
    ET.SubElement(root.find("part").find("measure"), "sound", tempo="140")
    assert playback.playback(root)["tempo"] == 140


def test_helpers_keep_their_rests_valid():
    root = parsed(rest(8))
    edit.ensure_note_ids(root)
    assert structure.check_measures(root) == []


def triplet_bar():
    triplet = "<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>"
    third = f"<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><type>eighth</type>{triplet}</note>"
    root = parsed(third * 3 + note(duration=6) * 3)
    root.find(".//divisions").text = "6"
    edit.ensure_note_ids(root)
    return root


def test_a_whole_triplet_can_be_written_over_from_its_first_note():
    root = triplet_bar()
    edit.write_note(root, first_id(root), "half", 0, "E")
    notes = root.findall(".//note")
    assert not root.findall(".//time-modification") and notes[0].findtext("pitch/step") == "E"
    assert notes[0].findtext("type") == "half" and structure.check_measures(root) == []


def test_a_note_before_a_triplet_cannot_cut_it():
    triplet = "<time-modification><actual-notes>3</actual-notes><normal-notes>2</normal-notes></time-modification>"
    third = f"<note><pitch><step>C</step><octave>4</octave></pitch><duration>2</duration><type>eighth</type>{triplet}</note>"
    root = parsed(note(duration=6) + third * 3 + note(duration=12))
    root.find(".//divisions").text = "6"
    edit.ensure_note_ids(root)
    with pytest.raises(EditError, match="Triplets"):
        edit.change_length(root, first_id(root), "quarter", 1)


def test_a_note_carried_over_the_barline_cannot_cut_a_triplet_there():
    """Three quarters of a half-note triplet fit inside the half note carried in, and the rest of it doesn't."""
    root = blank(measures=2)
    cursor = edit.make_tuplet(root, first_id(root, 1), Fraction(4), keep_note=False)["cursor"]
    for step in "CDE":
        cursor = edit.write_note(root, cursor, "quarter", 0, step)["cursor"]
    with pytest.raises(EditError, match="Triplets"):
        edit.write_note(root, first_id(root), "whole", 1, "G")  # Six beats: two of them in the second bar.
    assert [note.findtext("pitch/step") for note in root.findall("part/measure")[1].iter("note")][:3] == ["C", "D", "E"]


def test_a_note_too_long_for_the_rest_of_a_triplet_is_refused():
    root = triplet_bar()
    second = root.findall(".//note")[1].get("id")
    with pytest.raises(EditError, match="triplet"):
        edit.write_note(root, second, "half", 0, "E")


def test_writing_in_a_triplet_keeps_it_a_triplet():
    root = triplet_bar()
    second = root.findall(".//note")[1].get("id")
    done = edit.write_note(root, second, "eighth", 0, "E")
    notes = root.findall(".//note")
    assert notes[1].findtext("pitch/step") == "E" and notes[1].find("time-modification") is not None
    assert done["cursor"] == notes[2].get("id") and structure.check_measures(root) == []


def test_a_triplet_is_made_from_rests_and_then_filled():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    made = edit.make_tuplet(root, first_id(root), Fraction(1))
    assert made["type"] == "eighth" and made["dots"] == 0
    cursor = made["cursor"]
    for step in "CDE":
        cursor = edit.write_note(root, cursor, made["type"], 0, step)["cursor"]
    notes = [item for item in root.iter("note") if item.find("time-modification") is not None]
    assert [item.findtext("pitch/step") for item in notes] == ["C", "D", "E"]
    assert notes[0].find("notations/tuplet").get("type") == "start" and notes[2].find("notations/tuplet").get("type") == "stop"
    assert notes[1].find("notations") is None and structure.check_measures(root) == []
    assert playback.playback(root)["notes"][1]["start"] == pytest.approx(1 / 3)


def test_a_note_turned_into_a_triplet_stays_as_its_first_note():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    written = edit.write_note(root, first_id(root), "quarter", 0, "G")["written"]
    made = edit.toggle_tuplet(root, written)
    notes = root.findall(".//note")
    assert notes[0].findtext("pitch/step") == "G" and notes[0].find("time-modification") is not None
    assert notes[1].find("rest") is not None and made["cursor"] == notes[1].get("id")


def test_a_triplet_that_would_cross_the_barline_is_refused():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "half", 1, "C")
    with pytest.raises(EditError, match="fit"):
        edit.make_tuplet(root, root.findall(".//note")[1].get("id"), Fraction(2))


def test_a_triplet_can_be_taken_back_to_rests():
    root = triplet_bar()
    cursor = edit.toggle_tuplet(root, root.findall(".//note")[1].get("id"))["cursor"]
    assert not root.findall(".//time-modification") and edit.find_note(root, cursor).find("rest") is not None
    assert structure.check_measures(root) == []


def test_deleting_a_note_keeps_the_triplet_bracket():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    cursor = edit.make_tuplet(root, first_id(root), Fraction(1))["cursor"]
    first = edit.write_note(root, cursor, "eighth", 0, "C")["written"]
    edit.delete_note(root, first)
    assert root.findall(".//note")[0].find("notations/tuplet").get("type") == "start"


def test_recognized_scores_get_triplets_too():
    # A file with 2 divisions a quarter can't hold a third of a beat until it's rescaled.
    root = parsed(note(duration=8))
    edit.ensure_note_ids(root)
    edit.write_note(root, first_id(root), "quarter", 0, "C")
    made = edit.make_tuplet(root, root.findall(".//note")[1].get("id"), Fraction(1))
    assert int(root.findtext(".//divisions")) % 3 == 0 and made["type"] == "eighth"
    assert structure.check_measures(root) == []


def test_a_note_landing_mid_way_through_another_leaves_rests_on_both_sides():
    root = parsed(note(duration=8))
    edit.ensure_note_ids(root)
    context = list(contexts(root.find("part")))[0]
    timeline = read_measure(context.measure, context.divisions)
    key, line = next(iter(timeline.lines.items()))
    length = Fraction(1)
    new = rhythm.Event(Fraction(2), length, [rhythm.make_note(length, context.divisions, key, ("G", 0, 4))])
    placed = rhythm.overwrite(line, new, context, key)
    assert [(event.onset, event.length, event.is_rest) for event in placed] == [(0, 2, True), (2, 1, False), (3, 1, True)]


def test_a_tie_carries_the_sharp_over_the_barline_without_printing_it_again():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    first = edit.write_note(root, first_id(root), "whole", 0, "C")["written"]
    edit.set_accidental(root, first, 1)
    edit.toggle_tie(root, first)
    notes = [item for item in root.iter("note") if item.find("pitch") is not None]
    assert len(notes) == 2 and notes[1].findtext("pitch/alter") == "1"
    assert notes[0].findtext("accidental") == "sharp" and notes[1].find("accidental") is None


def f_note(alter, duration, type_name, ties=(), accidental=""):
    return tied_note("F", alter, duration, type_name, ties, accidental)


@pytest.mark.parametrize(("alter", "second_bar", "printed"), [
    (1, f_note(1, 4, "half", ["stop"]) + f_note(1, 4, "half"), [None, "sharp"]),
    (1, f_note(1, 4, "half", ["stop"]) + f_note(0, 4, "half"), [None, "natural"]),
    (1, f_note(1, 2, "quarter", ["stop", "start"]) + f_note(1, 2, "quarter", ["stop"]) + f_note(1, 2, "quarter") * 2, [None, None, "sharp", None]),
    (0, f_note(0, 4, "half", ["stop"]) + f_note(0, 4, "half"), [None, None]),
], ids=["sharp-again", "natural", "tie-runs-on", "plain-f"])
def test_a_sharp_tied_over_the_barline_holds_for_the_tied_note_alone(alter, second_bar, printed):
    """The next F in the bar shows its accidental, or a player reads the key's F there."""
    root = parsed(f_note(alter, 8, "whole", ["start"]), second_bar)
    for context in contexts(root.find("part")):
        rhythm.respell(context)
    bar = root.find("part").findall("measure")[1]
    assert [item.findtext("accidental") for item in bar.findall("note")] == printed


def test_a_plain_f_typed_after_a_sharp_tied_over_the_barline_is_f_natural():
    root = parsed(f_note(1, 8, "whole", ["start"]), f_note(1, 4, "half", ["stop"]) + rest(4))
    edit.ensure_note_ids(root)
    gap = root.find("part").findall("measure")[1].findall("note")[1].get("id")
    written = edit.find_note(root, edit.write_note(root, gap, "half", 0, "F")["written"])
    assert written.find("pitch/alter") is None and written.findtext("accidental") == "natural"


def test_a_new_key_reads_a_plain_f_after_a_sharp_tied_over_the_barline_as_f_natural():
    root = parsed(f_note(1, 8, "whole", ["start"], "sharp"), f_note(1, 4, "half", ["stop"]) + f_note(0, 4, "half"))
    structure.set_key(root, 0, 0)
    tied, plain = root.find("part").findall("measure")[1].findall("note")
    assert tied.findtext("pitch/alter") == "1" and plain.find("pitch/alter") is None


def bars_with_a_clef_change(changing_bar):
    """Three bars of 3/4, one changing to bass clef after its first beat, as piano music often does."""
    opening = ('<attributes><divisions>2</divisions><key><fifths>2</fifths></key><time><beats>3</beats><beat-type>4</beat-type></time>'
               '<clef><sign>G</sign><line>2</line></clef></attributes>')
    change = '<attributes><clef><sign>F</sign><line>4</line></clef></attributes>'
    body = "".join(
        f'<measure number="{number + 1}">{opening if number == 0 else ""}{note("D") + change + note("D", 3) * 2 if number == changing_bar else note("D") * 3}</measure>'
        for number in range(3)
    )
    root = ET.fromstring(HEADER + body + FOOTER)
    edit.ensure_note_ids(root)
    structure.ensure_measure_ids(root)
    return root


def clefs_in(bar):
    return [found.findtext("clef/sign") for found in bar.findall("attributes") if found.find("clef") is not None]


def test_deleting_the_first_bar_opens_the_next_with_its_signature_and_clef():
    root = bars_with_a_clef_change(1)
    structure.delete_measure(root, 0)
    bar = root.find("part/measure")
    assert [child.tag for child in bar] == ["attributes", "note", "attributes", "note", "note"]
    assert [child.tag for child in bar.find("attributes")] == ["divisions", "key", "time", "clef"] and clefs_in(bar) == ["G", "F"]


def test_a_deleted_bar_hands_on_the_clef_it_ends_in():
    root = bars_with_a_clef_change(0)
    structure.delete_measure(root, 0)
    assert clefs_in(root.find("part/measure")) == ["F"] and next(contexts(root.find("part"))).clefs == {"1": "F"}


def test_a_bar_added_at_the_start_leaves_a_clef_change_where_it_was():
    root = bars_with_a_clef_change(0)
    structure.insert_measure(root, 0)
    added, old = root.find("part").findall("measure")[:2]
    assert clefs_in(added) == ["G"] and added.findtext("attributes/key/fifths") == "2"
    assert [child.tag for child in old] == ["note", "attributes", "note", "note"] and clefs_in(old) == ["F"]


def test_a_new_key_starts_the_bar_even_if_the_bar_changes_clef_later():
    root = bars_with_a_clef_change(1)
    structure.set_key(root, 1, -1)
    bar = root.find("part").findall("measure")[1]
    assert [child.tag for child in bar] == ["attributes", "note", "attributes", "note", "note"]
    assert bar.findtext("attributes/key/fifths") == "-1" and clefs_in(bar) == ["F"]
