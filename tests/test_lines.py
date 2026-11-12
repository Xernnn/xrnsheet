"""Octave lines and pedal lines: placing, drawing, playing, removing and reading them back."""

import re
import threading
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import server
from pipeline import edit, marks, structure
from pipeline.engrave import render_pages
from pipeline.musicxml import to_text
from pipeline.playback import playback
from pipeline.rhythm import octave_lines

NOTEHEAD = re.compile(r'<g id="([^"]+)" class="note">\s*<g class="notehead">\s*<use xlink:href="#E0A[0-9A-F][^"]*" transform="translate\(\d+, (\d+)\)')
STAFF_STEP = 87  # Half a staff space on the page, in Verovio's units at this size.


def written(root, steps, staff_rest=None, length="quarter", voice=None):
    """Write notes from the first rest of a staff on, and return their ids."""
    cursor = staff_rest or root.find("part/measure/note").get("id")
    ids = []
    for step in steps:
        done = edit.write_note(root, cursor, length, 0, step, voice=voice)
        ids.append(done["written"])
        cursor = done["cursor"]
    edit.ensure_note_ids(root)
    return ids


def lower_rest(root):
    return next(note.get("id") for note in root.find("part/measure").findall("note") if note.findtext("staff") == "2")


def heights(root):
    return {found[0]: int(found[1]) for found in NOTEHEAD.findall(render_pages(to_text(root))[0])}


def moved(before, after, ids):
    return [round((after[note] - before[note]) / STAFF_STEP) for note in ids]


def piano():
    root = structure.blank_score(clef="piano", measures=2)
    edit.ensure_note_ids(root)
    return root


def test_an_8va_is_written_as_musicxml_writes_it():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEF")
    marks.toggle_ottava(root, ids[0], ids[2], "8va")
    shifts = list(root.iter("octave-shift"))
    assert [(shift.get("type"), shift.get("size")) for shift in shifts] == [("down", "8"), ("stop", "8")]
    assert shifts[0].get("number") == shifts[1].get("number")
    line = octave_lines(root.find("part"))[0]
    assert (line.start, line.stop, line.octaves) == ((0, 0), (0, 3), 1), "it ends where its last note does"
    assert [note.findtext("pitch/octave") for note in root.iter("note") if note.find("pitch") is not None] == ["5"] * 4, "the notes keep their pitch"


def test_the_same_line_again_takes_it_away_and_an_overlapping_one_replaces_it():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEF")
    marks.toggle_ottava(root, ids[0], ids[2], "8va")
    marks.toggle_ottava(root, ids[0], ids[2], "8va")
    assert not list(root.iter("octave-shift"))
    marks.toggle_ottava(root, ids[0], ids[2], "8va")
    marks.toggle_ottava(root, ids[1], ids[3], "15ma")
    assert [(line.start, line.octaves) for line in octave_lines(root.find("part"))] == [((0, 1), 2)]


def test_notes_under_octave_lines_are_drawn_an_octave_away_on_their_own_staff():
    root = piano()
    top = written(root, "CDEF")
    second = written(root, "EG", staff_rest=top[0], length="half", voice=2)
    bottom = written(root, "CDEF", staff_rest=lower_rest(root))
    before = heights(root)
    marks.toggle_ottava(root, top[0], top[2], "8va")
    marks.toggle_ottava(root, bottom[1], bottom[2], "8vb")
    after = heights(root)
    assert moved(before, after, top) == [7, 7, 7, 0]
    assert moved(before, after, second) == [7, 7], "a second voice under the line moves too"
    assert moved(before, after, bottom) == [0, -7, -7, 0], "a lower staff's line moves its own notes"


def test_octave_lines_are_marked_as_musescore_marks_them():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEFGABC")
    for first, name in ((0, "8va"), (2, "8vb"), (4, "15ma"), (6, "15mb")):
        marks.toggle_ottava(root, ids[first], ids[first + 1], name)
    page = render_pages(to_text(root))[0]
    assert re.findall(r'class="octave".*?#(E5[0-9A-F]{2})', page, re.S) == ["E511", "E51C", "E515", "E51D"], "8va, 8vb, 15ma and 15mb"


def test_a_line_is_drawn_on_its_own_staff():
    root = piano()
    bottom = written(root, "CDEF", staff_rest=lower_rest(root))
    marks.toggle_ottava(root, bottom[0], bottom[1], "8vb")
    page = render_pages(to_text(root))[0]
    staves = [int(found) for found in re.findall(r'class="staff"[^>]*>\s*<path d="M\d+ (\d+)', page)]
    line = int(re.search(r'class="octave"[^>]*>.*?<path d="M\d+ (\d+)', page, re.S).group(1))
    assert line > staves[1], "an 8vb on the bass staff hangs below the bass staff"


def test_a_click_under_an_8va_writes_the_note_an_octave_up():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEF")
    marks.toggle_ottava(root, ids[0], ids[3], "8va")
    edit.write_note(root, ids[1], "quarter", 0, None, line=0)  # The top line, F.
    assert edit.find_note(root, edit.write_note(root, ids[2], "quarter", 0, None, line=0)["written"]).findtext("pitch/octave") == "6"


def test_an_interval_under_an_8va_is_counted_from_the_note():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "C")
    marks.toggle_ottava(root, ids[0], ids[0], "8va")
    added = edit.find_note(root, edit.add_interval(root, ids[0], 3))
    assert (added.findtext("pitch/step"), added.findtext("pitch/octave")) == ("E", "5")


def test_octave_lines_play_the_notes_as_written():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CD")
    before = [note["midi"] for note in playback(root)["notes"]]
    marks.toggle_ottava(root, ids[0], ids[1], "8vb")
    assert [note["midi"] for note in playback(root)["notes"]] == before == [72, 74]


def test_the_pedal_holds_notes_until_it_comes_up():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEC", length="eighth")
    plain = playback(root)["notes"]
    assert [note["length"] for note in plain] == [0.5] * 4
    marks.toggle_pedal(root, ids[0], ids[2], "pedstar")
    held = playback(root)["notes"]
    assert [round(note["start"] + note["length"], 3) for note in held[:3]] == [1.5, 1.5, 1.5], "held to the end of the third note"
    assert held[3]["length"] == 0.5


def test_a_note_played_again_under_the_pedal_stops_the_first():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDC", length="eighth")
    marks.toggle_pedal(root, ids[0], ids[2], "line")
    held = playback(root)["notes"]
    assert held[0]["start"] + held[0]["length"] == held[2]["start"]


def test_pedal_marks_are_listed_once_for_each_end_drawn_and_an_octave_line_once():
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEF")
    marks.toggle_pedal(root, ids[0], ids[1], "pedstar")
    marks.toggle_ottava(root, ids[2], ids[3], "8va")
    marks.ensure_mark_ids(root)
    listed = [(mark["kind"], mark["drawn"], mark["id"]) for mark in marks.list_marks(root)]
    pedals = [item for item in listed if item[0] == "pedal"]
    assert len(pedals) == 2 and pedals[0][2] == pedals[1][2]
    assert [item[:2] for item in listed if item[0] == "ottava"] == [("ottava", "octave")]


def test_removing_a_line_takes_both_its_ends():
    root = structure.blank_score(measures=3)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEFGA")
    marks.toggle_ottava(root, ids[0], ids[5], "8va")
    marks.toggle_pedal(root, ids[1], ids[4], "line")
    marks.ensure_mark_ids(root)
    for mark_id in {mark["id"] for mark in marks.list_marks(root) if mark["kind"] in ("ottava", "pedal")}:
        marks.set_label(root, mark_id, "")
    assert not list(root.iter("octave-shift")) and not list(root.iter("pedal"))


def test_deleting_the_bar_a_line_ends_in_takes_the_line_away():
    root = structure.blank_score(measures=3)
    edit.ensure_note_ids(root)
    ids = written(root, "CDEFG")
    marks.toggle_ottava(root, ids[0], ids[4], "8va")
    structure.delete_measure(root, 1)
    assert not list(root.iter("octave-shift"))


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def test_lines_are_placed_through_the_marks_endpoint_and_kept_in_the_musicxml(client):
    score_id = client.post("/api/scores/blank", json={"measures": 2, "clef": "piano"}).json()["id"]
    url = f"/api/scores/{score_id}"
    root = ET.fromstring(client.get(f"{url}/score.musicxml").text)
    cursor = root.find("part/measure/note").get("id")
    notes = []
    for step in "CDE":
        done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "step": step}).json()
        notes.append(done["written"])
        cursor = done["cursor"]
    assert client.post(f"{url}/marks", json={"action": "ottava", "name": "15mb", "notes": notes}).status_code == 200
    data = client.post(f"{url}/marks", json={"action": "pedal", "name": "pedstar", "notes": notes[:2]}).json()
    assert {mark["kind"] for mark in data["marks"]} == {"ottava", "pedal"}
    text = client.get(f"{url}/score.musicxml").text
    reopened = client.post("/api/scores", files={"file": ("lines.musicxml", text.encode())}).json()["id"]
    kept = ET.fromstring(client.get(f"/api/scores/{reopened}/score.musicxml").text)
    assert [(shift.get("type"), shift.get("size")) for shift in kept.iter("octave-shift")] == [("up", "15"), ("stop", "15")]
    assert [(pedal.get("type"), pedal.get("sign")) for pedal in kept.iter("pedal")] == [("start", "yes"), ("stop", "yes")]
    assert all(direction.findtext("staff") == "1" for direction in kept.iter("direction"))
    assert client.post(f"{url}/marks", json={"action": "ottava", "name": "22ma", "notes": notes}).status_code == 422
