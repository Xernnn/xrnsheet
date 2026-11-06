"""Random edits, checked against a simple model and against the rules every score must keep.

Unit tests only cover cases someone thought of. These walk through thousands of random edits,
and the seed is fixed so any failure can be replayed.

The rules matter more than they look. Bars that add up isn't enough on its own: a broken triplet
adds up perfectly, so triplets have a rule of their own.
"""

import copy
import random
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest

from pipeline import edit, playback, rhythm, structure
from pipeline.engrave import render_pages
from pipeline.musicxml import to_text
from pipeline.rhythm import EditError, contexts, read_measure

TRUTH = sorted((Path(__file__).parent.parent / "data" / "truth").glob("*.musicxml"))
SLOTS_PER_BAR = 16  # A sixteenth is the smallest length the model below uses.
SLOT = Fraction(1, 4)
LENGTHS = {"16th": 1, "eighth": 2, "quarter": 4, "half": 8, "whole": 16}
BIGGEST_FIRST = [16, 12, 8, 6, 4, 3, 2, 1]  # Plain and dotted lengths, in slots.


def check_rules(root):
    """Things that must hold after any edit, on any score."""
    seen = set()
    for note in root.iter("note"):
        assert note.get("id") and note.get("id") not in seen
        seen.add(note.get("id"))
        names = [child.tag for child in note]
        ranks = [rhythm.NOTE_ORDER.index(name) for name in names]
        assert ranks == sorted(ranks), names
    for part in root.findall("part"):
        known = list(contexts(part))
        lines: dict = {}
        for context in known:
            timeline = read_measure(context.measure, context.divisions)
            for key, line in timeline.lines.items():
                lines.setdefault(key, []).extend(sorted(line, key=lambda event: event.onset))
        for line in lines.values():
            for before, after in zip(line, line[1:]):
                for note in before.notes:
                    if rhythm.is_tied(note, "start"):
                        assert any(rhythm.same_pitch(note, other) and rhythm.is_tied(other, "stop") for other in after.notes)
    check_triplets(root)


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

    actions = {
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


def attempt(root, rng, overfill=True):
    """Try an edit on a copy, as the server does, so a refused edit leaves nothing behind."""
    trial = copy.deepcopy(root)
    try:
        random_edit(trial, rng, overfill)
    except EditError:
        return root, False
    return trial, True


@pytest.mark.parametrize("seed", range(20))
def test_random_edits_on_a_blank_score_keep_every_bar_full(seed):
    rng = random.Random(seed)
    root = structure.blank_score(measures=4, fifths=rng.randint(-3, 3))
    edit.ensure_note_ids(root)
    for _ in range(120):
        root, _ = attempt(root, rng, overfill=False)
        check_rules(root)
        assert structure.check_measures(root) == [], f"a bar stopped adding up after {root.get('last-edit')}"
    assert render_pages(to_text(root))


@pytest.mark.parametrize("path", TRUTH, ids=lambda path: path.stem)
def test_random_edits_on_real_scores_stay_valid(path):
    rng = random.Random(path.stem)
    root = ET.parse(path).getroot()
    edit.ensure_note_ids(root)
    structure.ensure_measure_ids(root)
    done = 0
    for _ in range(60):
        root, worked = attempt(root, rng)
        done += worked
        check_rules(root)
    assert done > 10
    assert render_pages(to_text(root))
    assert playback.playback(root)["total"] > 0


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
