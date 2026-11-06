"""Generate strips of printed melody whose exact notes are known.

    .venv/bin/python -m notes.render

Each page holds a few lines of random melody: the first line has a time signature and the
rest don't, as on a real page. Chord symbols, lyrics, slurs and repeat signs are added as
clutter the model must learn to ignore. Verovio has five music fonts, and Leipzig and
Petaluma are held out for testing, because the benchmark pages are drawn in those two.
"""

import csv
import multiprocessing
import os
import random
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
import verovio

from chords.fonts import ROOT
from chords.render import sample_label, warp_like_a_pen
from chords.vocab import parse_chord
from notes.build import DIVISIONS, add_note, add_rest, child, event_duration, notations_of
from notes.staves import crop_staff, find_staves
from notes.vocab import ACCIDENTALS, TIMES, key_alteration, length_of, position_to_pitch
from pipeline.engrave import RESOURCE_PATH, add_beams, drop_spelled_degrees, replace_private_glyphs
from pipeline.musicxml import harmony_element, to_text

DATA_DIR = ROOT / "data" / "notes"
TRAIN_FONTS = ["Bravura", "Leland", "Gootville"]
FONTS = {"train": TRAIN_FONTS, "val": TRAIN_FONTS, "test": ["Leipzig", "Petaluma"]}
PAGES = {"train": 22000, "val": 500, "test": 500}
TIME_WEIGHTS = [10, 6, 3, 3, 2, 1, 1]
LENGTH_WEIGHTS = {"1": 1, "2": 5, "2.": 1, "4": 10, "4.": 3, "8": 9, "8.": 1, "16": 3}
LYRICS = "love moon the night and day you my heart on blue".split()
PEN_SIZE = 20  # A staff step is 4 pixels here, so a letter-sized wobble would change the pitch.


def sample_events(total: float, rng: random.Random, triplets: bool) -> list[tuple[str, bool]]:
    """Fill a measure with (length code, is a triplet member)."""
    events: list[tuple[str, bool]] = []
    position = 0.0
    while position < total - 1e-6:
        remaining = total - position
        if triplets and position % 1 == 0 and remaining >= 1 and rng.random() < 0.15:
            code = "4" if position % 2 == 0 and remaining >= 2 and rng.random() < 0.3 else "8"
            events.extend([(code, True)] * 3)
            position += 2 * length_of(code)
            continue
        options = [code for code in LENGTH_WEIGHTS if length_of(code) <= remaining + 1e-6]
        code = rng.choices(options, [LENGTH_WEIGHTS[option] for option in options])[0]
        events.append((code, False))
        position += length_of(code)
    return events


def decorate(notes: list[ET.Element], triplet_groups: list[list[ET.Element]], slurs: bool, lyrics: bool, rng: random.Random) -> None:
    """Add marks after every note exists, to keep the order MusicXML requires."""
    for group in triplet_groups:
        child(notations_of(group[0]), "tuplet", type="start")
        child(notations_of(group[-1]), "tuplet", type="stop")
    for note in notes:
        roll = rng.random()
        if roll < 0.05:
            child(child(notations_of(note), "articulations"), rng.choice(["staccato", "accent", "tenuto"]))
        elif roll < 0.07:
            child(notations_of(note), "fermata")
    if slurs and len(notes) >= 2 and rng.random() < 0.5:
        first = rng.randrange(len(notes) - 1)
        last = rng.randrange(first + 1, len(notes))
        child(notations_of(notes[first]), "slur", type="start", number="1")
        child(notations_of(notes[last]), "slur", type="stop", number="1")
    if lyrics:
        for note in notes:
            if rng.random() < 0.6:
                child(child(note, "lyric"), "text", rng.choice(LYRICS))


def add_barline(measure: ET.Element, last_measure: bool, rng: random.Random) -> None:
    if last_measure:
        barline = child(measure, "barline", location="right")
        child(barline, "bar-style", "light-heavy")
        if rng.random() < 0.3:
            child(barline, "repeat", direction="backward")
    elif rng.random() < 0.05:
        barline = child(measure, "barline", location="right")
        child(barline, "bar-style", rng.choice(["light-light", "light-heavy"]))


def first_measure_attributes(measure: ET.Element, clef: str, fifths: int, beats: int, beat_type: int, rng: random.Random) -> None:
    attributes = child(measure, "attributes")
    child(attributes, "divisions", str(DIVISIONS))
    child(child(attributes, "key"), "fifths", str(fifths))
    time = child(attributes, "time")
    if rng.random() < 0.3 and (beats, beat_type) in ((4, 4), (2, 2)):
        time.set("symbol", "common" if beats == 4 else "cut")
    child(time, "beats", str(beats))
    child(time, "beat-type", str(beat_type))
    clef_element = child(attributes, "clef")
    child(clef_element, "sign", clef)
    child(clef_element, "line", "2" if clef == "G" else "4")


def sample_page(rng: random.Random) -> tuple[str, list[list[str]]]:
    """Return a MusicXML page, plus the tokens that should be read from each of its lines."""
    root = ET.Element("score-partwise", version="4.0")
    score_part = child(child(root, "part-list"), "score-part", id="P1")
    child(score_part, "part-name", "")
    part = child(root, "part", id="P1")

    clef = rng.choices(["G", "F"], [4, 1])[0]
    fifths = rng.choices(range(-6, 7), [1, 2, 3, 4, 5, 6, 9, 6, 5, 4, 3, 2, 1])[0]
    time = rng.choices(TIMES, TIME_WEIGHTS)[0]
    beats, beat_type = map(int, time.split("/"))
    total = beats * 4 / beat_type
    triplets = beat_type != 8 and rng.random() < 0.4
    has_chords = rng.random() < 0.6
    has_lyrics = rng.random() < 0.3
    has_slurs = rng.random() < 0.4
    position = rng.randint(0, 8)
    line_measures = [rng.randint(2, 5) for _ in range(rng.randint(2, 3))]
    pickup = rng.random() < 0.15
    line_tokens = []
    number = 0
    for line, measures in enumerate(line_measures):
        tokens = [f"clef:{clef}"]
        if fifths:
            tokens.append(f"key:{fifths}")
        if line == 0:
            tokens.append(f"time:{time}")
        for measure_index in range(measures):
            number += 1
            measure = child(part, "measure", number=str(number))
            if line and measure_index == 0:
                ET.SubElement(measure, "print", {"new-system": "yes"})
            is_pickup = pickup and number == 1
            if is_pickup:
                measure.set("implicit", "yes")
            if number == 1:
                first_measure_attributes(measure, clef, fifths, beats, beat_type, rng)
            state: dict[tuple[str, int], int] = {}
            onset = 0.0
            notes: list[ET.Element] = []
            groups: list[list[ET.Element]] = []
            events = sample_events(rng.choice([0.5, 1.0]) if is_pickup else total, rng, triplets)
            index = 0
            while index < len(events):
                code, triplet = events[index]
                if has_chords and onset in (0.0, float(total // 2)) and rng.random() < 0.7:
                    measure.append(harmony_element(parse_chord(sample_label(rng))))
                if triplet and (not groups or len(groups[-1]) == 3):
                    groups.append([])
                    tokens.append("triplet")
                onset += event_duration(code, triplet) / DIVISIONS
                index += 1
                if not triplet and rng.random() < 0.12:
                    add_rest(measure, code)
                    tokens.append(f"rest:{code}")
                    continue
                position = max(-5, min(13, position + rng.choice([-4, -3, -2, -2, -1, -1, -1, 0, 1, 1, 1, 2, 2, 3, 4])))
                step, octave = position_to_pitch(clef, position)
                current = state.get((step, octave), key_alteration(fifths, step))
                alter = current
                if rng.random() < 0.1:
                    alter = rng.choice([value for value in (-1, 0, 1) if value != current])
                shown = None
                if alter != current:
                    shown = next(name for name, value in ACCIDENTALS.items() if value == alter)
                    tokens.append(f"acc:{shown}")
                state[(step, octave)] = alter
                note = add_note(measure, clef, position, code, alter, shown, triplet)
                notes.append(note)
                if triplet:
                    groups[-1].append(note)
                tokens.append(f"note:{position}:{code}")
            decorate(notes, groups, has_slurs, has_lyrics, rng)
            add_barline(measure, number == sum(line_measures), rng)
            tokens.append("bar")
        line_tokens.append(tokens)
    add_beams(root)
    return drop_spelled_degrees(to_text(root)), line_tokens


def render_page(xml: str, font: str, rng: random.Random, toolkit: verovio.toolkit) -> np.ndarray | None:
    toolkit.setOptions({
        "font": font,
        "pageWidth": rng.randint(1500, 2200),
        "pageHeight": 2970,
        "pageMarginLeft": 60,
        "pageMarginRight": 60,
        "pageMarginTop": 60,
        "pageMarginBottom": 60,
        "scale": 45,
        "adjustPageHeight": True,
        "header": "none",
        "footer": "none",
        "breaks": "encoded",
        "spacingLinear": rng.uniform(0.2, 0.3),
        "spacingNonLinear": rng.uniform(0.5, 0.7),
    })
    if not toolkit.loadData(xml) or toolkit.getPageCount() != 1:
        return None
    with tempfile.NamedTemporaryFile(suffix=".svg", mode="w", encoding="utf-8") as svg:
        svg.write(replace_private_glyphs(toolkit.renderToSVG(1)))
        svg.flush()
        result = subprocess.run(["rsvg-convert", "-w", "2400", "-b", "white", svg.name], capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_GRAYSCALE)


def damage_strip(strip: np.ndarray, rng: random.Random) -> np.ndarray:
    """Add the damage a phone adds, gently enough that notes stay on their staff lines."""
    height, width = strip.shape
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), rng.uniform(-0.3, 0.3), rng.uniform(0.97, 1.03))
    matrix[1, 2] += rng.uniform(-3, 3)
    image = cv2.warpAffine(strip, matrix, (width, height), borderValue=255)
    if rng.random() < 0.3:
        image = warp_like_a_pen(image, PEN_SIZE, rng)
    paper, ink = rng.uniform(150, 250), rng.uniform(0, 90)
    pixels = ink + (paper - ink) * (image.astype(np.float32) / 255)
    ramp = np.linspace(rng.uniform(0.7, 1.0), rng.uniform(0.7, 1.0), width, dtype=np.float32)
    pixels = pixels * ramp[None, :]
    pixels = cv2.GaussianBlur(pixels, (0, 0), rng.uniform(0.3, 1.0))
    generator = np.random.default_rng(rng.getrandbits(32))
    pixels = np.clip(pixels + generator.normal(0, rng.uniform(0, 8), pixels.shape), 0, 255).astype(np.uint8)
    ok, encoded = cv2.imencode(".jpg", pixels, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(50, 95)])
    return cv2.imdecode(encoded, cv2.IMREAD_GRAYSCALE) if ok else pixels


def build_part(task: tuple[str, int, int, int]) -> list[tuple[str, str, str]]:
    """Render a run of pages and return (file, font, tokens) for each line."""
    split, start, stop, seed = task
    os.environ["OMP_NUM_THREADS"] = "1"
    cv2.setNumThreads(1)
    toolkit = verovio.toolkit()
    toolkit.setResourcePath(RESOURCE_PATH)
    folder = DATA_DIR / split
    rows = []
    for page_number in range(start, stop):
        rng = random.Random(f"{seed}-{split}-{page_number}")
        font = FONTS[split][page_number % len(FONTS[split])]
        xml, lines = sample_page(rng)
        page = render_page(xml, font, rng, toolkit)
        if page is None:
            continue
        staves = find_staves(page)
        if len(staves) != len(lines):
            continue  # The strips wouldn't match their labels, so skip the page.
        for line, (staff, tokens) in enumerate(zip(staves, lines)):
            strip, _, _ = crop_staff(page, staff)
            name = f"{page_number:06d}_{line}.png"
            cv2.imwrite(str(folder / name), strip)
            rows.append((name, font, " ".join(tokens)))
    return rows


def build(split: str, workers: int) -> None:
    folder = DATA_DIR / split
    folder.mkdir(parents=True, exist_ok=True)
    size = 200
    tasks = [(split, start, min(start + size, PAGES[split]), 11) for start in range(0, PAGES[split], size)]
    rows = []
    with multiprocessing.get_context("spawn").Pool(workers) as pool:
        for done, part in enumerate(pool.imap_unordered(build_part, tasks), start=1):
            rows.extend(part)
            if done % 10 == 0:
                print(f"{split}: {done}/{len(tasks)} parts, {len(rows)} strips", flush=True)
    rows.sort()
    with open(DATA_DIR / f"{split}.tsv", "w", newline="", encoding="utf-8") as file:
        csv.writer(file, delimiter="\t").writerows(rows)
    print(f"{split}: {len(rows)} strips from {PAGES[split]} pages", flush=True)


if __name__ == "__main__":
    for chosen in ("test", "val", "train"):
        build(chosen, workers=5)
