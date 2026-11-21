import asyncio
import io
import json
import os
import re
import signal
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import harmony, note, parsed, tied_note
from PIL import Image
from reading_stand_in import write_stand_in

from app import server
from pipeline import rhythm
from pipeline.musicxml import to_text


@pytest.fixture
def client(tmp_path, tmp_path_factory, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    # Reading is faked here, so it needs only stand-ins for the readers, not the ones fetched.
    readers = tmp_path_factory.mktemp("models")
    for name in ("chord_reader.onnx", "note_reader.onnx"):
        (readers / name).touch()
    monkeypatch.setattr(server, "MODEL_DIR", readers)
    monkeypatch.setattr(server, "stopping", threading.Event())  # Each test's app has yet to stop.
    for name, empty in (("line", []), ("began", {}), ("cancelled", set())):
        monkeypatch.setattr(server, name, empty)  # And nothing waiting to be read.
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


@pytest.fixture
def ready_score(client):
    score_id = "a" * 32
    folder = server.JOB_DIR / score_id
    folder.mkdir()
    server.write_status(folder, "ready")
    root = parsed(harmony("D", "m7", "minor-seventh") + note() + note("D") + note("E") + note("F"))
    server.edit.ensure_note_ids(root)
    server.save_version(folder, root)
    (folder / "recognized").mkdir()
    (folder / "recognized" / "pages.json").write_text("[1]")
    return score_id


def test_unknown_or_malformed_ids_are_not_found(client):
    assert client.get("/api/scores/nope").status_code == 404
    assert client.get("/api/scores/" + "b" * 32).status_code == 404
    assert client.get("/api/scores/..%2F..%2Fetc").status_code == 404


def test_uploads_must_be_images_or_pdfs(client):
    response = client.post("/api/scores", files={"file": ("notes.txt", io.BytesIO(b"hi"))})
    assert response.status_code == 415


def test_a_corrupt_image_is_refused_at_upload(client):
    response = client.post("/api/scores", files={"file": ("photo.jpg", io.BytesIO(b"not really a photo"))})
    assert response.status_code == 415 and "opened" in response.json()["detail"]


def test_a_score_opens_with_its_chords_and_pages(client, ready_score):
    data = client.get(f"/api/scores/{ready_score}").json()
    assert data["status"] == "ready" and data["chords"][0]["text"] == "Dm7" and "<svg" in data["pages"][0]


def test_edit_and_undo_round_trip(client, ready_score):
    url = f"/api/scores/{ready_score}"
    assert client.post(f"{url}/chords/0", json={"text": "Bb7"}).json()["chords"][0]["text"] == "Bb7"
    undone = client.post(f"{url}/undo").json()
    assert undone["chords"][0]["text"] == "Dm7" and undone["canUndo"] is False
    assert client.post(f"{url}/undo").status_code == 409


def test_bad_edits_are_refused_and_leave_the_score_alone(client, ready_score):
    url = f"/api/scores/{ready_score}"
    assert client.post(f"{url}/chords/0", json={"text": "Hello"}).status_code == 422
    assert client.post(f"{url}/notes/note0", json={"action": "explode"}).status_code == 422
    assert client.post(f"{url}/notes/note99", json={"action": "up"}).status_code == 422
    assert client.get(url).json()["canUndo"] is False


def test_downloads_work(client, ready_score):
    url = f"/api/scores/{ready_score}"
    assert client.get(f"{url}/score.pdf").content.startswith(b"%PDF")
    assert "<score-partwise" in client.get(f"{url}/score.musicxml").text


def test_marking_a_sheet_checked_round_trips(client, ready_score):
    assert client.get(f"/api/scores/{ready_score}").json()["checked"] is False
    assert client.post(f"/api/scores/{ready_score}/checked", json={"checked": True}).json()["checked"] is True
    assert client.get(f"/api/scores/{ready_score}").json()["checked"] is True
    assert client.post(f"/api/scores/{ready_score}/checked", json={"checked": False}).json()["checked"] is False


def test_a_sheet_un_marked_is_no_longer_learnt_from(client, tmp_path, monkeypatch):
    """Checked sheets are taught to the chord reader as right, so one marked by mistake must come out
    of what was gathered for it too, as retraining reads that until it's gathered again."""
    pytest.importorskip("torch")  # test_corrections, which writes the sheets, imports it
    from test_corrections import recognized, write_sheet

    out = tmp_path / "corrections"
    monkeypatch.setattr(server.corrections, "OUT_DIR", out)
    sheet = write_sheet(server.JOB_DIR, "f" * 32, [recognized(0, 0.0, "C")], ["D"])
    other = write_sheet(server.JOB_DIR, "e" * 32, [recognized(0, 0.0, "F")], ["G"], upload=b"other")
    for folder in (sheet, other):
        server.write_status(folder, "ready")
        (folder / "recognized" / "pages.json").write_text("[1]")
    server.corrections.export(server.JOB_DIR, out)
    gathered = {row["sheet"]: row["image"] for row in server.corrections.load_labels(out)}
    assert set(gathered) == {sheet.name, other.name}
    assert client.post(f"/api/scores/{sheet.name}/checked", json={"checked": False}).json()["checked"] is False
    assert [row["sheet"] for row in server.corrections.load_labels(out)] == [other.name]
    assert not (out / gathered[sheet.name]).exists() and (out / gathered[other.name]).exists()
    assert server.corrections.checked_sheets(server.JOB_DIR) == [other]
    assert client.post(f"/api/scores/{sheet.name}/checked", json={"checked": True}).json()["checked"] is True
    assert server.corrections.checked_sheets(server.JOB_DIR) == [other, sheet]  # Marked again, it's gathered next time.


def test_a_checked_sheet_deleted_is_no_longer_learnt_from(client, tmp_path, monkeypatch):
    """A deleted sheet sits in the trash, where gathering doesn't look, but what was gathered from it
    already must come out too, as retraining reads that until it's gathered again."""
    pytest.importorskip("torch")  # test_corrections, which writes the sheets, imports it
    from test_corrections import recognized, write_sheet

    out = tmp_path / "corrections"
    monkeypatch.setattr(server.corrections, "OUT_DIR", out)
    sheet = write_sheet(server.JOB_DIR, "f" * 32, [recognized(0, 0.0, "C")], ["D"])
    other = write_sheet(server.JOB_DIR, "e" * 32, [recognized(0, 0.0, "F")], ["G"], upload=b"other")
    for folder in (sheet, other):
        server.write_status(folder, "ready")
        (folder / "recognized" / "pages.json").write_text("[1]")
    server.corrections.export(server.JOB_DIR, out)
    gathered = {row["sheet"]: row["image"] for row in server.corrections.load_labels(out)}
    assert client.delete(f"/api/scores/{sheet.name}").json()["undo"] is True
    assert [row["sheet"] for row in server.corrections.load_labels(out)] == [other.name]
    assert not (out / gathered[sheet.name]).exists() and (out / gathered[other.name]).exists()
    assert server.corrections.checked_sheets(server.JOB_DIR) == [other]
    assert client.post(f"/api/scores/{sheet.name}/restore").status_code == 200
    assert server.corrections.checked_sheets(server.JOB_DIR) == [other, sheet]  # Back, and still checked, it's gathered next time.


def test_a_sheet_still_reading_cannot_be_checked(client):
    score_id = "c" * 32
    folder = server.JOB_DIR / score_id
    folder.mkdir()
    server.write_status(folder, "processing")
    assert client.post(f"/api/scores/{score_id}/checked", json={"checked": True}).status_code == 409


def test_sheets_are_listed_with_how_far_they_have_got(client, ready_score):
    unfinished = server.JOB_DIR / ("d" * 32)
    unfinished.mkdir()
    server.write_status(unfinished, "processing")
    sheets = {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}
    assert sheets[ready_score]["status"] == "ready" and sheets[ready_score]["pages"] == 1
    assert sheets[unfinished.name]["status"] == "processing"


def test_the_library_reads_a_score_only_when_it_has_changed(client, monkeypatch):
    """Each listing parsed every score's latest MusicXML in full for its title, every 2 s while a sheet
    was read and after every edit with the library open."""
    first, second = blank(client, title="Blue Bossa"), blank(client, title="Solar")
    read = []
    title_of = server.structure.title_of
    monkeypatch.setattr(server.structure, "title_of", lambda root: read.append(root) or title_of(root))
    titles = lambda: {sheet["id"]: sheet["title"] for sheet in client.get("/api/scores").json()}
    assert titles() == {first: "Blue Bossa", second: "Solar"}
    read.clear()
    assert titles() == {first: "Blue Bossa", second: "Solar"} and read == [], "unchanged, neither is read again"
    client.post(f"/api/scores/{second}/title", json={"title": "Nardis"})
    read.clear()
    assert titles() == {first: "Blue Bossa", second: "Nardis"} and len(read) == 1
    client.post(f"/api/scores/{second}/undo")
    client.post(f"/api/scores/{second}/title", json={"title": "Footprints"})  # The same version number as the one undone.
    assert titles()[second] == "Footprints"


def test_a_missing_source_page_is_not_found(client, ready_score):
    assert client.get(f"/api/scores/{ready_score}/source/7.jpg").status_code == 404


def pdf_with_pages(path, count):
    pages = [Image.new("RGB", (200, 280), "white") for _ in range(count)]
    pages[0].save(path, save_all=True, append_images=pages[1:])


def fake_recognizer(failing=()):
    """Stand in for the recognition process, writing what it would write."""

    def recognize(image, out_dir):
        number = int(out_dir.name.split("_")[1])
        if number in failing:
            return "Recognition failed."
        out_dir.mkdir(parents=True)
        root = parsed(harmony("C", "", "major") + note() + note("D") + note("E") + note("F"))
        (out_dir / "page.musicxml").write_text(to_text(root), encoding="utf-8")
        item = {"text": "C", "chord": "C", "confidence": 0.9, "box": [1, 2, 3, 4], "measure": 0, "onset": 0.0}
        (out_dir / "chords.json").write_text(json.dumps([item]), encoding="utf-8")
        Image.new("RGB", (100, 100), "white").save(out_dir / "page.jpg")
        return None

    return recognize


def page_with_its_places(image, out_dir):
    """Stand in for reading a page of four bars of four quarter notes, two bars a line, keeping where they are.

    Each line runs from x 50 to 850 with a barline at 450 and a note every 100 from x 100, the first
    line's staff from y 100 to 140, the second's from 300 to 340. A C chord is found on the first beat.
    """
    out_dir.mkdir(parents=True)
    bar = "".join(note(step) for step in "CDEF")
    root = parsed(harmony("C", "", "major") + bar, bar, '<print new-system="yes"/>' + bar, bar)
    (out_dir / "page.musicxml").write_text(to_text(root), encoding="utf-8")
    item = {"text": "C", "chord": "C", "confidence": 0.9, "box": [90, 70, 130, 90], "measure": 0, "onset": 0.0}
    (out_dir / "chords.json").write_text(json.dumps([item]), encoding="utf-8")
    lines = [{"left": 50, "right": 850, "top": top, "bottom": top + 40, "unit": 10, "bars": [450], "notes": [100, 200, 300, 400, 500, 600, 700, 800]} for top in (100, 300)]
    (out_dir / "layout.json").write_text(json.dumps(lines), encoding="utf-8")
    Image.new("RGB", (900, 400), "white").save(out_dir / "page.jpg")


def chords_of(client, score_id):
    return [(chord["text"], chord["measure"], chord["onset"]) for chord in client.get(f"/api/scores/{score_id}").json()["chords"]]


def test_a_chord_missed_is_added_where_it_was_clicked_on_the_original(client, monkeypatch):
    """A chord the reader missed had no box and couldn't be added from the original, only from a note in the score."""
    monkeypatch.setattr(server, "recognize_page", page_with_its_places)
    folder, upload = job_for_pdf(2)
    server.recognize_job(folder, upload)
    url = f"/api/scores/{folder.name}"
    lines = client.get(f"{url}/source/2/layout").json()["lines"]
    assert [[bar["measure"] for bar in line["bars"]] for line in lines] == [[4, 5], [6, 7]]  # Page 2 starts at the score's fifth bar.
    assert [(beat["onset"], beat["x"]) for beat in lines[0]["bars"][1]["beats"]] == [(0.0, 500), (1.0, 600), (2.0, 700), (3.0, 800)]
    assert (lines[1]["top"], lines[1]["bottom"], lines[0]["bars"][1]["left"], lines[0]["bars"][1]["right"]) == (300, 340, 450, 850)

    box = [690, 75, 730, 95]
    added = client.post(f"{url}/chords/at", json={"measure": 5, "onset": 2.0, "text": "G7", "page": 2, "box": box}).json()
    found = next(chord for chord in added["chords"] if chord["text"] == "G7")
    assert (found["measure"], found["onset"], found["box"], found["page"], found["added"]) == (5, 2.0, box, 2, True)
    assert client.post(f"{url}/chords/at", json={"measure": 5, "onset": 2.0, "text": "A7"}).status_code == 422  # One's there already.
    assert "G7" not in (folder / "recognized" / "chords.json").read_text()  # What the reader found is what it learns from.
    client.post(f"{url}/undo")
    assert ("G7", 5, 2.0) not in chords_of(client, folder.name)

    first = server.current_root(folder).find("part/measure/note").get("id")
    client.post(f"{url}/bars", json={"action": "insert", "note": first})
    client.post(f"{url}/chords/at", json={"measure": 5, "onset": 1.0, "text": "Dm7"})
    assert ("Dm7", 6, 1.0) in chords_of(client, folder.name)  # In the bar read as the sixth, wherever it is now.
    sixth = next(measure for measure in server.current_root(folder).find("part").findall("measure") if measure.get("id") == "m5")
    client.post(f"{url}/bars", json={"action": "delete", "note": sixth.find("note").get("id")})
    refused = client.post(f"{url}/chords/at", json={"measure": 5, "onset": 0.0, "text": "C"})
    assert refused.status_code == 422 and "deleted" in refused.json()["detail"]


@pytest.mark.parametrize("onset", ["1e400", "-1e400", "NaN", "1e300", "4", "2.0"])
def test_a_chord_that_cant_be_added_from_the_original_leaves_no_box_behind(client, monkeypatch, onset):
    """A moment no number can hold was a 500, one past the bar was saved, and a refused chord still
    wrote its box, over the box of the chord already there."""
    monkeypatch.setattr(server, "recognize_page", page_with_its_places)
    folder, upload = job_for_pdf(1)
    server.recognize_job(folder, upload)
    url = f"/api/scores/{folder.name}"
    client.post(f"{url}/chords/at", json={"measure": 1, "onset": 2.0, "text": "G7", "page": 1, "box": [690, 75, 730, 95]})
    before, kept = server.versions(folder), server.added_chords(folder)
    body = f'{{"measure": 1, "onset": {onset}, "text": "A7", "page": 1, "box": [1, 2, 3, 4]}}'
    response = client.post(f"{url}/chords/at", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert server.versions(folder) == before and server.added_chords(folder) == kept


def test_a_page_read_before_its_places_were_kept_says_so(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    folder, upload = job_for_pdf(1)
    server.recognize_job(folder, upload)
    assert client.get(f"/api/scores/{folder.name}/source/1/layout").json() == {"lines": []}
    assert client.get(f"/api/scores/{folder.name}/source/3/layout").status_code == 404


def job_for_pdf(count):
    folder = server.JOB_DIR / ("e" * 32)
    folder.mkdir()
    upload = folder / "upload.pdf"
    pdf_with_pages(upload, count)
    server.write_status(folder, "processing")
    return folder, upload


def test_every_pdf_page_is_read_into_one_score(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    data = client.get(f"/api/scores/{folder.name}").json()
    assert data["status"] == "ready" and data["message"] == "" and data["sourcePages"] == [1, 2, 3]
    assert [chord["measure"] for chord in data["chords"]] == [0, 1, 2]
    assert [chord["page"] for chord in data["chords"]] == [1, 2, 3]
    assert all(chord["box"] == [1, 2, 3, 4] and chord["confidence"] == 0.9 for chord in data["chords"])
    assert client.get(f"/api/scores/{folder.name}/source/2.jpg").status_code == 200


def test_a_page_that_fails_is_left_out_and_reported(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer(failing={2}))
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    data = client.get(f"/api/scores/{folder.name}").json()
    assert data["status"] == "ready" and "page 2" in data["message"] and data["sourcePages"] == [1, 3]


def test_a_document_with_no_readable_page_fails(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer(failing={1, 2}))
    folder, upload = job_for_pdf(2)
    server.recognize_job(folder, upload)
    data = client.get(f"/api/scores/{folder.name}").json()
    assert data["status"] == "failed" and data["message"] == "Recognition failed."


def test_a_sheet_read_before_the_readers_are_fetched_says_how_to_fetch_them(client, monkeypatch, tmp_path_factory):
    """The readers are files of a release, not of git, so a fresh checkout has none. A sheet read then
    failed in each page's own process, saying only "Recognition failed"."""
    monkeypatch.setattr(server, "MODEL_DIR", tmp_path_factory.mktemp("empty"))
    monkeypatch.setattr(server, "recognize_page", lambda image, out_dir: pytest.fail("a page was read with no reader"))
    monkeypatch.setenv("XRNSHEET_NOTES", "own")
    assert server.missing_readers("own") == ["chord_reader.onnx", "note_reader.onnx"]
    folder, upload = job_for_pdf(1)
    server.recognize_job(folder, upload)
    data = client.get(f"/api/scores/{folder.name}").json()
    assert data["status"] == "failed" and data["message"] == server.MISSING_READERS
    assert "python3 tools/fetch_models.py" in data["message"]


def test_the_readers_a_sheet_needs_are_the_chord_readers_and_with_own_notes_the_note_readers(client, monkeypatch, tmp_path_factory):
    readers = tmp_path_factory.mktemp("some")
    monkeypatch.setattr(server, "MODEL_DIR", readers)
    (readers / "chord_reader.pt").touch()  # Read in PyTorch, as before, when it isn't exported.
    assert server.missing_readers("homr") == [] and server.missing_readers("own") == ["note_reader.onnx"]
    (readers / "note_reader.onnx").touch()
    assert server.missing_readers("own") == []


def test_a_long_pdf_says_what_was_skipped(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    monkeypatch.setattr(server, "MAX_PDF_PAGES", 2)
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    assert "first 2 of 3" in client.get(f"/api/scores/{folder.name}").json()["message"]


@pytest.mark.parametrize(
    ("name", "title"),
    [
        ("autumn_leaves (2).jpg", "Autumn leaves"),
        ("fly-me-to-the-moon.png", "Fly me to the moon"),
        ("Blue Bossa - Copy.jpg", "Blue Bossa"),
        ("Nocturne Op. 9 No. 2.pdf", "Nocturne Op. 9 No. 2"),
        ("IMG_2034.jpeg", ""),
        ("scan0001.pdf", ""),
        ("photo 3.png", ""),
        ("WhatsApp Image 2024-11-02 at 15.30.12.jpeg", ""),
        ("PXL_20241102_153012345.MP.jpg", ""),
        ("20241102_153012.jpg", ""),
    ],
)
def test_a_file_name_is_a_title_unless_a_camera_gave_it(name, title):
    assert server.title_from_name(name) == title


def read_as(name):
    """Read a one-page sheet uploaded as a file of this name, and return its address."""
    folder = server.JOB_DIR / os.urandom(16).hex()
    folder.mkdir()
    pdf_with_pages(folder / "upload.pdf", 1)
    server.write_whole(folder / server.SOURCE_FILE, json.dumps({"name": name}))
    server.write_status(folder, "processing")
    server.recognize_job(folder, folder / "upload.pdf")
    return f"/api/scores/{folder.name}"


def test_a_sheet_read_with_no_title_is_called_after_its_file(client, monkeypatch):
    """One whose reading found no title was "Untitled score" in the library and the top bar, and saved
    as score.pdf, though the photo it was read from had a name."""
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    url = read_as("autumn_leaves (2).pdf")
    assert client.get(url).json()["title"] == "Autumn leaves"
    assert [sheet["title"] for sheet in client.get("/api/scores").json()] == ["Autumn leaves"]
    assert "Autumn%20leaves.pdf" in client.get(f"{url}/score.pdf").headers["content-disposition"]
    assert "Autumn" not in "".join(client.get(url).json()["pages"])  # Not printed until saved, as a title read isn't.
    client.post(f"{url}/title", json={"title": "Les feuilles mortes"})
    assert client.get(url).json()["title"] == "Les feuilles mortes"  # One typed wins, as over any other.


def test_a_title_read_from_the_sheet_wins_and_a_cameras_name_gives_none(client, monkeypatch):
    def titled(image, out_dir):
        fake_recognizer()(image, out_dir)
        root = ET.parse(out_dir / "page.musicxml").getroot()
        root.insert(0, ET.fromstring("<work><work-title>Blue Bossa</work-title></work>"))
        (out_dir / "page.musicxml").write_text(to_text(root), encoding="utf-8")

    monkeypatch.setattr(server, "recognize_page", titled)
    assert client.get(read_as("autumn_leaves.pdf")).json()["title"] == "Blue Bossa"
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    assert client.get(read_as("IMG_2034.pdf")).json()["title"] == ""


def test_progress_is_reported_page_by_page(client, monkeypatch):
    seen = []
    original = server.write_status
    monkeypatch.setattr(server, "write_status", lambda folder, status, message="": (seen.append(message), original(folder, status, message)))
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    folder, upload = job_for_pdf(2)
    server.recognize_job(folder, upload)
    assert "Reading page 2 of 2." in seen


def test_one_chord_page_keeps_its_confidence(client, ready_score):
    folder = server.JOB_DIR / ready_score
    items = [
        {"text": "Dm7", "chord": "Dm7", "confidence": 0.5, "box": [1, 2, 3, 4], "measure": 0, "onset": 0.0, "page": 1},
        {"text": "Em7", "chord": "Em7", "confidence": 0.9, "box": [5, 6, 7, 8], "measure": 0, "onset": 0.0, "page": 1},
    ]
    (folder / "recognized" / "chords.json").write_text(json.dumps(items), encoding="utf-8")
    chord = client.get(f"/api/scores/{ready_score}").json()["chords"][0]
    assert chord["confidence"] == 0.5 and chord["box"] == [1, 2, 3, 4]  # Only the first was placed.


def blank(client, **settings):
    response = client.post("/api/scores/blank", json={"measures": 4, **settings})
    assert response.status_code == 200
    return response.json()["id"]


def saved(client, score_id):
    return ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)


def notes_of(client, score_id):
    return list(saved(client, score_id).iter("note"))


def first_id(client, score_id):
    return notes_of(client, score_id)[0].get("id")


def pitches(client, score_id):
    return [note.findtext("pitch/step") for note in notes_of(client, score_id) if note.find("pitch") is not None]


def write(client, score_id, at, step, length="quarter"):
    response = client.post(f"/api/scores/{score_id}/notes/{at}", json={"action": "write", "type": length, "step": step})
    assert response.status_code == 200
    return response.json()


def test_a_blank_score_opens_with_the_chosen_signature(client):
    score_id = blank(client, fifths=-3, beats=6, beat_type=8, clef="bass", measures=2)
    root = saved(client, score_id)
    attributes = root.find("part/measure/attributes")
    assert attributes.findtext("key/fifths") == "-3" and attributes.findtext("time/beats") == "6"
    assert attributes.findtext("clef/sign") == "F" and len(root.findall("part/measure")) == 2
    data = client.get(f"/api/scores/{score_id}").json()
    assert data["blank"] is True and data["canUndo"] is False and data["barChecks"] == []


def test_a_blank_score_is_listed_as_blank(client):
    score_id = blank(client)
    assert next(sheet for sheet in client.get("/api/scores").json() if sheet["id"] == score_id)["blank"] is True


def test_impossible_blank_scores_are_refused(client):
    assert client.post("/api/scores/blank", json={"measures": 0}).status_code in (400, 422)
    assert client.post("/api/scores/blank", json={"beat_type": 5}).status_code in (400, 422)
    assert client.post("/api/scores/blank", json={"clef": "kazoo"}).status_code in (400, 422)


def test_a_blank_score_cannot_be_marked_checked(client):
    # There's no recognized version to compare against, so it could only feed bad training labels.
    assert client.post(f"/api/scores/{blank(client)}/checked", json={"checked": True}).status_code == 409


def test_writing_a_note_returns_the_note_and_the_next_place(client):
    score_id = blank(client)
    data = write(client, score_id, first_id(client, score_id), "G")
    assert data["written"] and data["cursor"] and data["written"] != data["cursor"]
    following = write(client, score_id, data["cursor"], "A")
    assert pitches(client, score_id) == ["G", "A"] and following["canUndo"] is True


def test_filling_the_last_bar_adds_no_bar_until_the_next_note(client):
    score_id = blank(client, measures=1)
    cursor = write(client, score_id, first_id(client, score_id), "C", "whole")["cursor"]
    assert len(saved(client, score_id).findall("part/measure")) == 1
    write(client, score_id, cursor, "D", "quarter")
    assert len(saved(client, score_id).findall("part/measure")) == 2 and pitches(client, score_id) == ["C", "D"]


def test_notes_can_be_stacked_into_a_chord(client):
    score_id = blank(client)
    written = write(client, score_id, first_id(client, score_id), "C")["written"]
    response = client.post(f"/api/scores/{score_id}/notes/{written}", json={"action": "stack", "step": "E"})
    assert response.status_code == 200
    assert sum(note.find("chord") is not None for note in notes_of(client, score_id)) == 1


def test_redo_replays_an_undone_edit(client):
    score_id = blank(client)
    url = f"/api/scores/{score_id}"
    write(client, score_id, first_id(client, score_id), "C")
    assert client.post(f"{url}/undo").json()["canRedo"] is True
    assert pitches(client, score_id) == []
    assert client.post(f"{url}/redo").json()["canRedo"] is False
    assert pitches(client, score_id) == ["C"]


def test_a_new_edit_clears_redo(client):
    score_id = blank(client)
    url = f"/api/scores/{score_id}"
    write(client, score_id, first_id(client, score_id), "C")
    client.post(f"{url}/undo")
    write(client, score_id, first_id(client, score_id), "D")
    assert client.get(url).json()["canRedo"] is False
    assert client.post(f"{url}/redo").status_code == 409
    assert pitches(client, score_id) == ["D"]


def test_a_refused_edit_keeps_redo(client):
    score_id = blank(client)
    url = f"/api/scores/{score_id}"
    write(client, score_id, first_id(client, score_id), "C")
    client.post(f"{url}/undo")
    assert client.post(f"{url}/notes/note999", json={"action": "up"}).status_code == 422
    assert client.get(url).json()["canRedo"] is True


def test_bars_can_be_added_and_deleted(client):
    score_id = blank(client, measures=2)
    url = f"/api/scores/{score_id}"
    assert client.post(f"{url}/bars", json={"action": "insert"}).status_code == 200
    assert len(saved(client, score_id).findall("part/measure")) == 3
    assert client.post(f"{url}/bars", json={"action": "delete", "note": first_id(client, score_id)}).status_code == 200
    root = saved(client, score_id)
    assert len(root.findall("part/measure")) == 2 and root.findtext("part/measure/attributes/divisions")


def test_the_last_bar_cannot_be_deleted(client):
    score_id = blank(client, measures=1)
    response = client.post(f"/api/scores/{score_id}/bars", json={"action": "delete", "note": first_id(client, score_id)})
    assert response.status_code == 422 and len(saved(client, score_id).findall("part/measure")) == 1


def test_a_new_key_is_saved(client):
    score_id = blank(client)
    client.post(f"/api/scores/{score_id}/bars", json={"action": "key", "fifths": 2})
    assert saved(client, score_id).findtext("part/measure/attributes/key/fifths") == "2"


def test_a_new_time_flags_bars_that_no_longer_fit(client):
    score_id = blank(client, measures=2)
    cursor = first_id(client, score_id)
    for step in "CDEF":
        cursor = write(client, score_id, cursor, step)["cursor"]
    data = client.post(f"/api/scores/{score_id}/bars", json={"action": "time", "beats": 3, "beat_type": 4}).json()
    assert [check["problem"] for check in data["barChecks"]] == ["long"]
    assert data["barChecks"][0]["measure"] == 0 and data["barChecks"][0]["note"]


def test_edits_arriving_together_are_all_kept(client):
    score_id = blank(client, measures=1)
    ids = [note.get("id") for note in notes_of(client, score_id)]
    url = f"/api/scores/{score_id}"
    start = len(list((server.JOB_DIR / score_id).glob("version_*.musicxml")))

    def edit(_):
        return client.post(f"{url}/bars", json={"action": "insert"}).status_code

    with ThreadPoolExecutor(8) as pool:
        assert set(pool.map(edit, range(16))) == {200}
    assert ids and len(saved(client, score_id).findall("part/measure")) == 17
    assert len(list((server.JOB_DIR / score_id).glob("version_*.musicxml"))) == start + 16


def test_chords_stay_on_their_boxes_after_a_bar_is_added(client, ready_score):
    folder = server.JOB_DIR / ready_score
    (folder / "recognized" / "chords.json").write_text(json.dumps([
        {"measure": 0, "onset": 0.0, "chord": "Dm7", "text": "Dm7", "confidence": 0.5, "box": [1, 2, 3, 4], "page": 1},
    ]))
    url = f"/api/scores/{ready_score}"
    assert client.post(f"{url}/bars", json={"action": "insert", "note": "note0"}).status_code == 200
    chord = client.get(url).json()["chords"][0]
    assert chord["measure"] == 1 and chord["box"] == [1, 2, 3, 4] and chord["confidence"] == 0.5


def test_playback_lists_notes_and_chords_in_time_order(client, ready_score):
    data = client.get(f"/api/scores/{ready_score}/playback").json()
    assert [note["start"] for note in data["notes"]] == [0, 1, 2, 3] and data["total"] == 4
    assert data["chords"][0]["length"] == 4 and data["notes"][0]["id"] == "note0"


def melody(client, score_id, steps):
    cursor = first_id(client, score_id)
    written = []
    for step in steps:
        data = write(client, score_id, cursor, step)
        written.append(data["written"])
        cursor = data["cursor"]
    return written, cursor


def test_a_note_can_be_placed_by_staff_line(client):
    score_id = blank(client)
    url = f"/api/scores/{score_id}/notes/{first_id(client, score_id)}"
    assert client.post(url, json={"action": "write", "type": "quarter", "line": 0}).status_code == 200
    assert pitches(client, score_id) == ["F"]


def test_a_triplet_endpoint_reports_the_length_to_use(client):
    score_id = blank(client)
    url = f"/api/scores/{score_id}/notes/{first_id(client, score_id)}"
    data = client.post(url, json={"action": "tuplet", "type": "quarter"}).json()
    assert data["type"] == "eighth" and data["cursor"]
    assert sum(1 for _ in saved(client, score_id).iter("time-modification")) == 3


def test_a_selected_note_becomes_a_triplet_and_back(client):
    score_id = blank(client)
    written, _ = melody(client, score_id, "C")
    url = f"/api/scores/{score_id}/notes/{written[0]}"
    assert client.post(url, json={"action": "tupletHere"}).status_code == 200
    assert client.post(url, json={"action": "tupletHere"}).status_code == 200
    assert not list(saved(client, score_id).iter("time-modification"))


def test_a_group_edit_is_one_undo(client):
    score_id = blank(client)
    written, _ = melody(client, score_id, "CD")
    url = f"/api/scores/{score_id}"
    versions_before = len(list((server.JOB_DIR / score_id).glob("version_*.musicxml")))
    assert client.post(f"{url}/group", json={"action": "octaveUp", "notes": written}).status_code == 200
    assert len(list((server.JOB_DIR / score_id).glob("version_*.musicxml"))) == versions_before + 1
    client.post(f"{url}/undo")
    assert [note.findtext("pitch/octave") for note in notes_of(client, score_id)[:2]] == ["5", "5"]


def test_a_group_edit_skips_rests(client):
    score_id = blank(client)
    written, cursor = melody(client, score_id, "C")
    response = client.post(f"/api/scores/{score_id}/group", json={"action": "up", "notes": [*written, cursor]})
    assert response.status_code == 200 and pitches(client, score_id) == ["D"]


def test_a_group_of_only_rests_is_refused(client):
    score_id = blank(client)
    response = client.post(f"/api/scores/{score_id}/group", json={"action": "up", "notes": [first_id(client, score_id)]})
    assert response.status_code == 422


def test_a_group_can_only_do_group_actions(client):
    score_id = blank(client)
    assert client.post(f"/api/scores/{score_id}/group", json={"action": "write", "notes": []}).status_code == 422


def test_looking_at_a_range_saves_nothing(client):
    score_id = blank(client)
    written, _ = melody(client, score_id, "CD")
    url = f"/api/scores/{score_id}"
    before = len(list((server.JOB_DIR / score_id).glob("version_*.musicxml")))
    assert client.post(f"{url}/range", json={"first": written[0], "last": written[1]}).json()["ids"] == written
    assert client.post(f"{url}/copy", json={"first": written[0], "last": written[1]}).status_code == 200
    assert len(list((server.JOB_DIR / score_id).glob("version_*.musicxml"))) == before


def test_copy_and_paste_work_between_scores(client):
    source = blank(client)
    written, _ = melody(client, source, "CDE")
    clip = client.post(f"/api/scores/{source}/copy", json={"first": written[0], "last": written[-1]}).json()
    target = blank(client)
    response = client.post(f"/api/scores/{target}/paste", json={"at": first_id(client, target), "events": clip["events"]})
    assert response.status_code == 200 and pitches(client, target) == ["C", "D", "E"] and response.json()["cursor"]


def test_a_bad_range_is_refused(client):
    score_id = blank(client)
    assert client.post(f"/api/scores/{score_id}/range", json={"first": "nope", "last": "nope"}).status_code == 422
    assert client.post(f"/api/scores/{score_id}/copy", json={"first": "nope", "last": "nope"}).status_code == 422


def test_a_lyric_is_saved(client):
    score_id = blank(client)
    written, _ = melody(client, score_id, "CD")
    response = client.post(f"/api/scores/{score_id}/lyrics/{written[0]}", json={"text": "hel", "joined": True})
    assert response.status_code == 200 and next(saved(client, score_id).iter("syllabic")).text == "begin"
    assert response.json()["next"] == written[1]


def test_a_title_appears_in_the_score_and_the_list(client):
    score_id = blank(client)
    data = client.post(f"/api/scores/{score_id}/title", json={"title": "Song", "composer": "Writer"}).json()
    assert data["title"] == "Song" and data["composer"] == "Writer" and "Song" in data["pages"][0]
    assert client.get("/api/scores").json()[0]["title"] == "Song"


def test_repeats_and_endings_change_how_a_score_plays(client):
    score_id = blank(client, measures=3)
    written, _ = melody(client, score_id, "CDEF")
    url = f"/api/scores/{score_id}"
    bars = [measure.find("note").get("id") for measure in saved(client, score_id).findall("part/measure")]
    assert client.post(f"{url}/bars", json={"action": "backward", "note": bars[1]}).status_code == 200
    assert client.post(f"{url}/bars", json={"action": "ending", "note": bars[1], "number": 1}).status_code == 200
    assert client.post(f"{url}/bars", json={"action": "ending", "note": bars[2], "number": 2}).status_code == 200
    assert client.get(f"{url}/playback").json()["total"] == 16
    assert written


NEW_EDITS = [
    ("marks", {"action": "toggle", "name": "staccato", "notes": ["note0"]}),
    ("marks", {"action": "toggle", "name": "fermata", "notes": ["note0", "note1"]}),
    ("marks", {"action": "slur", "notes": ["note0", "note2"]}),
    ("marks", {"action": "hairpin", "name": "crescendo", "notes": ["note0", "note2"]}),
    ("marks", {"action": "dynamic", "name": "pp", "notes": ["note1"]}),
    ("marks", {"action": "tempo", "text": "Allegro 132", "notes": ["note0"]}),
    ("marks", {"action": "text", "name": "staff", "text": "solo", "notes": ["note0"]}),
    ("marks", {"action": "rehearsal", "notes": ["note0"]}),
    ("marks", {"action": "swing", "on": True, "notes": ["note0"]}),
    ("marks", {"action": "jump", "name": "dc", "notes": ["note3"]}),
    ("marks", {"action": "grace", "notes": ["note1"]}),
    ("transpose", {"interval": "M2", "direction": "up"}),
    ("transpose", {"interval": "m3", "direction": "down", "first": "note1", "last": "note2"}),
    ("transpose", {"key": 3}),
    ("voice", {"notes": ["note1"], "voice": 2}),
    ("parts", {"action": "add", "name": "trumpet"}),
    ("bars", {"action": "clef", "clef": "bass", "note": "note0"}),
    ("bars", {"action": "barsPerLine", "count": 4, "note": "note0"}),
]


@pytest.mark.parametrize(("route", "body"), NEW_EDITS, ids=lambda item: item if isinstance(item, str) else f"{item.get('action', '')}{item.get('name', '')}")
def test_new_edits_undo_and_redo_exactly(client, ready_score, route, body):
    url = f"/api/scores/{ready_score}"
    before = client.get(f"{url}/score.musicxml").text
    response = client.post(f"{url}/{route}", json=body)
    assert response.status_code == 200, response.text
    assert response.json()["canUndo"] is True
    after = client.get(f"{url}/score.musicxml").text
    assert after != before
    client.post(f"{url}/undo")
    assert client.get(f"{url}/score.musicxml").text == before
    client.post(f"{url}/redo")
    assert client.get(f"{url}/score.musicxml").text == after


REFUSED_EDITS = [
    ("marks", {"action": "explode", "notes": ["note0"]}),
    ("marks", {"action": "toggle", "name": "kazoo", "notes": ["note0"]}),
    ("marks", {"action": "toggle", "name": "staccato", "notes": []}),
    ("marks", {"action": "toggle", "name": "staccato", "notes": ["note99"]}),
    ("marks", {"action": "dynamic", "name": "loud", "notes": ["note0"]}),
    ("marks", {"action": "tempo", "text": "9000", "notes": ["note0"]}),
    ("marks", {"action": "jump", "name": "elsewhere", "notes": ["note0"]}),
    ("marks", {"action": "remove", "id": "mark99"}),
    ("marks", {"action": "label", "id": "mark99", "text": "x"}),
    ("transpose", {"interval": "P9"}),
    ("transpose", {"interval": "M2", "direction": "sideways"}),
    ("transpose", {"key": 99}),
    ("voice", {"notes": ["note0"], "voice": 9}),
    ("voice", {"notes": ["note0"], "voice": 1}),
    ("parts", {"action": "add", "name": "kazoo"}),
    ("parts", {"action": "remove", "note": "note0"}),
    ("parts", {"action": "juggle"}),
    ("bars", {"action": "clef", "clef": "kazoo", "note": "note0"}),
    ("bars", {"action": "barsPerLine", "count": 99, "note": "note0"}),
    ("bars", {"action": "pickup", "count": 2}),
    ("bars", {"action": "pickup", "count": 16}),
    # A chord at a moment past its bar's end was saved, at onset 1e+300, and a tuplet of no notes was a 500.
    ("chords/at", {"measure": 0, "onset": 1e300, "text": "C"}),
    ("chords/at", {"measure": 0, "onset": 4, "text": "C"}),
    ("paste", {"at": "note0", "events": [{"type": "eighth", "pitches": [["C", 0, 4]], "triplet": 0, "actual": 0}]}),
    ("paste", {"at": "note0", "events": [{"type": "eighth", "pitches": [["C", 0, 4]], "triplet": 0, "actual": -3}]}),
]


@pytest.mark.parametrize(("route", "body"), REFUSED_EDITS, ids=lambda item: item if isinstance(item, str) else "-".join(str(value) for value in item.values())[:40])
def test_refused_edits_explain_themselves_and_save_nothing(client, ready_score, route, body):
    url = f"/api/scores/{ready_score}"
    before = client.get(f"{url}/score.musicxml").text
    response = client.post(f"{url}/{route}", json=body)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str) and response.json()["detail"]
    assert client.get(url).json()["canUndo"] is False
    assert client.get(f"{url}/score.musicxml").text == before


def test_a_musicxml_file_opens_ready_to_edit(client, ready_score):
    text = client.get(f"/api/scores/{ready_score}/score.musicxml").content
    opened = client.post("/api/scores", files={"file": ("tune.musicxml", io.BytesIO(text))}).json()
    score = client.get(f"/api/scores/{opened['id']}").json()
    assert score["status"] == "ready"
    assert score["pages"] and "Dm7" in score["chords"][0]["text"]


def test_a_file_that_prints_its_title_only_on_the_page_is_listed_by_it_as_opened_from_a_file(client):
    """MuseScore 2 wrote a score's title and composer only as words on its first page."""
    root = parsed(note())
    credits = [("Maple Leaf Rag", "24", "center", "1627"), ("Scott Joplin", "12", "right", "1527")]
    for place, (words, size, justify, high) in enumerate(credits):
        credit = ET.Element("credit", page="1")
        ET.SubElement(credit, "credit-words", {"default-y": high, "font-size": size, "justify": justify}).text = words
        root.insert(place, credit)
    opened = client.post("/api/scores", files={"file": ("rag.musicxml", io.BytesIO(to_text(root).encode()))}).json()["id"]
    score = client.get(f"/api/scores/{opened}").json()
    assert (score["title"], score["composer"]) == ("Maple Leaf Rag", "Scott Joplin")
    written = blank(client)
    listed = {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}
    assert listed[opened]["title"] == "Maple Leaf Rag" and listed[opened]["opened"] is True
    assert listed[written]["opened"] is False and listed[written]["blank"] is True


def test_a_score_opened_before_files_were_marked_is_still_known_by_who_wrote_it(client):
    """A file from another program names it, which a score begun here never does."""
    root = parsed(note())
    identification = ET.Element("identification")
    ET.SubElement(ET.SubElement(identification, "encoding"), "software").text = "MuseScore 2.1.0"
    root.insert(0, identification)
    opened = client.post("/api/scores", files={"file": ("old.musicxml", io.BytesIO(to_text(root).encode()))}).json()["id"]
    (server.JOB_DIR / opened / "opened.json").unlink()
    assert {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}[opened]["opened"] is True


@pytest.mark.parametrize(("name", "data"), [("broken.musicxml", b"<nonsense"), ("broken.mxl", b"not a zip"), ("page.xml", b"<html/>")])
def test_a_broken_score_file_is_refused_at_upload(client, name, data):
    response = client.post("/api/scores", files={"file": (name, io.BytesIO(data))})
    assert response.status_code == 415
    assert "detail" in response.json()


def test_a_part_prints_alone_and_a_missing_part_is_refused(client, ready_score):
    url = f"/api/scores/{ready_score}"
    client.post(f"{url}/parts", json={"action": "add", "name": "trumpet"})
    assert client.get(f"{url}/score.pdf?part=1").content.startswith(b"%PDF")
    assert client.get(f"{url}/score.pdf?part=7").status_code in (404, 422)


def test_midi_downloads_with_its_own_type(client, ready_score):
    response = client.get(f"/api/scores/{ready_score}/score.mid")
    assert response.content.startswith(b"MThd")
    assert response.headers["content-type"] == "audio/midi"


def test_the_piano_samples_are_served_as_audio(client):
    """A piano part plays recordings of each minor third from A0 to C8, which the page fetches by name."""
    names = ["A0", *(f"{letter}{octave}" for octave in range(1, 8) for letter in ("C", "Ds", "Fs", "A")), "C8"]
    assert len(names) == 30
    for name in names:
        response = client.get(f"/sounds/piano/{name}.mp3")
        assert response.status_code == 200, name
        assert response.headers["content-type"] == "audio/mpeg", name
        assert response.content.startswith(b"ID3"), name


# The notes recorded of each string instrument, bowed and plucked, by the note each sounds.
STRING_SAMPLES = {
    "guitar": ["E2", "G2", "B2", "D3", "F3", "Gs3", "B3", "D4", "F4", "A4", "C5", "Ds5", "Fs5", "A5", "C6"],
    "bass-guitar": ["E1", "Fs1", "A1", "C2", "Ds2", "Fs2", "A2", "Ds3", "A3", "Ds4"],
    "violin": ["G3", "C4", "E4", "G4", "C5", "E5", "G5", "C6", "E6", "A6"],
    "violin/pizzicato": ["G3", "C4", "E4", "G4", "C5", "E5", "A5", "A6"],
    "viola": ["C3", "E3", "G3", "B3", "F4", "A4", "E5", "B5"],
    "viola/pizzicato": ["C3", "E3", "G3", "D4", "A4", "G5"],
    "cello": ["C2", "Ds2", "Fs2", "A2", "C3", "Ds3", "Fs3", "C4", "Fs4", "C5", "Fs5", "C6"],
    "cello/pizzicato": ["C2", "Fs2", "C3", "Fs3", "C4", "Fs4", "C5"],
    "double-bass": ["Fs1", "As1", "D2", "Fs2", "A2", "Cs3", "E3", "Gs3", "B3"],
    "double-bass/pizzicato": ["Ds1", "Fs1", "A1", "Ds2", "A2", "Fs3", "C4"],
}


def test_the_string_samples_are_served_as_audio(client):
    """Guitar, bass and bowed-string parts play recordings, as FLAC, which the page fetches by name."""
    for folder, names in STRING_SAMPLES.items():
        for name in names:
            response = client.get(f"/sounds/{folder}/{name}.flac")
            assert response.status_code == 200, (folder, name)
            assert response.headers["content-type"].startswith("audio/"), (folder, name, response.headers["content-type"])
            assert response.content.startswith(b"fLaC"), (folder, name)
    assert sum(len(names) for names in STRING_SAMPLES.values()) == 92


def test_marks_carry_through_copy_and_paste_over_the_server(client, ready_score):
    url = f"/api/scores/{ready_score}"
    client.post(f"{url}/marks", json={"action": "toggle", "name": "accent", "notes": ["note0"]})
    copied = client.post(f"{url}/copy", json={"first": "note0", "last": "note0"}).json()
    assert client.post(f"{url}/paste", json={"at": "note3", "events": copied["events"]}).status_code == 200
    assert client.get(f"{url}/score.musicxml").text.count("<accent") == 2


def test_clickable_marks_are_listed_with_the_score(client, ready_score):
    url = f"/api/scores/{ready_score}"
    client.post(f"{url}/marks", json={"action": "dynamic", "name": "pp", "notes": ["note1"]})
    listed = client.get(url).json()["marks"]
    assert [(item["kind"], item["text"]) for item in listed] == [("dynamic", "pp")]
    done = client.post(f"{url}/marks", json={"action": "label", "id": listed[0]["id"], "text": "ff"}).json()
    assert done["marks"][0]["text"] == "ff"
    gone = client.post(f"{url}/marks", json={"action": "remove", "id": listed[0]["id"]}).json()
    assert gone["marks"] == []


def test_text_xml_cannot_hold_is_cleaned_before_saving(client):
    """A control character pasted from a word processor, or half of a broken one, used to save a version that wouldn't open."""
    score_id = blank(client)
    url = f"/api/scores/{score_id}"
    # A browser can send half a character as a JSON escape, which Python's own encoder won't write.
    body = b'{"title": "Song\\u000bTitle\\u0001", "composer": "A\\ud800B"}'
    data = client.post(f"{url}/title", content=body, headers={"Content-Type": "application/json"}).json()
    assert (data["title"], data["composer"]) == ("Song Title", "AB")
    written = write(client, score_id, first_id(client, score_id), "C")["written"]
    assert client.post(f"{url}/lyrics/{written}", json={"text": "la\x01"}).status_code == 200
    assert client.post(f"{url}/marks", json={"action": "text", "text": "so\x08lo", "notes": [written]}).status_code == 200
    assert client.get(url).json()["marks"][0]["text"] == "solo"
    assert client.get("/api/scores").json()[0]["title"] == "Song Title"
    for path in (server.JOB_DIR / score_id).glob("version_*.musicxml"):
        ET.parse(path)


def test_a_save_that_fails_part_way_leaves_the_last_version_in_place(client, monkeypatch):
    score_id = blank(client)
    folder = server.JOB_DIR / score_id
    before = sorted(path.name for path in folder.iterdir())
    with monkeypatch.context() as patch:
        patch.setattr(server, "to_text", lambda root: "<score-partwise>\ud800")  # This can't be written as UTF-8.
        with pytest.raises(UnicodeEncodeError):
            server.save_version(folder, server.current_root(folder))
    assert sorted(path.name for path in folder.iterdir()) == before
    assert client.get(f"/api/scores/{score_id}").status_code == 200


def test_one_unreadable_score_does_not_hide_the_others(client):
    good, cut_short = blank(client), blank(client)
    (server.JOB_DIR / cut_short / "version_0001.musicxml").write_text("<score-partwise", encoding="utf-8")
    broken_status = server.JOB_DIR / ("f" * 32)
    broken_status.mkdir()
    (broken_status / "status.json").write_text("{", encoding="utf-8")
    no_versions = server.JOB_DIR / ("e" * 32)
    no_versions.mkdir()
    server.write_status(no_versions, "ready")
    assert [sheet["id"] for sheet in client.get("/api/scores").json()] == [good]


def test_versions_past_9999_keep_every_edit_and_undo_in_order(client, ready_score):
    """Sorted by name, version_10000 came before version_9999, so every edit from there on was lost."""
    folder = server.JOB_DIR / ready_score
    (folder / "version_0000.musicxml").rename(folder / "version_9998.musicxml")
    url = f"/api/scores/{ready_score}"
    for _ in range(3):
        assert client.post(f"{url}/notes/note0", json={"action": "up"}).status_code == 200
    assert [path.name for path in server.versions(folder)][-2:] == ["version_10000.musicxml", "version_10001.musicxml"]
    assert pitches(client, ready_score)[0] == "F"
    client.post(f"{url}/undo")
    client.post(f"{url}/undo")
    assert pitches(client, ready_score)[0] == "D"
    client.post(f"{url}/redo")
    assert pitches(client, ready_score)[0] == "E"


def test_only_the_last_versions_are_kept_with_the_first(client, monkeypatch):
    """Every edit kept a whole copy of the score for ever, so a long session on a dense score filled
    hundreds of megabytes. The first stays: it's the reading the real-world benchmark compares with,
    and how a score opened from a file is known when it predates opened.json."""
    monkeypatch.setattr(server, "VERSIONS_KEPT", 3)
    root = parsed(note())
    identification = ET.Element("identification")
    ET.SubElement(ET.SubElement(identification, "encoding"), "software").text = "MuseScore 2.1.0"
    root.insert(0, identification)
    score_id = client.post("/api/scores", files={"file": ("old.musicxml", io.BytesIO(to_text(root).encode()))}).json()["id"]
    folder = server.JOB_DIR / score_id
    (folder / "opened.json").unlink()
    for title in "ABCDEF":
        assert client.post(f"/api/scores/{score_id}/title", json={"title": title}).status_code == 200
    assert [path.name for path in server.versions(folder)] == ["version_0000.musicxml", "version_0004.musicxml", "version_0005.musicxml", "version_0006.musicxml"]
    assert {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}[score_id]["opened"] is True
    titles = []
    while client.get(f"/api/scores/{score_id}").json()["canUndo"]:
        titles.append(client.post(f"/api/scores/{score_id}/undo").json()["title"])
    assert titles == ["E", "D", ""], "undo walks back through what's kept, to the score as it was opened"
    assert client.post(f"/api/scores/{score_id}/redo").json()["title"] == "D"


def test_a_pdfs_page_pictures_go_once_it_is_read_and_what_the_score_uses_stays(client, monkeypatch):
    """Each page of a PDF was kept as a 250-dpi PNG for good, though only the reader looks at them. The
    upload, the pictures shown beside the score and the first version are still used, so they stay."""
    from chords import corrections

    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    assert not (folder / "pages").exists()
    assert upload.exists() and (folder / "version_0000.musicxml").exists()
    assert all(client.get(f"/api/scores/{folder.name}/source/{number}.jpg").status_code == 200 for number in (1, 2, 3))
    (folder / "checked.json").write_text("{}", encoding="utf-8")
    gathered = corrections.export(server.JOB_DIR, server.JOB_DIR / ".corrections")  # It hashes the upload, and crops page.jpg.
    assert gathered["train_sheets"] + gathered["test_sheets"] == 1 and gathered["confirmed"] == 3

    left = server.JOB_DIR / ("d" * 32)  # Read before page pictures were removed.
    (left / "pages").mkdir(parents=True)
    (left / "pages" / "page_001.png").write_bytes(b"png")
    server.write_status(left, "ready")
    with TestClient(server.app):  # Starting the app clears them away.
        pass
    assert not (left / "pages").exists() and (left / "status.json").exists()


def test_a_selection_holding_both_ends_of_a_tie_is_respelled_once(client):
    score_id = "c" * 32
    folder = server.JOB_DIR / score_id
    folder.mkdir()
    server.write_status(folder, "ready")
    root = parsed(tied_note("C", 1, 8, "whole", ["start"]), tied_note("C", 1, 8, "whole", ["stop"]))
    server.edit.ensure_note_ids(root)
    server.save_version(folder, root)
    assert client.post(f"/api/scores/{score_id}/group", json={"action": "respell", "notes": ["note0", "note1"]}).status_code == 200
    notes = notes_of(client, score_id)
    assert [(item.findtext("pitch/step"), item.findtext("pitch/alter")) for item in notes] == [("D", "-1")] * 2
    assert [tie.get("type") for item in notes for tie in item.findall("tie")] == ["start", "stop"]


def test_sheets_still_being_read_when_the_app_stopped_are_marked_failed(client):
    """Nothing reads them after a restart, so the page would wait for them forever."""
    stopped = server.JOB_DIR / ("d" * 32)
    stopped.mkdir()
    server.write_status(stopped, "processing", "Reading page 2 of 3.")
    unreadable = server.JOB_DIR / ("e" * 32)
    unreadable.mkdir()
    (unreadable / "status.json").write_text("{", encoding="utf-8")
    ready = blank(client)
    with TestClient(server.app):  # Starting the app is what notices them.
        pass
    data = client.get(f"/api/scores/{stopped.name}").json()
    assert data["status"] == "failed" and "again" in data["message"]
    assert client.get(f"/api/scores/{ready}").json()["status"] == "ready"


def test_saved_files_keep_the_usual_permissions(client):
    folder = server.JOB_DIR / blank(client)
    ordinary = folder / "ordinary.txt"
    ordinary.write_text("x", encoding="utf-8")
    for path in [*folder.glob("version_*.musicxml"), folder / "status.json"]:
        assert path.stat().st_mode & 0o777 == ordinary.stat().st_mode & 0o777


def test_a_page_on_another_site_cannot_upload_or_change_anything(client, ready_score):
    """A browser sends the app requests from any page that's open, and says which site the page is from."""
    theirs = {"Origin": "https://example.com"}
    score = to_text(parsed(note() + note("D") + note("E") + note("F"))).encode()
    response = client.post("/api/scores", files={"file": ("score.musicxml", io.BytesIO(score))}, headers=theirs)
    assert response.status_code == 403 and "another site" in response.json()["detail"]
    assert [folder.name for folder in server.JOB_DIR.iterdir()] == [ready_score]
    client.post(f"/api/scores/{ready_score}/chords/0", json={"text": "Bb7"})
    assert client.post(f"/api/scores/{ready_score}/undo", headers=theirs).status_code == 403
    assert client.post(f"/api/scores/{ready_score}/undo", headers={"Origin": "null"}).status_code == 403
    assert client.get(f"/api/scores/{ready_score}").json()["chords"][0]["text"] == "Bb7"


@pytest.mark.parametrize("address", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST:8000"])
def test_the_apps_own_page_can_change_things(client, address):
    own = {"Host": address, "Origin": f"http://{address.lower()}"}
    assert client.post("/api/scores/blank", json={"measures": 1}, headers=own).status_code == 200
    assert client.post("/api/scores/blank", json={"measures": 1}, headers={"Host": address}).status_code == 200  # A script.


@pytest.mark.parametrize("name", ["example.com:8000", "127.0.0.1.example.com", "localhost.example.com:8000", "[::1", ""])
def test_a_site_whose_name_points_at_this_computer_is_not_served(client, ready_score, name):
    """Its page would otherwise count as the app's own, and could read every score."""
    assert client.get("/api/scores", headers={"Host": name}).status_code == 403
    assert client.get(f"/api/scores/{ready_score}/score.musicxml", headers={"Host": name}).status_code == 403
    assert client.get("/", headers={"Host": name}).status_code == 403


def test_a_page_being_read_stops_when_the_app_does(client, tmp_path, monkeypatch):
    """Left running it would read on with nothing to time it out, and nothing to take its answer."""
    stand_in = tmp_path / "stand_in"
    (stand_in / "pipeline").mkdir(parents=True)
    (stand_in / "pipeline" / "__init__.py").write_text("", encoding="utf-8")
    lines = ["import os, pathlib, time", "pathlib.Path('started').write_text(str(os.getpid()))", "time.sleep(60)"]
    (stand_in / "pipeline" / "recognize.py").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(server, "ROOT", stand_in)  # The page is read by whatever pipeline.recognize is there.
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    reader = threading.Thread(target=server.recognize_page, args=(tmp_path / "page.png", tmp_path / "out"), daemon=True)
    reader.start()
    started = stand_in / "started"
    for _ in range(200):
        if started.exists() and started.read_text():
            break
        time.sleep(0.05)
    process = int(started.read_text())
    try:
        with TestClient(server.app):  # Starting the app, then stopping it.
            pass
        reader.join(5)
        assert not reader.is_alive()
        with pytest.raises(ProcessLookupError):
            os.kill(process, 0)
        assert server.recognize_page(tmp_path / "page.png", tmp_path / "out") == server.INTERRUPTED  # And no other starts.
    finally:
        try:
            os.kill(process, signal.SIGKILL)
        except ProcessLookupError:
            pass
        reader.join(5)


def test_a_sheet_whose_reading_was_stopped_is_left_for_the_next_start_to_report(client, monkeypatch):
    """Its later pages mustn't be tried, and the pages read so far mustn't pass for the whole sheet."""
    tried = []

    def stopped_during_page_two(image, out_dir):
        tried.append(out_dir.name)
        if len(tried) == 2:
            server.stopping.set()
            return "Recognition failed."
        return fake_recognizer()(image, out_dir)

    monkeypatch.setattr(server, "recognize_page", stopped_during_page_two)
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    assert tried == ["page_001", "page_002"] and server.read_status(folder)["status"] == "processing"


def photo(shade="black"):
    """A small photo, with something dark on it unless it's all white."""
    image = Image.new("RGB", (120, 80), "white")
    image.paste(Image.new("RGB", (100, 4), shade), (10, 30))
    data = io.BytesIO()
    image.save(data, format="PNG")
    return data.getvalue()


def upload_photo(client, name, shade="black"):
    response = client.post("/api/scores", files={"file": (name, io.BytesIO(photo(shade)))})
    assert response.status_code == 200
    return response.json()["id"]


def test_photos_sent_together_are_read_as_the_pages_of_one_score(client, monkeypatch):
    """Only the first of several files was opened, so a piece photographed page by page couldn't be
    one score: each photo had to be a score of its own, or be made into a PDF elsewhere first."""
    seen = []
    fake = fake_recognizer()
    monkeypatch.setattr(server, "recognize_page", lambda image, out_dir: seen.append(image.read_bytes()) or fake(image, out_dir))
    pages = [("autumn leaves 1.png", photo("black")), ("autumn leaves 2.png", photo("gray")), ("autumn leaves 3.jpg", photo("red"))]
    response = client.post("/api/scores", files=[("file", (name, io.BytesIO(data))) for name, data in pages])
    assert response.status_code == 200
    score_id = response.json()["id"]
    assert until(lambda: client.get(f"/api/scores/{score_id}").json()["status"] == "ready")
    data = client.get(f"/api/scores/{score_id}").json()
    assert seen == [data for _, data in pages], "read in the order they were sent"
    assert data["sourcePages"] == [1, 2, 3] and [chord["page"] for chord in data["chords"]] == [1, 2, 3]
    assert data["title"] == "Autumn leaves 1"
    assert {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}[score_id]["pages"] == 3

    seen.clear()
    server.write_status(server.JOB_DIR / score_id, "failed", "Recognition failed.")
    assert client.post(f"/api/scores/{score_id}/retry").status_code == 200
    assert until(lambda: client.get(f"/api/scores/{score_id}").json()["status"] == "ready") and len(seen) == 3, "Try again reads every page"


@pytest.mark.parametrize(("name", "slow"), [("song.musicxml", (server, "open_score_file")), ("page.png", (server.cv2, "imdecode"))], ids=["musicxml", "photo"])
def test_opening_a_file_holds_up_no_other_request(client, monkeypatch, name, slow):
    """A file was opened on the server's one event loop: a large MusicXML engraved twice, or a photo
    decoded, before anything else was answered, as the library's polls or an edit in another window."""
    entered, release = threading.Event(), threading.Event()
    real = getattr(*slow)

    def held(*arguments):
        entered.set()
        release.wait(10)
        return real(*arguments)

    monkeypatch.setattr(*slow, held)
    data = to_text(parsed(note())).encode() if name.endswith(".musicxml") else photo()
    with TestClient(server.app, base_url="http://127.0.0.1:8000") as shared, ThreadPoolExecutor(1) as pool:  # One event loop for both.
        opening = pool.submit(shared.post, "/api/scores", files={"file": (name, io.BytesIO(data))})
        assert entered.wait(10)
        started = time.monotonic()
        answered = shared.get("/api/instruments").status_code
        waited = time.monotonic() - started
        release.set()
        assert opening.result().status_code == 200
    assert answered == 200 and waited < 5, waited


@pytest.mark.parametrize("names", [("page.png", "piece.pdf"), ("page.png", "song.musicxml"), ("a.pdf", "b.pdf")])
def test_only_photos_are_read_together(client, names):
    files = [("file", (name, io.BytesIO(photo()))) for name in names]
    response = client.post("/api/scores", files=files)
    assert response.status_code == 415 and "on its own" in response.json()["detail"] and client.get("/api/scores").json() == []


def test_too_many_photos_together_are_refused(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_PDF_PAGES", 2)
    response = client.post("/api/scores", files=[("file", (f"{number}.png", io.BytesIO(photo()))) for number in range(3)])
    assert response.status_code == 422 and "2" in response.json()["detail"] and client.get("/api/scores").json() == []


def test_one_bad_photo_among_several_refuses_them_all(client):
    files = [("file", ("1.png", io.BytesIO(photo()))), ("file", ("2.png", io.BytesIO(b"not a photo")))]
    response = client.post("/api/scores", files=files)
    assert response.status_code == 415 and "2.png" in response.json()["detail"] and client.get("/api/scores").json() == []


def until(condition, seconds=20):
    for _ in range(int(seconds / 0.05)):
        if condition():
            return True
        time.sleep(0.05)
    return False


def test_a_sheet_waiting_its_turn_is_listed_with_how_many_are_ahead(client, monkeypatch):
    """The window used to say nothing about a second sheet, which waited unseen behind the first."""
    finish = threading.Event()
    read = []

    def slow(folder, upload):
        read.append(folder.name)
        finish.wait(20)
        server.write_status(folder, "failed", "Recognition failed.")

    monkeypatch.setattr(server, "recognize_job", slow)
    first = upload_photo(client, "first page.jpg")
    second = upload_photo(client, "second page.jpg")
    try:
        assert until(lambda: read == [first])
        sheets = {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}
        assert sheets[first]["status"] == sheets[second]["status"] == "processing"
        assert (sheets[first]["ahead"], sheets[second]["ahead"]) == (0, 1)
        assert sheets[first]["name"] == "first page.jpg" and sheets[first]["elapsed"] is not None and sheets[second]["elapsed"] is None
        assert client.get(f"/api/scores/{second}").json() | {"message": ""} == {"status": "processing", "message": "", "name": "second page.jpg", "pdf": False, "ahead": 1, "elapsed": None}
    finally:
        finish.set()
    assert until(lambda: read == [first, second] and not server.line)


def test_cancelling_a_sheet_waiting_its_turn_removes_it_unread(client, monkeypatch):
    finish = threading.Event()
    read = []

    def slow(folder, upload):
        read.append(folder.name)
        finish.wait(20)
        server.write_status(folder, "failed", "Recognition failed.")

    monkeypatch.setattr(server, "recognize_job", slow)
    first = upload_photo(client, "first.png")
    second = upload_photo(client, "second.png")
    try:
        assert until(lambda: read == [first])
        assert client.delete(f"/api/scores/{second}").json() == {"removed": second, "undo": False}
        assert not (server.JOB_DIR / second).exists() and [sheet["id"] for sheet in client.get("/api/scores").json()] == [first]
    finally:
        finish.set()
    assert until(lambda: not server.line) and read == [first]


def test_cancelling_the_sheet_being_read_stops_its_reader_and_starts_the_next(client, tmp_path, monkeypatch):
    """Cancel has to stop the reader itself: hidden, it would hold its memory and the line for minutes."""
    stand_in = write_stand_in(tmp_path / "stand_in", seconds=60)
    monkeypatch.setattr(server, "ROOT", stand_in)
    monkeypatch.setattr(server.shutil, "which", lambda name: None)
    started = stand_in / "started"
    first = upload_photo(client, "first.png")
    second = upload_photo(client, "second.png")
    readers = []
    try:
        assert until(lambda: started.exists() and started.read_text())
        readers.append(int(started.read_text()))
        assert client.delete(f"/api/scores/{first}").json() == {"removed": first, "undo": False}
        with pytest.raises(ProcessLookupError):
            os.kill(readers[0], 0)
        assert not (server.JOB_DIR / first).exists()
        assert client.get(f"/api/scores/{first}").status_code == 404
        assert until(lambda: started.read_text() != str(readers[0]))  # The next sheet's turn has come.
        readers.append(int(started.read_text()))
        assert [(sheet["id"], sheet["ahead"]) for sheet in client.get("/api/scores").json()] == [(second, 0)]
        assert client.delete(f"/api/scores/{second}").json() == {"removed": second, "undo": False}
        assert until(lambda: not server.line) and not (server.JOB_DIR / second).exists()
    finally:
        for reader in readers:
            try:
                os.kill(reader, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_sheet_that_could_not_be_read_stays_listed_with_why_to_read_again_or_remove(client, ready_score, monkeypatch):
    """It was dropped with a message that went as soon as anything else was done, and nobody saw it if they'd gone elsewhere."""
    def ready(folder, upload):
        root = parsed(note() + note("D") + note("E") + note("F"))
        server.edit.ensure_note_ids(root)
        server.save_version(folder, root)
        server.write_status(folder, "ready")

    monkeypatch.setattr(server, "recognize_job", lambda folder, upload: server.write_status(folder, "failed", server.NO_STAVES))
    failed = upload_photo(client, "blurry.jpg")
    assert until(lambda: not server.line)
    listed = {sheet["id"]: sheet for sheet in client.get("/api/scores").json()}[failed]
    assert (listed["status"], listed["message"], listed["name"]) == ("failed", server.NO_STAVES, "blurry.jpg")
    monkeypatch.setattr(server, "recognize_job", ready)
    assert client.post(f"/api/scores/{failed}/retry").json() == {"id": failed}
    assert until(lambda: not server.line) and client.get(f"/api/scores/{failed}").json()["status"] == "ready"
    assert client.post(f"/api/scores/{failed}/retry").status_code == 409  # Read now, so there's nothing to try again.

    monkeypatch.setattr(server, "recognize_job", lambda folder, upload: server.write_status(folder, "failed", server.NO_STAVES))
    other = upload_photo(client, "dark.jpg")
    assert until(lambda: not server.line)
    assert client.delete(f"/api/scores/{other}").json() == {"removed": other, "undo": True} and not (server.JOB_DIR / other).exists()
    assert client.post(f"/api/scores/{other}/restore").json() == {"id": other}
    assert {sheet["id"]: sheet["status"] for sheet in client.get("/api/scores").json()}[other] == "failed"  # Back as it was.


def test_a_deleted_score_leaves_the_library_and_comes_back_if_undone(client, ready_score):
    """There was no way to delete a score at all."""
    url = f"/api/scores/{ready_score}"
    client.post(f"{url}/chords/0", json={"text": "Bb7"})
    client.post(f"{url}/checked", json={"checked": True})
    assert client.delete(url).json() == {"removed": ready_score, "undo": True}
    assert client.get(url).status_code == 404 and client.get("/api/scores").json() == []
    assert server.corrections.checked_sheets(server.JOB_DIR) == []  # Nor is it learnt from.
    assert client.post(f"{url}/chords/0", json={"text": "C"}).status_code == 404
    assert client.post(f"{url}/restore").json() == {"id": ready_score}
    back = client.get(url).json()
    assert back["chords"][0]["text"] == "Bb7" and back["canUndo"] is True and back["checked"] is True  # As it was.
    assert [sheet["id"] for sheet in client.get("/api/scores").json()] == [ready_score]
    assert client.post(f"{url}/restore").status_code == 404  # Nothing left to bring back.


def test_the_trash_is_emptied_a_week_after_a_delete(client, ready_score):
    client.delete(f"/api/scores/{ready_score}")
    kept = server.JOB_DIR / server.TRASH / ready_score
    week = time.time() - server.TRASH_SECONDS - 60
    os.utime(kept, (week, week))
    other = blank(client)
    client.delete(f"/api/scores/{other}")  # Deleting empties what's old.
    assert not kept.exists() and (server.JOB_DIR / server.TRASH / other).exists()
    assert client.post(f"/api/scores/{ready_score}/restore").status_code == 404


def long_score(name):
    """A ready score of twelve bars of four notes, saved straight to disk, with its note ids."""
    score_id = name * 32
    folder = server.JOB_DIR / score_id
    folder.mkdir()
    server.write_status(folder, "ready")
    root = parsed(*["".join(note("CDEFGAB"[(bar * 4 + beat) % 7]) for beat in range(4)) for bar in range(12)])
    server.edit.ensure_note_ids(root)
    server.save_version(folder, root)
    return score_id, [item.get("id") for item in root.iter("note")]


def searches_during(client, monkeypatch, url, body):
    """Count the times a request goes through the whole score to find a note or a bar's place."""
    searches = []

    class Watched(ET.Element):
        def iter(self, tag=None):
            if self.tag == "score-partwise" and tag == "note":
                searches.append(tag)
            return super().iter(tag)

    def watched_root(folder):
        return ET.parse(server.versions(folder)[-1], ET.XMLParser(target=ET.TreeBuilder(element_factory=Watched))).getroot()

    every_bar = rhythm.contexts

    def counted_bars(part):
        searches.append("bars")
        return every_bar(part)

    with monkeypatch.context() as patched:
        patched.setattr(server, "current_root", watched_root)
        for module in (rhythm, server.edit):
            patched.setattr(module, "contexts", counted_bars)
        assert client.post(url, json=body).status_code == 200
    return len(searches)


@pytest.mark.parametrize(("route", "body"), [
    ("group", {"action": "up"}),
    ("group", {"action": "sharp"}),
    ("group", {"action": "respell"}),
    ("group", {"action": "flip"}),
    ("marks", {"action": "toggle", "name": "staccato"}),
    ("copy", {}),
], ids=["up", "sharp", "respell", "flip", "staccato", "copy"])
def test_an_edit_to_many_notes_does_not_search_the_score_for_each(client, monkeypatch, route, body):
    counts = []
    for name, size in (("a", 4), ("b", 40)):
        score_id, ids = long_score(name)
        chosen = ids[:size]
        sent = {"first": chosen[0], "last": chosen[-1]} if route == "copy" else body | {"notes": chosen}
        counts.append(searches_during(client, monkeypatch, f"/api/scores/{score_id}/{route}", sent))
    assert counts[1] == counts[0]


def test_a_group_delete_passes_over_a_grace_note_that_went_with_its_note(client):
    score_id, ids = long_score("c")
    url = f"/api/scores/{score_id}"
    grace = client.post(f"{url}/marks", json={"action": "grace", "notes": [ids[1]]}).json()["written"]
    assert client.post(f"{url}/group", json={"action": "delete", "notes": [ids[1], grace, ids[2]]}).status_code == 200
    left = notes_of(client, score_id)
    assert grace not in [item.get("id") for item in left] and [item.find("rest") is not None for item in left[:4]] == [False, True, True, False]


PIECE = 64 * 1024


def upload_in_pieces(size, stated):
    """Send a photo of this many bytes straight to the app, a piece at a time, as a browser would.

    Returns the status and how many pieces the app took in. `stated` says whether the request
    gives its size up front, as a browser's does, or only ends when it ends.
    """
    form = b'--edge\r\nContent-Disposition: form-data; name="file"; filename="page.png"\r\nContent-Type: image/png\r\n\r\n'
    body = form + b"\0" * size + b"\r\n--edge--\r\n"
    pieces = [body[start:start + PIECE] for start in range(0, len(body), PIECE)]
    taken = 0
    sent = []

    async def receive():
        nonlocal taken
        if taken == len(pieces):
            return {"type": "http.disconnect"}
        taken += 1
        return {"type": "http.request", "body": pieces[taken - 1], "more_body": taken < len(pieces)}

    async def send(message):
        sent.append(message)

    headers = [(b"host", b"127.0.0.1:8000"), (b"content-type", b"multipart/form-data; boundary=edge")]
    if stated:
        headers.append((b"content-length", str(len(body)).encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": "/api/scores", "raw_path": b"/api/scores", "root_path": "", "query_string": b"", "headers": headers,
        "client": ("127.0.0.1", 50000), "server": ("127.0.0.1", 8000),
    }
    asyncio.run(server.app(scope, receive, send))
    return next(message["status"] for message in sent if message["type"] == "http.response.start"), taken


def test_an_upload_that_says_it_is_too_large_is_refused_before_it_is_taken_in(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_SENT_BYTES", 1024 * 1024)
    assert upload_in_pieces(2 * 1024 * 1024, stated=True) == (413, 0)


def test_an_upload_that_does_not_say_its_size_stops_being_taken_in_at_the_limit(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_SENT_BYTES", 1024 * 1024)
    status, taken = upload_in_pieces(4 * 1024 * 1024, stated=False)
    assert status == 413 and taken * PIECE <= 2 * 1024 * 1024


def test_one_file_over_its_own_limit_is_refused_though_several_together_may_be_larger(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024 * 1024)
    response = client.post("/api/scores", files={"file": ("page.png", photo() + b"\0" * 2 * 1024 * 1024)})
    assert response.status_code == 413 and response.json()["detail"] == server.TOO_LARGE


def test_an_upload_within_the_limit_is_still_taken(client, monkeypatch):
    monkeypatch.setattr(server, "MAX_UPLOAD_BYTES", 1024 * 1024)
    monkeypatch.setattr(server, "recognize_job", lambda folder, upload: None)
    image = io.BytesIO()
    Image.new("RGB", (40, 40), "white").save(image, "PNG")
    padded = image.getvalue() + b"\0" * (1024 * 1024 - len(image.getvalue()))  # A PNG may carry data after its end.
    assert client.post("/api/scores", files={"file": ("page.png", padded)}).status_code == 200


def test_a_pdf_page_too_large_to_read_in_full_detail_is_read_at_a_size_that_fits(tmp_path):
    import pypdfium2

    document = pypdfium2.PdfDocument.new()
    document.new_page(2000, 1000)  # 28 inches across, which 250 dots an inch would make 7,000 pixels.
    document.new_page(595, 842)  # A4, which is read in full detail.
    document.save(tmp_path / "upload.pdf")
    document = server.opened_pdf(tmp_path / "upload.pdf")
    images = [server.draw_pdf_page(document, index, tmp_path / "pages") for index in range(2)]
    document.close()
    assert [Image.open(image).size for image in images] == [(5000, 2500), (2066, 2924)]


def test_a_pdfs_pages_are_drawn_as_they_are_read_so_a_cancel_stops_the_drawing(client, monkeypatch):
    """Every page of a PDF, up to 40 at 250 dots an inch, was drawn before the first was read, inside
    the server with no limit, and a cancel waited for all of them."""
    drawn = []

    def cancelled_during_page_one(image, out_dir):
        folder = out_dir.parent.parent
        drawn.append(sorted(path.name for path in (folder / "pages").glob("*.png")))
        server.cancelled.add(folder.name)
        return fake_recognizer()(image, out_dir)

    monkeypatch.setattr(server, "recognize_page", cancelled_during_page_one)
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    assert drawn == [["page_001.png"]]


def locked_pdf() -> bytes:
    """A PDF that asks for a password, as a bank statement or a shop's sheet music might."""
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>",
               b"<< /Filter /Standard /V 1 /R 2 /O <" + b"11" * 32 + b"> /U <" + b"22" * 32 + b"> /P -4 >>"]
    out, offsets = b"%PDF-1.4\n", []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1) + b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R /Encrypt 4 0 R /ID [<%s> <%s>] >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, b"ab" * 16, b"ab" * 16, start)


@pytest.mark.parametrize(("data", "reason"), [(locked_pdf(), "password"), (b"%PDF-1.4 and nothing more", "couldn't be opened")], ids=["locked", "broken"])
def test_a_pdf_that_cant_be_opened_says_why(client, data, reason):
    """A password-protected or broken PDF ended as the general "Recognition failed."."""
    folder = server.JOB_DIR / ("e" * 32)
    folder.mkdir()
    (folder / "upload.pdf").write_bytes(data)
    server.write_status(folder, "processing")
    server.recognize_job(folder, folder / "upload.pdf")
    status = server.read_status(folder)
    assert status["status"] == "failed" and reason in status["message"], status


@pytest.mark.parametrize(("name", "data", "reason"), [
    ("big.mxl", "unpacks too large", "That file is too large."),
    ("page.xml", b"<html/>", "Only MusicXML scores written part by part can be opened."),
    ("broken.musicxml", b"<nonsense", "That file couldn't be read as MusicXML."),
], ids=["too-large", "not-part-by-part", "not-musicxml"])
def test_a_score_file_that_cannot_be_opened_says_why(client, monkeypatch, name, data, reason):
    if data == "unpacks too large":
        monkeypatch.setattr("pipeline.musicxml.MOST_UNPACKED_BYTES", 1000)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
            archive.writestr("score.xml", "<score-partwise><part/></score-partwise>" + " " * 5000)
        data = buffer.getvalue()
    response = client.post("/api/scores", files={"file": (name, io.BytesIO(data))})
    assert response.status_code == 415 and response.json()["detail"] == reason


def test_a_score_file_that_cannot_be_drawn_says_so(client, monkeypatch):
    monkeypatch.setattr("verovio.toolkit.loadData", lambda self, data: False)  # As it does with a file it can't follow.
    response = client.post("/api/scores", files={"file": ("song.musicxml", to_text(parsed(note())).encode())})
    assert response.status_code == 415 and response.json()["detail"] == "Could not read this MusicXML"


@pytest.mark.parametrize("read", [
    lambda score_id: server.get_score(score_id),
    lambda score_id: server.find_range(score_id, server.Stretch(first="note0", last="note1")),
    lambda score_id: server.copy_notes(score_id, server.Stretch(first="note0", last="note1")),
    lambda score_id: server.get_playback(score_id),
    lambda score_id: server.download_musicxml(score_id),
    lambda score_id: server.download_midi(score_id),
], ids=["score", "range", "copy", "playback", "musicxml", "midi"])
def test_a_score_read_as_an_edit_is_undone_reads_one_version_whole(client, ready_score, monkeypatch, read):
    folder = server.JOB_DIR / ready_score
    assert client.post(f"/api/scores/{ready_score}/notes/note0", json={"action": "up"}).status_code == 200
    listed = server.versions
    undoing = []

    def listed_then_undone(place):
        found = listed(place)
        if not undoing:  # The read has just found the latest version, and an undo comes in.
            undoing.append(threading.Thread(target=server.undo, args=(ready_score,)))
            undoing[0].start()
            undoing[0].join(timeout=0.3)
        return found

    monkeypatch.setattr(server, "versions", listed_then_undone)
    read(ready_score)
    undoing[0].join()
    assert len(listed(folder)) == 1  # The undo still happened, once the read was done.


def test_an_edit_sent_as_plain_text_is_not_read(client, ready_score):
    """A page on another site can send plain text here without asking first, but not JSON."""
    body = json.dumps({"text": "Bb7"})
    response = client.post(f"/api/scores/{ready_score}/chords/0", content=body, headers={"Content-Type": "text/plain"})
    assert response.status_code == 422
    assert client.get(f"/api/scores/{ready_score}").json()["chords"][0]["text"] == "Dm7"


def test_a_page_being_read_is_stopped_by_the_system_if_the_app_is_killed_outright(client, tmp_path, monkeypatch):
    """Killed with SIGKILL, the app can't stop the page it's reading, so the system has to, a while after the app would have."""
    started = []

    class Finished:
        returncode = 1

        def __init__(self, command, **_):
            started.append(command)

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setattr(server.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(server.subprocess, "Popen", Finished)
    server.recognize_page(tmp_path / "page.png", tmp_path / "out")
    limits = [started[0][index + 1] for index, part in enumerate(started[0]) if part == "-p"]
    longest = [int(limit.split("=")[1]) for limit in limits if limit.startswith("RuntimeMaxSec=")]
    assert len(longest) == 1 and server.PAGE_TIMEOUT_SECONDS < longest[0] <= 2 * server.PAGE_TIMEOUT_SECONDS


def test_a_thumbnail_is_the_first_page_and_follows_the_score(client, ready_score):
    first = client.get(f"/api/scores/{ready_score}/thumbnail.svg")
    assert first.status_code == 200 and first.headers["content-type"].startswith("image/svg+xml")
    assert first.text.lstrip().startswith("<svg") and "Dm7" in first.text
    client.post(f"/api/scores/{ready_score}/chords/0", json={"text": "G7"})
    assert "G7" in client.get(f"/api/scores/{ready_score}/thumbnail.svg").text
    client.post(f"/api/scores/{ready_score}/undo")
    client.post(f"/api/scores/{ready_score}/chords/0", json={"text": "A7"})
    assert "A7" in client.get(f"/api/scores/{ready_score}/thumbnail.svg").text
    assert len(list((server.JOB_DIR / ready_score).glob("thumbnail_*.svg"))) == 1


def test_a_sheet_still_being_read_has_no_thumbnail(client):
    folder = server.JOB_DIR / ("f" * 32)
    folder.mkdir()
    server.write_status(folder, "processing")
    assert client.get(f"/api/scores/{'f' * 32}/thumbnail.svg").status_code == 404


STATIC = Path(server.__file__).parent / "static"


def test_every_symbol_the_window_draws_is_one_the_engraver_has(client):
    """The toolbar and palettes ask for music symbols by their code. One that's missing would draw as nothing."""
    response = client.get("/glyphs.js")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/javascript")
    assert response.text.startswith("const GLYPHS = {")
    known = json.loads(response.text.removeprefix("const GLYPHS = ").rstrip().removesuffix(";"))
    outline, left, bottom, width, height, advance = known["E050"]
    assert outline.startswith("M") and width > 0 and height > 1000 and bottom < 0 and advance > 0
    asked = set()
    for path in [*STATIC.glob("*.js"), *STATIC.glob("*.html")]:
        for found in re.findall(r"""["'`]((?:[EF][0-9A-F]{3})(?: [EF][0-9A-F]{3})*)["'`]""", path.read_text(encoding="utf-8")):
            asked.update(found.split())
    assert len(asked) > 40 and asked - set(known) == set()


def test_a_new_score_can_start_with_several_instruments_and_its_first_page_written(client):
    """What MuseScore's New score dialog asks for, all in the first version, so Undo can't take half of it away."""
    body = {"clef": "voice", "more": ["piano"], "measures": 4, "title": "Evening", "composer": "A. Writer", "tempo": "Andante 92", "pickup": 4}
    score_id = client.post("/api/scores/blank", json=body).json()["id"]
    data = client.get(f"/api/scores/{score_id}").json()
    assert data["parts"] == ["Voice", "Piano"] and (data["title"], data["composer"]) == ("Evening", "A. Writer")
    assert not data["canUndo"]
    root = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    assert [part.find("measure").get("implicit") for part in root.findall("part")] == ["yes", "yes"]
    assert float(root.find(".//sound").get("tempo")) == 92
    assert client.get(f"/api/scores/{score_id}/playback").json()["tempo"] == 92


@pytest.mark.parametrize("body", [{"more": ["kazoo"]}, {"tempo": "Allegro 9000"}, {"pickup": 64}], ids=["instrument", "tempo", "pickup"])
def test_a_new_score_that_cannot_be_made_is_refused_whole(client, body):
    response = client.post("/api/scores/blank", json=body)
    assert response.status_code == 422 and isinstance(response.json()["detail"], str)
    assert client.get("/api/scores").json() == []


def test_instruments_are_listed_by_family_for_the_chooser(client):
    found = client.get("/api/instruments").json()
    assert {"id": "violin", "label": "Violin", "family": "Strings - Bowed"} in found
    assert {"id": "treble", "label": "Treble staff", "family": "Plain staves"} in found
    assert len({item["id"] for item in found}) == len(found)


def test_instruments_are_added_together_and_removed_by_their_place(client):
    score_id = client.post("/api/scores/blank", json={"clef": "voice", "measures": 2}).json()["id"]
    data = client.post(f"/api/scores/{score_id}/parts", json={"action": "add", "names": ["piano", "cello"]}).json()
    assert data["parts"] == ["Voice", "Piano", "Cello"] and data["partStaves"] == [1, 2, 1]
    assert client.post(f"/api/scores/{score_id}/undo").json()["parts"] == ["Voice"]
    client.post(f"/api/scores/{score_id}/redo")
    assert client.post(f"/api/scores/{score_id}/parts", json={"action": "remove", "index": 1}).json()["parts"] == ["Voice", "Cello"]
    assert client.post(f"/api/scores/{score_id}/parts", json={"action": "remove", "index": 7}).status_code == 422
    assert client.post(f"/api/scores/{score_id}/parts", json={"action": "add"}).status_code == 422


def test_the_keys_musescore_users_reach_for_are_note_actions(client):
    score_id = client.post("/api/scores/blank", json={"measures": 2}).json()["id"]
    first = client.get(f"/api/scores/{score_id}/playback").json()
    note_id = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text).find(".//note").get("id")
    written = client.post(f"/api/scores/{score_id}/notes/{note_id}", json={"action": "write", "step": "C"}).json()["written"]
    for action, extra in [("semitoneUp", {}), ("double", {}), ("dotLonger", {}), ("interval", {"interval": 3}), ("repitch", {"step": "D"})]:
        assert client.post(f"/api/scores/{score_id}/notes/{written}", json={"action": action} | extra).status_code == 200, action
    root = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    found = [(note.findtext("pitch/step"), note.findtext("pitch/alter"), note.findtext("type"), len(note.findall("dot"))) for note in root.iter("note") if note.find("pitch") is not None]
    assert found == [("D", None, "half", 1), ("E", None, "half", 1)]
    assert first["notes"] == []
    assert client.post(f"/api/scores/{score_id}/group", json={"action": "semitoneDown", "notes": [written]}).status_code == 200


def test_a_stretch_of_bars_is_deleted_together(client):
    score_id = client.post("/api/scores/blank", json={"measures": 6}).json()["id"]
    measures = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text).find("part").findall("measure")
    ids = [measure.find("note").get("id") for measure in measures]
    client.post(f"/api/scores/{score_id}/notes/{ids[0]}", json={"action": "write", "step": "C", "type": "whole"})
    client.post(f"/api/scores/{score_id}/notes/{ids[4]}", json={"action": "write", "step": "G", "type": "whole"})
    data = client.post(f"/api/scores/{score_id}/bars", json={"action": "delete", "note": ids[1], "last": ids[3]}).json()
    assert data["canUndo"]
    left = ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text).find("part").findall("measure")
    assert [measure.findtext("note/pitch/step") for measure in left] == ["C", "G", None]
    client.post(f"/api/scores/{score_id}/undo")
    assert len(ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text).find("part").findall("measure")) == 6


def test_removing_a_selected_stretch_is_one_edit(client):
    score_id = client.post("/api/scores/blank", json={"measures": 3}).json()["id"]
    score = lambda: ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    cursor = score().find(".//note").get("id")
    written = []
    for step in "CDEFGA":
        done = client.post(f"/api/scores/{score_id}/notes/{cursor}", json={"action": "write", "step": step}).json()
        written.append(done["written"])
        cursor = done["cursor"]
    ids = written[2:6]
    assert client.post(f"/api/scores/{score_id}/group", json={"action": "remove", "notes": ids}).status_code == 200
    steps = lambda: [note.findtext("pitch/step") for note in score().iter("note") if note.find("pitch") is not None]
    assert steps() == ["C", "D"] and len(score().find("part").findall("measure")) == 3
    client.post(f"/api/scores/{score_id}/undo")
    assert steps() == list("CDEFGA")
    refused = client.post(f"/api/scores/{score_id}/group", json={"action": "remove", "notes": []})
    assert refused.status_code == 422 and "Select" in refused.json()["detail"]


def test_double_sharps_and_flats_go_on_one_note_or_a_selection(client):
    score_id = client.post("/api/scores/blank", json={"measures": 2}).json()["id"]
    score = lambda: ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    cursor = score().find(".//note").get("id")
    written = []
    for step in "CD":
        done = client.post(f"/api/scores/{score_id}/notes/{cursor}", json={"action": "write", "step": step}).json()
        written.append(done["written"])
        cursor = done["cursor"]
    assert client.post(f"/api/scores/{score_id}/notes/{written[0]}", json={"action": "doubleSharp"}).status_code == 200
    assert [note.findtext("accidental") for note in score().iter("note")][:2] == ["double-sharp", None]
    assert client.post(f"/api/scores/{score_id}/group", json={"action": "doubleFlat", "notes": written}).status_code == 200
    assert [(note.findtext("pitch/alter"), note.findtext("accidental")) for note in score().iter("note")][:2] == [("-2", "flat-flat"), ("-2", "flat-flat")]
    client.post(f"/api/scores/{score_id}/undo")
    assert [note.findtext("accidental") for note in score().iter("note")][:2] == ["double-sharp", None]


def test_a_score_starts_in_or_changes_to_common_and_cut_time(client):
    made = client.post("/api/scores/blank", json={"measures": 4, "beats": 2, "beat_type": 2, "symbol": "cut"})
    score_id = made.json()["id"]
    score = lambda: ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    assert [found.get("symbol") for found in score().iter("time")] == ["cut"]
    third = score().find("part").findall("measure")[2].find("note").get("id")
    changed = client.post(f"/api/scores/{score_id}/bars", json={"action": "time", "note": third, "beats": 4, "beat_type": 4, "symbol": "common"})
    assert changed.status_code == 200
    assert [(found.findtext("beats"), found.get("symbol")) for found in score().iter("time")] == [("2", "cut"), ("4", "common")]
    refused = client.post(f"/api/scores/{score_id}/bars", json={"action": "time", "note": third, "beats": 3, "beat_type": 4, "symbol": "common"})
    assert refused.status_code == 422
    assert client.post("/api/scores/blank", json={"beats": 6, "beat_type": 8, "symbol": "cut"}).status_code == 422


def test_a_barline_is_chosen_for_the_measure_a_note_is_in(client):
    score_id = client.post("/api/scores/blank", json={"measures": 3}).json()["id"]
    score = lambda: ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)
    styles = lambda: [measure.findtext("barline/bar-style") for measure in score().find("part").findall("measure")]
    assert styles() == [None, None, "light-heavy"]
    second = score().find("part").findall("measure")[1].find("note").get("id")
    assert client.post(f"/api/scores/{score_id}/bars", json={"action": "barline", "note": second, "style": "double"}).status_code == 200
    assert styles() == [None, "light-light", "light-heavy"]
    client.post(f"/api/scores/{score_id}/undo")
    assert styles() == [None, None, "light-heavy"]
    assert client.post(f"/api/scores/{score_id}/bars", json={"action": "barline", "note": second, "style": "wavy"}).status_code == 422
    assert client.post(f"/api/scores/{score_id}/bars", json={"action": "barline", "style": "double"}).status_code == 422


def test_the_app_keeps_its_scores_in_the_folder_it_is_given(tmp_path, monkeypatch):
    started = {}
    monkeypatch.setattr(server, "JOB_DIR", server.JOB_DIR)  # Put back after the test.
    monkeypatch.setattr(server.uvicorn, "run", lambda app, **options: started.update(options))
    server.main(["--jobs", str(tmp_path / "scores"), "--port", "8123"])
    assert server.JOB_DIR == (tmp_path / "scores").resolve() and server.JOB_DIR.is_dir()
    assert started["host"] == "127.0.0.1" and started["port"] == 8123
    client = TestClient(server.app, base_url="http://127.0.0.1:8123")
    score_id = client.post("/api/scores/blank", json={"measures": 2}).json()["id"]
    assert (tmp_path / "scores" / score_id).is_dir()


def test_the_server_stops_once_whatever_started_it_has_gone(tmp_path):
    import socket
    import subprocess
    import sys
    import urllib.request

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    command = [sys.executable, "-m", "app.server", "--port", str(port), "--jobs", str(tmp_path), "--stop-when-stdin-closes"]
    process = subprocess.Popen(command, cwd=Path(server.__file__).parent.parent, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/scores", timeout=1) as answer:
                    assert json.loads(answer.read()) == []
                break
            except OSError:
                assert process.poll() is None, process.stderr.read().decode()
                assert time.monotonic() < deadline, "the server never answered"
                time.sleep(0.2)
        process.stdin.close()  # As when the desktop app is killed: its end of the pipe closes.
        assert process.wait(timeout=20) is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_the_printable_score_and_parts_show_where_they_open(client, ready_score):
    # Print and Parts open the PDF in a window to print from; as an attachment, a Chromium window
    # (the desktop app's among them) would only offer to save it.
    for query in ("", "?part=0"):
        answer = client.get(f"/api/scores/{ready_score}/score.pdf{query}")
        assert answer.status_code == 200 and answer.content.startswith(b"%PDF")
        disposition = answer.headers["content-disposition"]
        assert disposition.startswith("inline;"), disposition
        # Named after the score, untitled here, and a part after its instrument too, as "score (Violin).pdf".
        assert 'filename="score.pdf"' in disposition if not query else "filename*=utf-8''score%20%28" in disposition, disposition


def test_each_note_is_named_as_it_is_shown(client):
    """The inspector names a selected note from these: its pitch as the page shows it, and its length."""
    score_id = blank(client, clef="trumpet", measures=1)
    first = client.get(f"/api/scores/{score_id}").json()["notes"]
    rest_id = next(iter(first))
    assert first[rest_id] == ["", 0, 0, "", 0], "a whole-bar rest has no pitch and no written type"
    written = client.post(f"/api/scores/{score_id}/notes/{rest_id}", json={"action": "write", "type": "quarter", "dots": 1, "step": "F"}).json()
    named = written["notes"][written["written"]]
    assert named[0] == "F" and named[3:] == ["quarter", 1], named
    client.post(f"/api/scores/{score_id}/notes/{written['written']}", json={"action": "sharp"})
    client.post(f"/api/scores/{score_id}/concert", json={"on": True})
    shown = client.get(f"/api/scores/{score_id}").json()["notes"][written["written"]]
    assert shown[:2] == ["E", 0] and shown[2] == named[2], "a trumpet's F sharp sounds as E, a step lower"


def test_the_window_is_sent_only_the_symbols_it_draws(client):
    """The music font has hundreds of symbols, and the window draws a few dozen of them."""
    response = client.get("/glyphs.js")
    sent = json.loads(response.text.removeprefix("const GLYPHS = ").rstrip().removesuffix(";"))
    everything = json.loads(server.glyph_script().removeprefix("const GLYPHS = ").rstrip().removesuffix(";"))
    assert len(sent) < len(everything) / 3
    assert {f"E08{digit}" for digit in range(10)} | {"E050", "E1D5", "E262"} <= set(sent), "time signature digits, a clef, a note, a sharp"
    again = client.get("/glyphs.js", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304 and response.headers["cache-control"] == "no-cache", "the browser checks it has the latest"


@pytest.mark.parametrize("path", ["/", "/index.html", "/page.js", "/editor.js", "/app.css"])
def test_the_pages_own_files_are_checked_for_a_newer_copy_each_time(client, path):
    """With only an ETag, a browser took each script as fresh for a while without asking, so after an
    update a window could run the new page with some of the old scripts."""
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-cache" and response.headers["etag"]
    again = client.get(path, headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304 and again.headers["cache-control"] == "no-cache", "unchanged, it isn't sent again"


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_the_page_may_run_only_its_own_scripts(client, path):
    """Pages of music go into the window as markup; everything read from a score is escaped today,
    and a policy keeps a slip in the engraver or a future innerHTML from running anything."""
    policy = client.get(path).headers["content-security-policy"]
    assert "default-src 'self'" in policy and "object-src 'none'" in policy and "base-uri 'none'" in policy
    assert "script-src" not in policy and "'unsafe-eval'" not in policy, "scripts come from the app alone"


def test_about_names_the_licence_the_source_and_the_credits_the_app_owes(client):
    """AGPL-3.0 asks a changed copy used over a network to offer its source, MiSans's licence asks the
    software to say it uses MiSans, and the piano's CC BY 3.0 asks for its credit wherever the app goes.
    About once named only Verovio and Leland."""
    about = re.search(r'<dialog id="aboutDialog".*?</dialog>', client.get("/").text, re.S).group(0)
    credits = dict(re.findall(r"<dt>(.*?)</dt><dd>(.*?)</dd>", about))
    links = {text: (href, rest) for href, rest, text in re.findall(r'<a href="([^"]+)"([^>]*)>([^<]+)</a>', about)}
    plain = {name: re.sub(r"<[^>]+>", "", credit) for name, credit in credits.items()}
    assert plain["Licence"] == "© 2026 Xernnn · GNU AGPL-3.0 · No warranty"
    assert plain["Source"] == "github.com/Xernnn/xrnsheet"
    assert links["github.com/Xernnn/xrnsheet"][0] == "https://github.com/Xernnn/xrnsheet"
    assert links["GNU AGPL-3.0"][0] == "https://www.gnu.org/licenses/agpl-3.0.html"
    assert plain["Engraving"] == "Verovio · LGPL-3.0" and plain["Music font"] == "Leland · SIL OFL 1.1"
    assert plain["Font"].startswith("MiSans (Xiaomi)")
    assert plain["Piano"] == "Salamander Grand Piano by Alexander Holm · CC BY 3.0"
    assert links["CC BY 3.0"][0] == "https://creativecommons.org/licenses/by/3.0/", "the credit links to the licence"
    assert all('target="_blank"' in rest and "noopener" in rest for _, rest in links.values()), "each opens beside the score, not over it"


def test_a_drawing_just_made_is_kept_for_the_next_time_it_is_asked_for(client):
    """Opening a score again, and undoing, ask for a drawing the server has just made."""
    score_id = blank(client, measures=2)
    client.get(f"/api/scores/{score_id}")
    written = write(client, score_id, first_id(client, score_id), "C")
    before = server.drawn_pages.cache_info()
    client.get(f"/api/scores/{score_id}")
    client.post(f"/api/scores/{score_id}/undo")
    after = server.drawn_pages.cache_info()
    assert after.hits - before.hits == 2 and after.misses == before.misses
    assert client.get(f"/api/scores/{score_id}").json()["pages"] != written["pages"], "the drawing undone to is the earlier one"


def line_starts(data):
    """The measure each drawn line starts with, as the page has them."""
    return [re.search(r'<g id="([^"]+)" class="measure', system).group(1) for page in data["pages"] for system in re.split(r'<g [^>]*class="system"', page)[1:]]


def fill_measure(client, score_id, index, length, count):
    """Write a run of notes into a measure, a note at a time, as someone entering them would."""
    measure = saved(client, score_id).find("part").findall("measure")[index]
    at = next(measure.iter("note")).get("id")
    for number in range(count):
        data = write(client, score_id, at, "CDEFGAB"[number % 7], length)
        at = data["cursor"]
    return data


def test_lines_keep_their_measures_while_notes_are_written(client):
    score_id = blank(client, clef="piano", measures=32)
    first = line_starts(client.get(f"/api/scores/{score_id}").json())
    # A few notes in the third measure leave it room enough, so no line changes.
    data = fill_measure(client, score_id, 2, "quarter", 4)
    assert line_starts(data) == first
    # Undoing them, which makes the measure narrow again, doesn't pull measures back up either.
    assert line_starts(client.post(f"/api/scores/{score_id}/undo").json()) == first
    # Once a measure no longer fits, the lines from there on are laid out afresh, with no line left short.
    data = fill_measure(client, score_id, 3, "16th", 16)
    data = fill_measure(client, score_id, 4, "16th", 16)
    after = line_starts(data)
    assert after != first and after[0] == first[0]
    counts = [b - a for a, b in zip([int(start[1:]) for start in after], [int(start[1:]) for start in after[1:]] + [32])]
    assert min(counts[:-1]) >= 3, counts
    # The PDF has the lines the page has.
    assert client.get(f"/api/scores/{score_id}/score.pdf").status_code == 200


def test_lines_are_laid_out_afresh_when_the_page_changes(client):
    score_id = blank(client, clef="piano", measures=32)
    first = line_starts(client.get(f"/api/scores/{score_id}").json())
    crowded = line_starts(fill_measure(client, score_id, 2, "eighth", 8))
    assert crowded != first  # Eight eighths no longer fit the first line.
    for _ in range(8):
        narrow = line_starts(client.post(f"/api/scores/{score_id}/undo").json())
    assert narrow == crowded  # Kept, though the measure is empty and narrow again.
    layout = client.get(f"/api/scores/{score_id}").json()["layout"]
    data = client.post(f"/api/scores/{score_id}/layout", json=layout | {"left": 50}).json()
    assert line_starts(data) != narrow and len(line_starts(data)) >= len(first)  # A narrower page: the lines were laid out for it.
    data = client.post(f"/api/scores/{score_id}/undo").json()
    assert line_starts(data) == first  # Back on the old page, laid out afresh: the narrow measure is room enough again.


def test_the_page_is_told_what_a_place_on_each_staff_means(client):
    score_id = blank(client, clef="piano", measures=2)
    data = client.get(f"/api/scores/{score_id}").json()
    rests = [note for note in notes_of(client, score_id)]
    treble, bass = rests[0].get("id"), next(note.get("id") for note in rests if note.findtext("staff") == "2")
    # The treble staff's top line is F5 and the bass staff's A3, as rhythm.diatonic numbers them.
    assert data["places"][treble] == [0, 0.0, 3 + 7 * 5, 0, 1] and data["places"][bass] == [0, 0.0, 5 + 7 * 3, 0, 1]
    # The beat as a player counts it: the third quarter of a bar is beat 3, and in 6/8 the fourth eighth is beat 2.
    data = write(client, score_id, treble, "C", "half")
    assert data["places"][data["cursor"]][4] == 3
    six = blank(client, beats=6, beat_type=8, measures=1)
    data = write(client, six, first_id(client, six), "C", "eighth")
    data = write(client, six, data["cursor"], "D", "eighth")
    data = write(client, six, data["cursor"], "E", "eighth")
    assert data["places"][data["cursor"]][4] == 2


def test_a_key_pressed_with_a_note_selected_sets_its_pitch(client):
    score_id = blank(client, fifths=-1)
    data = write(client, score_id, first_id(client, score_id), "G", "half")
    note = data["written"]
    data = client.post(f"/api/scores/{score_id}/notes/{note}", json={"action": "pitch", "midi": 70}).json()
    played = notes_of(client, score_id)[0]
    assert (played.findtext("pitch/step"), played.findtext("pitch/alter"), played.findtext("type")) == ("B", "-1", "half")
    assert data["cursor"] == note  # Still the one selected.
    # A rest becomes a note of its own length.
    rest = next(item.get("id") for item in notes_of(client, score_id) if item.find("rest") is not None)
    data = client.post(f"/api/scores/{score_id}/notes/{rest}", json={"action": "pitch", "midi": 60}).json()
    made = next(item for item in notes_of(client, score_id) if item.get("id") == data["cursor"])
    assert made.findtext("pitch/step") == "C" and made.findtext("type") == "half"
