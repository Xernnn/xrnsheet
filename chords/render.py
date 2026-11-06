"""Generate chord symbol images whose text is known, for training and testing.

Real labelled chord images are scarce and mostly non-commercial, so the images are
synthetic. Each one mimics a box from the text detector: tight around the chord, often
with a stem or staff line poking in, and often with camera damage.
"""

import random
import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

from chords.fonts import FALLBACK_FONTS
from chords.vocab import QUALITIES, ROOTS, parse_chord

HEIGHT = 48
MAX_WIDTH = 400

WORDS = (
    "the and come on hear band Alex ander best land like you never heard that just what "
    "Piano Pno Voice Violin Allegro Andante Moderato Swing Ballad Medium Fine Coda Intro "
    "rit cresc dim Bridge Solo Verse Chorus Fast Slow Tempo love night moon blue day sun "
    "1 2 3 4 5 8 9 10 12 16 24 32 N.C. D.C. D.S. mf mp pp ff"
).split()


@lru_cache(maxsize=None)
def load_font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


@lru_cache(maxsize=None)
def font_characters(path: str) -> frozenset[int]:
    with TTFont(path, lazy=True) as font:
        return frozenset(font.getBestCmap())


def sample_label(rng: random.Random, common_weight: float = 1.0) -> str:
    """Pick a chord spelling. common_weight below 1 shows rare chords more often."""
    meanings = list(QUALITIES)
    weights = [QUALITIES[meaning][0] ** common_weight for meaning in meanings]
    meaning = rng.choices(meanings, weights=weights)[0]
    bass = "/" + rng.choice(ROOTS) if rng.random() < 0.1 else ""
    return rng.choice(ROOTS) + rng.choice(QUALITIES[meaning][1]) + bass


def sample_word(rng: random.Random) -> str:
    """Pick text the detector might box that isn't a chord."""
    while True:
        word = rng.choice(WORDS)
        if parse_chord(word) is None:
            return word


def glyph_font(character: str, font_path: str, size: int, rng: random.Random) -> ImageFont.FreeTypeFont:
    if ord(character) in font_characters(font_path):
        return load_font(font_path, size)
    options = [path for path in FALLBACK_FONTS if ord(character) in font_characters(path)]
    return load_font(rng.choice(options) if options else font_path, size)


def draw_text(text: str, font_path: Path, rng: random.Random, music_glyphs: bool) -> np.ndarray:
    """Draw text the way charts do: music flats, sometimes a raised, smaller quality."""
    size = rng.randint(36, 64)
    canvas = Image.new("L", (size * (len(text) + 4), size * 4), 255)
    draw = ImageDraw.Draw(canvas)
    root_length = 2 if re.match(r"^[A-G][b#]", text) else 1
    raised = music_glyphs and rng.random() < 0.3
    tracking = rng.uniform(-1, 3)
    x, baseline = float(size), size * 2.6

    for index, character in enumerate(text):
        is_quality = raised and index >= root_length and "/" not in text[root_length:index + 1]
        shown = character
        if music_glyphs and character in "b#" and rng.random() < 0.85:
            shown = "♭" if character == "b" else "♯"
        glyph_size = int(size * 0.72) if is_quality else size
        font = glyph_font(shown, str(font_path), glyph_size, rng)
        y = baseline - (size * 0.3 if is_quality else 0)
        draw.text((x, y), shown, font=font, fill=0, anchor="ls")
        x += font.getlength(shown) + tracking

    pixels = np.array(canvas)
    rows = np.where(pixels.min(axis=1) < 128)[0]
    columns = np.where(pixels.min(axis=0) < 128)[0]
    if len(rows) == 0 or len(columns) == 0:
        return np.full((size, size), 255, dtype=np.uint8)
    pad = [rng.randint(2, max(3, size // 4)) for _ in range(4)]
    top, bottom = max(0, rows[0] - pad[0]), rows[-1] + pad[1]
    left, right = max(0, columns[0] - pad[2]), columns[-1] + pad[3]
    return pixels[top:bottom, left:right].copy()


def pasted_glyph(canvas: np.ndarray, character: str, font_path: Path, size: int, x: float, y: float, rng: random.Random, tall: float = 1.0) -> float:
    """Draw one character with its own tilt onto a canvas, keeping the darker ink. Returns its width."""
    font = glyph_font(character, str(font_path), size, rng)
    cell = int(size * 2.4)
    glyph = Image.new("L", (cell, cell), 255)
    ImageDraw.Draw(glyph).text((cell // 2, cell // 2), character, font=font, fill=0, anchor="mm")
    pixels = np.array(glyph)
    if character == "7" and rng.random() < 0.3:  # A European 7 has a bar through its stem.
        middle = cell // 2 + rng.randint(-size // 10, size // 8)
        half = rng.randint(size // 6, size // 3)
        cv2.line(pixels, (cell // 2 - half, middle), (cell // 2 + half, middle), 0, max(2, size // 14))
    matrix = cv2.getRotationMatrix2D((cell / 2, cell / 2), rng.uniform(-13, 13), 1.0)
    matrix[1, 1] *= tall
    matrix[1, 2] += cell / 2 * (1 - tall)
    pixels = cv2.warpAffine(pixels, matrix, (cell, cell), borderValue=255)

    left, top = int(x - cell / 2), int(y - cell / 2)
    x0, y0 = max(left, 0), max(top, 0)
    x1, y1 = min(left + cell, canvas.shape[1]), min(top + cell, canvas.shape[0])
    if x1 > x0 and y1 > y0:
        patch = pixels[y0 - top:y1 - top, x0 - left:x1 - left]
        canvas[y0:y1, x0:x1] = np.minimum(canvas[y0:y1, x0:x1], patch)
    return font.getlength(character)


def warp_like_a_pen(image: np.ndarray, size: int, rng: random.Random) -> np.ndarray:
    """Bend strokes smoothly, slant the writing and vary the pen width.

    The bend is scaled to the character size. Past about a tenth of it, letters stop being
    letters, and a label nobody could read is only noise to the model.
    """
    height, width = image.shape
    generator = np.random.default_rng(rng.getrandbits(32))
    strength = rng.uniform(0.02, 0.09) * size
    coarse = (max(2, width // size + 1), max(2, height // size + 1))
    shifts = [cv2.resize(generator.normal(0, strength, (coarse[1], coarse[0])).astype(np.float32), (width, height), interpolation=cv2.INTER_CUBIC) for _ in range(2)]
    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    slant = rng.uniform(-0.28, 0.12)
    image = cv2.remap(image, grid_x + shifts[0] + slant * (grid_y - height / 2), grid_y + shifts[1], cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    pen = rng.random()
    if pen < 0.35:
        image = cv2.erode(image, np.ones((2, 2), np.uint8))
    elif pen < 0.5:
        image = cv2.dilate(image, np.ones((2, 2), np.uint8))
    return image


def draw_hand_layout(text: str, font_path: Path, rng: random.Random) -> np.ndarray:
    """Lay a chord out the way people write one, rather than as a straight line of type.

    Real charts raise the extension well above the root, leave wide gaps after it, and run
    tall sharps straight through the letter. None of that appears in typeset text.
    """
    size = rng.randint(40, 62)
    canvas = np.full((size * 5, size * (2 * len(text) + 6)), 255, dtype=np.uint8)
    root_length = 2 if re.match(r"^[A-G][b#]", text) else 1
    slash = text.find("/", root_length)
    raised = rng.random() < 0.65
    lift = rng.uniform(0.2, 0.7) * size if raised else 0.0
    root_gap = rng.uniform(0.15, 1.0) * size if rng.random() < 0.45 else 0.0
    x, baseline = float(size), size * 3.0

    for index, character in enumerate(text):
        is_quality = index >= root_length and (slash < 0 or index < slash)
        if index == root_length:
            x += root_gap
        scale = rng.uniform(0.85, 1.15) * (rng.uniform(0.65, 1.0) if is_quality and raised else 1.0)
        y = baseline - (lift if is_quality else 0) + rng.uniform(-0.09, 0.09) * size
        shown, tall = character, 1.0
        if character in "b#" and rng.random() < 0.85:
            shown = "♭" if character == "b" else "♯"
        if index == 1 and character == "#" and rng.random() < 0.6:
            x -= size * rng.uniform(0.15, 0.5)  # The sharp is drawn through the end of the letter.
            tall = rng.uniform(1.3, 2.0)
        if index == 1 and character == "b":
            y -= size * rng.uniform(0.1, 0.45)
        advance = pasted_glyph(canvas, shown, font_path, int(size * scale), x, y, rng, tall)
        x += advance + rng.uniform(-0.04, 0.2) * size
        if character == "-" and rng.random() < 0.4:
            x += rng.uniform(0.1, 0.5) * size

    canvas = warp_like_a_pen(canvas, size, rng)
    rows = np.where(canvas.min(axis=1) < 128)[0]
    columns = np.where(canvas.min(axis=0) < 128)[0]
    if len(rows) == 0 or len(columns) == 0:
        return np.full((size, size), 255, dtype=np.uint8)
    pad = [rng.randint(2, max(3, size // 3)) for _ in range(4)]
    top, bottom = max(0, rows[0] - pad[0]), rows[-1] + pad[1]
    left, right = max(0, columns[0] - pad[2]), columns[-1] + pad[3]
    return canvas[top:bottom, left:right].copy()


def add_slur(image: np.ndarray, rng: random.Random) -> np.ndarray:
    """Slurs and bracket lines from nearby music often cross the box."""
    height, width = image.shape
    if rng.random() < 0.5:
        center = (rng.randrange(width), rng.choice([-height // 4, height + height // 4]))
        cv2.ellipse(image, center, (width // 2, height // 2), 0, 0, 360, 0, rng.randint(1, 3))
    else:
        y = rng.choice([rng.randint(0, height // 6), height - 1 - rng.randint(0, height // 6)])
        cv2.line(image, (0, y), (width, y + rng.randint(-height // 10, height // 10)), 0, rng.randint(1, 3))
    return image


def add_notation_clutter(image: np.ndarray, rng: random.Random) -> np.ndarray:
    """Stems, staff lines and noteheads often reach into the box around a chord."""
    height, width = image.shape
    if rng.random() < 0.3:
        x = rng.randrange(width)
        cv2.line(image, (x, height), (x, rng.randint(0, height)), 0, rng.randint(1, 3))
    if rng.random() < 0.15:
        y = height - rng.randint(1, max(2, height // 6))
        cv2.line(image, (0, y), (width, y), 0, rng.randint(1, 2))
    if rng.random() < 0.08:
        center = (rng.randrange(width), height - rng.randint(0, height // 5))
        cv2.ellipse(image, center, (height // 5, height // 7), -20, 0, 360, 0, -1)
    return image


def notehead_image(rng: random.Random) -> np.ndarray:
    """The detector sometimes boxes a single notehead."""
    size = rng.randint(30, 60)
    image = np.full((size, int(size * 1.2)), 255, dtype=np.uint8)
    center = (image.shape[1] // 2, size // 2)
    axes = (size // 3, size // 4)
    cv2.ellipse(image, center, axes, -20, 0, 360, 0, -1 if rng.random() < 0.7 else 3)
    for y in range(size // 6, size, size // 3):
        cv2.line(image, (0, y), (image.shape[1], y), 0, 1)
    return image


def staff_fragment_image(rng: random.Random) -> np.ndarray:
    """A piece of staff with pen strokes through it, which is not a chord.

    Text detectors box these all over handwritten pages. Bars across a stem look a lot like
    F and E, so a model that has never seen them calls them chords with high confidence.
    """
    spacing = rng.randint(12, 34)
    lines = rng.randint(1, 5)
    width = rng.randint(spacing, spacing * 5)
    height = spacing * lines + rng.randint(spacing // 2, spacing * 2)
    image = np.full((height, width), 255, dtype=np.uint8)
    top = rng.randint(0, spacing)
    thickness = rng.randint(1, 5)
    for line in range(lines):
        y = top + line * spacing
        cv2.line(image, (0, y), (width, y + rng.randint(-2, 2)), 0, thickness)
    for _ in range(rng.randint(1, 4)):
        kind = rng.random()
        x, y = rng.randrange(width), rng.randrange(height)
        pen = rng.randint(1, 4)
        if kind < 0.35:  # A stem, often leaning.
            lean = rng.randint(-spacing // 2, spacing // 2)
            cv2.line(image, (x, 0), (x + lean, height), 0, pen)
        elif kind < 0.6:  # A slashed notehead.
            reach = rng.randint(spacing // 2, spacing)
            cv2.line(image, (x - reach, y + reach // 2), (x + reach, y - reach // 2), 0, pen + 1)
        elif kind < 0.8:  # The doubled stems of a tall sharp.
            gap = rng.randint(spacing // 3, spacing)
            for offset in (0, gap):
                cv2.line(image, (x + offset, 0), (x + offset + rng.randint(-4, 4), height), 0, pen)
        else:  # An open notehead or a flat.
            cv2.ellipse(image, (x, y), (spacing // 2, spacing // 3), -20, 0, 360, 0, pen)
    return warp_like_a_pen(image, spacing * 2, rng)


def add_camera_damage(image: np.ndarray, rng: random.Random) -> np.ndarray:
    height, width = image.shape
    angle = rng.uniform(-3, 3)
    shear = rng.uniform(-0.12, 0.12)
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    matrix[0, 1] += shear
    image = cv2.warpAffine(image, matrix, (width, height), borderValue=255)

    paper = rng.uniform(170, 250)
    ink = rng.uniform(0, 80)
    pixels = ink + (paper - ink) * (image.astype(np.float32) / 255)
    ramp = np.linspace(rng.uniform(0.8, 1.0), rng.uniform(0.8, 1.0), width, dtype=np.float32)
    pixels = pixels * ramp[None, :]

    scale = rng.uniform(0.45, 1.0)
    small = cv2.resize(pixels, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    pixels = cv2.resize(small, (width, height), interpolation=cv2.INTER_LINEAR)
    pixels = cv2.GaussianBlur(pixels, (0, 0), rng.uniform(0.3, 1.5))
    generator = np.random.default_rng(rng.getrandbits(32))
    pixels = pixels + generator.normal(0, rng.uniform(0, 12), pixels.shape)
    pixels = np.clip(pixels, 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(45, 95)])
    return cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE) if ok else pixels


def fit_height(image: np.ndarray) -> np.ndarray:
    """Resize to the model's fixed height, keeping the shape of the characters."""
    height, width = image.shape
    new_width = max(HEIGHT // 2, min(MAX_WIDTH, round(width * HEIGHT / height)))
    return cv2.resize(image, (new_width, HEIGHT), interpolation=cv2.INTER_AREA)


def make_sample(
    font_path: Path,
    rng: random.Random,
    damage: bool,
    negatives: float = 0.0,
    common_weight: float = 1.0,
    hand_layout: float = 0.0,
) -> tuple[np.ndarray, str]:
    """Return one image and its text. Non-chord images get empty text.

    hand_layout is the share of chords laid out like handwriting. It stays at 0 by default
    so the stored test images keep meaning what they did when they were generated.
    """
    roll = rng.random()
    if roll < negatives * 0.25:
        image, label = notehead_image(rng), ""
    elif hand_layout and roll < negatives * 0.65:
        image, label = staff_fragment_image(rng), ""
    elif roll < negatives:
        image, label = draw_text(sample_word(rng), font_path, rng, music_glyphs=False), ""
    else:
        label = sample_label(rng, common_weight)
        if hand_layout and rng.random() < hand_layout:
            image = draw_hand_layout(label, font_path, rng)
            if rng.random() < 0.3:
                image = add_slur(image, rng)
        else:
            image = draw_text(label, font_path, rng, music_glyphs=rng.random() < 0.75)
    if damage:
        image = add_camera_damage(add_notation_clutter(image, rng), rng)
    return fit_height(image), label
