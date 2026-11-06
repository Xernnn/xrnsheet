"""Turn the sheets you've checked into labelled chord images.

    .venv/bin/python -m chords.corrections

This rebuilds data/corrections/ from data/jobs/, so run it whenever you've checked more sheets.

A sheet is only used once you've pressed "Mark as checked", because an unchanged chord only
counts as right if someone looked at it. About one sheet in five is kept back as a test set.
A hash picks those sheets, not a person, so hard sheets can't be steered in or out of the test.
"""

import csv
import hashlib
import json
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import cv2

from chords.fonts import ROOT
from chords.model import CHARACTER_INDEX
from pipeline import edit
from pipeline.chord_boxes import crop

JOB_DIR = ROOT / "data" / "jobs"
OUT_DIR = ROOT / "data" / "corrections"
LABEL_FILE = "labels.tsv"
TEST_SHARE = 5  # One sheet in this many is held out.
SAME_PIECE = 0.8  # Two sheets whose chords match this closely are the same piece.
SHORTEST_COMPARED = 8


def checked_sheets(job_dir: Path) -> list[Path]:
    return sorted(path.parent for path in job_dir.glob("*/checked.json"))


def versions_of(folder: Path) -> tuple[Path, Path]:
    """Return the file as recognized, and the file as you left it."""
    saved = sorted(folder.glob("version_*.musicxml"))
    return saved[0], saved[-1]


def label_chords(recognized: list[dict], final: list[dict]) -> list[dict]:
    """Label each chord the app placed, using what the person left in the score.

    A chord that was deleted is usually a false one, which makes a useful "not a chord"
    example. But if a new chord was added nearby, it may be a real chord that was only on the
    wrong beat, so it's skipped rather than risk teaching the model something false. Placement
    can miss by a whole measure, so "nearby" includes the measures on either side.
    """
    placed: dict[tuple[int, float], dict] = {}
    for item in recognized:
        placed.setdefault((item["measure"], float(item["onset"])), item)  # Only the first was placed.
    kept = {(chord["measure"], chord["onset"]): chord["text"] for chord in final}
    rows = []
    for key, item in placed.items():
        text = kept.get(key)
        if text is not None:
            confirmed = text == item["chord"]
            # The model's own text keeps the page's spelling, so C-7 isn't taught as Cm7.
            rows.append(item | {"label": item["text"] if confirmed else text, "outcome": "confirmed" if confirmed else "corrected"})
            continue
        added = [value for other, value in kept.items() if other not in placed and abs(other[0] - key[0]) <= 1]
        if item["chord"] in added:
            rows.append(item | {"label": item["text"], "outcome": "moved"})
        elif not added:
            rows.append(item | {"label": "", "outcome": "removed"})
    return rows


def count_added(recognized: list[dict], final: list[dict]) -> int:
    """Count chords the app missed entirely, which have no image to learn from."""
    placed = {(item["measure"], float(item["onset"])) for item in recognized}
    return sum((chord["measure"], chord["onset"]) not in placed for chord in final)


def is_test_key(key: str) -> bool:
    return int(key[:8], 16) % TEST_SHARE == 0


def group_key(digests: dict[str, str], chords: dict[str, list[str]]) -> dict[str, str]:
    """Give each sheet a key, shared by sheets that look like the same piece.

    Two photos of one page have different hashes. If one reached training and the other the
    test set, the model would be tested on music it had already seen, so they're tied together.
    """
    keys = dict(digests)
    names = sorted(digests)
    for index, first in enumerate(names):
        for second in names[index + 1 :]:
            a, b = chords[first], chords[second]
            if min(len(a), len(b)) < SHORTEST_COMPARED or SequenceMatcher(a=a, b=b, autojunk=False).ratio() < SAME_PIECE:
                continue
            old, new = sorted((keys[first], keys[second]), reverse=True)
            keys = {name: new if key == old else key for name, key in keys.items()}
    return keys


def sheet_digest(folder: Path) -> str:
    upload = next(folder.glob("upload.*"))
    return hashlib.sha256(upload.read_bytes()).hexdigest()


def export(job_dir: Path = JOB_DIR, out_dir: Path = OUT_DIR) -> Counter:
    """Rebuild the corrections folder, and return counts of what went into it."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    sheets = checked_sheets(job_dir)
    finals = {folder.name: edit.list_chords(ET.parse(versions_of(folder)[1]).getroot()) for folder in sheets}
    digests = {folder.name: sheet_digest(folder) for folder in sheets}
    keys = group_key(digests, {name: [chord["text"] for chord in chords] for name, chords in finals.items()})

    summary: Counter = Counter()
    rows = []
    for folder in sheets:
        recognized = json.loads((folder / "recognized" / "chords.json").read_text(encoding="utf-8"))
        split = "test" if is_test_key(keys[folder.name]) else "train"
        summary[f"{split}_sheets"] += 1
        labelled = label_chords(recognized, finals[folder.name])
        moved = sum(row["outcome"] == "moved" for row in labelled)
        summary["added"] += count_added(recognized, finals[folder.name]) - moved
        for number, row in enumerate(labelled):
            if any(character not in CHARACTER_INDEX for character in row["label"]):
                summary["unreadable_labels"] += 1
                continue
            page = cv2.imread(str(folder / "recognized" / f"page_{row['page']:03d}" / "page.jpg"), cv2.IMREAD_GRAYSCALE)
            image = f"{split}/{folder.name[:8]}_{number:04d}.png"
            (out_dir / split).mkdir(exist_ok=True)
            cv2.imwrite(str(out_dir / image), crop(page, row["box"]))
            rows.append([image, folder.name, split, row["label"], row["text"], f"{row['confidence']:.4f}", row["outcome"]])
            summary[row["outcome"]] += 1
            summary[f"{split}_chords"] += 1

    with open(out_dir / LABEL_FILE, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(["image", "sheet", "split", "label", "read_as", "confidence", "outcome"])
        writer.writerows(rows)
    return summary


def load_labels(out_dir: Path = OUT_DIR) -> list[dict]:
    path = out_dir / LABEL_FILE
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file, delimiter="\t"))


if __name__ == "__main__":
    counts = export()
    if not counts:
        print("No checked sheets yet. Press \"Mark as checked\" in the app once a sheet is fixed.")
    else:
        print(f"Sheets: {counts['train_sheets']} for training, {counts['test_sheets']} held out for testing")
        print(f"Chords: {counts['train_chords']} for training, {counts['test_chords']} held out for testing")
        print(f"Confirmed {counts['confirmed']}, corrected {counts['corrected']}, moved {counts['moved']}, removed {counts['removed']}")
        print(f"Missed by the app, so no image to learn from: {counts['added']}")
