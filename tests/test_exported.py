"""The app reads with the readers exported to ONNX, so they have to read as the trained ones do.

A reader retrained and not exported again would leave the app reading with the old one. These read
the same images both ways and fail until `python -m chords.export` or `python -m notes.export` is run.
"""

import random
import shutil

import numpy as np
import pytest
import verovio

from chords.fonts import ROOT, split_fonts
from chords.model import load_reader as load_chord_reader, read_images as read_images_in_pytorch
from chords.reading import load_network, read_images
from chords.render import make_sample
from notes import render
from notes.model import load_reader as load_note_reader, read_strips as read_strips_in_pytorch
from notes.reading import read_strips
from notes.staves import crop_staff, find_staves
from pipeline.engrave import RESOURCE_PATH

MODELS = ROOT / "models"
# What onnxruntime and PyTorch differ by on the same network is about 2e-6. A model retrained, even
# for a few steps, differs by far more.
CLOSE = 1e-4
STALE = "The exported reader reads differently from the trained one: run python -m {}.export"


def chord_crops() -> list[np.ndarray]:
    """Chords, words and clutter as the app crops them, in the fonts held out of training, some damaged."""
    rng = random.Random(3)
    fonts = [font.path for font in split_fonts("test")]
    return [make_sample(fonts[index % len(fonts)], rng, damage=index % 2 == 0, negatives=0.2, hand_layout=0.35)[0] for index in range(40)]


def staff_strips() -> list[np.ndarray]:
    """Lines of a generated melody, drawn in a music font held out of training."""
    toolkit = verovio.toolkit()
    toolkit.setResourcePath(RESOURCE_PATH)
    strips = []
    for seed in range(4):
        rng = random.Random(seed)
        xml, _ = render.sample_page(rng)
        page = render.render_page(xml, "Leipzig", rng, toolkit)
        if page is not None:
            strips += [crop_staff(page, staff)[0] for staff in find_staves(page)]
    return strips


def test_the_exported_chord_reader_reads_as_the_trained_one():
    crops = chord_crops()
    trained = read_images_in_pytorch(load_chord_reader(MODELS / "chord_reader.pt"), crops)
    exported = read_images(load_network(MODELS / "chord_reader.pt", "chords.model"), crops)
    assert [text for text, _ in exported] == [text for text, _ in trained], STALE.format("chords")
    assert [confidence for _, confidence in exported] == pytest.approx([confidence for _, confidence in trained], abs=CLOSE), STALE.format("chords")
    assert sum(bool(text) for text, _ in trained) > 20  # Read as chords, so the check means something.


def test_the_exported_note_reader_reads_as_the_trained_one():
    strips = staff_strips()
    assert len(strips) >= 8
    trained = read_strips_in_pytorch(load_note_reader(MODELS / "note_reader.pt"), strips)
    exported = read_strips(load_network(MODELS / "note_reader.pt", "notes.model"), strips)
    assert [reading.tokens for reading in exported] == [reading.tokens for reading in trained], STALE.format("notes")
    assert [reading.xs for reading in exported] == [reading.xs for reading in trained], STALE.format("notes")
    for ours, theirs in zip(exported, trained):
        assert ours.confidences == pytest.approx(theirs.confidences, abs=CLOSE), STALE.format("notes")


def test_a_reader_not_exported_is_read_with_pytorch_and_says_so(tmp_path, capsys):
    shutil.copy(MODELS / "chord_reader.pt", tmp_path / "chord_reader.pt")
    crops = chord_crops()[:6]
    network = load_network(tmp_path / "chord_reader.pt", "chords.model")
    assert "chord_reader.onnx is missing" in capsys.readouterr().err
    assert read_images(network, crops) == read_images_in_pytorch(load_chord_reader(tmp_path / "chord_reader.pt"), crops)
