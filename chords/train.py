"""Train the chord reader on images generated on the fly.

    .venv/bin/python -m chords.train
    .venv/bin/python -m chords.train --steps 300 --name smoke   quick check
    .venv/bin/python -m chords.train --no-damage --name no_damage   see what augmentation adds
"""

import argparse
import csv
import random
import time

import numpy as np
import torch
from torch import nn
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

from chords.fonts import ROOT, split_fonts
from chords.metrics import is_correct
from chords.model import CHARACTER_INDEX, ChordReader, read_images, to_batch
from chords.render import make_sample

MODEL_DIR = ROOT / "models"
RESULT_DIR = ROOT / "results" / "chords"
VALIDATION_SIZE = 1200


class SyntheticChords(IterableDataset):
    """An endless stream of fresh images, so the model never sees the same one twice."""

    def __init__(self, damage: float, seed: int, hand_layout: float = 0.0, negatives: float = 0.12) -> None:
        self.fonts = [font.path for font in split_fonts("train")]
        self.damage = damage
        self.seed = seed
        self.hand_layout = hand_layout
        self.negatives = negatives

    def __iter__(self):
        worker = get_worker_info()
        rng = random.Random(self.seed + (worker.id if worker else 0))
        while True:
            damaged = rng.random() < self.damage
            yield make_sample(rng.choice(self.fonts), rng, damaged, negatives=self.negatives, common_weight=0.5, hand_layout=self.hand_layout)


def collate(samples: list[tuple[np.ndarray, str]]):
    images, labels = zip(*samples)
    pixels, widths = to_batch(list(images))
    targets = torch.tensor([CHARACTER_INDEX[c] for label in labels for c in label], dtype=torch.long)
    target_lengths = torch.tensor([len(label) for label in labels])
    return pixels, widths, targets, target_lengths


def validation_set() -> list[tuple[np.ndarray, str]]:
    rng = random.Random(1234)
    fonts = split_fonts("val")
    return [
        make_sample(fonts[index % len(fonts)].path, rng, damage=index % 2 == 0)
        for index in range(VALIDATION_SIZE)
    ]


def validate(model: nn.Module, samples: list, device: str) -> float:
    model.eval()
    predictions = read_images(model, [image for image, _ in samples], device, batch_size=48)
    model.train()
    torch.cuda.empty_cache()
    return sum(is_correct(text, label) for (text, _), (_, label) in zip(predictions, samples)) / len(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=24000)
    parser.add_argument("--batch-size", type=int, default=80)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--name", default="chord_reader")
    parser.add_argument("--no-damage", action="store_true", help="train on clean images only")
    parser.add_argument("--hand-layout", type=float, default=0.0, help="share of chords laid out like handwriting")
    parser.add_argument("--negatives", type=float, default=0.12, help="share of images that aren't chords")
    arguments = parser.parse_args()

    torch.manual_seed(arguments.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    MODEL_DIR.mkdir(exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    model = ChordReader().to(device)
    print(f"{sum(p.numel() for p in model.parameters()):,} parameters on {device}")
    damage = 0.0 if arguments.no_damage else 0.75
    loader = DataLoader(
        SyntheticChords(damage, arguments.seed, arguments.hand_layout, arguments.negatives),
        batch_size=arguments.batch_size,
        num_workers=arguments.workers,
        collate_fn=collate,
        persistent_workers=True,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=arguments.learning_rate, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, arguments.learning_rate, total_steps=arguments.steps)
    scaler = torch.amp.GradScaler(device, enabled=device == "cuda")
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    validation = validation_set()

    best = 0.0
    started = time.monotonic()
    running_loss = 0.0
    log_path = RESULT_DIR / f"{arguments.name}_log.csv"
    with open(log_path, "w", newline="", encoding="utf-8") as log_file:
        log = csv.writer(log_file)
        log.writerow(["step", "train_loss", "val_accuracy", "minutes"])
        for step, (pixels, widths, targets, target_lengths) in enumerate(loader, start=1):
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
            running_loss += loss.item()

            if step % 500 == 0 or step == arguments.steps:
                accuracy = validate(model, validation, device)
                minutes = (time.monotonic() - started) / 60
                average_loss = running_loss / (500 if step % 500 == 0 else step % 500)
                running_loss = 0.0
                log.writerow([step, f"{average_loss:.4f}", f"{accuracy:.4f}", f"{minutes:.1f}"])
                log_file.flush()
                print(f"step {step:>6}  loss {average_loss:.3f}  val accuracy {accuracy:.3f}  {minutes:.1f} min", flush=True)
                if accuracy >= best:
                    best = accuracy
                    torch.save({"model": model.state_dict(), "step": step, "val_accuracy": accuracy}, MODEL_DIR / f"{arguments.name}.pt")
            if step >= arguments.steps:
                break
    print(f"best validation accuracy {best:.3f}")


if __name__ == "__main__":
    main()
