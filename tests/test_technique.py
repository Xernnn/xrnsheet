"""How guitarists, string players and pianists are told to play: the marks, their drawing and their sound."""

import copy
import io
import re
import xml.etree.ElementTree as ET

import pytest

from pipeline import edit, fretted, marks, structure, technique
from pipeline.engrave import render_pages
from pipeline.midi_file import midi_bytes
from pipeline.musicxml import to_text
from pipeline.playback import playback
from pipeline.rhythm import TAB_COPY, EditError


def finished(root):
    """Finish an edit as the server does."""
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    fretted.sync(root)
    return root


def score(clef="guitar-tab", measures=2, *midis, length="quarter"):
    root = finished(structure.blank_score(clef=clef, measures=measures))
    cursor = next(note for note in root.iter("note") if (note.findtext("staff") or "1") == "1").get("id")
    written = []
    for midi in midis:
        done = edit.write_note(root, cursor, length, 0, None, midi=midi)
        written.append(done["written"])
        cursor = done["cursor"]
        finished(root)
    return root, written


def note(root, note_id):
    """Find a note, or a fret number's copy of it on the tablature by its own id."""
    return next(found for found in root.iter("note") if found.get("id") == note_id)


def technical(root, note_id, tag):
    return [found for found in note(root, note_id).iterfind(f"notations/technical/{tag}")]


def svg_of(root):
    return "".join(render_pages(to_text(root)))


def heard(root):
    return playback(root)["notes"]


def by_id(root, note_id):
    return [entry for entry in heard(root) if entry["id"] == note_id]


def test_a_hammer_on_joins_a_note_to_the_next_and_comes_off_again():
    root, ids = score("guitar-tab", 1, 64, 67, 69)
    technique.toggle_pair(root, ids[0], ids[0], "hammerOn")
    start, stop = technical(root, ids[0], "hammer-on"), technical(root, ids[1], "hammer-on")
    assert [(start[0].get("type"), start[0].text)] == [("start", "H")] and stop[0].get("type") == "stop"
    assert start[0].get("number") == stop[0].get("number")
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    assert not technical(root, ids[0], "hammer-on") and note(root, ids[1]).find("notations") is None


def test_a_stretch_is_joined_note_to_note():
    root, ids = score("guitar-tab", 1, 64, 67, 69)
    technique.toggle_pair(root, ids[0], ids[2], "pullOff")
    middle = technical(root, ids[1], "pull-off")
    assert sorted(found.get("type") for found in middle) == ["start", "stop"]
    assert len({found.get("number") for found in middle}) == 2, "each joint has its own number"


def test_a_hammer_on_whose_second_note_is_deleted_goes():
    root, ids = score("guitar-tab", 1, 64, 67)
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    edit.delete_note(root, ids[1])
    finished(root)
    assert not technical(root, ids[0], "hammer-on")


@pytest.mark.parametrize(("name", "line"), [("slide", "solid"), ("glissando", "wavy")])
def test_a_slide_or_glissando_joins_two_notes(name, line):
    root, ids = score("guitar-tab", 1, 64, 69)
    technique.toggle_pair(root, ids[0], ids[1], name)
    finished(root)
    start = note(root, ids[0]).find(f"notations/{name}")
    assert (start.get("type"), start.get("line-type")) == ("start", line)
    assert note(root, ids[1]).find(f"notations/{name}").get("type") == "stop"
    assert svg_of(root).count(f'class="gliss {name}"') == 2, "the tablature shows it too"


def test_a_bend_is_half_full_or_one_and_a_half_tones():
    root, ids = score("guitar-tab", 1, 64)
    technique.toggle_bend(root, ids, "full")
    assert [bend.findtext("bend-alter") for bend in technical(root, ids[0], "bend")] == ["2"]
    technique.toggle_bend(root, ids, "half")
    assert [bend.findtext("bend-alter") for bend in technical(root, ids[0], "bend")] == ["1"]
    technique.toggle_bend(root, ids, "half")
    assert not technical(root, ids[0], "bend")
    with pytest.raises(EditError):
        technique.toggle_bend(root, ids, "quarter")


def test_a_vibrato_is_a_wavy_line_over_a_note():
    root, ids = score("guitar-tab", 1, 64, 67)
    technique.toggle_vibrato(root, ids[0], ids[0])
    ends = note(root, ids[0]).findall("notations/ornaments/wavy-line")
    assert [(end.get("type"), end.get("smufl")) for end in ends] == [("start", "wiggleVibrato"), ("stop", None)]
    technique.toggle_vibrato(root, ids[0], ids[0])
    assert note(root, ids[0]).find("notations") is None


@pytest.mark.parametrize(("name", "words", "placement"), [("palmMute", "P.M.", "below"), ("letRing", "let ring", "above")])
def test_palm_mute_and_let_ring_are_words_and_a_dashed_line(name, words, placement):
    root, ids = score("guitar-tab", 2, 52, 55, 57, 59, 60)
    technique.toggle_named_line(root, ids[1], ids[4], name)
    finished(root)
    directions = list(root.iter("direction"))
    assert [found.findtext("direction-type/words") for found in directions] == [words, None]
    assert [found.get("placement") for found in directions] == [placement, placement]
    assert [found.find(".//dashes").get("type") for found in directions] == ["start", "stop"]
    listed = [item for item in marks.list_marks(root) if item["kind"] == "textline"]
    assert [(item["text"], item["drawn"]) for item in listed] == [(words, "dir")], "drawn once, from its words"
    marks.remove_placed(root, listed[0]["id"])
    assert not list(root.iter("direction"))


def test_the_same_line_again_comes_off_and_an_overlapping_one_replaces_it():
    root, ids = score("guitar-tab", 1, 52, 55, 57, 59)
    technique.toggle_named_line(root, ids[0], ids[1], "palmMute")
    technique.toggle_named_line(root, ids[1], ids[3], "palmMute")
    starts = [found for found in root.iter("dashes") if found.get("type") == "start"]
    assert len(starts) == 1
    technique.toggle_named_line(root, ids[1], ids[3], "palmMute")
    assert not list(root.iter("direction"))


def test_a_text_line_loses_its_words_with_its_end():
    root, ids = score("guitar-tab", 2, 52, 55, 57, 59, 60)
    technique.toggle_named_line(root, ids[0], ids[4], "letRing")
    structure.delete_measure(root, 1)
    assert not list(root.iter("direction"))


def test_natural_and_artificial_harmonics_on_a_guitar():
    root, ids = score("guitar-tab", 1, 64, 67)
    technique.toggle_harmonic(root, [ids[0]], "natural")
    technique.toggle_harmonic(root, [ids[1]], "artificial")
    assert technique.harmonic_kind(note(root, ids[0])) == ("natural", None)
    assert technique.harmonic_kind(note(root, ids[1])) == ("artificial", None)
    assert len(edit.locate(root, ids[1]).event.notes) == 1, "a guitar's artificial harmonic is the fretted note alone"
    technique.toggle_harmonic(root, [ids[0], ids[1]], "natural")
    technique.toggle_harmonic(root, [ids[0], ids[1]], "natural")
    assert not technical(root, ids[0], "harmonic") and not technical(root, ids[1], "harmonic")


def test_a_violins_artificial_harmonic_touches_a_fourth_above():
    root, ids = score("violin", 1, 64)
    technique.toggle_harmonic(root, ids, "artificial")
    finished(root)
    stopped, touched = edit.locate(root, ids[0]).event.notes
    assert technique.harmonic_kind(stopped) == ("artificial", "base-pitch")
    assert technique.harmonic_kind(touched) == ("artificial", "touching-pitch")
    assert (touched.findtext("pitch/step"), touched.findtext("pitch/octave"), touched.findtext("notehead")) == ("A", "4", "diamond")
    technique.toggle_harmonic(root, ids, "artificial")
    assert len(edit.locate(root, ids[0]).event.notes) == 1 and not technical(root, ids[0], "harmonic")


def test_a_dead_note_is_an_x():
    root, ids = score("guitar-tab", 1, 64)
    technique.toggle_dead_note(root, ids)
    assert note(root, ids[0]).findtext("notehead") == "x"
    technique.toggle_dead_note(root, ids)
    assert note(root, ids[0]).find("notehead") is None


def test_right_hand_fingers_are_p_i_m_a():
    root, ids = score("guitar-tab", 1, 52, 59)
    technique.set_pluck(root, [ids[0]], "p")
    technique.set_pluck(root, [ids[1]], "i")
    assert [found.text for found in technical(root, ids[0], "pluck")] == ["p"]
    technique.set_pluck(root, [ids[1]], "i")
    assert not technical(root, ids[1], "pluck")
    with pytest.raises(EditError):
        technique.set_pluck(root, ids, "x")


def test_a_finger_goes_on_each_chosen_note_or_one_each_in_turn():
    root, ids = score("guitar-tab", 1, 64, 65, 67)
    technique.set_fingering(root, ids, "1")
    assert [[found.text for found in technical(root, item, "fingering")] for item in ids] == [["1"]] * 3
    technique.set_fingering(root, ids, "1 2 4")
    assert [technical(root, item, "fingering")[0].text for item in ids] == ["1", "2", "4"]
    technique.set_fingering(root, ids, "124")
    assert all(not technical(root, item, "fingering") for item in ids), "the same fingers again take them off"
    with pytest.raises(EditError, match="0 to 4"):
        technique.set_fingering(root, ids[:1], "5")
    with pytest.raises(EditError, match="one for each"):
        technique.set_fingering(root, ids, "12")


def test_a_pianists_fingers_go_above_the_right_hand_and_below_the_left():
    root = finished(structure.blank_score(clef="piano", measures=1))
    upper = edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, None, midi=72)["written"]
    lower_rest = next(found for found in root.iter("note") if found.findtext("staff") == "2").get("id")
    lower = edit.write_note(root, lower_rest, "quarter", 0, None, midi=48)["written"]
    technique.set_fingering(root, [upper, lower], "15")
    assert [(found.text, found.get("placement")) for found in technical(root, upper, "fingering")] == [("1", "above")]
    assert [(found.text, found.get("placement")) for found in technical(root, lower, "fingering")] == [("5", "below")]
    with pytest.raises(EditError, match="1 to 5"):
        technique.set_fingering(root, [upper], "0")


def test_a_guitars_string_number_moves_its_tablature_too():
    root, ids = score("guitar-tab", 1, 64)
    technique.set_string_number(root, ids, 2)
    finished(root)
    assert [found.text for found in technical(root, ids[0], "string")] == ["2"]
    copied = note(root, ids[0] + TAB_COPY)
    assert (copied.findtext("notations/technical/string"), copied.findtext("notations/technical/fret")) == ("2", "5")
    with pytest.raises(EditError, match="strings 1 to 6"):
        technique.set_string_number(root, ids, 7)
    with pytest.raises(EditError, match="can't play"):
        technique.set_string_number(root, ids, 6)  # E4 would be past the low E string's 19th fret.


def test_a_string_number_says_the_tablatures_string_when_none_is_given():
    root, ids = score("guitar-tab", 1, 64)
    technique.set_string_number(root, ids, 0)
    assert [found.text for found in technical(root, ids[0], "string")] == ["1"]


def test_a_violins_string_must_be_able_to_play_the_note():
    root, ids = score("violin", 1, 60)  # C4 is below the D, A and E strings.
    technique.set_string_number(root, ids, 4)
    assert [found.text for found in technical(root, ids[0], "string")] == ["4"]
    with pytest.raises(EditError, match="String 3 can't play C4"):
        technique.set_string_number(root, ids, 3)


def test_pizz_and_arco_are_words_that_playback_follows():
    root, ids = score("violin", 1, 64, 65, 67, 69)
    technique.set_pizzicato(root, ids[0], True)
    technique.set_pizzicato(root, ids[2], False)
    finished(root)
    sounds = [(found.findtext("direction-type/words"), found.find("sound").get("pizzicato")) for found in root.iter("direction")]
    assert sounds == [("pizz.", "yes"), ("arco", "no")]
    played = heard(root)
    assert [entry.get("program") for entry in played] == [46, 46, None, None]
    assert [entry["length"] for entry in played] == [0.5, 0.5, 1.0, 1.0]
    assert bytes([0xC0, 45]) in midi_bytes(playback(root)), "a MIDI file changes to pizzicato strings"
    technique.set_pizzicato(root, ids[0], True)
    assert [found.findtext("direction-type/words") for found in root.iter("direction")] == ["arco"]


def test_a_position_is_marked_in_roman_numerals():
    root, ids = score("violin", 1, 64, 65)
    technique.set_position(root, ids[0], 3)
    assert [marks.direction_label(found) for found in root.iter("direction")] == ["III pos."]
    technique.set_position(root, ids[0], 5)
    assert [marks.direction_label(found) for found in root.iter("direction")] == ["V pos."]
    technique.set_position(root, ids[0], 5)
    assert not list(root.iter("direction"))
    with pytest.raises(EditError):
        technique.set_position(root, ids[0], 13)


def test_sul_g_is_a_line_over_a_passage():
    root, ids = score("violin", 1, 64, 65)
    technique.toggle_sul(root, ids[0], ids[1], "G")
    assert [marks.direction_label(found) for found in root.iter("direction")][0] == "sul G"
    with pytest.raises(EditError, match="string"):
        technique.toggle_sul(root, ids[0], ids[1], "C")


def test_note_marks_can_be_clicked_and_removed_by_their_ids():
    root, ids = score("guitar-tab", 1, 64, 67, 69)
    technique.set_fingering(root, [ids[0]], "2")
    technique.set_pluck(root, [ids[1]], "m")
    technique.toggle_bend(root, [ids[2]], "full")
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    finished(root)
    listed = {item["kind"]: item for item in marks.list_marks(root)}
    assert {kind: (item["text"], item["drawn"]) for kind, item in listed.items()} == {"fingering": ("2", "self"), "pluck": ("m", "self"), "bend": ("full", "self"), "hammer": ("H", "self")}
    for kind in ("fingering", "hammer", "bend"):
        marks.remove_placed(root, listed[kind]["id"])
    assert not technical(root, ids[0], "fingering") and not technical(root, ids[2], "bend")
    assert not technical(root, ids[0], "hammer-on") and not technical(root, ids[1], "hammer-on"), "a hammer-on goes with its other end"


def test_clearing_a_notes_marks_takes_a_hammer_on_with_it():
    root, ids = score("guitar-tab", 1, 64, 67)
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    edit.clear_marks(root, ids[0])
    assert not technical(root, ids[1], "hammer-on")


def test_techniques_go_through_musicxml_unchanged():
    root, ids = score("guitar-tab", 1, 64, 67, 69, 71)
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    technique.toggle_bend(root, [ids[2]], "half")
    technique.toggle_vibrato(root, ids[3], ids[3])
    technique.set_pluck(root, [ids[3]], "a")
    technique.toggle_named_line(root, ids[0], ids[3], "palmMute")
    finished(root)
    opened = finished(ET.fromstring(to_text(root).split("\n", 1)[1]))
    assert to_text(opened) == to_text(root)


def test_a_guitars_marks_are_drawn_where_verovio_leaves_them_out():
    root, ids = score("guitar-tab", 2, 64, 67, 69, 71, 59, 62, 64)
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    technique.toggle_pair(root, ids[2], ids[3], "pullOff")
    technique.toggle_bend(root, [ids[4]], "full")
    technique.toggle_dead_note(root, [ids[5]])
    technique.toggle_harmonic(root, [ids[6]], "natural")
    technique.set_pluck(root, [ids[0]], "p")
    technique.set_string_number(root, [ids[1]], 2)
    finished(root)
    svg = svg_of(root)
    listed = {item["id"]: item for item in marks.list_marks(root)}
    named = re.findall(r'data-mark="([^"]+)"', svg)
    assert set(named) == set(listed), "every mark a click can name is named in the drawing"
    hammer = next(item["id"] for item in listed.values() if item["kind"] == "hammer")
    assert re.search(rf'class="xrn-joined" data-mark="{hammer}"><text [^>]*>H</text>', svg) and ">P</text>" in svg
    assert svg.count(f'class="slur" data-mark="{hammer}"') == 2, "the slur on both staves is the hammer-on"
    assert re.search(rf'<g id="{ids[5]}{TAB_COPY}" class="note">\s*<text [^>]*>\s*<tspan [^>]*>X</tspan>', svg), "a dead note is an X on the tablature"
    assert "&lt;" + note(root, ids[6] + TAB_COPY).findtext("notations/technical/fret") + "&gt;" in svg
    assert 'class="xrn-bend"' in svg and ">full</text>" in svg
    pluck = next(item["id"] for item in listed.values() if item["kind"] == "pluck")
    assert re.search(rf'data-mark="{pluck}">\s*<text font-style="italic"', svg)
    string = next(item["id"] for item in listed.values() if item["kind"] == "string")
    drawn = re.search(rf'data-mark="{string}">(.*?)</g>', svg, re.S).group(1)
    assert "<circle" in drawn and ">2</tspan>" in drawn


def test_fingerings_are_named_in_the_order_each_bar_draws_them():
    root = finished(structure.blank_score(clef="piano", measures=1))
    structure.add_part(root, "guitar")
    finished(root)
    upper = edit.write_note(root, root.find("part/measure/note").get("id"), "quarter", 0, None, midi=72)
    second = edit.write_note(root, upper["cursor"], "quarter", 0, None, midi=74)["written"]
    edit.add_pitch(root, upper["written"], "", midi=76)
    lower_rest = next(found for found in root.iter("note") if found.findtext("staff") == "2").get("id")
    lower = edit.write_note(root, lower_rest, "half", 0, None, midi=48)["written"]
    guitar_rest = root.findall("part")[1].find("measure/note").get("id")
    plucked = edit.write_note(root, guitar_rest, "quarter", 0, None, midi=60)["written"]
    finished(root)
    chord = [found.get("id") for found in edit.locate(root, upper["written"]).event.notes]
    technique.set_fingering(root, chord + [second, lower], "1352")
    technique.set_fingering(root, [plucked], "3")
    finished(root)
    svg = svg_of(root)
    texts = {item["id"]: item["text"] for item in marks.list_marks(root)}
    drawn = re.findall(r'data-mark="([^"]+)">\s*<text [^>]*>\s*<tspan [^>]*>\s*<tspan [^>]*>([^<]+)</tspan>', svg)
    assert len(drawn) == 5 and all(texts[mark_id] == text for mark_id, text in drawn)


def test_a_violins_string_numbers_are_roman():
    root, ids = score("violin", 1, 55)
    technique.set_string_number(root, ids, 4)
    finished(root)
    assert re.search(r"data-mark=\"[^\"]+\">\s*<text [^>]*>\s*<tspan [^>]*>\s*<tspan [^>]*>IV</tspan>", svg_of(root))


def test_an_artificial_harmonic_is_marked_ah_on_a_guitar():
    root, ids = score("guitar-tab", 1, 64)
    technique.toggle_harmonic(root, ids, "artificial")
    finished(root)
    svg = svg_of(root)
    assert ">A.H.</text>" in svg and ">0&lt;12&gt;</tspan>" in svg, "the open string, touched at the twelfth fret"


def test_a_hammer_on_is_played_legato():
    root, ids = score("guitar-tab", 1, 64, 67)
    marks.toggle_mark(root, [ids[0]], "staccato")
    before = by_id(root, ids[1])[0]["volume"]
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    assert by_id(root, ids[0])[0]["length"] == 1.0, "the first note isn't cut short"
    assert by_id(root, ids[1])[0]["volume"] < before, "the hammered note isn't picked"


def test_a_slide_passes_through_the_notes_between():
    root, ids = score("guitar-tab", 1, 64, 69)
    technique.toggle_pair(root, ids[0], ids[1], "slide")
    sounds = by_id(root, ids[0])
    assert [entry["midi"] for entry in sounds] == [64, 65, 66, 67, 68]
    assert sum(entry["length"] for entry in sounds) == pytest.approx(1.0)


def test_a_bend_reaches_its_pitch():
    root, ids = score("guitar-tab", 1, 64)
    technique.toggle_bend(root, ids, "full")
    sounds = by_id(root, ids[0])
    assert [(entry["midi"], entry.get("bend")) for entry in sounds] == [(64, None), (66, 2)]
    assert sounds[1]["start"] == pytest.approx(0.25)


def test_palm_muted_notes_are_short_and_let_ring_notes_ring():
    root, ids = score("guitar-tab", 1, 52, 55, 57, 59)
    technique.toggle_named_line(root, ids[0], ids[1], "palmMute")
    technique.toggle_named_line(root, ids[2], ids[3], "letRing")
    lengths = [entry["length"] for entry in heard(root)]
    assert lengths[0] == lengths[1] == pytest.approx(0.4)
    assert lengths[2] == pytest.approx(2.0), "the first note under let ring rings to the end of the line"


def test_a_dead_note_is_a_short_click():
    root, ids = score("guitar-tab", 1, 64)
    technique.toggle_dead_note(root, ids)
    assert by_id(root, ids[0])[0]["length"] == pytest.approx(0.1)


def test_harmonics_sound_where_they_ring():
    root, ids = score("guitar-tab", 1, 64, 52)
    fretted.choose_string(root, ids[0], 1)
    technique.toggle_harmonic(root, [ids[0]], "artificial")
    technique.toggle_harmonic(root, [ids[1]], "natural")
    finished(root)
    copied = note(root, ids[1] + TAB_COPY)
    assert copied.findtext("notations/technical/fret") == "2"
    assert by_id(root, ids[0])[0]["midi"] == 76, "a guitar's artificial harmonic is an octave up"
    assert by_id(root, ids[1])[0]["midi"] == 64, "a natural harmonic away from a node sounds an octave up"
    violin, played = score("violin", 1, 64)
    technique.toggle_harmonic(violin, played, "artificial")
    finished(violin)
    assert [entry["midi"] for entry in heard(violin)] == [88], "two octaves above the stopped note, and the diamond is silent"


def test_a_natural_harmonic_at_the_twelfth_fret_sounds_an_octave_above_the_string():
    root, ids = score("guitar-tab", 1, 52)  # E3 on the low E string's twelfth fret, as a player chooses it.
    fretted.choose_string(root, ids[0], 6)
    technique.toggle_harmonic(root, ids, "natural")
    finished(root)
    assert note(root, ids[0] + TAB_COPY).findtext("notations/technical/fret") == "12"
    assert by_id(root, ids[0])[0]["midi"] == 52
    root, ids = score("guitar-tab", 1, 47)  # B2 at the seventh fret of the low E string, whose harmonic there is B3.
    fretted.choose_string(root, ids[0], 6)
    technique.toggle_harmonic(root, ids, "natural")
    finished(root)
    assert by_id(root, ids[0])[0]["midi"] == 59


@pytest.fixture
def client(tmp_path, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from app import server

    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def written_through(client, clef, *midis):
    made = client.post("/api/scores/blank", json={"measures": 2, "clef": clef}).json()
    url = f"/api/scores/{made['id']}"
    cursor = next(found.get("id") for found in ET.fromstring(client.get(f"{url}/score.musicxml").text.encode()).iter("note"))
    ids = []
    for midi in midis:
        done = client.post(f"{url}/notes/{cursor}", json={"action": "write", "type": "quarter", "midi": midi}).json()
        ids.append(done["written"])
        cursor = done["cursor"]
    return url, ids


def saved(client, url):
    return ET.fromstring(client.get(f"{url}/score.musicxml").text.encode())


def test_every_guitar_technique_is_an_edit_that_can_be_undone(client):
    url, ids = written_through(client, "guitar-tab", 64, 67, 69, 71)
    requests = [
        {"action": "fingering", "notes": ids[:3], "text": "1 3 4"},
        {"action": "pluck", "notes": ids[:1], "text": "p"},
        {"action": "stringNumber", "notes": ids[1:2], "number": 2},
        {"action": "bend", "notes": ids[2:3], "name": "half"},
        {"action": "harmonic", "notes": ids[3:], "name": "natural"},
        {"action": "deadNote", "notes": ids[3:]},
        {"action": "join", "notes": [ids[0], ids[1]], "name": "hammerOn"},
        {"action": "join", "notes": [ids[2], ids[3]], "name": "slide"},
        {"action": "vibrato", "notes": ids[3:]},
        {"action": "line", "notes": [ids[0], ids[3]], "name": "palmMute"},
        {"action": "toggle", "notes": ids[:1], "name": "open"},
    ]
    for request in requests:
        response = client.post(f"{url}/marks", json=request)
        assert response.status_code == 200, (request, response.json())
    data = client.get(url).json()
    assert {item["kind"] for item in data["marks"]} >= {"fingering", "pluck", "string", "bend", "hammer", "textline"}
    root = saved(client, url)
    assert note(root, ids[1] + TAB_COPY).findtext("notations/technical/string") == "2"
    fingering = next(item for item in data["marks"] if item["kind"] == "fingering")
    assert client.post(f"{url}/marks", json={"action": "remove", "id": fingering["id"]}).status_code == 200
    assert len(saved(client, url).findall(".//fingering")) == 2
    client.post(f"{url}/undo")
    assert len(saved(client, url).findall(".//fingering")) == 3
    assert client.post(f"{url}/marks", json={"action": "bend", "notes": ids[:1], "name": "huge"}).status_code == 422
    assert client.post(f"{url}/marks", json={"action": "stringNumber", "notes": ids[:1], "number": 9}).status_code == 422


def test_every_violin_marking_is_an_edit(client):
    url, ids = written_through(client, "violin", 55, 62, 69, 76)
    requests = [
        {"action": "toggle", "notes": ids[:1], "name": "downbow"},
        {"action": "toggle", "notes": ids[1:2], "name": "upbow"},
        {"action": "stringNumber", "notes": ids[:1], "number": 4},
        {"action": "sul", "notes": [ids[0], ids[1]], "text": "G"},
        {"action": "position", "notes": ids[2:3], "number": 3},
        {"action": "pizzicato", "notes": ids[2:3], "on": True},
        {"action": "pizzicato", "notes": ids[3:], "on": False},
        {"action": "harmonic", "notes": ids[3:], "name": "artificial"},
        {"action": "fingering", "notes": ids, "text": "0124"},
    ]
    for request in requests:
        response = client.post(f"{url}/marks", json=request)
        assert response.status_code == 200, (request, response.json())
    labels = [item["text"] for item in client.get(url).json()["marks"]]
    assert {"sul G", "III pos.", "pizz.", "arco"} <= set(labels)
    played = client.get(f"{url}/playback").json()["notes"]
    assert [entry.get("program") for entry in played][:3] == [None, None, 46]
    assert client.post(f"{url}/marks", json={"action": "fingering", "notes": ids[:1], "text": "5"}).status_code == 422


def test_techniques_at_concert_pitch_are_kept_as_written(client):
    url, ids = written_through(client, "bass-guitar-tab", 43, 45)
    client.post(f"{url}/concert", json={"on": True})
    assert client.post(f"{url}/marks", json={"action": "stringNumber", "notes": ids[1:], "number": 4}).status_code == 200
    refused = client.post(f"{url}/marks", json={"action": "stringNumber", "notes": ids[:1], "number": 3})
    assert refused.status_code == 422 and "can't play G1" in refused.json()["detail"], "a written G2 sounds G1, below the A string"
    root = saved(client, url)
    assert note(root, ids[1]).findtext("pitch/octave") == "2", "the note is still written an octave above how it sounds"
    copied = note(root, ids[1] + TAB_COPY)
    assert (copied.findtext("notations/technical/string"), copied.findtext("notations/technical/fret")) == ("4", "5")


def piano(*midis):
    root = finished(structure.blank_score(clef="piano", measures=2))
    cursor = root.find("part/measure/note").get("id")
    written = []
    for midi in midis:
        done = edit.write_note(root, cursor, "quarter", 0, None, midi=midi)
        written.append(done["written"])
        cursor = done["cursor"]
    return finished(root), written


def staff_tops(svg):
    return sorted({int(found) for found in re.findall(r'class="staff">\s*<path d="M\d+ (\d+)', svg)})


def test_a_hand_is_marked_over_its_notes():
    root, ids = piano(60, 62)
    technique.toggle_hand(root, ids[:1], "LH")
    assert [marks.direction_label(found) for found in root.iter("direction")] == ["L.H."]
    technique.toggle_hand(root, ids[:1], "RH")
    assert [marks.direction_label(found) for found in root.iter("direction")] == ["R.H."]
    technique.toggle_hand(root, ids[:1], "RH")
    assert not list(root.iter("direction"))
    with pytest.raises(EditError):
        technique.toggle_hand(root, ids, "feet")


def test_a_note_can_be_drawn_on_the_other_staff_and_stay_in_its_voice():
    root, ids = piano(60, 55, 52, 60)
    technique.cross_staff(root, ids[1], 1)
    technique.cross_staff(root, ids[2], 1)
    finished(root)
    assert note(root, ids[1]).get(technique.CROSS_STAFF) == "2" and note(root, ids[1]).find("staff") is None
    assert structure.check_measures(root) == []
    svg = svg_of(root)
    top, lower = staff_tops(svg)[:2]
    heads = {found.group(1): int(found.group(2)) for found in re.finditer(r'<g id="(note\d+)" class="note">\s*<g class="notehead">\s*<use [^>]*translate\(\d+, (\d+)\)', svg)}
    assert heads[ids[0]] < lower <= heads[ids[1]], "drawn on the lower staff"
    played = {entry["id"]: entry["staff"] for entry in heard(root)}
    assert (played[ids[0]], played[ids[1]], played[ids[2]]) == (1, 2, 2), "played by the left hand"
    technique.cross_staff(root, ids[1], -1)
    assert note(root, ids[1]).get(technique.CROSS_STAFF) is None
    with pytest.raises(EditError, match="no staff"):
        technique.cross_staff(root, ids[0], -1)


def test_a_note_on_the_other_staff_is_saved_as_musicxml_has_it_and_opened_again():
    root, ids = piano(60, 55, 52, 60)
    technique.cross_staff(root, ids[1], 1)
    finished(root)
    written = technique.drawn_on_other_staves(copy.deepcopy(root))
    moved = next(found for found in written.iter("note") if found.get("id") == ids[1])
    assert moved.findtext("staff") == "2" and moved.get(technique.CROSS_STAFF) is None and moved.findtext("voice") == "1"
    technique.keep_cross_staff(written)
    for found in written.iter("note"):
        if found.get("id") == ids[1]:
            found.remove(found.find("staff"))  # A staff 1 note says nothing of its staff.
    assert to_text(written) == to_text(root)


def test_a_rest_and_a_one_staff_instrument_stay_where_they_are():
    root, ids = score("violin", 1, 64)
    with pytest.raises(EditError):
        technique.cross_staff(root, ids[0], 1)
    root, ids = piano(60)
    rest = edit.locate(root, ids[0]).event
    with pytest.raises(EditError):
        technique.cross_staff(root, next(found.get("id") for found in root.iter("note") if found.find("rest") is not None), 1)


def test_piano_hands_through_the_server(client):
    url, ids = written_through(client, "piano", 60, 55, 52, 60)
    assert client.post(f"{url}/notes/{ids[1]}", json={"action": "crossDown"}).status_code == 200
    assert client.post(f"{url}/group", json={"action": "crossDown", "notes": ids[2:4]}).status_code == 200
    assert client.post(f"{url}/marks", json={"action": "hand", "notes": ids[3:], "name": "LH"}).status_code == 200
    assert client.post(f"{url}/marks", json={"action": "fingering", "notes": ids, "text": "1 2 3 1"}).status_code == 200
    staves = [found.findtext("staff") for found in saved(client, url).iter("note") if found.get("id") in ids]
    assert staves == [None, "2", "2", "2"], "a downloaded file has them on the staff they're drawn on"
    played = client.get(f"{url}/playback").json()["notes"]
    assert [entry["staff"] for entry in played] == [1, 2, 2, 2]
    reopened = client.post("/api/scores", files={"file": ("piano.musicxml", io.BytesIO(client.get(f"{url}/score.musicxml").content))}).json()
    again = client.get(f"/api/scores/{reopened['id']}/playback").json()["notes"]
    assert [entry["staff"] for entry in again] == [1, 2, 2, 2]
    assert client.get(f"/api/scores/{reopened['id']}").json()["barChecks"] == []
    assert client.post(f"{url}/notes/{ids[0]}", json={"action": "crossUp"}).status_code == 422


@pytest.mark.parametrize("seed", range(8))
def test_random_edits_on_a_piano_with_notes_on_the_other_staff_keep_it_whole(seed):
    import random

    from test_editor_fuzz import attempt, check_outputs, check_rules

    rng = random.Random(seed)
    root, ids = piano(*(rng.randint(48, 76) for _ in range(6)))
    for step in range(80):
        if rng.random() < 0.25:
            trial = copy.deepcopy(root)
            notes = [found.get("id") for found in trial.iter("note") if found.find("pitch") is not None]
            try:
                if notes:
                    target = rng.choice(notes)
                    trial.set("last-edit", f"cross staff on {target}")
                    rng.choice([lambda: technique.cross_staff(trial, target, rng.choice([-1, 1])), lambda: technique.toggle_hand(trial, [target], rng.choice(list(technique.HANDS))), lambda: technique.set_fingering(trial, [target], rng.choice("12345"))])()
                root = trial
            except EditError:
                pass
        else:
            root, _ = attempt(root, rng, overfill=False)
        finished(root)
        check_rules(root)
        assert structure.check_measures(root) == [], f"a bar stopped adding up after {root.get('last-edit')}"
        saved_and_opened = technique.drawn_on_other_staves(copy.deepcopy(root))
        technique.keep_cross_staff(saved_and_opened)
        moved = {found.get("id"): found.get(technique.CROSS_STAFF) for found in saved_and_opened.iter("note")}
        assert moved == {found.get("id"): found.get(technique.CROSS_STAFF) for found in root.iter("note")}, f"a file lost where notes are drawn after {root.get('last-edit')}"
        if step % 20 == 0:
            check_outputs(root)
    check_outputs(root)


def test_a_hammer_ons_letter_goes_on_the_tablature_or_else_the_staff():
    root, ids = score("guitar-tab", 1, 64, 67)
    technique.toggle_pair(root, ids[0], ids[1], "hammerOn")
    finished(root)
    assert svg_of(root).count(">H</text>") == 1
    plain, ids = score("guitar", 1, 64, 67)
    technique.toggle_pair(plain, ids[0], ids[1], "hammerOn")
    finished(plain)
    svg = svg_of(plain)
    assert svg.count(">H</text>") == 1 and 'class="slur" data-mark=' in svg
