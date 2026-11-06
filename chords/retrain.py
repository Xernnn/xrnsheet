"""Fine-tune the chord reader on your corrected chords, and keep it only if it wins.

    .venv/bin/python -m chords.corrections      gather your corrections first
    .venv/bin/python -m chords.retrain          train a candidate and report on it
    .venv/bin/python -m chords.retrain --promote    also replace the live model if it passes

The candidate must beat the current model on held-out real sheets it never trained on, by
more than chance could explain, and must not get worse on the frozen synthetic test.
"""

import argparse
import random
import shutil
import time
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader

from chords.corrections import OUT_DIR, load_labels
from chords.fonts import ROOT
from chords.metrics import is_correct, mcnemar_exact, wilson_interval
from chords.model import CHARACTER_INDEX, ChordReader, load_reader, read_images, to_batch
from chords.render import add_camera_damage, fit_height
from chords.testset import TEST_DIR, load
from chords.train import SyntheticChords, collate
from chords.vocab import parse_chord

MODEL_PATH = ROOT / "models" / "chord_reader.pt"
CANDIDATE_PATH = ROOT / "models" / "chord_reader_candidate.pt"
MIN_TRAIN_CHORDS = 30
MIN_TEST_CHORDS = 30
SYNTHETIC_FLOOR = 0.99
SIGNIFICANCE = 0.05
BATCH_SIZE = 60
REAL_PER_BATCH = 20


def same_answer(predicted: str, label: str) -> bool:
    """Score a reading. An empty label means it isn't a chord, so reading none is right."""
    if label == "":
        return parse_chord(predicted) is None
    return is_correct(predicted, label)


def vary(crop: np.ndarray, rng: random.Random) -> np.ndarray:
    """Make a real crop look a little different each time, so it can't just be memorized."""
    image = fit_height(crop)
    return add_camera_damage(image, rng) if rng.random() < 0.5 else image


def real_batch(real: list[tuple[np.ndarray, str]], rng: random.Random):
    chosen = rng.choices(real, k=REAL_PER_BATCH)
    images = [vary(crop, rng) for crop, _ in chosen]
    labels = [label for _, label in chosen]
    return images, labels


def join_batches(fake: tuple, images: list[np.ndarray], labels: list[str]) -> tuple:
    """Add real images to a synthetic batch, which keeps the model's general skill alive."""
    pixels, widths, targets, target_lengths = fake
    real_pixels, real_widths = to_batch(images)
    longest = max(pixels.shape[3], real_pixels.shape[3])
    pad = lambda batch: nn.functional.pad(batch, (0, longest - batch.shape[3]), value=1.0)
    real_targets = torch.tensor([CHARACTER_INDEX[character] for label in labels for character in label], dtype=torch.long)
    return (
        torch.cat([pad(pixels), pad(real_pixels)]),
        torch.cat([widths, real_widths]),
        torch.cat([targets, real_targets]),
        torch.cat([target_lengths, torch.tensor([len(label) for label in labels])]),
    )


def fine_tune(start: Path, real: list[tuple[np.ndarray, str]], steps: int, device: str, seed: int = 0, learning_rate: float = 1e-4, workers: int = 4) -> ChordReader:
    """Continue training from an existing model on a mix of real and synthetic images.

    The learning rate is a tenth of the original, so the model adjusts instead of relearning.
    """
    torch.manual_seed(seed)
    rng = random.Random(seed)
    model = ChordReader()
    model.load_state_dict(torch.load(start, map_location=device, weights_only=True)["model"])
    model.to(device).train()
    loader = DataLoader(SyntheticChords(0.75, seed, 0.35, 0.2), batch_size=BATCH_SIZE, num_workers=workers, collate_fn=collate)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, learning_rate, total_steps=steps)
    scaler = torch.amp.GradScaler(device, enabled=device == "cuda")
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    for step, fake in enumerate(loader, start=1):
        pixels, widths, targets, target_lengths = join_batches(fake, *real_batch(real, rng))
        with torch.autocast(device, enabled=device == "cuda"):
            log_probabilities, lengths = model(pixels.to(device), widths.to(device))
        with torch.autocast(device, enabled=False):
            loss = ctc(log_probabilities.float().permute(1, 0, 2), targets.to(device), lengths, target_lengths)
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.unscale_(optimizer)
        nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        scaler.step(optimizer)
        scaler.update()
        schedule.step()
        if step >= steps:
            break
    return model.eval()


def score_crops(model: nn.Module, crops: list[tuple[np.ndarray, str]], device: str) -> list[bool]:
    readings = read_images(model, [fit_height(crop) for crop, _ in crops], device, batch_size=48)
    return [same_answer(text, label) for (text, _), (_, label) in zip(readings, crops)]


def synthetic_score(model: nn.Module, device: str) -> float:
    rows = load()
    images = [cv2.imread(str(TEST_DIR / row["image"]), cv2.IMREAD_GRAYSCALE) for row in rows]
    readings = read_images(model, images, device, batch_size=48)
    return sum(is_correct(text, row["label"]) for (text, _), row in zip(readings, rows)) / len(rows)


def decide(old: list[bool], new: list[bool], synthetic: float) -> tuple[bool, str]:
    """Apply the promotion rules. Every rule here was fixed before any real data existed."""
    gained = sum(after and not before for before, after in zip(old, new))
    lost = sum(before and not after for before, after in zip(old, new))
    p_value = mcnemar_exact(gained, lost)
    if len(old) < MIN_TEST_CHORDS:
        return False, f"Only {len(old)} held-out chords, and at least {MIN_TEST_CHORDS} are needed to tell the models apart."
    if synthetic < SYNTHETIC_FLOOR:
        return False, f"It fell to {synthetic:.3f} on the synthetic test, below the {SYNTHETIC_FLOOR} floor."
    if gained <= lost:
        return False, f"It gained {gained} chords and lost {lost}, so it isn't better."
    if p_value >= SIGNIFICANCE:
        return False, f"It gained {gained} and lost {lost}, but p = {p_value:.3f}, so that could be chance."
    return True, f"It gained {gained} chords and lost {lost} (p = {p_value:.3f})."


def load_crops(rows: list[dict]) -> list[tuple[np.ndarray, str]]:
    return [(cv2.imread(str(OUT_DIR / row["image"]), cv2.IMREAD_GRAYSCALE), row["label"]) for row in rows]


def describe(name: str, results: list[bool]) -> str:
    right = sum(results)
    low, high = wilson_interval(right, len(results))
    return f"{name}: {right}/{len(results)} ({right / len(results):.1%}, 95% interval {low:.1%} to {high:.1%})"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=1500)
    parser.add_argument("--promote", action="store_true", help="replace the live model if the candidate passes")
    arguments = parser.parse_args()

    labels = load_labels()
    train = load_crops([row for row in labels if row["split"] == "train"])
    test = load_crops([row for row in labels if row["split"] == "test"])
    if len(train) < MIN_TRAIN_CHORDS:
        raise SystemExit(f"Only {len(train)} corrected chords to train on, and at least {MIN_TRAIN_CHORDS} are needed. Check more sheets, then run chords.corrections.")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    started = time.monotonic()
    candidate = fine_tune(MODEL_PATH, train, arguments.steps, device)
    torch.save({"model": candidate.state_dict(), "steps": arguments.steps}, CANDIDATE_PATH)
    print(f"Trained on {len(train)} real chords in {(time.monotonic() - started) / 60:.1f} minutes")

    current = load_reader(MODEL_PATH, device)
    old_scores = score_crops(current, test, device) if test else []
    new_scores = score_crops(candidate, test, device) if test else []
    synthetic = synthetic_score(candidate, device)
    if test:
        print(describe("Current model on held-out sheets", old_scores))
        print(describe("Candidate on held-out sheets", new_scores))
    print(f"Candidate on the synthetic test: {synthetic:.3f}")

    passed, reason = decide(old_scores, new_scores, synthetic)
    print(f"{'Passed' if passed else 'Not promoted'}: {reason}")
    if passed and arguments.promote:
        backup = MODEL_PATH.with_name(f"chord_reader_before_{time.strftime('%Y%m%d_%H%M%S')}.pt")
        shutil.copy(MODEL_PATH, backup)
        shutil.copy(CANDIDATE_PATH, MODEL_PATH)
        print(f"Promoted. The old model is saved as {backup.name}")
    elif passed:
        print("Run again with --promote to use it, or copy models/chord_reader_candidate.pt over models/chord_reader.pt.")


if __name__ == "__main__":
    main()
