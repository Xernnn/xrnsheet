"""Build the fixed chord test set from fonts the model never trains on.

    .venv/bin/python -m chords.testset
"""

import csv
import random
from pathlib import Path

import cv2

from chords.fonts import ROOT, split_fonts
from chords.render import make_sample

TEST_DIR = ROOT / "data" / "chords_test"
SAMPLES_PER_CONDITION = 600


def build() -> None:
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    for font in split_fonts("test"):
        for damage in (False, True):
            rng = random.Random(f"{font.file}-{damage}")
            for index in range(SAMPLES_PER_CONDITION):
                image, label = make_sample(font.path, rng, damage)
                name = f"{Path(font.file).stem}_{'damaged' if damage else 'clean'}_{index:04d}.png"
                cv2.imwrite(str(TEST_DIR / name), image)
                rows.append({"image": name, "font": font.file, "style": font.style, "damage": int(damage), "label": label})
    with open(TEST_DIR / "labels.tsv", "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} images in {TEST_DIR}")


def load() -> list[dict]:
    with open(TEST_DIR / "labels.tsv", newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file, delimiter="\t", quoting=csv.QUOTE_NONE))
    for row in rows:
        row["damage"] = bool(int(row["damage"]))
    return rows


if __name__ == "__main__":
    build()
