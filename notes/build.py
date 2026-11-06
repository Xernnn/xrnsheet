"""Turn the note reader's tokens into MusicXML by applying the rules of notation.

The model only says what's printed. Pitch comes from here: a staff position plus the clef
gives a note name, the key signature adds its sharps or flats, and an accidental lasts until
the end of the measure. The training generator writes notes with these same functions, so a
test can prove that tokens and MusicXML agree exactly.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from notes.vocab import ACCIDENTALS, LENGTH_NAMES, Reading, key_alteration, length_of, position_to_pitch

DIVISIONS = 48
TRIPLET_SIZE = 3


def child(parent: ET.Element, tag: str, text: str | None = None, **attributes: str) -> ET.Element:
    element = ET.SubElement(parent, tag, attributes)
    element.text = text
    return element


def event_duration(code: str, triplet: bool) -> int:
    return int(length_of(code) * DIVISIONS * (2 / 3 if triplet else 1))


def write_shape(note: ET.Element, code: str, shown: str | None, triplet: bool) -> None:
    """Add the elements every note and rest shares, in the order MusicXML requires."""
    child(note, "duration", str(event_duration(code, triplet)))
    child(note, "type", LENGTH_NAMES[code.rstrip(".")])
    if code.endswith("."):
        child(note, "dot")
    if shown:
        child(note, "accidental", shown)
    if triplet:
        modification = child(note, "time-modification")
        child(modification, "actual-notes", "3")
        child(modification, "normal-notes", "2")


def add_note(measure: ET.Element, clef: str, position: int, code: str, alter: int, shown: str | None, triplet: bool) -> ET.Element:
    note = child(measure, "note")
    step, octave = position_to_pitch(clef, position)
    pitch = child(note, "pitch")
    child(pitch, "step", step)
    if alter:
        child(pitch, "alter", str(alter))
    child(pitch, "octave", str(octave))
    write_shape(note, code, shown, triplet)
    return note


def add_rest(measure: ET.Element, code: str, triplet: bool = False) -> ET.Element:
    note = child(measure, "note")
    child(note, "rest")
    write_shape(note, code, None, triplet)
    return note


def notations_of(note: ET.Element) -> ET.Element:
    found = note.find("notations")
    return found if found is not None else child(note, "notations")


@dataclass
class LineMarks:
    """Where one line's barlines and notes sit along its strip."""

    bars: list[float] = field(default_factory=list)
    notes: list[float] = field(default_factory=list)


class ScoreWriter:
    def __init__(self) -> None:
        self.root = ET.Element("score-partwise", version="4.0")
        score_part = child(child(self.root, "part-list"), "score-part", id="P1")
        child(score_part, "part-name", "")
        self.part = child(self.root, "part", id="P1")
        self.clef, self.fifths, self.time = "G", 0, "4/4"
        self.written: tuple[str, int, str] | None = None
        self.number = 0
        self.measure: ET.Element | None = None
        self.state: dict[tuple[str, int], int] = {}
        self.starting_line = True

    def write_attributes(self) -> None:
        """State only what changed, because each repeat would be printed again on the page."""
        before = self.written
        if before == (self.clef, self.fifths, self.time):
            return
        attributes = child(self.measure, "attributes")
        if before is None:
            child(attributes, "divisions", str(DIVISIONS))
        if before is None or before[1] != self.fifths:
            child(child(attributes, "key"), "fifths", str(self.fifths))
        if before is None or before[2] != self.time:
            beats, beat_type = self.time.split("/")
            time = child(attributes, "time")
            child(time, "beats", beats)
            child(time, "beat-type", beat_type)
        if before is None or before[0] != self.clef:
            clef = child(attributes, "clef")
            child(clef, "sign", self.clef)
            child(clef, "line", "2" if self.clef == "G" else "4")
        self.written = (self.clef, self.fifths, self.time)

    def open_measure(self) -> ET.Element:
        if self.measure is None:
            self.number += 1
            self.measure = child(self.part, "measure", number=str(self.number))
            if self.starting_line and self.number > 1:
                ET.SubElement(self.measure, "print", {"new-system": "yes"})
            self.starting_line = False
            self.write_attributes()
            self.state = {}
        return self.measure

    def close_measure(self) -> bool:
        closed = self.measure is not None
        self.measure = None
        return closed

    def resolve_alteration(self, position: int, accidental: str | None) -> tuple[int, str | None]:
        """Return the note's alteration, and the accidental to print, if any."""
        step, octave = position_to_pitch(self.clef, position)
        current = self.state.get((step, octave), key_alteration(self.fifths, step))
        if accidental is None:
            return current, None
        self.state[(step, octave)] = ACCIDENTALS[accidental]
        return ACCIDENTALS[accidental], accidental


def read_header(tokens: list[str], writer: ScoreWriter) -> int:
    """Apply the clef, key and time at the start of a line, and return where the music starts.

    A line that shows no key keeps the one in force, because missing a key signature is a
    likelier mistake than a piece changing key to C major.
    """
    for index, token in enumerate(tokens):
        kind, _, value = token.partition(":")
        if kind == "clef":
            writer.clef = value
        elif kind == "key":
            writer.fifths = int(value)
        elif kind == "time":
            writer.time = value
        else:
            return index
    return len(tokens)


def write_line(reading: Reading, writer: ScoreWriter) -> LineMarks | None:
    """Write one strip's music. Returns None if it held no music, as a false staff won't."""
    writer.starting_line = True
    start = read_header(reading.tokens, writer)
    marks = LineMarks()
    accidental = None
    group: list[ET.Element] = []
    triplet_left = 0
    for token, x in zip(reading.tokens[start:], reading.xs[start:]):
        kind, _, value = token.partition(":")
        if kind == "bar":
            if writer.close_measure():
                marks.bars.append(x)
            accidental, triplet_left = None, 0
        elif kind == "acc":
            accidental = value
        elif kind == "triplet":
            triplet_left, group = TRIPLET_SIZE, []
        elif kind in ("note", "rest"):
            measure = writer.open_measure()
            in_triplet = triplet_left > 0
            if kind == "rest":
                note = add_rest(measure, value, in_triplet)
            else:
                position_text, code = value.split(":")
                position = int(position_text)
                alter, shown = writer.resolve_alteration(position, accidental)
                note = add_note(measure, writer.clef, position, code, alter, shown, in_triplet)
                marks.notes.append(x)
                accidental = None
            if in_triplet:
                group.append(note)
                triplet_left -= 1
                if triplet_left == 0:
                    child(notations_of(group[0]), "tuplet", type="start")
                    child(notations_of(group[-1]), "tuplet", type="stop")
    writer.close_measure()
    return marks if marks.notes or marks.bars else None


def build_score(readings: list[Reading]) -> tuple[ET.Element, list[int], list[LineMarks]]:
    """Return the score, plus which readings held music and their marks."""
    writer = ScoreWriter()
    kept, all_marks = [], []
    for index, reading in enumerate(readings):
        before = writer.number
        marks = write_line(reading, writer)
        if marks is not None and writer.number > before:
            kept.append(index)
            all_marks.append(marks)
    return writer.root, kept, all_marks
