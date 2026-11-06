"""How chord reading is scored. Every reader goes through the same functions."""

import math
from collections import defaultdict

from chords.vocab import clean_text, parse_chord


def is_correct(predicted: str, label: str) -> bool:
    """True if both texts spell the same chord, so CM7 counts as correct for Cmaj7."""
    chord = parse_chord(predicted)
    return chord is not None and chord == parse_chord(label)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def character_error_rate(predicted: list[str], labels: list[str]) -> float:
    errors = sum(edit_distance(clean_text(p), clean_text(t)) for p, t in zip(predicted, labels))
    return errors / max(1, sum(len(clean_text(t)) for t in labels))


def wilson_interval(correct: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% confidence interval for an accuracy, which behaves well near 0 and 1."""
    if total == 0:
        return (0.0, 0.0)
    rate = correct / total
    denominator = 1 + z**2 / total
    center = (rate + z**2 / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z**2 / (4 * total**2)) / denominator
    return (center - margin, center + margin)


def label_kind(label: str) -> str:
    if any(symbol in label for symbol in "Δø°"):
        return "special symbol"
    if "b" in label or "#" in label:
        return "flat or sharp"
    return "plain"


def summarize(rows: list[dict]) -> dict[str, dict]:
    """Score rows of {font, style, damage, label, predicted}, overall and by slice."""
    groups = defaultdict(list)
    for row in rows:
        condition = "damaged" if row["damage"] else "clean"
        groups["all"].append(row)
        groups[f"condition: {condition}"].append(row)
        groups[f"style: {row['style']}"].append(row)
        groups[f"style+condition: {row['style']} {condition}"].append(row)
        groups[f"font: {row['font']}"].append(row)
        groups[f"kind: {label_kind(row['label'])}"].append(row)

    summary = {}
    for name, group in sorted(groups.items()):
        correct = sum(is_correct(row["predicted"], row["label"]) for row in group)
        low, high = wilson_interval(correct, len(group))
        summary[name] = {
            "n": len(group),
            "accuracy": correct / len(group),
            "low": low,
            "high": high,
            "cer": character_error_rate([r["predicted"] for r in group], [r["label"] for r in group]),
        }
    return summary


def print_summary(summary: dict[str, dict], title: str) -> None:
    print(f"\n{title}\n{'slice':<36}{'n':>6}{'accuracy':>10}{'95% CI':>16}{'CER':>7}")
    for name, values in summary.items():
        interval = f"{values['low']:.3f}-{values['high']:.3f}"
        print(f"{name:<36}{values['n']:>6}{values['accuracy']:>10.3f}{interval:>16}{values['cer']:>7.3f}")
