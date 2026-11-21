"""A stand-in for the page reader that takes as long as it's told, for trying the screens around reading.

The real reader takes up to a minute a page, and what it finds depends on its models, so the screens
shown while a sheet waits, is read, is cancelled or fails can't be tried with it. This one waits, then
writes what the real one writes for a short score: the notes, a chord with its box, the page, and
where its staff lines are. A page with nothing dark on it fails, as a photo with no staves does.

    .venv/bin/python tests/reading_stand_in.py --seconds 6 --port 8012 --jobs /tmp/jobs --reader /tmp/reader

serves the app with it, as the browser checks in e2e_editor.py do. test_server.py uses write_stand_in.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

READER = '''
import argparse, json, os, pathlib, sys, time
from PIL import Image

parser = argparse.ArgumentParser()
parser.add_argument("image", type=pathlib.Path)
parser.add_argument("--out", type=pathlib.Path, required=True)
parser.add_argument("--notes", default="homr")
arguments = parser.parse_args()
pathlib.Path("started").write_text(str(os.getpid()))  # For a test to stop it, or see it stopped.
time.sleep(SECONDS)
image = Image.open(arguments.image).convert("L")
if image.getextrema()[0] > 200:
    print("No staffs found", file=sys.stderr)
    sys.exit(1)
arguments.out.mkdir(parents=True, exist_ok=True)
width, height = image.size
bar = '<measure>{0}<note><pitch><step>C</step><octave>5</octave></pitch><duration>1</duration><type>quarter</type></note>' + '<note><pitch><step>E</step><octave>5</octave></pitch><duration>1</duration><type>quarter</type></note>' * 3 + '</measure>'
first = '<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes><harmony><root><root-step>C</root-step></root><kind>major</kind></harmony>'
score = '<score-partwise version="4.0"><part-list><score-part id="P1"><part-name/></score-part></part-list><part id="P1">' + bar.format(first) + bar.format('') * 3 + '</part></score-partwise>'
(arguments.out / "page.musicxml").write_text(score, encoding="utf-8")
top, unit = height * 0.4, height * 0.02
box = [width * 0.1, top - unit * 3, width * 0.16, top - unit]
chord = {"text": "C", "chord": "C", "confidence": 0.95, "box": box, "measure": 0, "onset": 0.0}
(arguments.out / "chords.json").write_text(json.dumps([chord]), encoding="utf-8")
staff = {"left": width * 0.08, "right": width * 0.92, "top": top, "bottom": top + unit * 4, "unit": unit, "bars": [], "notes": []}
(arguments.out / "layout.json").write_text(json.dumps([staff]), encoding="utf-8")
image.convert("RGB").save(arguments.out / "page.jpg")
'''


def write_stand_in(folder: Path, seconds: float) -> Path:
    """Write a pipeline.recognize into the folder that reads a page in this many seconds, and return the folder."""
    (folder / "pipeline").mkdir(parents=True, exist_ok=True)
    (folder / "pipeline" / "__init__.py").write_text("", encoding="utf-8")
    (folder / "pipeline" / "recognize.py").write_text(READER.replace("SECONDS", repr(float(seconds))), encoding="utf-8")
    # It reads without the real readers, so it needs only stand-ins for them, not the ones fetched.
    (folder / "models").mkdir(exist_ok=True)
    for name in ("chord_reader.onnx", "note_reader.onnx"):
        (folder / "models" / name).touch()
    return folder


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from app import server

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=6.0, help="how long each page takes to read")
    parser.add_argument("--reader", type=Path, help="the folder to write the stand-in into, for whoever starts this to remove (default: one of its own)")
    arguments, rest = parser.parse_known_args()
    # Stopped with a signal, as a test stops it, the server ends without coming back here, so a folder
    # of its own would be left behind; one given is removed by whoever gave it.
    folder = write_stand_in(arguments.reader or Path(tempfile.mkdtemp(prefix="xrnsheet-stand-in-")), arguments.seconds)
    server.ROOT = folder  # Pages are read by whatever pipeline.recognize is there.
    server.MODEL_DIR = folder / "models"
    try:
        server.main(rest)
    finally:
        if arguments.reader is None:
            shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
