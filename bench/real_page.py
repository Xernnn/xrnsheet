"""Test the chord reader on one real handwritten page and a strip from a second writer.

Both come from arXiv 2509.05329, so they stay out of the repo and no model trained on them may
ship. `real_page_labels.tsv` holds only chord names and box positions, typed by hand. Fetch the
images into a new empty folder, and use -I for anything reading them:

    mkdir FOLDER && cd FOLDER
    curl -o page.jpg https://arxiv.org/html/2509.05329v1/figs/Prelude_to_a_Kiss_paper.JPG
    curl -o strip.png https://arxiv.org/html/2509.05329v1/pred_comparison_2.png

    .venv/bin/python -I bench/real_page.py FOLDER                    # read the hand-cut boxes
    .venv/bin/python -I bench/real_page.py FOLDER --pipeline         # boxes found by the app, needs homr
    .venv/bin/python -I bench/real_page.py FOLDER --folds 10 20 42   # 5-fold retraining, on the GPU

The page shaped earlier fixes, so it's a development page. Its scores can't be quoted as a clean
accuracy. Folds never train on the chord being tested, and no fold model is saved.
"""

import argparse
import csv
import json
import random
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from score import matched  # noqa: E402

from chords import retrain  # noqa: E402
from chords.metrics import mcnemar_exact, wilson_interval  # noqa: E402
from chords.model import CHARACTER_INDEX, load_reader  # noqa: E402
from chords.vocab import parse_chord  # noqa: E402

LABELS = ROOT / "bench" / "real_page_labels.tsv"
PAGE_SCALE = 2130 / 1800  # The boxes were drawn on a view 1800 pixels wide.
STRIP_SCALE = 2032 / 1924
FOLDS = 5
STEPS = 500
SPLIT_SEED = 7

Crop = tuple[np.ndarray, str]


def read_labels(source: str) -> list[tuple[str, int, int, int, int]]:
    with open(LABELS, newline="", encoding="utf-8") as file:
        rows = [row for row in csv.DictReader(file, delimiter="\t") if row["source"] == source]
    return [(row["label"], int(row["left"]), int(row["top"]), int(row["right"]), int(row["bottom"])) for row in rows]


def cut(image: np.ndarray, box: tuple[int, int, int, int], scale: float) -> np.ndarray:
    left, top, right, bottom = (int(value * scale) for value in box)
    return image[top:bottom, left:right]


def load_page(folder: Path) -> list[Crop]:
    image = cv2.cvtColor(cv2.imread(str(folder / "page.jpg")), cv2.COLOR_BGR2GRAY)
    crops = [(cut(image, box, PAGE_SCALE), label) for label, *box in read_labels("page")]
    assert all(letter in CHARACTER_INDEX for _, label in crops for letter in label), "a label is outside the alphabet"
    return crops


def load_strip(folder: Path) -> list[Crop]:
    """Return the second writer's chords, which the paper's figure prints on their side."""
    image = cv2.imread(str(folder / "strip.png"))
    turned = [cv2.rotate(cut(image, box, STRIP_SCALE), cv2.ROTATE_90_COUNTERCLOCKWISE) for _, *box in read_labels("strip")]
    return [(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), label) for crop, (label, *_) in zip(turned, read_labels("strip"))]


def score_boxes(page: list[Crop], strip: list[Crop], device: str) -> None:
    model = load_reader(retrain.MODEL_PATH, device)
    print(f"page {sum(retrain.score_crops(model, page, device))}/{len(page)}")
    print(f"second writer {sum(retrain.score_crops(model, strip, device))}/{len(strip)}")


def score_pipeline(folder: Path, page: list[Crop], device: str) -> None:
    """Score the boxes the app finds for itself, which is what a user would get."""
    from pipeline import recognize
    from pipeline.chord_boxes import find_chords

    with tempfile.TemporaryDirectory() as temporary:
        _, layouts, image = recognize.run_homr(folder / "page.jpg", Path(temporary))
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    found = find_chords(gray, recognize.detect_text(image), layouts, lambda crops: recognize.read_crops(crops, "model", device))
    found.sort(key=lambda item: (item.system, item.edges[0]))
    predicted = [str(parse_chord(item.text)) for item in found]
    truth = [str(parse_chord(label)) for _, label in page]
    hits = matched(truth, predicted)
    print(f"pipeline {hits}/{len(truth)}, {len(predicted) - hits} false chords")


def cross_validate(page: list[Crop], strip: list[Crop], sizes: list[int], steps: int, device: str) -> dict:
    """Test every chord once with models that never trained on it, at each amount of real data."""
    base = load_reader(retrain.MODEL_PATH, device)
    base_page = retrain.score_crops(base, page, device)
    base_strip = retrain.score_crops(base, strip, device)
    print(f"base {sum(base_page)}/{len(page)}, second writer {sum(base_strip)}/{len(strip)}", flush=True)
    order = list(range(len(page)))
    random.Random(SPLIT_SEED).shuffle(order)
    folds = [order[number::FOLDS] for number in range(FOLDS)]
    results: dict = {}
    started = time.time()
    for size in sizes:
        predictions: dict[int, bool] = {}
        strip_right = []
        for number, fold in enumerate(folds):
            chosen = [index for index in order if index not in fold][:size]
            model = retrain.fine_tune(retrain.MODEL_PATH, [page[index] for index in chosen], steps, device, seed=number, workers=3)
            predictions.update(zip(fold, retrain.score_crops(model, [page[index] for index in fold], device)))
            strip_right.append(sum(retrain.score_crops(model, strip, device)))
            del model
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            print(f"size {size} fold {number}, second writer {strip_right[-1]}/{len(strip)}, {(time.time() - started) / 60:.1f} min", flush=True)
        new = [predictions[index] for index in range(len(page))]
        gained = sum(after and not before for before, after in zip(base_page, new))
        lost = sum(before and not after for before, after in zip(base_page, new))
        low, high = wilson_interval(sum(new), len(new))
        results[str(size)] = {
            "right": sum(new), "total": len(new), "low": low, "high": high, "gained": gained, "lost": lost,
            "p": mcnemar_exact(gained, lost), "second_writer_mean": sum(strip_right) / len(strip_right),
        }
        print(size, results[str(size)], flush=True)
    results["base"] = {"right": sum(base_page), "total": len(page), "second_writer": sum(base_strip)}
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="the folder holding page.jpg and strip.png")
    parser.add_argument("--pipeline", action="store_true", help="score the boxes the app finds")
    parser.add_argument("--folds", type=int, nargs="*", help="amounts of real data to cross-validate")
    parser.add_argument("--steps", type=int, default=STEPS)
    arguments = parser.parse_args()
    device = "cuda" if torch.cuda.is_available() else "cpu"
    page, strip = load_page(arguments.folder), load_strip(arguments.folder)
    score_boxes(page, strip, device)
    if arguments.pipeline:
        score_pipeline(arguments.folder, page, device)
    if arguments.folds:
        results = cross_validate(page, strip, arguments.folds, arguments.steps, device)
        target = ROOT / "results" / "chords" / f"finetune_real_{arguments.steps}.json"
        target.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"saved {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
