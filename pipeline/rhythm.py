"""See a measure as timed events, so rhythm can change while every bar still adds up.

A MusicXML measure is a flat list: notes, a <backup> to jump back for the next voice, and
chord symbols sitting wherever they were written. That makes even a small rhythm change
risky, so an edit reads the measure into one timeline per voice, changes that, and writes the
whole measure out again. Positions are exact fractions of a quarter note, because a triplet
stored as a float would slowly stop adding up.

Only measures that are edited get rewritten, so the rest of a score stays as it was read.
"""

import xml.etree.ElementTree as ET
from collections.abc import Iterator
from dataclasses import dataclass, field
from fractions import Fraction
from math import lcm

# Every note length has to be a whole number of divisions. A dotted 64th needs 32 a quarter, and
# a triplet needs that to divide by 3.
SMALLEST_DIVISIONS = 96
LENGTHS = {
    "whole": Fraction(4),
    "half": Fraction(2),
    "quarter": Fraction(1),
    "eighth": Fraction(1, 2),
    "16th": Fraction(1, 4),
    "32nd": Fraction(1, 8),
    "64th": Fraction(1, 16),
    "128th": Fraction(1, 32),
}
# Children of a <note>, in the order the MusicXML schema requires. Stricter programs reject others.
NOTE_ORDER = [
    "grace", "cue", "chord", "pitch", "unpitched", "rest", "duration", "tie", "instrument",
    "footnote", "level", "voice", "type", "dot", "accidental", "time-modification", "stem",
    "notehead", "notehead-text", "staff", "beam", "notations", "lyric", "play", "listen",
]
SHARP_ORDER = "FCGDAEB"
STEPS = "CDEFGAB"
NATURAL_SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
FIFTHS_FROM_C = {"F": -1, "C": 0, "G": 1, "D": 2, "A": 3, "E": 4, "B": 5}
CLEF_PITCHES = {"G": ("G", 4), "F": ("F", 3), "C": ("C", 4)}
ACCIDENTAL_NAMES = {-2: "flat-flat", -1: "flat", 0: "natural", 1: "sharp", 2: "double-sharp"}
# These can sit between two notes' start times, because MusicXML gives them an offset.
SHIFTABLE = {"harmony", "direction"}
OFFSET_FOLLOWERS = {"footnote", "level", "voice", "staff", "sound"}


class EditError(ValueError):
    """The requested edit doesn't make sense, for example text that isn't a chord."""


@dataclass(frozen=True)
class Tuplet:
    """A group fitting more notes into a length than it would normally hold.

    A triplet of eighths is 3 notes in the time of 2 eighths, so its unit is "eighth". The unit
    is what fixes where a triplet ends, because a dotted note inside one can land on a beat.
    """

    actual: int = 3
    normal: int = 2
    unit: str | None = None

    @classmethod
    def of(cls, actual: int, unit: str | None = None) -> "Tuplet":
        """Make the usual tuplet of this many notes: 3 in the time of 2, 5 in the time of 4."""
        return cls(actual, 2 ** (actual.bit_length() - 1), unit)

    @property
    def scale(self) -> Fraction:
        """Return how long a note lasts, compared with how it's written."""
        return Fraction(self.normal, self.actual)


@dataclass
class Event:
    """One moment in a voice: a rest, a note, or several notes stacked as a chord."""

    onset: Fraction
    length: Fraction
    notes: list[ET.Element]
    grace: list[ET.Element] = field(default_factory=list)

    @property
    def end(self) -> Fraction:
        return self.onset + self.length

    @property
    def is_rest(self) -> bool:
        return self.notes[0].find("rest") is not None

    @property
    def in_tuplet(self) -> bool:
        return self.notes[0].find("time-modification") is not None

    @property
    def tuplet(self) -> Tuplet | None:
        found = self.notes[0].find("time-modification")
        if found is None:
            return None
        return Tuplet(int(found.findtext("actual-notes")), int(found.findtext("normal-notes")), found.findtext("normal-type"))

    @property
    def nominal(self) -> Fraction:
        """Return the length this event is written as, which in a triplet isn't how long it lasts."""
        return self.length / self.tuplet.scale if self.tuplet else self.length


@dataclass
class Mark:
    """Anything in a measure that isn't a note, with the moment it belongs to."""

    onset: Fraction
    element: ET.Element


@dataclass
class Timeline:
    """A measure taken apart. Each line is one voice, keyed by (staff, voice)."""

    lines: dict[tuple[str, str], list[Event]] = field(default_factory=dict)
    marks: list[Mark] = field(default_factory=list)
    head: list[ET.Element] = field(default_factory=list)
    tail: list[ET.Element] = field(default_factory=list)

    def events(self) -> list[Event]:
        return sorted((event for line in self.lines.values() for event in line), key=lambda event: event.onset)


@dataclass
class Context:
    """What's in force at a measure: everything needed to do arithmetic inside it."""

    index: int
    measure: ET.Element
    divisions: int
    beats: int
    beat_type: int
    fifths: int
    clefs: dict[str, str]
    staves: int
    tops: dict[str, int] = field(default_factory=dict)  # The height of each staff's top line.

    @property
    def length(self) -> Fraction:
        return Fraction(self.beats * 4, self.beat_type)

    @property
    def is_pickup(self) -> bool:
        return self.measure.get("implicit") == "yes"


def fifths_of(steps: int, semitones: int) -> int:
    """Return how far round the circle of fifths an interval moves a key."""
    step = STEPS[steps % 7]
    alter = semitones - NATURAL_SEMITONES[step] - 12 * (steps // 7)
    return FIFTHS_FROM_C[step] + 7 * alter


def diatonic(step: str, octave: int) -> int:
    """Number a pitch by its place on the staff, so one step up is always one more."""
    return STEPS.index(step) + 7 * octave


def top_line_of(clef: ET.Element) -> int:
    """Work out which pitch sits on a staff's top line, because a clef fixes one line's pitch."""
    sign = clef.findtext("sign") or "G"
    line = int(clef.findtext("line") or 2)
    octave_change = int(clef.findtext("clef-octave-change") or 0)
    return diatonic(*CLEF_PITCHES.get(sign, CLEF_PITCHES["G"])) + 2 * (5 - line) + 7 * octave_change


def contexts(part: ET.Element) -> Iterator[Context]:
    divisions, beats, beat_type, fifths, staves = 1, 4, 4, 0, 1
    clefs: dict[str, str] = {}
    tops: dict[str, int] = {}
    for index, measure in enumerate(part.findall("measure")):
        for attributes in measure.findall("attributes"):
            divisions = int(attributes.findtext("divisions") or divisions)
            fifths = int(attributes.findtext("key/fifths") or fifths)
            staves = int(attributes.findtext("staves") or staves)
            time = attributes.find("time")
            if time is not None and time.findtext("beats"):
                beats, beat_type = int(time.findtext("beats").split("+")[0]), int(time.findtext("beat-type"))
            for clef in attributes.findall("clef"):
                clefs[clef.get("number", "1")] = clef.findtext("sign") or "G"
                tops[clef.get("number", "1")] = top_line_of(clef)
        yield Context(index, measure, divisions, beats, beat_type, fifths, dict(clefs), staves, dict(tops))


def fit_divisions(root: ET.Element, also_divides_by: int = 1) -> None:
    """Rescale a score so that any length a person can enter is a whole number of divisions.

    A quintuplet needs that number to divide by 5, which most scores never need, so it's asked for.
    """
    for part in root.findall("part"):
        factor = 1
        for measure in part.findall("measure"):
            for attributes in measure.findall("attributes"):
                found = attributes.find("divisions")
                if found is not None:
                    current = int(found.text)
                    factor = lcm(current, SMALLEST_DIVISIONS, also_divides_by) // current
                    found.text = str(current * factor)
            if factor == 1:
                continue
            for element in measure.iter():
                if element.tag in ("note", "backup", "forward"):
                    duration = element.find("duration")
                    if duration is not None:
                        duration.text = str(Fraction(duration.text) * factor)
                elif element.tag == "offset":
                    element.text = str(Fraction(element.text) * factor)


def offset_of(element: ET.Element, divisions: int) -> Fraction:
    found = element.find("offset")
    return Fraction(found.text) / divisions if found is not None and found.text else Fraction(0)


def units(length: Fraction, divisions: int) -> str:
    value = length * divisions
    if value.denominator != 1:
        raise EditError("This rhythm is too fine for this score.")
    return str(value.numerator)


def read_measure(measure: ET.Element, divisions: int) -> Timeline:
    timeline = Timeline()
    position = Fraction(0)
    latest: Event | None = None
    waiting: dict[tuple[str, str], list[ET.Element]] = {}
    for child in measure:
        length = Fraction(child.findtext("duration") or 0) / divisions
        if child.tag == "backup":
            position -= length
        elif child.tag == "forward":
            position += length
        elif child.tag == "barline":
            (timeline.head if child.get("location") == "left" else timeline.tail).append(child)
        elif child.tag != "note":
            timeline.marks.append(Mark(position + offset_of(child, divisions), child))
        else:
            key = (child.findtext("staff") or "1", child.findtext("voice") or "1")
            if child.find("grace") is not None:
                waiting.setdefault(key, []).append(child)
            elif child.find("chord") is not None and latest is not None:
                latest.notes.append(child)
            else:
                latest = Event(position, length, [child], waiting.pop(key, []))
                timeline.lines.setdefault(key, []).append(latest)
                position += length
    for leftovers in waiting.values():  # Grace notes with no note after them.
        timeline.marks.extend(Mark(position, note) for note in leftovers)
    return timeline


def write_offset(element: ET.Element, offset: Fraction, divisions: int) -> None:
    for found in element.findall("offset"):
        element.remove(found)
    if offset == 0:
        return
    tag = ET.Element("offset")
    tag.text = units(offset, divisions)
    children = list(element)
    position = next((index for index, child in enumerate(children) if child.tag in OFFSET_FOLLOWERS), len(children))
    element.insert(position, tag)


def jump(tag: str, length: Fraction, divisions: int, key: tuple[str, str] | None) -> ET.Element:
    element = ET.Element(tag)
    ET.SubElement(element, "duration").text = units(length, divisions)
    if key is not None and tag == "forward":
        ET.SubElement(element, "voice").text = key[1]
        if key[0] != "1":
            ET.SubElement(element, "staff").text = key[0]
    return element


def write_measure(measure: ET.Element, timeline: Timeline, divisions: int) -> None:
    """Rebuild a measure, leaving each mark at its own moment even if the notes around it changed."""
    written: list[ET.Element] = list(timeline.head)
    position = Fraction(0)
    marks = sorted(timeline.marks, key=lambda mark: mark.onset)

    def move_to(onset: Fraction, key: tuple[str, str] | None) -> None:
        nonlocal position
        if onset > position:
            written.append(jump("forward", onset - position, divisions, key))
        elif onset < position:
            written.append(jump("backup", position - onset, divisions, key))
        position = onset

    def write_marks(fits) -> None:
        nonlocal marks
        for mark in [mark for mark in marks if fits(mark)]:
            marks.remove(mark)
            if mark.element.tag in SHIFTABLE:
                write_offset(mark.element, mark.onset - position, divisions)
            written.append(mark.element)

    for number, (key, line) in enumerate(timeline.lines.items()):
        for event in sorted(line, key=lambda event: event.onset):
            move_to(event.onset, key)
            if number == 0:
                write_marks(lambda mark: mark.onset <= event.onset or (mark.element.tag in SHIFTABLE and mark.onset < event.end))
            written.extend([*event.grace, *event.notes])
            position += event.length
    write_marks(lambda mark: True)
    written.extend(timeline.tail)

    for child in list(measure):
        measure.remove(child)
    measure.extend(written)


def line_end(line: list[Event]) -> Fraction:
    return max((event.end for event in line), default=Fraction(0))


def place(note: ET.Element, child: ET.Element) -> None:
    """Add a child to a note at the position the schema requires."""
    rank = NOTE_ORDER.index(child.tag)
    children = list(note)
    position = next((index for index, existing in enumerate(children) if NOTE_ORDER.index(existing.tag) > rank), len(children))
    note.insert(position, child)


def type_of(length: Fraction) -> tuple[str, int]:
    """Return the note type and number of dots for a length, if one exists."""
    for name, value in LENGTHS.items():
        if length == value:
            return name, 0
        if length == value * Fraction(3, 2):
            return name, 1
        if length == value * Fraction(7, 4):
            return name, 2
    raise EditError("That length can't be written as a single note.")


def note_lengths(total: Fraction) -> list[Fraction]:
    """Split a length into the fewest plain or dotted notes, longest first, to be tied together."""
    choices = sorted([*LENGTHS.values(), *(value * Fraction(3, 2) for value in LENGTHS.values())], reverse=True)
    found = []
    while total > 0:
        size = next((value for value in choices if value <= total), None)
        if size is None:
            raise EditError("This rhythm is too fine for this score.")
        found.append(size)
        total -= size
    return found


def set_length(note: ET.Element, length: Fraction, divisions: int, tuplet: Tuplet | None = None) -> None:
    """Rewrite how long a note says it is, leaving its pitch and markings alone.

    In a triplet, the length is what's written, and the note lasts two thirds of that.
    """
    name, dots = type_of(length)
    note.find("duration").text = units(length * tuplet.scale if tuplet else length, divisions)
    for tag in ("type", "dot", "time-modification"):
        for found in note.findall(tag):
            note.remove(found)
    kind = ET.Element("type")
    kind.text = name
    place(note, kind)
    for _ in range(dots):
        place(note, ET.Element("dot"))
    if tuplet is not None:
        modification = ET.Element("time-modification")
        ET.SubElement(modification, "actual-notes").text = str(tuplet.actual)
        ET.SubElement(modification, "normal-notes").text = str(tuplet.normal)
        if tuplet.unit:
            ET.SubElement(modification, "normal-type").text = tuplet.unit
        place(note, modification)
    rest = note.find("rest")
    if rest is not None:
        rest.attrib.pop("measure", None)


def make_note(length: Fraction, divisions: int, key: tuple[str, str], pitch: tuple[str, int, int] | None, tuplet: Tuplet | None = None) -> ET.Element:
    """Make a note, or a rest when there's no pitch."""
    note = ET.Element("note")
    if pitch is None:
        ET.SubElement(note, "rest")
    else:
        step, alter, octave = pitch
        written = ET.SubElement(note, "pitch")
        ET.SubElement(written, "step").text = step
        if alter:
            ET.SubElement(written, "alter").text = str(alter)
        ET.SubElement(written, "octave").text = str(octave)
    ET.SubElement(note, "duration").text = "0"
    ET.SubElement(note, "voice").text = key[1]
    if key[0] != "1":
        ET.SubElement(note, "staff").text = key[0]
    set_length(note, length, divisions, tuplet)
    return note


def make_bar_rest(length: Fraction, divisions: int, key: tuple[str, str]) -> ET.Element:
    """Make a rest that fills a whole bar, whatever the time signature."""
    note = ET.Element("note")
    ET.SubElement(note, "rest", measure="yes")
    ET.SubElement(note, "duration").text = units(length, divisions)
    ET.SubElement(note, "voice").text = key[1]
    if key[0] != "1":
        ET.SubElement(note, "staff").text = key[0]
    return note


def rest_lengths(start: Fraction, end: Fraction, context: Context) -> list[Fraction]:
    """Split a gap into the rests a reader expects, each starting on a multiple of itself."""
    choices = sorted(LENGTHS.values(), reverse=True)
    if context.beat_type == 8 and context.beats % 3 == 0:
        choices = sorted([*choices, Fraction(3), Fraction(3, 2), Fraction(3, 4)], reverse=True)
    found = []
    while start < end:
        size = next((value for value in choices if value <= end - start and start % value == 0), None)
        if size is None:
            raise EditError("This bar's rhythm is too unusual to fill with rests.")
        found.append(size)
        start += size
    return found


def rests(start: Fraction, end: Fraction, context: Context, key: tuple[str, str]) -> list[Event]:
    if start == 0 and end == context.length:
        return [Event(start, end, [make_bar_rest(end, context.divisions, key)])]
    made = []
    for length in rest_lengths(start, end, context):
        made.append(Event(start, length, [make_note(length, context.divisions, key, None)]))
        start += length
    return made


def on_plain_grid(position: Fraction) -> bool:
    """Say whether ordinary note lengths can reach this position, which a triplet's inner notes can't."""
    return position.denominator & (position.denominator - 1) == 0


def tuplet_run(line: list[Event], event: Event) -> list[Event]:
    """Return the unbroken stretch of matching triplet notes around this event."""
    ordered = sorted(line, key=lambda item: item.onset)
    index = next(number for number, item in enumerate(ordered) if item is event)
    first = last = index
    while first > 0 and ordered[first - 1].tuplet == event.tuplet and ordered[first - 1].end == ordered[first].onset:
        first -= 1
    while last + 1 < len(ordered) and ordered[last + 1].tuplet == event.tuplet and ordered[last].end == ordered[last + 1].onset:
        last += 1
    return ordered[first : last + 1]


def tuplet_span(run: list[Event]) -> Fraction:
    """Return how long one triplet in this stretch lasts.

    Files from elsewhere often don't say what a triplet counts in, so take the shortest note.
    """
    tuplet = run[0].tuplet
    unit = LENGTHS[tuplet.unit] if tuplet.unit in LENGTHS else min(item.nominal for item in run)
    return unit * tuplet.normal


def tuplet_bounds(line: list[Event], event: Event) -> tuple[Fraction, Fraction]:
    """Return where the triplet holding this event starts and ends."""
    run = tuplet_run(line, event)
    span = tuplet_span(run)
    start = run[0].onset + (event.onset - run[0].onset) // span * span
    return start, start + span


def tuplet_rests(start: Fraction, end: Fraction, tuplet: Tuplet, context: Context, key: tuple[str, str]) -> list[Event]:
    """Fill a gap inside a triplet with rests that are still part of it."""
    made = []
    for written in note_lengths((end - start) / tuplet.scale):
        length = written * tuplet.scale
        made.append(Event(start, length, [make_note(written, context.divisions, key, None, tuplet)]))
        start += length
    return made


def overwrite_in_tuplet(line: list[Event], new: Event, tuplet: Tuplet, context: Context, key: tuple[str, str]) -> list[Event]:
    """Put a note inside a triplet. Whatever it no longer covers stays in the triplet, as rests."""
    kept: list[Event] = []
    for event in sorted(line, key=lambda event: event.onset):
        if event.end <= new.onset or event.onset >= new.end:
            kept.append(event)
            continue
        if event.onset < new.onset:
            kept.extend(tuplet_rests(event.onset, new.onset, tuplet, context, key))
        if event.end > new.end:
            kept.extend(tuplet_rests(new.end, event.end, tuplet, context, key))
    kept.append(new)
    return sorted(kept, key=lambda event: event.onset)


def repair_tuplets(context: Context) -> None:
    """Mark where each triplet in a bar starts and stops, which is what draws its bracket."""
    timeline = read_measure(context.measure, context.divisions)
    for line in timeline.lines.values():
        ordered = sorted(line, key=lambda event: event.onset)
        for event in ordered:
            if event.in_tuplet:
                for note in event.notes:
                    set_tuplet_mark(note, None)
        seen: set[Fraction] = set()
        for event in ordered:
            if not event.in_tuplet:
                continue
            start, end = tuplet_bounds(line, event)
            if start in seen:
                continue
            seen.add(start)
            members = [item for item in ordered if item.in_tuplet and start <= item.onset < end]
            if len(members) > 1:
                set_tuplet_mark(members[0].notes[0], "start")
                set_tuplet_mark(members[-1].notes[0], "stop")


def set_tuplet_mark(note: ET.Element, kind: str | None) -> None:
    notations = note.find("notations")
    if notations is not None:
        for found in notations.findall("tuplet"):
            notations.remove(found)
        if len(notations) == 0:
            note.remove(notations)
            notations = None
    if kind is None:
        return
    if notations is None:
        notations = ET.Element("notations")
        place(note, notations)
    notations.append(ET.Element("tuplet", {"type": kind, "bracket": "yes"} if kind == "start" else {"type": kind}))


def overwrite(line: list[Event], new: Event, context: Context, key: tuple[str, str]) -> list[Event]:
    """Put an event into a voice. Anything it covers goes, and leftover time becomes rests.

    A triplet can only be replaced whole, because part of one wouldn't add up to anything.
    """
    kept: list[Event] = []
    for event in sorted(line, key=lambda event: event.onset):
        if event.end <= new.onset or event.onset >= new.end:
            kept.append(event)
            continue
        if event.in_tuplet and (event.onset < new.onset or event.end > new.end):
            raise EditError("Triplets can't be cut in half.")
        if event.onset < new.onset:
            kept.extend(rests(event.onset, new.onset, context, key))
        if event.end > new.end:
            kept.extend(rests(new.end, event.end, context, key))
    kept.append(new)
    return sorted(kept, key=lambda event: event.onset)


def alteration_in_key(step: str, fifths: int) -> int:
    if fifths > 0 and step in SHARP_ORDER[:fifths]:
        return 1
    if fifths < 0 and step in SHARP_ORDER[::-1][:-fifths]:
        return -1
    return 0


def is_tied(note: ET.Element, kind: str) -> bool:
    return any(tie.get("type") == kind for tie in note.findall("tie"))


def respell(context: Context) -> None:
    """Work out which accidentals to print in a measure.

    A note only needs one if it differs from what's in force at that point. An earlier F sharp
    stays in force until the barline, so a later plain F needs a natural, which the key alone
    wouldn't show. A sharp tied over the barline holds for the tied note alone, so the next F in
    that bar shows its accidental, whatever it is.
    """
    in_force: dict[tuple[str, str, str], int | None] = {}
    timeline = read_measure(context.measure, context.divisions)
    for event in timeline.events():
        for note in [*event.grace, *event.notes]:
            pitch = note.find("pitch")
            if pitch is None:
                continue
            step, octave = pitch.findtext("step"), pitch.findtext("octave")
            alter = int(float(pitch.findtext("alter") or 0))
            where = (note.findtext("staff") or "1", step, octave)
            expected = in_force.get(where, alteration_in_key(step, context.fifths))
            for accidental in note.findall("accidental"):
                note.remove(accidental)
            if is_tied(note, "stop"):
                if where not in in_force and alter != expected:
                    in_force[where] = None  # Nothing a later note could match, so it shows its own.
                continue
            if alter != expected and alter in ACCIDENTAL_NAMES:
                accidental = ET.Element("accidental")
                accidental.text = ACCIDENTAL_NAMES[alter]
                place(note, accidental)
            in_force[where] = alter


def same_pitch(first: ET.Element, second: ET.Element) -> bool:
    return all(first.findtext(f"pitch/{tag}", "0") == second.findtext(f"pitch/{tag}", "0") for tag in ("step", "alter", "octave"))


def set_tie(note: ET.Element, kind: str, wanted: bool) -> None:
    """Add or remove one end of a tie, which MusicXML stores in two places."""
    for tie in note.findall("tie"):
        if tie.get("type") == kind:
            note.remove(tie)
    notations = note.find("notations")
    if notations is not None:
        for tied in notations.findall("tied"):
            if tied.get("type") == kind:
                notations.remove(tied)
        if len(notations) == 0:
            note.remove(notations)
    if wanted:
        place(note, ET.Element("tie", type=kind))
        notations = note.find("notations")
        if notations is None:
            notations = ET.Element("notations")
            place(note, notations)
        notations.insert(0, ET.Element("tied", type=kind))


def notes_starting(timeline: Timeline, onset: Fraction) -> list[ET.Element]:
    return [note for line in timeline.lines.values() for event in line if event.onset == onset for note in event.notes]


def notes_ending(timeline: Timeline, end: Fraction) -> list[ET.Element]:
    return [note for line in timeline.lines.values() for event in line if event.end == end for note in event.notes]


def bar_ending_notes(timeline: Timeline) -> list[ET.Element]:
    return [note for line in timeline.lines.values() if line for note in max(line, key=lambda event: event.end).notes]


def repair_ties(part: ET.Element, index: int) -> None:
    """Remove half-ties around an edited measure, left when the note at one end has gone.

    A tie joins two sounds, so its other end is whatever sounds at that moment, in any voice. The
    next note in the same voice won't do: that voice may not play again for bars.
    """
    known = list(contexts(part))
    timelines = {number: read_measure(known[number].measure, known[number].divisions) for number in range(max(index - 2, 0), min(index + 3, len(known)))}
    for number in range(max(index - 1, 0), min(index + 2, len(known))):
        timeline = timelines[number]
        for line in timeline.lines.values():
            ordered = sorted(line, key=lambda event: event.onset)
            for position, event in enumerate(ordered):
                if position + 1 < len(ordered):
                    after = [*ordered[position + 1].notes, *notes_starting(timeline, event.end)]
                else:
                    after = notes_starting(timelines[number + 1], Fraction(0)) if number + 1 in timelines else []
                if position:
                    before = [*ordered[position - 1].notes, *notes_ending(timeline, event.onset)]
                else:
                    before = bar_ending_notes(timelines[number - 1]) if number - 1 in timelines else []
                for note in event.notes:
                    if is_tied(note, "start") and not any(same_pitch(note, other) and is_tied(other, "stop") for other in after):
                        set_tie(note, "start", False)
                    if is_tied(note, "stop") and not any(same_pitch(note, other) and is_tied(other, "start") for other in before):
                        set_tie(note, "stop", False)


def repair_slurs(part: ET.Element) -> None:
    """Remove slurs that have lost their start or their end."""
    waiting: dict[str, tuple[ET.Element, ET.Element]] = {}
    unmatched = []
    for note in part.iter("note"):
        for notations in note.findall("notations"):
            for slur in notations.findall("slur"):
                number = slur.get("number", "1")
                if slur.get("type") == "start":
                    if number in waiting:
                        unmatched.append(waiting[number])
                    waiting[number] = (notations, slur)
                elif slur.get("type") == "stop":
                    if number in waiting:
                        del waiting[number]
                    else:
                        unmatched.append((notations, slur))
    for notations, slur in [*unmatched, *waiting.values()]:
        notations.remove(slur)


def repair_wedges(part: ET.Element) -> None:
    """Remove hairpins that have lost their start or their end."""
    waiting: dict[str, tuple[ET.Element, ET.Element]] = {}
    unmatched = []
    for measure in part.findall("measure"):
        for direction in measure.findall("direction"):
            for wedge in direction.iter("wedge"):
                number = wedge.get("number", "1")
                if wedge.get("type") == "stop":
                    if number in waiting:
                        del waiting[number]
                    else:
                        unmatched.append((measure, direction))
                elif wedge.get("type") in ("crescendo", "diminuendo"):
                    if number in waiting:
                        unmatched.append(waiting[number])
                    waiting[number] = (measure, direction)
    for measure, direction in [*unmatched, *waiting.values()]:
        measure.remove(direction)
