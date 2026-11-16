"""Export the note reader to ONNX, which is what the app reads notes with when run with --notes own.

    .venv-train/bin/python -m notes.export                 after training the note reader
    .venv-train/bin/python -m notes.export --name smoke    another model in models/

This writes models/<name>.onnx beside models/<name>.pt, as chords/export.py does for the chord
reader, and for the same reason. Unlike that one, this network exports as it is.
"""

import argparse
from pathlib import Path

import numpy as np

from chords.export import MODEL_DIR, export
from notes.model import load_reader
from notes.staves import STRIP_HEIGHT


def export_note_reader(name: str = "note_reader") -> Path:
    path = MODEL_DIR / f"{name}.onnx"
    sample = [np.full((STRIP_HEIGHT, width), 255, np.uint8) for width in (200, 120)]
    export(load_reader(MODEL_DIR / f"{name}.pt", "cpu"), sample, path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", default="note_reader", help="the model in models/, without .pt")
    arguments = parser.parse_args()
    print(f"Wrote {export_note_reader(arguments.name)}")


if __name__ == "__main__":
    main()
