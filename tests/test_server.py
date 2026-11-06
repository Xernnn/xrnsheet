import io
import json

import pytest
from fastapi.testclient import TestClient
from helpers import harmony, note, parsed
from PIL import Image

from app import server
from pipeline.musicxml import to_text


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    return TestClient(server.app)


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


def test_a_sheet_still_reading_cannot_be_checked(client):
    score_id = "c" * 32
    folder = server.JOB_DIR / score_id
    folder.mkdir()
    server.write_status(folder, "processing")
    assert client.post(f"/api/scores/{score_id}/checked", json={"checked": True}).status_code == 409


def test_finished_sheets_are_listed_and_unfinished_ones_are_not(client, ready_score):
    unfinished = server.JOB_DIR / ("d" * 32)
    unfinished.mkdir()
    server.write_status(unfinished, "processing")
    sheets = client.get("/api/scores").json()
    assert [sheet["id"] for sheet in sheets] == [ready_score] and sheets[0]["pages"] == 1


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


def test_a_long_pdf_says_what_was_skipped(client, monkeypatch):
    monkeypatch.setattr(server, "recognize_page", fake_recognizer())
    monkeypatch.setattr(server, "MAX_PDF_PAGES", 2)
    folder, upload = job_for_pdf(3)
    server.recognize_job(folder, upload)
    assert "first 2 of 3" in client.get(f"/api/scores/{folder.name}").json()["message"]


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
