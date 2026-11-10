"""Transposing notes, keys and chord symbols."""

import random

import pytest

from pipeline import edit, playback, rhythm, structure, transpose
from pipeline.musicxml import chord_from_harmony, to_text
from pipeline.rhythm import EditError, contexts
from pipeline.transpose import INTERVALS, Interval, interval_between_keys, move_pitch


def score(fifths=0, **settings):
    root = structure.blank_score(fifths=fifths, **{"measures": 2} | settings)
    edit.ensure_note_ids(root)
    return root


def write(root, *pitches):
    """Write quarter notes from pitches such as C#5, and return their ids."""
    cursor = next(root.iter("note")).get("id")
    written = []
    for pitch in pitches:
        done = edit.write_note(root, cursor, "quarter", 0, pitch[0])
        written.append(done["written"])
        cursor = done["cursor"]
        note = edit.find_note(root, done["written"])
        note.find("pitch/octave").text = pitch[-1]
        edit.write_alter(note.find("pitch"), {"#": 1, "b": -1, "x": 2}.get(pitch[1], 0))
    for part in root.findall("part"):
        for context in contexts(part):
            rhythm.respell(context)
    return written


def names(root):
    found = []
    for note in root.iter("note"):
        if note.find("pitch") is not None:
            alter = int(note.findtext("pitch/alter") or 0)
            found.append(note.findtext("pitch/step") + {-2: "bb", -1: "b", 0: "", 1: "#", 2: "x"}[alter] + note.findtext("pitch/octave"))
    return found


def sounds(root):
    """Compare chords by sound: one with a double flat comes back respelled, as nobody writes those."""
    return [[midi % 12 for midi in playback.chord_notes(chord_from_harmony(found))] for found in root.iter("harmony")]


def chord_names(root):
    return [str(chord_from_harmony(found)) for found in root.iter("harmony")]


@pytest.mark.parametrize(("start", "name", "direction", "expected"), [
    (("C", 1, 4), "M2", "up", ("D", 1, 4)),
    (("B", -1, 4), "P4", "down", ("F", 0, 4)),
    (("B", 0, 4), "m2", "up", ("C", 0, 5)),
    (("C", 0, 4), "m2", "down", ("B", 0, 3)),
    (("F", 0, 4), "A4", "up", ("B", 0, 4)),
    (("E", -1, 4), "M6", "up", ("C", 0, 5)),
])
def test_a_pitch_keeps_its_spelling(start, name, direction, expected):
    assert move_pitch(*start, transpose.named_interval(name, direction)) == expected


def test_a_double_sharp_too_far_is_respelled():
    assert move_pitch("B", 2, 4, Interval(0, 1)) == ("D", 0, 5)


@pytest.mark.parametrize(("name", "fifths"), [("P5", 1), ("P4", -1), ("M2", 2), ("m3", -3), ("M3", 4), ("P8", 0), ("A4", 6), ("d5", -6)])
def test_an_interval_knows_how_far_it_moves_a_key(name, fifths):
    assert INTERVALS[name].fifths == fifths


def test_every_key_can_be_reached_from_every_key():
    for start in range(-7, 8):
        for target in range(-7, 8):
            for direction, check in (("closest", lambda s: -6 <= s <= 6), ("up", lambda s: s >= 0), ("down", lambda s: s <= 0)):
                interval = interval_between_keys(start, target, direction)
                assert interval.fifths == target - start
                assert check(interval.semitones), (start, target, direction, interval)


def test_going_to_a_key_takes_the_nearest_way_by_default():
    assert interval_between_keys(0, 2) == Interval(1, 2)
    assert interval_between_keys(0, -1) == Interval(3, 5)  # F is a fourth above C, nearer than a fifth below.
    assert interval_between_keys(0, 1) == Interval(-3, -5)  # G is a fourth below C, nearer than a fifth above.


def test_a_whole_score_moves_its_notes_key_and_chords():
    root = score(fifths=-2)
    ids = write(root, "Bb4", "D5", "F5", "A5")
    edit.add_chord(root, ids[0], "Bbmaj7")
    edit.add_chord(root, ids[2], "F7/A")
    transpose.transpose_score(root, INTERVALS["M2"])
    assert names(root) == ["C5", "E5", "G5", "B5"]
    assert next(root.iter("fifths")).text == "0"
    assert chord_names(root) == ["Cmaj7", "G7/B"]


def test_accidentals_are_worked_out_again_for_the_new_key():
    root = score(fifths=0)
    ids = write(root, "C4", "F#4", "C4", "C4")
    assert edit.find_note(root, ids[1]).findtext("accidental") == "sharp"
    transpose.transpose_score(root, INTERVALS["M2"])  # D major has an F sharp and a C sharp.
    assert names(root) == ["D4", "G#4", "D4", "D4"]
    assert edit.find_note(root, ids[1]).findtext("accidental") == "sharp"
    plain = score(fifths=0)
    ids = write(plain, "F4", "A4", "C5", "E5")
    transpose.transpose_score(plain, INTERVALS["M2"])
    assert names(plain) == ["G4", "B4", "D5", "F#5"]
    assert edit.find_note(plain, ids[3]).find("accidental") is None  # F sharp is in D major.


def test_a_key_past_six_is_respelled_to_read_easily():
    root = score(fifths=3)
    write(root, "A4", "B4", "C#5", "D5")
    transpose.transpose_score(root, INTERVALS["M3"])
    assert next(root.iter("fifths")).text == "-5"  # D flat, not C sharp.
    assert names(root) == ["Db5", "Eb5", "F5", "Gb5"]


def test_keys_too_far_apart_are_refused():
    root = score(fifths=7)
    structure.set_key(root, 1, -7)
    with pytest.raises(EditError):
        transpose.transpose_score(root, INTERVALS["M2"])


def test_a_key_change_in_the_middle_moves_too():
    root = score(fifths=0)
    structure.set_key(root, 1, 2)
    transpose.transpose_score(root, INTERVALS["P4"])
    assert [found.text for found in root.iter("fifths")] == ["-1"] * 1 + ["1"]


def test_ties_survive():
    root = score()
    ids = write(root, "C5", "C5")
    edit.toggle_tie(root, ids[0])
    transpose.transpose_score(root, INTERVALS["m3"])
    assert rhythm.is_tied(edit.find_note(root, ids[0]), "start")
    assert rhythm.is_tied(edit.find_note(root, ids[1]), "stop")


def test_going_there_and_back_changes_nothing():
    rng = random.Random(4)
    for _ in range(60):
        root = score(fifths=rng.randint(-3, 3))
        ids = write(root, *[rng.choice(["C4", "D5", "Eb4", "F#5", "G4", "Bb4"]) for _ in range(6)])
        edit.add_chord(root, ids[0], rng.choice(["Cm7", "F#7", "Bbmaj7", "Eb7/G"]))
        before = names(root), sounds(root), next(root.iter("fifths")).text
        interval = INTERVALS[rng.choice(["m2", "M2", "m3", "M3", "P4"])]
        chosen = transpose.readable(interval, transpose.key_fifths(root))
        transpose.transpose_score(root, chosen)
        transpose.transpose_score(root, -chosen)
        assert (names(root), sounds(root), next(root.iter("fifths")).text) == before


def test_every_note_sounds_the_same_distance_away():
    rng = random.Random(9)
    for _ in range(100):
        root = score(fifths=rng.randint(-5, 5))
        write(root, *[rng.choice(["C4", "D5", "Eb4", "F#5", "G4", "Bb4", "Cx5"]) for _ in range(7)])
        before = [item["midi"] for item in playback.playback(root)["notes"]]
        interval = rng.choice(list(INTERVALS.values())) if rng.random() < 0.5 else -rng.choice(list(INTERVALS.values()))
        interval = transpose.readable(interval, transpose.key_fifths(root))
        transpose.transpose_score(root, interval)
        assert [item["midi"] for item in playback.playback(root)["notes"]] == [midi + interval.semitones for midi in before]


def test_going_to_a_key_lands_on_that_key():
    root = score(fifths=-3)
    write(root, "Eb4", "G4", "Bb4", "D5")
    transpose.transpose_score_to_key(root, 0)
    assert next(root.iter("fifths")).text == "0"
    assert names(root) == ["C4", "E4", "G4", "B4"]


def test_a_stretch_moves_alone_and_leaves_the_key():
    root = score()
    ids = write(root, "C5", "D5", "E5", "F5")
    edit.add_chord(root, ids[1], "Dm7")
    edit.add_chord(root, ids[3], "F")
    transpose.transpose_stretch(root, ids[1], ids[2], INTERVALS["M2"])
    assert names(root) == ["C5", "E5", "F#5", "F5"]
    assert chord_names(root) == ["Em7", "F"]
    assert next(root.iter("fifths")).text == "0"
    assert edit.find_note(root, ids[2]).findtext("accidental") == "sharp"


def test_a_tie_broken_by_moving_half_of_it_is_removed():
    root = score()
    ids = write(root, "C5", "C5", "D5", "E5")
    edit.toggle_tie(root, ids[0])
    transpose.transpose_stretch(root, ids[1], ids[1], INTERVALS["M2"])
    assert not rhythm.is_tied(edit.find_note(root, ids[0]), "start")


def test_the_memo_records_chords_changed_on_purpose():
    root = score()
    write(root, "C5", "D5")
    assert not transpose.is_transposed(root)
    transpose.transpose_score(root, INTERVALS["M2"])
    assert transpose.is_transposed(root)
    transpose.transpose_score(root, -INTERVALS["M2"])
    assert not transpose.is_transposed(root)
    assert root.find("identification") is None


def test_the_memo_keeps_what_was_already_there():
    root = score()
    structure.set_title(root, "Song", "Me")
    transpose.transpose_score(root, INTERVALS["M2"])
    assert [found.get("type") for found in root.iter("creator")] == ["composer"]
    names_in_order = [child.tag for child in root.find("identification")]
    assert names_in_order == ["creator", "miscellaneous"]
