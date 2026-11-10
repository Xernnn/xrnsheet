"""Each of these guards a scoring bug that once made an engine look worse than it was."""

import importlib
from pathlib import Path

import pytest
from helpers import harmony, note, score

from score import compare, read_chords, read_notes, read_placed_chords, share_of_bars_full


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


def bars(*inside, beats=4, beat_type=4):
    measures = []
    for number, content in enumerate(inside, 1):
        attributes = f"<attributes><divisions>4</divisions><time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time></attributes>" if number == 1 else ""
        measures.append(f'<measure number="{number}">{attributes}{content}</measure>')
    return f'<score-partwise><part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list><part id="P1">{"".join(measures)}</part></score-partwise>'


def quarters(count):
    return '<note><pitch><step>C</step><octave>5</octave></pitch><duration>4</duration><type>quarter</type></note>' * count


def saved(tmp_path, name, text):
    path = tmp_path / f"{name}.musicxml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_right_signature_scores_every_bar(tmp_path):
    truth = saved(tmp_path, "truth", bars(*[quarters(3)] * 4, beats=3))
    found = compare(truth, [truth])
    assert found.time_right == 1 and found.truth_time == "3/4"


def test_a_wrong_signature_scores_nothing_even_with_every_note_right(tmp_path):
    truth = saved(tmp_path, "truth", bars(*[quarters(3)] * 4, beats=3))
    wrong = saved(tmp_path, "wrong", bars(*[quarters(3)] * 4, beats=4))
    found = compare(truth, [wrong])
    assert found.time_right == 0 and found.note_f1 == 1


def test_a_signature_that_changes_partway_is_scored_by_the_bar(tmp_path):
    truth = saved(tmp_path, "truth", bars(*[quarters(4)] * 4))
    text = bars(*[quarters(4)] * 2).replace("</part>", '<measure number="3"><attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes></measure><measure number="4"></measure></part>')
    assert compare(truth, [saved(tmp_path, "changed", text)]).time_right == 0.5


def test_missing_bars_count_against_the_signature_score(tmp_path):
    truth = saved(tmp_path, "truth", bars(*[quarters(4)] * 4))
    short = saved(tmp_path, "short", bars(*[quarters(4)] * 2))
    assert compare(truth, [short]).time_right == 0.5


def test_bars_that_add_up_are_counted(tmp_path):
    good = saved(tmp_path, "good", bars(*[quarters(4)] * 4))
    bad = saved(tmp_path, "bad", bars(quarters(4), quarters(5), quarters(3), quarters(4)))
    assert share_of_bars_full([good]) == 1
    assert share_of_bars_full([bad]) == 0.5


def test_a_cached_run_never_starts_an_engine(tmp_path, monkeypatch):
    run_engines = importlib.import_module("run_engines")
    monkeypatch.setattr(run_engines, "RESULTS_DIR", tmp_path)
    monkeypatch.setitem(run_engines.ENGINES, "homr", lambda image, folder: pytest.fail("an engine ran"))
    row = run_engines.evaluate("homr", tmp_path / "piece__clean.png", cached=True)
    assert row["engine"] == "homr" and row["output"] is False
