import random

import numpy as np
import torch

from chords.fonts import FONTS, split_fonts
from chords.model import BLANK, CHARACTER_INDEX, ChordReader, decode, to_batch
from chords.render import HEIGHT, make_sample, sample_label, sample_word
from chords.vocab import parse_chord


def test_test_fonts_are_never_trained_on():
    names = {split: {font.file for font in split_fonts(split)} for split in ("train", "val", "test")}
    assert not (names["train"] & names["test"]) and not (names["train"] & names["val"]) and not (names["val"] & names["test"])
    assert len(names["test"]) >= 3


def test_the_held_out_fonts_stay_held_out():
    """Results are only comparable if these never move, so they're pinned by name."""
    assert {font.file for font in split_fonts("test")} == {
        "PetalumaScript.otf",
        "Kalam-Regular.ttf",
        "Arimo[wght].ttf",
        "LiberationSerif-Regular.ttf",
        "NotoSerif-Regular.ttf",
    }


def test_metric_compatible_twins_of_test_fonts_are_not_in_training():
    # Tinos and Liberation Serif are the same design, as are Arimo and Liberation Sans.
    files = {font.file for font in FONTS if font.split == "train"}
    assert not any(name.startswith(("Tinos", "Arimo", "LiberationS", "NotoSerif")) for name in files)


def test_every_generated_label_is_a_chord():
    rng = random.Random(0)
    assert all(parse_chord(sample_label(rng)) is not None for _ in range(2000))


def test_negative_words_are_not_chords():
    rng = random.Random(0)
    assert all(parse_chord(sample_word(rng)) is None for _ in range(300))


def test_samples_have_model_height_and_are_repeatable():
    font = split_fonts("train")[0].path
    first = make_sample(font, random.Random(5), damage=True)
    second = make_sample(font, random.Random(5), damage=True)
    assert first[0].shape[0] == HEIGHT and first[1] == second[1] and np.array_equal(first[0], second[0])


def test_model_output_shape_follows_image_width():
    pixels, widths = to_batch([np.full((HEIGHT, 120), 255, np.uint8), np.full((HEIGHT, 80), 255, np.uint8)])
    model = ChordReader().eval()
    with torch.inference_mode():
        log_probabilities, lengths = model(pixels, widths)
    assert log_probabilities.shape[:2] == (2, 30) and lengths.tolist() == [30, 20]


def probabilities(indices, characters):
    table = torch.full((1, len(indices), characters), -10.0)
    for step, index in enumerate(indices):
        table[0, step, index] = 0.0
    return table


def test_decoding_merges_repeats_and_drops_blanks():
    size = max(CHARACTER_INDEX.values()) + 1
    a, seven = CHARACTER_INDEX["A"], CHARACTER_INDEX["7"]
    table = probabilities([a, a, BLANK, seven, seven], size)
    assert decode(table, torch.tensor([5]))[0][0] == "A7"


def test_a_blank_keeps_a_real_double_character():
    size = max(CHARACTER_INDEX.values()) + 1
    c, one = CHARACTER_INDEX["C"], CHARACTER_INDEX["1"]
    table = probabilities([c, one, BLANK, one], size)
    assert decode(table, torch.tensor([4]))[0][0] == "C11"
