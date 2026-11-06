"""Score existing text readers on the chord test set, as the numbers to beat.

    .venv/bin/python -m chords.baselines
"""

import json
import subprocess
import sys
from multiprocessing import Pool

import cv2

from chords.fonts import ROOT
from chords.metrics import print_summary, summarize
from chords.testset import TEST_DIR, load

RESULT_DIR = ROOT / "results" / "chords"


def tesseract(name: str) -> str:
    """Tesseract is the reader Audiveris uses. Enlarged, since it prefers bigger text."""
    image = cv2.imread(str(TEST_DIR / name), cv2.IMREAD_GRAYSCALE)
    image = cv2.resize(image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    image = cv2.copyMakeBorder(image, 20, 20, 20, 20, cv2.BORDER_REPLICATE)
    ok, encoded = cv2.imencode(".png", image)
    command = ["tesseract", "stdin", "stdout", "--psm", "7", "-l", "eng"]
    result = subprocess.run(command, input=encoded.tobytes(), capture_output=True, check=False)
    return result.stdout.decode("utf-8", "ignore").strip()


def run_tesseract(rows: list[dict]) -> None:
    with Pool(8) as pool:
        predictions = pool.map(tesseract, [row["image"] for row in rows], chunksize=20)
    save("tesseract", rows, predictions)


def run_rapidocr(rows: list[dict]) -> None:
    from rapidocr import RapidOCR

    reader = RapidOCR()
    predictions = []
    for row in rows:
        image = cv2.imread(str(TEST_DIR / row["image"]))
        result = reader(image, use_det=False, use_cls=False, use_rec=True)
        predictions.append("".join(result.txts) if result.txts else "")
    save("rapidocr", rows, predictions)


def save(name: str, rows: list[dict], predictions: list[str]) -> None:
    scored = [row | {"predicted": text} for row, text in zip(rows, predictions)]
    summary = summarize(scored)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    (RESULT_DIR / f"{name}.json").write_text(json.dumps({"summary": summary, "rows": scored}, ensure_ascii=False, indent=1))
    print_summary(summary, name)


if __name__ == "__main__":
    test_rows = load()
    chosen = sys.argv[1:] or ["tesseract", "rapidocr"]
    if "tesseract" in chosen:
        run_tesseract(test_rows)
    if "rapidocr" in chosen:
        run_rapidocr(test_rows)
