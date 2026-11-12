"""Page and style settings: kept as MusicXML's <defaults>, drawn by the pages, and refused when they can't be."""

import re
import threading
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import server
from pipeline import edit, layout, structure
from pipeline.engrave import page_size, render_pages
from pipeline.layout import Layout
from pipeline.musicxml import to_text


def score(bars: int = 16) -> ET.Element:
    root = structure.blank_score(measures=bars)
    structure.set_title(root, "Tune", "Someone")
    edit.ensure_note_ids(root)
    return root


def staff_lines(page: str) -> list[int]:
    return [int(found) for found in re.findall(r'<path d="M\d+ (\d+) L\d+ \d+" stroke-width="\d+" />', page[page.find('class="staff"'):])[:5]]


def test_a_score_starts_on_musescores_a4():
    assert layout.page_layout(score()) == Layout()
    assert [round(size) for size in page_size(render_pages(to_text(score()))[0])] == [210, 297]


def test_a_layout_is_kept_as_musicxml_defaults():
    root = score()
    layout.set_layout(root, Layout(width=215.9, height=279.4, top=20, space=2.0, systems=8))
    defaults = root.find("defaults")
    assert [child.tag for child in defaults] == ["scaling", "page-layout", "system-layout", "staff-layout"]
    assert (defaults.findtext("scaling/millimeters"), defaults.findtext("scaling/tenths")) == ("8", "40")
    assert (defaults.findtext("page-layout/page-width"), defaults.findtext("page-layout/page-height")) == ("1079.5", "1397")
    assert defaults.findtext("page-layout/page-margins/top-margin") == "100"
    assert defaults.findtext("system-layout/system-distance") == "80"
    assert [child.tag for child in root][:3] == ["work", "identification", "defaults"], "where the schema puts it"
    assert layout.page_layout(root) == Layout(width=215.9, height=279.4, top=20, space=2.0, systems=8)


def test_a_new_layout_keeps_what_else_defaults_say():
    root = score()
    defaults = ET.Element("defaults")
    ET.SubElement(defaults, "concert-score")
    ET.SubElement(defaults, "word-font", {"font-family": "Edwin"})
    root.insert(2, defaults)
    layout.set_layout(root, Layout())
    assert [child.tag for child in root.find("defaults")] == ["scaling", "concert-score", "page-layout", "system-layout", "staff-layout", "word-font"]


def test_the_pages_are_drawn_with_the_layout():
    root = score()
    before = render_pages(to_text(root))
    layout.set_layout(root, Layout(width=297, height=210, top=30, space=2.0))
    after = render_pages(to_text(root))
    assert [round(size) for size in page_size(after[0])] == [297, 210], "a landscape A4"
    gap = lambda page: staff_lines(page)[1] - staff_lines(page)[0]
    assert round(gap(after[0]) / gap(before[0]), 2) == round(2.0 / 1.75, 2), "the staff space"
    assert staff_lines(after[0])[0] > staff_lines(before[0])[0], "the top margin and the larger title frame"


def test_more_room_between_systems_spreads_them():
    root = score(24)
    tops = lambda page: [int(found) for found in re.findall(r'class="system">.*?class="staff">\s*<path d="M\d+ (\d+)', page, re.S)]
    before = tops(render_pages(to_text(root))[0])
    layout.set_layout(root, Layout(systems=14))
    after = tops(render_pages(to_text(root))[0])
    assert after[1] - after[0] > before[1] - before[0]


def test_settings_that_cannot_be_drawn_are_refused():
    for bad in (Layout(width=20), Layout(top=60), Layout(left=100, right=100), Layout(space=5), Layout(systems=-1), Layout(width=float("nan"))):
        with pytest.raises(edit.EditError):
            layout.set_layout(score(), bad)


def test_a_photos_guess_at_a_page_is_not_used():
    root = score()
    layout.set_layout(root, Layout(width=400, height=600))
    structure.write_setting(root, layout.MARKER, None)
    assert layout.page_layout(root) == Layout()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def test_page_settings_are_an_edit_that_exports_and_comes_back(client):
    score_id = client.post("/api/scores/blank", json={"measures": 4}).json()["id"]
    url = f"/api/scores/{score_id}"
    data = client.post(f"{url}/layout", json={"width": 215.9, "height": 279.4, "space": 1.5}).json()
    assert data["layout"]["width"] == 215.9 and data["pageSizes"][0] == [215.9, 279.4] and data["canUndo"]
    assert client.post(f"{url}/layout", json={"top": 70}).status_code == 422
    text = client.get(f"{url}/score.musicxml").text
    reopened = client.post("/api/scores", files={"file": ("lettered.musicxml", text.encode())}).json()["id"]
    assert client.get(f"/api/scores/{reopened}").json()["layout"]["width"] == 215.9
    assert client.post(f"{url}/undo").json()["layout"] == layout.layout_of(score())


def test_a_file_from_elsewhere_opens_on_its_own_page(client):
    root = score(2)
    defaults = ET.Element("defaults")
    scaling = ET.SubElement(defaults, "scaling")
    ET.SubElement(scaling, "millimeters").text = "7.2319"
    ET.SubElement(scaling, "tenths").text = "40"
    page = ET.SubElement(defaults, "page-layout")
    ET.SubElement(page, "page-height").text = "1545.32"
    ET.SubElement(page, "page-width").text = "1194.17"
    root.insert(2, defaults)
    opened = client.post("/api/scores", files={"file": ("letter.musicxml", to_text(root).encode())}).json()["id"]
    found = client.get(f"/api/scores/{opened}").json()["layout"]
    assert (round(found["width"], 1), round(found["height"], 1), round(found["space"], 2)) == (215.9, 279.4, 1.81)
