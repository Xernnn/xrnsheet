"""Instruments, tuplets, verses, line breaks, MIDI files, opening files and copying marks."""

import io
import signal
import xml.etree.ElementTree as ET
import zipfile
from fractions import Fraction

import pytest
from helpers import note, parsed

from pipeline import edit, engrave, marks, rhythm, structure, transpose
from pipeline.midi_file import channel_of, midi_bytes, variable_length
from pipeline.musicxml import read_score_file, to_text
from pipeline.playback import playback
from pipeline.rhythm import EditError, Tuplet, contexts, read_measure


def blank(**settings):
    root = structure.blank_score(**{"measures": 4} | settings)
    edit.ensure_note_ids(root)
    return root


def bars(root, part=0):
    return root.findall("part")[part].findall("measure")


def melody(root, *steps, length="quarter", start=None):
    cursor = start or bars(root)[0].find("note").get("id")
    written = []
    for step in steps:
        done = edit.write_note(root, cursor, length, 0, step)
        written.append(done["written"])
        cursor = done["cursor"]
    return written, cursor


def drawn(root):
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    return "".join(engrave.render_pages(to_text(root)))


def systems(svg):
    return [piece.count('class="measure"') for piece in svg.split('class="system"')[1:]]


@pytest.mark.parametrize("name", sorted(structure.INSTRUMENTS))
def test_every_instrument_starts_a_score_and_is_drawn(name):
    root = blank(clef=name)
    assert list(contexts(root.find("part")))[0].staves == len(structure.INSTRUMENTS[name].clefs)
    melody(root, "C")
    drawn(root)
    assert playback(root)["notes"]


@pytest.mark.parametrize(("name", "offset"), [("clarinet", 2), ("trumpet", 2), ("alto-sax", 3), ("tenor-sax", 2), ("horn", 1), ("flute", 0), ("cello", 0)])
def test_a_transposing_instrument_is_written_in_its_own_key(name, offset):
    root = blank(fifths=-1)
    structure.add_part(root, name)
    assert int(bars(root, 1)[0].findtext("attributes/key/fifths")) == -1 + offset


def test_an_added_part_matches_the_bars_above():
    root = blank(fifths=2, beats=3)
    structure.set_key(root, 2, -1)
    structure.set_time(root, 3, 2, 4)
    structure.toggle_repeat(root, 1, "backward")
    structure.add_part(root, "violin")
    added = list(contexts(root.findall("part")[1]))
    top = list(contexts(root.find("part")))
    assert [item.length for item in added] == [item.length for item in top]
    assert [item.fifths for item in added] == [item.fifths for item in top]
    assert bars(root, 1)[1].find("barline/repeat") is not None
    assert structure.check_measures(root) == []
    drawn(root)


def test_a_new_part_after_a_pickup_starts_short():
    root = blank()
    bars(root)[0].set("implicit", "yes")
    melody(root, "C")
    first = bars(root)[0]
    for note in first.findall("note")[1:]:
        first.remove(note)
    structure.add_part(root, "violin")
    assert sum(int(note.findtext("duration")) for note in bars(root, 1)[0].findall("note")) == 96


def test_bar_edits_reach_every_part():
    root = blank()
    structure.add_part(root, "violin")
    edit.ensure_note_ids(root)
    structure.insert_measure(root, 2)
    structure.delete_measure(root, 0)
    assert len(bars(root)) == len(bars(root, 1)) == 4
    assert structure.check_measures(root) == []
    drawn(root)


def test_a_key_change_keeps_a_transposing_part_apart():
    root = blank()
    structure.add_part(root, "trumpet")
    structure.set_key(root, 2, -2)
    assert [item.fifths for item in contexts(root.findall("part")[1])] == [2, 2, 0, 0]
    assert [item.fifths for item in contexts(root.find("part"))] == [0, 0, -2, -2]


def test_transposing_a_score_moves_every_part():
    root = blank()
    structure.add_part(root, "trumpet")
    transpose.transpose_score(root, transpose.INTERVALS["M2"])
    assert [found.text for found in root.iter("fifths")] == ["2", "4"]


def test_a_part_can_be_removed_but_not_the_last():
    root = blank()
    structure.add_part(root, "violin")
    structure.remove_part(root, 0)
    assert structure.part_names(root) == ["Violin"]
    with pytest.raises(EditError):
        structure.remove_part(root, 0)
    drawn(root)


def test_a_player_can_have_their_own_part():
    root = blank()
    melody(root, "C")
    structure.add_part(root, "violin")
    single = structure.only_part(root, 1)
    assert structure.part_names(single) == ["Violin"]
    assert len(single.findall("part")) == 1
    assert len(root.findall("part")) == 2
    drawn(single)


def test_chords_in_two_parts_are_listed_in_drawn_order():
    root = blank()
    ids, _ = melody(root, "C", "D", "E", "F", "G")
    structure.add_part(root, "violin")
    edit.ensure_note_ids(root)
    lower, _ = melody(root, "C", start=bars(root, 1)[0].find("note").get("id"))
    edit.add_chord(root, ids[0], "C")
    edit.add_chord(root, ids[4], "G")
    edit.add_chord(root, lower[0], "F")
    assert [item["text"] for item in edit.list_chords(root)] == ["C", "F", "G"]
    edit.set_chord(root, 1, "Bb")
    assert [item["text"] for item in edit.list_chords(root)] == ["C", "Bb", "G"]
    svg = drawn(root)
    assert svg.count('class="harm"') == 3


@pytest.mark.parametrize(("actual", "unit"), [(5, "16th"), (6, "16th"), (7, "16th")])
def test_tuplets_of_other_sizes_fill_a_quarter(actual, unit):
    root = blank()
    start = bars(root)[0].find("note").get("id")
    done = edit.make_tuplet(root, start, Fraction(1), actual=actual)
    assert done["type"] == unit
    known = next(contexts(root.find("part")))
    line = read_measure(bars(root)[0], known.divisions).lines[("1", "1")]
    inside = [event for event in line if event.in_tuplet]
    assert len(inside) == actual
    assert sum(event.length for event in inside) == 1
    assert structure.check_measures(root) == []
    assert 'class="tuplet"' in drawn(root)


def test_notes_go_into_a_quintuplet_and_it_still_adds_up():
    root = blank()
    done = edit.make_tuplet(root, bars(root)[0].find("note").get("id"), Fraction(1), actual=5)
    cursor = done["cursor"]
    for step in "CDEFG":
        written = edit.write_note(root, cursor, "16th", 0, step)
        cursor = written["cursor"]
    notes = playback(root)["notes"]
    assert [item["start"] for item in notes] == pytest.approx([0.2 * number for number in range(5)])
    assert structure.check_measures(root) == []


def test_a_quintuplet_can_be_taken_away():
    root = blank()
    done = edit.make_tuplet(root, bars(root)[0].find("note").get("id"), Fraction(1), actual=5)
    edit.toggle_tuplet(root, done["cursor"])
    assert not any(note.find("time-modification") is not None for note in root.iter("note"))
    assert structure.check_measures(root) == []


def test_a_tuplet_size_nobody_writes_is_refused():
    root = blank()
    with pytest.raises(EditError):
        edit.make_tuplet(root, bars(root)[0].find("note").get("id"), Fraction(1), actual=4)


def test_a_quintuplet_survives_copy_and_paste():
    root = blank()
    done = edit.make_tuplet(root, bars(root)[0].find("note").get("id"), Fraction(1), actual=5)
    cursor = done["cursor"]
    ids = []
    for step in "CDEFG":
        written = edit.write_note(root, cursor, "16th", 0, step)
        ids.append(written["written"])
        cursor = written["cursor"]
    copied = marks.copy_stretch(root, ids[0], ids[0])["events"]
    assert copied[0]["actual"] == 5
    target = bars(root)[1].find("note").get("id")
    marks.paste_stretch(root, target, copied)
    known = list(contexts(root.find("part")))
    line = read_measure(known[1].measure, known[1].divisions).lines[("1", "1")]
    assert {event.tuplet.actual for event in line if event.in_tuplet} == {5}
    assert structure.check_measures(root) == []


def test_tuplet_shapes():
    assert (Tuplet.of(3).actual, Tuplet.of(3).normal) == (3, 2)
    assert Tuplet.of(5).normal == Tuplet.of(6).normal == Tuplet.of(7).normal == 4


def test_verses_stack_under_a_note():
    root = blank()
    ids, _ = melody(root, "C", "D")
    edit.set_lyric(root, ids[0], "two", False, verse=2)
    edit.set_lyric(root, ids[0], "one", False, verse=1)
    assert [found.get("number") for found in edit.find_note(root, ids[0]).findall("lyric")] == ["1", "2"]
    assert drawn(root).count('class="verse"') == 2


def test_each_verse_links_its_own_syllables():
    root = blank()
    ids, _ = melody(root, "C", "D", "E")
    edit.set_lyric(root, ids[0], "won", True, verse=2)
    edit.set_lyric(root, ids[1], "der", False, verse=2)
    edit.set_lyric(root, ids[0], "hi", False, verse=1)
    first = edit.lyric_of(edit.find_note(root, ids[0]), 2)
    assert first.findtext("syllabic") == "begin"
    assert edit.lyric_of(edit.find_note(root, ids[1]), 2).findtext("syllabic") == "end"
    assert edit.lyric_of(edit.find_note(root, ids[0]), 1).findtext("syllabic") == "single"


def test_a_verse_too_many_is_refused():
    root = blank()
    ids, _ = melody(root, "C", "D")
    with pytest.raises(EditError):
        edit.set_lyric(root, ids[0], "no", False, verse=7)


def test_clearing_one_verse_leaves_the_others():
    root = blank()
    ids, _ = melody(root, "C", "D")
    edit.set_lyric(root, ids[0], "one", False, verse=1)
    edit.set_lyric(root, ids[0], "two", False, verse=2)
    edit.set_lyric(root, ids[0], "", False, verse=1)
    assert [found.findtext("text") for found in edit.find_note(root, ids[0]).findall("lyric")] == ["two"]


def test_a_line_break_starts_a_new_line():
    root = blank(measures=12)
    assert systems(drawn(root))[0] > 2
    structure.toggle_line_break(root, 2)
    assert systems(drawn(root))[0] == 2
    structure.toggle_line_break(root, 2)
    assert systems(drawn(root))[0] > 2
    assert not list(root.iter("print"))


def test_the_first_bar_cant_take_a_line_break():
    root = blank()
    with pytest.raises(EditError):
        structure.toggle_line_break(root, 0)


def test_bars_can_be_kept_to_a_number_a_line():
    root = blank(measures=12)
    structure.set_bars_per_line(root, 4)
    assert systems(drawn(root)) == [4, 4, 4]
    structure.set_bars_per_line(root, 0)
    assert systems(drawn(root)) != [4, 4, 4]
    assert root.find("identification") is None


def test_bars_a_line_counts_again_after_a_bar_is_added():
    root = blank(measures=8)
    structure.set_bars_per_line(root, 4)
    structure.insert_measure(root, 0)
    assert systems(drawn(root)) == [4, 4, 1]


def test_a_pickup_doesnt_count_as_a_bar_of_the_line():
    root = blank(measures=9)
    bars(root)[0].set("implicit", "yes")
    structure.set_bars_per_line(root, 4)
    assert systems(drawn(root)) == [5, 4]


def test_lines_read_from_a_page_are_left_out_of_the_drawing():
    root = blank(measures=12)
    bars(root)[2].insert(0, ET.Element("print", {"new-system": "yes"}))
    assert systems(drawn(root))[0] > 2
    assert bars(root)[2].find("print") is not None


def midi_notes(data):
    """Read a MIDI file back with another library, so this isn't checked against itself."""
    midi = pytest.importorskip("music21.midi")
    found = midi.MidiFile()
    found.readstr(data)
    return found


def test_midi_lengths_are_written_in_seven_bit_pieces():
    assert variable_length(0) == b"\x00"
    assert variable_length(127) == b"\x7f"
    assert variable_length(128) == b"\x81\x00"
    assert variable_length(0x3FFF) == b"\xff\x7f"


def test_a_midi_file_holds_the_notes_and_tempo():
    root = blank()
    melody(root, "C", "D", "E", "F")
    marks.set_tempo(root, next(root.iter("note")).get("id"), "120")
    found = midi_notes(midi_bytes(playback(root)))
    assert found.format == 1
    assert found.ticksPerQuarterNote == 480
    assert len(found.tracks) == 2
    events = found.tracks[1].events
    onsets = [event for event in events if event.isNoteOn()]
    assert [event.pitch for event in onsets] == [72, 74, 76, 77] or [event.pitch for event in onsets] == [60, 62, 64, 65]
    tempo = [event for event in found.tracks[0].events if event.type == "SET_TEMPO" or getattr(event.type, "name", "") == "SET_TEMPO"]
    assert int.from_bytes(tempo[0].data, "big") == 500000


def test_midi_notes_land_at_the_right_time():
    root = blank()
    melody(root, "C", "D")
    data = midi_bytes(playback(root))
    found = midi_notes(data)
    deltas = [event.time for event in found.tracks[1].events if event.type in ("NOTE_ON", "NOTE_OFF") or getattr(event.type, "name", "") in ("NOTE_ON", "NOTE_OFF") or event.isDeltaTime()]
    assert 480 in deltas


def test_midi_follows_dynamics_and_repeats():
    root = blank(measures=2)
    ids, _ = melody(root, "C", "D", "E", "F", "G", "A", "B", "C")
    marks.set_dynamic(root, ids[0], "pp")
    structure.toggle_repeat(root, 0, "backward")
    found = midi_notes(midi_bytes(playback(root)))
    onsets = [event for event in found.tracks[1].events if event.isNoteOn()]
    assert len(onsets) == 12
    assert {event.velocity for event in onsets} == {33}


def test_each_part_has_its_own_channel_and_sound():
    root = blank()
    melody(root, "C")
    structure.add_part(root, "trumpet")
    data = playback(root)
    assert data["programs"] == [1, 57]
    found = midi_notes(midi_bytes(data))
    assert len(found.tracks) == 3
    programs = [event for event in found.tracks[2].events if event.type == "PROGRAM_CHANGE" or getattr(event.type, "name", "") == "PROGRAM_CHANGE"]
    assert programs[0].data == 56 or programs[0].patch == 56


def test_notes_a_midi_file_cannot_hold_are_left_out_of_it():
    """A note number has to fit in seven bits. One past that would corrupt the file, and one below 0 can't be written."""
    notes = [{"start": start, "length": 1, "midi": number, "part": 0} for start, number in enumerate((60, 131, -9))]
    found = midi_notes(midi_bytes({"tempos": [], "programs": [1], "chords": [], "notes": notes}))
    assert [event.pitch for event in found.tracks[1].events if event.isNoteOn()] == [60]


def before_giving_up(action, seconds=2):
    """Run something that once never finished, so it fails its own test and doesn't hold up the rest."""

    def give_up(*_):
        raise TimeoutError("This was still running.")

    signal.signal(signal.SIGALRM, give_up)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return action()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, signal.SIG_DFL)


@pytest.mark.parametrize("opening", [
    "<backup><duration>2</duration></backup>",
    '<direction><direction-type><words>Slow</words></direction-type><offset>-1</offset><sound tempo="90"/></direction>',
])
def test_music_a_file_puts_before_its_own_start_still_gives_a_midi_file(opening):
    """A file from another program can step back past the start of its first bar, or hang a tempo there."""
    root = parsed(opening + note() + note("D"))
    found = midi_notes(before_giving_up(lambda: midi_bytes(playback(root))))
    assert [event.pitch for event in found.tracks[1].events if event.isNoteOn()] == [60, 62]


def events_named(track, name):
    return [event for event in track.events if event.type == name or getattr(event.type, "name", "") == name]


def test_a_tempo_slower_than_a_midi_file_can_hold_is_written_as_its_slowest():
    """An eighth note at 10 is 5 quarter notes a minute, and a fermata holds its note at half of that."""
    root = blank()
    ids, _ = melody(root, "C", "D")
    marks.set_tempo(root, ids[0], "10", "eighth")
    marks.toggle_mark(root, [ids[0]], "fermata")
    found = midi_notes(midi_bytes(playback(root)))
    assert [int.from_bytes(event.data, "big") for event in events_named(found.tracks[0], "SET_TEMPO")] == [0xFFFFFF, 12_000_000]


def test_a_tempo_of_nothing_is_left_out_of_a_midi_file():
    tempos = [{"start": 0.0, "bpm": 0.0}, {"start": 1.0, "bpm": 120.0}]
    found = midi_notes(midi_bytes({"tempos": tempos, "programs": [1], "chords": [], "notes": []}))
    assert [int.from_bytes(event.data, "big") for event in events_named(found.tracks[0], "SET_TEMPO")] == [500000]


@pytest.mark.parametrize("program", [0, 129, 300])
def test_a_sound_a_midi_file_has_no_number_for_plays_as_the_usual_one(program):
    """Sounds are numbered 1 to 128. Some programs write them from 0, and a wrong one mustn't spoil the file."""
    root = parsed(note() + note("D"))
    root.find("part-list/score-part").append(ET.fromstring(f"<midi-instrument id='i'><midi-program>{program}</midi-program></midi-instrument>"))
    found = midi_notes(midi_bytes(playback(root)))
    assert [event.data for event in events_named(found.tracks[1], "PROGRAM_CHANGE")] == [0]
    assert [event.pitch for event in found.tracks[1].events if event.isNoteOn()] == [60, 62]


def test_drums_channel_is_skipped():
    assert 9 not in [channel_of(part) for part in range(30)]
    assert all(0 <= channel_of(part) <= 15 for part in range(30))


def test_a_score_with_chords_has_a_track_for_them():
    root = blank()
    ids, _ = melody(root, "C")
    edit.add_chord(root, ids[0], "Am7")
    assert len(midi_notes(midi_bytes(playback(root))).tracks) == 3


def packed(text: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr("score.xml", text)
    return buffer.getvalue()


def test_a_plain_file_opens():
    root = read_score_file(to_text(blank()).encode(), ".musicxml")
    assert root.tag == "score-partwise"


def test_a_compressed_file_opens():
    root = read_score_file(packed(to_text(blank()).encode()), ".mxl")
    assert len(root.find("part").findall("measure")) == 4


@pytest.mark.parametrize(("data", "suffix"), [
    (b"<nonsense", ".musicxml"),
    (b"not a zip", ".mxl"),
    (b"<html><body/></html>", ".xml"),
    (b'<score-timewise version="3.1"><measure/></score-timewise>', ".musicxml"),
    (b"<score-partwise/>", ".xml"),
])
def test_files_that_cant_be_edited_are_refused(data, suffix):
    with pytest.raises(ValueError):
        read_score_file(data, suffix)


def test_a_compressed_file_that_unpacks_too_large_is_refused(monkeypatch):
    monkeypatch.setattr("pipeline.musicxml.MOST_UNPACKED_BYTES", 10)
    with pytest.raises(ValueError):
        read_score_file(packed(to_text(blank()).encode()), ".mxl")


def test_a_compressed_file_whose_list_of_contents_unpacks_too_large_is_refused(monkeypatch):
    """The list is read before the score it names, so it can be the huge file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("META-INF/container.xml", f'<container><rootfiles><rootfile full-path="score.xml"/></rootfiles><!--{" " * 5000}--></container>')
        archive.writestr("score.xml", "<score-partwise><part/></score-partwise>")
    monkeypatch.setattr("pipeline.musicxml.MOST_UNPACKED_BYTES", 1000)
    with pytest.raises(ValueError):
        read_score_file(buffer.getvalue(), ".mxl")


def test_a_compressed_file_is_measured_by_what_unpacks_not_by_what_it_claims(monkeypatch):
    stated = len(b"<score-partwise><part/></score-partwise>")
    honest = packed(b"<score-partwise><part/></score-partwise>" + b" " * 5000)
    lying = honest.replace((stated + 5000).to_bytes(4, "little"), stated.to_bytes(4, "little"))
    assert lying != honest
    read = []
    original = zipfile.ZipExtFile.read
    monkeypatch.setattr(zipfile.ZipExtFile, "read", lambda self, n=-1: read.append(n) or original(self, n))
    monkeypatch.setattr("pipeline.musicxml.MOST_UNPACKED_BYTES", 1000)
    with pytest.raises(ValueError):
        read_score_file(lying, ".mxl")
    assert read and all(0 < n <= 1001 for n in read)  # Never asked to unpack more than the limit.


def test_a_real_chorale_comes_through_note_for_note():
    """A score written by other software has to survive being opened and saved here."""
    music21 = pytest.importorskip("music21")
    path = music21.corpus.getWork("bwv66.6")
    original = music21.converter.parse(path)
    root = read_score_file(path.read_bytes(), ".mxl")
    rhythm.fit_divisions(root)
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)
    structure.ensure_measure_ids(root)
    saved = music21.converter.parse(to_text(root), format="musicxml")

    def notes_of(score):
        return [(float(note.getOffsetInHierarchy(score)), note.pitch.midi, float(note.quarterLength)) for note in score.recurse().notes if note.isNote]

    assert sorted(notes_of(saved)) == sorted(notes_of(original))
    assert drawn(root).count('class="note"') == len(notes_of(original))
    # A tied note sounds once, and this chorale has no repeats, so each tie takes one away.
    tied = sum(tie.get("type") == "stop" for tie in root.iter("tie"))
    assert tied > 0
    assert len(playback(root)["notes"]) == len(notes_of(original)) - tied


def test_a_real_chorale_can_be_edited():
    music21 = pytest.importorskip("music21")
    root = read_score_file(music21.corpus.getWork("bwv66.6").read_bytes(), ".mxl")
    rhythm.fit_divisions(root)
    edit.ensure_note_ids(root)
    before = len(structure.check_measures(root))
    first = next(note for note in root.iter("note") if note.find("pitch") is not None)
    edit.change_length(root, first.get("id"), "half", 0)
    marks.toggle_mark(root, [first.get("id")], "fermata")
    transpose.transpose_score(root, transpose.INTERVALS["M2"])
    structure.add_part(root, "flute")
    assert len(structure.check_measures(root)) <= before + 1
    drawn(root)


def staccato_tune():
    root = blank()
    ids, _ = melody(root, "C", "D", "E", "F")
    return root, ids


def test_marks_come_along_when_copied():
    root, ids = staccato_tune()
    marks.toggle_mark(root, [ids[1]], "accent")
    marks.set_dynamic(root, ids[0], "pp")
    copied = marks.copy_stretch(root, ids[0], ids[2])["events"]
    target = bars(root)[1].find("note").get("id")
    done = marks.paste_stretch(root, target, copied)
    pasted = [note for note in bars(root)[1].findall("note") if note.find("pitch") is not None]
    assert marks.has_mark(pasted[1], "accent")
    assert not marks.has_mark(pasted[0], "accent")
    assert bars(root)[1].find("direction/direction-type/dynamics/pp") is not None
    assert "placed" not in done


def test_a_slur_comes_along_when_copied_whole():
    root, ids = staccato_tune()
    marks.toggle_slur(root, ids[0], ids[2])
    copied = marks.copy_stretch(root, ids[0], ids[2])["events"]
    marks.paste_stretch(root, bars(root)[1].find("note").get("id"), copied)
    assert len([slur for slur in bars(root)[1].iter("slur")]) == 2


def test_half_a_slur_is_left_behind():
    root, ids = staccato_tune()
    marks.toggle_slur(root, ids[0], ids[2])
    copied = marks.copy_stretch(root, ids[1], ids[2])["events"]
    marks.paste_stretch(root, bars(root)[1].find("note").get("id"), copied)
    assert not list(bars(root)[1].iter("slur"))


def test_overlapping_slurs_paste_only_the_whole_one():
    root, ids = staccato_tune()
    marks.toggle_slur(root, ids[0], ids[2])
    marks.toggle_slur(root, ids[1], ids[3])
    copied = marks.copy_stretch(root, ids[1], ids[3])["events"]
    assert [item["slur"] for item in copied] == [[2], [], []]
    marks.paste_stretch(root, bars(root)[1].find("note").get("id"), copied)
    assert [slur.get("type") for slur in bars(root)[1].iter("slur")] == ["start", "stop"]


def test_a_clipboard_from_before_marks_still_pastes():
    root, ids = staccato_tune()
    copied = marks.copy_stretch(root, ids[0], ids[1])["events"]
    for item in copied:
        for name in ("marks", "dynamic", "slur", "actual"):
            item.pop(name)
    marks.paste_stretch(root, bars(root)[1].find("note").get("id"), copied)
    assert len([note for note in bars(root)[1].findall("note") if note.find("pitch") is not None]) == 2


def test_copying_says_where_a_repeat_would_go():
    root, ids = staccato_tune()
    assert marks.copy_stretch(root, ids[0], ids[1])["next"] == ids[2]


@pytest.mark.parametrize(("number", "fifths", "expected"), [
    (61, 0, ("C", 1, 4)),
    (61, -3, ("D", -1, 4)),
    (60, 0, ("C", 0, 4)),
    (70, -1, ("B", -1, 4)),
    (72, 0, ("C", 0, 5)),
])
def test_a_midi_key_is_spelled_by_the_key(number, fifths, expected):
    assert edit.pitch_of_key(number, fifths) == expected


def test_a_midi_key_writes_that_exact_note():
    root = blank()
    done = edit.write_note(root, bars(root)[0].find("note").get("id"), "quarter", 0, None, midi=48)
    note = edit.find_note(root, done["written"])
    assert (note.findtext("pitch/step"), note.findtext("pitch/octave")) == ("C", "3")
    other = edit.add_pitch(root, done["written"], "", midi=52)
    assert edit.find_note(root, other).findtext("pitch/step") == "E"


def test_a_midi_key_can_only_be_in_a_chord_once():
    root = blank()
    done = edit.write_note(root, bars(root)[0].find("note").get("id"), "quarter", 0, None, midi=60)
    with pytest.raises(EditError):
        edit.add_pitch(root, done["written"], "", midi=60)


def starts(root):
    return [note["start"] for note in playback(root)["notes"]]


def test_a_pickup_shortens_the_first_bar():
    root = blank()
    structure.set_pickup(root, Fraction(1))
    first = next(contexts(root.find("part")))
    assert first.is_pickup
    assert bars(root)[0].get("number") == "0" and bars(root)[1].get("number") == "1"
    assert sum(int(note.findtext("duration")) for note in bars(root)[0].findall("note")) == first.divisions
    assert structure.check_measures(root) == []
    drawn(root)


def test_notes_flow_out_of_a_pickup_into_the_next_bar():
    root = blank()
    structure.set_pickup(root, Fraction(1))
    edit.ensure_note_ids(root)
    written, _ = melody(root, "G", "C", "D")
    assert [len([note for note in bar.findall("note") if note.find("pitch") is not None]) for bar in bars(root)[:2]] == [1, 2]
    assert starts(root) == [0.0, 1.0, 2.0]
    assert structure.check_measures(root) == []


def test_a_pickup_can_be_taken_away_again():
    root = blank()
    structure.set_pickup(root, Fraction(1, 2))
    edit.ensure_note_ids(root)
    melody(root, "G")
    structure.set_pickup(root, Fraction(0))
    edit.ensure_note_ids(root)
    assert not next(contexts(root.find("part"))).is_pickup
    assert bars(root)[0].get("number") == "1"
    assert structure.check_measures(root) == []
    assert [note.findtext("pitch/step") for note in bars(root)[0].findall("note") if note.find("pitch") is not None] == ["G"]


def test_a_pickup_never_loses_notes_or_marks():
    root = blank()
    ids, _ = melody(root, "C", "D")
    with pytest.raises(EditError, match="notes past"):
        structure.set_pickup(root, Fraction(1))
    root = blank()
    ids, _ = melody(root, "C", "D")
    marks.set_dynamic(root, ids[1], "pp")
    edit.delete_note(root, ids[1])
    with pytest.raises(EditError, match="marks past"):
        structure.set_pickup(root, Fraction(1))


def test_a_pickup_as_long_as_a_bar_is_refused():
    with pytest.raises(EditError):
        structure.set_pickup(blank(), Fraction(4))


def test_a_pickup_reaches_every_part_and_new_ones():
    root = blank()
    structure.add_part(root, "violin")
    structure.set_pickup(root, Fraction(1))
    structure.add_part(root, "flute")
    for part in root.findall("part"):
        first = next(contexts(part))
        assert first.is_pickup
        assert sum(int(note.findtext("duration")) for note in first.measure.findall("note")) == first.divisions
    assert structure.check_measures(root) == []
    drawn(root)


def test_every_voice_of_a_staff_shortens_with_a_pickup():
    root = blank(clef="piano")
    structure.set_pickup(root, Fraction(2))
    known = next(contexts(root.find("part")))
    timeline = read_measure(known.measure, known.divisions)
    assert {rhythm.line_end(line) for line in timeline.lines.values()} == {Fraction(2)}
    assert structure.check_measures(root) == []
    drawn(root)


def shorten(root, index, quarters):
    """Cut a bar's rest down, as a bar printed in two halves arrives from another program."""
    context = list(contexts(root.find("part")))[index]
    rest = context.measure.find("note")
    rest.find("duration").text = str(quarters * context.divisions)
    rest.find("rest").attrib.pop("measure", None)


def test_a_bar_split_in_two_isnt_a_mistake():
    root = blank()
    shorten(root, 1, 3)
    shorten(root, 2, 1)
    assert structure.check_measures(root) == []


def test_a_short_bar_with_no_other_half_is_still_flagged():
    root = blank()
    shorten(root, 1, 3)
    assert [item["problem"] for item in structure.check_measures(root)] == ["short"]


def test_two_short_bars_that_dont_add_up_are_both_flagged():
    root = blank()
    shorten(root, 1, 3)
    shorten(root, 2, 3)
    assert [item["measure"] for item in structure.check_measures(root)] == [1, 2]
