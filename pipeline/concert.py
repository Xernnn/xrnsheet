"""Show a score at the pitch it sounds, as MuseScore's Concert pitch does, and edit it there.

A score is kept as it's written for its players. MusicXML stores a B flat trumpet's notes a tone
above how they sound, with a <transpose> saying how far apart the two are. At concert pitch each
transposing part is shown as it sounds instead, and its key and chord symbols move with it. An
instrument written an octave or two from how it sounds, such as a double bass or a piccolo, keeps
its notes where they stand and takes an octave clef, which is what MuseScore shows for it too.

An edit made at concert pitch is made to the score as it's shown, which is then moved back to how
it's written. Whatever the edit didn't touch gets back exactly what it had. Going there and back
can't always keep a spelling: a clarinet's written C double flat would need a triple flat at
concert pitch. So an edit to one note mustn't respell the rest, and nor may it drop a courtesy
accidental in a bar it never changed.
"""

import copy
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from pipeline.rhythm import EditError, contexts, is_tied, place, read_measure, respell
from pipeline.structure import CLEF_SHAPES, MOST_KEY, at_sounding_pitch, instrument_of, read_setting, write_setting
from pipeline.transpose import SAME_SOUND, Interval, move_chord, move_pitch

CONCERT_PITCH = "xrnsheet-concert-pitch"
NO_INTERVAL = Interval(0, 0)
OCTAVE_SIGNS = {"G", "F"}  # Clefs that can be drawn with an 8 or a 15 above or below.
MOST_CLEF_OCTAVES = 2
LOWEST_OCTAVE, HIGHEST_OCTAVE = 0, 9
Pitch = tuple[str, int, int]


def shown_at_concert_pitch(root: ET.Element) -> bool:
    return read_setting(root, CONCERT_PITCH) == "yes"


def set_concert_pitch(root: ET.Element, on: bool) -> None:
    """Show the score at concert pitch, or as written. It's kept with the score, as MuseScore keeps it."""
    write_setting(root, CONCERT_PITCH, "yes" if on else None)


def interval_of(transpose: ET.Element) -> Interval:
    """Read how far a part sounds from how it's written, octaves included."""
    octaves = int(transpose.findtext("octave-change") or 0)
    steps = int(transpose.findtext("diatonic") or 0) + 7 * octaves
    semitones = round(float(transpose.findtext("chromatic") or 0)) + 12 * octaves
    return Interval(steps, semitones)


def transpositions(part: ET.Element) -> list[Interval]:
    """Return how far each bar of a part sounds from how it's written. Inside an edit at concert pitch it's nothing."""
    found = []
    current = NO_INTERVAL
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            transpose = attributes.find("transpose")
            if transpose is not None:
                current = interval_of(transpose)
        found.append(current)
    return found


def transposition_at(part: ET.Element, index: int) -> Interval:
    """How far one bar of a part sounds from how it's written, as edits must see it.

    While a score is being edited at concert pitch its notes already stand where they sound.
    """
    if at_sounding_pitch.get():
        return NO_INTERVAL
    found = transpositions(part)
    return found[min(index, len(found) - 1)] if found else NO_INTERVAL


def is_transposing(root: ET.Element) -> bool:
    return any(interval != NO_INTERVAL for part in root.findall("part") for interval in transpositions(part))


def readable(interval: Interval, keys: list[int]) -> Interval:
    """Spell a transposition so every key it makes can be written: D sharp major would need nine sharps."""
    def worst(choice: Interval) -> int:
        return max((abs(fifths + choice.fifths) for fifths in keys), default=0)

    if worst(interval) <= MOST_KEY:
        return interval
    return min((interval, interval + SAME_SOUND, interval + -SAME_SOUND), key=worst)


def wrapped(fifths: int) -> int:
    """Bring a key past seven sharps or flats back to the same sound written the other way."""
    return fifths - 12 * (1 if fifths > 0 else -1) if abs(fifths) > MOST_KEY else fifths


def pitch_of(note: ET.Element) -> Pitch | None:
    pitch = note.find("pitch")
    if pitch is None:
        return None
    return pitch.findtext("step"), round(float(pitch.findtext("alter") or 0)), int(pitch.findtext("octave"))


def write_pitch(note: ET.Element, value: Pitch) -> None:
    pitch = note.find("pitch")
    step, alter, octave = value
    pitch.find("step").text = step
    pitch.find("octave").text = str(octave)
    existing = pitch.find("alter")
    if alter and existing is None:
        existing = ET.Element("alter")
        pitch.insert(1, existing)
    if alter:
        existing.text = str(alter)
    elif existing is not None:
        pitch.remove(existing)


def chord_pitches(harmony: ET.Element) -> tuple:
    return tuple(harmony.findtext(path) for path in ("root/root-step", "root/root-alter", "bass/bass-step", "bass/bass-alter"))


def restore_chord(harmony: ET.Element, original: ET.Element) -> None:
    for tag in ("root", "bass"):
        for found in harmony.findall(tag):
            position = list(harmony).index(found)
            harmony.remove(found)
            kept = original.find(tag)
            if kept is not None:
                harmony.insert(position, copy.deepcopy(kept))


def clefs_of(part: ET.Element) -> list[ET.Element]:
    """Return a part's clefs that stand for pitches. A tablature staff's lines are strings, whatever the pitch."""
    return [clef for measure in part.findall("measure") for attributes in measure.findall("attributes") for clef in attributes.findall("clef") if clef.findtext("sign") != "TAB"]


def octave_change(clef: ET.Element) -> int:
    return int(clef.findtext("clef-octave-change") or 0)


def shape_of(clef: ET.Element) -> tuple[str | None, str | None, int]:
    return clef.findtext("sign"), clef.findtext("line"), octave_change(clef)


def set_shape(clef: ET.Element, shape: tuple[str | None, str | None, int]) -> None:
    """Draw a clef as another: its sign, its line and its octaves, in the order MusicXML wants them."""
    for tag in ("sign", "line", "clef-octave-change"):
        for found in clef.findall(tag):
            clef.remove(found)
    sign, line, octaves = shape
    position = 0
    for tag, text in (("sign", sign), ("line", line), ("clef-octave-change", str(octaves) if octaves else None)):
        if text is not None:
            element = ET.Element(tag)
            element.text = text
            clef.insert(position, element)
            position += 1


def concert_shapes(part: ET.Element, entry: ET.Element | None, intervals: list[Interval]) -> dict[ET.Element, tuple]:
    """Return how each of a part's clefs is drawn at concert pitch.

    MuseScore gives a few instruments a clef of their own there, such as a bass clef for a baritone
    saxophone, at the start of the part. Otherwise a part's clefs take on its octaves.
    """
    clefs = clefs_of(part)
    instrument = instrument_of(entry)
    if instrument is not None and instrument.concert_clefs:
        opening = {clef.get("number", "1"): clef for clef in clefs[: len(instrument.concert_clefs)]}
        shapes = {clef: shape_of(clef) for clef in clefs}
        for number, name in enumerate(instrument.concert_clefs, start=1):
            if str(number) in opening:
                sign, line, octaves = CLEF_SHAPES[name]
                shapes[opening[str(number)]] = (sign, line or None, octaves)
        return shapes
    octaves = clef_octaves(part, intervals)
    return {clef: (*shape_of(clef)[:2], octave_change(clef) + octaves) for clef in clefs}


def clef_octaves(part: ET.Element, intervals: list[Interval]) -> int:
    """Return how many octaves of a part's transposition its clefs take on at concert pitch.

    A bass guitar sounds an octave below how it's written, and at concert pitch it's shown with a
    bass clef that has an 8 below it, so its notes stay where a bass player reads them. A part whose
    clefs can't show that, or whose octaves change partway, is moved the whole way instead.
    """
    octaves = {int(interval.steps / 7) for interval in intervals if interval != NO_INTERVAL}
    if len(octaves) != 1:
        return 0
    shift = octaves.pop()
    for clef in clefs_of(part):
        if clef.findtext("sign") not in OCTAVE_SIGNS or abs(octave_change(clef) + shift) > MOST_CLEF_OCTAVES:
            return 0
    return shift


def signature(measure: ET.Element, divisions: int, fifths: int) -> tuple:
    """What decides a bar's accidentals: its key, and each note's moment, pitch, tie and printed accidental."""
    found = []
    for event in read_measure(measure, divisions).events():
        for note in [*event.grace, *event.notes]:
            found.append((event.onset, note.get("id"), note.findtext("staff"), pitch_of(note), tuple(item.text for item in note.findall("accidental")), is_tied(note, "stop")))
    return fifths, tuple(found)


@dataclass
class Moved:
    """What a trip to concert pitch changed, so the trip back can give it all back."""

    notes: dict[ET.Element, tuple[Pitch, Pitch, list[ET.Element]]] = field(default_factory=dict)  # Written, sounding, printed accidentals.
    keys: dict[ET.Element, tuple[int, int]] = field(default_factory=dict)  # Written and sounding fifths.
    clefs: dict[ET.Element, tuple[tuple, tuple]] = field(default_factory=dict)  # Written and shown sign, line and octaves.
    chords: dict[ET.Element, tuple[ET.Element, tuple]] = field(default_factory=dict)  # A copy as written, and its pitches as shown.
    bars: dict[ET.Element, tuple] = field(default_factory=dict)  # Each moved bar's signature as shown.


def part_moves(part: ET.Element, sounding: bool) -> list[Interval]:
    """Return how far to move each bar of a part: to how it sounds, or back to how it's written.

    The transposition is spelled so the keys it lands on can be written.
    """
    intervals = [interval if sounding else -interval for interval in transpositions(part)]
    keys: dict[Interval, list[int]] = {}
    for context, interval in zip(contexts(part), intervals):
        if interval != NO_INTERVAL:
            keys.setdefault(interval, []).append(context.fifths)
    spelled = {interval: readable(interval, found) for interval, found in keys.items()}
    return [spelled.get(interval, NO_INTERVAL) for interval in intervals]


def key_elements(measure: ET.Element) -> list[ET.Element]:
    return [key for attributes in measure.findall("attributes") for key in attributes.findall("key") if key.find("fifths") is not None]


def to_sounding(root: ET.Element) -> Moved:
    """Move every transposing part to how it sounds, in place, and say what was moved."""
    moved = Moved()
    entries = {entry.get("id"): entry for entry in root.findall("part-list/score-part")}
    for part in root.findall("part"):
        moves = part_moves(part, sounding=True)
        if not any(move != NO_INTERVAL for move in moves):
            continue
        for clef, shape in concert_shapes(part, entries.get(part.get("id")), transpositions(part)).items():
            moved.clefs[clef] = (shape_of(clef), shape)
            set_shape(clef, shape)
        for measure, move in zip(part.findall("measure"), moves):
            if move == NO_INTERVAL:
                continue
            for key in key_elements(measure):
                written = int(key.findtext("fifths"))
                key.find("fifths").text = str(wrapped(written + move.fifths))
                moved.keys[key] = (written, int(key.findtext("fifths")))
            for note in measure.iter("note"):
                written = pitch_of(note)
                if written is None:
                    continue
                sounding = move_pitch(*written, move)
                write_pitch(note, sounding)
                moved.notes[note] = (written, sounding, [copy.deepcopy(found) for found in note.findall("accidental")])
            for harmony in measure.iter("harmony"):
                original = copy.deepcopy(harmony)
                move_chord(harmony, move)
                moved.chords[harmony] = (original, chord_pitches(harmony))
        for context, move in zip(contexts(part), moves):
            if move != NO_INTERVAL:
                respell(context)
                moved.bars[context.measure] = signature(context.measure, context.divisions, context.fifths)
    return moved


def to_written(root: ET.Element, moved: Moved) -> None:
    """Move a score edited at concert pitch back to how it's written, keeping all the edit left alone."""
    for part in root.findall("part"):
        moves = part_moves(part, sounding=False)
        if not any(move != NO_INTERVAL for move in moves):
            continue
        known = list(contexts(part))
        unchanged = {context.measure for context in known if moved.bars.get(context.measure) == signature(context.measure, context.divisions, context.fifths)}
        for clef in clefs_of(part):
            if clef in moved.clefs and shape_of(clef) == moved.clefs[clef][1]:
                set_shape(clef, moved.clefs[clef][0])
        for context, back in zip(known, moves):
            if back == NO_INTERVAL:
                continue
            for key in key_elements(context.measure):
                fifths = int(key.findtext("fifths"))
                was = moved.keys.get(key)
                key.find("fifths").text = str(was[0] if was and was[1] == fifths else wrapped(fifths + back.fifths))
            for note in context.measure.iter("note"):
                sounding = pitch_of(note)
                if sounding is None:
                    continue
                was = moved.notes.get(note)
                written = was[0] if was and was[1] == sounding else move_pitch(*sounding, back)
                if not LOWEST_OCTAVE <= written[2] <= HIGHEST_OCTAVE:
                    raise EditError("That note is too far out of this instrument's range to be written.")
                write_pitch(note, written)
            for harmony in context.measure.iter("harmony"):
                was = moved.chords.get(harmony)
                if was and was[1] == chord_pitches(harmony):
                    restore_chord(harmony, was[0])
                else:
                    move_chord(harmony, back)
        for context, move in zip(contexts(part), moves):
            if move == NO_INTERVAL:
                continue
            if context.measure not in unchanged:
                respell(context)
                continue
            for note in context.measure.iter("note"):
                if note in moved.notes:
                    for found in note.findall("accidental"):
                        note.remove(found)
                    for found in moved.notes[note][2]:
                        place(note, copy.deepcopy(found))


def shown(root: ET.Element) -> ET.Element:
    """Return the score as it's to be shown: at concert pitch if that's chosen, else as it is."""
    if not shown_at_concert_pitch(root):
        return root
    display = copy.deepcopy(root)
    to_sounding(display)
    return display


@contextmanager
def editing(root: ET.Element) -> Iterator[None]:
    """Edit a score as it's shown. At concert pitch the edit is made there, then moved back.

    Nothing is moved back if the edit fails, as a failed edit isn't saved.
    """
    if not shown_at_concert_pitch(root):
        yield
        return
    moved = to_sounding(root)
    token = at_sounding_pitch.set(True)
    try:
        yield
    finally:
        at_sounding_pitch.reset(token)
    to_written(root, moved)
