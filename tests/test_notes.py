import random
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest
import torch

import score
from chords.metrics import edit_distance
from notes import render
from notes.build import build_score
from notes.model import NoteReader, decode, to_batch
from notes.staves import STRIP_HEIGHT, crop_staff, find_staves
from notes.train import fit_memory, token_error
from notes.vocab import BLANK, TOKEN_INDEX, TOKENS, Reading, key_alteration, length_of, position_to_pitch
from pipeline.musicxml import read_measures, to_text


def reading(*tokens: str) -> Reading:
    return Reading(list(tokens), [float(index * 10) for index in range(len(tokens))], [1.0] * len(tokens))


def pitches(root: ET.Element) -> list[tuple[str, str, str]]:
    return [
        (note.findtext("pitch/step"), note.findtext("pitch/alter") or "0", note.findtext("pitch/octave"))
        for note in root.iter("note")
        if note.find("pitch") is not None
    ]


def draw_page(staff_tops, curve=0.0, ledger_at=None, width=1600, height=700, spacing=16):
    page = np.full((height, width), 255, np.uint8)
    for top in staff_tops:
        for line in range(5):
            points = np.array([[x, top + line * spacing + curve * np.sin(np.pi * x / width)] for x in range(100, width - 100, 20)], np.int32)
            cv2.polylines(page, [points], False, 0, 2)
    if ledger_at:
        x, y = ledger_at
        for row in (y, y - spacing):  # Two ledger lines long enough to look like staff lines.
            cv2.line(page, (x, row), (x + 90, row), 0, 2)
    return page


def test_staff_positions_name_the_right_notes():
    assert position_to_pitch("G", 0) == ("E", 4)  # Bottom line of the treble staff.
    assert position_to_pitch("G", 8) == ("F", 5)  # Top line.
    assert position_to_pitch("G", -2) == ("C", 4)  # Middle C, one ledger line below.
    assert position_to_pitch("F", 0) == ("G", 2)
    assert position_to_pitch("F", 10) == ("C", 4)  # Middle C from the other side.


def test_key_signatures_add_sharps_and_flats_in_order():
    assert key_alteration(2, "F") == 1 and key_alteration(2, "C") == 1 and key_alteration(2, "G") == 0
    assert key_alteration(-2, "B") == -1 and key_alteration(-2, "E") == -1 and key_alteration(-2, "A") == 0
    assert key_alteration(0, "F") == 0


def test_dotted_lengths_add_half():
    assert length_of("4.") == 1.5 and length_of("8.") == 0.75 and length_of("1") == 4


def test_every_token_is_unique_and_blank_comes_first():
    assert len(set(TOKENS)) == len(TOKENS) and TOKENS[BLANK] == "<blank>"


def test_clef_and_key_turn_positions_into_pitches():
    root, _, _ = build_score([reading("clef:F", "key:2", "time:3/4", "note:3:4", "note:7:4", "bar")])
    assert pitches(root) == [("C", "1", "3"), ("G", "0", "3")]  # D major sharpens the C.


def test_an_accidental_lasts_until_the_barline():
    root, _, _ = build_score([reading("clef:G", "acc:sharp", "note:3:4", "note:3:4", "bar", "note:3:4", "bar")])
    assert pitches(root) == [("A", "1", "4"), ("A", "1", "4"), ("A", "0", "4")]


def test_an_accidental_only_applies_in_its_own_octave():
    root, _, _ = build_score([reading("clef:G", "acc:flat", "note:0:4", "note:7:4", "bar")])
    assert pitches(root) == [("E", "-1", "4"), ("E", "0", "5")]


def test_a_natural_cancels_the_key_for_the_rest_of_the_measure():
    root, _, _ = build_score([reading("clef:G", "key:1", "acc:natural", "note:8:4", "note:8:4", "bar", "note:8:4", "bar")])
    assert pitches(root) == [("F", "0", "5"), ("F", "0", "5"), ("F", "1", "5")]


def test_each_line_starts_a_new_system_and_keeps_the_key():
    root, kept, _ = build_score([reading("clef:G", "key:3", "note:1:1", "bar"), reading("clef:G", "note:1:1", "bar")])
    measures = read_measures(root.find("part"))
    assert [measure.starts_system for measure in measures] == [True, True] and kept == [0, 1]
    assert pitches(root)[1][1] == "1"  # The F is still sharp on a line that lost its key token.


def test_triplets_share_the_time_of_two_notes():
    root, _, _ = build_score([reading("clef:G", "triplet", "note:1:8", "note:2:8", "note:3:8", "note:4:4", "note:5:2", "bar")])
    durations = [int(note.findtext("duration")) for note in root.iter("note")]
    assert sum(durations[:3]) == 48 and durations[3] == 48
    assert len(list(root.iter("time-modification"))) == 3 and len(list(root.iter("tuplet"))) == 2


def test_a_strip_with_no_music_is_left_out():
    root, kept, marks = build_score([reading("clef:G"), reading("clef:G", "note:1:1", "bar")])
    assert kept == [1] and len(marks) == 1 and len(root.findall("part/measure")) == 1


def test_a_doubled_barline_does_not_make_an_empty_measure():
    root, _, marks = build_score([reading("clef:G", "note:1:1", "bar", "bar", "note:2:1", "bar")])
    assert len(root.findall("part/measure")) == 2 and len(marks[0].bars) == 2


def test_marks_record_where_notes_and_bars_sit():
    _, _, marks = build_score([reading("clef:G", "note:1:2", "note:2:2", "bar")])
    assert marks[0].notes == [10.0, 20.0] and marks[0].bars == [30.0]


def test_generated_labels_rebuild_the_exact_score(tmp_path):
    """The generator and the token decoder must agree, or every training label is wrong."""
    for seed in range(25):
        xml, lines = render.sample_page(random.Random(seed))
        root, _, _ = build_score([reading(*tokens) for tokens in lines])
        (tmp_path / "truth.musicxml").write_text(xml, encoding="utf-8")
        (tmp_path / "built.musicxml").write_text(to_text(root), encoding="utf-8")
        result = score.compare(tmp_path / "truth.musicxml", [tmp_path / "built.musicxml"])
        assert result.note_f1 == 1.0 and result.truth_notes == result.predicted_notes


def test_decoding_merges_repeats_and_reports_positions():
    indices = [BLANK, 1, 1, 1, BLANK, 1, BLANK]
    log_probabilities = torch.full((1, len(indices), len(TOKENS)), -20.0)
    for step, index in enumerate(indices):
        log_probabilities[0, step, index] = 0.0
    result = decode(log_probabilities, torch.tensor([len(indices)]))[0]
    assert result.tokens == [TOKENS[1], TOKENS[1]]
    assert result.xs == [10.0, 22.0]  # The middles of slices 1 to 4 and 5 to 6, at four pixels each.


def test_decoding_stops_at_the_strip_length():
    log_probabilities = torch.full((1, 4, len(TOKENS)), -20.0)
    log_probabilities[0, :, 1] = 0.0
    log_probabilities[0, 3, 2] = 10.0
    assert decode(log_probabilities, torch.tensor([3]))[0].tokens == [TOKENS[1]]


def test_the_model_keeps_height_and_returns_one_prediction_per_slice():
    model = NoteReader().eval()
    strips = [np.full((STRIP_HEIGHT, 200), 255, np.uint8), np.full((STRIP_HEIGHT, 120), 255, np.uint8)]
    pixels, widths = to_batch(strips)
    log_probabilities, lengths = model(pixels, widths)
    assert log_probabilities.shape == (2, 50, len(TOKENS)) and lengths.tolist() == [50, 30]


def test_the_features_keep_a_note_at_its_height():
    """Average pooling would throw pitch away, so a higher note must show in a higher row."""
    model = NoteReader().eval()
    blank = np.full((STRIP_HEIGHT, 120), 255, np.uint8)
    low, high = blank.copy(), blank.copy()
    cv2.circle(low, (60, 70), 5, 0, -1)
    cv2.circle(high, (60, 26), 5, 0, -1)

    def busiest_row(strip):
        with torch.inference_mode():
            change = (model.features(to_batch([strip])[0]) - model.features(to_batch([blank])[0])).abs()
        return int(change.sum(dim=(0, 1, 3)).argmax())

    assert busiest_row(high) < busiest_row(low)


def test_find_staves_follows_a_curled_page():
    page = draw_page([120, 400], curve=40)
    found = find_staves(page)
    assert len(found) == 2
    middle = page.shape[1] / 2
    assert found[0].top_at(middle) == pytest.approx(160, abs=3)
    assert found[0].top_at(200) == pytest.approx(120 + 40 * np.sin(np.pi * 200 / 1600), abs=3)


def test_a_curled_staff_comes_out_level():
    page = draw_page([200], curve=60)
    strip, _, _ = crop_staff(page, find_staves(page)[0])
    ink_rows = [np.argmin(strip[:, column]) for column in range(30, strip.shape[1] - 30, 40)]
    assert max(ink_rows) - min(ink_rows) <= 3


def test_ledger_lines_do_not_make_a_false_staff():
    """Two ledger lines over a staff look like a staff themselves, two lines higher."""
    page = draw_page([200, 450], ledger_at=(500, 184))
    assert len(find_staves(page)) == 2


def test_a_page_with_no_staff_has_none():
    assert find_staves(np.full((500, 800), 255, np.uint8)) == []


def test_a_strip_position_maps_back_to_the_page():
    page = draw_page([200])
    cv2.circle(page, (700, 232), 8, 0, -1)
    strip, left, scale = crop_staff(page, find_staves(page)[0])
    mask = strip[STRIP_HEIGHT // 2 - 8 : STRIP_HEIGHT // 2 + 8] < 90
    column = int(np.median(np.flatnonzero(mask.mean(axis=0) > 0.4)))
    assert left + column / scale == pytest.approx(700, abs=6)


def test_wide_batches_are_cut_to_fit_memory():
    wide = [(np.zeros((96, 1900), np.uint8), [])] * 24
    assert 1 <= len(fit_memory(wide)) < 24


def test_token_error_counts_edits_per_true_token():
    assert token_error([["a", "b"]], [["a", "b", "c", "d"]]) == 0.5
    assert edit_distance(["note:1:4"], ["note:2:4"]) == 1
