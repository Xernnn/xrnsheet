"""Read one sheet music image into MusicXML with chord symbols.

    .venv/bin/python -m pipeline.recognize photo.jpg --out results/photo

Notes come from homr, or from our own note reader with --notes own. Chords come from the
text detector and our chord reader, then get attached to notes by their position on the page.

This file imports homr, which is AGPL-3.0. Keep that import here, and inside run_homr, so a
run with --notes own never loads it. Check the licence before shipping this as a closed product.
"""

import argparse
import json
import shutil
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from chords.fonts import ROOT
from chords.model import load_reader, read_images
from chords.render import fit_height
from chords.vocab import parse_chord
from pipeline import align
from pipeline.chord_boxes import find_chords
from pipeline.musicxml import insert_harmony, load_root, read_measures, to_text
from pipeline.repair import repair

MODEL_PATH = ROOT / "models" / "chord_reader.pt"


@dataclass
class ReadChord:
    text: str
    chord: str
    confidence: float
    box: list[float]
    measure: int
    onset: float


def run_homr(image_path: Path, work_dir: Path) -> tuple[ET.Element, list[align.SystemLayout], np.ndarray]:
    """Run homr's steps directly, to keep the note and barline positions it computes."""
    from homr.main import ProcessingConfig, detect_staffs_in_image, download_weights
    from homr.music_xml_generator import XmlGeneratorArguments, generate_xml
    from homr.staff_parsing import parse_staffs
    from homr.transformer.configs import Config

    download_weights(False, False, False)
    working_copy = work_dir / image_path.name
    if working_copy != image_path:
        shutil.copy(image_path, working_copy)
    config = ProcessingConfig(False, False, False, False, -1, False, False, False)
    multi_staffs, image, debug, title_future = detect_staffs_in_image(str(working_copy), config)
    transformer_config = Config()
    transformer_config.use_gpu_inference = False
    voices = parse_staffs(debug, multi_staffs, image, transformer_config)
    xml = generate_xml(XmlGeneratorArguments(), voices, title_future.result(60))
    xml_path = work_dir / "homr.musicxml"
    xml.write(str(xml_path))
    debug.clean_debug_files_from_previous_runs()
    if working_copy != image_path:
        working_copy.unlink()
    for leftover in work_dir.glob("*_tesseract_input.png"):
        leftover.unlink()

    layouts = []
    for system in sorted(multi_staffs, key=lambda item: item.staffs[0].min_y):
        top_staff, bottom_staff = system.staffs[0], system.staffs[-1]
        unit = float(top_staff.average_unit_size)
        layouts.append(
            align.SystemLayout(
                left=float(top_staff.min_x),
                right=float(top_staff.max_x),
                top=float(np.median([min(point.y) for point in top_staff.grid])),
                bottom=float(np.median([max(point.y) for point in bottom_staff.grid])),
                unit=unit,
                bars=[float(bar.center[0]) for bar in top_staff.get_bar_lines()],  # Empty in homr 0.7.
                notes=[float(note.center[0]) for note in top_staff.get_notes()],
            )
        )
    return load_root(xml_path), layouts, image


@lru_cache(maxsize=1)
def ocr_engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def detect_text(image: np.ndarray) -> list[tuple[float, float, float, float]]:
    """Return level boxes as (left, top, right, bottom).

    The detector's tilted boxes aren't used, because a raised "7" looks like a rising
    baseline. Straightening to that tilt would turn a level chord sideways.
    """
    result = ocr_engine()(image, use_det=True, use_cls=False, use_rec=False)
    if result.boxes is None:
        return []
    return [(float(box[:, 0].min()), float(box[:, 1].min()), float(box[:, 0].max()), float(box[:, 1].max())) for box in np.array(result.boxes, dtype=np.float32)]


@lru_cache(maxsize=1)
def chord_model(device: str):
    return load_reader(MODEL_PATH, device)


def read_crops(crops: list[np.ndarray], reader: str, device: str) -> list[tuple[str, float]]:
    if not crops:
        return []
    if reader == "model":
        return read_images(chord_model(device), [fit_height(crop) for crop in crops], device)
    readings = []
    for crop in crops:
        result = ocr_engine()(cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR), use_det=False, use_cls=False, use_rec=True)
        text = "".join(result.txts) if result.txts else ""
        readings.append((text, float(result.scores[0]) if result.scores else 0.0))
    return readings


def read_chords_on_page(
    image: np.ndarray, layouts: list[align.SystemLayout], systems: list[list], reader: str, device: str
) -> list[ReadChord]:
    page = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    candidates = find_chords(page, detect_text(image), layouts, lambda crops: read_crops(crops, reader, device))
    offsets = np.cumsum([0, *[len(system) for system in systems]])
    found = []
    for candidate in candidates:
        if candidate.system >= len(systems):
            continue
        anchor = candidate.edges[0] + layouts[candidate.system].unit / 2
        index, onset = align.place_chord(anchor, layouts[candidate.system], systems[candidate.system])
        found.append(
            ReadChord(
                candidate.text,
                str(parse_chord(candidate.text)),
                round(candidate.confidence, 3),
                [round(value, 1) for value in candidate.edges],
                int(offsets[candidate.system] + index),
                onset,
            )
        )
    return sorted(found, key=lambda item: (item.measure, item.onset))


def run_own_reader(image_path: Path, device: str) -> tuple[ET.Element, list[align.SystemLayout], np.ndarray]:
    from notes.page import read_page

    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not open {image_path}")
    return read_page(image, device)


def recognize(image_path: Path, out_dir: Path, reader: str = "model", device: str = "cpu", notes: str = "homr") -> tuple[Path, list[ReadChord]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    root, layouts, image = run_homr(image_path, out_dir) if notes == "homr" else run_own_reader(image_path, device)
    part = root.find("part")
    measures = read_measures(part)
    systems = align.split_into_systems(measures)
    if len(systems) != len(layouts):
        print(f"warning: {len(systems)} lines in the score, {len(layouts)} on the page", file=sys.stderr)

    chords = read_chords_on_page(image, layouts, systems, reader, device)
    divisions = int(root.findtext(".//divisions") or 1)
    seen = set()
    for item in chords:
        if (item.measure, item.onset) in seen:
            continue
        seen.add((item.measure, item.onset))
        current = divisions
        for measure in part.findall("measure")[: item.measure + 1]:
            current = int(measure.findtext(".//attributes/divisions") or current)
        insert_harmony(measures[item.measure], parse_chord(item.chord), item.onset, current)

    # homr takes the text above the first staff as a title, and on a chart that's chords.
    title = root.find("work/work-title")
    if title is not None and any(parse_chord(word) for word in (title.text or "").split()):
        title.text = ""
    for name in root.iter("part-name"):
        name.text = ""  # homr always guesses "Voice" or "Piano", which shouldn't be printed.

    repair(root)
    out_path = out_dir / f"{image_path.stem}.musicxml"
    out_path.write_text(to_text(root), encoding="utf-8")
    (out_dir / "homr.musicxml").unlink(missing_ok=True)
    (out_dir / "chords.json").write_text(json.dumps([asdict(item) for item in chords], ensure_ascii=False, indent=1), encoding="utf-8")
    cv2.imwrite(str(out_dir / "page.jpg"), image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    return out_path, chords


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("image", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--reader", choices=["model", "rapidocr"], default="model")
    parser.add_argument("--notes", choices=["homr", "own"], default="homr")
    arguments = parser.parse_args()
    out_path, chords = recognize(arguments.image, arguments.out, arguments.reader, notes=arguments.notes)
    print(out_path)
    print(" ".join(item.chord for item in chords))


if __name__ == "__main__":
    main()
