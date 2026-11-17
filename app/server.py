"""Serve the correction screen.

    .venv/bin/python -m app.server

Then open http://127.0.0.1:8000. Each score lives in its own folder in data/jobs/, or in the
folder given with --jobs, with every saved version kept so edits can be undone. Add --notes own
to read notes with our own reader instead of homr.
"""

import argparse
import functools
import hashlib
import json
import os
import re
import shutil
import signal
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
from urllib.parse import quote, urlsplit

import uvicorn
import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chords import corrections
from chords.fonts import ROOT
from pipeline import align, concert, diagrams, edit, fretted, layout, marks, mixer, structure, technique, transpose
from pipeline.engrave import VIEWS, export_pdf, glyph_script, keep_lines, page_size, render_pages, system_starts
from pipeline.merge import merge_pages
from pipeline.midi_file import midi_bytes
from pipeline.musicxml import UnreadableScore, read_measures, read_score_file, to_text
from pipeline.playback import playback
from pipeline.rhythm import fit_divisions, indexed
from pipeline.timeline import places, timeline

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
DRAWINGS_KEPT = 4  # A long score's drawing is a few megabytes, so only the last few are kept.
VERSIONS_KEPT = 100  # The versions an edit leaves to undo to, besides the first (see prune_versions).
NO_STAVES = "No staves were found. Try a sharper, straighter photo."
INTERRUPTED = "The app was closed before this sheet was read. Please open it again."
SOURCE_FILE = "source.json"  # The name of the file a sheet is read from, to call it by until it has a title.
CANCEL_WAIT_SECONDS = 10  # How long a cancel waits for the page being read to stop, which is usually at once.
TRASH = ".trash"  # In the folder of scores: those deleted, kept a while so a delete can be undone.
TRASH_SECONDS = 7 * 24 * 60 * 60
OWN_NAMES = {"127.0.0.1", "localhost"}  # What a browser on this computer calls the app.
ONLY_READS = {"GET", "HEAD", "OPTIONS"}
OPENED_FILE = "opened.json"  # Beside a score opened from a MusicXML file.
LAYOUT_FILE = "layout.json"  # Beside each page read: where its lines of music, barlines and notes are.
ADDED_FILE = "added.json"  # Chords added from the original sheet, with where they were clicked, for their boxes.
FIRST_VERSION_HEAD = 8192  # Where a file names the program that wrote it, near its start.


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    fail_interrupted_jobs()
    clear_page_pictures()
    empty_trash()
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
# Sheets to be read, by id, in the order they came. The first is the one being read: recognition is
# memory hungry, so sheets are read one at a time, and the page shows each its place in the line.
line: list[str] = []
line_changed = threading.Condition()
began: dict[str, float] = {}  # When the sheet being read began, for the page to show how long it has taken.
cancelled: set[str] = set()  # Sheets whose reading is being stopped, to be removed as they leave the line.
# The page being read now, by the folder it's read into, and whether the app is stopping, so that
# the page can stop with it.
reading: dict[subprocess.Popen, Path] = {}
reading_lock = threading.Lock()
stopping = threading.Event()
# Edits read the latest version and write the next one, so two at once would lose one of them.
edit_lock = threading.RLock()


class ChordEdit(BaseModel):
    text: str


class ChordAdd(BaseModel):
    note: str
    text: str


class ChordAt(BaseModel):
    """A chord the reader missed, at a bar numbered as the score was read and a place in it in quarter notes,
    with where on which page of the original it was clicked."""

    measure: int
    onset: float
    text: str
    page: int | None = None
    box: list[float] | None = None


class ChordFrame(BaseModel):
    """A chord diagram of its own: each string's fret from the lowest string up, counted from the capo,
    0 for open and null for not played, and a barre as its fret and the strings it lies across,
    counted from the lowest from 0. No frets gives the chord its usual shape again."""

    frets: list[int | None] | None = None
    barre: list[int] | None = None


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
    string: int | None = None  # Which string plays it, numbered from 1 for the highest.
    fret: int | None = None  # A fret typed for it on the tablature, counted from the capo.


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
    number: int = 0  # A string, or a position, by its number.


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
    transposition: tuple[int, int] | None = None  # How far the copied part sounds from how it's written.
    techniques: list[dict] = []  # Each note's fingering, pluck, string number, bend, harmonic and dead note.
    strings: list[int | None] = []  # Each note's string on a guitar's tablature.
    joins: list[tuple[int, str]] = []  # Hammer-ons and the like to a later copied note: its place and the join.
    words: list[dict] = []  # "pizz." or "arco", a position or a hand at the note: {"kind": "position", "number": 3}.
    lines: list[tuple[int, str]] = []  # A P.M., let ring or sul line from the note: the copied note it ends on, and its words.


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
    symbol: str = ""  # "common" or "cut", for a time signature drawn as a sign.
    style: str = ""  # How a barline is drawn: single, double, dashed, final, heavy, heavyDouble or dotted.
    number: int = 0
    clef: str = ""
    count: int = 0


class BlankScore(BaseModel):
    fifths: int = 0
    beats: int = 4
    beat_type: int = 4
    symbol: str = ""  # "common" or "cut".
    clef: str = "treble"
    measures: int = 16
    more: list[str] = []  # Instruments below the first.
    title: str = ""
    composer: str = ""
    tempo: str = ""
    pickup: int = 0  # In sixteenth notes.


class CheckedMark(BaseModel):
    checked: bool


class ConcertPitch(BaseModel):
    on: bool


class PageLayout(BaseModel):
    """A page in millimetres, and the room between staves and systems in staff spaces."""

    width: float = 210.0
    height: float = 297.0
    top: float = 15.0
    bottom: float = 15.0
    left: float = 15.0
    right: float = 15.0
    space: float = 1.75
    staves: float = 6.0
    systems: float = 6.0


class ViewChoice(BaseModel):
    view: str  # "page", or "horizontal" or "vertical" for MuseScore's continuous views.


class StringsEdit(BaseModel):
    """A fretted instrument's tuning, capo and tablature. Whatever is left out stays as it is."""

    part: int = 0  # Counted from the top.
    tuning: str | None = None  # A tuning's name, as "standard", "dropD" or "DADGAD".
    strings: list[str] | None = None  # Or each string's sound, from the lowest string up, as ["D2", "A2", ...].
    capo: int | None = None
    tab: bool | None = None  # Show a tablature staff linked under the standard staff.


class MixerChannel(BaseModel):
    volume: float = 0.0  # In decibels, 0 being the instrument's usual level.
    pan: float = 0.0  # From -100, hard left, to 100, hard right.
    mute: bool = False
    solo: bool = False


class MixerSettings(BaseModel):
    parts: dict[str, MixerChannel] = {}  # By the part's id.
    chords: MixerChannel = MixerChannel()
    metronome: MixerChannel = MixerChannel()
    master: MixerChannel = MixerChannel()


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


def save_version(folder: Path, root: ET.Element) -> str:
    """Save the next version, and return it as it was written. A new edit starts a new future, so anything undone is dropped."""
    number = version_number(versions(folder)[-1]) + 1 if versions(folder) else 0
    text = to_text(root)
    write_whole(folder / f"version_{number:04d}.musicxml", text)
    shutil.rmtree(folder / "undone", ignore_errors=True)
    prune_versions(folder)
    return text


def prune_versions(folder: Path) -> None:
    """Keep the last VERSIONS_KEPT versions to undo to, and the first, and delete those between.

    Every edit saves the whole score again, so a long session on a dense score left hundreds of
    megabytes. The first is kept whatever its age: for a sheet read from a photo it's the reading as
    the reader made it, which the real-world benchmark compares the corrected score with, and for an
    older score opened from a file it's how the library knows where it came from. So undo goes back
    through the last hundred edits, and then to the score as it began.
    """
    saved = versions(folder)
    for old in saved[1:-VERSIONS_KEPT]:
        old.unlink(missing_ok=True)


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
        reading[process] = out_dir
    try:
        _, errors = process.communicate(timeout=PAGE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        return "Recognition took too long."
    finally:
        with reading_lock:
            reading.pop(process, None)
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
    with line_changed:
        line_changed.notify_all()  # Sheets waiting their turn stop waiting, and are reported at the next start.


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


def given_up(folder: Path) -> bool:
    """Say whether a sheet's reading is to stop: the app is stopping, or someone cancelled it."""
    return stopping.is_set() or folder.name in cancelled


def recognize_job(folder: Path, upload: Path) -> None:
    try:
        images, note = pdf_to_images(upload) if upload.suffix.lower() == ".pdf" else ([upload], "")
        if not images:
            write_status(folder, "failed", "That PDF has no pages.")
            return
        read, failed = [], {}
        for number, image in enumerate(images, start=1):
            if given_up(folder):
                return
            if len(images) > 1:
                write_status(folder, "processing", f"Reading page {number} of {len(images)}.")
            reason = recognize_page(image, folder / "recognized" / f"page_{number:03d}")
            if given_up(folder):
                return  # Stopping, it's left as still being read, which the next start reports as interrupted.
            if reason is None:
                read.append(number)
            else:
                failed[number] = reason
        if not read:
            write_status(folder, "failed", next(iter(failed.values())))
            return
        root = combine_pages(folder, read)
        name_after_file(folder, root)
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
    finally:
        # A PDF's pages, drawn at 250 dots an inch, are only for the reader: what's shown beside the
        # score is the page.jpg it keeps of each, and Try again draws them afresh.
        shutil.rmtree(folder / "pages", ignore_errors=True)


def clear_page_pictures() -> None:
    """Remove the pictures of PDF pages that sheets read before they were removed once read left behind.

    Nothing is being read as the app starts, and nothing else looks at them.
    """
    for pictures in JOB_DIR.glob("*/pages") if JOB_DIR.exists() else []:
        shutil.rmtree(pictures, ignore_errors=True)


def name_after_file(folder: Path, root: ET.Element) -> None:
    """Call a sheet read with no title after the file it was read from, rather than "Untitled score".

    A title the reading found stays, and one typed later replaces this as it would any other. Like a
    title recognition guesses, it's the work's title only, so it isn't printed until it's saved.
    """
    title = title_from_name(source_of(folder)["name"] or "")
    if structure.title_of(root)["title"] or not title:
        return
    work = root.find("work")
    if work is None:
        work = ET.Element("work")
        root.insert(0, work)
    found = work.find("work-title")
    if found is None:
        found = ET.SubElement(work, "work-title")
    found.text = title


def queue_reading(folder: Path, upload: Path) -> None:
    """Put a sheet at the end of the line, to be read when its turn comes."""
    with line_changed:
        line.append(folder.name)
    threading.Thread(target=read_in_turn, args=(folder, upload), daemon=True).start()


def read_in_turn(folder: Path, upload: Path) -> None:
    """Wait for the sheets ahead to be read, then read this one, and give the next its turn.

    A sheet cancelled while it waits has left the line already, with its folder. One cancelled while
    it's read is removed here, once its reader has stopped, so nothing is still writing into it.
    """
    name = folder.name
    with line_changed:
        line_changed.wait_for(lambda: name not in line or line[0] == name or stopping.is_set())
        if name not in line or stopping.is_set():
            return
        began[name] = time.time()
    try:
        recognize_job(folder, upload)
    finally:
        with line_changed:
            line.remove(name)
            began.pop(name, None)
            if name in cancelled:
                cancelled.discard(name)
                shutil.rmtree(folder, ignore_errors=True)
            line_changed.notify_all()


def source_of(folder: Path) -> dict:
    """Say what a sheet was read from: the file's name, if it was kept, and whether it was a PDF."""
    try:
        name = json.loads((folder / SOURCE_FILE).read_text(encoding="utf-8")).get("name")
    except (OSError, ValueError, AttributeError):
        name = None
    return {"name": name if isinstance(name, str) and name else None, "pdf": (folder / "upload.pdf").exists()}


# Words a camera, phone, scanner or app names its pictures with, as in "IMG_2034.jpeg", "scan0001.pdf",
# "photo 3.png" or "WhatsApp Image 2024-11-02 at 15.30.12.jpeg". A file named with only these and
# numbers says nothing of its music, so it gives a sheet no title.
CAMERA_WORDS = {
    "img", "image", "images", "photo", "photos", "pic", "picture", "pxl", "dsc", "dscn", "dscf", "mvimg", "mp", "wa", "fb",
    "screenshot", "screen", "shot", "scan", "scanned", "scanner", "camscanner", "adobe", "whatsapp", "received",
    "document", "doc", "file", "page", "untitled", "unnamed", "download", "upload", "copy", "new", "at",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "sheet", "score", "music",
}


def title_from_name(name: str) -> str:
    """Make a title from the name of the file a sheet was read from: "Autumn leaves" from
    "autumn_leaves (2).jpg", but none from a camera's or scanner's name for it, such as "IMG_2034.jpeg".

    The number a browser or file manager adds to a second copy goes, as do underscores, and hyphens in
    a name with no spaces. A name all in small letters starts with a capital; any other is left as typed.
    """
    words = re.sub(r"(\s*\(\d+\)|[\s_-]+copy(\s*\d+)?)$", "", Path(name).stem, flags=re.IGNORECASE).replace("_", " ")
    if " " not in words:
        words = words.replace("-", " ")
    words = " ".join(words.split())[: structure.MOST_TITLE_LETTERS]
    if all(word in CAMERA_WORDS for word in re.findall(r"[^\W\d_]+", words.lower())):
        return ""
    return words[:1].upper() + words[1:] if words == words.lower() else words


def reading_state(folder: Path) -> dict:
    """Say how a sheet still to be read is getting on: how many sheets are ahead of it, and how long it has been read for."""
    with line_changed:
        ahead = line.index(folder.name) if folder.name in line else None
        started = began.get(folder.name)
    return {"ahead": ahead, "elapsed": round(time.time() - started, 1) if ahead == 0 and started else None}


def unfinished(folder: Path, status: dict) -> dict:
    """Describe a sheet that isn't ready: being read, waiting its turn, or failed, with why."""
    return status | source_of(folder) | (reading_state(folder) if status["status"] == "processing" else {})


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
    write_whole(folder / SOURCE_FILE, json.dumps({"name": Path(file.filename or "").name[:200]}))
    write_status(folder, "processing")
    queue_reading(folder, upload)
    return {"id": score_id}


def open_score_file(data: bytes, suffix: str) -> dict:
    """Open a score written elsewhere. It's kept like one written here, with no photo behind it."""
    try:
        root = read_score_file(data, suffix)
        technique.keep_cross_staff(root)  # A note drawn on the other staff stays in its own staff's voice.
        fit_divisions(root)
        edit.ensure_note_ids(root)
        fretted.sync(root)  # A TAB staff from elsewhere is linked to its standard staff, keeping its frets.
        marks.ensure_mark_ids(root)
        structure.ensure_measure_ids(root)
        layout.keep_imported(root)  # On the page its file sets out, as MuseScore opens it.
        diagrams.keep_imported(root)  # With its chord diagrams, if it has them.
        playback(root)
        render_pages(to_text(root))
    except (ValueError, TypeError, AttributeError, ZeroDivisionError) as error:
        message = str(error) if isinstance(error, UnreadableScore) else "That score couldn't be opened."
        raise HTTPException(415, message) from error
    score_id = uuid.uuid4().hex
    folder = JOB_DIR / score_id
    folder.mkdir(parents=True)
    save_version(folder, root)
    write_whole(folder / OPENED_FILE, json.dumps({"type": suffix}))  # So the library can say where it came from.
    if (levels := mixer.read_from_score(root)) is not None:
        save_mixer(folder, levels, root)  # A file's instruments keep the volume and pan it gave them.
    write_status(folder, "ready")
    return {"id": score_id}


def opened_from_file(folder: Path) -> bool:
    """Say whether a score was opened from a file, rather than begun here or read from a photo.

    Opening one leaves a note of it beside the score. One opened before that was kept is known by
    its first version, which names the program that wrote it, as a score begun here never does.
    """
    if (folder / OPENED_FILE).exists():
        return True
    if (folder / "recognized").exists() or not (found := versions(folder)):
        return False
    with open(found[0], encoding="utf-8") as file:
        return "<encoding>" in file.read(FIRST_VERSION_HEAD)


def read_chord_file(folder: Path) -> list[dict]:
    path = folder / "recognized" / "chords.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def added_chords(folder: Path) -> list[dict]:
    """The chords added from the original sheet, kept apart from those the reader found, which are what
    its corrections are learnt from."""
    try:
        found = json.loads((folder / "recognized" / ADDED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return found if isinstance(found, list) else []


def source_pages(folder: Path) -> list[int]:
    path = folder / "recognized" / "pages.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def part_ids(root: ET.Element) -> list[str]:
    return [part.get("id") for part in root.findall("part")]


def saved_mixer(folder: Path) -> dict | None:
    path = folder / "mixer.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except ValueError:
        return None  # A mixer that can't be read is the usual one again, and the music is untouched.


def mixer_of(folder: Path, root: ET.Element) -> dict:
    return mixer.settled(saved_mixer(folder), part_ids(root))


def save_mixer(folder: Path, settings: dict | None, root: ET.Element) -> dict:
    settled = mixer.settled(settings, part_ids(root))
    write_whole(folder / "mixer.json", json.dumps(settled))
    return settled


def view_of(folder: Path) -> str:
    """Return how the score is shown: as pages, or one of MuseScore's continuous views."""
    try:
        found = json.loads((folder / "view.json").read_text(encoding="utf-8")).get("view")
    except (OSError, ValueError, AttributeError):
        return "page"
    return found if found in VIEWS else "page"


@functools.lru_cache(maxsize=DRAWINGS_KEPT)
def drawn_pages(musicxml: str, view: str) -> tuple[str, ...]:
    """Draw a score's pages, keeping the last few drawings made.

    Opening a score again, undoing and redoing, switching a view back, and the library's picture of
    the score being edited each ask for a drawing that has just been made, and drawing takes longer
    than everything else an edit does put together.
    """
    return tuple(render_pages(musicxml, view=view))


LINES_FILE = "lines.json"


def lines_asked_for(root: ET.Element) -> str:
    """What a score's lines are laid out under. A new page, spacing, measures per line or line break lays them out afresh."""
    first = root.find("part")
    asked = [measure.get("id") for measure in (first.findall("measure") if first is not None else []) if any(found.get("new-system") == "yes" and (found.get("id") or "").startswith(structure.BREAK_PREFIX) for found in measure.findall("print"))]
    return json.dumps([layout.layout_of(root), structure.bars_per_line(root), asked], sort_keys=True)


def kept_lines(folder: Path) -> dict:
    try:
        found = json.loads((folder / LINES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def remembered_starts(folder: Path, shown: ET.Element, view: str) -> list[str]:
    """The measures the lines started with when the score was last drawn this way, if they still count."""
    if view == "horizontal" or structure.bars_per_line(shown):
        return []
    memory = kept_lines(folder).get(view)
    if not isinstance(memory, dict) or memory.get("asked") != lines_asked_for(shown):
        return []
    there = {measure.get("id") for measure in shown.find("part").findall("measure")}
    return [start for start in memory.get("starts", []) if start in there]


def with_lines(text: str, starts: list[str]) -> str:
    if not starts:
        return text
    kept = ET.fromstring(text)
    keep_lines(kept, starts)
    return to_text(kept)


def laid_out(folder: Path, shown: ET.Element, text: str, view: str) -> list[str]:
    """Draw a score's pages with each line holding the measures it held when last drawn.

    Left to itself the engraver fits as many measures on a line as it can, so a note written in one
    measure moved every measure after it from line to line, and whatever was aimed at next had gone.
    A line now keeps its measures until one of them no longer fits, and from there on the lines are
    laid out afresh. They're laid out afresh too when the page, the spacing, the measures per line or
    a line break asked for change. Which measures start each line is kept beside the score, as how
    it was last drawn, so it's no edit to undo.
    """
    starts = remembered_starts(folder, shown, view)
    pages = list(drawn_pages(with_lines(text, starts), view))
    forced = [start for start in system_starts(pages)[1:] if start not in starts] if starts else []
    if forced:
        # A measure no longer fits its line: the lines from there on are laid out afresh.
        order = {measure.get("id"): index for index, measure in enumerate(shown.find("part").findall("measure"))}
        first = min(order.get(start, len(order)) for start in forced)
        pages = list(drawn_pages(with_lines(text, [start for start in starts if order[start] < first]), view))
    if view != "horizontal":
        write_whole(folder / LINES_FILE, json.dumps(kept_lines(folder) | {view: {"asked": lines_asked_for(shown), "starts": system_starts(pages)[1:]}}))
    return pages


def note_details(root: ET.Element) -> dict[str, list]:
    """Say what each note and rest is, by its id, for the inspector to name it as it's shown.

    A note has its step, alteration and octave, then its written type and dots; a rest has no pitch.
    A note the score says something about how to play has a sixth item: its string and fret on a
    guitar's tablature, its finger, string number and the rest (see technique.how_played), which the
    inspector shows and the fretboard and fingerboard place it by.
    Reading them from the score is far quicker than having the engraver write them into the pages.
    """
    played = technique.how_played(root)
    found = {}
    for note in root.iter("note"):
        if not note.get("id"):
            continue
        pitch = note.find("pitch")
        named = [pitch.findtext("step") or "", round(float(pitch.findtext("alter") or 0)), int(pitch.findtext("octave") or 0)] if pitch is not None else ["", 0, 0]
        found[note.get("id")] = [*named, note.findtext("type") or "", len(note.findall("dot"))]
        if note.get("id") in played:
            found[note.get("id")].append(played[note.get("id")])
    return found


def describe(folder: Path, status: dict, saved: str | None = None) -> dict:
    """Say everything the page shows of a score.

    An edit hands over the version it has just saved, so it isn't written out again to be drawn:
    with a long score that took as long as a twentieth of the reply.
    """
    root = ET.fromstring(saved) if saved is not None else current_root(folder)
    text = saved
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    shown = concert.shown(root)  # The same score at concert pitch, if it's shown that way.
    read_info: dict[tuple[int, float], dict] = {}
    for item in [*read_chord_file(folder), *added_chords(folder)]:
        read_info.setdefault((item["measure"], round(float(item["onset"]), 3)), item)  # Only the first was placed.
    chords = []
    shapes = diagrams.chord_diagrams(shown)
    for chord in edit.list_chords(shown):
        found = read_info.get((chord["origin"], chord["onset"])) if chord["part"] == 0 else None
        unchanged = found is not None and found["chord"] == chord["text"]
        chords.append(chord | {
            "confidence": found["confidence"] if unchanged else None,
            "box": found["box"] if found else None,
            "page": found["page"] if found else None,
            "added": bool(found and found.get("added")),
            "diagram": shapes[chord["index"]] if chord["index"] < len(shapes) else None,
        })
    view = view_of(folder)
    pages = laid_out(folder, shown, text if text is not None and shown is root else to_text(shown), view)
    return status | {
        "chords": chords,
        "canUndo": len(versions(folder)) > 1,
        "canRedo": bool(undone(folder)),
        "pages": pages,
        "pageSizes": [page_size(page) for page in pages],
        "view": view,
        "sourcePages": source_pages(folder),
        "checked": (folder / "checked.json").exists(),
        "blank": not (folder / "recognized").exists(),
        "barChecks": structure.check_measures(root),
        "marks": marks.list_marks(root),
        "parts": structure.part_names(root),
        "partStaves": structure.part_staves(root),
        "barsPerLine": structure.bars_per_line(root),
        "concertPitch": concert.shown_at_concert_pitch(root),
        "partIds": part_ids(root),
        "layout": layout.layout_of(root),
        "timeline": timeline(shown),
        "mixer": mixer_of(folder, root),
        "transposing": concert.is_transposing(root),
        "notes": note_details(shown),
        "places": places(shown),
        "ties": [note.get("id") for note in shown.iter("note") if note.get("id") and any(tie.get("type") == "start" for tie in note.findall("tie"))],
        "fretted": [board.describe() if board is not None else None for board in fretted.boards(root)],
        "strings": [list(technique.string_names(root, part)) or None for part in root.findall("part")],
        # How far each part's first bar sounds from how it's written, known before playback is fetched.
        "transpositions": [found[0].semitones if (found := concert.transpositions(part)) else 0 for part in root.findall("part")],
        "fretProblems": fretted.problems(root),
        "rangeProblems": edit.range_problems(root),
        "diagrams": diagrams.shown(root),
    } | structure.title_of(root)


@app.post("/api/scores/blank")
def create_blank_score(body: BlankScore) -> dict:
    """Start an empty score to write in, with no photo behind it."""
    try:
        root = structure.blank_score(body.fifths, body.beats, body.beat_type, body.clef, body.measures, body.symbol)
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
    fretted.sync(root)
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


# Each score's title and composer as the library last read them, by its folder, with the version
# they were read from: its name, when it was written and its size.
listed_titles: dict[Path, tuple[tuple[str, int, int], dict]] = {}


def listed_title(folder: Path) -> dict:
    """A score's title and composer, read from its latest version only when that has changed.

    The library is listed every 2 s while a sheet is read, and after every edit while it's shown,
    and parsing every score's whole MusicXML each time just for these grew with the library. An undo
    followed by a new edit gives a version the number of the one undone, so its time and size count too.
    """
    with edit_lock:  # An undo moves the latest version away, so it can't land between finding it and reading it.
        latest = versions(folder)[-1]
        found = latest.stat()
        key = (latest.name, found.st_mtime_ns, found.st_size)
        kept = listed_titles.get(folder)
        if kept is None or kept[0] != key:
            kept = (key, structure.title_of(ET.parse(latest).getroot()))
            listed_titles[folder] = kept
    return kept[1]


@app.get("/api/scores")
def list_scores() -> list[dict]:
    """List the sheets, newest first, so one can be reopened after closing the page.

    A sheet still being read is listed too, with how it's getting on, so the window can be used for
    something else meanwhile, and so is one that couldn't be read, with why, to read again or remove.
    A sheet whose files can't be read is left out, rather than taking every other sheet with it.
    """
    found = []
    for folder in JOB_DIR.glob("*") if JOB_DIR.exists() else []:
        try:
            if not (folder / "status.json").exists() or folder.name in cancelled:
                continue
            status = read_status(folder)
            if status["status"] != "ready":
                upload = next(folder.glob("upload.*"), folder / "status.json")
                found.append(unfinished(folder, status) | {"id": folder.name, "saved": upload.stat().st_mtime})
                continue
            found.append({
                "status": "ready",
                "name": source_of(folder)["name"],
                "id": folder.name,
                "saved": versions(folder)[-1].stat().st_mtime,
                "pages": len(source_pages(folder)),
                "chords": len(read_chord_file(folder)),
                "checked": (folder / "checked.json").exists(),
                "blank": not (folder / "recognized").exists(),
                "opened": opened_from_file(folder),
            } | listed_title(folder))
        except (OSError, ValueError, TypeError, KeyError, IndexError, ET.ParseError) as error:
            print(f"left an unreadable sheet out of the list, {folder.name}: {error!r}", file=sys.stderr)
    return sorted(found, key=lambda item: item["saved"], reverse=True)


@app.get("/api/scores/{score_id}")
def get_score(score_id: str) -> dict:
    folder = job_folder(score_id)
    if score_id in cancelled:
        raise HTTPException(404, "That sheet's reading was cancelled.")
    status = read_status(folder)
    return describe(folder, status) if status["status"] == "ready" else unfinished(folder, status)


def empty_trash() -> None:
    """Delete for good the scores deleted more than a week ago."""
    for folder in (JOB_DIR / TRASH).glob("*") if (JOB_DIR / TRASH).is_dir() else []:
        try:
            if time.time() - folder.stat().st_mtime > TRASH_SECONDS:
                shutil.rmtree(folder)
        except OSError as error:
            print(f"couldn't empty {folder.name} from the trash: {error!r}", file=sys.stderr)


@app.delete("/api/scores/{score_id}")
def remove_sheet(score_id: str) -> dict:
    """Delete a score, remove a sheet that couldn't be read, or stop reading one and remove it.

    The one way anything leaves the library. A score or a failed sheet goes to the trash, a folder
    beside the scores, where nothing lists it, and comes back from there if the delete is undone;
    it's deleted for good a week later. Deleting only once the page's Undo had gone would lose the
    delete if the window closed first, and would need the page to stay open to finish it.

    A sheet waiting its turn just leaves the line. The one being read has its reader stopped, not
    merely hidden, as it would otherwise hold its memory, and the sheets behind it, for minutes more.
    Neither is kept: there's no reading half done to come back to, and the file is where it was.
    """
    folder = job_folder(score_id)
    with line_changed:
        if score_id in line:
            if line[0] != score_id:
                line.remove(score_id)
                line_changed.notify_all()
                shutil.rmtree(folder, ignore_errors=True)
                return {"removed": score_id, "undo": False}
            cancelled.add(score_id)
            with reading_lock:
                for process, out_dir in reading.items():
                    if folder in out_dir.parents:
                        process.kill()
            # Its reader stops at once and removes the folder as it leaves the line. A PDF still
            # being turned into pictures stops after that, and is listed as gone meanwhile.
            line_changed.wait_for(lambda: score_id not in line, timeout=CANCEL_WAIT_SECONDS)
            return {"removed": score_id, "undo": False}
        with edit_lock:  # An edit to it can't land halfway through its going.
            trash = JOB_DIR / TRASH
            trash.mkdir(exist_ok=True)
            shutil.rmtree(trash / score_id, ignore_errors=True)
            os.replace(folder, trash / score_id)
            os.utime(trash / score_id)  # When it was deleted, which the trash is emptied by.
        # Gathering corrections doesn't look in the trash, but retraining reads what was gathered last,
        # which would still teach a deleted sheet's chords as right. Brought back, it's gathered again.
        corrections.forget(score_id, corrections.OUT_DIR)
    empty_trash()
    return {"removed": score_id, "undo": True}


@app.post("/api/scores/{score_id}/restore")
def restore_sheet(score_id: str) -> dict:
    """Bring a deleted score back from the trash, as it was."""
    kept = JOB_DIR / TRASH / score_id
    if re.fullmatch(r"[0-9a-f]{32}", score_id) is None or not kept.is_dir():
        raise HTTPException(404, "That score is no longer in the trash.")
    with edit_lock:
        if (JOB_DIR / score_id).exists():
            raise HTTPException(409, "That score is in the library already.")
        os.replace(kept, JOB_DIR / score_id)
    return {"id": score_id}


@app.post("/api/scores/{score_id}/retry")
def read_again(score_id: str) -> dict:
    """Read a sheet that couldn't be read once more, from the file it came from."""
    folder = job_folder(score_id)
    with line_changed:
        upload = next(folder.glob("upload.*"), None)
        if score_id in line or read_status(folder)["status"] != "failed" or upload is None:
            raise HTTPException(409, "Only a sheet that couldn't be read can be read again.")
        for leftover in ("recognized", "pages"):
            shutil.rmtree(folder / leftover, ignore_errors=True)
        write_status(folder, "processing")
        queue_reading(folder, upload)
    return {"id": score_id}


@app.get("/api/scores/{score_id}/source/{number}.jpg")
def get_source_page(score_id: str, number: int) -> FileResponse:
    path = job_folder(score_id) / "recognized" / f"page_{number:03d}" / "page.jpg"
    if not path.exists():
        raise HTTPException(404, "No such page")
    return FileResponse(path)


def page_score(folder: Path, number: int) -> Path:
    return next((folder / "recognized" / f"page_{number:03d}").glob("*.musicxml"))


@app.get("/api/scores/{score_id}/source/{number}/layout")
def get_source_layout(score_id: str, number: int) -> dict:
    """Say where each bar and beat is on a page of the original sheet, as the reader found them.

    A chord the reader missed is added by clicking where it is on the page. Its bar and beat are found
    as the reader placed the chords it found (align.place_chord): the line of music, the bar between
    its barlines, and the nearest note or rest in that bar. So the page is told where those are. Bars
    are numbered as the score was read, as chords.json numbers them, which bars added or deleted since
    don't change. A page read before they were kept has none.
    """
    folder = job_folder(score_id)
    pages = source_pages(folder)
    kept = folder / "recognized" / f"page_{number:03d}" / LAYOUT_FILE
    if number not in pages:
        raise HTTPException(404, "No such page")
    if not kept.exists():
        return {"lines": []}
    roots = [ET.parse(page_score(folder, page)).getroot() for page in pages]
    _, offsets = merge_pages(roots)
    first = offsets[pages.index(number)]
    lines = []
    found = json.loads(kept.read_text(encoding="utf-8"))
    for layout_found, measures in zip([align.SystemLayout(**item) for item in found], align.split_into_systems(read_measures(roots[pages.index(number)].find("part")))):
        edges = align.measure_edges(layout_found, measures)
        columns = align.note_columns(layout_found.notes, layout_found.unit)
        bars = []
        for index, measure in enumerate(measures):
            places = align.event_positions(measure, edges[index], edges[index + 1], columns)
            beats = [{"onset": event.onset, "x": float(x)} for event, x in zip(measure.events, places)] or [{"onset": 0.0, "x": float(edges[index])}]
            bars.append({"measure": first + index, "left": float(edges[index]), "right": float(edges[index + 1]), "beats": beats})
        first += len(measures)
        lines.append({"top": layout_found.top, "bottom": layout_found.bottom, "unit": layout_found.unit, "bars": bars})
    return {"lines": lines}


@app.post("/api/scores/{score_id}/chords/at")
def add_chord_at(score_id: str, body: ChordAt) -> dict:
    """Add a chord the reader missed at the bar and beat found from where it was clicked on the original.

    Where it was clicked is kept, apart from what the reader found, so the original draws its box.
    """
    folder = job_folder(score_id)
    with edit_lock:
        if body.page is not None and body.box is not None and len(body.box) == 4 and (folder / "recognized").exists():
            kept = [item for item in added_chords(folder) if (item.get("measure"), item.get("onset")) != (body.measure, body.onset)]
            kept.append({"measure": body.measure, "onset": body.onset, "page": body.page, "box": body.box, "chord": body.text.strip(), "confidence": None, "added": True})
            write_whole(folder / "recognized" / ADDED_FILE, json.dumps(kept))
        return apply_edit(score_id, lambda root: edit.add_chord_at(root, body.measure, Fraction(body.onset).limit_denominator(64), body.text))


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
        # Un-marked, it's no longer learnt from: gathering corrections takes only checked sheets, and
        # what was gathered from it already is taken out, as retraining reads that.
        path.unlink(missing_ok=True)
        corrections.forget(score_id, corrections.OUT_DIR)
    return describe(folder, read_status(folder))


def apply_edit(score_id: str, change: Callable[[ET.Element], dict | str | None], then: Callable[[ET.Element, dict], None] | None = None) -> dict:
    """Run one edit and save it. Anything the edit returns is passed on to the page.

    `then` finishes the edit once the TAB staves have caught up with it, as written for the player
    whatever is shown, and is given what the edit returned to add to.
    """
    folder = job_folder(score_id)
    with edit_lock:
        root = current_root(folder)
        structure.ensure_measure_ids(root)
        edit.ensure_note_ids(root)
        marks.ensure_mark_ids(root)
        try:
            with concert.editing(root):  # At concert pitch the edit is made to what's shown.
                extra = change(root)
        except edit.EditError as error:
            raise HTTPException(422, str(error)) from error
        edit.ensure_note_ids(root)
        marks.ensure_mark_ids(root)
        fretted.sync(root)  # Every TAB staff shows its standard staff's notes as they now are.
        extra = extra if isinstance(extra, dict) else {}
        if then is not None:
            try:
                then(root, extra)
            except edit.EditError as error:
                raise HTTPException(422, str(error)) from error
            marks.ensure_mark_ids(root)
            fretted.sync(root)
        text = save_version(folder, root)
        return describe(folder, read_status(folder), text) | extra


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
        "doubleSharp": lambda root: edit.set_accidental(root, note_id, 2),
        "doubleFlat": lambda root: edit.set_accidental(root, note_id, -2),
        "delete": lambda root: edit.delete_note(root, note_id),
        "clear": lambda root: edit.clear_marks(root, note_id),
        "length": lambda root: edit.change_length(root, note_id, body.type, body.dots),
        "dot": lambda root: edit.toggle_dot(root, note_id),
        "write": lambda root: edit.write_note(root, note_id, body.type, body.dots, body.step, body.line, body.voice, body.midi),
        "moveBy": lambda root: edit.move_note(root, note_id, body.steps),
        "semitoneUp": lambda root: edit.move_semitone(root, note_id, 1),
        "semitoneDown": lambda root: edit.move_semitone(root, note_id, -1),
        "repitch": lambda root: edit.set_step(root, note_id, body.step or ""),
        "pitch": lambda root: edit.set_pitch(root, note_id, body.midi if body.midi is not None else -1),
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
        "string": lambda root: fretted.choose_string(root, note_id, body.string or 0),
        "fret": lambda root: {"onString": fretted.play_at_fret(root, note_id, body.fret if body.fret is not None else -1, body.string)},
        "crossUp": lambda root: technique.cross_staff(root, note_id, -1),
        "crossDown": lambda root: technique.cross_staff(root, note_id, 1),
    }
    if body.action not in actions:
        raise HTTPException(422, "Unknown note action")
    if body.action == "fret":
        # The note has its new pitch once the tablature has caught up, and is then put on its string.
        return apply_edit(score_id, actions["fret"], lambda root, extra: fretted.choose_string(root, note_id, extra.pop("onString")))
    on_string = body.string if body.action in ("write", "stack", "pitch") and body.string else None
    return apply_edit(score_id, actions[body.action], (lambda root, extra: play_on_string(root, extra, on_string)) if on_string else None)


def play_on_string(root: ET.Element, extra: dict, string: int) -> None:
    """Keep the string a note was played on, on the fretboard or the fingerboard below the score.

    A guitar plays it there, on its tablature or, without one, kept on the note, and a bowed
    instrument gets the string's number over it. Should the string be busy, the note keeps the
    one chosen for it, and the page is told why.
    """
    note_id = extra.get("written")
    if not note_id:
        return
    part, _ = edit.context_of(root, edit.find_note(root, note_id))
    board = fretted.boards(root)[root.findall("part").index(part)]
    try:
        if board is not None:
            fretted.choose_string(root, note_id, string)
        elif board is None and technique.family_of(root, part) == technique.BOWED:
            technique.set_string_number(root, [note_id], string)
    except edit.EditError as error:
        extra["message"] = str(error)


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
    "doubleSharp": lambda root, note: edit.set_accidental(root, note, 2),
    "doubleFlat": lambda root, note: edit.set_accidental(root, note, -2),
    "delete": edit.delete_note,
    "clear": edit.clear_marks,
    "respell": edit.respell_note,
    "flip": edit.flip_stem,
    "crossUp": lambda root, note: technique.cross_staff(root, note, -1),
    "crossDown": lambda root, note: technique.cross_staff(root, note, 1),
}


@app.post("/api/scores/{score_id}/group")
def change_group(score_id: str, body: GroupEdit) -> dict:
    """Apply one action to several notes as one edit, so one undo takes it all back."""
    if body.action == "remove":
        return apply_edit(score_id, lambda root: edit.remove_range(root, body.notes))
    if body.action not in GROUP_ACTIONS:
        raise HTTPException(422, "That can't be done to a selection")

    def change(root: ET.Element) -> None:
        done = 0
        with indexed(root):  # Every chosen note is looked up, so they are found in one pass, not one search each.
            # A note and its fret number on a TAB staff are one note, and moved once.
            chosen = list(dict.fromkeys(edit.own_ids(root, body.notes)))
            # Respelling a note respells the notes tied to it, so a second one of those would undo it.
            chosen = edit.one_note_per_tie(root, chosen) if body.action == "respell" else chosen
            if body.action in ("crossUp", "crossDown"):
                # A chord moves to another staff as one, so a second of its notes would move it again.
                events = {}
                for note_id in chosen:
                    try:
                        events.setdefault(technique.event_key(edit.locate(root, note_id)), note_id)
                    except edit.EditError:
                        events.setdefault(note_id, note_id)
                chosen = list(events.values())
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
    "ottava": lambda root, body: marks.toggle_ottava(root, *first_and_last(body), body.name),
    "pedal": lambda root, body: marks.toggle_pedal(root, *first_and_last(body), body.name),
    "dynamic": lambda root, body: marks.set_dynamic(root, first_and_last(body)[0], body.name),
    "tempo": lambda root, body: marks.set_tempo(root, first_and_last(body)[0], body.text, body.unit, body.dots),
    "text": lambda root, body: marks.add_text(root, first_and_last(body)[0], body.text, body.name or "staff"),
    "rehearsal": lambda root, body: marks.add_rehearsal(root, first_and_last(body)[0], body.text),
    "swing": lambda root, body: marks.set_swing(root, first_and_last(body)[0], body.on),
    "jump": lambda root, body: marks.set_jump(root, first_and_last(body)[0], body.name),
    "grace": lambda root, body: {"written": marks.add_grace(root, first_and_last(body)[0], body.on)},
    "remove": lambda root, body: marks.remove_placed(root, body.id or ""),
    "label": lambda root, body: marks.set_label(root, body.id or "", body.text),
    # How guitarists, string players and pianists play: see docs/instrument-api.md.
    "fingering": lambda root, body: technique.set_fingering(root, body.notes, body.text),
    "pluck": lambda root, body: technique.set_pluck(root, body.notes, body.text or body.name),
    "stringNumber": lambda root, body: technique.set_string_number(root, body.notes, body.number),
    "bend": lambda root, body: technique.toggle_bend(root, body.notes, body.name),
    "harmonic": lambda root, body: technique.toggle_harmonic(root, body.notes, body.name),
    "deadNote": lambda root, body: technique.toggle_dead_note(root, body.notes),
    "join": lambda root, body: technique.toggle_pair(root, *first_and_last(body), body.name),
    "vibrato": lambda root, body: technique.toggle_vibrato(root, *first_and_last(body)),
    "line": lambda root, body: technique.toggle_named_line(root, *first_and_last(body), body.name),
    "sul": lambda root, body: technique.toggle_sul(root, *first_and_last(body), body.text or body.name),
    "pizzicato": lambda root, body: technique.set_pizzicato(root, first_and_last(body)[0], body.on),
    "position": lambda root, body: technique.set_position(root, first_and_last(body)[0], body.number),
    "hand": lambda root, body: technique.toggle_hand(root, body.notes, body.name),
}


@app.post("/api/scores/{score_id}/marks")
def change_marks(score_id: str, body: MarkEdit) -> dict:
    if body.action not in MARK_ACTIONS:
        raise HTTPException(422, "Unknown mark")
    return apply_edit(score_id, lambda root: MARK_ACTIONS[body.action](root, body))


@app.post("/api/scores/{score_id}/strings")
def change_strings(score_id: str, body: StringsEdit) -> dict:
    """Tune a fretted instrument, move its capo, or show or hide its tablature. The notes keep their sound."""

    def change(root: ET.Element) -> None:
        _, board = fretted.part_and_board(root, body.part)
        shown_for = diagrams.board_for(root)  # The chord diagrams' guitar, before it changes.
        if body.tab is not None and body.tab != bool(board.tab):
            fretted.set_tab(root, body.part, body.tab)
        if body.tuning is not None or body.strings is not None or body.capo is not None:
            fretted.set_strings(root, body.part, body.tuning, body.strings, body.capo)
        if fretted.boards(root)[body.part].describe() == board.describe():
            raise edit.EditError("That's how this instrument is already set up.")
        diagrams.refit(root, shown_for)

    return apply_edit(score_id, change)


@app.get("/api/tunings")
def list_tunings() -> dict:
    """List the tunings a guitar or a bass can be given by name, each string's sound from the lowest up."""
    return {"guitar": {name: list(strings) for name, strings in fretted.TUNINGS[structure.GUITAR_STRINGS].items()}, "bass": {name: list(strings) for name, strings in fretted.TUNINGS[structure.BASS_STRINGS].items()}, "mostCapo": fretted.MOST_CAPO}


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

    done = apply_edit(score_id, change)
    if body.action == "remove" and saved_mixer(job_folder(score_id)) is not None:
        # The next instrument added may be given the removed one's id, and mustn't take its levels.
        with edit_lock:
            folder = job_folder(score_id)
            done["mixer"] = save_mixer(folder, saved_mixer(folder), current_root(folder))
    return done


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
    """Paste copied music. Guitar notes go back on their strings once the tablature has them."""

    def on_strings(root: ET.Element, extra: dict) -> None:
        for note_id, string in extra.pop("onStrings", []):
            try:
                fretted.choose_string(root, note_id, string)
            except edit.EditError:
                pass  # Pasted into an instrument whose strings can't play it so: its string is chosen for it.

    return apply_edit(score_id, lambda root: marks.paste_stretch(root, body.at, [item.model_dump() for item in body.events]), on_strings)


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
            # A key goes on the staff it's chosen for as it's shown there, so a trumpet's D major is a trumpet's.
            part = structure.part_of(root, body.note) if body.note else 0
            structure.set_key(root, index or 0, concert.wrapped(body.fifths + concert.transposition_at(root.findall("part")[part], index or 0).fifths))
        elif body.action == "time":
            structure.set_time(root, index or 0, body.beats, body.beat_type, body.symbol)
        elif body.action == "pickup":
            structure.set_pickup(root, Fraction(body.count, SIXTEENTHS_PER_QUARTER))
        elif body.action in ("forward", "backward"):
            structure.toggle_repeat(root, index or 0, body.action)
        elif body.action == "barline":
            if index is None:
                raise edit.EditError("Click a note in the measure the barline ends first.")
            structure.set_barline(root, index, body.style)
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


@app.post("/api/scores/{score_id}/chords/{index}/frame")
def change_chord_frame(score_id: str, index: int, body: ChordFrame) -> dict:
    """Give a chord symbol a diagram of its own, or its usual one again. It's an edit, so it can be undone."""
    return apply_edit(score_id, lambda root: diagrams.set_frame(root, index, body.frets, body.barre))


@app.get("/api/scores/{score_id}/chords/{index}/shapes")
def chord_shapes(score_id: str, index: int) -> dict:
    """List the shapes a chord can be played with, for choosing its diagram. Nothing is changed."""
    root = current_root(job_folder(score_id))
    try:
        return diagrams.chord_shapes(concert.shown(root), index)
    except edit.EditError as error:
        raise HTTPException(422, str(error)) from error


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


@app.post("/api/scores/{score_id}/concert")
def change_concert_pitch(score_id: str, body: ConcertPitch) -> dict:
    """Show the score at concert pitch or as written. It's an edit, so it can be undone, as in MuseScore."""
    return apply_edit(score_id, lambda root: concert.set_concert_pitch(root, body.on))


@app.post("/api/scores/{score_id}/diagrams")
def change_diagrams(score_id: str, body: ConcertPitch) -> dict:
    """Show chord diagrams over the chord symbols, or stop. It's an edit, so it can be undone."""
    return apply_edit(score_id, lambda root: diagrams.set_shown(root, body.on))


@app.post("/api/scores/{score_id}/layout")
def change_layout(score_id: str, body: PageLayout) -> dict:
    """Set the page and its spacing, as Format ▸ Page settings and Style do. It's an edit, so it can be undone."""
    return apply_edit(score_id, lambda root: layout.set_layout(root, layout.Layout(**body.model_dump())))


@app.post("/api/scores/{score_id}/view")
def change_view(score_id: str, body: ViewChoice) -> dict:
    """Show the score as pages or in a continuous view. It's how the score is looked at, so it's no edit to undo."""
    if body.view not in VIEWS:
        raise HTTPException(422, "That isn't a view this app has.")
    folder = job_folder(score_id)
    with edit_lock:
        write_whole(folder / "view.json", json.dumps({"view": body.view}))
        return describe(folder, read_status(folder))


@app.post("/api/scores/{score_id}/mixer")
def change_mixer(score_id: str, body: MixerSettings) -> dict:
    """Set the mixer. It's kept beside the score, not in it, so it isn't an edit to undo, as in MuseScore."""
    folder = job_folder(score_id)
    with edit_lock:
        return {"mixer": save_mixer(folder, body.model_dump(), current_root(folder))}


@app.get("/api/scores/{score_id}/playback")
def get_playback(score_id: str) -> dict:
    return playback(current_root(job_folder(score_id)))


def download_name(root: ET.Element, suffix: str) -> str:
    """Name a file saved from a score after its title, as "Maple Leaf Rag.pdf", or "score.pdf" untitled.

    Every file saved was score.pdf, score.mid and so on, so the next score saved beside one became
    "score (1).pdf", and which was which could only be told by opening them. Characters a file's name
    can't hold on Windows or Linux are left out.
    """
    title = " ".join(re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", structure.title_of(root)["title"]).split())[:100].strip(" .")
    return f"{title or 'score'}{suffix}"


def attachment(name: str) -> dict:
    """The header that saves a file under this name: in full, and in plain ASCII for older programs."""
    plain = name.encode("ascii", "replace").decode().replace("?", "_")
    return {"Content-Disposition": f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name)}"}


@app.get("/api/scores/{score_id}/score.musicxml")
def download_musicxml(score_id: str) -> Response:
    folder = job_folder(score_id)
    root = current_root(folder)
    if saved_mixer(folder) is not None:
        root = mixer.with_mixer(root, mixer_of(folder, root))
    text = to_text(technique.drawn_on_other_staves(diagrams.with_frames(root)))  # With its chord diagrams, and notes on the staff they're drawn on.
    return Response(text, media_type="application/vnd.recordare.musicxml+xml", headers=attachment(download_name(root, ".musicxml")))


@app.get("/api/scores/{score_id}/score.mid")
def download_midi(score_id: str) -> Response:
    folder = job_folder(score_id)
    root = current_root(folder)
    levels = mixer_of(folder, root)
    data = midi_bytes(playback(root) | {"mixer": [levels["parts"][part_id] for part_id in part_ids(root)]})
    return Response(data, media_type="audio/midi", headers=attachment(download_name(root, ".mid")))


@app.get("/api/scores/{score_id}/score.pdf")
def download_pdf(score_id: str, part: int | None = None) -> FileResponse:
    """Print the score, or one player's part."""
    folder = job_folder(score_id)
    root = current_root(folder)
    name, saved = "score.pdf", download_name(root, ".pdf")
    if part is not None:
        try:
            root = structure.only_part(root, part)
        except edit.EditError as error:
            raise HTTPException(404, str(error)) from error
        name = f"part_{part + 1}.pdf"
        player = " ".join((root.findtext("part-list/score-part/part-name") or "").split())
        saved = download_name(root, f" ({player}).pdf" if player else f" (part {part + 1}).pdf")
    path = folder / name
    shown = concert.shown(root)
    # Printed as it's shown, at concert pitch if that's chosen, and with the lines the page has.
    export_pdf(with_lines(to_text(shown), remembered_starts(folder, shown, "page") if part is None else []), path)
    # Shown where it opens, as Print and Parts open it in a window of its own to print from. As an
    # attachment, Chromium and the desktop app would only offer to save it. Export PDF still saves
    # it, as its link says to.
    return FileResponse(path, media_type="application/pdf", filename=saved, content_disposition_type="inline")


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
            shown = concert.shown(ET.parse(saved[-1]).getroot())
            write_whole(path, drawn_pages(with_lines(to_text(shown), remembered_starts(folder, shown, "page")), "page")[0])
    return FileResponse(path, media_type="image/svg+xml")


GLYPH_CODES = re.compile(r"""["'`]((?:[EF][0-9A-F]{3})(?: [EF][0-9A-F]{3})*)["'`]""")


@functools.lru_cache(maxsize=1)
def window_glyphs(written: tuple[tuple[str, int], ...]) -> tuple[str, str]:
    """Return the script of the symbols the window draws, and a tag that changes when they do.

    The window names each symbol it draws by its code in its own files, so only those are sent. The
    whole music font is 352 symbols, and was 40% of everything the window loaded, for the few dozen
    it uses. Its files are read again whenever one changes.
    """
    asked = set()
    for name, _ in written:
        for found in GLYPH_CODES.findall((STATIC_DIR / name).read_text(encoding="utf-8")):
            asked.update(found.split())
    script = glyph_script(asked)
    return script, hashlib.sha256(script.encode()).hexdigest()[:20]


@app.get("/glyphs.js")
def music_glyphs(request: Request) -> Response:
    """Serve the shapes the pages are engraved with, so the window's buttons show the same symbols.

    The browser asks each time whether it has the latest, as a window updated to draw another
    symbol can't use a script from before that has it missing.
    """
    written = tuple(sorted((path.name, path.stat().st_mtime_ns) for path in [*STATIC_DIR.glob("*.js"), *STATIC_DIR.glob("*.html")]))
    script, tag = window_glyphs(written)
    headers = {"ETag": f'"{tag}"', "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == headers["ETag"]:
        return Response(status_code=304, headers=headers)
    return Response(script, media_type="text/javascript", headers=headers)


class CheckedStaticFiles(StaticFiles):
    """Serve the page's own files, telling the browser to ask each time whether it has the latest.

    With only an ETag, a browser takes a file as fresh for a while without asking, about a tenth of
    its age. The installed app's files all carry the date of their commit, and the page is always at
    the same address, so after an update a window could load the new page with some of the old
    scripts, or the old page with some of the new. Asked each time, an unchanged file is a 304.
    """

    def file_response(self, *args, **kwargs) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/", CheckedStaticFiles(directory=STATIC_DIR, html=True), name="static")


def stop_when_stdin_closes() -> None:
    """Stop the app once the program that started it has gone.

    The desktop app starts this server and keeps a pipe to it. If the desktop app is killed outright
    it can't stop the server, which would stay on, holding its port and any page being read. The
    pipe closes however the desktop app ends, so the server then stops itself as it would on Ctrl+C.
    """
    if sys.stdin is not None:
        while sys.stdin.buffer.read(65536):
            pass
    os.kill(os.getpid(), signal.SIGTERM)


def main(argv: list[str] | None = None) -> None:
    global JOB_DIR
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--notes", choices=["homr", "own"], default="homr")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--jobs", type=Path, default=JOB_DIR, help="the folder the scores are kept in (default: data/jobs here)")
    parser.add_argument("--stop-when-stdin-closes", action="store_true", help="stop when the program that started this one ends")
    arguments = parser.parse_args(argv)
    os.environ["XRNSHEET_NOTES"] = arguments.notes
    JOB_DIR = arguments.jobs.expanduser().resolve()
    JOB_DIR.mkdir(parents=True, exist_ok=True)
    if arguments.stop_when_stdin_closes:
        threading.Thread(target=stop_when_stdin_closes, daemon=True).start()
    # A request still being answered holds up stopping for at most this long, so closing the
    # desktop app can't hang on one.
    uvicorn.run(app, host="127.0.0.1", port=arguments.port, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
