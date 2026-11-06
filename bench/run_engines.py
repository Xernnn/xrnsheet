"""Run each recognition engine on every sheet and print how well it did.

    python bench/run_engines.py                 run everything
    python bench/run_engines.py --only jazz     only images with "jazz" in the name
    python bench/run_engines.py --engine homr   only one engine

Finished runs are kept in results/, so a second run only does what is missing. Put your
own photos in data/my_sheets/. If you also save a corrected copy as
data/truth/<same name>.musicxml, that sheet gets scored too.
"""

import argparse
import csv
import os
import shutil
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench"))

from make_samples import CHART_COUNT, CORPUS_PIECES  # noqa: E402
from score import compare, read_chords  # noqa: E402

RESULTS_DIR = ROOT / "results"
AUDIVERIS = ROOT / "tools/audiveris/opt/audiveris/bin/Audiveris"
HOMR = ROOT / ".venv/bin/homr"
TIMEOUT_SECONDS = 600
MEMORY_LIMIT = "6G"  # A runaway engine should fail on its own, not freeze the laptop.

STYLES = {name: style for name, (_, _, style) in CORPUS_PIECES.items()} | {"jazz_chart": "jazz"}
STYLES |= {f"chart_{index:02d}": "chart" for index in range(CHART_COUNT)}


def limited(command: list[str]) -> list[str]:
    return ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_LIMIT}", *command]


def run_homr(image: Path, out_dir: Path) -> None:
    copy = out_dir / image.name
    shutil.copy(image, copy)
    subprocess.run(limited([str(HOMR), str(copy)]), timeout=TIMEOUT_SECONDS, capture_output=True, check=False)
    copy.unlink()


def run_audiveris(image: Path, out_dir: Path) -> None:
    command = [str(AUDIVERIS), "-batch", "-transcribe", "-export", "-output", str(out_dir), "--", str(image)]
    environment = os.environ | {"TESSDATA_PREFIX": "/usr/share/tessdata"}
    subprocess.run(limited(command), timeout=TIMEOUT_SECONDS, capture_output=True, check=False, env=environment)


def run_pipeline(image: Path, out_dir: Path, reader: str) -> None:
    command = [sys.executable, "-m", "pipeline.recognize", str(image), "--out", str(out_dir), "--reader", reader]
    subprocess.run(limited(command), timeout=TIMEOUT_SECONDS, capture_output=True, check=False, cwd=ROOT)


def run_xrnsheet(image: Path, out_dir: Path) -> None:
    run_pipeline(image, out_dir, "model")


def run_general_ocr(image: Path, out_dir: Path) -> None:
    """The same pipeline with a general OCR reading chords, to measure what our model adds."""
    run_pipeline(image, out_dir, "rapidocr")


ENGINES = {
    "homr": run_homr,
    "audiveris": run_audiveris,
    "xrnsheet": run_xrnsheet,
    "general_ocr": run_general_ocr,
}


def list_images(pattern: str) -> list[Path]:
    images = sorted((ROOT / "data/images").glob("*.png"))
    images += sorted(path for path in (ROOT / "data/my_sheets").glob("*") if path.is_file())
    return [path for path in images if pattern in path.name]


def find_outputs(out_dir: Path) -> list[Path]:
    """Return every result file. Audiveris splits a sheet it reads as several movements."""
    return sorted([*out_dir.rglob("*.musicxml"), *out_dir.rglob("*.mxl")])


def evaluate(engine: str, image: Path) -> dict:
    out_dir = RESULTS_DIR / engine / image.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    timing_file = out_dir / "seconds.txt"

    outputs = find_outputs(out_dir)
    if not outputs:
        started = time.monotonic()
        try:
            ENGINES[engine](image, out_dir)
        except subprocess.TimeoutExpired:
            pass
        timing_file.write_text(f"{time.monotonic() - started:.1f}")
        outputs = find_outputs(out_dir)

    piece, _, condition = image.stem.partition("__")
    row = {
        "engine": engine,
        "piece": piece,
        "condition": condition or "my_sheet",
        "style": STYLES.get(piece, "mine"),
        "seconds": float(timing_file.read_text()) if timing_file.exists() else None,
        "output": bool(outputs),
    }
    truth = ROOT / "data/truth" / f"{piece}.musicxml"
    if not truth.exists():
        return row
    failed = {
        "pitch_f1": 0.0,
        "pitch_class_f1": 0.0,
        "note_f1": 0.0,
        "chord_recall": 0.0 if read_chords([truth], include_text=False) else None,
        "chord_placed": 0.0 if read_chords([truth], include_text=False) else None,
    }
    if not outputs:
        return row | failed
    try:
        result = compare(truth, outputs)
    except Exception as error:  # Engines sometimes write files that cannot be parsed.
        print(f"  could not read {outputs[0].name}: {error}")
        return row | {"output": False} | failed
    return row | {
        "chord_placed": result.chord_placed,
        "pitch_f1": result.pitch_f1,
        "pitch_class_f1": result.pitch_class_f1,
        "note_f1": result.note_f1,
        "chord_recall": result.chord_recall,
        "truth_notes": result.truth_notes,
        "predicted_notes": result.predicted_notes,
        "truth_chords": result.truth_chords,
        "predicted_chords": result.predicted_chords,
    }


def mean(rows: list[dict], key: str) -> str:
    values = [row[key] for row in rows if row.get(key) is not None]
    return f"{statistics.mean(values):.2f}" if values else "-"


def print_summary(rows: list[dict]) -> None:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["style"], row["condition"], row["engine"])].append(row)
    print(
        f"\n{'style':<10}{'condition':<13}{'engine':<13}{'sheets':>6}{'failed':>7}"
        f"{'pitch':>7}{'no-oct':>7}{'note':>7}{'chords':>8}{'placed':>8}{'sec':>7}"
    )
    for (style, condition, engine), group in sorted(groups.items()):
        print(
            f"{style:<10}{condition:<13}{engine:<13}{len(group):>6}"
            f"{sum(not row['output'] for row in group):>7}{mean(group, 'pitch_f1'):>7}"
            f"{mean(group, 'pitch_class_f1'):>7}{mean(group, 'note_f1'):>7}"
            f"{mean(group, 'chord_recall'):>8}{mean(group, 'chord_placed'):>8}{mean(group, 'seconds'):>7}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="only images whose name contains this text")
    parser.add_argument("--engine", choices=sorted(ENGINES), help="only run this engine")
    arguments = parser.parse_args()

    engines = [arguments.engine] if arguments.engine else list(ENGINES)
    rows = []
    for image in list_images(arguments.only):
        for engine in engines:
            row = evaluate(engine, image)
            rows.append(row)
            print(f"{engine:<10} {image.stem:<34} pitch={row.get('pitch_f1', 0):.2f} note={row.get('note_f1', 0):.2f}", flush=True)

    RESULTS_DIR.mkdir(exist_ok=True)
    with open(RESULTS_DIR / "scores.csv", "w", newline="", encoding="utf-8") as file:
        fields = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print_summary(rows)


if __name__ == "__main__":
    main()
