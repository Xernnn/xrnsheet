"""Score the bar repairs on outputs that already exist, before and after.

Outputs from before the repairs were added show what each one changed, so nothing is re-recognized.
Those are kept in `results/before_repair`, because anything written since has already been repaired
and would show no change. Every sheet counts, because the repairs were designed by looking at two
pieces, and a signature changed on any of the others would be a false alarm.

    .venv/bin/python bench/repair_report.py [folder of outputs]
"""

import csv
import statistics
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench"))

from score import compare, read_times  # noqa: E402

from pipeline.musicxml import load_root, to_text  # noqa: E402
from pipeline.repair import repair  # noqa: E402

ENGINES = ("xrnsheet", "own_notes")
RESULTS = ROOT / "results"
BEFORE_REPAIR = RESULTS / "before_repair"
METRICS = ("pitch_f1", "note_f1", "time_right", "bars_full")


def repaired_copy(path: Path, folder: Path) -> Path:
    root = load_root(path)
    repair(root)
    copy = folder / f"{path.stem}.musicxml"
    copy.write_text(to_text(root), encoding="utf-8")
    return copy


def score_folder(engine: str, folder: Path, truth: Path, work: Path) -> dict:
    outputs = sorted(path for path in folder.iterdir() if path.suffix in (".musicxml", ".mxl"))
    before = compare(truth, outputs)
    after_paths = [repaired_copy(path, work) for path in outputs]
    after = compare(truth, after_paths)
    piece, _, condition = folder.name.partition("__")
    return {
        "engine": engine,
        "piece": piece,
        "condition": condition,
        "truth_time": before.truth_time,
        "read_time": read_times(outputs)[0] if read_times(outputs) else "",
        "new_time": read_times(after_paths)[0] if read_times(after_paths) else "",
    } | {f"{name}_before": getattr(before, name) for name in METRICS} | {f"{name}_after": getattr(after, name) for name in METRICS}


def main() -> None:
    source = Path(sys.argv[1]) if len(sys.argv) > 1 else BEFORE_REPAIR
    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        for engine in ENGINES:
            for folder in sorted((source / engine).iterdir()):
                truth = ROOT / "data/truth" / f"{folder.name.partition('__')[0]}.musicxml"
                if folder.is_dir() and truth.exists() and any(path.suffix in (".musicxml", ".mxl") for path in folder.iterdir()):
                    rows.append(score_folder(engine, folder, truth, work))

    groups = defaultdict(list)
    for row in rows:
        groups[(row["engine"], row["truth_time"])].append(row)
    print(f"{'engine':<11}{'signature':<11}{'sheets':>6}" + "".join(f"{name.removesuffix('_f1'):>18}" for name in METRICS))
    for (engine, signature), group in sorted(groups.items()):
        cells = "".join(
            f"{statistics.mean(row[f'{name}_before'] for row in group):>10.2f} -> {statistics.mean(row[f'{name}_after'] for row in group):.2f}"
            for name in METRICS
        )
        print(f"{engine:<11}{signature:<11}{len(group):>6}{cells}")

    changed = [row for row in rows if row["read_time"] != row["new_time"]]
    print(f"\n{len(changed)} of {len(rows)} sheets had their signature changed")
    for row in changed:
        verdict = "right" if row["new_time"] == row["truth_time"] else "WRONG"
        print(f"  {verdict}  {row['engine']:<10} {row['piece']}__{row['condition']}  read {row['read_time']}, now {row['new_time']}, true {row['truth_time']}")
    missed = [row for row in rows if row["new_time"] != row["truth_time"]]
    print(f"{len(missed)} sheets still have the wrong signature")
    for row in missed:
        print(f"  {row['engine']:<10} {row['piece']}__{row['condition']}  now {row['new_time']}, true {row['truth_time']}")

    target = RESULTS / "notes" / "repair_scores.csv"
    with open(target, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"saved {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
