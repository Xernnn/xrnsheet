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
from fractions import Fraction
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chords.fonts import ROOT
from pipeline import edit, marks, structure, transpose
from pipeline.engrave import export_pdf, glyph_script, render_pages
from pipeline.merge import merge_pages
from pipeline.midi_file import midi_bytes
from pipeline.musicxml import UnreadableScore, read_score_file, to_text
from pipeline.playback import playback
from pipeline.rhythm import fit_divisions, indexed

JOB_DIR = ROOT / "data" / "jobs"
STATIC_DIR = Path(__file__).parent / "static"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".pdf"}
SCORE_TYPES = {".musicxml", ".xml", ".mxl"}
ALLOWED_TYPES = IMAGE_TYPES | SCORE_TYPES
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
FORM_BYTES = 64 * 1024  # What an upload wraps its file in: the file's name and type, and the form's edges.
TOO_LARGE = "That file is too large. The limit is 40 MB."
MAX_PDF_PAGES = 40
# A PDF page is drawn at 250 dots an inch, but no larger than this on its longest side: a page can
# say it's 200 inches across, which would take gigabytes. At 250 dots an inch, A3 is about 4,100.
LONGEST_PDF_PAGE = 5000
PAGE_TIMEOUT_SECONDS = 600
MEMORY_LIMIT = "6G"
NO_STAVES = "No staves were found. Try a sharper, straighter photo."
INTERRUPTED = "The app was closed before this sheet was read. Please open it again."
OWN_NAMES = {"127.0.0.1", "localhost"}  # What a browser on this computer calls the app.
ONLY_READS = {"GET", "HEAD", "OPTIONS"}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    fail_interrupted_jobs()
    yield
    stop_reading()


class OwnPagesOnly:
    """Refuse requests that the app's own page couldn't have sent.

    The app has no sign-in, because it only listens on this computer. But a browser here sends it
    requests from whatever page is open. A page on another site could upload files to it, and a
    site whose name is pointed at 127.0.0.1 would be served as if it were the app, and could read
    and change every score. So a request has to be addressed to this computer by one of its own
    names, and one that changes something can't come from another site's page. Requests from no
    page at all, such as a script's, carry no Origin and are let through.
    """

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = Headers(scope=scope)
            host, origin = headers.get("host", ""), headers.get("origin")
            try:
                named, sender = urlsplit(f"//{host}").hostname, urlsplit(origin or "").netloc
            except ValueError:
                named, sender = None, ""
            if named not in OWN_NAMES:
                refused = "Open this app at http://127.0.0.1 or http://localhost, with its port."
            elif scope["method"] not in ONLY_READS and origin is not None and sender.lower() != host.lower():
                refused = "That request came from another site's page."
            else:
                refused = None
            if refused:
                await JSONResponse({"detail": refused}, status_code=403)(scope, receive, send)
                return
        await self.inner(scope, receive, send)


class SizeLimit:
    """Refuse a request larger than an upload can be, without taking it in first.

    An upload is saved to disk as it arrives and only then read and measured, so one far over the
    limit would fill the disk and then the memory before being refused. A request that says it's
    too large is refused before any of it is read, and one that doesn't say is stopped as soon as
    it passes the limit.
    """

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        limit = MAX_UPLOAD_BYTES + FORM_BYTES
        stated = Headers(scope=scope).get("content-length", "")
        if stated.isdigit() and int(stated) > limit:
            await JSONResponse({"detail": TOO_LARGE}, status_code=413)(scope, receive, send)
            return
        taken = 0

        async def receive_within_limit() -> Message:
            nonlocal taken
            message = await receive()
            taken += len(message.get("body", b""))
            if taken > limit:
                raise HTTPException(413, TOO_LARGE)
            return message

        await self.inner(scope, receive_within_limit, send)


app = FastAPI(title="xrnsheet", lifespan=lifespan)
app.add_middleware(SizeLimit)
app.add_middleware(OwnPagesOnly)
one_at_a_time = threading.Semaphore(1)  # Recognition is memory hungry.
# The page being read now, and whether the app is stopping, so that the page can stop with it.
reading: set[subprocess.Popen] = set()
reading_lock = threading.Lock()
stopping = threading.Event()
# Edits read the latest version and write the next one, so two at once would lose one of them.
edit_lock = threading.RLock()


class ChordEdit(BaseModel):
    text: str


class ChordAdd(BaseModel):
    note: str
    text: str


class NoteEdit(BaseModel):
    action: str
    type: str = "quarter"
    dots: int = 0
    step: str | None = None
    line: int | None = None  # A place on the staff, in steps down from the top line.
    voice: int | None = None  # Which voice of the staff to write in, counting from 1.
    midi: int | None = None  # A key pressed on a MIDI keyboard.
    steps: int = 0
    actual: int = 3  # How many notes a tuplet holds.
    interval: int = 0  # A third is 3. Below the note if it's negative.


class GroupEdit(BaseModel):
    action: str
    notes: list[str]


class MarkEdit(BaseModel):
    action: str
    name: str = ""
    notes: list[str] = []
    text: str = ""
    id: str | None = None
    unit: str = "quarter"
    dots: int = 0
    on: bool = True


class TransposeEdit(BaseModel):
    interval: str | None = None
    direction: str = "up"
    key: int | None = None
    first: str | None = None
    last: str | None = None


class VoiceEdit(BaseModel):
    notes: list[str]
    voice: int


class PartEdit(BaseModel):
    action: str
    name: str = ""
    names: list[str] = []  # Several to add at once, as one edit.
    note: str | None = None
    index: int | None = None  # Which instrument to remove, counted from the top.


class Stretch(BaseModel):
    first: str
    last: str


class CopiedEvent(BaseModel):
    type: str
    dots: int = 0
    pitches: list[tuple[str, int, int]] = []
    tie: bool = False
    triplet: int | None = None
    actual: int = 3
    chord: str | None = None
    lyric: dict | None = None
    marks: list[str] = []
    dynamic: str | None = None
    slur: list[int] = []


class Paste(BaseModel):
    at: str
    events: list[CopiedEvent]


class Lyric(BaseModel):
    text: str
    joined: bool | None = False
    verse: int = 1


class Title(BaseModel):
    title: str = ""
    composer: str = ""


SIXTEENTHS_PER_QUARTER = 4


class BarEdit(BaseModel):
    action: str
    note: str | None = None
    last: str | None = None
    fifths: int = 0
    beats: int = 4
    beat_type: int = 4
    number: int = 0
    clef: str = ""
    count: int = 0


class BlankScore(BaseModel):
    fifths: int = 0
    beats: int = 4
    beat_type: int = 4
    clef: str = "treble"
    measures: int = 16
    more: list[str] = []  # Instruments below the first.
    title: str = ""
    composer: str = ""
    tempo: str = ""
    pickup: int = 0  # In sixteenth notes.


class CheckedMark(BaseModel):
    checked: bool


def job_folder(score_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", score_id) is None or not (JOB_DIR / score_id).is_dir():
        raise HTTPException(404, "No such score")
    return JOB_DIR / score_id


def write_whole(path: Path, text: str) -> None:
    """Write a file whole or not at all, so a save that fails can't leave a broken file in its place.

    It's written beside its place first, then renamed there, which can't happen halfway.
    """
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    try:
        with open(temporary, "x", encoding="utf-8") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_status(folder: Path, status: str, message: str = "") -> None:
    write_whole(folder / "status.json", json.dumps({"status": status, "message": message}))


def read_status(folder: Path) -> dict:
    return json.loads((folder / "status.json").read_text(encoding="utf-8"))


def fail_interrupted_jobs() -> None:
    """Mark sheets that were still being read when the app last stopped, as nothing reads them now.

    Otherwise a page waiting for one would wait forever.
    """
    for folder in JOB_DIR.glob("*") if JOB_DIR.exists() else []:
        try:
            if read_status(folder)["status"] == "processing":
                write_status(folder, "failed", INTERRUPTED)
        except (OSError, ValueError, TypeError, KeyError):
            continue


def version_number(path: Path) -> int:
    return int(path.stem.split("_")[1])


def versions(folder: Path) -> list[Path]:
    # In number order, because by name version_10000 would come before version_9999.
    return sorted(folder.glob("version_*.musicxml"), key=version_number)


def current_root(folder: Path) -> ET.Element:
    with edit_lock:  # An undo moves the latest version away, so it can't land between finding it and reading it.
        return ET.parse(versions(folder)[-1]).getroot()


def undone(folder: Path) -> list[Path]:
    return sorted((folder / "undone").glob("version_*.musicxml"), key=version_number)


def save_version(folder: Path, root: ET.Element) -> None:
    """Save the next version. A new edit starts a new future, so anything undone is dropped."""
    number = version_number(versions(folder)[-1]) + 1 if versions(folder) else 0
    write_whole(folder / f"version_{number:04d}.musicxml", to_text(root))
    shutil.rmtree(folder / "undone", ignore_errors=True)


def pdf_to_images(path: Path) -> tuple[list[Path], str]:
    import pypdfium2

    document = pypdfium2.PdfDocument(str(path))
    folder = path.parent / "pages"
    folder.mkdir(exist_ok=True)
    images = []
    for index in range(min(len(document), MAX_PDF_PAGES)):
        image_path = folder / f"page_{index + 1:03d}.png"
        page = document[index]
        scale = min(250 / 72, LONGEST_PDF_PAGE / max(*page.get_size(), 1))  # A PDF counts 72 points an inch.
        page.render(scale=scale).to_pil().convert("RGB").save(image_path)
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
        # The app stops a page that takes too long, but if the app is killed outright nothing would,
        # so the system ends it a minute after the app would have.
        limits = ["-p", f"MemoryMax={MEMORY_LIMIT}", "-p", f"RuntimeMaxSec={PAGE_TIMEOUT_SECONDS + 60}"]
        command = ["systemd-run", "--user", "--scope", "-q", *limits, *command]
    with reading_lock:  # So that a page can't start just as the app stops, and be missed.
        if stopping.is_set():
            return INTERRUPTED
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        reading.add(process)
    try:
        _, errors = process.communicate(timeout=PAGE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return "Recognition took too long."
    finally:
        with reading_lock:
            reading.discard(process)
    if process.returncode == 0 and list(out_dir.glob("*.musicxml")):
        return None
    print(errors[-2000:], file=sys.stderr)
    return NO_STAVES if "No staffs found" in errors or "No noteheads" in errors else "Recognition failed."


def stop_reading() -> None:
    """Stop the page being read, because the app is stopping.

    Its process would otherwise read on after the app had gone, holding its memory, with nothing
    left to time it out or to take its answer.
    """
    with reading_lock:
        stopping.set()
        for process in reading:
            process.kill()


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
                if stopping.is_set():
                    return  # It's left as still being read, which the next start reports as interrupted.
                if reason is None:
                    read.append(number)
                else:
                    failed[number] = reason
            if not read:
                write_status(folder, "failed", next(iter(failed.values())))
                return
            root = combine_pages(folder, read)
            edit.ensure_note_ids(root)
            structure.ensure_measure_ids(root)
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
        raise HTTPException(415, "Please upload a photo, a PDF or a MusicXML file.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, TOO_LARGE)
    if suffix in SCORE_TYPES:
        return open_score_file(data, suffix)
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


def open_score_file(data: bytes, suffix: str) -> dict:
    """Open a score written elsewhere. It's kept like one written here, with no photo behind it."""
    try:
        root = read_score_file(data, suffix)
        fit_divisions(root)
        edit.ensure_note_ids(root)
        marks.ensure_mark_ids(root)
        structure.ensure_measure_ids(root)
        playback(root)
        render_pages(to_text(root))
    except (ValueError, TypeError, AttributeError, ZeroDivisionError) as error:
        message = str(error) if isinstance(error, UnreadableScore) else "That score couldn't be opened."
        raise HTTPException(415, message) from error
    score_id = uuid.uuid4().hex
    folder = JOB_DIR / score_id
    folder.mkdir(parents=True)
    save_version(folder, root)
    write_status(folder, "ready")
    return {"id": score_id}


def read_chord_file(folder: Path) -> list[dict]:
    path = folder / "recognized" / "chords.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def source_pages(folder: Path) -> list[int]:
    path = folder / "recognized" / "pages.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def describe(folder: Path, status: dict) -> dict:
    root = current_root(folder)
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    read_info: dict[tuple[int, float], dict] = {}
    for item in read_chord_file(folder):
        read_info.setdefault((item["measure"], round(float(item["onset"]), 3)), item)  # Only the first was placed.
    chords = []
    for chord in edit.list_chords(root):
        found = read_info.get((chord["origin"], chord["onset"])) if chord["part"] == 0 else None
        unchanged = found is not None and found["chord"] == chord["text"]
        chords.append(chord | {
            "confidence": found["confidence"] if unchanged else None,
            "box": found["box"] if found else None,
            "page": found["page"] if found else None,
        })
    return status | {
        "chords": chords,
        "canUndo": len(versions(folder)) > 1,
        "canRedo": bool(undone(folder)),
        "pages": render_pages(to_text(root)),
        "sourcePages": source_pages(folder),
        "checked": (folder / "checked.json").exists(),
        "blank": not (folder / "recognized").exists(),
        "barChecks": structure.check_measures(root),
        "marks": marks.list_marks(root),
        "parts": structure.part_names(root),
        "partStaves": structure.part_staves(root),
        "barsPerLine": structure.bars_per_line(root),
    } | structure.title_of(root)


@app.post("/api/scores/blank")
def create_blank_score(body: BlankScore) -> dict:
    """Start an empty score to write in, with no photo behind it."""
    try:
        root = structure.blank_score(body.fifths, body.beats, body.beat_type, body.clef, body.measures)
        for name in body.more:
            structure.add_part(root, name)
        edit.ensure_note_ids(root)
        if body.title.strip() or body.composer.strip():
            structure.set_title(root, body.title, body.composer)
        if body.pickup:
            structure.set_pickup(root, Fraction(body.pickup, SIXTEENTHS_PER_QUARTER))
            edit.ensure_note_ids(root)
        if body.tempo.strip():
            marks.set_tempo(root, root.find("part/measure/note").get("id"), body.tempo)
    except edit.EditError as error:
        raise HTTPException(422, str(error)) from error
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    score_id = uuid.uuid4().hex
    folder = JOB_DIR / score_id
    folder.mkdir(parents=True)
    save_version(folder, root)
    write_status(folder, "ready")
    return {"id": score_id}


@app.get("/api/instruments")
def list_instruments() -> list[dict]:
    return structure.instrument_list()


@app.get("/api/scores")
def list_scores() -> list[dict]:
    """List finished sheets, newest first, so one can be reopened after closing the page.

    A sheet whose files can't be read is left out, rather than taking every other sheet with it.
    """
    found = []
    for folder in JOB_DIR.glob("*") if JOB_DIR.exists() else []:
        try:
            if not (folder / "status.json").exists() or read_status(folder)["status"] != "ready":
                continue
            found.append({
                "id": folder.name,
                "saved": versions(folder)[-1].stat().st_mtime,
                "pages": len(source_pages(folder)),
                "chords": len(read_chord_file(folder)),
                "checked": (folder / "checked.json").exists(),
                "blank": not (folder / "recognized").exists(),
                "title": structure.title_of(current_root(folder))["title"],
            })
        except (OSError, ValueError, TypeError, KeyError, IndexError, ET.ParseError) as error:
            print(f"left an unreadable sheet out of the list, {folder.name}: {error!r}", file=sys.stderr)
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
    if not (folder / "recognized").exists():
        raise HTTPException(409, "Only recognized sheets can be checked")
    path = folder / "checked.json"
    if body.checked:
        path.write_text(json.dumps({"checked_at": time.time()}), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)
    return describe(folder, read_status(folder))


def apply_edit(score_id: str, change: Callable[[ET.Element], dict | str | None]) -> dict:
    """Run one edit and save it. Anything the edit returns is passed on to the page."""
    folder = job_folder(score_id)
    with edit_lock:
        root = current_root(folder)
        structure.ensure_measure_ids(root)
        edit.ensure_note_ids(root)
        marks.ensure_mark_ids(root)
        try:
            extra = change(root)
        except edit.EditError as error:
            raise HTTPException(422, str(error)) from error
        edit.ensure_note_ids(root)
        marks.ensure_mark_ids(root)
        save_version(folder, root)
        return describe(folder, read_status(folder)) | (extra or {})


@app.post("/api/scores/{score_id}/notes/{note_id}")
def change_note(score_id: str, note_id: str, body: NoteEdit) -> dict:
    actions: dict[str, Callable[[ET.Element], None]] = {
        "up": lambda root: edit.move_note(root, note_id, 1),
        "down": lambda root: edit.move_note(root, note_id, -1),
        "octaveUp": lambda root: edit.move_note(root, note_id, 7),
        "octaveDown": lambda root: edit.move_note(root, note_id, -7),
        "sharp": lambda root: edit.set_accidental(root, note_id, 1),
        "flat": lambda root: edit.set_accidental(root, note_id, -1),
        "natural": lambda root: edit.set_accidental(root, note_id, 0),
        "delete": lambda root: edit.delete_note(root, note_id),
        "clear": lambda root: edit.clear_marks(root, note_id),
        "length": lambda root: edit.change_length(root, note_id, body.type, body.dots),
        "dot": lambda root: edit.toggle_dot(root, note_id),
        "write": lambda root: edit.write_note(root, note_id, body.type, body.dots, body.step, body.line, body.voice, body.midi),
        "moveBy": lambda root: edit.move_note(root, note_id, body.steps),
        "semitoneUp": lambda root: edit.move_semitone(root, note_id, 1),
        "semitoneDown": lambda root: edit.move_semitone(root, note_id, -1),
        "repitch": lambda root: edit.set_step(root, note_id, body.step or ""),
        "double": lambda root: edit.scale_length(root, note_id, True),
        "halve": lambda root: edit.scale_length(root, note_id, False),
        "dotLonger": lambda root: edit.scale_length(root, note_id, True, by_dot=True),
        "dotShorter": lambda root: edit.scale_length(root, note_id, False, by_dot=True),
        "interval": lambda root: {"written": edit.add_interval(root, note_id, body.interval)},
        "respell": lambda root: edit.respell_note(root, note_id),
        "flip": lambda root: edit.flip_stem(root, note_id),
        "stack": lambda root: {"written": edit.add_pitch(root, note_id, body.step or "", body.line, body.midi)},
        "tuplet": lambda root: edit.toggle_tuplet(root, note_id, edit.length_of(body.type, body.dots), body.actual),
        "tupletHere": lambda root: edit.toggle_tuplet(root, note_id, actual=body.actual),
        "tie": lambda root: edit.toggle_tie(root, note_id),
        "close": lambda root: {"cursor": edit.close_gap(root, note_id)},
        "insert": lambda root: {"cursor": edit.insert_rest(root, note_id, body.type, body.dots)},
    }
    if body.action not in actions:
        raise HTTPException(422, "Unknown note action")
    return apply_edit(score_id, actions[body.action])


GROUP_ACTIONS: dict[str, Callable[[ET.Element, str], None]] = {
    "up": lambda root, note: edit.move_note(root, note, 1),
    "down": lambda root, note: edit.move_note(root, note, -1),
    "octaveUp": lambda root, note: edit.move_note(root, note, 7),
    "octaveDown": lambda root, note: edit.move_note(root, note, -7),
    "semitoneUp": lambda root, note: edit.move_semitone(root, note, 1),
    "semitoneDown": lambda root, note: edit.move_semitone(root, note, -1),
    "sharp": lambda root, note: edit.set_accidental(root, note, 1),
    "flat": lambda root, note: edit.set_accidental(root, note, -1),
    "natural": lambda root, note: edit.set_accidental(root, note, 0),
    "delete": edit.delete_note,
    "clear": edit.clear_marks,
    "respell": edit.respell_note,
    "flip": edit.flip_stem,
}


@app.post("/api/scores/{score_id}/group")
def change_group(score_id: str, body: GroupEdit) -> dict:
    """Apply one action to several notes as one edit, so one undo takes it all back."""
    if body.action not in GROUP_ACTIONS:
        raise HTTPException(422, "That can't be done to a selection")

    def change(root: ET.Element) -> None:
        done = 0
        with indexed(root):  # Every chosen note is looked up, so they are found in one pass, not one search each.
            # Respelling a note respells the notes tied to it, so a second one of those would undo it.
            chosen = edit.one_note_per_tie(root, body.notes) if body.action == "respell" else body.notes
            for note_id in chosen:
                try:
                    GROUP_ACTIONS[body.action](root, note_id)
                    done += 1
                except edit.EditError:
                    pass  # Rests can't move, and most notes have no marks. The rest of the selection can.
        if not done:
            raise edit.EditError("That doesn't apply to anything selected.")

    return apply_edit(score_id, change)


def first_and_last(body: MarkEdit) -> tuple[str, str]:
    if not body.notes:
        raise edit.EditError("Choose a note first.")
    return body.notes[0], body.notes[-1]


MARK_ACTIONS: dict[str, Callable[[ET.Element, MarkEdit], dict | None]] = {
    "toggle": lambda root, body: marks.toggle_mark(root, body.notes, body.name),
    "slur": lambda root, body: marks.toggle_slur(root, *first_and_last(body)),
    "hairpin": lambda root, body: marks.toggle_hairpin(root, *first_and_last(body), body.name),
    "dynamic": lambda root, body: marks.set_dynamic(root, first_and_last(body)[0], body.name),
    "tempo": lambda root, body: marks.set_tempo(root, first_and_last(body)[0], body.text, body.unit, body.dots),
    "text": lambda root, body: marks.add_text(root, first_and_last(body)[0], body.text, body.name or "staff"),
    "rehearsal": lambda root, body: marks.add_rehearsal(root, first_and_last(body)[0], body.text),
    "swing": lambda root, body: marks.set_swing(root, first_and_last(body)[0], body.on),
    "jump": lambda root, body: marks.set_jump(root, first_and_last(body)[0], body.name),
    "grace": lambda root, body: {"written": marks.add_grace(root, first_and_last(body)[0], body.on)},
    "remove": lambda root, body: marks.remove_placed(root, body.id or ""),
    "label": lambda root, body: marks.set_label(root, body.id or "", body.text),
}


@app.post("/api/scores/{score_id}/marks")
def change_marks(score_id: str, body: MarkEdit) -> dict:
    if body.action not in MARK_ACTIONS:
        raise HTTPException(422, "Unknown mark")
    return apply_edit(score_id, lambda root: MARK_ACTIONS[body.action](root, body))


@app.post("/api/scores/{score_id}/transpose")
def change_pitch(score_id: str, body: TransposeEdit) -> dict:
    """Transpose a selection if one is given, or else the whole score, key signatures included."""

    def change(root: ET.Element) -> None:
        if body.key is not None:
            transpose.transpose_score_to_key(root, body.key, body.direction)
            return
        interval = transpose.named_interval(body.interval or "", body.direction)
        if body.first:
            transpose.transpose_stretch(root, body.first, body.last or body.first, interval)
        else:
            transpose.transpose_score(root, interval)

    return apply_edit(score_id, change)


@app.post("/api/scores/{score_id}/voice")
def change_voice(score_id: str, body: VoiceEdit) -> dict:
    return apply_edit(score_id, lambda root: edit.move_to_voice(root, body.notes, body.voice))


@app.post("/api/scores/{score_id}/parts")
def change_parts(score_id: str, body: PartEdit) -> dict:
    def change(root: ET.Element) -> None:
        if body.action == "add":
            names = ([body.name] if body.name else []) + body.names
            if not names:
                raise edit.EditError("Choose an instrument to add.")
            for name in names:
                structure.add_part(root, name)
        elif body.action == "remove":
            if body.index is None and not body.note:
                raise edit.EditError("Choose the instrument to remove first.")
            structure.remove_part(root, body.index if body.index is not None else structure.part_of(root, body.note))
        else:
            raise HTTPException(422, "Unknown instrument action")

    return apply_edit(score_id, change)


@app.post("/api/scores/{score_id}/range")
def find_range(score_id: str, body: Stretch) -> dict:
    """List what's between two notes. Nothing is changed, so nothing is saved."""
    try:
        return edit.range_ids(current_root(job_folder(score_id)), body.first, body.last)
    except edit.EditError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/scores/{score_id}/copy")
def copy_notes(score_id: str, body: Stretch) -> dict:
    try:
        return marks.copy_stretch(current_root(job_folder(score_id)), body.first, body.last)
    except edit.EditError as error:
        raise HTTPException(422, str(error)) from error


@app.post("/api/scores/{score_id}/paste")
def paste_notes(score_id: str, body: Paste) -> dict:
    return apply_edit(score_id, lambda root: marks.paste_stretch(root, body.at, [item.model_dump() for item in body.events]))


@app.post("/api/scores/{score_id}/lyrics/{note_id}")
def change_lyric(score_id: str, note_id: str, body: Lyric) -> dict:
    return apply_edit(score_id, lambda root: edit.set_lyric(root, note_id, body.text, body.joined, body.verse))


@app.post("/api/scores/{score_id}/title")
def change_title(score_id: str, body: Title) -> dict:
    return apply_edit(score_id, lambda root: structure.set_title(root, body.title, body.composer))


@app.post("/api/scores/{score_id}/bars")
def change_bars(score_id: str, body: BarEdit) -> dict:
    """Change the score's bars. Each change applies at the bar holding the given note."""

    def change(root: ET.Element) -> None:
        index = structure.bar_of(root, body.note) if body.note else None
        if body.action == "insert":
            structure.insert_measure(root, len(root.find("part").findall("measure")) if index is None else index)
        elif body.action == "delete":
            if index is None:
                raise edit.EditError("Click a note in the bar to delete first.")
            # From the last one back, so the bars still to go keep their numbers.
            for doomed in range(structure.bar_of(root, body.last) if body.last else index, index - 1, -1):
                structure.delete_measure(root, doomed)
        elif body.action == "key":
            structure.set_key(root, index or 0, body.fifths)
        elif body.action == "time":
            structure.set_time(root, index or 0, body.beats, body.beat_type)
        elif body.action == "pickup":
            structure.set_pickup(root, Fraction(body.count, SIXTEENTHS_PER_QUARTER))
        elif body.action in ("forward", "backward"):
            structure.toggle_repeat(root, index or 0, body.action)
        elif body.action == "ending":
            first = index or 0
            structure.set_ending(root, first, structure.bar_of(root, body.last) if body.last else first, body.number)
        elif body.action == "clef":
            if not body.note:
                raise edit.EditError("Click a note in the staff to change first.")
            spot = edit.locate(root, body.note)
            structure.set_clef(root, index or 0, structure.part_of(root, body.note), spot.key[0], body.clef)
        elif body.action == "break":
            structure.toggle_line_break(root, index or 0)
        elif body.action == "barsPerLine":
            structure.set_bars_per_line(root, body.count)
        else:
            raise HTTPException(422, "Unknown bar action")

    return apply_edit(score_id, change)


@app.post("/api/scores/{score_id}/chords/{index}")
def change_chord(score_id: str, index: int, body: ChordEdit) -> dict:
    return apply_edit(score_id, lambda root: edit.set_chord(root, index, body.text))


@app.post("/api/scores/{score_id}/chords")
def add_chord(score_id: str, body: ChordAdd) -> dict:
    return apply_edit(score_id, lambda root: edit.add_chord(root, body.note, body.text))


@app.post("/api/scores/{score_id}/undo")
def undo(score_id: str) -> dict:
    folder = job_folder(score_id)
    with edit_lock:
        saved = versions(folder)
        if len(saved) < 2:
            raise HTTPException(409, "Nothing to undo")
        (folder / "undone").mkdir(exist_ok=True)
        saved[-1].rename(folder / "undone" / saved[-1].name)
        return describe(folder, read_status(folder))


@app.post("/api/scores/{score_id}/redo")
def redo(score_id: str) -> dict:
    folder = job_folder(score_id)
    with edit_lock:
        waiting = undone(folder)
        if not waiting:
            raise HTTPException(409, "Nothing to redo")
        waiting[0].rename(folder / waiting[0].name)
        return describe(folder, read_status(folder))


@app.get("/api/scores/{score_id}/playback")
def get_playback(score_id: str) -> dict:
    return playback(current_root(job_folder(score_id)))


@app.get("/api/scores/{score_id}/score.musicxml")
def download_musicxml(score_id: str) -> Response:
    text = to_text(current_root(job_folder(score_id)))
    headers = {"Content-Disposition": 'attachment; filename="score.musicxml"'}
    return Response(text, media_type="application/vnd.recordare.musicxml+xml", headers=headers)


@app.get("/api/scores/{score_id}/score.mid")
def download_midi(score_id: str) -> Response:
    data = midi_bytes(playback(current_root(job_folder(score_id))))
    return Response(data, media_type="audio/midi", headers={"Content-Disposition": 'attachment; filename="score.mid"'})


@app.get("/api/scores/{score_id}/score.pdf")
def download_pdf(score_id: str, part: int | None = None) -> FileResponse:
    """Print the score, or one player's part."""
    folder = job_folder(score_id)
    root = current_root(folder)
    name = "score.pdf"
    if part is not None:
        try:
            root = structure.only_part(root, part)
        except edit.EditError as error:
            raise HTTPException(404, str(error)) from error
        name = f"part_{part + 1}.pdf"
    path = folder / name
    export_pdf(to_text(root), path)
    return FileResponse(path, media_type="application/pdf", filename=name)


@app.get("/api/scores/{score_id}/thumbnail.svg")
def get_thumbnail(score_id: str) -> FileResponse:
    """Draw a score's first page, for the list of scores. It's kept until the score changes."""
    folder = job_folder(score_id)
    with edit_lock:
        saved = versions(folder)
        if not saved:
            raise HTTPException(404, "This sheet isn't ready yet")
        # An undo followed by a new edit gives a different version the same number, so the time counts too.
        path = folder / f"thumbnail_{version_number(saved[-1])}_{saved[-1].stat().st_mtime_ns}.svg"
        if not path.exists():
            for old in folder.glob("thumbnail_*.svg"):
                old.unlink(missing_ok=True)
            write_whole(path, render_pages(to_text(ET.parse(saved[-1]).getroot()))[0])
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/glyphs.js")
def music_glyphs() -> Response:
    """Serve the shapes the pages are engraved with, so the toolbar and palettes show the same symbols."""
    return Response(glyph_script(), media_type="text/javascript", headers={"Cache-Control": "max-age=86400"})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--notes", choices=["homr", "own"], default="homr")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    os.environ["XRNSHEET_NOTES"] = arguments.notes
    uvicorn.run(app, host="127.0.0.1", port=arguments.port)
