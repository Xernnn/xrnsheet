"""Score the note reader on music fonts it never trained on.

    .venv/bin/python -m notes.evaluate
    .venv/bin/python -m notes.evaluate --name smoke

Results are split by font and damage, because an average would hide a font that fails.
"""

import argparse
import json
import random
from collections import Counter, defaultdict

import cv2
import torch

from chords.fonts import ROOT
from notes.model import load_reader, read_strips
from notes.render import DATA_DIR, damage_strip
from notes.train import RESULT_DIR, load_rows, token_error


def token_kind(token: str) -> str:
    return token.partition(":")[0]


def confusion_counts(predicted: list[list[str]], truth: list[list[str]]) -> Counter:
    """Count which kinds of tokens are lost, using strips with the right number of tokens."""
    counts: Counter = Counter()
    for read, answer in zip(predicted, truth):
        if len(read) != len(answer):
            counts["length_mismatch"] += 1
            continue
        for mine, right in zip(read, answer):
            counts[f"{token_kind(right)}_total"] += 1
            if mine == right:
                continue
            counts[f"{token_kind(right)}_wrong"] += 1
            if token_kind(mine) == token_kind(right) == "note":
                _, position, length = mine.split(":")
                _, right_position, right_length = right.split(":")
                if position != right_position:
                    counts["note_wrong_pitch"] += 1
                if length != right_length:
                    counts["note_wrong_length"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="note_reader")
    arguments = parser.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_reader(ROOT / "models" / f"{arguments.name}.pt", device)
    rng = random.Random(4321)
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for name, font, tokens in load_rows("test"):
        strip = cv2.imread(str(DATA_DIR / "test" / name), cv2.IMREAD_GRAYSCALE)
        groups[(font, "clean")].append((strip, tokens))
        groups[(font, "damaged")].append((damage_strip(strip, rng), tokens))

    report = {}
    for (font, condition), items in sorted(groups.items()):
        readings = read_strips(model, [strip for strip, _ in items], device, batch_size=16)
        predicted = [reading.tokens for reading in readings]
        truth = [tokens for _, tokens in items]
        counts = confusion_counts(predicted, truth)
        report[f"{font} {condition}"] = {
            "strips": len(items),
            "token_error": token_error(predicted, truth),
            "exact_strips": sum(read == right for read, right in zip(predicted, truth)) / len(items),
            "counts": dict(counts),
        }
        print(f"{font:<9} {condition:<8} {len(items):>5} strips  token error {report[f'{font} {condition}']['token_error']:.4f}  exact strips {report[f'{font} {condition}']['exact_strips']:.3f}", flush=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / f"{arguments.name}.json").write_text(json.dumps(report, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
