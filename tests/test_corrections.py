import json
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest
import torch

from bench.real_report import choose_sheets, pool
from bench.score import Score
from chords import corrections, retrain
from chords.metrics import mcnemar_exact
from chords.render import HEIGHT


def recognized(measure, onset, chord, text=None, box=(10, 10, 60, 40), page=1):
    return {"measure": measure, "onset": onset, "chord": chord, "text": text or chord, "confidence": 0.9, "box": list(box), "page": page}


def final(measure, onset, text):
    return {"measure": measure, "onset": float(onset), "text": text, "index": 0}


def write_sheet(job_dir, name, chords_read, chords_left, checked=True, upload=b"photo"):
    from pipeline.musicxml import to_text
    from helpers import harmony, note, parsed

    folder = job_dir / name
    (folder / "recognized" / "page_001").mkdir(parents=True)
    (folder / "upload.png").write_bytes(upload)
    cv2.imwrite(str(folder / "recognized" / "page_001" / "page.jpg"), np.full((200, 300), 255, np.uint8))
    (folder / "recognized" / "chords.json").write_text(json.dumps(chords_read))
    first = parsed("".join(harmony(item["chord"][0], "", "major") + note() for item in chords_read))
    (folder / "version_0000.musicxml").write_text(to_text(first))
    left = parsed("".join(harmony(text[0], "", "major") + note() for text in chords_left))
    (folder / "version_0001.musicxml").write_text(to_text(left))
    if checked:
        (folder / "checked.json").write_text("{}")
    return folder


def test_an_unchanged_chord_keeps_the_spelling_on_the_page():
    rows = corrections.label_chords([recognized(0, 0.0, "Cm7", "C-7")], [final(0, 0, "Cm7")])
    assert rows[0]["label"] == "C-7" and rows[0]["outcome"] == "confirmed"


def test_a_changed_chord_takes_the_new_answer():
    rows = corrections.label_chords([recognized(0, 0.0, "D7")], [final(0, 0, "Bm7")])
    assert rows[0]["label"] == "Bm7" and rows[0]["outcome"] == "corrected"


def test_a_deleted_chord_is_not_a_chord():
    rows = corrections.label_chords([recognized(0, 0.0, "F")], [])
    assert rows[0]["label"] == "" and rows[0]["outcome"] == "removed"


def test_a_chord_moved_to_another_beat_is_still_a_real_chord():
    rows = corrections.label_chords([recognized(0, 0.0, "G7", "G7")], [final(0, 2, "G7")])
    assert rows[0]["label"] == "G7" and rows[0]["outcome"] == "moved"


def test_a_deleted_chord_is_skipped_if_a_different_one_was_added_beside_it():
    """It could be a real chord corrected and moved, so it can't safely be called a false one."""
    assert corrections.label_chords([recognized(0, 0.0, "G7")], [final(0, 2, "Dm7")]) == []


def test_a_chord_moved_across_a_barline_is_still_a_real_chord():
    rows = corrections.label_chords([recognized(4, 2.0, "G7", "G7")], [final(5, 0, "G7")])
    assert rows[0]["label"] == "G7" and rows[0]["outcome"] == "moved"


def test_a_chord_added_far_away_says_nothing_about_a_deleted_one():
    rows = corrections.label_chords([recognized(0, 0.0, "G7")], [final(5, 0, "Dm7")])
    assert rows[0]["label"] == "" and rows[0]["outcome"] == "removed"


def test_only_the_first_chord_on_a_beat_was_placed():
    rows = corrections.label_chords([recognized(0, 0.0, "C"), recognized(0, 0.0, "D")], [final(0, 0, "C")])
    assert len(rows) == 1 and rows[0]["chord"] == "C"


def test_added_chords_are_counted_as_missed():
    assert corrections.count_added([recognized(0, 0.0, "C")], [final(0, 0, "C"), final(1, 0, "F")]) == 1


def test_two_photos_of_one_piece_stay_on_the_same_side():
    chords = ["C", "Am", "Dm", "G7", "C", "Am", "F", "G7", "C"]
    other = ["E7", "A7", "D7", "G7", "Cm", "Fm", "Bb7", "Eb", "Ab"]
    keys = corrections.group_key({"a": "f" * 8, "b": "0" * 8, "c": "7" * 8}, {"a": chords, "b": chords[:-1] + ["F"], "c": other})
    assert keys["a"] == keys["b"] and keys["c"] != keys["a"]


def test_short_sheets_are_not_compared():
    keys = corrections.group_key({"a": "1" * 8, "b": "2" * 8}, {"a": ["C", "F"], "b": ["C", "F"]})
    assert keys["a"] != keys["b"]


def test_the_held_out_share_is_about_one_in_five():
    import hashlib

    keys = [hashlib.sha256(str(number).encode()).hexdigest() for number in range(2000)]
    assert 0.17 < sum(map(corrections.is_test_key, keys)) / 2000 < 0.23


def test_export_uses_only_checked_sheets(tmp_path):
    jobs, out = tmp_path / "jobs", tmp_path / "out"
    write_sheet(jobs, "a" * 32, [recognized(0, 0.0, "C")], ["D"])
    write_sheet(jobs, "b" * 32, [recognized(0, 0.0, "F")], ["F"], checked=False, upload=b"other")
    counts = corrections.export(jobs, out)
    rows = corrections.load_labels(out)
    assert len(rows) == 1 and rows[0]["label"] == "D" and rows[0]["outcome"] == "corrected"
    assert counts["corrected"] == 1 and cv2.imread(str(out / rows[0]["image"])) is not None


def test_a_moved_chord_does_not_count_as_missed(tmp_path):
    jobs = tmp_path / "jobs"
    folder = write_sheet(jobs, "a" * 32, [recognized(0, 2.0, "C")], ["C"])
    assert corrections.export(jobs, tmp_path / "out")["moved"] == 1
    assert corrections.export(jobs, tmp_path / "out")["added"] == 0 and folder.exists()


def test_export_starts_clean(tmp_path):
    jobs, out = tmp_path / "jobs", tmp_path / "out"
    jobs.mkdir()
    out.mkdir()
    (out / "stale.txt").write_text("old")
    corrections.export(jobs, out)
    assert not (out / "stale.txt").exists()


def test_the_report_holds_out_the_same_sheets_as_training(tmp_path):
    uploads = {str(number) * 32: f"photo {number}".encode() for number in range(10)}
    for name, upload in uploads.items():
        write_sheet(tmp_path, name, [recognized(0, 0.0, "C")], ["C"], upload=upload)
    corrections.export(tmp_path, tmp_path / "out")
    held_out = {row["sheet"] for row in corrections.load_labels(tmp_path / "out") if row["split"] == "test"}
    assert {folder.name for folder in choose_sheets(tmp_path, False)} == held_out
    assert len(choose_sheets(tmp_path, True)) == 10


def score_row(recall, truth, predicted, note_f1=1.0, notes=10):
    return Score(1.0, 1.0, note_f1, recall, recall, notes, notes, truth, predicted)


def test_report_totals_weigh_long_sheets_more():
    totals = pool([score_row(1.0, 30, 30), score_row(0.0, 10, 0)])
    assert totals["truth_chords"] == 40 and totals["read_right"] == 30 and totals["false_chords"] == 0


def test_report_counts_false_chords():
    assert pool([score_row(0.5, 10, 8)])["false_chords"] == 3


def test_report_handles_notes_only_sheets():
    totals = pool([score_row(None, 0, 0)])
    assert totals["truth_chords"] == 0 and totals["note_f1"] == 1.0


def test_mcnemar_matches_the_published_results():
    assert mcnemar_exact(13, 7) == pytest.approx(0.263, abs=0.001)
    assert mcnemar_exact(21, 6) == pytest.approx(0.0059, abs=0.0001)
    assert mcnemar_exact(13, 4) == pytest.approx(0.049, abs=0.001)
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(5, 5) == 1.0


def test_an_empty_label_means_no_chord_is_the_right_reading():
    assert retrain.same_answer("", "") and retrain.same_answer("xyz", "")
    assert not retrain.same_answer("Cm7", "")
    assert retrain.same_answer("C-7", "Cm7") and not retrain.same_answer("", "Cm7")


def test_too_few_held_out_chords_blocks_promotion():
    passed, reason = retrain.decide([False] * 10, [True] * 10, 0.999)
    assert not passed and "10 held-out" in reason


def test_a_synthetic_regression_blocks_promotion():
    passed, reason = retrain.decide([False] * 40, [True] * 40, 0.95)
    assert not passed and "synthetic" in reason


def test_a_small_gain_could_be_chance():
    old = [True] * 30 + [False] * 10
    new = [True] * 29 + [False] + [True] * 3 + [False] * 7
    passed, reason = retrain.decide(old, new, 0.999)
    assert not passed and "chance" in reason


def test_getting_worse_blocks_promotion():
    passed, reason = retrain.decide([True] * 40, [True] * 30 + [False] * 10, 0.999)
    assert not passed and "isn't better" in reason


def test_a_clear_win_is_promoted():
    old = [True] * 25 + [False] * 15
    passed, _ = retrain.decide(old, [True] * 40, 0.999)
    assert passed


def test_real_images_join_a_synthetic_batch():
    fake = (torch.ones(2, 1, HEIGHT, 100), torch.tensor([100, 100]), torch.tensor([1, 2]), torch.tensor([1, 1]))
    images = [np.full((HEIGHT, 140), 255, np.uint8)]
    pixels, widths, targets, lengths = retrain.join_batches(fake, images, ["C"])
    assert pixels.shape == (3, 1, HEIGHT, 140) and widths.tolist() == [100, 100, 140]
    assert len(targets) == 3 and lengths.tolist() == [1, 1, 1]


def test_a_not_a_chord_example_adds_an_empty_target():
    fake = (torch.ones(1, 1, HEIGHT, 100), torch.tensor([100]), torch.tensor([1]), torch.tensor([1]))
    _, _, targets, lengths = retrain.join_batches(fake, [np.full((HEIGHT, 90), 255, np.uint8)], [""])
    assert len(targets) == 1 and lengths.tolist() == [1, 0]


def test_a_chord_keeps_its_original_bar_after_a_bar_is_added():
    moved = {"measure": 3, "origin": 2, "onset": 0.0, "text": "G7", "index": 0}
    assert corrections.bar_key(moved) == (2, 0.0)
    assert corrections.bar_key(final(2, 0, "G7")) == (2, 0.0)


def test_a_bar_added_before_a_chord_leaves_its_label_alone():
    labels = corrections.label_chords([recognized(2, 0, "G7")], [{"measure": 3, "origin": 2, "onset": 0.0, "text": "G7", "index": 0}])
    assert [item["label"] for item in labels] == ["G7"]


def test_chords_in_a_deleted_bar_are_not_called_false():
    # Deleting a whole bar says nothing about whether each chord in it was real.
    assert corrections.label_chords([recognized(2, 0, "G7")], [], deleted_bars={2}) == []


def test_a_chord_in_a_new_bar_may_be_the_one_that_moved():
    # A new bar has no original number, so it could sit anywhere. Never call the old chord false.
    same = {"measure": 3, "origin": None, "onset": 0.0, "text": "G7", "index": 0}
    assert [item["outcome"] for item in corrections.label_chords([recognized(2, 0, "G7")], [same])] == ["moved"]
    assert corrections.label_chords([recognized(2, 0, "G7")], [same | {"text": "Dm7"}]) == []


def test_chords_in_new_bars_count_as_missed():
    new_bar = {"measure": 3, "origin": None, "onset": 0.0, "text": "G7", "index": 0}
    assert corrections.count_added([recognized(2, 0, "G7")], [new_bar]) == 1
