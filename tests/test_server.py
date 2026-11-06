import io

import pytest
from fastapi.testclient import TestClient
from helpers import harmony, note, parsed

from app import server


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
