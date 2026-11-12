"""A guitar's tablature: the template, the strings and frets each note is given, and choosing them."""

import random
import re
import xml.etree.ElementTree as ET
from fractions import Fraction

import pytest

from pipeline import concert, edit, fretted, structure, transpose
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
    choice = rng.choice(["string", "string", "string", "tuning", "capo", "tab"])
    root.set("last-edit", f"fretted {choice}")
    if choice == "string" and notes:
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
