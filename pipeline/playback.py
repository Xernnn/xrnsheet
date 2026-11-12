"""Turn a score into timed notes the browser can play.

Hearing a score is the fastest way to catch a wrong note or chord, so chord symbols are played
too. Repeats, endings and D.C. and D.S. jumps are followed, and the marks that shape a performance
are heard: dynamics, hairpins, articulations, ornaments, fermatas, tempo changes, swing and the
sustain pedal. Notes under an 8va line need nothing done, as MusicXML gives them the pitch they sound.
"""

import re
import xml.etree.ElementTree as ET
from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction

from chords.vocab import QUALITIES
from pipeline.marks import ACCENTS, DYNAMICS
from pipeline.musicxml import chord_from_harmony
from pipeline.rhythm import STEPS, Context, Event, Timeline, alteration_in_key, contexts, is_tied, line_end, read_measure

DEFAULT_TEMPO = 100
MOST_PASSES = 16  # A file can ask for any number of repeats, and playback has to end.
START_LEVEL = 80  # Music with no dynamic plays at mezzo forte.
MOST_LEVEL, LEAST_LEVEL = 127, 12
HAIRPIN_CHANGE = 24  # About two dynamics, for a hairpin that doesn't lead to one.
HAIRPIN_REACH = 1.0  # A dynamic this soon after a hairpin is the one it leads to, in quarter notes.
ACCENT_THEN_SOFT = {"fp", "sfp"}
SHORTENED = {"staccatissimo": 0.25, "staccato": 0.5, "strong-accent": 0.75}
ACCENTED = {"strong-accent": 1.35, "accent": 1.25}
FERMATA_SPEED = 0.5  # A held note lasts about twice as long.
SWUNG_EIGHTH = Fraction(2, 3)  # Long-short, in a ratio of 2 to 1.
GRACE_NOTE = 0.125  # An acciaccatura takes about a 32nd note, in quarter notes.
ORNAMENT_NOTE = 0.125
ORNAMENTS = {"trill": "trill-mark", "mordent": "mordent", "prall": "inverted-mordent", "turn": "turn"}
ORNAMENT_SHAPES = {"mordent": (0, -1, 0), "prall": (0, 1, 0), "turn": (1, 0, -1, 0)}
SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
ROOT_OCTAVE_MIDI = 48  # Chords sit an octave below middle C, under most melodies.
# Semitones above the root, voiced simply, since the ear only has to judge the harmony.
CHORD_TONES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "m": (0, 3, 7),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "m7b5": (0, 3, 6, 10),
    "dim": (0, 3, 6),
    "dim7": (0, 3, 6, 9),
    "aug": (0, 4, 8),
    "7#5": (0, 4, 8, 10),
    "sus4": (0, 5, 7),
    "7sus4": (0, 5, 7, 10),
    "9sus4": (0, 5, 7, 10, 14),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "6/9": (0, 4, 7, 9, 14),
    "9": (0, 4, 7, 10, 14),
    "m9": (0, 3, 7, 10, 14),
    "maj9": (0, 4, 7, 11, 14),
    "11": (0, 5, 7, 10, 14, 17),
    "m11": (0, 3, 7, 10, 14, 17),
    "13": (0, 4, 7, 10, 14, 21),
    "7b9": (0, 4, 7, 10, 13),
    "7#9": (0, 4, 7, 10, 15),
    "7#11": (0, 4, 7, 10, 18),
    "7b13": (0, 4, 7, 10, 20),
    "7alt": (0, 4, 8, 10, 15),
    "13b9": (0, 4, 7, 10, 13, 21),
    "13#11": (0, 4, 7, 10, 18, 21),
    "maj7#11": (0, 4, 7, 11, 18),
    "m(maj7)": (0, 3, 7, 11),
    "add9": (0, 4, 7, 14),
}
assert set(CHORD_TONES) == set(QUALITIES)


def note_number(name: str, base: int) -> int:
    alter = {"b": -1, "#": 1}.get(name[1:], 0)
    return base + SEMITONES[name[0]] + alter


def chord_notes(chord) -> list[int]:
    root = note_number(chord.root, ROOT_OCTAVE_MIDI)
    tones = [root + interval for interval in CHORD_TONES[chord.quality]]
    if chord.bass:
        tones.insert(0, note_number(chord.bass, ROOT_OCTAVE_MIDI - 12))
    return tones


def midi_of(note: ET.Element) -> int:
    pitch = note.find("pitch")
    base = (int(pitch.findtext("octave")) + 1) * 12 + SEMITONES[pitch.findtext("step")]
    return base + round(float(pitch.findtext("alter") or 0))


def tempo_of(root: ET.Element) -> float:
    for sound in root.iter("sound"):
        if sound.get("tempo"):
            return float(sound.get("tempo"))
    found = root.findtext(".//metronome/per-minute")
    try:
        return float(found) if found else DEFAULT_TEMPO
    except ValueError:
        return DEFAULT_TEMPO


def endings_of(measure: ET.Element, kind: tuple[str, ...]) -> set[int]:
    """Return which passes an ending is for, read from a bar's barlines."""
    found: set[int] = set()
    for barline in measure.findall("barline"):
        for ending in barline.findall("ending"):
            if ending.get("type") in kind:
                found.update(int(number) for number in re.findall(r"\d+", ending.get("number", "")))
    return found


def repeats_back(measure: ET.Element) -> int:
    """Return how many times a bar's end repeat is played in all, or 0 if it hasn't one."""
    for barline in measure.findall("barline"):
        repeat = barline.find("repeat")
        if repeat is not None and repeat.get("direction") == "backward":
            return min(int(repeat.get("times") or 2), MOST_PASSES)
    return 0


def starts_repeat(measure: ET.Element) -> bool:
    return any(barline.find("repeat") is not None and barline.find("repeat").get("direction") == "forward" for barline in measure.findall("barline"))


def bar_sound(measure: ET.Element, attribute: str) -> bool:
    return any(sound.get(attribute) for sound in measure.iter("sound"))


def find_sound(measures: list[ET.Element], attribute: str) -> int | None:
    return next((index for index, measure in enumerate(measures) if bar_sound(measure, attribute)), None)


def jump_target(measures: list[ET.Element], index: int) -> int | None:
    """Return where a bar's D.C. or D.S. goes, if it has one that can be followed."""
    if bar_sound(measures[index], "dacapo"):
        return 0
    return find_sound(measures, "segno") if bar_sound(measures[index], "dalsegno") else None


def play_order(measures: list[ET.Element]) -> list[int]:
    """Return the bars in the order they're played, following repeats, endings and jumps.

    After a D.C. or D.S., repeats aren't taken again and only the last ending is played, which is
    how they're usually read. That's also when Fine and To Coda start to count.
    """
    ending_of: dict[int, set[int]] = {}
    last_of_ending: dict[int, int] = {}
    groups: list[list[int]] = []
    index = 0
    while index < len(measures):
        passes = endings_of(measures[index], ("start",))
        if passes:
            last = index
            while last + 1 < len(measures) and not endings_of(measures[last], ("stop", "discontinue")) and not endings_of(measures[last + 1], ("start",)):
                last += 1
            # An ending nothing repeats back to is never reached, so it's just played.
            if 1 in passes or any(repeats_back(measure) for measure in measures[:index]):
                if groups and groups[-1][-1] + 1 == index:
                    groups[-1].extend(range(index, last + 1))
                else:
                    groups.append(list(range(index, last + 1)))
                for number in range(index, last + 1):
                    ending_of[number], last_of_ending[number] = passes, last
            index = last
        index += 1
    final_pass = {number: max(max(ending_of[bar]) for bar in group) for group in groups for number in group}

    order: list[int] = []
    start, index, current_pass = 0, 0, 1
    played: dict[int, int] = {}
    jumped_from: set[int] = set()
    coda = find_sound(measures, "coda")
    while index < len(measures):
        after_jump = bool(jumped_from)
        if starts_repeat(measures[index]) and index != start and not after_jump:
            start, current_pass = index, 1
        wanted_pass = final_pass.get(index, 0) if after_jump else current_pass
        if index in ending_of and wanted_pass not in ending_of[index]:
            index = last_of_ending[index] + 1
            continue
        order.append(index)
        if after_jump:
            if bar_sound(measures[index], "fine"):
                break
            if coda is not None and coda > index and bar_sound(measures[index], "tocoda"):
                index = coda
                continue
        else:
            times = repeats_back(measures[index])
            played[index] = played.get(index, 0) + 1
            if times and played[index] < times:
                current_pass = played[index] + 1
                index = start
                continue
            if times or (index in ending_of and index == last_of_ending[index] and current_pass > 1):
                start, current_pass = index + 1, 1
        target = jump_target(measures, index)
        if target is not None and index not in jumped_from:
            jumped_from.add(index)
            index = target
            continue
        index += 1
    return order


def laid_out(part: ET.Element, order: list[int]) -> tuple[list[tuple[Context, Timeline, float]], float]:
    """Return each bar as it's played, with the moment it starts, and when the music ends."""
    known = list(contexts(part))
    found = []
    start = 0.0
    for index in order:
        if index >= len(known):
            continue
        timeline = read_measure(known[index].measure, known[index].divisions)
        found.append((known[index], timeline, start))
        longest = max((line_end(line) for line in timeline.lines.values()), default=0)
        start += float(longest or known[index].length)
    return found, start


def transposed_bars(part: ET.Element) -> list[int]:
    """Return how far each bar sounds from how it's written, for instruments such as a trumpet."""
    found = []
    shift = 0
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            transpose = attributes.find("transpose")
            if transpose is not None:
                shift = round(float(transpose.findtext("chromatic") or 0)) + 12 * int(transpose.findtext("octave-change") or 0)
        found.append(shift)
    return found


@dataclass
class Swell:
    """A stretch of the music's loudness: steady from `start`, or changing up to `end`."""

    start: float
    end: float
    before: float
    after: float


class Loudness:
    """How loud a part is at each moment, read from its dynamics and hairpins."""

    def __init__(self, bars: list[tuple[Context, Timeline, float]]) -> None:
        marks = []
        for _, timeline, start in bars:
            for mark in timeline.marks:
                if mark.element.tag == "direction":
                    marks.append((start + float(mark.onset), mark.element))
        marks.sort(key=lambda item: item[0])
        self.accents: dict[float, float] = {}
        self.swells = [Swell(0.0, 0.0, START_LEVEL, START_LEVEL)]
        level = START_LEVEL
        opened: dict[str, tuple[float, str, float]] = {}
        for position, element in marks:
            for found in element.iterfind("direction-type/dynamics/*"):
                if found.tag in DYNAMICS:
                    level = DYNAMICS[found.tag]
                    self.swells.append(Swell(position, position, level, level))
                elif found.tag in ACCENTS:
                    self.accents[position] = ACCENTS[found.tag]
                    if found.tag in ACCENT_THEN_SOFT:
                        level = DYNAMICS["p"]
                        self.swells.append(Swell(position, position, level, level))
            for wedge in element.iterfind("direction-type/wedge"):
                number = wedge.get("number", "1")
                if wedge.get("type") in ("crescendo", "diminuendo"):
                    opened[number] = (position, wedge.get("type"), level)
                elif wedge.get("type") == "stop" and number in opened:
                    begun, kind, before = opened.pop(number)
                    after = self.dynamic_near(marks, position)
                    if after is None:
                        after = before + (HAIRPIN_CHANGE if kind == "crescendo" else -HAIRPIN_CHANGE)
                    level = max(min(after, MOST_LEVEL), LEAST_LEVEL)
                    self.swells.append(Swell(begun, position, before, level))
                    self.swells.append(Swell(position, position, level, level))
        self.swells.sort(key=lambda swell: swell.start)
        self.starts = [swell.start for swell in self.swells]

    @staticmethod
    def dynamic_near(marks: list, position: float) -> float | None:
        """Return the dynamic a hairpin leads to, which usually stands just after it ends."""
        for where, element in marks:
            if position <= where <= position + HAIRPIN_REACH:
                for found in element.iterfind("direction-type/dynamics/*"):
                    if found.tag in DYNAMICS:
                        return DYNAMICS[found.tag]
        return None

    def at(self, position: float) -> float:
        swell = self.swells[bisect_right(self.starts, position) - 1]
        if position >= swell.end or swell.end == swell.start:
            return swell.after
        return swell.before + (swell.after - swell.before) * (position - swell.start) / (swell.end - swell.start)


def mark_on(note: ET.Element, group: str | None, tag: str) -> bool:
    return note.find(f"notations/{group}/{tag}" if group else f"notations/{tag}") is not None


def how_long(note: ET.Element, length: float) -> float:
    """Return how much of its written length a note sounds for."""
    for tag, share in SHORTENED.items():
        if mark_on(note, "articulations", tag):
            return length * share
    return length


def how_loud(note: ET.Element, level: float) -> float:
    for tag, boost in ACCENTED.items():
        if mark_on(note, "articulations", tag):
            return min(level * boost, MOST_LEVEL)
    return level


def swung(position: Fraction) -> Fraction:
    """Move a beat's second eighth later, which is all that swing does to the written rhythm."""
    return position - position % 1 + SWUNG_EIGHTH if position % 1 == Fraction(1, 2) else position


def neighbour(note: ET.Element, change: int, fifths: int) -> int:
    """Return the note a step above or below, using the key's own sharps and flats."""
    pitch = note.find("pitch")
    height = STEPS.index(pitch.findtext("step")) + 7 * int(pitch.findtext("octave")) + change
    step, octave = STEPS[height % 7], height // 7
    return (octave + 1) * 12 + SEMITONES[step] + alteration_in_key(step, fifths)


def ornament_of(note: ET.Element) -> str | None:
    return next((name for name, tag in ORNAMENTS.items() if mark_on(note, "ornaments", tag)), None)


def ornament_sounds(note: ET.Element, midi: int, fifths: int, begins: float, length: float, volume: float) -> list[dict]:
    """Spell out an ornament as the quick notes it stands for."""
    shape = ornament_of(note)
    tones = {0: midi}
    pitches = (0, 1) * int(length / ORNAMENT_NOTE) if shape == "trill" else ORNAMENT_SHAPES[shape]
    found = []
    place_in_note = 0.0
    for step_away in pitches:
        if place_in_note + ORNAMENT_NOTE > length:
            break
        tones.setdefault(step_away, neighbour(note, step_away, fifths))
        found.append({"id": note.get("id"), "start": begins + place_in_note, "length": ORNAMENT_NOTE, "midi": tones[step_away], "volume": volume})
        place_in_note += ORNAMENT_NOTE
    if place_in_note < length:
        found.append({"id": note.get("id"), "start": begins + place_in_note, "length": length - place_in_note, "midi": midi, "volume": volume})
    return found


def tempo_changes(bars: list[tuple[Context, Timeline, float]], first: float) -> list[tuple[float, float]]:
    changes = [(0.0, first)]
    for _, timeline, start in bars:
        for mark in sorted(timeline.marks, key=lambda item: item.onset):
            if mark.element.tag == "direction":
                for sound in mark.element.iter("sound"):
                    if sound.get("tempo"):
                        position = start + float(mark.onset)
                        if position == 0.0:
                            changes[0] = (0.0, float(sound.get("tempo")))
                        else:
                            changes.append((position, float(sound.get("tempo"))))
    return changes


def tempo_at(changes: list[tuple[float, float]], position: float) -> float:
    return next((bpm for where, bpm in reversed(changes) if where <= position), changes[0][1])


def with_holds(changes: list[tuple[float, float]], holds: list[tuple[float, float]]) -> list[dict]:
    """Add fermatas to the tempo, because a held note slows all the music for that moment."""
    merged: list[list[float]] = []
    for start, end in sorted(holds):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    final = [(where, bpm) for where, bpm in changes if not any(start <= where < end for start, end in merged)]
    for start, end in merged:
        final.append((start, tempo_at(changes, start) * FERMATA_SPEED))
        final.append((end, tempo_at(changes, end)))
    return [{"start": where, "bpm": bpm} for where, bpm in sorted(final)]


def swing_at(bars: list[tuple[Context, Timeline, float]]) -> list[tuple[float, bool]]:
    found = []
    for _, timeline, start in bars:
        for mark in sorted(timeline.marks, key=lambda item: item.onset):
            swing = mark.element.find("sound/swing") if mark.element.tag == "direction" else None
            if swing is not None:
                found.append((start + float(mark.onset), swing.find("first") is not None))
    return found


def tied_from(ringing: dict[tuple, dict], key: tuple[str, str], midi: int) -> tuple | None:
    """Find the note a tie carries on, usually in its own voice but sometimes from another."""
    if (key, midi) in ringing:
        return key, midi
    return next((held for held in ringing if held[0][0] == key[0] and held[1] == midi), None)


def play_part(bars: list, shifts: list[int], swings: list, holds: list) -> list[dict]:
    """Return one part's notes, each with its moment and how loud it is."""
    notes: list[dict] = []
    loudness = Loudness(bars)
    ringing: dict[tuple, dict] = {}
    for context, timeline, start in bars:
        shift = shifts[context.index] if context.index < len(shifts) else 0
        # In time order across voices, because a tie can lead from one voice into another.
        events = sorted(((key, event) for key, line in timeline.lines.items() for event in line), key=lambda item: item[1].onset)
        for key, event in events:
            begins = start + float(event.onset)
            length = float(event.length)
            if next((on for where, on in reversed(swings) if where <= begins), False):
                length = float(swung(event.end) - swung(event.onset))
                begins = start + float(swung(event.onset))
            if event.notes[0].find("notations/fermata") is not None:
                holds.append((begins, begins + length))
            ahead = play_grace(event, begins, length, shift, loudness, notes)
            for note in event.notes:
                if note.find("pitch") is None:
                    continue
                midi = midi_of(note) + shift
                source = tied_from(ringing, key, midi) if is_tied(note, "stop") else None
                if source:
                    entry = ringing.pop(source)
                    entry["length"] = begins + length - entry["start"]
                else:
                    level = loudness.accents.get(begins, loudness.at(begins))
                    volume = how_loud(event.notes[0], level) / MOST_LEVEL
                    sounding = length if is_tied(note, "start") else how_long(event.notes[0], length)
                    if ornament_of(note) and not is_tied(note, "start"):
                        notes.extend(ornament_sounds(note, midi, context.fifths, begins + ahead, sounding - ahead, volume))
                        continue
                    entry = {"id": note.get("id"), "start": begins + ahead, "length": sounding - ahead, "midi": midi, "volume": volume}
                    notes.append(entry)
                if is_tied(note, "start"):
                    ringing[key, midi] = entry
                else:
                    ringing.pop((key, midi), None)
    return notes


def play_grace(event: Event, begins: float, length: float, shift: int, loudness: Loudness, notes: list[dict]) -> float:
    """Play grace notes in the time of the note they lead to, and return how much they took."""
    sounding = [note for note in event.grace if note.find("pitch") is not None]
    if not sounding or event.is_rest:
        return 0.0
    quick = sounding[0].find("grace").get("slash") == "yes"
    each = min(GRACE_NOTE, length / (len(sounding) + 1)) if quick else length / 2 / len(sounding)
    volume = loudness.at(begins) / MOST_LEVEL
    for number, note in enumerate(sounding):
        notes.append({"id": note.get("id"), "start": begins + number * each, "length": each, "midi": midi_of(note) + shift, "volume": volume})
    return each * len(sounding)


def pedal_spans(bars: list[tuple[Context, Timeline, float]], end: float) -> list[tuple[float, float]]:
    """Return when a part's sustain pedal is held down. A change lets it up and puts it down again."""
    moments = []
    for _, timeline, start in bars:
        for mark in timeline.marks:
            if mark.element.tag == "direction":
                moments.extend((start + float(mark.onset), pedal.get("type")) for pedal in mark.element.iter("pedal"))
    spans = []
    down = None
    for at, kind in sorted(moments, key=lambda item: item[0]):
        if kind in ("stop", "change", "discontinue") and down is not None:
            spans.append((down, at))
            down = None
        if kind in ("start", "change", "resume"):
            down = at if down is None else down
    if down is not None:
        spans.append((down, end))
    return spans


def sustained(notes: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    """Hold each note let go while the pedal is down until the pedal comes up, or the key is played again."""
    if not spans:
        return notes
    starts: dict[int, list[float]] = {}
    for note in notes:
        starts.setdefault(note["midi"], []).append(note["start"])
    for midi in starts:
        starts[midi].sort()
    for note in notes:
        let_go = note["start"] + note["length"]
        held = next((up for down, up in spans if down <= let_go < up), None)
        if held is None:
            continue
        again = starts[note["midi"]][bisect_right(starts[note["midi"]], note["start"]):]
        note["length"] = min(held, again[0] if again else held) - note["start"]
    return notes


def programs_of(root: ET.Element) -> list[int]:
    """Return each part's General MIDI sound, counted from 1, so a file can say what plays it."""
    found = []
    for entry in root.findall("part-list/score-part"):
        program = entry.findtext("midi-instrument/midi-program")
        found.append(int(program) if program and program.isdigit() else 1)
    return found


def bars_played(bars: list[tuple[Context, Timeline, float]]) -> list[dict]:
    """Return each bar as it's played, for the playback position and the metronome.

    A pickup is shorter than its time signature says, and its beats are the last ones of a bar.
    """
    found = []
    for context, timeline, start in bars:
        longest = max((line_end(line) for line in timeline.lines.values()), default=0)
        found.append({"start": start, "length": float(longest or context.length), "full": float(context.length), "index": context.index, "beats": context.beats, "beatType": context.beat_type})
    return found


def event_places(root: ET.Element) -> dict[str, list]:
    """Say where in its bar each note and rest is, and how long it lasts, so a stretch can be found to loop."""
    found: dict[str, list] = {}
    for part in root.findall("part"):
        for context in contexts(part):
            for line in read_measure(context.measure, context.divisions).lines.values():
                for event in line:
                    for note in event.notes:
                        if note.get("id"):
                            found[note.get("id")] = [context.index, float(event.onset), float(event.length)]
    return found


def playback(root: ET.Element) -> dict:
    """Return notes and chords, timed in quarter notes from the start of the score.

    The bars are listed as they're played, and each note and rest says where it is in its bar, so
    the page can show the playback position as a measure and beat and loop over a selection.
    """
    notes: list[dict] = []
    chords: list[dict] = []
    holds: list[tuple[float, float]] = []
    parts = root.findall("part")
    order = play_order(parts[0].findall("measure"))
    top_bars, _ = laid_out(parts[0], order)
    swings = swing_at(top_bars)
    total = 0.0
    for part_number, part in enumerate(parts):
        bars, end = laid_out(part, order)
        total = max(total, end)
        played = sustained(play_part(bars, transposed_bars(part), swings, holds), pedal_spans(bars, end))
        notes.extend(item | {"part": part_number} for item in played)
        if part_number == 0:
            for _, timeline, start in bars:
                for mark in sorted(timeline.marks, key=lambda mark: mark.onset):
                    if mark.element.tag == "harmony" and (chord := chord_from_harmony(mark.element)) is not None:
                        chords.append({"start": start + float(mark.onset), "midi": chord_notes(chord)})
    for chord, following in zip(chords, [*chords[1:], {"start": total}]):
        chord["length"] = max(following["start"] - chord["start"], 0.0)
    tempo = tempo_of(root)
    return {
        "tempo": tempo,
        "tempos": with_holds(tempo_changes(top_bars, tempo), holds),
        "notes": sorted(notes, key=lambda item: item["start"]),
        "chords": chords,
        "total": total,
        "programs": programs_of(root),
        "bars": bars_played(top_bars),
        "events": event_places(root),
    }
