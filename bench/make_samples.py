"""Build the benchmark set: scores rendered to images whose correct answer is known.

Each piece is written to data/truth/<piece>.musicxml and rendered to data/images/
under three conditions:

    clean        crisp engraved print, like a good PDF
    photo        the same page with simulated phone-camera damage
    handwritten  a handwritten-style music font, like many jazz charts

The "photo" condition is a simulation. It is useful for comparing engines, but real
photos of your own sheets are the test that counts.
"""

import os
import random
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import verovio
from music21 import clef, corpus, harmony, key, metadata, meter, note, stream

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chords.fonts import FONT_DIR  # noqa: E402
from chords.render import sample_label  # noqa: E402
from chords.vocab import parse_chord  # noqa: E402
from pipeline.engrave import drop_spelled_degrees, replace_private_glyphs  # noqa: E402
from pipeline.musicxml import insert_harmony, read_measures, to_text  # noqa: E402

TRUTH_DIR = ROOT / "data" / "truth"
IMAGE_DIR = ROOT / "data" / "images"
FONT_CONFIG = ROOT / "data" / "fonts.conf"
PAGE_WIDTH_PIXELS = 3000  # Close to a phone photo, where a page fills most of a 12MP frame.

# These charts use only the fonts the chord model never trains on.
CHART_COUNT = 10
CHART_FONTS = ["Petaluma Script", "Kalam", "Arimo", "Liberation Serif", "Noto Serif"]

# The chorus of "Jingle Bells" by James Lord Pierpont (1822-1893), first published in 1857, written
# out here as a lead sheet in F with its words. Each bar is the chord that starts it, if one does,
# and its notes as pitch:quarters:syllable, a hyphen joining the syllables of a word.
JINGLE_BELLS = [
    ("F", "A4:1:Jin- A4:1:-gle A4:2:bells,"),
    (None, "A4:1:jin- A4:1:-gle A4:2:bells,"),
    (None, "A4:1:jin- C5:1:-gle F4:1.5:all G4:0.5:the"),
    (None, "A4:4:way."),
    ("B-", "B-4:1:Oh B-4:1:what B-4:1.5:fun B-4:0.5:it"),
    ("F", "B-4:1:is A4:1:to A4:1:ride A4:0.5:in A4:0.5:a"),
    ("G7", "A4:1:one- G4:1:-horse G4:1:o- A4:1:-pen"),
    ("C7", "G4:2:sleigh, C5:2:hey!"),
    ("F", "A4:1:Jin- A4:1:-gle A4:2:bells,"),
    (None, "A4:1:jin- A4:1:-gle A4:2:bells,"),
    (None, "A4:1:jin- C5:1:-gle F4:1.5:all G4:0.5:the"),
    (None, "A4:4:way."),
    ("B-", "B-4:1:Oh B-4:1:what B-4:1:fun B-4:1:it"),
    ("F", "B-4:1:is A4:1:to A4:1:ride A4:0.5:in A4:0.5:a"),
    ("C7", "C5:1:one- C5:1:-horse B-4:1:o- G4:1:-pen"),
    ("F", "F4:4:sleigh."),
]


def jingle_bells() -> stream.Score:
    """Write out the lead sheet above: a song whose author died in 1893 is out of copyright everywhere."""
    part = stream.Part()
    for number, (figure, notes) in enumerate(JINGLE_BELLS, start=1):
        measure = stream.Measure(number=number)
        if number == 1:
            measure.append([clef.TrebleClef(), key.KeySignature(-1), meter.TimeSignature("4/4")])
        if figure:
            measure.insert(0, harmony.ChordSymbol(figure))
        for written in notes.split():
            pitch, quarters, syllable = written.split(":")
            sung = note.Note(pitch, quarterLength=float(quarters))
            sung.addLyric(syllable)
            measure.append(sung)
        part.append(measure)
    part.makeBeams(inPlace=True)
    score = stream.Score([part])
    score.metadata = metadata.Metadata(title="Jingle Bells", composer="James Lord Pierpont")
    return score


# name: (music21 corpus path or a function that writes the piece out, last measure to keep, style tag)
PIECES = {
    "foster_leadsheet": ("leadSheet/fosterBrownHair.mxl", 16, "jazz"),
    "pierpont_leadsheet": (jingle_bells, 16, "jazz"),
    "joplin_piano": ("joplin/maple_leaf_rag.mxl", 12, "classical"),
    "schumann_piano": ("schumann_clara/polonaise_op1n1.mxl", 12, "classical"),
    "schubert_song": ("schubert/Lindenbaum.xml", 12, "classical"),
    "bach_chorale": ("bach/bwv66.6.mxl", 9, "classical"),
    "mozart_quartet": ("mozart/k155/movement1.mxl", 10, "classical"),
}

# Chord vocabulary typical of a jazz chart, including alterations the old public-domain
# lead sheets above never use. music21 spells a flat root with "-", so E-m7 is Ebm7.
JAZZ_PROGRESSION = [
    "Dm7", "G7", "Cmaj7", "A7", "Dm7", "D-7", "Cmaj7", "Bm7b5",
    "E7", "Am7", "E-m7", "A-7", "D-maj7", "Gm7", "C7", "Fmaj7",
]
MELODY_NOTES = ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5", "D5", "E5", "F5", "G5"]


def jazz_chart() -> stream.Score:
    """Generate an original chart so its chords and melody carry no copyright."""
    rng = random.Random(7)
    part = stream.Part()
    part.append(clef.TrebleClef())
    part.append(key.KeySignature(0))
    part.append(meter.TimeSignature("4/4"))
    for number, figure in enumerate(JAZZ_PROGRESSION, start=1):
        measure = stream.Measure(number=number)
        measure.insert(0, harmony.ChordSymbol(figure))
        offset = 0.0
        position = rng.randrange(len(MELODY_NOTES))
        while offset < 4.0:
            length = rng.choice([0.5, 0.5, 1.0, 1.0, 1.5, 2.0])
            length = min(length, 4.0 - offset)
            if rng.random() < 0.12:
                measure.insert(offset, note.Rest(quarterLength=length))
            else:
                position = max(0, min(len(MELODY_NOTES) - 1, position + rng.choice([-2, -1, 1, 2])))
                measure.insert(offset, note.Note(MELODY_NOTES[position], quarterLength=length))
            offset += length
        part.append(measure)
    part.makeBeams(inPlace=True)
    return stream.Score([part])


def render_png(musicxml: str, font: str, out_path: Path, chord_font: str | None = None) -> None:
    toolkit = verovio.toolkit()
    toolkit.setOptions({
        "font": font,
        "pageWidth": 2100,
        "pageHeight": 2970,
        "pageMarginLeft": 100,
        "pageMarginRight": 100,
        "pageMarginTop": 100,
        "pageMarginBottom": 100,
        "scale": 45,
        "adjustPageHeight": True,
        "header": "none",
        "footer": "none",
        "breaks": "auto",
    })
    if not toolkit.loadData(musicxml):
        raise RuntimeError(f"Verovio could not load {out_path.stem}")
    pages = toolkit.getPageCount()
    if pages != 1:
        raise RuntimeError(f"{out_path.stem} spans {pages} pages; keep fewer measures")
    with tempfile.NamedTemporaryFile(suffix=".svg", mode="w") as svg:
        markup = replace_private_glyphs(toolkit.renderToSVG(1))
        leftover = sorted({f"U+{ord(c):04X}" for c in re.findall("[\ue000-\uf8ff]", markup)})
        if leftover:
            raise RuntimeError(f"{out_path.stem} has unmapped music symbols: {leftover}")
        if chord_font:
            markup = re.sub(r'(class="harm">\s*<text )', rf'\1font-family="{chord_font}" ', markup)
        svg.write(markup)
        svg.flush()
        subprocess.run(
            ["rsvg-convert", "-w", str(PAGE_WIDTH_PIXELS), "-b", "white", svg.name, "-o", str(out_path)],
            check=True,
            env=os.environ | {"FONTCONFIG_FILE": str(FONT_CONFIG)},
        )


def test_chart(seed: int) -> str:
    """Write a chart using jazz spellings (Δ, ø, slash chords) and return its MusicXML."""
    rng = random.Random(seed)
    part = stream.Part()
    part.append(clef.TrebleClef())
    part.append(key.KeySignature(0))
    part.append(meter.TimeSignature("4/4"))
    for number in range(1, 17):
        measure = stream.Measure(number=number)
        offset = 0.0
        position = rng.randrange(len(MELODY_NOTES))
        while offset < 4.0:
            length = min(rng.choice([0.5, 1.0, 1.0, 2.0]), 4.0 - offset)
            position = max(0, min(len(MELODY_NOTES) - 1, position + rng.choice([-2, -1, 1, 2])))
            measure.insert(offset, note.Note(MELODY_NOTES[position], quarterLength=length))
            offset += length
        part.append(measure)
    part.makeBeams(inPlace=True)

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "chart.musicxml"
        stream.Score([part]).write("musicxml", fp=path)
        root = ET.parse(path).getroot()

    part_element = root.find("part")
    divisions = int(root.findtext(".//divisions"))
    for measure in read_measures(part_element):
        onsets = [0.0]
        later = [event.onset for event in measure.events if event.onset >= 2.0]
        if later and rng.random() < 0.4:
            onsets.append(later[0])
        for onset in onsets:
            label = sample_label(rng)
            chord = parse_chord(label)
            insert_harmony(measure, chord, onset, divisions)
            printed = label.split("/")[0]
            printed = printed[2:] if printed[1:2] in ("b", "#") else printed[1:]
            added = measure.element.findall("harmony")[-1 if onset else 0]
            added.find("kind").set("text", printed)
    return drop_spelled_degrees(to_text(root))


def simulate_photo(image: np.ndarray, seed: int) -> np.ndarray:
    """Apply the damage a phone photo adds: angle, page curl, shadow, blur and noise."""
    rng = np.random.default_rng(seed)
    height, width = image.shape[:2]

    pad = int(0.05 * width)
    image = cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    height, width = image.shape[:2]

    corners = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    jitter = rng.uniform(-0.03, 0.03, size=(4, 2)) * [width, height]
    warp = cv2.getPerspectiveTransform(corners, np.float32(corners + jitter))
    image = cv2.warpPerspective(
        image, warp, (width, height), borderMode=cv2.BORDER_CONSTANT, borderValue=(205, 200, 190)
    )

    grid_x, grid_y = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    curl = 0.012 * height * np.sin(np.pi * grid_x / width + rng.uniform(0, 0.6))
    image = cv2.remap(
        image, grid_x, grid_y + curl, cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(205, 200, 190),
    )

    angle = rng.uniform(0, 2 * np.pi)
    ramp = grid_x * np.cos(angle) + grid_y * np.sin(angle)
    ramp = (ramp - ramp.min()) / (ramp.max() - ramp.min())
    shade = (0.62 + 0.38 * ramp)[..., None] * np.array([0.93, 0.97, 1.0])
    photo = image.astype(np.float32) * shade

    photo = cv2.GaussianBlur(photo, (0, 0), 1.5)
    photo += rng.normal(0, 7, photo.shape)
    photo = np.clip(photo, 0, 255).astype(np.uint8)

    ok, encoded = cv2.imencode(".jpg", photo, [cv2.IMWRITE_JPEG_QUALITY, 72])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def write_truth(name: str, score: stream.Score) -> str:
    path = TRUTH_DIR / f"{name}.musicxml"
    score.write("musicxml", fp=path)
    return path.read_text(encoding="utf-8")


def main() -> None:
    TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    FONT_CONFIG.write_text(
        '<fontconfig><include ignore_missing="yes">/etc/fonts/fonts.conf</include>'
        f"<dir>{FONT_DIR}</dir></fontconfig>",
        encoding="utf-8",
    )

    for index in range(CHART_COUNT):
        name = f"chart_{index:02d}"
        xml = test_chart(seed=100 + index)
        (TRUTH_DIR / f"{name}.musicxml").write_text(xml, encoding="utf-8")
        clean_path = IMAGE_DIR / f"{name}__clean.png"
        render_png(xml, "Leipzig", clean_path, CHART_FONTS[index % len(CHART_FONTS)])
        cv2.imwrite(str(IMAGE_DIR / f"{name}__photo.png"), simulate_photo(cv2.imread(str(clean_path)), seed=100 + index))
        print(f"{name}: rendered in {CHART_FONTS[index % len(CHART_FONTS)]}")

    pieces = {"jazz_chart": (jazz_chart(), "jazz")}
    for name, (source, last_measure, style) in PIECES.items():
        score = source() if callable(source) else corpus.parse(source)
        pieces[name] = (score.measures(0, last_measure), style)

    for index, (name, (score, style)) in enumerate(pieces.items()):
        xml = write_truth(name, score)
        clean_path = IMAGE_DIR / f"{name}__clean.png"
        render_png(xml, "Leipzig", clean_path)
        render_png(xml, "Petaluma", IMAGE_DIR / f"{name}__handwritten.png")
        clean = cv2.imread(str(clean_path))
        cv2.imwrite(str(IMAGE_DIR / f"{name}__photo.png"), simulate_photo(clean, seed=index))
        print(f"{name}: rendered ({style})")


if __name__ == "__main__":
    main()
