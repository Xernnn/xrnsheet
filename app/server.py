"""Serve the correction screen.

    .venv/bin/python -m app.server

Then open http://127.0.0.1:8000. Each score lives in its own folder in data/jobs/, with
every saved version kept so edits can be undone.
"""

import json
import re
import shutil
import subprocess
import sys
import threading
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

import uvicorn
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from chords.fonts import ROOT
from pipeline import edit
from pipeline.engrave import export_pdf, render_pages
from pipeline.musicxml import to_text

JOB_DIR = ROOT / "data" / "jobs"
STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MEMORY_LIMIT = "6G"

app = FastAPI(title="xrnsheet")
one_at_a_time = threading.Semaphore(1)  # Recognition is memory hungry.


class ChordEdit(BaseModel):
    text: str


class ChordAdd(BaseModel):
    note: str
    text: str


class NoteEdit(BaseModel):
    action: str


def job_folder(score_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", score_id) is None or not (JOB_DIR / score_id).is_dir():
        raise HTTPException(404, "No such score")
    return JOB_DIR / score_id


def write_status(folder: Path, status: str, message: str = "") -> None:
    (folder / "status.json").write_text(json.dumps({"status": status, "message": message}), encoding="utf-8")


def read_status(folder: Path) -> dict:
    return json.loads((folder / "status.json").read_text(encoding="utf-8"))


def versions(folder: Path) -> list[Path]:
    return sorted(folder.glob("version_*.musicxml"))


def current_root(folder: Path) -> ET.Element:
    return ET.parse(versions(folder)[-1]).getroot()


def save_version(folder: Path, root: ET.Element) -> None:
    number = int(versions(folder)[-1].stem.split("_")[1]) + 1 if versions(folder) else 0
    (folder / f"version_{number:04d}.musicxml").write_text(to_text(root), encoding="utf-8")


def pdf_to_image(path: Path) -> tuple[Path, str]:
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    image_path = path.with_suffix(".png")
    document[0].render(scale=250 / 72).to_pil().convert("RGB").save(image_path)
    note = f"Only the first of {len(document)} pages was read." if len(document) > 1 else ""
    document.close()
    return image_path, note


def recognize_job(folder: Path, upload: Path) -> None:
    """Run recognition in its own process, so a failure or memory spike can't kill the app."""
    with one_at_a_time:
        try:
            image, note = pdf_to_image(upload) if upload.suffix.lower() == ".pdf" else (upload, "")
            command = [sys.executable, "-m", "pipeline.recognize", str(image), "--out", str(folder / "recognized")]
            if shutil.which("systemd-run"):
                command = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_LIMIT}", *command]
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)
            scores = list((folder / "recognized").glob("*.musicxml"))
            if result.returncode != 0 or not scores:
                reason = "No staves were found. Try a sharper, straighter photo." if "No staffs found" in result.stderr or "No noteheads" in result.stderr else "Recognition failed."
                print(result.stderr[-2000:], file=sys.stderr)
                write_status(folder, "failed", reason)
                return
            root = ET.parse(scores[0]).getroot()
            edit.ensure_note_ids(root)
            save_version(folder, root)
            write_status(folder, "ready", note)
        except Exception as error:  # A background thread has no one else to report to.
            print(f"recognition error: {error}", file=sys.stderr)
            write_status(folder, "failed", "Recognition failed.")


@app.post("/api/scores")
async def create_score(file: UploadFile) -> dict:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_TYPES:
        raise HTTPException(415, "Please upload a photo or a PDF.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "That file is too large. The limit is 40 MB.")
    if suffix != ".pdf" and cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE) is None:
        raise HTTPException(415, "That file couldn't be opened as an image.")
    score_id = uuid.uuid4().hex
    folder = JOB_DIR / score_id
    folder.mkdir(parents=True)
    upload = folder / f"upload{suffix}"
    upload.write_bytes(data)
    write_status(folder, "processing")
    threading.Thread(target=recognize_job, args=(folder, upload), daemon=True).start()
    return {"id": score_id}


def describe(folder: Path, status: dict) -> dict:
    root = current_root(folder)
    read_info = {}
    chords_file = folder / "recognized" / "chords.json"
    if chords_file.exists():
        for item in json.loads(chords_file.read_text(encoding="utf-8")):
            read_info[(item["measure"], float(item["onset"]))] = item
    chords = []
    for chord in edit.list_chords(root):
        found = read_info.get((chord["measure"], chord["onset"]))
        unchanged = found is not None and found["chord"] == chord["text"]
        chords.append(chord | {"confidence": found["confidence"] if unchanged else None, "box": found["box"] if found else None})
    return status | {"chords": chords, "canUndo": len(versions(folder)) > 1, "pages": render_pages(to_text(root))}


@app.get("/api/scores/{score_id}")
def get_score(score_id: str) -> dict:
    folder = job_folder(score_id)
    status = read_status(folder)
    return describe(folder, status) if status["status"] == "ready" else status


@app.get("/api/scores/{score_id}/page.jpg")
def get_page(score_id: str) -> FileResponse:
    return FileResponse(job_folder(score_id) / "recognized" / "page.jpg")


def apply_edit(score_id: str, change: Callable[[ET.Element], None]) -> dict:
    folder = job_folder(score_id)
    root = current_root(folder)
    try:
        change(root)
    except edit.EditError as error:
        raise HTTPException(422, str(error)) from error
    save_version(folder, root)
    return describe(folder, read_status(folder))


@app.post("/api/scores/{score_id}/notes/{note_id}")
def change_note(score_id: str, note_id: str, body: NoteEdit) -> dict:
    actions: dict[str, Callable[[ET.Element], None]] = {
        "up": lambda root: edit.move_note(root, note_id, 1),
        "down": lambda root: edit.move_note(root, note_id, -1),
        "sharp": lambda root: edit.set_accidental(root, note_id, 1),
        "flat": lambda root: edit.set_accidental(root, note_id, -1),
        "natural": lambda root: edit.set_accidental(root, note_id, 0),
        "delete": lambda root: edit.delete_note(root, note_id),
        "clear": lambda root: edit.clear_marks(root, note_id),
    }
    if body.action not in actions:
        raise HTTPException(422, "Unknown note action")
    return apply_edit(score_id, actions[body.action])


@app.post("/api/scores/{score_id}/chords/{index}")
def change_chord(score_id: str, index: int, body: ChordEdit) -> dict:
    return apply_edit(score_id, lambda root: edit.set_chord(root, index, body.text))


@app.post("/api/scores/{score_id}/chords")
def add_chord(score_id: str, body: ChordAdd) -> dict:
    return apply_edit(score_id, lambda root: edit.add_chord(root, body.note, body.text))


@app.post("/api/scores/{score_id}/undo")
def undo(score_id: str) -> dict:
    folder = job_folder(score_id)
    saved = versions(folder)
    if len(saved) < 2:
        raise HTTPException(409, "Nothing to undo")
    saved[-1].unlink()
    return describe(folder, read_status(folder))


@app.get("/api/scores/{score_id}/score.musicxml")
def download_musicxml(score_id: str) -> Response:
    text = to_text(current_root(job_folder(score_id)))
    headers = {"Content-Disposition": 'attachment; filename="score.musicxml"'}
    return Response(text, media_type="application/vnd.recordare.musicxml+xml", headers=headers)


@app.get("/api/scores/{score_id}/score.pdf")
def download_pdf(score_id: str) -> FileResponse:
    folder = job_folder(score_id)
    path = folder / "score.pdf"
    export_pdf(to_text(current_root(folder)), path)
    return FileResponse(path, media_type="application/pdf", filename="score.pdf")


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
