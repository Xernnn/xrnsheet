"""Each of these guards a scoring bug that once made an engine look worse than it was."""

from pathlib import Path

from helpers import harmony, note, score

from score import compare, read_chords, read_notes, read_placed_chords


def write(folder: Path, name: str, text: str) -> Path:
    path = folder / f"{name}.musicxml"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_perfect_copy_scores_one(tmp_path):
    path = write(tmp_path, "a", score(harmony() + note() + note("D") + note("E") + note("F")))
    result = compare(path, [path])
    assert (result.pitch_f1, result.note_f1, result.chord_recall, result.chord_placed) == (1, 1, 1, 1)


def test_notes_split_across_files_all_count(tmp_path):
    whole = write(tmp_path, "whole", score(note("C", duration=8), note("D", duration=8)))
    first = write(tmp_path, "m1", score(note("C", duration=8)))
    second = write(tmp_path, "m2", score(note("D", duration=8)))
    result = compare(whole, [first, second])
    assert result.predicted_notes == 2 and result.note_f1 == 1


def test_an_octave_error_shows_in_pitch_but_not_in_pitch_class(tmp_path):
    truth = write(tmp_path, "t", score(note("C") + note("D") + note("E") + note("F")))
    shifted = write(tmp_path, "s", score(note("C", 3) + note("D", 3) + note("E", 3) + note("F", 3)))
    result = compare(truth, [shifted])
    assert result.pitch_f1 == 0 and result.pitch_class_f1 == 1


def test_one_missed_note_costs_one_note(tmp_path):
    truth = write(tmp_path, "t", score(note("C") + note("D") + note("E") + note("F")))
    missing = write(tmp_path, "m", score(note("C") + note("E") + note("F")))
    assert round(compare(truth, [missing]).pitch_f1, 3) == round(6 / 7, 3)


def test_rounded_triplet_durations_still_match(tmp_path):
    truth = write(tmp_path, "t", score(note(duration=1000)).replace("<divisions>2", "<divisions>5952"))
    other = write(tmp_path, "o", score(note(duration=1000)).replace("<divisions>2", "<divisions>6000"))
    assert read_notes([truth])[0][2] == read_notes([other])[0][2]


def test_lyrics_that_look_like_chords_are_not_correct_answers(tmp_path):
    lyric = '<lyric><text>Am</text></lyric>'
    text = score(note().replace("</note>", lyric + "</note>"))
    path = write(tmp_path, "t", text)
    assert read_chords([path], include_text=False) == []


def test_chord_text_above_the_staff_counts_for_predictions(tmp_path):
    words = '<direction><direction-type><words>Cm7</words></direction-type></direction>'
    path = write(tmp_path, "p", score(words + note(duration=8)))
    assert read_chords([path]) == ["Cm7"]


def test_a_piece_without_chords_has_no_chord_score(tmp_path):
    path = write(tmp_path, "t", score(note(duration=8)))
    assert compare(path, [path]).chord_recall is None


def test_a_right_chord_on_the_wrong_beat_is_read_but_not_placed(tmp_path):
    truth = write(tmp_path, "t", score(harmony() + note() + note() + note() + note()))
    late = write(tmp_path, "l", score(note() + note() + harmony() + note() + note()))
    result = compare(truth, [late])
    assert result.chord_recall == 1 and result.chord_placed == 0


def test_chord_positions_are_measure_and_beat(tmp_path):
    path = write(tmp_path, "t", score(note(duration=8), note() + harmony("F", "", "major") + note() + note(duration=4)))
    assert read_placed_chords([path], include_text=False) == [("F", 1, 1.0)]
