"""Concert pitch: a score shown as it sounds, and edits made there landing as they're written."""

import copy
import threading
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from app import server
from pipeline import concert, edit, structure
from pipeline.musicxml import to_text


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def blank(client, **settings):
    response = client.post("/api/scores/blank", json={"measures": 4, **settings})
    assert response.status_code == 200
    return response.json()["id"]


def saved(client, score_id):
    return ET.fromstring(client.get(f"/api/scores/{score_id}/score.musicxml").text)


def written_notes(client, score_id, part=0):
    found = []
    for note in saved(client, score_id).findall("part")[part].iter("note"):
        if note.find("pitch") is not None:
            found.append(f"{note.findtext('pitch/step')}{({'-1': 'b', '1': '#', '-2': 'bb', '2': 'x'}).get(note.findtext('pitch/alter'), '')}{note.findtext('pitch/octave')}")
    return found


def first_rest(client, score_id, part=0):
    return next(note.get("id") for note in saved(client, score_id).findall("part")[part].iter("note"))


def post(client, score_id, path, body):
    response = client.post(f"/api/scores/{score_id}/{path}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def at_concert_pitch(client, score_id, on=True):
    return post(client, score_id, "concert", {"on": on})


def trumpet_score(client):
    """A B flat trumpet alone, with C, D, E and F written in its first bar."""
    score_id = blank(client, clef="trumpet")
    cursor = first_rest(client, score_id)
    for step in "CDEF":
        cursor = post(client, score_id, f"notes/{cursor}", {"action": "write", "type": "quarter", "step": step})["cursor"]
    return score_id


def drawn_key(page: str) -> int:
    """Count the sharps (positive) or flats (negative) of the first key signature on a page."""
    start = page.find('class="keySig"')
    section = page[start:page.find('class="meterSig"', start)]
    return section.count("#E262") - section.count("#E260")


def test_a_trumpet_is_shown_a_tone_lower_at_concert_pitch(client):
    score_id = trumpet_score(client)
    assert written_notes(client, score_id) == ["C#5", "D5", "E5", "F#5"]  # In its key of D, two sharps.
    data = at_concert_pitch(client, score_id)
    assert data["concertPitch"] is True and data["transposing"] is True
    assert drawn_key(data["pages"][0]) == 0
    shown = concert.shown(saved(client, score_id))
    assert [note.findtext("pitch/step") for note in shown.iter("note") if note.find("pitch") is not None] == list("BCDE")
    assert written_notes(client, score_id) == ["C#5", "D5", "E5", "F#5"], "the score itself is still written for the trumpet"
    assert drawn_key(client.post(f"/api/scores/{score_id}/undo").json()["pages"][0]) == 2


def test_the_musicxml_file_stays_written_and_the_pdf_follows_the_view(client):
    score_id = trumpet_score(client)
    at_concert_pitch(client, score_id)
    root = saved(client, score_id)
    assert root.find(".//transpose/chromatic").text == "-2" and root.find(".//fifths").text == "2"
    assert client.get(f"/api/scores/{score_id}/score.pdf").content[:5] == b"%PDF-"
    assert client.get(f"/api/scores/{score_id}/score.pdf?part=0").content[:5] == b"%PDF-"


def test_letters_and_clicks_at_concert_pitch_write_the_note_that_sounds(client):
    score_id = trumpet_score(client)
    at_concert_pitch(client, score_id)
    cursor = next(note.get("id") for note in saved(client, score_id).findall("part/measure")[1].iter("note"))
    cursor = post(client, score_id, f"notes/{cursor}", {"action": "write", "type": "quarter", "step": "C"})["cursor"]
    post(client, score_id, f"notes/{cursor}", {"action": "write", "type": "quarter", "line": 0})  # The top line, F.
    assert written_notes(client, score_id)[4:] == ["D5", "G5"]


def test_accidentals_at_concert_pitch_are_concert_accidentals(client):
    score_id = blank(client, clef="trumpet", fifths=-2)  # Concert B flat major: the trumpet reads C major.
    note = post(client, score_id, f"notes/{first_rest(client, score_id)}", {"action": "write", "type": "quarter", "step": "C"})["written"]
    assert written_notes(client, score_id) == ["C5"]
    at_concert_pitch(client, score_id)
    post(client, score_id, f"notes/{note}", {"action": "natural"})
    assert written_notes(client, score_id) == ["C#5"], "a concert B flat made natural is the trumpet's C sharp"


def test_a_key_chosen_at_concert_pitch_is_the_concert_key(client):
    score_id = trumpet_score(client)
    at_concert_pitch(client, score_id)
    post(client, score_id, "bars", {"action": "key", "fifths": 1, "note": first_rest(client, score_id)})
    assert saved(client, score_id).find(".//fifths").text == "3"


def test_a_key_chosen_for_a_trumpet_staff_is_the_trumpets_own(client):
    score_id = blank(client, more=["trumpet"])
    trumpet = first_rest(client, score_id, part=1)
    post(client, score_id, "bars", {"action": "key", "fifths": 2, "note": trumpet})
    fifths = [part.find(".//fifths").text for part in saved(client, score_id).findall("part")]
    assert fifths == ["0", "2"], "D major on the trumpet's staff is concert C"


def test_a_chord_symbol_typed_at_concert_pitch_is_stored_as_the_trumpet_reads_it(client):
    score_id = trumpet_score(client)
    at_concert_pitch(client, score_id)
    data = post(client, score_id, "chords", {"note": first_rest(client, score_id), "text": "C7"})
    assert data["chords"][0]["text"] == "C7"
    assert at_concert_pitch(client, score_id, on=False)["chords"][0]["text"] == "D7"


def test_an_instrument_added_at_concert_pitch_gets_its_written_key(client):
    score_id = blank(client, fifths=-1)  # F major.
    at_concert_pitch(client, score_id)
    post(client, score_id, "parts", {"action": "add", "names": ["horn", "trumpet"]})
    fifths = [part.find(".//fifths").text for part in saved(client, score_id).findall("part")]
    assert fifths == ["-1", "0", "1"], "a horn in F reads C major and a trumpet G major"


def test_an_edit_elsewhere_keeps_a_spelling_concert_pitch_cannot_show(client):
    score_id = blank(client, clef="clarinet")
    note = post(client, score_id, f"notes/{first_rest(client, score_id)}", {"action": "write", "type": "quarter", "step": "C"})["written"]
    post(client, score_id, f"notes/{note}", {"action": "doubleFlat"})
    assert written_notes(client, score_id) == ["Cbb5"]
    at_concert_pitch(client, score_id)
    later = next(note.get("id") for note in saved(client, score_id).findall("part/measure")[2].iter("note"))
    post(client, score_id, f"notes/{later}", {"action": "write", "type": "quarter", "step": "G"})
    assert written_notes(client, score_id)[0] == "Cbb5"


def test_a_courtesy_accidental_in_an_untouched_bar_survives_an_edit_at_concert_pitch():
    root = structure.blank_score(clef="trumpet", measures=2)
    edit.ensure_note_ids(root)
    cursor = root.find("part/measure/note").get("id")
    written = edit.write_note(root, cursor, "quarter", 0, "G")
    courtesy = edit.find_note(root, written["written"])
    edit.place(courtesy, ET.Element("accidental"))
    courtesy.find("accidental").text = "natural"
    concert.set_concert_pitch(root, True)
    with concert.editing(root):
        later = next(note.get("id") for note in root.findall("part/measure")[1].iter("note"))
        edit.write_note(root, later, "quarter", 0, "E")
    assert courtesy.findtext("accidental") == "natural"


def test_going_to_concert_pitch_and_back_changes_nothing():
    root = structure.blank_score(clef="piano", measures=2)
    for name in ("trumpet", "alto-sax", "tenor-sax", "bass-guitar", "horn"):
        structure.add_part(root, name)
    edit.ensure_note_ids(root)
    for part in root.findall("part")[1:]:
        cursor = part.find("measure/note").get("id")
        for step in "CDEF":
            cursor = edit.write_note(root, cursor, "eighth", 0, step)["cursor"]
    before = to_text(root)
    moved = concert.to_sounding(root)
    concert.to_written(root, moved)
    assert to_text(root) == before


def test_an_octave_instrument_takes_an_octave_clef_at_concert_pitch():
    root = structure.blank_score(clef="bass-guitar", measures=1)
    edit.ensure_note_ids(root)
    edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, "E")
    shown = concert.shown(copy.deepcopy(root))
    assert shown.findtext(".//clef/clef-octave-change") is None, "shown as written until asked"
    concert.set_concert_pitch(root, True)
    shown = concert.shown(root)
    assert shown.findtext(".//clef/sign") == "F" and shown.findtext(".//clef/clef-octave-change") == "-1"
    assert [int(note.findtext("pitch/octave")) for note in shown.iter("note") if note.find("pitch") is not None] == [2]


def test_a_tenor_saxophone_sounds_a_ninth_lower_and_reads_in_a_treble_clef_with_an_eight_below():
    root = structure.blank_score(clef="tenor-sax", measures=1)
    edit.ensure_note_ids(root)
    edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, "D")
    concert.set_concert_pitch(root, True)
    shown = concert.shown(root)
    note = next(note for note in shown.iter("note") if note.find("pitch") is not None)
    assert (note.findtext("pitch/step"), note.findtext("pitch/octave")) == ("C", "4")
    assert shown.findtext(".//clef/clef-octave-change") == "-1"


def test_a_click_at_concert_pitch_on_an_octave_clef_lands_where_it_was_clicked(client):
    score_id = blank(client, clef="bass-guitar")
    at_concert_pitch(client, score_id)
    post(client, score_id, f"notes/{first_rest(client, score_id)}", {"action": "write", "type": "quarter", "line": 8})  # The bottom line.
    assert written_notes(client, score_id) == ["G2"], "the bottom line of a bass staff is G however it's shown"


def test_a_midi_key_at_concert_pitch_is_the_sounding_note(client):
    score_id = blank(client, clef="trumpet")
    at_concert_pitch(client, score_id)
    post(client, score_id, f"notes/{first_rest(client, score_id)}", {"action": "write", "type": "quarter", "midi": 60})
    assert written_notes(client, score_id) == ["D4"]


def test_pasting_from_a_trumpet_onto_a_piano_keeps_the_sound(client):
    score_id = blank(client, more=["trumpet"])
    trumpet = first_rest(client, score_id, part=1)
    note = post(client, score_id, f"notes/{trumpet}", {"action": "write", "type": "quarter", "step": "D"})["written"]
    copied = client.post(f"/api/scores/{score_id}/copy", json={"first": note, "last": note}).json()
    post(client, score_id, "paste", {"at": first_rest(client, score_id), "events": copied["events"]})
    assert written_notes(client, score_id, part=0) == ["C5"]
    assert written_notes(client, score_id, part=1) == ["D5"]


def test_a_copy_that_does_not_say_its_transposition_pastes_as_written(client):
    score_id = blank(client, clef="trumpet")
    events = [{"type": "quarter", "pitches": [["E", 0, 5]]}]
    post(client, score_id, "paste", {"at": first_rest(client, score_id), "events": events})
    assert written_notes(client, score_id) == ["E5"]


def test_a_score_without_transposing_parts_says_so(client):
    data = client.get(f"/api/scores/{blank(client)}").json()
    assert data["concertPitch"] is False and data["transposing"] is False
