"""Edit a score: fix what recognition got wrong, or write new music.

Pitch edits change one note in place. Rhythm edits go through a Timeline (see rhythm.py) and
finish in commit, so no edit can skip the accidentals, tie repair or bar top-up.

A cursor is a note's id, or "after:<id>" once writing has reached the end of the score. The bar
after that doesn't exist until something is written in it, so finishing a piece leaves no empty
bar behind.
"""

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction

from chords.vocab import parse_chord
from pipeline.musicxml import chord_from_harmony, harmony_element
from pipeline.rhythm import (
    LENGTHS,
    STEPS,
    Context,
    EditError,
    Event,
    Timeline,
    Tuplet,
    alteration_in_key,
    contexts,
    diatonic,
    fit_divisions,
    is_tied,
    line_end,
    make_note,
    note_lengths,
    offset_of,
    on_plain_grid,
    overwrite,
    overwrite_in_tuplet,
    place,
    read_measure,
    repair_slurs,
    repair_ties,
    repair_tuplets,
    respell,
    rests,
    same_pitch,
    set_length,
    set_tie,
    tuplet_bounds,
    type_of,
    write_measure,
    write_offset,
)
from pipeline.structure import insert_measure, origin_of

# Where the first note on an empty staff lands: the middle of that clef's staff.
CLEF_MIDDLE = {"G": ("B", 4), "F": ("D", 3), "C": ("C", 4)}
ENTERED_LENGTHS = {"whole", "half", "quarter", "eighth", "16th", "32nd", "64th"}
MOST_DOTS = 2
AFTER = "after:"
MOST_LYRIC_LETTERS = 60
SYLLABLES = {(False, False): "single", (False, True): "begin", (True, True): "middle", (True, False): "end"}
Pitch = tuple[str, int, int]


@dataclass
class Spot:
    """Where one note is, with everything needed to change the bar around it."""

    part: ET.Element
    context: Context
    timeline: Timeline
    key: tuple[str, str]
    event: Event | None
    note: ET.Element | None
    is_pickup: bool = False

    @property
    def line(self) -> list[Event]:
        return self.timeline.lines[self.key]


def ensure_note_ids(root: ET.Element) -> None:
    """Give each note a stable id. The renderer keeps them, so clicks map back to notes."""
    used = {note.get("id") for note in root.iter("note") if note.get("id")}
    counter = 0
    for note in root.iter("note"):
        if note.get("id"):
            continue
        while f"note{counter}" in used:
            counter += 1
        note.set("id", f"note{counter}")
        used.add(f"note{counter}")


def find_note(root: ET.Element, note_id: str) -> ET.Element:
    for note in root.iter("note"):
        if note.get("id") == note_id:
            return note
    raise EditError(f"No note with id {note_id}")


def context_of(root: ET.Element, target: ET.Element) -> tuple[ET.Element, Context]:
    for part in root.findall("part"):
        for context in contexts(part):
            if any(note is target for note in context.measure.iter("note")):
                return part, context
    raise EditError("That note isn't in this score")


def locate(root: ET.Element, note_id: str) -> Spot:
    note = find_note(root, note_id)
    part, context = context_of(root, note)
    timeline = read_measure(context.measure, context.divisions)
    for key, line in timeline.lines.items():
        for event in line:
            if any(member is note for member in event.notes):
                # Judged before the edit, or removing a note from a full first bar would look like one.
                is_pickup = context.is_pickup or (context.index == 0 and line_end(line) < context.length)
                return Spot(part, context, timeline, key, event, note, is_pickup)
    raise EditError("Grace notes can't be changed this way yet.")


def finish_bar(spot: Spot) -> None:
    """Write a changed bar back. A bar left short is topped up with rests, unless it's a pickup."""
    context = spot.context
    line = spot.line
    if not spot.is_pickup and line_end(line) < context.length:
        line.extend(rests(line_end(line), context.length, context, spot.key))
    write_measure(context.measure, spot.timeline, context.divisions)
    repair_tuplets(context)
    respell(context)


def finish_edit(root: ET.Element, part: ET.Element, first: int, last: int) -> None:
    for index in range(first, last + 1):
        repair_ties(part, index)
    repair_slurs(part)
    ensure_note_ids(root)


def commit(root: ET.Element, spot: Spot) -> None:
    """Finish a rhythm edit inside one bar."""
    finish_bar(spot)
    finish_edit(root, spot.part, spot.context.index, spot.context.index)


def length_of(type_name: str, dots: int) -> Fraction:
    if type_name not in ENTERED_LENGTHS or not 0 <= dots <= MOST_DOTS:
        raise EditError("That isn't a note length this app can write.")
    return LENGTHS[type_name] * (2 - Fraction(1, 2**dots))


def event_at(context: Context, key: tuple[str, str], onset: Fraction) -> Event | None:
    timeline = read_measure(context.measure, context.divisions)
    lines = [timeline.lines[key]] if key in timeline.lines else list(timeline.lines.values())
    for line in lines:
        for event in sorted(line, key=lambda event: event.onset):
            if event.onset >= onset:
                return event
    return None


def next_id(part: ET.Element, context: Context, key: tuple[str, str], onset: Fraction) -> str | None:
    """Return the event a writer would move to next, or None at the end of the score."""
    found = event_at(context, key, onset) if onset < context.length else None
    if found is None:
        known = list(contexts(part))
        if context.index + 1 >= len(known):
            return None
        found = event_at(known[context.index + 1], key, Fraction(0))
    return found.notes[0].get("id")


def next_bar(root: ET.Element, spot: Spot) -> Spot:
    """Return the first event of the following bar, adding that bar at the end of the score."""
    known = list(contexts(spot.part))
    if spot.context.index + 1 >= len(known):
        insert_measure(root, len(known))
        ensure_note_ids(root)
        known = list(contexts(spot.part))
    event = event_at(known[spot.context.index + 1], spot.key, Fraction(0))
    return locate(root, event.notes[0].get("id"))


def carry_on(root: ET.Element, spot: Spot) -> Spot:
    """Open the next bar for this voice to carry on in, even if the voice stops there.

    Other voices in that bar aren't touched. A voice that's new to the bar holds only what's
    carried into it, so it isn't padded with rests.
    """
    known = list(contexts(spot.part))
    if spot.context.index + 1 >= len(known):
        insert_measure(root, len(known))
        ensure_note_ids(root)
        known = list(contexts(spot.part))
    context = known[spot.context.index + 1]
    timeline = read_measure(context.measure, context.divisions)
    if spot.key in timeline.lines:
        first = min(timeline.lines[spot.key], key=lambda event: event.onset)
        return Spot(spot.part, context, timeline, spot.key, first, first.notes[0])
    timeline.lines[spot.key] = []
    return Spot(spot.part, context, timeline, spot.key, None, None, is_pickup=True)


def locate_cursor(root: ET.Element, cursor: str) -> Spot:
    if cursor.startswith(AFTER):
        return next_bar(root, locate(root, cursor.removeprefix(AFTER)))
    return locate(root, cursor)


def make_stack(length: Fraction, context: Context, key: tuple[str, str], pitches: list[Pitch], tuplet: Tuplet | None = None) -> list[ET.Element]:
    """Make the notes of one event: a rest, a single note or several on one stem."""
    if not pitches:
        return [make_note(length, context.divisions, key, None, tuplet)]
    notes = [make_note(length, context.divisions, key, pitch, tuplet) for pitch in pitches]
    for note in notes[1:]:
        place(note, ET.Element("chord"))
    return notes


def pitches_of(event: Event) -> list[Pitch]:
    return [] if event.is_rest else [
        (note.findtext("pitch/step"), int(float(note.findtext("pitch/alter") or 0)), int(note.findtext("pitch/octave")))
        for note in event.notes
    ]


def tuplet_fit(spot: Spot, length: Fraction) -> bool:
    """Decide whether something written here stays inside the triplet it lands on.

    A length is written as it reads, so a quarter in an eighth-note triplet takes two of the
    three places. Something as long as the whole triplet replaces it, from its first note.
    """
    if not spot.event.in_tuplet:
        return False
    start, end = tuplet_bounds(spot.line, spot.event)
    if spot.event.onset + length * spot.event.tuplet.scale <= end:
        return True
    if spot.event.onset == start and length >= end - start:
        return False
    raise EditError("That's too long to fit in this triplet.")


def place_in_tuplet(root: ET.Element, spot: Spot, length: Fraction, notes: list[ET.Element], tuplet: Tuplet) -> Event:
    new = Event(spot.event.onset, length * tuplet.scale, notes, spot.event.grace)
    spot.timeline.lines[spot.key] = overwrite_in_tuplet(spot.line, new, tuplet, spot.context, spot.key)
    commit(root, spot)
    return new


@dataclass
class Written:
    """What a write produced, for moving the cursor on and tying what comes next."""

    head: list[ET.Element]
    tail: list[ET.Element]
    cursor: str


def tie_together(before: list[ET.Element], after: list[ET.Element]) -> None:
    for first in before:
        for second in after:
            if same_pitch(first, second):
                set_tie(first, "start", True)
                set_tie(second, "stop", True)


def write_across(root: ET.Element, spot: Spot, length: Fraction, pitches: list[Pitch], keep: list[ET.Element] | None = None) -> Written:
    """Write from this spot, carrying over the barline and tying the pieces if the bar is too short.

    `keep` holds notes to reuse for the first piece, so a lengthened note keeps its id.
    """
    part, first_bar, onset, grace = spot.part, spot.context.index, spot.event.onset, spot.event.grace if keep else []
    head: list[ET.Element] = []
    tail: list[ET.Element] = []
    while True:
        context = spot.context
        if onset >= context.length:
            raise EditError("This note is past the end of its bar. Fix the bar first.")
        size = min(length, context.length - onset)
        if pitches:
            events = []
            position = onset
            for piece in note_lengths(size):
                notes = keep if keep and not head else make_stack(piece, context, spot.key, pitches)
                if keep and not head:
                    for note in notes:
                        set_length(note, piece, context.divisions)
                events.append(Event(position, piece, notes, grace if not head else []))
                tie_together(tail, notes)
                head = head or notes
                tail = notes
                position += piece
        else:
            events = rests(onset, onset + size, context, spot.key)
            if keep and not head and events[0].notes[0].find("type") is not None:
                set_length(keep[0], events[0].length, context.divisions)
                events[0] = Event(onset, events[0].length, keep, grace)
            head = head or events[0].notes
            tail = events[-1].notes
        for event in events:
            spot.timeline.lines[spot.key] = overwrite(spot.line, event, context, spot.key)
        finish_bar(spot)
        length -= size
        if length == 0:
            break
        spot, onset = carry_on(root, spot), Fraction(0)
    finish_edit(root, part, first_bar, spot.context.index)
    cursor = next_id(part, spot.context, spot.key, events[-1].end) or AFTER + tail[0].get("id")
    return Written(head, tail, cursor)


def write_at(root: ET.Element, spot: Spot, length: Fraction, pitches: list[Pitch]) -> Written:
    if not tuplet_fit(spot, length):
        return write_across(root, spot, length, pitches)
    tuplet = spot.event.tuplet
    notes = make_stack(length, spot.context, spot.key, pitches, tuplet)
    new = place_in_tuplet(root, spot, length, notes, tuplet)
    return Written(notes, notes, next_id(spot.part, spot.context, spot.key, new.end) or AFTER + notes[0].get("id"))


def change_length(root: ET.Element, note_id: str, type_name: str, dots: int) -> None:
    """Make a note or rest longer or shorter, keeping the bar full.

    A longer note overwrites what follows, and a shorter one leaves a rest behind. One that no
    longer fits in the bar carries on into the next, tied.
    """
    fit_divisions(root)
    spot = locate(root, note_id)
    length = length_of(type_name, dots)
    if tuplet_fit(spot, length):
        tuplet = spot.event.tuplet
        for note in spot.event.notes:
            set_length(note, length, spot.context.divisions, tuplet)
        place_in_tuplet(root, spot, length, spot.event.notes, tuplet)
        return
    write_across(root, spot, length, pitches_of(spot.event), keep=spot.event.notes)


def toggle_dot(root: ET.Element, note_id: str) -> None:
    fit_divisions(root)
    name, dots = type_of(locate(root, note_id).event.nominal)
    change_length(root, note_id, name, 0 if dots else 1)


def height_of(note: ET.Element) -> int:
    return diatonic(note.findtext("pitch/step"), int(note.findtext("pitch/octave")))


def previous_height(spot: Spot) -> int:
    """Find how high the music was just before this event, to keep a new note close to it."""
    for context in reversed(list(contexts(spot.part))[: spot.context.index + 1]):
        timeline = read_measure(context.measure, context.divisions)
        lines = [timeline.lines[spot.key]] if spot.key in timeline.lines else list(timeline.lines.values())
        for line in lines:
            earlier = [event for event in sorted(line, key=lambda event: event.onset, reverse=True) if not event.is_rest and (context.index < spot.context.index or event.onset < spot.event.onset)]
            if earlier:
                return height_of(earlier[0].notes[-1])
    return diatonic(*CLEF_MIDDLE.get(spot.context.clefs.get(spot.key[0], "G"), CLEF_MIDDLE["G"]))


def alter_in_force(spot: Spot, step: str, octave: int) -> int:
    """Return what a plain note here means: an earlier sharp holds until the barline."""
    alter = alteration_in_key(step, spot.context.fifths)
    for event in spot.timeline.events():
        if event.onset >= spot.event.onset:
            break
        for note in event.notes:
            if note.findtext("pitch/step") == step and note.findtext("pitch/octave") == str(octave) and (note.findtext("staff") or "1") == spot.key[0]:
                alter = int(float(note.findtext("pitch/alter") or 0))
    return alter


def pitch_on_line(spot: Spot, line: int) -> Pitch:
    """Return the pitch at a place on the staff, counted in steps down from the top line."""
    height = spot.context.tops.get(spot.key[0], diatonic("F", 5)) - line
    step, octave = STEPS[height % 7], height // 7
    if not 0 <= octave <= 9:
        raise EditError("That's too far from the staff.")
    return step, alter_in_force(spot, step, octave), octave


def write_note(root: ET.Element, at_id: str, type_name: str, dots: int, step: str | None, line: int | None = None) -> dict:
    """Write a note, or a rest if there's no step, over whatever is at this event.

    A letter says which note but not which octave, so it takes the nearest to the note before,
    which is what you almost always mean. A click gives the exact staff line instead.
    """
    if step is not None and step not in STEPS:
        raise EditError("A note is a letter from A to G.")
    fit_divisions(root)
    spot = locate_cursor(root, at_id)
    length = length_of(type_name, dots)
    pitches: list[Pitch] = []
    if line is not None:
        pitches = [pitch_on_line(spot, line)]
    elif step is not None:
        octave = round((previous_height(spot) - STEPS.index(step)) / 7)
        pitches = [(step, alter_in_force(spot, step, octave), octave)]
    written = write_at(root, spot, length, pitches)
    return {"written": written.head[0].get("id"), "cursor": written.cursor}


def add_pitch(root: ET.Element, note_id: str, step: str, line: int | None = None) -> str:
    """Stack a note on the ones already at this event, to build a chord on one stem.

    A letter goes above the top note. A click gives the exact staff line instead.
    """
    if line is None and step not in STEPS:
        raise EditError("A note is a letter from A to G.")
    fit_divisions(root)
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Add a note here first, then stack others on it.")
    if line is None:
        top = max(height_of(note) for note in spot.event.notes)
        octave = (top - STEPS.index(step)) // 7 + 1
        alter = alteration_in_key(step, spot.context.fifths)
    else:
        step, alter, octave = pitch_on_line(spot, line)
        if any(height_of(note) == diatonic(step, octave) for note in spot.event.notes):
            raise EditError("That note is already in this chord.")
    note = copy.deepcopy(spot.event.notes[0])
    note.attrib.pop("id", None)
    for tag in ("tie", "accidental", "beam", "notations", "lyric", "chord"):
        for found in note.findall(tag):
            note.remove(found)
    pitch = note.find("pitch")
    for child in list(pitch):
        pitch.remove(child)
    ET.SubElement(pitch, "step").text = step
    if alter:
        ET.SubElement(pitch, "alter").text = str(alter)
    ET.SubElement(pitch, "octave").text = str(octave)
    place(note, ET.Element("chord"))
    spot.event.notes.append(note)
    commit(root, spot)
    return note.get("id")


def following(root: ET.Element, spot: Spot) -> Spot | None:
    """Return the next event in this voice, which may be in the next bar."""
    later = [event for event in spot.line if event.onset >= spot.event.end]
    if later:
        event = min(later, key=lambda item: item.onset)
        return Spot(spot.part, spot.context, spot.timeline, spot.key, event, event.notes[0], spot.is_pickup)
    known = list(contexts(spot.part))
    if spot.context.index + 1 >= len(known):
        return None
    context = known[spot.context.index + 1]
    event = event_at(context, spot.key, Fraction(0))
    return locate(root, event.notes[0].get("id")) if event is not None else None


def toggle_tie(root: ET.Element, note_id: str) -> None:
    """Tie a note to the same note after it, or untie it.

    If a rest comes next, the note is repeated over it, because that's how you hold a note
    across a barline.
    """
    fit_divisions(root)
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Rests can't be tied.")
    after = following(root, spot)
    tied = is_tied(spot.note, "start")
    if after is None:
        if tied:
            set_tie(spot.note, "start", False)
            commit(root, spot)
            return
        raise EditError("There's nothing after this note to tie it to.")
    partner = next((note for note in after.event.notes if same_pitch(note, spot.note)), None)
    if tied:
        set_tie(spot.note, "start", False)
        if partner is not None:
            set_tie(partner, "stop", False)
    elif partner is not None:
        set_tie(spot.note, "start", True)
        set_tie(partner, "stop", True)
    elif after.event.is_rest:
        length = min(spot.event.length, after.context.length - after.event.onset, after.event.length)
        if after.event.in_tuplet:
            raise EditError("A tie can't start a note inside a triplet yet.")
        note = make_note(length, after.context.divisions, after.key, (spot.note.findtext("pitch/step"), int(float(spot.note.findtext("pitch/alter") or 0)), int(spot.note.findtext("pitch/octave"))))
        new = Event(after.event.onset, length, [note])
        after.timeline.lines[after.key] = overwrite(after.line, new, after.context, after.key)
        set_tie(spot.note, "start", True)
        set_tie(note, "stop", True)
    else:
        raise EditError("A tie joins two notes of the same pitch.")
    if after.context is not spot.context:
        commit(root, after)
        spot = locate(root, note_id)
    commit(root, spot)


def close_gap(root: ET.Element, note_id: str) -> str:
    """Remove an event and pull the rest of the voice earlier.

    This is the fix for a bar with too many beats, usually from a note recognition invented.
    Returns the event that now sits in its place.
    """
    fit_divisions(root)
    spot = locate(root, note_id)
    if spot.event.in_tuplet:
        raise EditError("Notes in a triplet can't be removed this way yet.")
    if len(spot.line) == 1:
        raise EditError("That's the only thing in this bar.")
    gone = spot.event
    spot.line.remove(gone)
    for event in spot.line:
        if event.onset > gone.onset:
            event.onset -= gone.length
    commit(root, spot)
    return next_id(spot.part, spot.context, spot.key, gone.onset) or spot.line[-1].notes[0].get("id")


def insert_rest(root: ET.Element, note_id: str, type_name: str, dots: int) -> str:
    """Open a gap before an event, to fit in a note recognition missed.

    Rests at the end of the bar are used up first, so a bar that was short ends up full.
    """
    fit_divisions(root)
    spot = locate(root, note_id)
    length = length_of(type_name, dots)
    start = spot.event.onset
    line = sorted(spot.line, key=lambda event: event.onset)
    if spot.event.in_tuplet and not on_plain_grid(start):
        raise EditError("A gap can't open inside a triplet.")
    spare = length - (spot.context.length - line_end(line))
    while spare > 0 and line[-1].is_rest and not line[-1].in_tuplet and line[-1].onset >= start and len(line) > 1:
        last = line.pop()
        spot.line.remove(last)
        spare -= last.length
    if spare < 0:
        spot.line.extend(rests(line_end(line), line_end(line) - spare, spot.context, spot.key))
    for event in spot.line:
        if event.onset >= start:
            event.onset += length
    gap = rests(start, start + length, spot.context, spot.key)
    spot.line.extend(gap)
    commit(root, spot)
    return gap[0].notes[0].get("id")


def move_note(root: ET.Element, note_id: str, steps: int) -> None:
    """Move a note up or down the staff, taking the key signature's sharps and flats.

    An octave keeps its accidental, because it's the same note name.
    """
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no pitch to move")
    part, context = context_of(root, note)
    absolute = STEPS.index(pitch.findtext("step")) + 7 * int(pitch.findtext("octave")) + steps
    step = STEPS[absolute % 7]
    pitch.find("step").text = step
    pitch.find("octave").text = str(absolute // 7)
    if steps % 7:
        write_alter(pitch, alteration_in_key(step, context.fifths))
    respell(context)
    repair_ties(part, context.index)  # A tie can only join two notes of the same pitch.


def write_alter(pitch: ET.Element, alter: int) -> None:
    existing = pitch.find("alter")
    if alter:
        if existing is None:
            existing = ET.Element("alter")
            pitch.insert(1, existing)
        existing.text = str(alter)
    elif existing is not None:
        pitch.remove(existing)


def set_accidental(root: ET.Element, note_id: str, alter: int) -> None:
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no accidental")
    write_alter(pitch, alter)
    part, context = context_of(root, note)
    respell(context)
    repair_ties(part, context.index)


def delete_note(root: ET.Element, note_id: str) -> None:
    """Remove a note. Its time has to stay filled, so a lone note becomes a rest."""
    note = find_note(root, note_id)
    if note.find("pitch") is None:
        raise EditError("This is already a rest")
    part, context = context_of(root, note)
    if note.find("grace") is not None:
        context.measure.remove(note)
        return
    spot = locate(root, note_id)
    if len(spot.event.notes) > 1:
        # The next stacked note inherits the duration, so it just stops being a chord member.
        if spot.event.notes[0] is note:
            second = spot.event.notes[1]
            second.remove(second.find("chord"))
        context.measure.remove(note)
    else:
        pitch_index = list(note).index(note.find("pitch"))
        note.remove(note.find("pitch"))
        note.insert(pitch_index, ET.Element("rest"))
        for tag in ("accidental", "stem", "beam", "tie", "notations", "lyric"):
            for child in note.findall(tag):
                note.remove(child)
    repair_tuplets(context)
    respell(context)
    repair_ties(part, context.index)
    repair_slurs(part)


def make_tuplet(root: ET.Element, note_id: str, length: Fraction | None = None, keep_note: bool = True) -> dict:
    """Turn a length into a triplet of three rests, with any note already here as the first.

    Without a length, it divides the event at this place. The result says which note length the
    triplet uses, because that's what the writer will want next.
    """
    fit_divisions(root)
    spot = locate_cursor(root, note_id)
    context, key, onset = spot.context, spot.key, spot.event.onset
    span = spot.event.length if length is None else length
    if spot.event.in_tuplet or not on_plain_grid(onset):
        raise EditError("Triplets inside triplets aren't supported.")
    if onset + span > context.length:
        raise EditError("That triplet is too long to fit in this bar.")
    written = span / 2
    name, dots = type_of(written)
    if dots:
        raise EditError("A triplet needs a length without a dot.")
    tuplet = Tuplet(unit=name)
    pitches = pitches_of(spot.event) if keep_note else []
    spot.timeline.lines[key] = [event for event in overwrite(spot.line, Event(onset, span, []), context, key) if event.notes]
    members = []
    for number in range(tuplet.actual):
        stack = make_stack(written, context, key, pitches if number == 0 else [], tuplet)
        members.append(Event(onset + number * written * tuplet.scale, written * tuplet.scale, stack))
    spot.line.extend(members)
    commit(root, spot)
    return {"cursor": members[1 if pitches else 0].notes[0].get("id"), "type": name, "dots": 0}


def remove_tuplet(root: ET.Element, note_id: str) -> str:
    """Replace a whole triplet with ordinary rests."""
    fit_divisions(root)
    spot = locate(root, note_id)
    if not spot.event.in_tuplet:
        raise EditError("This isn't in a triplet.")
    start, end = tuplet_bounds(spot.line, spot.event)
    kept = [event for event in spot.line if event.end <= start or event.onset >= end]
    gap = rests(start, end, spot.context, spot.key)
    spot.timeline.lines[spot.key] = sorted([*kept, *gap], key=lambda event: event.onset)
    commit(root, spot)
    return gap[0].notes[0].get("id")


def toggle_tuplet(root: ET.Element, note_id: str, length: Fraction | None = None) -> dict:
    """Make a triplet here, or undo the one that's already here."""
    if not note_id.startswith(AFTER) and locate(root, note_id).event.in_tuplet:
        return {"cursor": remove_tuplet(root, note_id)}
    return make_tuplet(root, note_id, length)


def lyric_of(note: ET.Element) -> ET.Element | None:
    return next((found for found in note.findall("lyric") if found.get("number", "1") == "1"), None)


def link_syllables(part: ET.Element, key: tuple[str, str]) -> None:
    """Work out each syllable's place in its word, which depends on the syllables around it."""
    joined = False
    for context in contexts(part):
        line = read_measure(context.measure, context.divisions).lines.get(key, [])
        for event in sorted(line, key=lambda item: item.onset):
            lyric = lyric_of(event.notes[0])
            if lyric is None:
                continue
            carries_on = lyric.findtext("syllabic") in ("begin", "middle")
            lyric.find("syllabic").text = SYLLABLES[(joined, carries_on)]
            joined = carries_on


def next_note_id(spot: Spot) -> str | None:
    """Return the next note that starts a sound, because a tied or rested place takes no syllable."""
    for context in list(contexts(spot.part))[spot.context.index :]:
        line = read_measure(context.measure, context.divisions).lines.get(spot.key, [])
        for event in sorted(line, key=lambda item: item.onset):
            later = context.index > spot.context.index or event.onset > spot.event.onset
            if later and not event.is_rest and not is_tied(event.notes[0], "stop"):
                return event.notes[0].get("id")
    return None


def store_lyric(spot: Spot, text: str, joined: bool | None) -> None:
    """Put a word or syllable under a note. `joined` means the word carries on at the next note."""
    if spot.event.is_rest:
        raise EditError("Lyrics go under notes, not rests.")
    text = text.strip()
    if len(text) > MOST_LYRIC_LETTERS:
        raise EditError("That's too long for one note.")
    note = spot.event.notes[0]
    existing = lyric_of(note)
    if joined is None:
        joined = existing is not None and existing.findtext("syllabic") in ("begin", "middle")
    if existing is not None:
        note.remove(existing)
    if text:
        lyric = ET.Element("lyric", number="1")
        ET.SubElement(lyric, "syllabic").text = "begin" if joined else "single"
        ET.SubElement(lyric, "text").text = text
        place(note, lyric)
    link_syllables(spot.part, spot.key)


def set_lyric(root: ET.Element, note_id: str, text: str, joined: bool | None = False) -> dict:
    """Store a syllable someone typed, and say which note comes next.

    `joined` of None keeps how it was joined. A word typed on the last note has nothing to join.
    """
    spot = locate(root, note_id)
    following = next_note_id(spot)
    store_lyric(spot, text, joined if following else False)
    return {"next": following}


def events_between(root: ET.Element, first_id: str, last_id: str) -> tuple[Spot, list[tuple[Context, list[Event], Event]]]:
    """Return every event in one voice from one note to another, taking in whole triplets."""
    start, stop = locate(root, first_id), locate(root, last_id)
    if (stop.context.index, stop.event.onset) < (start.context.index, start.event.onset):
        start, stop = stop, start
    lower = tuplet_bounds(start.line, start.event)[0] if start.event.in_tuplet else start.event.onset
    upper = tuplet_bounds(stop.line, stop.event)[1] if stop.event.in_tuplet else stop.event.end
    chosen = []
    for context in list(contexts(start.part))[start.context.index : stop.context.index + 1]:
        line = read_measure(context.measure, context.divisions).lines.get(start.key, [])
        for event in sorted(line, key=lambda item: item.onset):
            after_start = context.index > start.context.index or event.onset >= lower
            before_stop = context.index < stop.context.index or event.onset < upper
            if after_start and before_stop:
                chosen.append((context, line, event))
    return start, chosen


def range_ids(root: ET.Element, first_id: str, last_id: str) -> dict:
    _, chosen = events_between(root, first_id, last_id)
    return {"ids": [note.get("id") for _, _, event in chosen for note in event.notes]}


def chord_on(context: Context, event: Event) -> str | None:
    timeline = read_measure(context.measure, context.divisions)
    for mark in timeline.marks:
        if mark.element.tag == "harmony" and event.onset <= mark.onset < event.end:
            chord = chord_from_harmony(mark.element)
            return str(chord) if chord else None
    return None


def copy_range(root: ET.Element, first_id: str, last_id: str) -> dict:
    """Describe a stretch of one voice, so it can be pasted anywhere, even into another score."""
    _, chosen = events_between(root, first_id, last_id)
    items: list[dict] = []
    triplets: dict[tuple[int, Fraction], int] = {}
    for context, line, event in chosen:
        triplet = None
        if event.in_tuplet:
            triplet = triplets.setdefault((context.index, tuplet_bounds(line, event)[0]), len(triplets))
        written = [event.nominal] if not event.is_rest else note_lengths(event.nominal)
        lyric = lyric_of(event.notes[0])
        for number, length in enumerate(written):
            name, dots = type_of(length)
            items.append({
                "type": name,
                "dots": dots,
                "pitches": [list(pitch) for pitch in pitches_of(event)],
                "tie": any(is_tied(note, "start") for note in event.notes) and number == len(written) - 1,
                "triplet": triplet,
                "chord": chord_on(context, event) if number == 0 else None,
                "lyric": None if lyric is None or number else {"text": lyric.findtext("text") or "", "joined": lyric.findtext("syllabic") in ("begin", "middle")},
            })
    return {"events": items}


def checked_pitches(item: dict) -> list[Pitch]:
    found = []
    for step, alter, octave in item["pitches"]:
        if step not in STEPS or not -2 <= alter <= 2 or not 0 <= octave <= 9:
            raise EditError("The copied notes can't be read.")
        found.append((step, alter, octave))
    return found


def set_chord_at(root: ET.Element, spot: Spot, text: str) -> None:
    """Put a chord at an event, replacing any chord already at that moment."""
    for mark in read_measure(spot.context.measure, spot.context.divisions).marks:
        if mark.element.tag == "harmony" and mark.onset == spot.event.onset:
            spot.context.measure.remove(mark.element)
    add_chord(root, spot.event.notes[0].get("id"), text)


def paste(root: ET.Element, at_id: str, items: list[dict]) -> dict:
    """Write copied music from this place on, overwriting what's there.

    Notes that no longer fit a bar carry over it, tied, so a passage can land on any beat. A
    triplet has to fit inside one bar.
    """
    if not items:
        raise EditError("Nothing has been copied yet.")
    fit_divisions(root)
    part = locate_cursor(root, at_id).part
    first_bar = None
    cursor = at_id
    tied: list[ET.Element] = []
    written: Written | None = None
    triplets_done: set[int] = set()
    for number, item in enumerate(items):
        triplet = item["triplet"]
        if triplet is not None and triplet not in triplets_done:
            triplets_done.add(triplet)
            span = sum((length_of(other["type"], other["dots"]) for other in items if other["triplet"] == triplet), Fraction(0))
            cursor = make_tuplet(root, cursor, span * Tuplet().scale, keep_note=False)["cursor"]
        spot = locate_cursor(root, cursor)
        first_bar = spot.context.index if first_bar is None else first_bar
        written = write_at(root, spot, length_of(item["type"], item["dots"]), checked_pitches(item))
        tie_together(tied, written.head)
        tied = written.tail if item["tie"] and number + 1 < len(items) else []
        cursor = written.cursor
        landed = locate(root, written.head[0].get("id"))
        if item["chord"]:
            set_chord_at(root, landed, item["chord"])
        if item["lyric"] and not landed.event.is_rest:
            store_lyric(locate(root, written.head[0].get("id")), item["lyric"]["text"], item["lyric"]["joined"])
    last_bar = locate(root, written.tail[0].get("id")).context.index
    for context in list(contexts(part))[first_bar : last_bar + 1]:
        respell(context)
    finish_edit(root, part, first_bar, last_bar)
    return {"written": written.tail[0].get("id"), "cursor": cursor}


def clear_marks(root: ET.Element, note_id: str) -> None:
    """Remove fermatas, ornaments and articulations, keeping slurs and ties.

    Chord text just above a staff is sometimes misread as one of these, about once a page.
    """
    notations = find_note(root, note_id).find("notations")
    removed = 0
    if notations is not None:
        for tag in ("fermata", "ornaments", "articulations"):
            for child in notations.findall(tag):
                notations.remove(child)
                removed += 1
    if not removed:
        raise EditError("This note has no marks to clear")


def list_chords(root: ET.Element) -> list[dict]:
    """List chords in document order, which matches their order on the rendered page."""
    chords = []
    part = root.find("part")
    divisions = 1.0
    for measure_index, measure in enumerate(part.findall("measure")):
        position = 0.0
        for child in measure:
            length = float(child.findtext("duration") or 0)
            if child.tag == "attributes":
                divisions = float(child.findtext("divisions") or divisions)
            elif child.tag == "harmony":
                chord = chord_from_harmony(child)
                onset = round(position + float(offset_of(child, int(divisions))), 3)
                chords.append({
                    "index": len(chords),
                    "text": str(chord) if chord else "?",
                    "measure": measure_index,
                    "origin": origin_of(measure, measure_index),
                    "onset": onset,
                })
            elif child.tag == "backup":
                position -= length / divisions
            elif child.tag == "forward":
                position += length / divisions
            elif child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
                position += length / divisions
    return chords


def require_chord(text: str):
    chord = parse_chord(text)
    if chord is None:
        raise EditError(f'"{text}" is not a chord symbol this app understands')
    return chord


def harmony_at(root: ET.Element, index: int) -> tuple[ET.Element, ET.Element]:
    harmonies = [(measure, harmony) for measure in root.find("part").findall("measure") for harmony in measure.findall("harmony")]
    if not 0 <= index < len(harmonies):
        raise EditError(f"No chord number {index}")
    return harmonies[index]


def set_chord(root: ET.Element, index: int, text: str) -> None:
    """Replace a chord, or delete it when the text is empty."""
    measure, harmony = harmony_at(root, index)
    position = list(measure).index(harmony)
    new_element = harmony_element(require_chord(text)) if text.strip() else None
    if new_element is not None and harmony.find("offset") is not None:
        # A chord can sit partway through a long note, so its replacement must stay there too.
        offset = Fraction(harmony.findtext("offset"))
        write_offset(new_element, offset, 1)
    measure.remove(harmony)
    if new_element is not None:
        measure.insert(position, new_element)


def add_chord(root: ET.Element, note_id: str, text: str) -> None:
    """Add a chord at a note, which matters when recognition missed one completely."""
    chord = require_chord(text)
    note = find_note(root, note_id)
    for measure in root.iter("measure"):
        children = list(measure)
        if note not in children:
            continue
        index = children.index(note)
        while children[index].find("chord") is not None:
            index -= 1
        measure.insert(index, harmony_element(chord))
        return
