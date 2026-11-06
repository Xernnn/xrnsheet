"""Draw the report's charts from saved results.

    .venv/bin/python bench/make_figures.py
"""

import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chords.metrics import wilson_interval  # noqa: E402

RESULTS = ROOT / "results" / "chords"
FIGURES = ROOT / "docs" / "figures"

SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
MUTED = "#52514e"
GRID = "#e6e5e0"
TESSERACT, RAPIDOCR, FINAL_MODEL, FIRST_MODEL = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
READERS = [
    ("tesseract", "Tesseract", TESSERACT),
    ("rapidocr", "RapidOCR", RAPIDOCR),
    ("chord_reader_v2", "Our final model", FINAL_MODEL),
]
# Correct chords out of 53 on one real handwritten page. The page isn't ours to redistribute,
# so it isn't in the repository and these counts are recorded here instead.
REAL_PAGE_TOTAL = 53
REAL_PAGE = {
    "Real page, chords cropped by hand": {"Our first model": 32, "Our final model": 41, "RapidOCR": 26},
    "Real page, end to end": {"Our first model": 18, "Our final model": 20, "RapidOCR": 6},
}
# Orange and yellow are too alike side by side, so bars run first model, final model, RapidOCR.
REAL_COLORS = {"RapidOCR": RAPIDOCR, "Our first model": FIRST_MODEL, "Our final model": FINAL_MODEL}
SLICES = [
    ("condition: clean", "Clean print"),
    ("condition: damaged", "Camera damage"),
    ("kind: special symbol", "Symbols (Δ ø °)"),
    ("style+condition: jazz damaged", "Jazz font, damaged"),
    ("style+condition: hand damaged", "Handwriting font, damaged"),
]


def percent(value: float) -> str:
    """Keep a decimal near the top, so 99.6 doesn't print as a perfect score."""
    return f"{value:.1f}%" if value >= 99 else f"{value:.0f}%"


def style(axis) -> None:
    axis.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        axis.spines[side].set_visible(False)
    axis.spines["bottom"].set_color(GRID)
    axis.tick_params(colors=MUTED, length=0, labelsize=10)


def load_summary(name: str) -> dict:
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))["summary"]


def accuracy_chart() -> None:
    summaries = {name: load_summary(name) for name, _, _ in READERS}
    figure, axis = plt.subplots(figsize=(8.6, 4.8), facecolor=SURFACE)
    style(axis)
    bar_height = 0.23
    for reader_index, (name, label, color) in enumerate(READERS):
        for slice_index, (key, _) in enumerate(SLICES):
            values = summaries[name][key]
            y = slice_index + (reader_index - 1) * (bar_height + 0.03)
            accuracy = values["accuracy"] * 100
            axis.barh(y, accuracy, height=bar_height, color=color, label=label if slice_index == 0 else None, zorder=2)
            axis.errorbar(
                accuracy, y, xerr=[[accuracy - values["low"] * 100], [values["high"] * 100 - accuracy]],
                color=TEXT, linewidth=1, capsize=0, zorder=3,
            )
            axis.text(values["high"] * 100 + 1.4, y, percent(accuracy), va="center", color=TEXT, fontsize=9)
    axis.set_yticks(range(len(SLICES)), [label for _, label in SLICES], color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(0, 112)
    axis.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    axis.xaxis.grid(True, color=GRID, zorder=0)
    axis.set_axisbelow(True)
    axis.set_title("Chord accuracy on fonts held out of training", loc="left", color=TEXT, fontsize=13, fontweight="bold", pad=34)
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=10, labelcolor=TEXT, handlelength=1.1)
    figure.tight_layout()
    figure.savefig(FIGURES / "chord_accuracy.png", dpi=180, facecolor=SURFACE)


def real_gap_chart() -> None:
    """Show accuracy falling from synthetic test images to a real handwritten page."""
    held_out = {
        "Our first model": load_summary("chord_reader_v1")["all"],
        "Our final model": load_summary("chord_reader_v2")["all"],
        "RapidOCR": load_summary("rapidocr")["all"],
    }
    groups = {"Held-out fonts (synthetic)": {name: (values["accuracy"], values["low"], values["high"]) for name, values in held_out.items()}}
    for title, counts in REAL_PAGE.items():
        groups[title] = {
            name: (right / REAL_PAGE_TOTAL, *wilson_interval(right, REAL_PAGE_TOTAL)) for name, right in counts.items()
        }

    figure, axis = plt.subplots(figsize=(8.6, 4.0), facecolor=SURFACE)
    style(axis)
    bar_height = 0.23
    for group_index, scores in enumerate(groups.values()):
        for reader_index, (name, (accuracy, low, high)) in enumerate(scores.items()):
            y = group_index + (reader_index - 1) * (bar_height + 0.03)
            axis.barh(y, accuracy * 100, height=bar_height, color=REAL_COLORS[name], label=name if group_index == 0 else None, zorder=2)
            axis.errorbar(accuracy * 100, y, xerr=[[(accuracy - low) * 100], [(high - accuracy) * 100]], color=TEXT, linewidth=1, zorder=3)
            axis.text(high * 100 + 1.4, y, percent(accuracy * 100), va="center", color=TEXT, fontsize=9)
    axis.set_yticks(range(len(groups)), list(groups), color=TEXT)
    axis.invert_yaxis()
    axis.set_xlim(0, 112)
    axis.set_xticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    axis.xaxis.grid(True, color=GRID, zorder=0)
    axis.set_axisbelow(True)
    axis.set_title("The same models, from synthetic images to a real page", loc="left", color=TEXT, fontsize=13, fontweight="bold", pad=34)
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=10, labelcolor=TEXT, handlelength=1.1)
    figure.tight_layout()
    figure.savefig(FIGURES / "real_gap.png", dpi=180, facecolor=SURFACE)


def read_log(name: str) -> tuple[list[float], list[float]]:
    with open(RESULTS / f"{name}_log.csv", newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return [int(row["step"]) / 1000 for row in rows], [float(row["val_accuracy"]) * 100 for row in rows]


def training_chart() -> None:
    figure, axis = plt.subplots(figsize=(8.6, 3.6), facecolor=SURFACE)
    style(axis)
    for name, label, color, nudge in (
        ("chord_reader_v1", "Our first model", FIRST_MODEL, -13),
        ("chord_reader_v2", "Our final model", FINAL_MODEL, 8),
    ):
        steps, accuracy = read_log(name)
        axis.plot(steps, accuracy, color=color, linewidth=2, label=label, zorder=2)
        axis.annotate(f"{label}: {accuracy[-1]:.0f}%", (steps[-1], accuracy[-1]), xytext=(0, nudge), textcoords="offset points", ha="right", color=TEXT, fontsize=10)
    axis.set_ylim(0, 104)
    axis.set_yticks([0, 25, 50, 75, 100], ["0%", "25%", "50%", "75%", "100%"])
    axis.set_xlabel("Training steps (thousands)", color=MUTED, fontsize=10)
    axis.yaxis.grid(True, color=GRID, zorder=0)
    axis.set_axisbelow(True)
    axis.set_title("Validation accuracy on unseen fonts during training", loc="left", color=TEXT, fontsize=13, fontweight="bold", pad=30)
    axis.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=10, labelcolor=TEXT, handlelength=1.1)
    figure.tight_layout()
    figure.savefig(FIGURES / "training_curve.png", dpi=180, facecolor=SURFACE)


if __name__ == "__main__":
    FIGURES.mkdir(parents=True, exist_ok=True)
    accuracy_chart()
    real_gap_chart()
    training_chart()
    print("charts written to", FIGURES)
