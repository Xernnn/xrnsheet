"""The readers as the app runs them: exported to ONNX, in onnxruntime, with no PyTorch.

They're fetched from a release (`python3 tools/fetch_models.py`, which CI runs with --onnx), and
these read with what was fetched. tests/test_exported.py checks that they read as the trained ones
do, which needs PyTorch.
"""

import random

import numpy as np
import pytest
import verovio
from PIL import Image, ImageDraw, ImageFont

from chords.fonts import ROOT
from chords.metrics import edit_distance
from chords.reading import FETCH_MODELS, MissingModel, load_network, read_images
from chords.render import fit_height
from notes import render
from notes.page import note_model
from notes.reading import read_strips
from notes.staves import crop_staff, find_staves
from pipeline.engrave import RESOURCE_PATH

MODELS = ROOT / "models"
# Drawn in Pillow's own font, which the chord reader never trained on, so no font has to be downloaded.
CHORDS = ["C", "G7", "Dm7", "Fmaj7", "Bb", "Am", "E7", "Cm7", "D", "A7", "Gm7", "F"]


def drawn_chord(text: str) -> np.ndarray:
    font = ImageFont.load_default(size=44)
    image = Image.new("L", (400, 80), 255)
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((10, 10), text, font=font)
    draw.text((10, 10), text, font=font, fill=0)
    return fit_height(np.array(image)[top - 6 : bottom + 6, left - 6 : right + 6])


def test_the_chord_reader_reads_chords():
    read = read_images(load_network(MODELS / "chord_reader.pt", "chords.model"), [drawn_chord(text) for text in CHORDS])
    assert sum(text == chord for (text, _), chord in zip(read, CHORDS)) >= len(CHORDS) - 1, read


def test_the_note_reader_reads_a_page():
    """Pages of a generated melody, drawn in a music font held out of training, read a line at a time."""
    toolkit = verovio.toolkit()
    toolkit.setResourcePath(RESOURCE_PATH)
    wrong = total = 0
    for seed in range(3):
        rng = random.Random(seed)
        xml, lines = render.sample_page(rng)
        page = render.render_page(xml, "Leipzig", rng, toolkit)
        strips = [crop_staff(page, staff)[0] for staff in find_staves(page)]
        assert len(strips) == len(lines)
        for reading, tokens in zip(read_strips(note_model("cpu"), strips), lines):
            wrong += edit_distance(reading.tokens, tokens)
            total += len(tokens)
    assert total > 100 and wrong / total < 0.05, f"{wrong} of {total} tokens wrong"


def test_a_reader_not_yet_fetched_says_how_to_fetch_it(tmp_path):
    with pytest.raises(MissingModel, match=FETCH_MODELS.replace(".", r"\.")):
        load_network(tmp_path / "chord_reader.pt", "chords.model")
