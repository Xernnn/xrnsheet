"""Lyrics, placing notes by staff line, copy and paste, repeats and titles."""

import random
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest
from helpers import harmony, note, parsed

from pipeline import edit, engrave, playback, rhythm, structure
from pipeline.musicxml import to_text
from pipeline.rhythm import EditError, contexts, read_measure


def blank(**settings):
    root = structure.blank_score(**{"measures": 4} | settings)
    edit.ensure_note_ids(root)
    return root


def bar(root, index):
    return root.find("part").findall("measure")[index]


def first_id(root, index=0):
    return bar(root, index).find("note").get("id")


def write(root, *steps, length="quarter", start=None):
    """Write a melody and return the ids written, and where the cursor ended up."""
    cursor = start or first_id(root)
    written = []
    for step in steps:
        done = edit.write_note(root, cursor, length, 0, step)
        written.append(done["written"])
        cursor = done["cursor"]
    return written, cursor


def steps_in(root, index):
    return [item.findtext("pitch/step") or "-" for item in bar(root, index).findall("note")]


def spelled(root, index):
    return [(item.findtext("pitch/step"), item.findtext("pitch/alter"), item.findtext("pitch/octave")) for item in bar(root, index).findall("note") if item.find("pitch") is not None]


def lyric_text(root, note_id):
    found = edit.find_note(root, note_id).find("lyric")
    return None if found is None else (found.findtext("syllabic"), found.findtext("text"))


# Placing notes by staff line.

@pytest.mark.parametrize(("sign", "line", "change", "place", "expected"), [
    ("G", 2, 0, 0, ("F", 5)),
    ("G", 2, 0, 4, ("B", 4)),
    ("G", 2, 0, 8, ("E", 4)),
    ("G", 2, 0, 10, ("C", 4)),
    ("G", 2, 0, -1, ("G", 5)),
    ("F", 4, 0, 0, ("A", 3)),
    ("F", 4, 0, 8, ("G", 2)),
    ("C", 3, 0, 4, ("C", 4)),
    ("C", 4, 0, 2, ("C", 4)),
    ("G", 2, -1, 8, ("E", 3)),
])
def test_a_staff_line_gives_the_pitch_for_each_clef(sign, line, change, place, expected):
    root = blank()
    clef = bar(root, 0).find("attributes/clef")
    clef.find("sign").text, clef.find("line").text = sign, str(line)
    if change:
        ET.SubElement(clef, "clef-octave-change").text = str(change)
    written = edit.write_note(root, first_id(root), "quarter", 0, None, place)["written"]
    pitch = edit.find_note(root, written).find("pitch")
    assert (pitch.findtext("step"), int(pitch.findtext("octave"))) == expected


def test_a_placed_note_takes_the_key_signature():
    root = blank(fifths=2)
    written = edit.write_note(root, first_id(root), "quarter", 0, None, 0)["written"]
    assert edit.find_note(root, written).findtext("pitch/alter") == "1"


def test_a_sharp_holds_until_the_barline():
    root = blank()
    written, cursor = write(root, "F")
    edit.set_accidental(root, written[0], 1)
    later, _ = write(root, "F", start=cursor)
    note_after = edit.find_note(root, later[0])
    assert note_after.findtext("pitch/alter") == "1" and note_after.find("accidental") is None


def test_a_sharp_does_not_hold_past_the_barline():
    root = blank()
    written, _ = write(root, "F")
    edit.set_accidental(root, written[0], 1)
    later, _ = write(root, "F", start=first_id(root, 1))
    assert edit.find_note(root, later[0]).find("pitch/alter") is None


def test_a_click_can_stack_a_note_on_a_chord():
    root = blank()
    written, _ = write(root, "C")
    added = edit.add_pitch(root, written[0], "", 8)
    assert edit.find_note(root, added).find("chord") is not None
    with pytest.raises(EditError, match="already"):
        edit.add_pitch(root, written[0], "", 8)


def test_a_click_far_off_the_staff_is_refused():
    root = blank()
    with pytest.raises(EditError, match="far"):
        edit.write_note(root, first_id(root), "quarter", 0, None, -60)


# Lyrics.

def test_a_word_on_its_own_is_a_single_syllable():
    root = blank()
    written, _ = write(root, "C")
    edit.set_lyric(root, written[0], "love")
    assert lyric_text(root, written[0]) == ("single", "love")


def test_syllables_join_up_across_notes():
    root = blank()
    written, _ = write(root, "C", "D", "E")
    edit.set_lyric(root, written[0], "hal", joined=True)
    edit.set_lyric(root, written[1], "le", joined=True)
    edit.set_lyric(root, written[2], "lu")
    assert [lyric_text(root, item)[0] for item in written] == ["begin", "middle", "end"]


def test_removing_a_syllable_relinks_the_ones_around_it():
    root = blank()
    written, _ = write(root, "C", "D")
    edit.set_lyric(root, written[0], "hel", joined=True)
    edit.set_lyric(root, written[1], "lo")
    edit.set_lyric(root, written[0], "")
    assert lyric_text(root, written[0]) is None and lyric_text(root, written[1]) == ("single", "lo")


def test_lyrics_stay_with_the_note_when_its_length_changes():
    root = blank()
    written, _ = write(root, "C")
    edit.set_lyric(root, written[0], "la")
    edit.change_length(root, written[0], "half", 0)
    assert lyric_text(root, written[0]) == ("single", "la")


def test_deleting_a_note_removes_its_lyric():
    root = blank()
    written, _ = write(root, "C")
    edit.set_lyric(root, written[0], "la")
    edit.delete_note(root, written[0])
    assert edit.find_note(root, written[0]).find("lyric") is None


def test_the_next_syllable_skips_rests_and_tied_notes():
    root = blank()
    written, cursor = write(root, "C", "C")
    edit.toggle_tie(root, written[0])
    after_rest, _ = write(root, "D", start=edit.write_note(root, cursor, "quarter", 0, None)["cursor"])
    assert edit.set_lyric(root, written[0], "la")["next"] == after_rest[0]


def test_the_last_syllable_has_nowhere_to_go():
    root = blank(measures=1)
    written, _ = write(root, *"CDEF")
    assert edit.set_lyric(root, written[-1], "la")["next"] is None


def test_a_word_cant_carry_on_past_the_last_note():
    root = blank()
    written, _ = write(root, "C", "D")
    edit.set_lyric(root, written[0], "hal", joined=True)
    edit.set_lyric(root, written[1], "le", joined=True)
    assert lyric_text(root, written[1]) == ("end", "le")
    edit.set_lyric(root, written[1], "lu", joined=None)
    assert lyric_text(root, written[1]) == ("end", "lu")
    edit.set_lyric(root, written[0], "on", joined=False)
    edit.set_lyric(root, written[1], "won", joined=True)
    assert lyric_text(root, written[1]) == ("single", "won")


def test_finishing_a_lyric_can_keep_how_it_was_joined():
    root = blank()
    written, _ = write(root, "C", "D")
    edit.set_lyric(root, written[0], "hel", joined=True)
    edit.set_lyric(root, written[1], "lo")
    edit.set_lyric(root, written[0], "hal", joined=None)
    assert lyric_text(root, written[0]) == ("begin", "hal")


def test_rests_cannot_have_lyrics():
    root = blank()
    with pytest.raises(EditError, match="rests"):
        edit.set_lyric(root, first_id(root), "la")


def test_only_the_first_note_of_a_chord_carries_the_lyric():
    root = blank()
    written, _ = write(root, "C")
    edit.add_pitch(root, written[0], "E")
    edit.set_lyric(root, written[0], "la")
    assert sum(1 for _ in root.iter("lyric")) == 1


def test_lyrics_are_printed():
    root = blank()
    written, _ = write(root, "C")
    edit.set_lyric(root, written[0], "lalala")
    assert "lalala" in engrave.render_pages(to_text(root))[0]


def test_neighbouring_words_are_kept_apart(monkeypatch):
    root = blank()
    written, _ = write(root, "C", "D")
    edit.set_lyric(root, written[0], "autumnal")
    edit.set_lyric(root, written[1], "leaves")
    positions = lambda: [int(found) for found in re.findall(r'class="syl">\s*<text x="(\d+)"', engrave.render_pages(to_text(root))[0])]
    spaced = positions()
    monkeypatch.setattr(engrave, "LYRIC_WORD_SPACE", 1.2)
    tight = positions()
    assert spaced[1] - spaced[0] > tight[1] - tight[0]


def sung(step, lyric=""):
    return f"<note><pitch><step>{step}</step><octave>4</octave></pitch><duration>2</duration><voice>1</voice><type>quarter</type>{lyric}</note>"


@pytest.mark.parametrize(("second", "tags", "read_as", "typed"), [
    ('<lyric number="1"><extend type="stop"/></lyric>', ["extend"], (None, None), "end"),
    ('<lyric number="1"><text>ri</text></lyric>', ["syllabic", "text"], ("end", "ri"), "single"),
], ids=["held-syllable", "no-syllabic"])
def test_a_lyric_can_be_typed_beside_one_written_without_a_syllabic(second, tags, read_as, typed):
    """MusicXML leaves syllabic out where a held syllable ends, and may leave it out of any syllable."""
    root = parsed(sung("C", '<lyric number="1"><syllabic>begin</syllabic><text>Glo</text><extend type="start"/></lyric>') + sung("D", second) + sung("E") + sung("F"))
    edit.ensure_note_ids(root)
    edit.set_lyric(root, "note3", "a")
    assert [child.tag for child in edit.find_note(root, "note1").find("lyric")] == tags
    assert lyric_text(root, "note1") == read_as and lyric_text(root, "note3") == (typed, "a")


def test_a_lyric_follows_the_schema_order():
    root = blank()
    written, _ = write(root, "C")
    edit.set_lyric(root, written[0], "la")
    names = [child.tag for child in edit.find_note(root, written[0])]
    assert names.index("lyric") > names.index("type")


# Choosing, copying and pasting.

def test_a_range_covers_every_note_between_two_notes():
    root = blank()
    written, _ = write(root, *"CDEFG")
    assert edit.range_ids(root, written[1], written[3])["ids"] == written[1:4]


def test_a_range_can_be_chosen_backwards():
    root = blank()
    written, _ = write(root, *"CDE")
    assert edit.range_ids(root, written[2], written[0])["ids"] == written


def test_a_range_includes_every_note_of_a_chord():
    root = blank()
    written, _ = write(root, *"CD")
    added = edit.add_pitch(root, written[0], "E")
    assert edit.range_ids(root, written[0], written[0])["ids"] == [written[0], added]


def test_a_range_takes_in_a_whole_triplet():
    root = blank()
    written, cursor = write(root, "C")
    made = edit.make_tuplet(root, cursor, Fraction(1))
    notes = [item.get("id") for item in bar(root, 0).findall("note")]
    assert edit.range_ids(root, written[0], made["cursor"])["ids"] == notes[:4]


def test_a_range_starting_inside_a_triplet_takes_in_all_of_it():
    root = blank()
    edit.make_tuplet(root, first_id(root), Fraction(1))
    notes = [item.get("id") for item in bar(root, 0).findall("note")]
    assert edit.range_ids(root, notes[1], notes[1])["ids"] == notes[:3]


def test_a_range_stays_in_one_voice():
    other = "<backup><duration>8</duration></backup>"
    upper = "".join(note(step, duration=2).replace("<type>", "<voice>1</voice><type>") for step in "CDEF")
    lower = note("G", duration=8, note_type="whole").replace("<type>", "<voice>2</voice><type>")
    root = parsed(upper + other + lower)
    edit.ensure_note_ids(root)
    notes = bar(root, 0).findall("note")
    assert edit.range_ids(root, notes[0].get("id"), notes[3].get("id"))["ids"] == [item.get("id") for item in notes[:4]]


def test_pasting_repeats_a_passage_exactly():
    root = blank()
    written, _ = write(root, *"CDEF")
    edit.paste(root, first_id(root, 2), edit.copy_range(root, written[0], written[-1])["events"])
    assert spelled(root, 2) == spelled(root, 0)


def test_pasting_keeps_rhythm():
    root = blank()
    first, cursor = write(root, "C", length="half")
    second, _ = write(root, "D", "E", length="eighth", start=cursor)
    edit.paste(root, first_id(root, 1), edit.copy_range(root, first[0], second[-1])["events"])
    assert [item.findtext("type") for item in bar(root, 1).findall("note")][:3] == ["half", "eighth", "eighth"]


def test_a_pasted_passage_carries_over_the_barline():
    root = blank()
    written, cursor = write(root, *"CDE")
    events = edit.copy_range(root, written[0], written[0])["events"]
    events[0]["type"] = "half"
    edit.paste(root, cursor, events)
    last, following = bar(root, 0).findall("note")[-1], bar(root, 1).findall("note")[0]
    assert last.findtext("pitch/step") == following.findtext("pitch/step") == "C"
    assert rhythm.is_tied(last, "start") and rhythm.is_tied(following, "stop")
    assert structure.check_measures(root) == []


def test_pasting_past_the_end_adds_bars():
    root = blank(measures=1)
    written, _ = write(root, *"CDEF")
    edit.paste(root, written[2], edit.copy_range(root, written[0], written[-1])["events"])
    assert len(root.find("part").findall("measure")) == 2 and steps_in(root, 1)[:2] == ["E", "F"]


def test_a_tie_inside_a_passage_is_pasted():
    root = blank()
    written, _ = write(root, "G", "G")
    edit.toggle_tie(root, written[0])
    done = edit.paste(root, first_id(root, 1), edit.copy_range(root, written[0], written[1])["events"])
    notes = bar(root, 1).findall("note")
    assert rhythm.is_tied(notes[0], "start") and rhythm.is_tied(notes[1], "stop") and done["cursor"]


def test_a_tie_out_of_a_passage_is_not_left_hanging():
    root = blank()
    written, _ = write(root, "G", "G")
    edit.toggle_tie(root, written[0])
    edit.paste(root, first_id(root, 1), edit.copy_range(root, written[0], written[0])["events"])
    assert not rhythm.is_tied(bar(root, 1).find("note"), "start")


def test_a_triplet_is_pasted_whole():
    root = blank()
    cursor = edit.make_tuplet(root, first_id(root), Fraction(1))["cursor"]
    for step in "CDE":
        cursor = edit.write_note(root, cursor, "eighth", 0, step)["cursor"]
    notes = bar(root, 0).findall("note")
    edit.paste(root, first_id(root, 1), edit.copy_range(root, notes[0].get("id"), notes[0].get("id"))["events"])
    pasted = bar(root, 1).findall("note")[:3]
    assert [item.findtext("pitch/step") for item in pasted] == ["C", "D", "E"]
    assert all(item.find("time-modification") is not None for item in pasted)
    assert structure.check_measures(root) == []


def test_two_triplets_side_by_side_are_pasted_as_two():
    root = blank()
    cursor = first_id(root)
    for _ in range(2):
        cursor = edit.make_tuplet(root, cursor, Fraction(1))["cursor"]
        for step in "CDE":
            cursor = edit.write_note(root, cursor, "eighth", 0, step)["cursor"]
    notes = bar(root, 0).findall("note")
    edit.paste(root, first_id(root, 1), edit.copy_range(root, notes[0].get("id"), notes[5].get("id"))["events"])
    assert sum(item.find("time-modification") is not None for item in bar(root, 1).findall("note")) == 6
    assert structure.check_measures(root) == []


def test_a_triplet_that_cannot_fit_is_refused():
    root = blank()
    edit.make_tuplet(root, first_id(root), Fraction(2))
    notes = bar(root, 0).findall("note")
    clip = edit.copy_range(root, notes[0].get("id"), notes[0].get("id"))["events"]
    written, _ = write(root, "C", "D", start=bar(root, 0).findall("note")[-1].get("id"))
    with pytest.raises(EditError, match="fit"):
        edit.paste(root, written[1], clip)  # A half note's worth, with one beat left in the bar.


def test_a_chord_symbol_is_pasted_and_replaces_the_one_there():
    root = blank()
    written, _ = write(root, *"CD")
    edit.add_chord(root, written[0], "Dm7")
    target = write(root, "G", start=first_id(root, 1))[0][0]
    edit.add_chord(root, target, "G7")
    edit.paste(root, target, edit.copy_range(root, written[0], written[1])["events"])
    assert [chord["text"] for chord in edit.list_chords(root)] == ["Dm7", "Dm7"]


def test_lyrics_are_pasted():
    root = blank()
    written, _ = write(root, *"CD")
    edit.set_lyric(root, written[0], "hel", joined=True)
    edit.set_lyric(root, written[1], "lo")
    edit.paste(root, first_id(root, 1), edit.copy_range(root, written[0], written[1])["events"])
    pasted = bar(root, 1).findall("note")
    assert [item.findtext("lyric/text") for item in pasted[:2]] == ["hel", "lo"]
    assert [item.findtext("lyric/syllabic") for item in pasted[:2]] == ["begin", "end"]


def test_a_whole_bar_rest_can_be_copied_in_any_time_signature():
    root = blank(beats=5)
    events = edit.copy_range(root, first_id(root), first_id(root))["events"]
    assert sum(edit.length_of(item["type"], item["dots"]) for item in events) == 5
    edit.paste(root, first_id(root, 1), events)
    assert structure.check_measures(root) == []


def test_pasting_nothing_is_refused():
    root = blank()
    with pytest.raises(EditError, match="copied"):
        edit.paste(root, first_id(root), [])


def test_bad_pasted_pitches_are_refused():
    root = blank()
    with pytest.raises(EditError, match="read"):
        edit.paste(root, first_id(root), [{"type": "quarter", "dots": 0, "pitches": [["H", 0, 4]], "tie": False, "triplet": None, "chord": None, "lyric": None}])


# Repeats and endings.

def repeat_bars(*marks):
    """Make a score of four-beat bars, one per mark. Marks are |: :| 1 2, in any combination."""
    root = blank(measures=len(marks))
    for index, mark in enumerate(marks):
        if "|:" in mark:
            structure.toggle_repeat(root, index, "forward")
        if ":|" in mark:
            structure.toggle_repeat(root, index, "backward")
        for number in (1, 2):
            if str(number) in mark:
                structure.set_ending(root, index, index, number)
    return root


def order(root):
    return playback.play_order(root.find("part").findall("measure"))


@pytest.mark.parametrize(("marks", "expected"), [
    (["", "", ""], [0, 1, 2]),
    (["", ":|", ""], [0, 1, 0, 1, 2]),
    (["|:", ":|", ""], [0, 1, 0, 1, 2]),
    (["", "|:", ":|", ""], [0, 1, 2, 1, 2, 3]),
    (["|:", ":|", "|:", ":|"], [0, 1, 0, 1, 2, 3, 2, 3]),
    (["", ":|", ":|"], [0, 1, 0, 1, 2, 2]),
    (["|:", "", "1:|", "2"], [0, 1, 2, 0, 1, 3]),
    (["", "1:|", "2", ""], [0, 1, 0, 2, 3]),
    (["|:", "1:|", "2", "|:", ":|"], [0, 1, 0, 2, 3, 4, 3, 4]),
    (["", "1:|", "2", "", "1:|", "2"], [0, 1, 0, 2, 3, 4, 3, 5]),
])
def test_bars_are_played_in_the_right_order(marks, expected):
    assert order(repeat_bars(*marks)) == expected


def test_an_ending_can_span_several_bars():
    root = repeat_bars("", "", "", "")
    structure.toggle_repeat(root, 2, "backward")
    structure.set_ending(root, 1, 2, 1)
    structure.set_ending(root, 3, 3, 2)
    assert order(root) == [0, 1, 2, 0, 3]


def test_a_repeat_can_say_how_many_times():
    root = repeat_bars("", ":|", "")
    bar(root, 1).find("barline/repeat").set("times", "3")
    assert order(root) == [0, 1, 0, 1, 0, 1, 2]


def test_a_huge_repeat_count_still_ends():
    root = repeat_bars(":|")
    bar(root, 0).find("barline/repeat").set("times", "1000000")
    assert len(order(root)) == playback.MOST_PASSES


def test_a_repeat_can_be_taken_away():
    root = repeat_bars("", ":|", "")
    structure.toggle_repeat(root, 1, "backward")
    # Only the barline that ends the piece is left.
    assert order(root) == [0, 1, 2] and [(index, found.findtext("bar-style")) for index in range(3) for found in bar(root, index).findall("barline")] == [(2, "light-heavy")]


def test_a_first_ending_is_closed_and_a_second_is_left_open():
    root = repeat_bars("", "1:|", "2")
    assert bar(root, 1).find("barline[@location='right']/ending").get("type") == "stop"
    assert bar(root, 2).find("barline[@location='right']/ending").get("type") == "discontinue"


def test_an_ending_can_be_taken_away_and_keeps_the_repeat():
    root = repeat_bars("", "1:|", "2")
    structure.set_ending(root, 1, 1, 0)
    assert not bar(root, 1).findall("barline/ending") and bar(root, 1).findall("barline/repeat")
    assert bar(root, 2).findall("barline/ending")


def test_a_barline_follows_the_schema_order():
    root = repeat_bars("", "1:|")
    tags = [child.tag for child in bar(root, 1).findall("barline")[-1]]
    assert tags == ["bar-style", "ending", "repeat"]


def test_repeats_survive_a_rhythm_edit():
    root = repeat_bars("|:", ":|")
    written, _ = write(root, "C", length="half")
    edit.change_length(root, written[0], "quarter", 0)
    assert len(list(root.iter("repeat"))) == 2


@pytest.mark.parametrize("seed", range(200))
def test_any_arrangement_of_repeats_finishes_playing(seed):
    rng = random.Random(seed)
    marks = [rng.choice(["", "", "|:", ":|", "|::|", "1", "2", "1:|", "2:|"]) for _ in range(rng.randint(1, 12))]
    played = order(repeat_bars(*marks))
    assert played and all(0 <= index < len(marks) for index in played)
    if not any(":|" in mark for mark in marks):
        assert played == list(range(len(marks))) or "1" in "".join(marks), "with no repeats, every bar plays once"


def test_a_second_ending_with_nothing_repeating_back_is_played():
    assert order(repeat_bars("", "2", "")) == [0, 1, 2]


def test_repeated_bars_are_played_again_later():
    root = repeat_bars("", ":|", "")
    written, _ = write(root, "C", length="whole")
    played = playback.playback(root)
    assert [item["start"] for item in played["notes"]] == [0, 8] and played["total"] == 20


def test_chords_are_held_across_a_repeat():
    root = repeat_bars(":|", "")
    written, _ = write(root, "C", length="whole")
    edit.add_chord(root, written[0], "Dm7")
    assert [(item["start"], item["length"]) for item in playback.playback(root)["chords"]] == [(0, 4), (4, 8)]


def test_repeats_are_drawn():
    svg = engrave.render_pages(to_text(repeat_bars("|:", "1:|", "2")))[0]
    assert "voltaBracket" in svg


# Titles.

def test_a_title_is_saved_and_read_back():
    root = blank()
    structure.set_title(root, " Autumn Song ", "A. Writer")
    assert structure.title_of(root) == {"title": "Autumn Song", "composer": "A. Writer"}


def test_a_title_can_be_cleared():
    root = blank()
    structure.set_title(root, "Autumn Song", "A. Writer")
    structure.set_title(root, "", "")
    assert structure.title_of(root) == {"title": "", "composer": ""} and not root.findall("credit")


def test_title_elements_follow_the_schema_order():
    root = blank()
    structure.set_title(root, "Autumn Song", "A. Writer")
    names = [child.tag for child in root]
    assert names[:5] == ["work", "identification", "credit", "credit", "part-list"]


def test_a_composer_alone_still_prints():
    root = blank()
    structure.set_title(root, "", "A. Writer")
    assert "A. Writer" in engrave.render_pages(to_text(root))[0]


def test_a_composer_alone_stays_its_usual_size():
    root = blank()
    structure.set_title(root, "", "A. Writer")
    alone = engrave.render_pages(to_text(root))[0]
    structure.set_title(root, "Autumn Song", "A. Writer")
    both = engrave.render_pages(to_text(root))[0]
    size = lambda svg: re.search(r'font-size="(\d{3,})px">A. Writer', svg).group(1)
    assert size(alone) == size(both)


def test_title_and_composer_are_printed_with_the_title_larger():
    root = blank()
    structure.set_title(root, "Autumn Song", "A. Writer")
    svg = engrave.render_pages(to_text(root))[0]
    assert "Autumn Song" in svg and "A. Writer" in svg
    sizes = [int(size) for size in re.findall(r'font-size="(\d{3,})px"', svg[svg.index('class="pgHead"'):])]
    assert sizes[:2] == [round(engrave.TITLE_POINTS * engrave.POINT), round(engrave.COMPOSER_POINTS * engrave.POINT)]


def test_the_title_sits_at_the_top_of_its_frame_and_the_composer_at_the_bottom():
    """As MuseScore lays them out. Verovio alone would print both on one line."""
    root = blank()
    structure.set_title(root, "Autumn Song", "A. Writer")
    svg = engrave.render_pages(to_text(root))[0]
    header = svg[svg.index('class="pgHead"'):]
    baselines = [int(y) for y in re.findall(r' y="(\d+)"', header)][:2]
    assert baselines == [engrave.TITLE_BASELINE, engrave.COMPOSER_BASELINE]
    first_staff_line = int(re.search(r'class="staff">\s*<path d="M\d+ (\d+) ', svg).group(1))
    assert first_staff_line > engrave.FRAME_HEIGHT


def test_a_title_recognition_guessed_is_not_printed_until_saved():
    root = blank()
    ET.SubElement(ET.Element("x"), "y")
    work = ET.Element("work")
    ET.SubElement(work, "work-title").text = "Guessed Title"
    root.insert(0, work)
    assert structure.title_of(root)["title"] == "Guessed Title"
    assert "Guessed Title" not in engrave.render_pages(to_text(root))[0]


def test_a_long_title_is_refused():
    root = blank()
    with pytest.raises(EditError, match="long"):
        structure.set_title(root, "x" * 500, "")


def imported(*credits):
    """A score as another program writes one, with credits for more than the title and composer."""
    root = blank()
    work = ET.Element("work")
    ET.SubElement(work, "work-title").text = "Old Song"
    root.insert(0, work)
    position = list(root).index(root.find("part-list"))
    for number, (kind, words) in enumerate(credits):
        credit = ET.Element("credit", page="1")
        if kind:
            ET.SubElement(credit, "credit-type").text = kind
        ET.SubElement(credit, "credit-words", justify="center").text = words
        root.insert(position + number, credit)
    return root


def credit_words(root):
    return [credit.findtext("credit-words") for credit in root.findall("credit")]


def test_a_new_title_keeps_the_other_credits_of_an_imported_score():
    root = imported(("title", "Old Song"), ("subtitle", "from the musical"), ("composer", "Old Writer"), ("lyricist", "Words: A. Poet"), ("rights", "(c) 2024 Publisher"))
    structure.set_title(root, "New Song", "New Writer")
    assert credit_words(root) == ["New Song", "New Writer", "from the musical", "Words: A. Poet", "(c) 2024 Publisher"]


def test_credits_that_do_not_say_what_they_are_are_known_by_their_words():
    root = imported((None, "Old Song"), (None, "from the musical"))
    structure.set_title(root, "New Song", "")
    assert credit_words(root) == ["New Song", "from the musical"]


def test_a_new_title_prints_above_an_imported_subtitle():
    """Verovio prints credits in the order they come, and makes the first centred one the title."""
    root = imported(("subtitle", "from the musical"), ("rights", "(c) 2024 Publisher"))
    structure.set_title(root, "New Song", "")
    head = engrave.render_pages(to_text(root))[0].split('class="pgHead"')[1]
    assert head.index("New Song") < head.index("from the musical") < head.index("(c) 2024 Publisher")


# Drawing triplets.

def test_a_dotted_length_cannot_become_a_triplet():
    root = blank()
    with pytest.raises(EditError, match="dot"):
        edit.make_tuplet(root, first_id(root), Fraction(3, 2))


def test_a_written_triplet_is_beamed_and_bracketed():
    root = blank()
    cursor = edit.make_tuplet(root, first_id(root), Fraction(1))["cursor"]
    for step in "CDE":
        cursor = edit.write_note(root, cursor, "eighth", 0, step)["cursor"]
    prepared = ET.fromstring(engrave.prepare_for_drawing(to_text(root)))
    assert sum(1 for _ in prepared.iter("beam")) == 3
    assert "tupletNum" in engrave.render_pages(to_text(root))[0]


def test_a_quarter_note_triplet_is_not_beamed():
    root = blank()
    cursor = edit.make_tuplet(root, first_id(root), Fraction(2))["cursor"]
    for step in "CDE":
        cursor = edit.write_note(root, cursor, "quarter", 0, step)["cursor"]
    assert not list(ET.fromstring(engrave.prepare_for_drawing(to_text(root))).iter("beam"))


def test_the_unit_is_saved_in_a_valid_order():
    root = blank()
    edit.make_tuplet(root, first_id(root), Fraction(1))
    tags = [child.tag for child in next(root.iter("time-modification"))]
    assert tags == ["actual-notes", "normal-notes", "normal-type"]


def test_a_chord_partway_through_a_long_note_is_copied_with_it():
    root = parsed(harmony("G", "7", "dominant").replace("</harmony>", "<offset>4</offset></harmony>") + note(duration=8, note_type="whole"))
    edit.ensure_note_ids(root)
    context = next(contexts(root.find("part")))
    timeline = read_measure(context.measure, context.divisions)
    assert edit.chord_on(context, timeline.events()[0]) == "G7"


def pitches(root):
    return [(note.findtext("pitch/step"), int(float(note.findtext("pitch/alter") or 0)), int(note.findtext("pitch/octave"))) for note in root.iter("note") if note.find("pitch") is not None]


@pytest.mark.parametrize(("fifths", "start", "change", "expected"), [
    (0, "C", 1, ("C", 1, 5)),    # Up: a sharp.
    (0, "D", -1, ("D", -1, 5)),  # Down: a flat.
    (0, "E", 1, ("F", 0, 5)),    # A plain note where there is one.
    (0, "C", -1, ("B", 0, 4)),   # Across the octave's edge.
    (-1, "A", 1, ("B", -1, 4)),  # In F, the key's own B flat, not A sharp.
    (2, "E", 1, ("F", 0, 5)),    # In D, F natural is plain F, not E sharp.
    (2, "E", 2, ("F", 1, 5)),    # And the key's own F sharp a tone up.
], ids=["sharp-up", "flat-down", "plain", "octave-edge", "key-flat", "plain-outside-key", "key-sharp"])
def test_an_arrow_moves_a_note_a_semitone_and_spells_it_as_musescore_does(fifths, start, change, expected):
    root = blank(fifths=fifths)
    written, _ = write(root, start)
    edit.move_semitone(root, written[0], change)
    assert pitches(root) == [expected]


def test_twelve_semitones_up_and_down_end_on_the_same_note():
    root = blank()
    written, _ = write(root, "G")
    for change in [1] * 12 + [-1] * 12:
        edit.move_semitone(root, written[0], change)
    assert pitches(root) == [("G", 0, 4)]


def test_a_semitone_shows_its_accidental_and_a_rest_cannot_move():
    root = blank()
    written, cursor = write(root, "C")
    edit.move_semitone(root, written[0], 1)
    assert root.find(".//note/accidental").text == "sharp"
    with pytest.raises(EditError):
        edit.move_semitone(root, cursor, 1)


def test_a_letter_renames_a_selected_note_at_the_nearest_octave():
    root = blank(fifths=2)
    written, _ = write(root, "C", "B")
    assert pitches(root) == [("C", 1, 5), ("B", 0, 4)]
    edit.set_step(root, written[0], "B")
    edit.set_step(root, written[1], "C")
    assert pitches(root) == [("B", 0, 4), ("C", 1, 5)]


def test_a_letter_on_a_selected_rest_writes_a_note_of_the_rests_length():
    root = blank()
    written, cursor = write(root, "C", length="half")
    done = edit.set_step(root, cursor, "E")
    assert pitches(root) == [("C", 0, 5), ("E", 0, 5)]
    assert [note.findtext("type") for note in bar(root, 0).findall("note")] == ["half", "half"]
    assert done["cursor"] == bar(root, 0).findall("note")[1].get("id")


def test_w_and_q_double_and_halve_a_note_and_keep_its_bar_full():
    root = blank()
    written, _ = write(root, "C")
    edit.scale_length(root, written[0], True)
    assert bar(root, 0).find("note").findtext("type") == "half"
    edit.scale_length(root, written[0], False)
    edit.scale_length(root, bar(root, 0).find("note").get("id"), False)
    assert bar(root, 0).find("note").findtext("type") == "eighth"
    assert structure.check_measures(root) == []


def test_shift_w_and_q_step_through_the_dotted_lengths():
    root = blank()
    written, _ = write(root, "C")
    seen = []
    for longer in [True, True, False, False, False]:
        edit.scale_length(root, bar(root, 0).find("note").get("id"), longer, by_dot=True)
        first = bar(root, 0).find("note")
        seen.append((first.findtext("type"), len(first.findall("dot"))))
    assert seen == [("quarter", 1), ("half", 0), ("quarter", 1), ("quarter", 0), ("eighth", 1)]


def test_the_longest_and_shortest_notes_stop_there():
    root = blank()
    written, _ = write(root, "C", length="whole")
    with pytest.raises(EditError, match="longer"):
        edit.scale_length(root, written[0], True)


@pytest.mark.parametrize(("interval", "expected"), [(3, ("E", 0, 5)), (5, ("G", 0, 5)), (8, ("C", 0, 6)), (-3, ("A", 0, 4))], ids=["third", "fifth", "octave", "third-below"])
def test_alt_and_a_number_adds_that_interval_to_a_note(interval, expected):
    root = blank()
    written, _ = write(root, "C")
    added = edit.add_interval(root, written[0], interval)
    assert expected in pitches(root) and len(pitches(root)) == 2
    assert edit.find_note(root, added).find("pitch") is not None


def test_an_interval_is_counted_from_the_top_of_a_chord_and_takes_the_keys_sharps():
    root = blank(fifths=2)
    written, _ = write(root, "D")
    edit.add_interval(root, written[0], 3)
    edit.add_interval(root, written[0], 3)
    assert pitches(root) == [("D", 0, 5), ("F", 1, 5), ("A", 0, 5)]
    with pytest.raises(EditError):
        edit.add_interval(root, written[0], 1)


def stretch(root, first, last):
    return edit.range_ids(root, first, last)["ids"]


def test_removing_a_stretch_of_whole_measures_deletes_them():
    root = blank(measures=4)
    written, _ = write(root, *"CDEFGABC")
    edit.remove_range(root, stretch(root, written[0], written[7]))
    measures = root.find("part").findall("measure")
    assert len(measures) == 2 and pitches(root) == []
    assert [measure.get("number") for measure in measures] == ["1", "2"]
    assert measures[0].find("attributes/clef") is not None


def test_removing_part_of_a_measure_pulls_its_later_notes_in():
    root = blank(measures=2)
    written, _ = write(root, *"CDEF")
    edit.remove_range(root, stretch(root, written[1], written[2]))
    first = bar(root, 0)
    assert [note.findtext("pitch/step") for note in first.findall("note")] == ["C", "F", None]
    assert [note.findtext("type") for note in first.findall("note")] == ["quarter", "quarter", "half"]
    assert len(root.find("part").findall("measure")) == 2


def test_a_stretch_over_a_barline_removes_whole_measures_and_closes_up_the_ends():
    root = blank(measures=4)
    written, _ = write(root, *"CDEFGABCDEFG")
    # From the third note of the first measure to the second of the third.
    edit.remove_range(root, stretch(root, written[2], written[9]))
    measures = root.find("part").findall("measure")
    assert len(measures) == 3
    assert [[note.findtext("pitch/step") for note in measure.findall("note") if note.find("pitch") is not None] for measure in measures] == [["C", "D"], ["F", "G"], []]
    assert structure.check_measures(root) == []


def test_a_measure_goes_from_every_instrument_when_one_staff_of_it_is_removed():
    root = blank(measures=3, clef="piano")
    structure.add_part(root, "violin")
    edit.ensure_note_ids(root)
    first = root.find("part").findall("measure")[1].find("note").get("id")
    edit.remove_range(root, [first])
    assert [len(part.findall("measure")) for part in root.findall("part")] == [2, 2]
    assert structure.check_measures(root) == []


def test_the_last_measure_left_cannot_be_removed_and_nothing_selected_says_so():
    root = blank(measures=1)
    with pytest.raises(EditError, match="at least one"):
        edit.remove_range(root, [first_id(root)])
    with pytest.raises(EditError):
        edit.remove_range(root, [])
    with pytest.raises(EditError):
        edit.remove_range(root, ["no-such-note"])


def test_a_triplet_cut_through_is_refused_and_one_inside_a_removed_measure_goes_with_it():
    root = blank(measures=3)
    written, _ = write(root, *"CDEF")
    edit.toggle_tuplet(root, written[0])
    ids = [note.get("id") for note in bar(root, 0).findall("note")]
    before = to_text(root)
    with pytest.raises(EditError, match="triplet"):
        edit.remove_range(root, ids[:1])
    assert to_text(root) == before
    edit.remove_range(root, ids)
    assert len(root.find("part").findall("measure")) == 2


@pytest.mark.parametrize(("alter", "name", "glyph", "sounds"), [(2, "double-sharp", "E263", 74), (-2, "flat-flat", "E264", 70)], ids=["double-sharp", "double-flat"])
def test_a_double_accidental_is_written_drawn_and_played(alter, name, glyph, sounds):
    root = blank()
    written, _ = write(root, "C")
    edit.set_accidental(root, written[0], alter)
    assert pitches(root) == [("C", alter, 5)]
    assert root.find(".//note").findtext("accidental") == name
    assert f"#{glyph}" in engrave.render_pages(to_text(root))[0]
    assert playback.playback(root)["notes"][0]["midi"] == sounds


def test_a_double_sharp_holds_to_the_barline_and_a_plain_note_after_it_shows_a_natural():
    root = blank()
    written, _ = write(root, "F", "F", "G", "F")
    edit.set_accidental(root, written[0], 2)
    edit.set_accidental(root, written[1], 2)
    signs = [note.findtext("accidental") for note in bar(root, 0).findall("note")]
    assert signs == ["double-sharp", None, None, "natural"]


def test_a_double_sharp_moves_by_semitones_and_respells_like_any_note():
    root = blank()
    written, _ = write(root, "C")
    edit.set_accidental(root, written[0], 2)
    edit.respell_note(root, written[0])
    assert pitches(root) == [("D", 0, 5)]
    edit.set_accidental(root, written[0], -2)
    edit.move_semitone(root, written[0], 1)
    assert pitches(root) == [("C", 1, 5)]
    edit.set_accidental(root, written[0], 2)
    edit.set_accidental(root, written[0], 0)
    assert pitches(root) == [("C", 0, 5)] and root.find(".//note/accidental") is None
