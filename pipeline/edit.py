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
    NATURAL_SEMITONES,
    STEPS,
    TAB_COPY,
    Context,
    EditError,
    Event,
    Timeline,
    Tuplet,
    alteration_in_key,
    contexts,
    diatonic,
    fit_divisions,
    index_of,
    is_tied,
    line_end,
    make_note,
    note_lengths,
    octave_shift_at,
    offset_of,
    on_plain_grid,
    overwrite,
    overwrite_in_tuplet,
    part_contexts,
    place,
    read_measure,
    repair_slurs,
    repair_ties,
    repair_tuplets,
    respell,
    rests,
    same_pitch,
    same_staff_kind,
    set_length,
    set_tie,
    tie_ends,
    tuplet_bounds,
    type_of,
    write_measure,
    write_offset,
)
from pipeline.structure import VOICES_PER_STAFF, delete_measure, insert_measure, origin_of

# Where the first note on an empty staff lands: the middle of that clef's staff.
CLEF_MIDDLE = {"G": ("B", 4), "F": ("D", 3), "C": ("C", 4)}
MIDDLE_LINE_BELOW_TOP = 4  # Steps from a staff's top line down to its middle one.
COMFORTABLE_REACH = 36  # Three octaves up from a fretted instrument's lowest string: E2 to E5 on a guitar, written E3 to E6.
ENTERED_LENGTHS = {"whole", "half", "quarter", "eighth", "16th", "32nd", "64th"}
MOST_DOTS = 2
AFTER = "after:"
MOST_LYRIC_LETTERS = 60
MOST_VERSES = 6
TUPLET_SIZES = {3, 5, 6, 7}
SYLLABLES = {(False, False): "single", (False, True): "begin", (True, True): "middle", (True, False): "end"}
# Most of a chord's markings are kept on its first note, but these belong to one pitch of it.
OWN_PITCH = {"tied", "glissando", "slide"}
MARK_GROUPS = {"articulations", "ornaments", "technical"}  # These hold several marks each.
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
    """Find a note by its id. A fret number on a linked tablature staff stands for the note it plays."""
    if note_id.endswith(TAB_COPY):
        try:
            return find_note(root, note_id.removesuffix(TAB_COPY))
        except EditError:
            pass  # It's a note of a tablature staff of its own.
    index = index_of(root)
    found = index.notes.get(note_id) if index is not None else None
    if found is not None and found.get("id") == note_id and index.place_of(found) is not None:
        return found
    for note in root.iter("note"):
        if note.get("id") == note_id:
            return note
    raise EditError(f"No note with id {note_id}")


def own_ids(root: ET.Element, note_ids: list[str]) -> list[str]:
    """Name each note by its own id, where a selection picked a fret number on its tablature staff."""
    found = []
    for note_id in note_ids:
        try:
            found.append(find_note(root, note_id).get("id") if note_id.endswith(TAB_COPY) else note_id)
        except EditError:
            found.append(note_id)  # The edit itself will say what's wrong with this one.
    return found


def context_of(root: ET.Element, target: ET.Element) -> tuple[ET.Element, Context]:
    index = index_of(root)
    found = index.place_of(target) if index is not None else None
    if found is not None:
        return found
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


def voices_on_staff(part: ET.Element, staff: str) -> list[str]:
    """Return the voices a staff uses, lowest first. Files number them in different ways."""
    used = {
        note.findtext("voice") or "1"
        for note in part.iter("note")
        if (note.findtext("staff") or "1") == staff
    }
    return sorted(used, key=int)


def voice_number(spot: Spot) -> int:
    """Say which voice of its staff this is, counting from 1."""
    return voices_on_staff(spot.part, spot.key[0]).index(spot.key[1]) + 1


def key_for_voice(spot: Spot, number: int) -> tuple[str, str]:
    """Return the voice to write in. A new voice takes the next free number, four to a staff at most."""
    if not 1 <= number <= VOICES_PER_STAFF:
        raise EditError(f"A staff has {VOICES_PER_STAFF} voices.")
    used = voices_on_staff(spot.part, spot.key[0])
    return spot.key[0], used[number - 1] if number <= len(used) else str(int(used[-1]) + 1)


def bar_end(spot: Spot) -> Fraction:
    """Return where this bar ends, which in a pickup is where its music ends."""
    written = max((line_end(line) for line in spot.timeline.lines.values()), default=Fraction(0))
    return written if spot.context.is_pickup else max(spot.context.length, written)


def in_voice(spot: Spot, number: int) -> Spot:
    """Return this same moment in another voice, opening that voice with rests if it's new here.

    A rest lying across the moment is split, so something can be written from exactly there.
    """
    key = key_for_voice(spot, number)
    if key == spot.key:
        return spot
    onset, end = spot.event.onset, bar_end(spot)
    line = spot.timeline.lines.setdefault(key, [])
    covering = next((event for event in line if event.onset <= onset < event.end), None)
    if covering is not None and covering.onset == onset:
        return Spot(spot.part, spot.context, spot.timeline, key, covering, covering.notes[0], spot.is_pickup)
    if covering is not None and not covering.is_rest:
        raise EditError("That voice is already playing a note here.")
    if covering is not None and covering.in_tuplet:
        raise EditError("That voice has a triplet here.")
    if covering is not None:
        line.remove(covering)
        made = [*rests(covering.onset, onset, spot.context, key), *rests(onset, covering.end, spot.context, key)]
    else:
        before = max((event.end for event in line if event.end <= onset), default=Fraction(0))
        after = min((event.onset for event in line if event.onset > onset), default=end)
        made = [*rests(before, onset, spot.context, key), *rests(onset, after, spot.context, key)]
    line.extend(made)
    line.sort(key=lambda event: event.onset)
    event = next(event for event in line if event.onset == onset)
    return Spot(spot.part, spot.context, spot.timeline, key, event, event.notes[0], spot.is_pickup)


def drop_empty_voice(spot: Spot) -> None:
    """Take a bar's extra voice away once it holds only rests, so they don't clutter the staff.

    A voice whose only note has just moved out of it is on the staff nowhere else, which makes it
    an extra voice if any other is there.
    """
    used = voices_on_staff(spot.part, spot.key[0])
    if sorted({*used, spot.key[1]}, key=int).index(spot.key[1]) == 0:
        return
    if all(event.is_rest for event in spot.line):
        del spot.timeline.lines[spot.key]


def move_to_voice(root: ET.Element, note_ids: list[str], number: int) -> None:
    """Move notes into another voice of their staff, as when a line turns out to be two."""
    fit_divisions(root)
    note_ids = own_ids(root, note_ids)
    done = False
    for note_id in note_ids:
        spot = locate(root, note_id)
        if spot.event.is_rest or spot.event.notes[0].get("id") != note_id:
            continue
        target = in_voice(spot, number)
        if target.key == spot.key:
            continue
        event = spot.event
        if event.in_tuplet:
            raise EditError("Notes in a triplet can't change voice yet.")
        for note in [*event.grace, *event.notes]:
            # Files from other programs often leave the voice out when there's only one.
            if note.find("voice") is None:
                place(note, ET.Element("voice"))
            note.find("voice").text = target.key[1]
        spot.line.remove(event)
        spot.timeline.lines[target.key] = overwrite(target.line, event, spot.context, target.key)
        spot.line.extend(rests(event.onset, event.end, spot.context, spot.key))
        spot.line.sort(key=lambda item: item.onset)
        drop_empty_voice(spot)
        write_measure(spot.context.measure, spot.timeline, spot.context.divisions)
        repair_tuplets(spot.context)
        respell(spot.context)
        finish_edit(root, spot.part, spot.context.index, spot.context.index)
        done = True
    if not done:
        raise EditError("Those notes are already in that voice.")


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
    # The middle of the staff, where its clef puts it: a guitar's treble clef with an 8 below has B3 there.
    if spot.key[0] in spot.context.tops:
        return spot.context.tops[spot.key[0]] - MIDDLE_LINE_BELOW_TOP
    return diatonic(*CLEF_MIDDLE.get(spot.context.clefs.get(spot.key[0], "G"), CLEF_MIDDLE["G"]))


def within_reach(root: ET.Element, spot: Spot, step: str, octave: int) -> int:
    """Move a typed note by octaves into the three octaves a guitar or bass plays most, from its lowest string up.

    The nearest note to the one before can be out of reach: a guitar that has climbed to its top
    E would put a typed G above the staff and the frets. Anything else is left where it was.
    """
    from pipeline import fretted  # Fretted parts are this module's caller's business too, and it imports this.

    entry = next((found for found in root.findall("part-list/score-part") if found.get("id") == spot.part.get("id")), None)
    board = fretted.fretboard(spot.part, entry)
    if board is None:
        return octave
    shifts = fretted.shifts_of(spot.part)
    lowest = min(board.opens) + board.capo - (shifts[spot.context.index] if spot.context.index < len(shifts) else 0)
    sound = lambda at: 12 * (at + 1) + NATURAL_SEMITONES[step]
    while sound(octave) < lowest:
        octave += 1
    while sound(octave) > lowest + COMFORTABLE_REACH and sound(octave - 1) >= lowest:
        octave -= 1
    return octave


def alter_in_force(spot: Spot, step: str, octave: int) -> int:
    """Return what a plain note here means: an earlier sharp holds until the barline.

    A tied note only carries on the one before it, so a sharp tied over the barline doesn't count.
    """
    alter = alteration_in_key(step, spot.context.fifths)
    for event in spot.timeline.events():
        if event.onset >= spot.event.onset:
            break
        for note in event.notes:
            if note.findtext("pitch/step") == step and note.findtext("pitch/octave") == str(octave) and (note.findtext("staff") or "1") == spot.key[0] and not is_tied(note, "stop"):
                alter = int(float(note.findtext("pitch/alter") or 0))
    return alter


def pitch_of_key(number: int, fifths: int) -> Pitch:
    """Name a key of a MIDI keyboard, spelling black keys the way the key signature leans."""
    if not 12 <= number <= 127:
        raise EditError("That key is out of range.")
    octave, semitone = number // 12 - 1, number % 12
    natural = next((step for step, value in NATURAL_SEMITONES.items() if value == semitone), None)
    if natural is not None:
        return natural, 0, octave
    if fifths < 0:
        step = next(step for step, value in NATURAL_SEMITONES.items() if value == semitone + 1)
        return step, -1, octave
    step = next(step for step, value in NATURAL_SEMITONES.items() if value == semitone - 1)
    return step, 1, octave


def octaves_drawn_down(spot: Spot) -> int:
    """How many octaves below how they sound notes are drawn here, under an 8va or the like."""
    return octave_shift_at(spot.part, spot.context.index, spot.event.onset if spot.event else Fraction(0), spot.key[0])


def pitch_on_line(spot: Spot, line: int) -> Pitch:
    """Return the pitch at a place on the staff, counted in steps down from the top line.

    Under an 8va line the place stands for the note an octave above it.
    """
    height = spot.context.tops.get(spot.key[0], diatonic("F", 5)) - line + 7 * octaves_drawn_down(spot)
    step, octave = STEPS[height % 7], height // 7
    if not 0 <= octave <= 9:
        raise EditError("That's too far from the staff.")
    return step, alter_in_force(spot, step, octave), octave


def write_note(root: ET.Element, at_id: str, type_name: str, dots: int, step: str | None, line: int | None = None, voice: int | None = None, midi: int | None = None) -> dict:
    """Write a note, or a rest if there's no step, over whatever is at this event.

    A letter says which note but not which octave, so it takes the nearest to the note before,
    which is what you almost always mean. A click gives the exact staff line instead.
    A voice number writes at this same moment in that voice, and a key of a MIDI keyboard says
    exactly which note it is.
    """
    if step is not None and step not in STEPS:
        raise EditError("A note is a letter from A to G.")
    fit_divisions(root)
    spot = locate_cursor(root, at_id)
    if voice is not None:
        spot = in_voice(spot, voice)
    length = length_of(type_name, dots)
    pitches: list[Pitch] = []
    if midi is not None:
        pitches = [pitch_of_key(midi, spot.context.fifths)]
    elif line is not None:
        pitches = [pitch_on_line(spot, line)]
    elif step is not None:
        octave = within_reach(root, spot, step, round((previous_height(spot) - STEPS.index(step)) / 7))
        pitches = [(step, alter_in_force(spot, step, octave), octave)]
    written = write_at(root, spot, length, pitches)
    return {"written": written.head[0].get("id"), "cursor": written.cursor}


def tied_notes(root: ET.Element, note: ET.Element) -> list[ET.Element]:
    """Return a note with every note tied to it, either way, however many bars they cross."""
    found = [note]
    for member in found:
        if member.find("grace") is not None or not (is_tied(member, "start") or is_tied(member, "stop")):
            continue
        part, context = context_of(root, member)
        known = part_contexts(part)
        timelines = {number: read_measure(known[number].measure, known[number].divisions) for number in range(max(context.index - 1, 0), min(context.index + 2, len(known)))}
        for line in timelines[context.index].lines.values():
            ordered = sorted(line, key=lambda event: event.onset)
            for position, event in enumerate(ordered):
                if not any(item is member for item in event.notes):
                    continue
                after, before = tie_ends(timelines, context.index, ordered, position)
                after, before = same_staff_kind(context, member, after), same_staff_kind(context, member, before)
                ends = [other for other in after if is_tied(member, "start") and is_tied(other, "stop")]
                ends += [other for other in before if is_tied(member, "stop") and is_tied(other, "start")]
                found.extend(other for other in ends if same_pitch(member, other) and not any(other is seen for seen in found))
    return found


def one_note_per_tie(root: ET.Element, note_ids: list[str]) -> list[str]:
    """Leave out notes tied to one listed before them, because respelling a note respells those too."""
    kept: list[str] = []
    covered: set[str | None] = set()
    for note_id in note_ids:
        if note_id in covered:
            continue
        kept.append(note_id)
        try:
            covered.update(member.get("id") for member in tied_notes(root, find_note(root, note_id)))
        except EditError:
            pass  # The edit itself will say what's wrong with this one.
    return kept


def respell_note(root: ET.Element, note_id: str) -> None:
    """Swap a note for the same sound under another name, as C sharp for D flat.

    Notes tied to it change with it. They're one sound, and a tie only joins notes spelled alike.
    """
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no pitch to respell.")
    step, alter = pitch.findtext("step"), int(float(pitch.findtext("alter") or 0))
    octave = int(pitch.findtext("octave"))
    sound = 12 * octave + NATURAL_SEMITONES[step] + alter
    # Up a letter name, or down one for a note with a flat, whichever leaves fewer accidentals.
    choices = []
    for change in (1, -1):
        height = diatonic(step, octave) + change
        other, other_octave = STEPS[height % 7], height // 7
        other_alter = sound - 12 * other_octave - NATURAL_SEMITONES[other]
        if abs(other_alter) <= 2:
            choices.append((abs(other_alter), other_alter < 0 if alter > 0 else other_alter > 0, other, other_alter, other_octave))
    if not choices:
        raise EditError("This note has no other spelling.")
    _, _, other, other_alter, other_octave = min(choices)
    chain = tied_notes(root, note)
    for member in chain:
        written = member.find("pitch")
        written.find("step").text = other
        written.find("octave").text = str(other_octave)
        write_alter(written, other_alter)
    for member in chain:
        part, context = context_of(root, member)
        respell(context)
        repair_ties(part, context.index)


def flip_stem(root: ET.Element, note_id: str) -> None:
    """Turn a note's stem the other way. The drawing decides when nothing has been said."""
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Rests have no stem.")
    first = spot.event.notes[0].find("stem")
    # Verovio puts stems of notes on or above the middle line down, so that's the one to go against.
    middle = spot.context.tops.get(spot.key[0], diatonic("F", 5)) - 4
    drawn_down = height_of(spot.event.notes[0]) >= middle
    now = first.text if first is not None else ("down" if drawn_down else "up")
    for note in spot.event.notes:
        for found in note.findall("stem"):
            note.remove(found)
        stem = ET.Element("stem")
        stem.text = "up" if now == "down" else "down"
        place(note, stem)


def add_pitch(root: ET.Element, note_id: str, step: str, line: int | None = None, midi: int | None = None) -> str:
    """Stack a note on the ones already at this event, to build a chord on one stem.

    A letter goes above the top note. A click or a MIDI key gives the exact note instead.
    """
    if line is None and midi is None and step not in STEPS:
        raise EditError("A note is a letter from A to G.")
    fit_divisions(root)
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Add a note here first, then stack others on it.")
    if midi is not None:
        step, alter, octave = pitch_of_key(midi, spot.context.fifths)
    elif line is not None:
        step, alter, octave = pitch_on_line(spot, line)
    else:
        top = max(height_of(note) for note in spot.event.notes)
        octave = (top - STEPS.index(step)) // 7 + 1
        alter = alteration_in_key(step, spot.context.fifths)
    if (midi is not None or line is not None) and any(height_of(note) == diatonic(step, octave) for note in spot.event.notes):
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


def remove_range(root: ET.Element, note_ids: list[str]) -> None:
    """Take a selected stretch of time out of the score, which is MuseScore's Ctrl+Delete.

    A measure the stretch covers from end to end goes, in every part. Where it covers part of a
    measure those notes go and the later ones in the voice are pulled in, as closing a gap does.
    """
    fit_divisions(root)
    note_ids = own_ids(root, note_ids)
    chosen = set(note_ids)
    whole: set[int] = set()
    seen: set[tuple[int, tuple[str, str]]] = set()
    handled: set[str | None] = set()
    for note_id in note_ids:
        if note_id in handled:
            continue  # It went with its measure's others, and a rest written since may have taken its id.
        spot = locate(root, note_id)
        place = (spot.context.index, spot.key)
        if place in seen:
            continue
        seen.add(place)
        gone = [event for event in spot.line if any(note.get("id") in chosen for note in event.notes)]
        handled.update(note.get("id") for event in gone for note in event.notes)
        if len(gone) == len(spot.line):
            whole.add(spot.context.index)
            continue
        if any(event.in_tuplet for event in gone):
            raise EditError("Notes in a triplet can't be removed this way yet.")
        for event in sorted(gone, key=lambda item: item.onset, reverse=True):
            spot.line.remove(event)
            for later in spot.line:
                if later.onset > event.onset:
                    later.onset -= event.length
        commit(root, spot)
    if not seen:
        raise EditError("Select what to remove first.")
    if len(whole) >= len(root.find("part").findall("measure")):
        raise EditError("A score needs at least one bar.")
    for index in sorted(whole, reverse=True):
        delete_measure(root, index)


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

    An octave keeps its accidental, because it's the same note name. MusicXML numbers octaves
    from 0 to 9, so a note can't go past them.
    """
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no pitch to move")
    part, context = context_of(root, note)
    absolute = STEPS.index(pitch.findtext("step")) + 7 * int(pitch.findtext("octave")) + steps
    if not 0 <= absolute // 7 <= 9:
        raise EditError("That's too far from the staff.")
    step = STEPS[absolute % 7]
    pitch.find("step").text = step
    pitch.find("octave").text = str(absolute // 7)
    if steps % 7:
        write_alter(pitch, alteration_in_key(step, context.fifths))
    respell(context)
    repair_ties(part, context.index)  # A tie can only join two notes of the same pitch.


def spell_sound(sound: int, fifths: int, rising: bool) -> Pitch:
    """Name a sound: as a note of the key if it is one, else a plain note, else a sharp on the way up or a flat on the way down.

    The sound is counted in semitones, twelve to the octave, from the C of octave 0.
    """
    semitone = sound % 12
    in_key = [(step, alteration_in_key(step, fifths)) for step in STEPS]
    plain = [(step, 0) for step in STEPS]
    for step, alter in [*in_key, *plain]:
        if (NATURAL_SEMITONES[step] + alter) % 12 == semitone:
            return step, alter, (sound - NATURAL_SEMITONES[step] - alter) // 12
    alter = 1 if rising else -1
    step = next(step for step in STEPS if NATURAL_SEMITONES[step] == (semitone - alter) % 12)
    return step, alter, (sound - NATURAL_SEMITONES[step] - alter) // 12


def move_semitone(root: ET.Element, note_id: str, change: int) -> None:
    """Move a note up or down by semitones, which is what the arrow keys do in MuseScore.

    Going up it's spelled with a sharp and going down with a flat, unless the key has a name for it.
    """
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        raise EditError("Rests have no pitch to move")
    part, context = context_of(root, note)
    sound = 12 * int(pitch.findtext("octave")) + NATURAL_SEMITONES[pitch.findtext("step")] + int(float(pitch.findtext("alter") or 0)) + change
    step, alter, octave = spell_sound(sound, context.fifths, change > 0)
    if not 0 <= octave <= 9:
        raise EditError("That's too far from the staff.")
    pitch.find("step").text = step
    pitch.find("octave").text = str(octave)
    write_alter(pitch, alter)
    respell(context)
    repair_ties(part, context.index)


def set_step(root: ET.Element, note_id: str, step: str) -> dict:
    """Rename a selected note by its letter, as typing a letter does in MuseScore outside note input.

    It takes the octave nearest where the note was, and the sharp or flat the key gives that letter.
    A rest becomes a note of its own length.
    """
    if step not in STEPS:
        raise EditError("A note is a letter from A to G.")
    note = find_note(root, note_id)
    pitch = note.find("pitch")
    if pitch is None:
        try:
            name, dots = type_of(locate(root, note_id).event.nominal)
        except EditError:
            name = None
        if name not in ENTERED_LENGTHS:
            raise EditError("Choose a duration for this rest first, or press N to enter notes.")
        return {"cursor": write_note(root, note_id, name, dots, step)["written"]}
    part, context = context_of(root, note)
    octave = round((height_of(note) - STEPS.index(step)) / 7)
    if not 0 <= octave <= 9:
        raise EditError("That's too far from the staff.")
    pitch.find("step").text = step
    pitch.find("octave").text = str(octave)
    write_alter(pitch, alteration_in_key(step, context.fifths))
    respell(context)
    repair_ties(part, context.index)
    return {}


LENGTH_ORDER = ["64th", "32nd", "16th", "eighth", "quarter", "half", "whole"]


def scale_length(root: ET.Element, note_id: str, longer: bool, by_dot: bool = False) -> None:
    """Double or halve a note's length, which is MuseScore's W and Q.

    With `by_dot` it goes one step along quarter, dotted quarter, half, as Shift does there.
    """
    try:
        name, dots = type_of(locate(root, note_id).event.nominal)
    except EditError:
        name, dots = "", 0
    if name not in LENGTH_ORDER:
        raise EditError("Choose a duration for this one first.")
    index = LENGTH_ORDER.index(name)
    if not by_dot:
        index += 1 if longer else -1
    elif longer:
        index, dots = (index, 1) if dots == 0 else (index + 1, 0)
    else:
        index, dots = (index - 1, 1) if dots == 0 else (index, 0)
    if not 0 <= index < len(LENGTH_ORDER):
        raise EditError("There's no longer note to use." if longer else "There's no shorter note to use.")
    change_length(root, note_id, LENGTH_ORDER[index], dots)


def add_interval(root: ET.Element, note_id: str, interval: int) -> str:
    """Add a note this interval above a chord's top note, or below its bottom one if it's negative.

    A third is 3, as in MuseScore, where Alt and a number adds that interval above.
    """
    if not 2 <= abs(interval) <= 9:
        raise EditError("An interval is a second to a ninth.")
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Choose a note to add an interval to.")
    heights = [height_of(note) for note in spot.event.notes]
    height = max(heights) + interval - 1 if interval > 0 else min(heights) + interval + 1
    line = spot.context.tops.get(spot.key[0], diatonic("F", 5)) - height + 7 * octaves_drawn_down(spot)
    return add_pitch(root, note_id, "", line=line)


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


def hand_on_marks(note: ET.Element, heir: ET.Element) -> None:
    """Move what a chord keeps on its first note to the note that's first now.

    That's its lyrics, beams, slurs and marks such as a fermata. A tie belongs to one pitch, so it
    goes with that pitch, and anything the new first note already has stays as it is.
    """
    notations = note.find("notations")
    if notations is not None:
        kept = heir.find("notations")
        if kept is None:
            kept = ET.Element("notations")
            place(heir, kept)
        for child in list(notations):
            existing = kept.find(child.tag)
            if child.tag in OWN_PITCH:
                continue
            notations.remove(child)
            if child.tag in MARK_GROUPS and existing is not None:
                existing.extend(mark for mark in list(child) if existing.find(mark.tag) is None)
            elif existing is None or child.tag == "slur":
                kept.append(child)
        if len(kept) == 0:
            heir.remove(kept)
    for tag in ("lyric", "beam"):
        if heir.find(tag) is None:
            for found in note.findall(tag):
                note.remove(found)
                place(heir, found)


def delete_note(root: ET.Element, note_id: str) -> None:
    """Remove a note. Its time has to stay filled, so a lone note becomes a rest.

    Only a staff's first voice has to fill its bars. An extra voice can simply stop, so one of its
    rests can be removed, and it leaves the bar with its last note.
    """
    note = find_note(root, note_id)
    if note.find("pitch") is None:
        delete_extra_rest(root, note_id)
        return
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
            hand_on_marks(note, second)
        context.measure.remove(note)
    else:
        for grace in spot.event.grace:
            context.measure.remove(grace)
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
    spot = empty_extra_voice(part, context)
    if spot is not None:
        drop_empty_voice(spot)
        write_measure(context.measure, spot.timeline, context.divisions)


def empty_extra_voice(part: ET.Element, context: Context) -> Spot | None:
    """Return an extra voice in this bar that holds only rests, if there is one."""
    timeline = read_measure(context.measure, context.divisions)
    for key, line in timeline.lines.items():
        if voices_on_staff(part, key[0]).index(key[1]) > 0 and all(event.is_rest for event in line):
            return Spot(part, context, timeline, key, line[0], line[0].notes[0])
    return None


def delete_extra_rest(root: ET.Element, note_id: str) -> None:
    spot = locate(root, note_id)
    if voices_on_staff(spot.part, spot.key[0]).index(spot.key[1]) == 0:
        raise EditError("This is already a rest")
    if spot.event.in_tuplet:
        raise EditError("Rests in a triplet can't be removed this way.")
    spot.line.remove(spot.event)
    if not spot.line or all(event.is_rest for event in spot.line):
        del spot.timeline.lines[spot.key]
    write_measure(spot.context.measure, spot.timeline, spot.context.divisions)
    respell(spot.context)


def make_tuplet(root: ET.Element, note_id: str, length: Fraction | None = None, keep_note: bool = True, actual: int = 3) -> dict:
    """Turn a length into a tuplet of rests, with any note already here as the first.

    Without a length, it divides the event at this place. The result says which note length the
    tuplet uses, because that's what the writer will want next.
    """
    if actual not in TUPLET_SIZES:
        raise EditError("That isn't a tuplet this app can write.")
    fit_divisions(root, actual)
    spot = locate_cursor(root, note_id)
    context, key, onset = spot.context, spot.key, spot.event.onset
    span = spot.event.length if length is None else length
    if spot.event.in_tuplet or not on_plain_grid(onset):
        raise EditError("Triplets inside triplets aren't supported.")
    if onset + span > context.length:
        raise EditError("That triplet is too long to fit in this bar.")
    tuplet = Tuplet.of(actual)
    written = span / tuplet.normal
    name, dots = type_of(written)
    if dots:
        raise EditError("A triplet needs a length without a dot.")
    tuplet = Tuplet.of(actual, name)
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


def toggle_tuplet(root: ET.Element, note_id: str, length: Fraction | None = None, actual: int = 3) -> dict:
    """Make a tuplet here, or undo the one that's already here."""
    if not note_id.startswith(AFTER) and locate(root, note_id).event.in_tuplet:
        return {"cursor": remove_tuplet(root, note_id)}
    return make_tuplet(root, note_id, length, actual=actual)


def lyric_of(note: ET.Element, verse: int = 1) -> ET.Element | None:
    return next((found for found in note.findall("lyric") if found.get("number", "1") == str(verse)), None)


def link_syllables(part: ET.Element, key: tuple[str, str], verse: int = 1) -> None:
    """Work out each syllable's place in its word, which depends on the syllables around it.

    A lyric with no text, such as the end of a line holding a syllable over several notes, isn't a
    syllable. One that leaves out its syllabic is a word of its own, as MusicXML allows.
    """
    joined = False
    for context in contexts(part):
        line = read_measure(context.measure, context.divisions).lines.get(key, [])
        for event in sorted(line, key=lambda item: item.onset):
            lyric = lyric_of(event.notes[0], verse)
            if lyric is None or lyric.find("text") is None:
                continue
            carries_on = lyric.findtext("syllabic") in ("begin", "middle")
            syllabic = lyric.find("syllabic")
            if syllabic is None:
                syllabic = ET.Element("syllabic")
                lyric.insert(list(lyric).index(lyric.find("text")), syllabic)
            syllabic.text = SYLLABLES[(joined, carries_on)]
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


def store_lyric(spot: Spot, text: str, joined: bool | None, verse: int = 1) -> None:
    """Put a word or syllable under a note. `joined` means the word carries on at the next note."""
    if spot.event.is_rest:
        raise EditError("Lyrics go under notes, not rests.")
    if not 1 <= verse <= MOST_VERSES:
        raise EditError(f"A note can have up to {MOST_VERSES} verses.")
    text = text.strip()
    if len(text) > MOST_LYRIC_LETTERS:
        raise EditError("That's too long for one note.")
    note = spot.event.notes[0]
    existing = lyric_of(note, verse)
    if joined is None:
        joined = existing is not None and existing.findtext("syllabic") in ("begin", "middle")
    if existing is not None:
        note.remove(existing)
    if text:
        lyric = ET.Element("lyric", number=str(verse))
        ET.SubElement(lyric, "syllabic").text = "begin" if joined else "single"
        ET.SubElement(lyric, "text").text = text
        # Verses stay in order under the note, because that's the order they're drawn in.
        later = [found for found in note.findall("lyric") if int(found.get("number", "1")) > verse]
        if later:
            note.insert(list(note).index(later[0]), lyric)
        else:
            place(note, lyric)
    link_syllables(spot.part, spot.key, verse)


def set_lyric(root: ET.Element, note_id: str, text: str, joined: bool | None = False, verse: int = 1) -> dict:
    """Store a syllable someone typed, and say which note comes next.

    `joined` of None keeps how it was joined. A word typed on the last note has nothing to join.
    """
    spot = locate(root, note_id)
    following = next_note_id(spot)
    store_lyric(spot, text, joined if following else False, verse)
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
                "actual": event.tuplet.actual if event.in_tuplet else 3,
                "chord": chord_on(context, event) if number == 0 else None,
                "lyric": None if lyric is None or number else {"text": lyric.findtext("text") or "", "joined": lyric.findtext("syllabic") in ("begin", "middle")},
                "source": event.notes[0].get("id") if number == 0 else None,
            })
    context, _, event = chosen[-1]
    part = locate(root, event.notes[0].get("id")).part
    key = locate(root, event.notes[0].get("id")).key
    after = next_id(part, context, key, event.end) or AFTER + event.notes[-1].get("id")
    return {"events": items, "next": after}


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
    placed: list[str] = []
    triplets_done: set[int] = set()
    for number, item in enumerate(items):
        triplet = item["triplet"]
        if triplet is not None and triplet not in triplets_done:
            triplets_done.add(triplet)
            span = sum((length_of(other["type"], other["dots"]) for other in items if other["triplet"] == triplet), Fraction(0))
            actual = item.get("actual", 3)
            cursor = make_tuplet(root, cursor, span * Tuplet.of(actual).scale, keep_note=False, actual=actual)["cursor"]
        spot = locate_cursor(root, cursor)
        first_bar = spot.context.index if first_bar is None else first_bar
        written = write_at(root, spot, length_of(item["type"], item["dots"]), checked_pitches(item))
        tie_together(tied, written.head)
        tied = written.tail if item["tie"] and number + 1 < len(items) else []
        cursor = written.cursor
        placed.append(written.head[0].get("id"))
        landed = locate(root, written.head[0].get("id"))
        if item["chord"]:
            set_chord_at(root, landed, item["chord"])
        if item["lyric"] and not landed.event.is_rest:
            store_lyric(locate(root, written.head[0].get("id")), item["lyric"]["text"], item["lyric"]["joined"])
    last_bar = locate(root, written.tail[0].get("id")).context.index
    for context in list(contexts(part))[first_bar : last_bar + 1]:
        respell(context)
    finish_edit(root, part, first_bar, last_bar)
    return {"written": written.tail[0].get("id"), "cursor": cursor, "placed": placed}


def clear_marks(root: ET.Element, note_id: str) -> None:
    """Remove fermatas, ornaments, articulations, bowings and a player's techniques, keeping slurs and ties.

    Chord text just above a staff is sometimes misread as one of these, about once a page. A dead
    note's x is one of a guitarist's techniques too, and its notehead becomes a plain one again.
    """
    from pipeline.technique import harmonic_kind, remove_harmonic  # It builds on this module.

    note = find_note(root, note_id)
    removed = 0
    for head in note.findall("notehead"):
        if (head.text or "").strip() == "x":
            note.remove(head)
            removed += 1
    if harmonic_kind(note) == ("artificial", "base-pitch"):
        remove_harmonic(root, note)  # With the diamond touched above it.
        removed += 1
    notations = note.find("notations")
    if notations is not None:
        for tag in ("fermata", "ornaments", "articulations", "technical", "arpeggiate", "slide", "glissando"):
            for child in notations.findall(tag):
                if tag == "technical" and child.find("fret") is not None:
                    # The string and fret a guitar's note is played at aren't marks.
                    marks = [mark for mark in child if mark.tag not in ("string", "fret")]
                    for mark in marks:
                        child.remove(mark)
                    removed += len(marks)
                    continue
                notations.remove(child)
                removed += 1
    if not removed:
        raise EditError("This note has no marks to clear")
    if notations is not None and len(notations) == 0:
        note.remove(notations)
    repair_slurs(context_of(root, find_note(root, note_id))[0], {"hammer-on", "pull-off", "wavy-line", "slide", "glissando"})  # Their other ends go too.


def list_chords(root: ET.Element) -> list[dict]:
    """List chords in the order they're drawn, which goes bar by bar through every part."""
    chords = []
    for part_number, part in enumerate(root.findall("part")):
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
                        "text": str(chord) if chord else "?",
                        "measure": measure_index,
                        "origin": origin_of(measure, measure_index),
                        "onset": onset,
                        "part": part_number,
                    })
                elif child.tag == "backup":
                    position -= length / divisions
                elif child.tag == "forward":
                    position += length / divisions
                elif child.tag == "note" and child.find("chord") is None and child.find("grace") is None:
                    position += length / divisions
    chords.sort(key=lambda chord: (chord["measure"], chord["part"]))
    return [chord | {"index": index} for index, chord in enumerate(chords)]


def require_chord(text: str):
    chord = parse_chord(text)
    if chord is None:
        raise EditError(f'"{text}" is not a chord symbol this app understands')
    return chord


def harmony_at(root: ET.Element, index: int) -> tuple[ET.Element, ET.Element]:
    placed = []
    for part_number, part in enumerate(root.findall("part")):
        for measure_index, measure in enumerate(part.findall("measure")):
            placed.extend((measure_index, part_number, measure, harmony) for harmony in measure.findall("harmony"))
    placed.sort(key=lambda item: item[:2])
    harmonies = [item[2:] for item in placed]
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
