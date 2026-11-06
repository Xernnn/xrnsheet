"""Turn a score into timed notes the browser can play.

Hearing a score is the fastest way to catch a wrong note or chord, so chord symbols are played
too. Repeats and first and second endings are followed. D.C. and D.S. aren't.
"""

import re
import xml.etree.ElementTree as ET

from chords.vocab import QUALITIES
from pipeline.musicxml import chord_from_harmony
from pipeline.rhythm import contexts, is_tied, line_end, read_measure

DEFAULT_TEMPO = 100
MOST_PASSES = 16  # A file can ask for any number of repeats, and playback has to end.
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


def play_order(measures: list[ET.Element]) -> list[int]:
    """Return the bars in the order they're played, following repeats and endings."""
    ending_of: dict[int, set[int]] = {}
    last_of_ending: dict[int, int] = {}
    index = 0
    while index < len(measures):
        passes = endings_of(measures[index], ("start",))
        if passes:
            last = index
            while last + 1 < len(measures) and not endings_of(measures[last], ("stop", "discontinue")) and not endings_of(measures[last + 1], ("start",)):
                last += 1
            # An ending nothing repeats back to is never reached, so it's just played.
            if 1 in passes or any(repeats_back(measure) for measure in measures[:index]):
                for number in range(index, last + 1):
                    ending_of[number], last_of_ending[number] = passes, last
            index = last
        index += 1

    order: list[int] = []
    start, index, current_pass = 0, 0, 1
    played: dict[int, int] = {}
    while index < len(measures):
        if starts_repeat(measures[index]) and index != start:
            start, current_pass = index, 1
        if index in ending_of and current_pass not in ending_of[index]:
            index = last_of_ending[index] + 1
            continue
        order.append(index)
        times = repeats_back(measures[index])
        played[index] = played.get(index, 0) + 1
        if times and played[index] < times:
            current_pass = played[index] + 1
            index = start
            continue
        if times or (index in ending_of and index == last_of_ending[index] and current_pass > 1):
            start, current_pass = index + 1, 1
        index += 1
    return order


def playback(root: ET.Element) -> dict:
    """Return notes and chords, timed in quarter notes from the start of the score."""
    notes: list[dict] = []
    chords: list[dict] = []
    total = 0.0
    for part_number, part in enumerate(root.findall("part")):
        start = 0.0
        ringing: dict[tuple, dict] = {}
        known = list(contexts(part))
        for context in (known[index] for index in play_order([item.measure for item in known])):
            timeline = read_measure(context.measure, context.divisions)
            for key, line in timeline.lines.items():
                for event in sorted(line, key=lambda event: event.onset):
                    begins = start + float(event.onset)
                    for note in event.notes:
                        if note.find("pitch") is None:
                            continue
                        midi = midi_of(note)
                        held = (key, midi)
                        length = float(event.length)
                        if is_tied(note, "stop") and held in ringing:
                            entry = ringing[held]
                            entry["length"] = begins + length - entry["start"]
                        else:
                            entry = {"id": note.get("id"), "start": begins, "length": length, "midi": midi}
                            notes.append(entry)
                        if is_tied(note, "start"):
                            ringing[held] = entry
                        else:
                            ringing.pop(held, None)
            if part_number == 0:
                for mark in sorted(timeline.marks, key=lambda mark: mark.onset):
                    if mark.element.tag == "harmony" and (chord := chord_from_harmony(mark.element)) is not None:
                        chords.append({"start": start + float(mark.onset), "midi": chord_notes(chord)})
            longest = max((line_end(line) for line in timeline.lines.values()), default=0)
            start += float(longest or context.length)
        total = max(total, start)
    for chord, following in zip(chords, [*chords[1:], {"start": total}]):
        chord["length"] = max(following["start"] - chord["start"], 0.0)
    return {"tempo": tempo_of(root), "notes": sorted(notes, key=lambda item: item["start"]), "chords": chords, "total": total}
