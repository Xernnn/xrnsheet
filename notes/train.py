"""Train the note reader on rendered strips, damaging half of them as they're loaded.

    .venv-train/bin/python -m notes.train
    .venv-train/bin/python -m notes.train --steps 300 --name smoke   quick check
"""

import argparse
import csv
import random
import time

import cv2
import torch
from torch import nn
from torch.utils.data import DataLoader, IterableDataset, get_worker_info

from chords.fonts import ROOT
from chords.metrics import edit_distance
from notes.model import NoteReader, read_strips, to_batch
from notes.render import DATA_DIR, damage_strip
from notes.vocab import TOKEN_INDEX

MODEL_DIR = ROOT / "models"
RESULT_DIR = ROOT / "results" / "notes"
VALIDATION_SIZE = 600
POOL_BATCHES = 40  # Sorting a pool by width keeps padding, and so wasted memory, low.
MOST_PIXELS = 3_000_000  # About 32 full-width strips, measured at under 2 GB of GPU memory.


def load_rows(split: str) -> list[tuple[str, str, list[str]]]:
    with open(DATA_DIR / f"{split}.tsv", encoding="utf-8", newline="") as file:
        return [(name, font, tokens.split(" ")) for name, font, tokens in csv.reader(file, delimiter="\t")]


class Strips(IterableDataset):
    """An endless stream of width-matched batches."""

    def __init__(self, rows: list, batch_size: int, damage: float, seed: int) -> None:
        self.rows = rows
        self.batch_size = batch_size
        self.damage = damage
        self.seed = seed

    def __iter__(self):
        worker = get_worker_info()
        rng = random.Random(self.seed + (worker.id if worker else 0))
        while True:
            pool = rng.sample(self.rows, self.batch_size * POOL_BATCHES)
            loaded = []
            for name, _, tokens in pool:
                strip = cv2.imread(str(DATA_DIR / "train" / name), cv2.IMREAD_GRAYSCALE)
                if rng.random() < self.damage:
                    strip = damage_strip(strip, rng)
                loaded.append((strip, tokens))
            loaded.sort(key=lambda item: item[0].shape[1])
            batches = [loaded[start : start + self.batch_size] for start in range(0, len(loaded), self.batch_size)]
            rng.shuffle(batches)
            for batch in batches:
                yield fit_memory(batch)


def fit_memory(batch: list) -> list:
    """Drop strips from a batch of very wide ones, so a rare wide batch can't run out of memory."""
    widest = max(strip.shape[1] for strip, _ in batch)
    return batch[: max(1, MOST_PIXELS // (widest * 96))]


def collate(batch: list):
    strips, labels = zip(*batch[0])
    pixels, widths = to_batch(list(strips))
    targets = torch.tensor([TOKEN_INDEX[token] for label in labels for token in label], dtype=torch.long)
    return pixels, widths, targets, torch.tensor([len(label) for label in labels])


def token_error(predicted: list[list[str]], truth: list[list[str]]) -> float:
    """Return edits needed per true token, which is the character error rate for symbols."""
    return sum(edit_distance(p, t) for p, t in zip(predicted, truth)) / sum(len(t) for t in truth)


def validation_set() -> tuple[list, list]:
    rng = random.Random(1234)
    rows = rng.sample(load_rows("val"), VALIDATION_SIZE)
    strips, labels = [], []
    for index, (name, _, tokens) in enumerate(rows):
        strip = cv2.imread(str(DATA_DIR / "val" / name), cv2.IMREAD_GRAYSCALE)
        strips.append(damage_strip(strip, rng) if index % 2 == 0 else strip)
        labels.append(tokens)
    return strips, labels


def validate(model: nn.Module, strips: list, labels: list, device: str) -> float:
    model.eval()
    readings = read_strips(model, strips, device, batch_size=16)
    model.train()
    torch.cuda.empty_cache()
    return token_error([reading.tokens for reading in readings], labels)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--steps", type=int, default=24000)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--name", default="note_reader")
    parser.add_argument("--damage", type=float, default=0.5)
    arguments = parser.parse_args()

    torch.manual_seed(arguments.seed)
    random.seed(arguments.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    MODEL_DIR.mkdir(exist_ok=True)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_rows("train")
    data = Strips(rows, arguments.batch_size, arguments.damage, arguments.seed)
    loader = DataLoader(data, batch_size=1, num_workers=arguments.workers, collate_fn=collate, prefetch_factor=4, persistent_workers=True)
    val_strips, val_labels = validation_set()

    model = NoteReader().to(device)
    print(f"{len(rows)} strips, {sum(parameter.numel() for parameter in model.parameters()):,} parameters", flush=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=arguments.learning_rate, weight_decay=0.01)
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, arguments.learning_rate, total_steps=arguments.steps, pct_start=0.05)
    loss_function = nn.CTCLoss(blank=0, zero_infinity=True)
    scaler = torch.amp.GradScaler(device, enabled=device == "cuda")

    log_path = RESULT_DIR / f"{arguments.name}_log.csv"
    best = float("inf")
    started = time.time()
    with open(log_path, "w", newline="", encoding="utf-8") as log:
        writer = csv.writer(log)
        writer.writerow(["step", "loss", "val_token_error", "minutes"])
        for step, (pixels, widths, targets, target_lengths) in enumerate(loader, start=1):
            pixels, widths = pixels.to(device, non_blocking=True), widths.to(device, non_blocking=True)
            with torch.autocast(device, enabled=device == "cuda"):
                log_probabilities, lengths = model(pixels, widths)
            loss = loss_function(log_probabilities.float().permute(1, 0, 2), targets.to(device), lengths, target_lengths)
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.unscale_(optimizer)
            nn.utils.clip_grad_norm_(model.parameters(), 5.0)
            scaler.step(optimizer)
            scaler.update()
            schedule.step()

            if step % 1000 == 0 or step == arguments.steps:
                error = validate(model, val_strips, val_labels, device)
                minutes = (time.time() - started) / 60
                writer.writerow([step, f"{loss.item():.4f}", f"{error:.4f}", f"{minutes:.1f}"])
                log.flush()
                print(f"step {step} loss {loss.item():.3f} val token error {error:.4f} {minutes:.1f} min", flush=True)
                if error < best:
                    best = error
                    torch.save({"model": model.state_dict(), "step": step, "val_token_error": error}, MODEL_DIR / f"{arguments.name}.pt")
            if step >= arguments.steps:
                break
    print(f"best validation token error {best:.4f}", flush=True)


if __name__ == "__main__":
    main()
