"""MuseScore's page and continuous views: how each draws, and that printing is always in pages."""

import threading

import pytest
from fastapi.testclient import TestClient

from app import server
from pipeline import edit, engrave, structure
from pipeline.engrave import page_size, render_pages
from pipeline.musicxml import to_text


def long_score(bars: int = 24) -> str:
    root = structure.blank_score(measures=bars)
    structure.set_title(root, "Long Tune", "Someone")
    edit.ensure_note_ids(root)
    cursor = root.find("part/measure/note").get("id")
    for step in "CDEFGFED" * (bars // 2):
        cursor = edit.write_note(root, cursor, "quarter", 0, step)["cursor"]
    return to_text(root)


def test_page_view_is_a4_pages_of_systems():
    pages = render_pages(long_score())
    assert [round(size) for size in page_size(pages[0])] == [210, 297]
    assert pages[0].count('class="system"') > 1 and 'class="pgHead"' in pages[0]


def test_the_horizontal_view_is_one_system_as_wide_as_its_music_with_no_title():
    pages = render_pages(long_score(), view="horizontal")
    assert len(pages) == 1 and pages[0].count('class="system"') == 1 and pages[0].count('class="measure"') == 24
    assert 'class="pgHead"' not in pages[0]
    width, height = page_size(pages[0])
    assert width > 600 and height < 297


def test_the_vertical_view_is_one_long_page_with_its_title():
    text = long_score(60)
    assert len(render_pages(text)) > 1
    pages = render_pages(text, view="vertical")
    assert len(pages) == 1 and 'class="pgHead"' in pages[0]
    assert page_size(pages[0])[1] > 297


def test_line_breaks_are_kept_in_the_vertical_view_and_left_out_of_the_horizontal_one():
    root = structure.blank_score(measures=8)
    structure.toggle_line_break(root, 2)
    text = to_text(root)
    assert render_pages(text, view="vertical")[0].count('class="system"') >= 2
    assert render_pages(text, view="horizontal")[0].count('class="system"') == 1


def test_an_unknown_view_is_refused():
    with pytest.raises(ValueError):
        render_pages(long_score(), view="sideways")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def test_a_score_keeps_its_view_which_is_not_an_edit(client, monkeypatch):
    score_id = client.post("/api/scores/blank", json={"measures": 4}).json()["id"]
    assert client.get(f"/api/scores/{score_id}").json()["view"] == "page"
    data = client.post(f"/api/scores/{score_id}/view", json={"view": "horizontal"}).json()
    assert data["view"] == "horizontal" and data["canUndo"] is False and len(data["pageSizes"]) == len(data["pages"]) == 1
    assert client.get(f"/api/scores/{score_id}").json()["view"] == "horizontal"
    assert client.post(f"/api/scores/{score_id}/view", json={"view": "sideways"}).status_code == 422
    views = []
    real = engrave.render_pages
    monkeypatch.setattr(engrave, "render_pages", lambda text, *more, view="page", **rest: (views.append(view), real(text, *more, view=view, **rest))[1])
    assert client.get(f"/api/scores/{score_id}/score.pdf").content[:5] == b"%PDF-"
    assert views == ["page"], "printed as pages whatever the view"
