"""Score the trained reader on the held-out test fonts, the same way as the baselines.

    .venv/bin/python -m chords.evaluate
    .venv/bin/python -m chords.evaluate --name no_damage
"""

import argparse
import json
import random
from collections import Counter

import cv2
import torch

from chords.baselines import RESULT_DIR, save
from chords.fonts import ROOT, split_fonts
from chords.metrics import is_correct
from chords.model import load_reader, read_images
from chords.render import make_sample
from chords.testset import TEST_DIR, load
from chords.vocab import parse_chord


def false_chords(model, device: str, count: int = 1500) -> float:
    """How often text that isn't a chord gets read as one."""
    rng = random.Random(99)
    fonts = split_fonts("test")
    samples = [make_sample(fonts[i % len(fonts)].path, rng, damage=i % 2 == 0, negatives=1.0)[0] for i in range(count)]
    return sum(parse_chord(text) is not None for text, _ in read_images(model, samples, device)) / count


def confidence_table(rows: list[dict]) -> list[dict]:
    """Show what's gained by only trusting confident readings, as the review screen does."""
    table = []
    for threshold in (0.0, 0.5, 0.7, 0.8, 0.9, 0.95):
        kept = [row for row in rows if row["confidence"] >= threshold]
        accuracy = sum(is_correct(row["predicted"], row["label"]) for row in kept) / max(1, len(kept))
        table.append({"threshold": threshold, "kept": len(kept) / len(rows), "accuracy": accuracy})
    return table


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="chord_reader")
    arguments = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_reader(ROOT / "models" / f"{arguments.name}.pt", device)
    rows = load()
    images = [cv2.imread(str(TEST_DIR / row["image"]), cv2.IMREAD_GRAYSCALE) for row in rows]
    readings = read_images(model, images, device, batch_size=48)
    save(arguments.name, rows, [text for text, _ in readings])

    scored = [row | {"predicted": text, "confidence": confidence} for row, (text, confidence) in zip(rows, readings)]
    extra = {
        "false_chord_rate": false_chords(model, device),
        "confidence": confidence_table(scored),
        "confusions": Counter(
            f"{row['label']} -> {row['predicted']}" for row in scored if not is_correct(row["predicted"], row["label"])
        ).most_common(40),
    }
    (RESULT_DIR / f"{arguments.name}_extra.json").write_text(json.dumps(extra, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nnon-chord text read as a chord: {extra['false_chord_rate']:.3f}")
    print("trust threshold  share kept  accuracy")
    for line in extra["confidence"]:
        print(f"{line['threshold']:>15.2f}{line['kept']:>12.3f}{line['accuracy']:>10.3f}")


if __name__ == "__main__":
    main()
