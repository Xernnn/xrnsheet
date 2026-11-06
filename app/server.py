"""Serve the correction screen.

    .venv/bin/python -m app.server

Then open http://127.0.0.1:8000. Each score lives in its own folder in data/jobs/, with
every saved version kept so edits can be undone. Add --notes own to read notes with our
own reader instead of homr.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
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
from pipeline.merge import merge_pages
from pipeline.musicxml import to_text

JOB_DIR = ROOT / "data" / "jobs"
STATIC_DIR = Path(__file__).parent / "static"
ALLOWED_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_PDF_PAGES = 40
PAGE_TIMEOUT_SECONDS = 600
MEMORY_LIMIT = "6G"
NO_STAVES = "No staves were found. Try a sharper, straighter photo."

app = FastAPI(title="xrnsheet")
one_at_a_time = threading.Semaphore(1)  # Recognition is memory hungry.


class ChordEdit(BaseModel):
    text: str


class ChordAdd(BaseModel):
    note: str
    text: str


class NoteEdit(BaseModel):
    action: str


class CheckedMark(BaseModel):
    checked: bool


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


def pdf_to_images(path: Path) -> tuple[list[Path], str]:
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    folder = path.parent / "pages"
    folder.mkdir(exist_ok=True)
    images = []
    for index in range(min(len(document), MAX_PDF_PAGES)):
        image_path = folder / f"page_{index + 1:03d}.png"
        document[index].render(scale=250 / 72).to_pil().convert("RGB").save(image_path)
        images.append(image_path)
    note = f"Only the first {MAX_PDF_PAGES} of {len(document)} pages were read." if len(document) > MAX_PDF_PAGES else ""
    document.close()
    return images, note


def recognize_page(image: Path, out_dir: Path) -> str | None:
    """Recognize one page in its own process, so a crash or memory spike can't kill the app.

    Returns None on success, or the reason it failed.
    """
    command = [sys.executable, "-m", "pipeline.recognize", str(image), "--out", str(out_dir), "--notes", os.environ.get("XRNSHEET_NOTES", "homr")]
    if shutil.which("systemd-run"):
        command = ["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={MEMORY_LIMIT}", *command]
    try:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=PAGE_TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired:
        return "Recognition took too long."
    if result.returncode == 0 and list(out_dir.glob("*.musicxml")):
        return None
    print(result.stderr[-2000:], file=sys.stderr)
    return NO_STAVES if "No staffs found" in result.stderr or "No noteheads" in result.stderr else "Recognition failed."


def combine_pages(folder: Path, numbers: list[int]) -> ET.Element:
    """Merge recognized pages, and renumber their chords to match the merged score."""
    pages = [ET.parse(next((folder / "recognized" / f"page_{number:03d}").glob("*.musicxml"))).getroot() for number in numbers]
    merged, offsets = merge_pages(pages)
    chords = []
    for number, offset in zip(numbers, offsets):
        found = json.loads((folder / "recognized" / f"page_{number:03d}" / "chords.json").read_text(encoding="utf-8"))
        chords.extend(item | {"page": number, "measure": item["measure"] + offset} for item in found)
    (folder / "recognized" / "chords.json").write_text(json.dumps(chords, ensure_ascii=False, indent=1), encoding="utf-8")
    (folder / "recognized" / "pages.json").write_text(json.dumps(numbers), encoding="utf-8")
    return merged


def recognize_job(folder: Path, upload: Path) -> None:
    with one_at_a_time:
        try:
            images, note = pdf_to_images(upload) if upload.suffix.lower() == ".pdf" else ([upload], "")
            if not images:
                write_status(folder, "failed", "That PDF has no pages.")
                return
            read, failed = [], {}
            for number, image in enumerate(images, start=1):
                if len(images) > 1:
                    write_status(folder, "processing", f"Reading page {number} of {len(images)}.")
                reason = recognize_page(image, folder / "recognized" / f"page_{number:03d}")
                if reason is None:
                    read.append(number)
                else:
                    failed[number] = reason
            if not read:
                write_status(folder, "failed", next(iter(failed.values())))
                return
            root = combine_pages(folder, read)
            edit.ensure_note_ids(root)
            save_version(folder, root)
            if failed:
                listed = ", ".join(str(number) for number in failed)
                note = f"{note} Couldn't read page {listed}, so it was left out.".strip()
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


def read_chord_file(folder: Path) -> list[dict]:
    path = folder / "recognized" / "chords.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def source_pages(folder: Path) -> list[int]:
    return json.loads((folder / "recognized" / "pages.json").read_text(encoding="utf-8"))


def describe(folder: Path, status: dict) -> dict:
    root = current_root(folder)
    read_info: dict[tuple[int, float], dict] = {}
    for item in read_chord_file(folder):
        read_info.setdefault((item["measure"], float(item["onset"])), item)  # Only the first was placed.
    chords = []
    for chord in edit.list_chords(root):
        found = read_info.get((chord["measure"], chord["onset"]))
        unchanged = found is not None and found["chord"] == chord["text"]
        chords.append(chord | {
            "confidence": found["confidence"] if unchanged else None,
            "box": found["box"] if found else None,
            "page": found["page"] if found else None,
        })
    return status | {
        "chords": chords,
        "canUndo": len(versions(folder)) > 1,
        "pages": render_pages(to_text(root)),
        "sourcePages": source_pages(folder),
        "checked": (folder / "checked.json").exists(),
    }


@app.get("/api/scores")
def list_scores() -> list[dict]:
    """List finished sheets, newest first, so one can be reopened after closing the page."""
    found = []
    for folder in JOB_DIR.glob("*") if JOB_DIR.exists() else []:
        if not (folder / "status.json").exists() or read_status(folder)["status"] != "ready":
            continue
        found.append({
            "id": folder.name,
            "saved": versions(folder)[-1].stat().st_mtime,
            "pages": len(source_pages(folder)),
            "chords": len(read_chord_file(folder)),
            "checked": (folder / "checked.json").exists(),
        })
    return sorted(found, key=lambda item: item["saved"], reverse=True)


@app.get("/api/scores/{score_id}")
def get_score(score_id: str) -> dict:
    folder = job_folder(score_id)
    status = read_status(folder)
    return describe(folder, status) if status["status"] == "ready" else status


@app.get("/api/scores/{score_id}/source/{number}.jpg")
def get_source_page(score_id: str, number: int) -> FileResponse:
    path = job_folder(score_id) / "recognized" / f"page_{number:03d}" / "page.jpg"
    if not path.exists():
        raise HTTPException(404, "No such page")
    return FileResponse(path)


@app.post("/api/scores/{score_id}/checked")
def mark_checked(score_id: str, body: CheckedMark) -> dict:
    """Record that a person has fixed this sheet, which makes its answers fit to learn from.

    An uncorrected chord only counts as right once someone has actually looked at it.
    """
    folder = job_folder(score_id)
    if read_status(folder)["status"] != "ready":
        raise HTTPException(409, "This sheet isn't ready yet")
    path = folder / "checked.json"
    if body.checked:
        path.write_text(json.dumps({"checked_at": time.time()}), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return describe(folder, read_status(folder))


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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--notes", choices=["homr", "own"], default="homr")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    os.environ["XRNSHEET_NOTES"] = arguments.notes
    uvicorn.run(app, host="127.0.0.1", port=arguments.port)
