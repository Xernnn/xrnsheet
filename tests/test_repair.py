import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
from helpers import harmony, note

from pipeline import recognize, repair, structure
from pipeline.musicxml import to_text
from pipeline.rhythm import contexts, read_measure


def score(*bars, beats=4, beat_type=4, divisions=4, parts=1):
    """Build a score from the inside of each bar. The first bar carries the signature."""
    measures = []
    for number, inside in enumerate(bars, 1):
        attributes = f"<attributes><divisions>{divisions}</divisions><time><beats>{beats}</beats><beat-type>{beat_type}</beat-type></time></attributes>" if number == 1 else ""
        measures.append(f'<measure number="{number}">{attributes}{inside}</measure>')
    body = "".join(f'<part id="P{number}">{"".join(measures)}</part>' for number in range(parts))
    return ET.fromstring(f'<score-partwise><part-list/>{body}</score-partwise>')


def beats_of(root):
    return [(context.beats, context.beat_type) for context in contexts(root.find("part"))]


def quarters(count):
    return note(duration=4, note_type="quarter") * count


def whole_rest(beats=4):
    return f"<note><rest/><duration>{beats * 4}</duration><voice>1</voice><type>whole</type></note>"


def pair(step="C"):
    """Two staves meant to sound together, written the way homr loses the step back between them."""
    upper = note(step, duration=4, note_type="quarter").replace("<type>", "<voice>1</voice><type>").replace("</note>", "<staff>1</staff></note>")
    lower = note("G", duration=4, note_type="quarter").replace("<type>", "<voice>5</voice><type>").replace("</note>", "<staff>2</staff></note>")
    return upper, lower


def line_onsets(root, bar, voice):
    context = list(contexts(root.find("part")))[bar]
    for key, line in read_measure(context.measure, context.divisions).lines.items():
        if key[1] == voice:
            return [event.onset for event in sorted(line, key=lambda item: item.onset)]
    return []


def test_a_misread_signature_is_put_right():
    root = score(whole_rest(), *[quarters(3)] * 6)
    repair.repair(root)
    assert beats_of(root)[0] == (3, 4) and structure.check_measures(root) == []


def test_every_part_changes_together():
    root = score(whole_rest(), *[quarters(3)] * 6, parts=2)
    repair.repair(root)
    assert all(context.beats == 3 for part in root.findall("part") for context in contexts(part))


def test_empty_bars_change_size_with_the_signature():
    root = score(whole_rest(), whole_rest(), *[quarters(3)] * 6)
    repair.repair(root)
    rests = [note_ for note_ in root.iter("note") if note_.find("rest") is not None]
    assert [rest.find("rest").get("measure") for rest in rests] == ["yes", "yes"]
    assert structure.check_measures(root) == []


def test_empty_bars_get_no_vote():
    root = score(*[whole_rest()] * 8, *[quarters(3)] * 6)
    repair.repair(root)
    assert beats_of(root)[0] == (3, 4)


def test_the_first_and_last_bars_get_no_vote():
    root = score(quarters(1), *[quarters(4)] * 5, quarters(3))
    before = to_text(root)
    repair.repair(root)
    assert to_text(root) == before


def test_a_right_signature_is_left_alone():
    root = score(*[quarters(4)] * 8)
    before = to_text(root)
    repair.repair(root)
    assert to_text(root) == before


def test_too_few_bars_are_left_alone():
    root = score(*[quarters(3)] * 4)
    repair.repair(root)
    assert beats_of(root)[0] == (4, 4)


def test_bars_that_disagree_are_left_alone():
    root = score(quarters(4), quarters(3), quarters(2), quarters(3), quarters(4), quarters(5), quarters(3), quarters(4))
    repair.repair(root)
    assert beats_of(root)[0] == (4, 4)


def test_a_score_that_changes_signature_is_left_alone():
    root = score(*[quarters(3)] * 6)
    list(root.find("part"))[3].insert(0, ET.fromstring("<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"))
    before = to_text(root)
    repair.repair(root)
    assert to_text(root) == before


def test_the_same_length_written_differently_is_left_alone():
    root = score(*[quarters(4)] * 6, beats=2, beat_type=2)
    repair.repair(root)
    assert beats_of(root)[0] == (2, 2)


def test_eighth_note_signatures_stay_in_eighths():
    root = score(*[quarters(3)] * 6, beats=9, beat_type=8)
    repair.repair(root)
    assert beats_of(root)[0] == (6, 8)


def test_a_length_no_signature_can_hold_is_left_alone():
    root = score(*[note(duration=5, note_type="quarter")] * 6, beats=4, beat_type=4)
    repair.repair(root)
    assert beats_of(root)[0] == (4, 4)


def test_a_lost_step_back_is_restored():
    upper, lower = pair()
    root = score(quarters(4), upper + lower + upper + lower + upper + lower + upper + lower, quarters(4))
    repair.repair(root)
    assert line_onsets(root, 1, "1") == [0, 1, 2, 3]
    assert line_onsets(root, 1, "5") == [0, 1, 2, 3]
    assert structure.check_measures(root) == []


def test_no_note_is_lost_or_changed_by_laying_out_voices():
    upper, lower = pair()
    root = score(upper + lower + upper + lower + upper + lower + upper + lower)
    before = sorted((item.findtext("pitch/step"), item.findtext("duration")) for item in root.iter("note"))
    repair.lay_out_voices(root)
    assert sorted((item.findtext("pitch/step"), item.findtext("duration")) for item in root.iter("note")) == before


def test_a_voice_that_really_is_too_long_keeps_its_notes_where_they_are():
    root = score(quarters(5))
    before = to_text(root)
    repair.lay_out_voices(root)
    assert to_text(root) == before


def test_a_real_gap_in_a_voice_that_fits_stays():
    root = score(note(duration=4, note_type="quarter") + "<forward><duration>4</duration></forward>" + note("D", duration=4, note_type="quarter"))
    repair.lay_out_voices(root)
    context = next(contexts(root.find("part")))
    onsets = [event.onset for event in read_measure(context.measure, context.divisions).events()]
    assert onsets == [Fraction(0), Fraction(2)]


def test_only_the_voice_that_overruns_is_moved():
    upper, lower = pair()
    root = score(lower + upper * 4 + "<backup><duration>8</duration></backup>" + lower)
    repair.lay_out_voices(root)
    assert line_onsets(root, 0, "1") == [0, 1, 2, 3]
    assert line_onsets(root, 0, "5") == [0, 3]


def test_recognition_repairs_what_it_saves(tmp_path, monkeypatch):
    root = score(whole_rest(), *[quarters(3)] * 6)
    monkeypatch.setattr(recognize, "run_homr", lambda image, folder: (root, [], np.zeros((4, 4, 3), "uint8")))
    monkeypatch.setattr(recognize, "read_chords_on_page", lambda *arguments: [])
    saved, _ = recognize.recognize(tmp_path / "sheet.png", tmp_path)
    assert "<beats>3</beats>" in saved.read_text(encoding="utf-8")


def test_a_bar_that_cannot_be_made_to_fit_stays_as_it_was():
    upper, lower = pair()
    root = score(upper + lower + upper + lower + upper + lower + upper + lower + lower + lower)
    before = to_text(root)
    repair.lay_out_voices(root)
    assert to_text(root) == before


def test_staves_read_with_different_signatures_are_brought_together():
    root = score(whole_rest(), *[quarters(3)] * 6, parts=2)
    time = root.findall("part")[1].find("measure/attributes/time")
    time.find("beats").text = "3"
    repair.repair(root)
    assert {(context.beats, context.beat_type) for part in root.findall("part") for context in contexts(part)} == {(3, 4)}
    assert structure.check_measures(root) == []


def test_a_part_that_changes_signature_leaves_the_score_alone():
    root = score(*[quarters(3)] * 6, parts=2)
    list(root.findall("part")[1])[3].insert(0, ET.fromstring("<attributes><time><beats>3</beats><beat-type>4</beat-type></time></attributes>"))
    before = to_text(root)
    repair.repair(root)
    assert to_text(root) == before


def test_a_chord_keeps_its_beat_when_voices_move():
    upper, lower = pair()
    root = score(upper + lower + harmony("G", "7", "dominant") + upper + lower + upper + lower + upper + lower)
    context = next(contexts(root.find("part")))
    before = [mark.onset for mark in read_measure(context.measure, context.divisions).marks if mark.element.tag == "harmony"]
    repair.lay_out_voices(root)
    context = next(contexts(root.find("part")))
    after = [mark.onset for mark in read_measure(context.measure, context.divisions).marks if mark.element.tag == "harmony"]
    assert before == after
