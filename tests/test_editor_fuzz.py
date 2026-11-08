"""Random edits, checked against a simple model and against the rules every score must keep.

Unit tests only cover cases someone thought of. These walk through thousands of random edits,
and the seed is fixed so any failure can be replayed.

The rules matter more than they look. Bars that add up isn't enough on its own: a broken triplet
adds up perfectly, so triplets have a rule of their own.
"""

import copy
import random
import re
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline import edit, marks, playback, rhythm, structure, transpose
from pipeline.engrave import render_pages
from pipeline.midi_file import midi_bytes
from pipeline.musicxml import to_text
from pipeline.rhythm import EditError, contexts, read_measure

PRIVATE_SYMBOL = re.compile("[\ue000-\uf8ff]")
TRUTH = sorted((Path(__file__).parent.parent / "data" / "truth").glob("*.musicxml"))
SLOTS_PER_BAR = 16  # A sixteenth is the smallest length the model below uses.
SLOT = Fraction(1, 4)
LENGTHS = {"16th": 1, "eighth": 2, "quarter": 4, "half": 8, "whole": 16}
BIGGEST_FIRST = [16, 12, 8, 6, 4, 3, 2, 1]  # Plain and dotted lengths, in slots.


def check_rules(root, already_loose=frozenset()):
    """Things that must hold after any edit, on any score.

    A real file can arrive with a tie that leads nowhere, as Joplin's last bar does. That isn't the
    editor's doing, so only a new one counts.
    """
    seen = set()
    for note in root.iter("note"):
        assert note.get("id") and note.get("id") not in seen
        seen.add(note.get("id"))
        names = [child.tag for child in note]
        ranks = [rhythm.NOTE_ORDER.index(name) for name in names]
        assert ranks == sorted(ranks), names
    assert loose_ties(root) <= already_loose, f"a tie lost its other end after {root.get('last-edit')}"
    check_triplets(root)
    check_pairs(root)
    check_voices(root)


def loose_ties(root):
    """List the notes whose tie leads to nothing: the same pitch has to sound as the note ends, in any voice."""
    loose = set()
    for part in root.findall("part"):
        known = [read_measure(context.measure, context.divisions) for context in contexts(part)]
        for number, timeline in enumerate(known):
            for line in timeline.lines.values():
                last = max(event.end for event in line)
                for event in line:
                    if event.end < last:
                        place, moment = timeline.lines.values(), event.end
                    else:
                        place, moment = (known[number + 1].lines.values() if number + 1 < len(known) else []), Fraction(0)
                    # The same voice's next note also counts, for pages whose own rhythm doesn't add up.
                    sounding = [other for candidates in place for found in candidates if found.onset == moment or (candidates is line and found.onset > event.onset) for other in found.notes]
                    for note in event.notes:
                        if rhythm.is_tied(note, "start") and not any(rhythm.same_pitch(note, other) and rhythm.is_tied(other, "stop") for other in sounding):
                            loose.add(note.get("id"))
    return loose


def check_pairs(root):
    """A slur or hairpin has two ends, and a lost end draws nothing or runs off the page."""
    for part in root.findall("part"):
        for tag, opening in (("slur", ("start",)), ("wedge", ("crescendo", "diminuendo"))):
            open_ends: dict[str, int] = {}
            for measure in part.findall("measure"):
                for element in measure.iter(tag):
                    number = element.get("number", "1")
                    if element.get("type") in opening:
                        assert number not in open_ends, f"{tag} {number} started twice"
                        open_ends[number] = 1
                    elif element.get("type") == "stop":
                        assert open_ends.pop(number, None), f"{tag} {number} stopped without a start"
            assert not open_ends, f"{tag} never stopped: {sorted(open_ends)}"


def check_voices(root):
    """An extra voice of nothing but rests is fine while someone's writing in it, but has to be removable.

    Which voice comes first depends on the staff: a piano's lower staff usually starts at 5.
    """
    edited = root.get("last-edit")
    for number, part in enumerate(root.findall("part")):
        for context in contexts(part):
            timeline = read_measure(context.measure, context.divisions)
            for key in timeline.lines:
                on_staff = sorted((other for other in timeline.lines if other[0] == key[0]), key=lambda other: int(other[1]))
                if key != on_staff[0] and all(event.is_rest for event in timeline.lines[key]):
                    assert removes_cleanly(root, number, context.index, key), f"voice {key[1]} of bar {context.index} is stuck after {edited}"


def removes_cleanly(root, part_number, index, key):
    """Delete a voice's rests one by one, as a person would, and see that the voice goes."""
    trial = copy.deepcopy(root)
    for _ in range(64):
        measure = trial.findall("part")[part_number].findall("measure")[index]
        context = next(item for item in contexts(trial.findall("part")[part_number]) if item.measure is measure)
        line = read_measure(measure, context.divisions).lines.get(key)
        if not line:
            return True
        event = line[0]
        if event.in_tuplet:
            edit.remove_tuplet(trial, event.notes[0].get("id"))
        else:
            edit.delete_note(trial, event.notes[0].get("id"))
    return False


def check_outputs(root):
    """Every way out of the editor has to cope with whatever was made."""
    marks.ensure_mark_ids(root)
    pages = render_pages(to_text(root))
    assert pages
    assert not PRIVATE_SYMBOL.search("".join(pages)), "a music symbol would print as an empty box"
    data = playback.playback(root)
    assert data["total"] >= 0
    assert midi_bytes(data)[:4] == b"MThd"
    listed = marks.list_marks(root)
    assert all(item["id"] and item["bar"] for item in listed), listed


def check_triplets(root):
    """Notes in a triplet have to fill whole triplets, or part of one has been lost."""
    for part in root.findall("part"):
        for context in contexts(part):
            for line in read_measure(context.measure, context.divisions).lines.values():
                for event in line:
                    if event.in_tuplet:
                        run = rhythm.tuplet_run(line, event)
                        whole = run[-1].end - run[0].onset
                        assert whole % rhythm.tuplet_span(run) == 0, (float(whole), float(rhythm.tuplet_span(run)))


class BarModel:
    """A deliberately plain model: a score as a row of sixteenth slots, sharing no code with the editor.

    One written note can be several pieces, cut at the barline and then into lengths that can be
    written. A piece that's partly covered goes altogether, as in the app.
    """

    def __init__(self, bars):
        self.slots = [None] * (bars * SLOTS_PER_BAR)
        self.pieces = 0

    def write(self, start, size):
        """Write a note, and return the piece numbers, in order."""
        if start + size > len(self.slots):
            spare = (start + size - len(self.slots) - 1) // SLOTS_PER_BAR + 1
            self.slots += [None] * (spare * SLOTS_PER_BAR)
        made = []
        while size:
            room = SLOTS_PER_BAR - start % SLOTS_PER_BAR
            piece = next(value for value in BIGGEST_FIRST if value <= min(size, room))
            self.pieces += 1
            covered = {value for value in self.slots[start : start + piece] if value is not None}
            self.slots = [None if value in covered else value for value in self.slots]
            self.slots[start : start + piece] = [self.pieces] * piece
            made.append(self.pieces)
            start += piece
            size -= piece
        return made


def read_pieces(root):
    """Read the score back as slots, with each note given the slots it fills."""
    found = []
    for context in contexts(root.find("part")):
        bar = [None] * SLOTS_PER_BAR
        for event in read_measure(context.measure, context.divisions).events():
            assert (event.onset / SLOT).denominator == 1
            if not event.is_rest:
                start = int(event.onset / SLOT)
                bar[start : start + int(event.length / SLOT)] = [event.notes[0]] * int(event.length / SLOT)
        found.extend(bar)
    return found


@pytest.mark.parametrize("seed", range(40))
def test_random_writing_matches_the_slot_model(seed):
    rng = random.Random(seed)
    root = structure.blank_score(measures=2)
    edit.ensure_note_ids(root)
    model = BarModel(2)
    for _ in range(40):
        events = [(context.index, event) for context in contexts(root.find("part")) for event in read_measure(context.measure, context.divisions).events()]
        bar, event = rng.choice(events)
        start = bar * SLOTS_PER_BAR + int(event.onset / SLOT)
        name, size = rng.choice(list(LENGTHS.items()))
        made = model.write(start, size)
        edit.write_note(root, event.notes[0].get("id"), name, 0, rng.choice("CDEFGAB"))
        read = read_pieces(root)
        assert len(read) == len(model.slots)
        assert [slot is None for slot in model.slots] == [note is None for note in read], "a note is in the wrong place"
        pairs = {(number, id(note)) for number, note in zip(model.slots, read) if number is not None}
        assert len({number for number, _ in pairs}) == len(pairs) == len({note for _, note in pairs}), "pieces don't match notes"
        for first, second in zip(made, made[1:]):
            before = read[model.slots.index(first)]
            after = read[model.slots.index(second)]
            assert rhythm.is_tied(before, "start") and rhythm.is_tied(after, "stop") and rhythm.same_pitch(before, after)
        assert structure.check_measures(root) == []
        check_rules(root)


def random_edit(root, rng, overfill=True):
    """Make one random edit. Plenty will be refused, which is itself part of the test.

    Opening a gap is the one edit allowed to push a bar past its length, for the bar check to flag.
    """
    notes = [note.get("id") for note in root.iter("note") if note.find("grace") is None]
    target = rng.choice(notes)
    name = rng.choice(["16th", "eighth", "quarter", "half", "whole"])
    bars = len(root.find("part").findall("measure"))
    other = rng.choice(notes)

    def copy_and_paste():
        clip = edit.copy_range(root, target, other)["events"]
        edit.paste(root, rng.choice(notes), clip)

    def voice_edit():
        edit.write_note(root, target, name, 0, rng.choice("CDEFGAB"), voice=rng.randint(1, 4))

    def move_voice():
        edit.move_to_voice(root, [target, other], rng.randint(1, 4))

    def remove_a_mark():
        listed = marks.list_marks(root)
        if listed:
            marks.remove_placed(root, rng.choice(listed)["id"])

    def relabel_a_mark():
        listed = [item for item in marks.list_marks(root) if item["text"]]
        if listed:
            marks.set_label(root, rng.choice(listed)["id"], rng.choice(["mf", "Allegro 120", "dolce", "B"]))

    def grace_edit():
        graces = [note.get("id") for note in root.iter("note") if note.find("grace") is not None]
        if graces:
            rng.choice([lambda: edit.move_note(root, rng.choice(graces), 1), lambda: edit.delete_note(root, rng.choice(graces))])()

    def copy_marks():
        clip = marks.copy_stretch(root, target, other)["events"]
        marks.paste_stretch(root, rng.choice(notes), clip)

    actions = {
        "mark": lambda: marks.toggle_mark(root, [target], rng.choice(list(marks.NOTE_MARKS))),
        "marks on a stretch": lambda: marks.toggle_mark(root, edit.range_ids(root, target, other)["ids"], rng.choice(list(marks.NOTE_MARKS))),
        "slur": lambda: marks.toggle_slur(root, target, other),
        "hairpin": lambda: marks.toggle_hairpin(root, target, other, rng.choice(["crescendo", "diminuendo"])),
        "dynamic": lambda: marks.set_dynamic(root, target, rng.choice(list(marks.DYNAMICS) + list(marks.ACCENTS))),
        "tempo": lambda: marks.set_tempo(root, target, rng.choice(["Allegro 132", "66", "Andante"])),
        "text": lambda: marks.add_text(root, target, "solo", rng.choice(["staff", "expression"])),
        "rehearsal": lambda: marks.add_rehearsal(root, target),
        "swing": lambda: marks.set_swing(root, target, rng.random() < 0.5),
        "jump": lambda: marks.set_jump(root, target, rng.choice(list(marks.JUMPS))),
        "grace": lambda: marks.add_grace(root, target, rng.random() < 0.5),
        "grace edit": grace_edit,
        "remove a mark": remove_a_mark,
        "relabel a mark": relabel_a_mark,
        "copy marks": copy_marks,
        "voice": voice_edit,
        "move voice": move_voice,
        "respell": lambda: edit.respell_note(root, target),
        "flip": lambda: edit.flip_stem(root, target),
        "tuplet": lambda: edit.make_tuplet(root, target, edit.length_of(name, 0), rng.random() < 0.5, rng.choice([3, 5, 6, 7])),
        "verse": lambda: edit.set_lyric(root, target, rng.choice(["la", ""]), rng.random() < 0.5, rng.randint(1, 3)),
        "clef": lambda: structure.set_clef(root, rng.randrange(bars), 0, "1", rng.choice(list(structure.CLEF_SHAPES))),
        "pickup": lambda: structure.set_pickup(root, rng.choice([Fraction(0), Fraction(1, 2), Fraction(1), Fraction(2)])),
        "line break": lambda: structure.toggle_line_break(root, rng.randrange(bars)),
        "bars a line": lambda: structure.set_bars_per_line(root, rng.choice([0, 2, 4])),
        "add part": lambda: structure.add_part(root, rng.choice(list(structure.INSTRUMENTS))),
        "remove part": lambda: structure.remove_part(root, rng.randrange(len(root.findall("part")))),
        "transpose": lambda: transpose.transpose_score(root, rng.choice(list(transpose.INTERVALS.values()))),
        "transpose stretch": lambda: transpose.transpose_stretch(root, target, other, rng.choice(list(transpose.INTERVALS.values()))),
        "write on a line": lambda: edit.write_note(root, target, name, rng.choice([0, 1]), None, rng.randint(-4, 12)),
        "stack on a line": lambda: edit.add_pitch(root, target, "", rng.randint(-4, 12)),
        "make triplet": lambda: edit.make_tuplet(root, target, edit.length_of(name, 0), rng.random() < 0.5),
        "toggle triplet": lambda: edit.toggle_tuplet(root, target),
        "remove triplet": lambda: edit.remove_tuplet(root, target),
        "insert": lambda: edit.insert_rest(root, target, name, 0) if overfill else None,
        "lyric": lambda: edit.set_lyric(root, target, rng.choice(["la", "", "doo"]), rng.random() < 0.5),
        "range": lambda: edit.range_ids(root, target, other),
        "paste": copy_and_paste,
        "repeat": lambda: structure.toggle_repeat(root, rng.randrange(bars), rng.choice(["forward", "backward"])),
        "ending": lambda: structure.set_ending(root, rng.randrange(bars), rng.randrange(bars), rng.choice([0, 1, 2])),
        "length": lambda: edit.change_length(root, target, name, rng.choice([0, 0, 1])),
        "write": lambda: edit.write_note(root, target, name, 0, rng.choice("CDEFGAB")),
        "write rest": lambda: edit.write_note(root, target, name, 0, None),
        "stack": lambda: edit.add_pitch(root, target, rng.choice("CDEFGAB")),
        "tie": lambda: edit.toggle_tie(root, target),
        "dot": lambda: edit.toggle_dot(root, target),
        "close": lambda: edit.close_gap(root, target),
        "move": lambda: edit.move_note(root, target, rng.choice([-7, -1, 1, 7])),
        "accidental": lambda: edit.set_accidental(root, target, rng.choice([-1, 0, 1])),
        "delete": lambda: edit.delete_note(root, target),
        "add bar": lambda: structure.insert_measure(root, rng.randrange(bars + 1)),
        "delete bar": lambda: structure.delete_measure(root, rng.randrange(bars)),
        "key": lambda: structure.set_key(root, rng.randrange(bars), rng.randint(-7, 7)),
    }
    chosen = rng.choice(list(actions))
    root.set("last-edit", f"{chosen} on {target}")  # So a failure says what caused it.
    actions[chosen]()
    edit.ensure_note_ids(root)
    marks.ensure_mark_ids(root)


def attempt(root, rng, overfill=True):
    """Try an edit on a copy, as the server does, so a refused edit leaves nothing behind."""
    trial = copy.deepcopy(root)
    try:
        random_edit(trial, rng, overfill)
    except EditError:
        return root, False
    try:
        to_text(trial)
    except TypeError as error:
        raise AssertionError(f"{trial.get('last-edit')} stored something that can't be saved: {error}") from error
    return trial, True


@pytest.mark.parametrize("seed", range(20))
def test_random_edits_on_a_blank_score_keep_every_bar_full(seed):
    rng = random.Random(seed)
    root = structure.blank_score(measures=4, fifths=rng.randint(-3, 3))
    edit.ensure_note_ids(root)
    for step in range(120):
        root, _ = attempt(root, rng, overfill=False)
        check_rules(root)
        assert structure.check_measures(root) == [], f"a bar stopped adding up after {root.get('last-edit')}"
        if step % 30 == 0:
            check_outputs(root)
    check_outputs(root)


@pytest.mark.parametrize("path", TRUTH, ids=lambda path: path.stem)
def test_random_edits_on_real_scores_stay_valid(path):
    rng = random.Random(path.stem)
    root = ET.parse(path).getroot()
    edit.ensure_note_ids(root)
    structure.ensure_measure_ids(root)
    arrived_loose = loose_ties(root)
    done = 0
    for _ in range(60):
        root, worked = attempt(root, rng)
        done += worked
        check_rules(root, arrived_loose)
    assert done > 10
    check_outputs(root)


def test_a_lost_tie_end_fails_the_tie_rule():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    first = edit.write_note(root, next(root.iter("note")).get("id"), "quarter", 0, "C")
    edit.write_note(root, first["cursor"], "quarter", 0, "C")
    edit.toggle_tie(root, first["written"])
    assert not loose_ties(root)
    rhythm.set_tie(list(root.iter("note"))[1], "stop", False)
    assert loose_ties(root) == {first["written"]}


def test_a_lost_slur_end_fails_the_pairing_rule():
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    first = edit.write_note(root, next(root.iter("note")).get("id"), "quarter", 0, "C")
    second = edit.write_note(root, first["cursor"], "quarter", 0, "D")
    marks.toggle_slur(root, first["written"], second["written"])
    check_pairs(root)
    notations = list(root.iter("note"))[1].find("notations")
    notations.remove(notations.find("slur"))
    with pytest.raises(AssertionError):
        check_pairs(root)


def test_a_broken_triplet_fails_the_triplet_rule():
    """The rule has to be able to fail, or it proves nothing."""
    root = structure.blank_score(measures=1)
    edit.ensure_note_ids(root)
    cursor = edit.make_tuplet(root, next(root.iter("note")).get("id"), Fraction(1))["cursor"]
    context = next(contexts(root.find("part")))
    notes = context.measure.findall("note")
    for note in notes[1:3]:
        context.measure.remove(note)
    with pytest.raises(AssertionError):
        check_triplets(root)
    assert cursor
