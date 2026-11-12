"""A guitar's tablature: the template, the strings and frets each note is given, and choosing them."""

import random
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

import copy

from pipeline import concert, edit, fretted, structure, technique, transpose
from pipeline.engrave import render_pages
from pipeline.midi_file import midi_bytes
from pipeline.musicxml import to_text
from pipeline.playback import playback
from pipeline.rhythm import EditError, TAB_COPY, contexts, read_measure


def guitar(measures=2, name="guitar-tab", fifths=0):
    root = structure.blank_score(clef=name, measures=measures, fifths=fifths)
    edit.ensure_note_ids(root)
    fretted.sync(root)
    return root


def synced(root):
    """Finish an edit as the server does."""
    edit.ensure_note_ids(root)
    fretted.sync(root)
    return root


def write(root, *midis, length="quarter"):
    """Write notes from the start, one MIDI key each, and return their ids."""
    cursor = next(note for note in root.iter("note") if (note.findtext("staff") or "1") == "1").get("id")
    written = []
    for midi in midis:
        done = edit.write_note(root, cursor, length, 0, None, midi=midi)
        written.append(done["written"])
        cursor = done["cursor"]
        synced(root)
    return written


def chord(root, at, *midis):
    for midi in midis:
        edit.add_pitch(root, at, "", midi=midi)
    synced(root)


def copy_of(root, note_id):
    return next(note for note in root.iter("note") if note.get("id") == note_id + TAB_COPY)


def string_and_fret(root, note_id):
    found = copy_of(root, note_id)
    return int(found.findtext("notations/technical/string")), int(found.findtext("notations/technical/fret"))


def board(root, part=0):
    return fretted.boards(root)[part]


def sounding(note, shift=0):
    return fretted.note_midi(note) + shift


def check_frets(root):
    """Every note on a linked TAB staff is played by a string and fret that sound it, and each moment's notes are on strings of their own.

    A note no string can play is missing from the TAB staff and listed as a problem.
    """
    listed = {item["note"] for item in fretted.problems(root)}
    for part, found in zip(root.findall("part"), fretted.boards(root)):
        if found is None or not found.linked:
            continue
        shifts = fretted.shifts_of(part)
        start = Fraction(0)
        for context in contexts(part):
            timeline = read_measure(context.measure, context.divisions)
            sources = {(key[1], event.onset): event for key, line in timeline.lines.items() if key[0] == found.source for event in line}
            copies = {note.get("id"): (event, note) for key, line in timeline.lines.items() if key[0] == found.linked for event in line for note in event.notes}
            strings: dict[Fraction, list[int]] = {}
            for (_, onset), event in sources.items():
                for note in event.notes:
                    copied = copies.get(note.get("id") + TAB_COPY)
                    if note.find("pitch") is None:
                        assert copied is not None and copied[1].get("print-object") == "no", "a rest is copied, and not drawn"
                        continue
                    if copied is None or copied[1].find("pitch") is None:
                        assert note.get("id") in listed, f"{note.get('id')} has no fret and isn't said to be unplayable"
                        continue
                    copy_event, copied = copied
                    assert (copy_event.onset, copy_event.length) == (event.onset, event.length)
                    string, fret = int(copied.findtext("notations/technical/string")), int(copied.findtext("notations/technical/fret"))
                    midi = sounding(note, shifts[context.index])
                    assert found.open_of(string) + found.capo + fret == midi, "the string and fret sound the note"
                    assert 0 <= fret <= found.frets
                    strings.setdefault(start + onset, []).append(string)
            for moment, used in strings.items():
                assert len(used) == len(set(used)), f"two notes at {moment} on one string"
            start += context.length


def test_a_guitar_with_tablature_has_a_standard_staff_and_a_tab_staff_of_six_strings():
    root = guitar()
    attributes = root.find("part/measure/attributes")
    clefs = attributes.findall("clef")
    assert attributes.findtext("staves") == "2"
    assert (clefs[0].findtext("sign"), clefs[0].findtext("clef-octave-change")) == ("G", "-1")
    assert (clefs[1].get("number"), clefs[1].findtext("sign")) == ("2", "TAB")
    details = attributes.find("staff-details")
    assert details.get("number") == "2" and details.findtext("staff-lines") == "6"
    assert fretted.tuning_of(details) == ("E2", "A2", "D3", "G3", "B3", "E4")
    assert board(root).describe() == {"strings": ["E2", "A2", "D3", "G3", "B3", "E4"], "tuning": "standard", "capo": 0, "frets": 19, "tab": True, "tabStaff": 2, "tunings": ["DADGAD", "dropD", "halfStepDown", "openD", "openG", "standard"]}


def test_a_bass_guitar_has_four_strings_and_sounds_an_octave_below_its_music():
    root = guitar(name="bass-guitar-tab")
    assert board(root).names == ("E1", "A1", "D2", "G2")
    assert root.findtext("part/measure/attributes/staff-details/staff-lines") == "4"
    low_e = write(root, 40)[0]  # Written E2, which sounds E1: the open fourth string.
    assert string_and_fret(root, low_e) == (4, 0)


def test_the_tab_staff_holds_a_copy_of_every_note_and_rest():
    root = guitar(measures=1)
    write(root, 64, 67)
    tab = [note for note in root.iter("note") if note.findtext("staff") == "2"]
    assert [note.get("id").endswith(TAB_COPY) for note in tab] == [True] * len(tab)
    assert [note.findtext("pitch/step") for note in tab if note.find("pitch") is not None] == ["E", "G"]
    assert all(note.get("print-object") == "no" for note in tab if note.find("rest") is not None)
    assert all(note.findtext("stem") == "none" and note.findtext("voice") == "5" for note in tab if note.find("pitch") is not None)
    assert structure.check_measures(root) == []


def test_open_strings_are_preferred_low_on_the_neck():
    root = guitar(measures=2)
    ids = write(root, 40, 45, 50, 55, 59, 64)  # The six open strings.
    assert [string_and_fret(root, note) for note in ids] == [(6, 0), (5, 0), (4, 0), (3, 0), (2, 0), (1, 0)]


def test_a_chord_is_played_on_strings_of_its_own():
    root = guitar(measures=1)
    first = write(root, 48)[0]  # C3, and a C major chord on it.
    chord(root, first, 52, 55, 60, 64)
    event = edit.locate(root, first).event
    assert [string_and_fret(root, note.get("id")) for note in event.notes] == [(5, 3), (4, 2), (3, 0), (2, 1), (1, 0)]


def test_notes_stay_near_where_the_hand_is():
    root = guitar(measures=1)
    ids = write(root, 69, 71, 72, 74)  # A4 B4 C5 D5, after which the hand is in fifth position.
    assert [string_and_fret(root, note) for note in ids] == [(1, 5), (1, 7), (1, 8), (1, 10)]
    root = guitar(measures=1)
    ids = write(root, 76, 64)  # High E at the twelfth fret, then the E an octave below.
    assert string_and_fret(root, ids[1]) == (3, 9), "the lower E is found near the twelfth fret, not on the open string"


def test_a_note_held_in_one_voice_keeps_its_string_from_the_other():
    root = guitar(measures=1)
    held = write(root, 59, length="whole")[0]  # B3 on the open B string, ringing all bar.
    edit.write_note(root, held, "quarter", 0, None, voice=2, midi=59 + 12)
    synced(root)
    upper = next(note for note in root.iter("note") if note.findtext("voice") == "2" and note.find("pitch") is not None)
    assert string_and_fret(root, held)[0] != string_and_fret(root, upper.get("id"))[0]


def test_a_tied_note_carries_on_on_its_string():
    root = guitar(measures=2)
    edit.write_note(root, root.find("part/measure/note").get("id"), "whole", 1, None, midi=57)  # Carried over the barline, tied.
    synced(root)
    first, second = [note for note in root.iter("note") if note.findtext("staff") == "2" and note.find("pitch") is not None]
    assert first.findtext("notations/technical/string") == second.findtext("notations/technical/string")
    assert first.find("tie").get("type") == "start" and second.find("tie").get("type") == "stop"


def test_a_note_no_string_can_play_is_left_off_the_tab_and_said_why():
    root = guitar(measures=1)
    low, chord_root = write(root, 36, 60)  # C2 is below a guitar's low E.
    assert copy_of(root, low).find("pitch") is None and copy_of(root, low).get("print-object") == "no"
    assert fretted.problems(root) == [{"note": low, "part": 0, "reason": "C2 is below this instrument's lowest string."}]
    chord(root, chord_root, 40, 45, 50, 55, 59, 64)  # Seven notes at once on six strings.
    reasons = [item["reason"] for item in fretted.problems(root)]
    assert len(reasons) == 2 and reasons[1].startswith("No string is free")
    check_frets(root)


def test_a_string_can_be_chosen_and_its_fret_follows():
    root = guitar(measures=1)
    note = write(root, 64)[0]
    fretted.choose_string(root, note, 2)
    synced(root)
    assert string_and_fret(root, note) == (2, 5)
    edit.flip_stem(root, note)  # An edit that leaves the note alone keeps the string chosen for it.
    synced(root)
    assert string_and_fret(root, note) == (2, 5)


@pytest.mark.parametrize(("string", "reason"), [(1, "the lowest it plays is E4"), (6, "only goes up to fret 19"), (7, "strings 1 to 6")])
def test_a_string_that_cannot_play_a_note_is_refused_with_the_reason(string, reason):
    root = guitar(measures=1)
    note = write(root, 60 if string != 6 else 88)[0]
    with pytest.raises(EditError, match=reason):
        fretted.choose_string(root, note, string)


def test_a_string_already_sounding_is_refused():
    root = guitar(measures=1)
    first = write(root, 60)[0]
    chord(root, first, 64)
    lower, upper = [item.get("id") for item in edit.locate(root, first).event.notes]
    assert string_and_fret(root, lower) == (2, 1)
    with pytest.raises(EditError, match="String 2 is already playing C4"):
        fretted.choose_string(root, upper, 2)


def test_a_tab_copy_stands_for_its_note():
    root = guitar(measures=1)
    note = write(root, 64)[0]
    edit.move_note(root, note + TAB_COPY, 1)  # A click on the fret number, then an arrow key.
    synced(root)
    assert edit.find_note(root, note).findtext("pitch/step") == "F"
    assert copy_of(root, note).findtext("pitch/step") == "F" and string_and_fret(root, note) == (1, 1)


def test_a_new_pitch_is_given_a_new_string():
    root = guitar(measures=1)
    note = write(root, 64)[0]
    fretted.choose_string(root, note, 3)
    synced(root)
    edit.move_semitone(root, note, -12)
    synced(root)
    assert string_and_fret(root, note) == (4, 2), "E3 is found afresh, not kept on the G string at fret -3"


def test_a_new_tuning_keeps_the_sound_and_moves_the_frets():
    root = guitar(measures=1)
    ids = write(root, 40, 43, 64)  # Open low E, G on the low E string, open high E.
    fretted.set_strings(root, 0, "dropD")
    synced(root)
    assert board(root).tuning == "dropD"
    assert [string_and_fret(root, note) for note in ids] == [(6, 2), (6, 5), (1, 0)], "each note stays on its string"
    assert [fretted.note_midi(edit.find_note(root, note)) for note in ids] == [40, 43, 64]
    check_frets(root)


def test_a_capo_counts_frets_from_itself():
    root = guitar(measures=1)
    ids = write(root, 45, 66)
    fretted.set_strings(root, 0, capo=2)
    synced(root)
    assert root.findtext("part/measure/attributes/staff-details/capo") == "2"
    assert string_and_fret(root, ids[1]) == (1, 0), "F#4 is the open high string with the capo on the second fret"
    assert string_and_fret(root, ids[0]) == (6, 3), "A2 can't be an open string below the capo"
    with pytest.raises(EditError, match="lowest it plays is B2 with the capo on"):
        fretted.choose_string(root, ids[0], 5)
    check_frets(root)


def test_a_custom_tuning_is_written_string_by_string():
    root = guitar(measures=1)
    fretted.set_strings(root, 0, strings=["C2", "G2", "D3", "G3", "B3", "D4"])
    synced(root)
    assert board(root).names == ("C2", "G2", "D3", "G3", "B3", "D4") and board(root).tuning == "custom"
    note = write(root, 36)[0]
    assert string_and_fret(root, note) == (6, 0)
    with pytest.raises(EditError):
        fretted.set_strings(root, 0, strings=["C2", "nonsense"])
    with pytest.raises(EditError):
        fretted.set_strings(root, 0, tuning="openQ")
    with pytest.raises(EditError):
        fretted.set_strings(root, 0, capo=13)


def test_seven_strings_give_the_tab_staff_seven_lines():
    root = guitar(measures=1)
    fretted.set_strings(root, 0, strings=["B1", "E2", "A2", "D3", "G3", "B3", "E4"])
    synced(root)
    assert root.findtext("part/measure/attributes/staff-details/staff-lines") == "7"
    assert string_and_fret(root, write(root, 35)[0]) == (7, 0)


def test_tablature_can_be_added_to_a_guitar_and_taken_away():
    root = structure.blank_score(clef="guitar", measures=2)
    edit.ensure_note_ids(root)
    ids = write(root, 64, 67)
    assert board(root).tab == []
    fretted.set_tab(root, 0, True)
    synced(root)
    assert root.findtext("part/measure/attributes/staves") == "2" and board(root).linked == "2"
    assert [string_and_fret(root, note) for note in ids] == [(1, 0), (1, 3)]
    assert structure.check_measures(root) == []
    fretted.set_strings(root, 0, "dropD")
    fretted.set_tab(root, 0, False)
    synced(root)
    assert root.find(".//staves") is None and not [note for note in root.iter("note") if note.findtext("staff") == "2"]
    assert board(root).tuning == "dropD", "the tuning stays with the guitar"
    assert render_pages(to_text(root))
    with pytest.raises(EditError):
        fretted.set_tab(root, 0, False)


def test_a_piano_has_no_strings_to_tune():
    root = structure.blank_score(clef="piano", measures=1)
    with pytest.raises(EditError, match="fretted"):
        fretted.set_strings(root, 0, "dropD")


def test_a_tab_staff_is_silent_in_playback_and_midi():
    root = guitar(measures=1)
    write(root, 64, 67, 71)
    data = playback(root)
    assert [note["midi"] for note in data["notes"]] == [64, 67, 71]
    assert {note["staff"] for note in data["notes"]} == {1}
    assert midi_bytes(data).count(bytes([0x90, 64])) == 1


def test_the_tab_staff_is_drawn_as_fret_numbers():
    root = guitar(measures=1)
    ids = write(root, 64, 67)
    svg = render_pages(to_text(root))[0]
    for note, fret in zip(ids, ("0", "3")):
        drawn = re.search(rf'<g id="{note}{TAB_COPY}" class="note">(.*?)</g>', svg, re.S)
        assert drawn and re.search(rf">{fret}</tspan>", drawn.group(1))
    assert "tabDurSym" not in svg, "no rhythm stems over the tablature"
    assert "-2147483647" not in svg


def test_a_bass_guitar_at_concert_pitch_keeps_its_tablature():
    root = guitar(name="bass-guitar-tab")
    note = write(root, 43)[0]
    concert.set_concert_pitch(root, True)
    shown = concert.shown(root)
    clefs = shown.find("part/measure/attributes").findall("clef")
    assert (clefs[0].findtext("sign"), clefs[0].findtext("clef-octave-change")) == ("F", "-1") and clefs[1].findtext("sign") == "TAB"
    with concert.editing(root):
        edit.move_semitone(root, note, 2)  # Written G2 to A2, which sounds A1: the open A string.
    synced(root)
    assert string_and_fret(root, note) == (3, 0)
    with concert.editing(root):
        fretted.choose_string(root, note, 4)
    synced(root)
    assert string_and_fret(root, note) == (4, 5)
    check_frets(root)


def test_transposing_a_score_moves_the_frets_with_the_notes():
    root = guitar(measures=1)
    ids = write(root, 60, 64)
    transpose.transpose_score(root, transpose.INTERVALS["M2"])
    synced(root)
    assert [string_and_fret(root, note) for note in ids] == [(2, 3), (1, 2)]
    check_frets(root)


def test_syncing_twice_changes_nothing():
    root = guitar(measures=2)
    write(root, 40, 52, 64, 59, 62, 67)
    first = to_text(root)
    fretted.sync(root)
    assert to_text(root) == first


def test_a_musicxml_file_keeps_its_strings_through_saving_and_opening():
    root = guitar(measures=1)
    ids = write(root, 64, 69)
    fretted.choose_string(root, ids[1], 2)
    synced(root)
    opened = ET.fromstring(to_text(root).split("\n", 1)[1])
    synced(opened)
    assert to_text(opened) == to_text(root)


def test_a_guitar_and_tablature_from_another_program_is_linked_and_keeps_its_frets():
    """MuseScore writes a linked tablature as a second staff of the part, its notes with no ids and their own frets."""
    root = guitar(measures=1)
    ids = write(root, 64, 69)
    fretted.choose_string(root, ids[1], 2)
    synced(root)
    for note in root.iter("note"):
        note.attrib.pop("id", None)
    synced(root)
    first, second = [note.get("id") for note in root.iter("note") if note.findtext("staff") is None and note.find("pitch") is not None]
    assert string_and_fret(root, second) == (2, 10), "the A4 a player put on the B string stays there"


def test_a_tablature_staff_on_its_own_gets_strings_where_it_is():
    root = guitar(measures=1)
    write(root, 64, 69)
    # Take the standard staff away, leaving the tablature as the only staff.
    single = ET.fromstring(to_text(root).split("\n", 1)[1])
    measure = single.find("part/measure")
    for child in list(measure):
        if child.tag == "note" and (child.findtext("staff") or "1") == "1" or child.tag == "backup":
            measure.remove(child)
    attributes = measure.find("attributes")
    attributes.remove(attributes.find("staves"))
    attributes.remove(attributes.findall("clef")[0])
    for found in measure.iter():
        if found.tag in ("clef", "staff-details"):
            found.attrib.pop("number", None)
        if found.tag == "note":
            found.remove(found.find("staff"))
            technical = found.find("notations/technical")
            if technical is not None:
                for child in list(technical):
                    technical.remove(child)
    synced(single)
    notes = [note for note in single.iter("note") if note.find("pitch") is not None]
    assert [(note.findtext("notations/technical/string"), note.findtext("notations/technical/fret")) for note in notes] == [("1", "0"), ("1", "5")]
    assert render_pages(to_text(single))


def test_a_tab_staff_keeps_its_clef():
    root = guitar(measures=1)
    with pytest.raises(EditError, match="TAB clef"):
        structure.set_clef(root, 0, 0, "2", "treble")


def test_a_tab_staff_shows_no_accidentals():
    root = guitar(measures=1)
    write(root, 66)
    assert not [note for note in root.iter("note") if note.findtext("staff") == "2" and note.find("accidental") is not None]
    assert [note for note in root.iter("note") if note.findtext("staff") is None and note.find("accidental") is not None]


@pytest.mark.parametrize("seed", range(30))
def test_every_string_and_fret_sounds_the_note_in_any_tuning_and_capo(seed):
    """Random notes and chords, in a random tuning with the capo anywhere, are all played where they sound."""
    rng = random.Random(seed)
    name = rng.choice(["guitar-tab", "electric-guitar-tab", "bass-guitar-tab"])
    root = guitar(measures=2, name=name)
    standard = board(root).standard
    tuning = rng.choice(sorted(fretted.tunings_for(standard)))
    fretted.set_strings(root, 0, tuning, capo=rng.randint(0, 7))
    synced(root)
    found = board(root)
    shift = -12 if name.startswith("bass") else 0
    low, high = min(found.opens) + found.capo - shift, max(found.opens) + found.capo + found.frets - shift
    cursor = next(note for note in root.iter("note") if (note.findtext("staff") or "1") == "1").get("id")
    for _ in range(rng.randint(4, 10)):
        done = edit.write_note(root, cursor, rng.choice(["quarter", "eighth", "half"]), 0, None, midi=rng.randint(low - 2, high + 2))
        if rng.random() < 0.4:
            for _ in range(rng.randint(1, 4)):
                try:
                    edit.add_pitch(root, done["written"], "", midi=rng.randint(low, low + 24))
                except EditError:
                    pass
        cursor = done["cursor"]
        synced(root)
        check_frets(root)
        if cursor.startswith(edit.AFTER):
            break


def fretted_edit(root, rng):
    """Make one random change to how the guitar is played: a string for a note, a tuning, a capo, or its tablature."""
    found = board(root)
    if found is None:
        return
    notes = [note.get("id") for note in root.iter("note") if note.find("pitch") is not None]
    choice = rng.choice(["string", "string", "string", "tuning", "capo", "tab", "technique", "technique", "join", "line"])
    root.set("last-edit", f"fretted {choice}")
    if choice in ("technique", "join", "line") and notes:
        from pipeline import technique

        first, other = rng.choice(notes), rng.choice(notes)
        if choice == "technique":
            rng.choice([
                lambda: technique.set_string_number(root, [first], rng.randint(0, found.count)),
                lambda: technique.toggle_bend(root, [first], rng.choice(list(technique.BENDS))),
                lambda: technique.toggle_harmonic(root, [first], rng.choice(["natural", "artificial"])),
                lambda: technique.toggle_dead_note(root, [first]),
                lambda: technique.set_fingering(root, [first], rng.choice("01234")),
                lambda: technique.set_pluck(root, [first], rng.choice("pima")),
            ])()
        elif choice == "join":
            technique.toggle_pair(root, first, other, rng.choice(list(technique.PAIRS)))
        else:
            rng.choice([lambda: technique.toggle_named_line(root, first, other, rng.choice(list(technique.TEXT_LINES))), lambda: technique.toggle_vibrato(root, first, other)])()
    elif choice == "string" and notes:
        fretted.choose_string(root, rng.choice(notes), rng.randint(1, found.count))
    elif choice == "tuning":
        fretted.set_strings(root, 0, rng.choice(sorted(fretted.tunings_for(found.standard))))
    elif choice == "capo":
        fretted.set_strings(root, 0, capo=rng.randint(0, 5))
    else:
        fretted.set_tab(root, 0, not found.tab)


@pytest.mark.parametrize("seed", range(16))
def test_random_edits_keep_a_guitars_tablature_in_step(seed):
    """Every kind of edit, with strings, tunings and capos chosen among them, and the TAB staff still plays every note where it sounds."""
    import copy

    from test_editor_fuzz import attempt, check_outputs, check_rules

    rng = random.Random(seed)
    root = guitar(measures=3, name=rng.choice(["guitar-tab", "electric-guitar-tab", "bass-guitar-tab"]), fifths=rng.randint(-3, 3))
    write(root, *(rng.randint(52, 76) for _ in range(6)))
    done = 0
    for step in range(90):
        if rng.random() < 0.2:
            trial = copy.deepcopy(root)
            try:
                fretted_edit(trial, rng)
                root = trial
            except EditError:
                pass
        else:
            root, worked = attempt(root, rng, overfill=False)
            done += worked
        synced(root)
        check_rules(root)
        check_frets(root)
        assert structure.check_measures(root) == [], f"a bar stopped adding up after {root.get('last-edit')}"
        if step % 30 == 0:
            check_outputs(root)
    assert done > 20
    check_outputs(root)


@pytest.mark.parametrize("seed", range(6))
def test_random_edits_at_concert_pitch_keep_a_bass_guitars_tablature_in_step(seed):
    import copy

    from test_editor_fuzz import check_rules, random_edit

    rng = random.Random(seed)
    root = guitar(measures=3, name="bass-guitar-tab")
    structure.add_part(root, "trumpet")
    synced(root)
    concert.set_concert_pitch(root, True)
    for step in range(60):
        trial = copy.deepcopy(root)
        try:
            with concert.editing(trial):
                random_edit(trial, rng, overfill=False)
            root = trial
        except EditError:
            pass
        synced(root)
        check_rules(root)
        check_frets(root)
        there_and_back = copy.deepcopy(root)
        concert.to_written(there_and_back, concert.to_sounding(there_and_back))
        assert to_text(there_and_back) == to_text(root), f"concert pitch and back changed the score after {root.get('last-edit')}"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from app import server

    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def new_score(client, clef="guitar-tab", **settings):
    made = client.post("/api/scores/blank", json={"measures": 2, "clef": clef, **settings}).json()
    return f"/api/scores/{made['id']}"


def first_note(data, staff="1"):
    root = ET.fromstring(data)
    return next(note.get("id") for note in root.iter("note") if (note.findtext("staff") or "1") == staff)


def write_through(client, url, *midis):
    cursor = first_note(client.get(f"{url}/score.musicxml").text.encode())
    written = []
    for midi in midis:
        done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "midi": midi}).json()
        written.append(done["written"])
        cursor = done["cursor"]
    return written, done


def test_a_new_guitar_score_says_how_its_strings_are_set_up(client):
    url = new_score(client)
    data = client.get(url).json()
    assert data["fretted"] == [board(guitar()).describe()]
    assert data["partStaves"] == [2] and data["fretProblems"] == []
    tab = [note for note in ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()).iter("note") if note.findtext("staff") == "2"]
    assert tab and all(note.get("print-object") == "no" for note in tab), "its tablature starts with rests that aren't drawn"


def test_writing_through_the_server_gives_each_note_its_string(client):
    url = new_score(client)
    written, done = write_through(client, url, 64, 36)
    root = ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())
    assert string_and_fret(root, written[0]) == (1, 0)
    assert done["fretProblems"] == [{"note": written[1], "part": 0, "reason": "C2 is below this instrument's lowest string."}]
    assert any(f'id="{written[0]}{TAB_COPY}"' in page for page in done["pages"])


def test_a_string_is_chosen_for_a_note_and_undone(client):
    url = new_score(client)
    note = write_through(client, url, 64)[0][0]
    chosen = client.post(f"{url}/notes/{note}{TAB_COPY}", json={"action": "string", "string": 2})
    assert chosen.status_code == 200
    assert string_and_fret(ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()), note) == (2, 5)
    refused = client.post(f"{url}/notes/{note}", json={"action": "string", "string": 7})
    assert refused.status_code == 422 and "strings 1 to 6" in refused.json()["detail"]
    client.post(f"{url}/undo")
    assert string_and_fret(ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()), note) == (1, 0)


def test_the_tuning_capo_and_tablature_are_set_through_the_server(client):
    url = new_score(client, clef="guitar")
    note = write_through(client, url, 64)[0][0]
    assert client.get(url).json()["fretted"][0]["tab"] is False
    shown = client.post(f"{url}/strings", json={"part": 0, "tab": True, "tuning": "dropD", "capo": 2}).json()
    assert shown["fretted"][0] | {"tunings": []} == {"strings": ["D2", "A2", "D3", "G3", "B3", "E4"], "tuning": "dropD", "capo": 2, "frets": 19, "tab": True, "tabStaff": 2, "tunings": []}
    assert shown["partStaves"] == [2]
    root = ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())
    assert string_and_fret(root, note) == (2, 3), "E4 at the third fret from a capo on the second"
    custom = client.post(f"{url}/strings", json={"part": 0, "strings": ["C2", "G2", "D3", "G3", "C4", "E4"]}).json()
    assert custom["fretted"][0]["tuning"] == "custom"
    assert client.post(f"{url}/strings", json={"part": 0, "capo": 20}).status_code == 422
    assert client.post(f"{url}/strings", json={"part": 0}).status_code == 422
    assert client.post(f"{url}/strings", json={"part": 3, "capo": 1}).status_code == 422
    client.post(f"{url}/undo")
    assert client.get(url).json()["fretted"][0]["tuning"] == "dropD"


def test_the_named_tunings_are_listed(client):
    tunings = client.get("/api/tunings").json()
    assert tunings["guitar"]["DADGAD"] == ["D2", "A2", "D3", "G3", "A3", "D4"]
    assert tunings["bass"]["standard"] == ["E1", "A1", "D2", "G2"] and tunings["mostCapo"] == 12


def test_a_guitar_with_tablature_opens_from_musicxml_with_its_frets(client):
    root = guitar(measures=1)
    ids = write(root, 64, 69)
    fretted.choose_string(root, ids[1], 2)
    synced(root)
    for note in root.iter("note"):
        note.attrib.pop("id", None)  # As another program would write it.
    import io

    opened = client.post("/api/scores", files={"file": ("guitar.musicxml", io.BytesIO(to_text(root).encode()))}).json()
    url = f"/api/scores/{opened['id']}"
    again = ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())
    second = [note.get("id") for note in again.iter("note") if note.findtext("staff") is None and note.find("pitch") is not None][1]
    assert string_and_fret(again, second) == (2, 10)
    assert client.get(url).json()["fretted"][0]["tab"] is True


def typed(root, *steps):
    """Type letters from the start of the standard staff, as the keys A to G do, and return each pitch written."""
    cursor = next(note for note in root.iter("note") if (note.findtext("staff") or "1") == "1").get("id")
    found = []
    for step in steps:
        done = edit.write_note(root, cursor, "quarter", 0, step)
        synced(root)
        note = next(note for note in root.iter("note") if note.get("id") == done["written"])
        found.append(f"{note.findtext('pitch/step')}{note.findtext('pitch/octave')}")
        cursor = done["cursor"]
    return found


def test_a_letter_typed_into_a_guitar_starts_at_the_middle_of_its_staff():
    # A guitar's music sounds an octave below its treble clef, whose middle line is B3 here.
    assert typed(guitar(), "C") == ["C4"]
    assert typed(guitar(name="guitar"), "B") == ["B3"]


def test_letters_typed_into_a_guitar_stay_where_it_plays():
    # Going up by the nearest note each time would climb past the frets; a guitar plays E2 to E5 most.
    assert typed(guitar(measures=4), "E", "G", "C", "E", "G", "C", "E", "G", "C") == ["E4", "G4", "C5", "E5", "G4", "C5", "E5", "G4", "C5"]
    assert typed(guitar(measures=2), "E", "C", "A", "F", "D", "B", "G") == ["E4", "C4", "A3", "F3", "D3", "B2", "G2"]


def test_a_letter_typed_into_a_bass_guitar_lands_on_its_staff():
    assert typed(guitar(name="bass-guitar-tab"), "E", "A") == ["E3", "A3"]


def test_a_note_written_from_the_fretboard_keeps_the_string_it_was_played_on(client):
    url = new_score(client)
    cursor = first_note(client.get(f"{url}/score.musicxml").text.encode())
    done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "midi": 64, "string": 2}).json()
    root = ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())
    assert string_and_fret(root, done["written"]) == (2, 5)
    assert done["notes"][done["written"]][5] == {"string": 2, "fret": 5}
    stacked = client.post(f"{url}/notes/{done['written']}", json={"action": "stack", "midi": 57, "string": 3}).json()
    assert string_and_fret(ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()), stacked["written"]) == (3, 2)
    busy = client.post(f"{url}/notes/{done['written']}", json={"action": "stack", "midi": 59, "string": 2}).json()
    assert "String 2 is already playing" in busy["message"], "the note is still written, on a string of its own"
    client.post(f"{url}/undo")
    client.post(f"{url}/undo")
    client.post(f"{url}/undo")
    assert client.get(url).json()["notes"][done["written"]][:3] == ["", 0, 0], "a note and its string are one undo"


def test_a_violin_note_written_on_a_string_gets_its_number(client):
    url = new_score(client, clef="violin")
    cursor = first_note(client.get(f"{url}/score.musicxml").text.encode())
    done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "midi": 71, "string": 3}).json()
    note = next(note for note in ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()).iter("note") if note.get("id") == done["written"])
    assert note.findtext("notations/technical/string") == "3"
    assert done["notes"][done["written"]][5] == {"mark": 3}


def test_the_page_is_told_how_each_note_is_played(client):
    url = new_score(client, clef="violin", measures=2)
    written, _ = write_through(client, url, 67, 71, 74)
    client.post(f"{url}/marks", json={"action": "fingering", "notes": written[:2], "text": "13"})
    client.post(f"{url}/marks", json={"action": "position", "notes": [written[1]], "number": 3})
    data = client.post(f"{url}/marks", json={"action": "sul", "notes": [written[1], written[2]], "text": "G"}).json()
    assert data["notes"][written[0]][5] == {"finger": "1"}
    assert data["notes"][written[1]][5] == {"finger": "3", "position": 3, "sul": "G"}
    assert data["notes"][written[2]][5] == {"position": 3, "sul": "G"}
    guitar_url = new_score(client)
    notes, _ = write_through(client, guitar_url, 64, 67)
    client.post(f"{guitar_url}/marks", json={"action": "pluck", "notes": notes[:1], "text": "i"})
    client.post(f"{guitar_url}/marks", json={"action": "bend", "notes": notes[1:], "name": "full"})
    shown = client.post(f"{guitar_url}/marks", json={"action": "harmonic", "notes": notes[:1], "name": "natural"}).json()
    assert shown["notes"][notes[0]][5] == {"string": 1, "fret": 0, "pluck": "i", "harmonic": "natural"}
    assert shown["notes"][notes[1]][5] == {"string": 1, "fret": 3, "bend": "full"}
    assert len(shown["notes"][f"{notes[0]}{TAB_COPY}"]) == 5, "a fret number's copy says nothing of its own"


def test_a_guitar_with_a_capo_says_so_over_its_music():
    root = guitar(measures=8)
    write(root, 64, 67)
    assert "Capo" not in "".join(render_pages(to_text(root)))
    fretted.set_strings(root, 0, capo=3)
    synced(root)
    pages = render_pages(to_text(root))
    assert pages[0].count('class="xrn-capo"') == 1 and "Capo 3" in pages[0]
    words = re.search(r'class="xrn-capo">.*?<text x="(\d+)" y="(\d+)"', pages[0], re.S)
    top = re.search(r'class="staff">\s*<path d="M(\d+) (\d+) L', pages[0])
    assert 0 < int(words.group(2)) < int(top.group(2)), "over its staff, and on the page"
    assert 'class="dir"' not in pages[0], "and not taken for one of the score's marks"
    assert all("Capo" not in page for page in pages[1:])
    assert "Capo" not in to_text(root), "the score itself is left as it was"


def own_note(root, note_id):
    return next(note for note in root.iter("note") if note.get("id") == note_id)


def pitch_name(note):
    alter = int(float(note.findtext("pitch/alter") or 0))
    return f"{note.findtext('pitch/step')}{'#' * alter if alter > 0 else 'b' * -alter}{note.findtext('pitch/octave')}"


def saved(client, url):
    return ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())


def test_a_fret_typed_on_the_tablature_changes_the_note_on_its_string(client):
    url = new_score(client)
    written, _ = write_through(client, url, 64, 59)
    done = client.post(f"{url}/notes/{written[0]}{TAB_COPY}", json={"action": "fret", "fret": 5}).json()
    root = saved(client, url)
    assert pitch_name(own_note(root, written[0])) == "A4" and string_and_fret(root, written[0]) == (1, 5), "the standard staff follows the fret"
    assert done["notes"][written[0]][5] == {"string": 1, "fret": 5}
    client.post(f"{url}/notes/{written[1]}{TAB_COPY}", json={"action": "fret", "fret": 12})
    root = saved(client, url)
    assert pitch_name(own_note(root, written[1])) == "B4" and string_and_fret(root, written[1]) == (2, 12), "a two-digit fret, on the B string"
    client.post(f"{url}/undo")
    assert string_and_fret(saved(client, url), written[1]) == (2, 0), "a note and its fret are one undo"


def test_a_fret_on_a_string_another_note_would_take_stays_on_its_own_string(client):
    url = new_score(client)
    written, _ = write_through(client, url, 52)
    client.post(f"{url}/notes/{written[0]}", json={"action": "stack", "midi": 59})
    root = saved(client, url)
    assert string_and_fret(root, written[0]) == (4, 2)
    # Fret 7 on the D string is A3, which the A string would play open: it stays on the D string.
    client.post(f"{url}/notes/{written[0]}{TAB_COPY}", json={"action": "fret", "fret": 7})
    root = saved(client, url)
    assert pitch_name(own_note(root, written[0])) == "A3" and string_and_fret(root, written[0]) == (4, 7)


def test_a_fret_typed_on_a_tied_note_moves_the_whole_tie():
    root = guitar(measures=2)
    first = write(root, 64, length="whole")[0]
    second = next(note for note in root.iter("note") if (note.findtext("staff") or "1") == "1" and note.get("id") != first)
    edit.write_note(root, second.get("id"), "quarter", 0, None, midi=64)
    synced(root)
    edit.toggle_tie(root, first)
    synced(root)
    fretted.play_at_fret(root, first, 3)
    synced(root)
    tied = edit.tied_notes(root, own_note(root, first))
    assert len(tied) == 2 and {pitch_name(note) for note in tied} == {"G4"}, "the tie holds, at the new pitch"
    assert {string_and_fret(root, note.get("id")) for note in tied} == {(1, 3)}


def test_a_fret_typed_for_a_bass_guitar_sounds_from_its_strings_at_concert_pitch_or_not():
    for concert_pitch in (False, True):
        root = guitar(measures=1, name="bass-guitar-tab")
        note = write(root, 40)[0]
        assert string_and_fret(root, note) == (4, 0), "written E2 sounds the open E1 string"
        if concert_pitch:
            concert.set_concert_pitch(root, True)
            with concert.editing(root):
                fretted.play_at_fret(root, note, 3)
        else:
            fretted.play_at_fret(root, note, 3)
        synced(root)
        assert pitch_name(own_note(root, note)) == "G2", "written an octave above the G1 it sounds"
        assert string_and_fret(root, note) == (4, 3)


@pytest.mark.parametrize("fret, reason", [(20, "frets go from 0 to 19"), (-1, "frets go from 0 to 19")])
def test_a_fret_the_neck_does_not_have_is_refused(fret, reason):
    root = guitar(measures=1)
    note = write(root, 64)[0]
    with pytest.raises(EditError, match=reason):
        fretted.play_at_fret(root, note, fret)


def test_a_rest_has_no_fret():
    root = guitar(measures=1)
    rest = next(note for note in root.iter("note") if note.find("rest") is not None)
    with pytest.raises(EditError, match="rest has no fret"):
        fretted.play_at_fret(root, rest.get("id"), 3)


def kept_place(root, note_id):
    return fretted.place_of(own_note(root, note_id))


def test_a_guitar_without_tablature_keeps_the_string_chosen_for_a_note(client):
    url = new_score(client, clef="guitar")
    cursor = first_note(client.get(f"{url}/score.musicxml").text.encode())
    done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "midi": 64, "string": 2}).json()
    note = done["written"]
    root = saved(client, url)
    assert kept_place(root, note) == (2, 5) and done["notes"][note][5] == {"string": 2, "fret": 5}
    assert "<fret>" not in "".join(done["pages"]), "it isn't drawn: there's no tablature to draw it on"
    assert done["marks"] == [], "and it isn't a string number to click"
    moved = client.post(f"{url}/notes/{note}", json={"action": "string", "string": 3}).json()
    assert moved["notes"][note][5] == {"string": 3, "fret": 9}
    capo = client.post(f"{url}/strings", json={"part": 0, "capo": 2}).json()
    assert capo["notes"][note][5] == {"string": 3, "fret": 7}, "a capo moves its fret, as on the tablature"
    client.post(f"{url}/strings", json={"part": 0, "capo": 0})
    tab = client.post(f"{url}/strings", json={"part": 0, "tab": True}).json()
    assert tab["notes"][note][5] == {"string": 3, "fret": 9}, "turned on, the tablature plays it there"
    assert kept_place(saved(client, url), note) is None, "and keeps it from then on"
    off = client.post(f"{url}/strings", json={"part": 0, "tab": False}).json()
    assert off["notes"][note][5] == {"string": 3, "fret": 9}, "taken away, the note keeps it"
    repitched = client.post(f"{url}/notes/{note}", json={"action": "semitoneUp"}).json()
    assert len(repitched["notes"][note]) == 5 or "string" not in repitched["notes"][note][5], "a new pitch forgets it"


def test_a_string_number_and_the_string_kept_on_a_note_are_apart():
    root = structure.blank_score(clef="guitar", measures=1)
    edit.ensure_note_ids(root)
    note = write(root, 64)[0]
    fretted.choose_string(root, note, 2)
    synced(root)
    from pipeline import marks, technique

    technique.set_string_number(root, [note], 2)
    synced(root)
    found = own_note(root, note)
    assert kept_place(root, note) == (2, 5) and fretted.string_number(found) == 2
    marks.ensure_mark_ids(root)
    assert [mark["kind"] for mark in marks.list_marks(root)] == ["string"], "only the circled number is a mark"
    technique.set_string_number(root, [note], 2)
    assert kept_place(root, note) == (2, 5) and fretted.string_number(found) is None, "taking the number away leaves the string"
    technique.set_fingering(root, [note], "2")
    edit.clear_marks(root, note)
    assert kept_place(root, note) == (2, 5) and not technique.marks_of(found, "fingering"), "Clear marks leaves where it's played"


def test_tablature_on_its_own_is_a_part_of_one_staff_that_is_the_music(client):
    url = new_score(client, clef="guitar-tab-only")
    data = client.get(url).json()
    assert data["partStaves"] == [1] and data["fretted"][0]["tab"] is True and data["fretted"][0]["tabStaff"] == 1
    written, done = write_through(client, url, 64, 67, 36)
    root = saved(client, url)
    assert [fretted.place_of(own_note(root, note)) for note in written[:2]] == [(1, 0), (1, 3)]
    assert done["fretProblems"] == [{"note": written[2], "part": 0, "reason": "C2 is below this instrument's lowest string."}]
    heard = client.get(f"{url}/playback").json()
    assert sorted(note["midi"] for note in heard["notes"]) == [36, 64, 67], "it's played, as it's the music"
    typed = client.post(f"{url}/notes/{written[1]}", json={"action": "fret", "fret": 5}).json()
    assert typed["notes"][written[1]][5] == {"string": 1, "fret": 5} and pitch_name(own_note(saved(client, url), written[1])) == "A4"


def test_tablature_on_its_own_is_drawn_with_its_rhythm():
    root = structure.blank_score(clef="guitar-tab-only", measures=2)
    edit.ensure_note_ids(root)
    write(root, 64, 67, length="eighth")
    page = render_pages(to_text(root))[0]
    assert page.count('class="stem"') >= 2 and 'class="beam"' in page, "its eighths have stems and a beam"
    rests = [note.get("id") for note in root.iter("note") if note.find("rest") is not None]
    assert rests and all(re.search(rf'<g id="{rest}" class="(rest|mRest)"', page) for rest in rests), "and its rests are drawn, with their own ids to click"
    linked = render_pages(to_text(guitar(measures=2)))[0]
    assert 'class="mRest"' in linked, "a TAB staff under a standard staff still shows its rhythm only above"


def test_tablature_on_its_own_taken_away_is_a_standard_staff_keeping_its_strings():
    root = structure.blank_score(clef="guitar-tab-only", measures=1)
    edit.ensure_note_ids(root)
    note = write(root, 64)[0]
    fretted.choose_string(root, note, 2)
    synced(root)
    fretted.set_tab(root, 0, False)
    synced(root)
    clef = root.find("part/measure/attributes/clef")
    assert (clef.findtext("sign"), clef.findtext("clef-octave-change")) == ("G", "-1") and fretted.tab_staves(root.find("part")) == []
    assert kept_place(root, note) == (2, 5)
    fretted.set_tab(root, 0, True)
    synced(root)
    assert string_and_fret(root, note) == (2, 5)


def test_tablature_on_its_own_is_offered_for_a_guitar_and_a_bass():
    listed = {item["id"]: item["label"] for item in structure.instrument_list()}
    assert listed["guitar-tab-only"] == "Guitar, tablature only" and listed["bass-guitar-tab-only"] == "Bass Guitar, tablature only"
    bass = structure.blank_score(clef="bass-guitar-tab-only", measures=1)
    assert board(bass).count == 4 and concert.transpositions(bass.find("part"))[0].semitones == -12


def test_a_group_written_as_one_tag_opens_nothing():
    from pipeline.decorate import group_end

    svg = '<g id="a" class="measure"><g id="b" class="tabDurSym" /><g id="c"><path /></g></g><g id="d"></g>'
    assert group_end(svg, 0) == svg.index('<g id="d"')


def test_a_capo_leaves_the_frets_behind_it_out_of_reach():
    root = guitar(measures=1)  # A nylon-string guitar, with 19 frets.
    high = write(root, 83, 84)
    assert [item["note"] for item in fretted.problems(root)] == [high[1]], "B5 is the top string's 19th fret, C6 would be its 20th"
    fretted.set_strings(root, 0, capo=12)
    synced(root)
    listed = {item["note"]: item["reason"] for item in fretted.problems(root)}
    assert high[1] in listed and "above the 19th fret" in listed[high[1]], "a capo on the 12th fret leaves seven above it, not nineteen"
    assert string_and_fret(root, high[0]) == (1, 7)
    with pytest.raises(EditError, match="fret 7 from the capo"):
        fretted.choose_string(root, high[0], 2)


def test_a_chord_written_a_note_at_a_time_is_fingered_as_a_chord():
    root = guitar(measures=1)
    first = write(root, 60)[0]
    chord(root, first, 64)
    chord(root, first, 67)
    frets = [string_and_fret(root, note.get("id"))[1] for note in edit.locate(root, first).event.notes]
    assert max(frets) - min(fret for fret in frets if fret) <= fretted.LONGEST_SPAN, f"playable together, not {frets}"


def test_a_chosen_string_still_holds_in_a_chord_it_can_be_played_in():
    root = guitar(measures=1)
    first = write(root, 60)[0]
    fretted.choose_string(root, first, 3)
    synced(root)
    chord(root, first, 64)
    assert string_and_fret(root, first) == (3, 5)


def test_a_note_hidden_on_the_staff_is_hidden_on_the_tablature():
    root = guitar(measures=1)
    hidden = write(root, 64, 67)[0]
    own_note(root, hidden).set("print-object", "no")
    synced(root)
    assert copy_of(root, hidden).get("print-object") == "no"


def test_a_string_chosen_for_a_note_is_the_apps_own_and_left_out_of_a_file():
    root = guitar(measures=1)
    note = write(root, 64)[0]
    fretted.choose_string(root, note, 2)
    synced(root)
    assert copy_of(root, note).get(fretted.CHOSEN) == "yes"
    assert fretted.CHOSEN not in to_text(technique.drawn_on_other_staves(copy.deepcopy(root)))
