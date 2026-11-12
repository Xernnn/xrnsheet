"""Chord diagrams: their shapes, their place in MusicXML, and their drawing over chord symbols."""

import io
import random
import re
import threading
import xml.etree.ElementTree as ET

import pytest

from chords.vocab import QUALITIES, Chord, parse_chord
from pipeline import diagrams, edit, fretted, marks, structure
from pipeline.diagrams import NAMES, ON_A, ON_E, OPEN, Shape, needed_of, shape_for, tones_of
from pipeline.engrave import render_pages
from pipeline.musicxml import to_text
from pipeline.structure import GUITAR_STRINGS, pitch_of_name

STANDARD = [fretted.midi_of(*pitch_of_name(name)) for name in GUITAR_STRINGS]


def sounds(shape: Shape, opens=STANDARD, capo=0) -> list[int]:
    return [open_string + capo + fret for open_string, fret in zip(opens, shape.frets) if fret is not None]


def plays(shape: Shape, chord: Chord, opens=STANDARD, capo=0) -> bool:
    """A shape plays a chord if its bass is the chord's bass, it has the notes the chord needs, and nothing else.

    A ukulele's strings don't rise from the first to the last, and its chords have no bass.
    """
    played = sounds(shape, opens, capo)
    classes = {midi % 12 for midi in played}
    bass = diagrams.PITCH_CLASSES[chord.bass or chord.root]
    rising = all(lower < higher for lower, higher in zip(opens, opens[1:]))
    return (min(played) % 12 == bass or not rising) and needed_of(chord) <= classes <= tones_of(chord) | {bass}


@pytest.mark.parametrize("name", sorted(OPEN))
def test_every_open_chord_in_the_library_plays_its_chord(name):
    assert plays(Shape(OPEN[name]), parse_chord(name)), name


@pytest.mark.parametrize("quality", sorted(set(ON_E) | set(ON_A)))
def test_every_barre_chord_in_the_library_plays_its_chord_on_every_root(quality):
    for root in NAMES:
        chord = Chord(root, quality)
        shape = shape_for(str(chord))
        assert shape is not None and plays(shape, chord), str(chord)
        assert all(0 <= fret <= 15 for fret in shape.fretted)


@pytest.mark.parametrize(("name", "frets"), [
    ("C", (None, 3, 2, 0, 1, 0)), ("G7", (3, 2, 0, 0, 0, 1)), ("F", (1, 3, 3, 2, 1, 1)), ("F#m", (2, 4, 4, 2, 2, 2)),
    ("Bb", (None, 1, 3, 3, 3, 1)), ("C#m7", (None, 4, 6, 4, 5, 4)), ("Bm7", (None, 2, 4, 2, 3, 2)),
])
def test_common_chords_are_the_shapes_guitarists_learn(name, frets):
    assert shape_for(name).frets == frets


def test_a_capo_turns_a_chord_into_the_shape_fingered_below_it():
    assert shape_for("D", capo=2) == shape_for("C"), "a D with the capo on the second fret is a C shape"
    assert shape_for("A", capo=2) == shape_for("G")


def test_a_barre_is_drawn_only_where_a_hand_needs_one():
    assert shape_for("F").barre == (1, 0, 5)
    assert shape_for("Bm7").barre == (2, 1, 5)
    assert shape_for("D").barre is None, "three fingers, no barre"


@pytest.mark.parametrize("seed", range(80))
def test_any_chord_on_any_tuning_is_worked_out_to_play_it(seed):
    rng = random.Random(seed)
    tuning = rng.choice([*fretted.TUNINGS[GUITAR_STRINGS].values(), ("C2", "G2", "D3", "A3", "E4", "G4"), ("G4", "C4", "E4", "A4")])
    quality = rng.choice(sorted(QUALITIES))
    chord = Chord(rng.choice(NAMES), quality, rng.choice([None, None, None, rng.choice(NAMES)]))
    capo = rng.randint(0, 5)
    shape = shape_for(str(chord), tuple(tuning), capo)
    if shape is None:
        pytest.skip(f"no shape for {chord} in {tuning}")
    opens = [fretted.midi_of(*pitch_of_name(name)) for name in tuning]
    assert plays(shape, chord, opens, capo), (str(chord), tuning, capo, shape)


def test_common_chords_all_have_shapes_in_every_named_tuning():
    for tuning in fretted.TUNINGS[GUITAR_STRINGS].values():
        for name in ("C", "G", "D", "A", "E", "Am", "Em", "Dm", "F", "Bb", "G7", "D7", "Cmaj7"):
            assert shape_for(name, tuning) is not None, (name, tuning)


def test_a_shape_is_written_as_a_musicxml_frame_and_read_back():
    shape = shape_for("F")
    frame = diagrams.frame_of(shape)
    assert frame.findtext("frame-strings") == "6" and frame.findtext("frame-frets") == "4"
    assert [(note.findtext("string"), note.findtext("fret")) for note in frame.findall("frame-note")][:2] == [("6", "1"), ("5", "3")]
    assert [note.find("barre").get("type") for note in frame.findall("frame-note") if note.find("barre") is not None] == ["start", "stop"]
    assert diagrams.shape_of_frame(frame) == shape
    high = diagrams.frame_of(shape_for("C#m7"))
    assert high.findtext("first-fret") == "4"


def chart(*chords, clef="guitar-tab", capo=0):
    root = structure.blank_score(clef=clef, measures=len(chords))
    edit.ensure_note_ids(root)
    cursor = root.find("part/measure/note").get("id")
    for chord in chords:
        done = edit.write_note(root, cursor, "whole", 0, None, midi=64)
        edit.add_chord(root, done["written"], chord)
        cursor = done["cursor"]
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    if capo:
        fretted.set_strings(root, 0, capo=capo)
    fretted.sync(root)
    return root


def test_a_score_shows_diagrams_only_when_asked():
    root = chart("C", "G7")
    assert "xrn-diagram" not in "".join(render_pages(to_text(root)))
    diagrams.set_shown(root, True)
    svg = "".join(render_pages(to_text(root)))
    harms = re.findall(r'<g id="[^"]+" class="harm">(.*?)</g>\s*</g>', svg, re.S)
    assert svg.count('class="xrn-diagram"') == 2 and all("xrn-diagram" in found for found in harms), "each diagram is inside its chord symbol"


def test_diagrams_follow_the_capo():
    root = chart("D", capo=2)
    diagrams.set_shown(root, True)
    drawn = re.search(r'class="xrn-diagram">(.*?)</g>', "".join(render_pages(to_text(root))), re.S).group(1)
    assert drawn.count("<circle") == 5, "a C shape: three fingers and two open strings"


def test_a_file_gets_frames_and_brings_them_back():
    root = chart("C", "F#m")
    diagrams.set_shown(root, True)
    written = diagrams.with_frames(ET.fromstring(to_text(root).split("\n", 1)[1]))
    frames = [harmony.find("frame") for harmony in written.iter("harmony")]
    assert all(frame is not None for frame in frames) and frames[1].findtext("first-fret") is None
    own = diagrams.frame_of(Shape((None, 3, 5, 5, 5, 3)))  # A C barred at the third fret, a player's own choice.
    first = next(written.iter("harmony"))
    first.remove(first.find("frame"))
    first.insert(len(first), own)
    for found in written.iter("miscellaneous-field"):
        found.text = "no"
    diagrams.keep_imported(written)
    assert diagrams.shown(written)
    kept = [harmony.find("frame") for harmony in written.iter("harmony")]
    assert kept[0] is not None and kept[1] is None, "the library's own frame is made again, a player's is kept"
    drawn = re.search(r'class="xrn-diagram">(.*?)</g>', "".join(render_pages(to_text(written))), re.S).group(1)
    assert "3fr" in drawn


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app import server

    monkeypatch.setattr(server, "JOB_DIR", tmp_path)
    monkeypatch.setattr(server, "stopping", threading.Event())
    return TestClient(server.app, base_url="http://127.0.0.1:8000")


def test_diagrams_are_turned_on_and_off_through_the_server(client):
    opened = client.post("/api/scores", files={"file": ("chart.musicxml", io.BytesIO(to_text(chart("C", "Am")).encode()))}).json()
    url = f"/api/scores/{opened['id']}"
    assert client.get(url).json()["diagrams"] is False
    shown = client.post(f"{url}/diagrams", json={"on": True}).json()
    assert shown["diagrams"] is True and "xrn-diagram" in shown["pages"][0]
    assert client.get(f"{url}/score.musicxml").text.count("<frame>") == 2
    client.post(f"{url}/undo")
    data = client.get(url).json()
    assert data["diagrams"] is False and "xrn-diagram" not in data["pages"][0]
    assert "<frame>" not in client.get(f"{url}/score.musicxml").text


def diagram_widths(svg):
    widths = []
    for drawn in re.findall(r'class="xrn-diagram">(.*?)</g>', svg, re.S):
        xs = [int(found) for found in re.findall(r'<path d="M(-?\d+) -?\d+ L\1 ', drawn)]
        widths.append(max(xs) - min(xs))
    return widths


def test_chords_close_together_get_smaller_diagrams():
    root = structure.blank_score(clef="guitar-tab", measures=2)
    edit.ensure_note_ids(root)
    cursor = root.find("part/measure/note").get("id")
    written = []
    for length in ("half", "eighth", "eighth", "quarter", "whole"):
        done = edit.write_note(root, cursor, length, 0, None, midi=64)
        written.append(done["written"])
        cursor = done["cursor"]
    for note_id, chord in zip(written, ["C", "G", "Am", "F", "E"]):
        edit.add_chord(root, note_id, chord)
    fretted.sync(root)
    diagrams.set_shown(root, True)
    widths = diagram_widths("".join(render_pages(to_text(root))))
    assert len(widths) == 5 and widths[0] == max(widths) and widths[1] == widths[2] == min(widths) < widths[0], "the chords an eighth apart are drawn smallest"


def test_the_composer_is_lifted_clear_of_the_first_diagrams():
    root = chart("C", "G")
    structure.set_title(root, "Song", "Someone")
    diagrams.set_shown(root, True)
    svg = render_pages(to_text(root))[0]
    composer = int(re.search(r'<text x="\d+" y="(\d+)"[^>]*text-anchor="end"', svg).group(1)) if 'text-anchor="end"' in svg else None
    head = svg[svg.find('class="pgHead"'):]
    baselines = [int(found) for found in re.findall(r' y="(\d+)"', head[: head.find("</g>")])]
    tops = [int(found) for found in re.findall(r'class="xrn-diagram"><path d="M-?\d+ (-?\d+)', svg)]
    assert baselines and tops and max(baselines) < min(tops), (composer, baselines, tops)
