"""Transpose music: its notes, key signatures and chord symbols together.

An interval here counts letter names as well as semitones. A major second and a diminished third
are both 2 semitones, but only the first takes C sharp to D sharp, which is why a transposed
score reads as the same music and isn't full of accidentals.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass

from pipeline.edit import events_between, write_alter
from pipeline.rhythm import NATURAL_SEMITONES as SEMITONES
from pipeline.rhythm import STEPS, EditError, contexts, diatonic, fifths_of, read_measure, repair_ties, respell
from pipeline.structure import read_setting, write_setting

NATURALS = {semitones: step for step, semitones in SEMITONES.items()}
FLATTEST_READABLE_KEY = 6  # Seven sharps can be written, but five flats says the same thing.
MOST_FIFTHS = 7
MOST_ALTER = 2
LOWEST_OCTAVE, HIGHEST_OCTAVE = 0, 9
MEMO = "xrnsheet-transposed"  # Chords changed on purpose, which retraining has to know.


@dataclass(frozen=True)
class Interval:
    """A distance between two notes: how many letter names, and how many semitones."""

    steps: int
    semitones: int

    @property
    def fifths(self) -> int:
        """Return how far round the circle of fifths this moves a key."""
        return fifths_of(self.steps, self.semitones)

    def __add__(self, other: "Interval") -> "Interval":
        return Interval(self.steps + other.steps, self.semitones + other.semitones)

    def __neg__(self) -> "Interval":
        return Interval(-self.steps, -self.semitones)


INTERVALS = {
    "P1": Interval(0, 0), "m2": Interval(1, 1), "M2": Interval(1, 2), "m3": Interval(2, 3), "M3": Interval(2, 4),
    "P4": Interval(3, 5), "A4": Interval(3, 6), "d5": Interval(4, 6), "P5": Interval(4, 7), "m6": Interval(5, 8),
    "M6": Interval(5, 9), "m7": Interval(6, 10), "M7": Interval(6, 11), "P8": Interval(7, 12),
}
OCTAVE = INTERVALS["P8"]
SAME_SOUND = Interval(1, 0)  # C to D double flat: a new spelling of the same note.


def named_interval(name: str, direction: str = "up") -> Interval:
    if name not in INTERVALS or direction not in ("up", "down"):
        raise EditError("That isn't an interval this app knows.")
    return INTERVALS[name] if direction == "up" else -INTERVALS[name]


def interval_between_keys(start: int, target: int, direction: str = "closest") -> Interval:
    """Return the interval from one key to another, each given in sharps (flats are negative)."""
    change = target - start
    steps, semitones = change * 4 % 7, change * 7 % 12
    # Keep semitones in step with letter names: an augmented seventh is nearly an octave, not a unison.
    semitones += 12 * round((steps * 12 / 7 - semitones) / 12)
    found = Interval(steps, semitones)
    if direction == "up" and found.semitones < 0:
        return found + OCTAVE
    if (direction == "down" and found.semitones > 0) or (direction == "closest" and found.semitones > 6):
        return found + -OCTAVE
    return found


def spell_plainly(sound: int, flats: bool) -> tuple[str, int]:
    """Spell a pitch with at most one sharp or flat. `sound` counts semitones from a C."""
    if sound % 12 in NATURALS:
        return NATURALS[sound % 12], 0
    return (NATURALS[(sound + 1) % 12], -1) if flats else (NATURALS[(sound - 1) % 12], 1)


def move_pitch(step: str, alter: int, octave: int, interval: Interval) -> tuple[str, int, int]:
    height = diatonic(step, octave) + interval.steps
    new_step, new_octave = STEPS[height % 7], height // 7
    sound = 12 * octave + SEMITONES[step] + alter + interval.semitones
    new_alter = sound - 12 * new_octave - SEMITONES[new_step]
    if abs(new_alter) > MOST_ALTER:
        new_step, new_alter = spell_plainly(sound, flats=interval.semitones < 0)
        new_octave = (sound - new_alter - SEMITONES[new_step]) // 12
    return new_step, new_alter, new_octave


def move_note(note: ET.Element, interval: Interval) -> None:
    pitch = note.find("pitch")
    if pitch is None:
        return
    step, alter, octave = move_pitch(pitch.findtext("step"), round(float(pitch.findtext("alter") or 0)), int(pitch.findtext("octave")), interval)
    if not LOWEST_OCTAVE <= octave <= HIGHEST_OCTAVE:
        raise EditError("That would take notes too far off the staff.")
    pitch.find("step").text = step
    pitch.find("octave").text = str(octave)
    write_alter(pitch, alter)


def move_chord_pitch(element: ET.Element | None, prefix: str, interval: Interval) -> None:
    if element is None or element.findtext(f"{prefix}-step") is None:
        return
    start = element.findtext(f"{prefix}-step")
    alter = round(float(element.findtext(f"{prefix}-alter") or 0))
    new_step, new_alter, octave = move_pitch(start, alter, 4, interval)
    if abs(new_alter) > 1:  # Nobody writes a chord of F double sharp.
        new_step, new_alter = spell_plainly(12 * octave + SEMITONES[new_step] + new_alter, flats=new_alter < 0)
    element.find(f"{prefix}-step").text = new_step
    found = element.find(f"{prefix}-alter")
    if new_alter == 0 and found is not None:
        element.remove(found)
    elif new_alter:
        if found is None:
            found = ET.Element(f"{prefix}-alter")
            element.insert(1, found)
        found.text = str(new_alter)


def move_chord(harmony: ET.Element, interval: Interval) -> None:
    move_chord_pitch(harmony.find("root"), "root", interval)
    move_chord_pitch(harmony.find("bass"), "bass", interval)


def key_fifths(root: ET.Element) -> list[int]:
    return [int(found.text) for found in root.iter("fifths")]


def readable(interval: Interval, keys: list[int]) -> Interval:
    """Choose the spelling of an interval that leaves the keys easiest to read.

    A major third up from A is C sharp major, seven sharps, and D flat is the same sound in five
    flats. A key past seven can't be written at all, so it must take the other spelling.
    """
    def worst(choice: Interval) -> int:
        return max((abs(fifths + choice.fifths) for fifths in keys), default=0)

    best = interval
    if worst(best) > FLATTEST_READABLE_KEY:
        best = min((interval, interval + SAME_SOUND, interval + -SAME_SOUND), key=worst)
    if worst(best) > MOST_FIFTHS:
        raise EditError("This score's keys are too far apart to transpose that way.")
    return best


def add_memo(root: ET.Element, interval: Interval) -> None:
    """Remember what's been done to the chords, in case the sheet is later used for retraining."""
    written = read_setting(root, MEMO)
    total = (Interval(*map(int, written.split())) if written else Interval(0, 0)) + interval
    back_again = total.semitones % 12 == 0 and total.steps % 7 == 0
    write_setting(root, MEMO, None if back_again else f"{total.steps} {total.semitones}")


def is_transposed(root: ET.Element) -> bool:
    return read_setting(root, MEMO) is not None


def transpose_score(root: ET.Element, interval: Interval) -> None:
    """Transpose everything, key signatures included."""
    interval = readable(interval, key_fifths(root))
    for fifths in root.iter("fifths"):
        fifths.text = str(int(fifths.text) + interval.fifths)
    for note in root.iter("note"):
        move_note(note, interval)
    for harmony in root.iter("harmony"):
        move_chord(harmony, interval)
    for part in root.findall("part"):
        for context in contexts(part):
            respell(context)
    add_memo(root, interval)


def transpose_score_to_key(root: ET.Element, target: int, direction: str = "closest") -> None:
    if not -MOST_FIFTHS <= target <= MOST_FIFTHS:
        raise EditError("A key has at most seven sharps or flats.")
    keys = key_fifths(root)
    transpose_score(root, interval_between_keys(keys[0] if keys else 0, target, direction))


def transpose_stretch(root: ET.Element, first_id: str, last_id: str, interval: Interval) -> None:
    """Transpose part of one voice, and any chords over it. The key stays as it is."""
    start, chosen = events_between(root, first_id, last_id)
    bars = {}
    for context, _, event in chosen:
        bars[context.index] = context
        for note in [*event.grace, *event.notes]:
            move_note(note, interval)
        for mark in read_measure(context.measure, context.divisions).marks:
            if mark.element.tag == "harmony" and event.onset <= mark.onset < event.end:
                move_chord(mark.element, interval)
    for index, context in bars.items():
        respell(context)
        repair_ties(start.part, index)
    if any(mark.element.tag == "harmony" for context in bars.values() for mark in read_measure(context.measure, context.divisions).marks):
        add_memo(root, interval)
