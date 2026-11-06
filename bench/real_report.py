"""Report how the app really did, on sheets you've checked by hand.

    .venv/bin/python bench/real_report.py          held-out sheets only
    .venv/bin/python bench/real_report.py --all    every checked sheet, to spot problems

Each sheet is scored by comparing what the app first produced with what you left after fixing
it. That's the real-world score the synthetic benchmark can't give. One limit: a mistake you
didn't notice counts as correct, so careful checking matters.
"""

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bench.score import Score, compare  # noqa: E402
from chords.corrections import JOB_DIR, checked_sheets, group_key, is_test_key, sheet_digest, versions_of  # noqa: E402
from chords.metrics import wilson_interval  # noqa: E402
from pipeline import edit  # noqa: E402


def choose_sheets(job_dir: Path, everything: bool) -> list[Path]:
    sheets = checked_sheets(job_dir)
    if everything:
        return sheets
    chords = {folder.name: [chord["text"] for chord in edit.list_chords(ET.parse(versions_of(folder)[1]).getroot())] for folder in sheets}
    keys = group_key({folder.name: sheet_digest(folder) for folder in sheets}, chords)
    return [folder for folder in sheets if is_test_key(keys[folder.name])]


def pool(scores: list[Score]) -> dict:
    """Add up counts across sheets, so a long sheet counts for more than a short one."""
    truth_chords = sum(score.truth_chords for score in scores)
    read_right = round(sum((score.chord_recall or 0) * score.truth_chords for score in scores))
    placed_right = round(sum((score.chord_placed or 0) * score.truth_chords for score in scores))
    predicted = sum(score.predicted_chords for score in scores)
    truth_notes = sum(score.truth_notes for score in scores)
    predicted_notes = sum(score.predicted_notes for score in scores)
    total_notes = truth_notes + predicted_notes
    note_matches = sum(score.note_f1 * (score.truth_notes + score.predicted_notes) / 2 for score in scores)
    return {
        "sheets": len(scores),
        "truth_chords": truth_chords,
        "read_right": read_right,
        "placed_right": placed_right,
        "false_chords": predicted - read_right,
        "truth_notes": truth_notes,
        "note_f1": 2 * note_matches / total_notes if total_notes else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all", action="store_true", help="include the sheets used for training")
    arguments = parser.parse_args()

    sheets = choose_sheets(JOB_DIR, arguments.all)
    if not sheets:
        raise SystemExit("No sheets to report yet. Check some in the app with \"Mark as checked\". About one in five is held out.")
    totals = pool([compare(versions_of(folder)[1], [versions_of(folder)[0]]) for folder in sheets])

    print(f"{totals['sheets']} {'checked' if arguments.all else 'held-out'} sheets")
    print(f"Notes: F1 {totals['note_f1']:.3f} over {totals['truth_notes']} notes")
    if totals["truth_chords"]:
        low, high = wilson_interval(totals["read_right"], totals["truth_chords"])
        print(f"Chords read: {totals['read_right']}/{totals['truth_chords']} ({totals['read_right'] / totals['truth_chords']:.1%}, 95% interval {low:.1%} to {high:.1%})")
        print(f"Chords also on the right beat: {totals['placed_right']}/{totals['truth_chords']}")
        print(f"False chords you removed or changed: {totals['false_chords']}")


if __name__ == "__main__":
    main()
