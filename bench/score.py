"""Score an engine's MusicXML against the correct answer.

Engines disagree on how to split music into staves and voices, so notes are flattened
into one time-ordered list before comparing. An alignment then finds how many of them
match, which means one missed note costs one note and does not shift everything after it.

    pitch_f1        right pitches, ignoring rhythm
    pitch_class_f1  same, ignoring octave, so one misread clef does not sink a whole sheet
    note_f1         right pitch and right duration
    chord_recall    share of chord symbols read correctly, in order
    chord_placed    share read correctly and attached to the right measure and beat
    time_right      share of bars with the right time signature
    bars_full       share of bars whose notes add up to the signature they ended up with

Most test pieces are in 4/4, so a reader that always answered 4/4 would do well on time_right.
Read it per signature, which `run_engines.py` prints. bars_full needs no answer key, but a bar
can add up with wrong notes, so it can only show that something is wrong.

An engine can return one sheet as several files, so every reader takes a list of paths.
"""

import sys
import warnings
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from music21 import converter, harmony

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chords.vocab import parse_chord  # noqa: E402
from pipeline.musicxml import chord_from_harmony, load_root  # noqa: E402
from pipeline.rhythm import contexts  # noqa: E402
from pipeline.structure import check_measures  # noqa: E402

warnings.filterwarnings("ignore", module="music21")

# Durations are stored approximately, so a sixteenth triplet can read 0.164 or 0.168.
# Snapping to a fine grid makes both equal 1/6 of a quarter note.
DURATION_GRID = 48


@dataclass
class Score:
    pitch_f1: float
    pitch_class_f1: float
    note_f1: float
    chord_recall: float | None
    chord_placed: float | None
    truth_notes: int
    predicted_notes: int
    truth_chords: int
    predicted_chords: int
    time_right: float = 0.0
    bars_full: float = 0.0
    truth_time: str = ""


def read_notes(paths: list[Path]) -> list[tuple[float, int, float]]:
    events: list[tuple[float, int, float]] = []
    start = 0.0
    for path in paths:
        sheet = []
        for part in converter.parse(path).parts:
            for element in part.flatten().notes:
                if isinstance(element, harmony.Harmony):
                    continue
                onset = float(element.offset) + start
                length = round(float(element.quarterLength) * DURATION_GRID) / DURATION_GRID
                sheet.extend((onset, pitch.midi, length) for pitch in element.pitches)
        events.extend(sorted(sheet))
        if sheet:
            start = max(onset + length for onset, _, length in sheet)
    return events


def read_placed_chords(paths: list[Path], include_text: bool = True) -> list[tuple[str, int, float]]:
    """Return (chord, measure, onset) for each chord, walking the markup beat by beat.

    Some engines write a chord as proper harmony, and others leave it as text above the
    staff. A musician would still read that text as a chord, so it counts for predictions.
    Correct answers use include_text=False, because lyrics like "Am" only look like chords.
    """
    found = []
    first_measure = 0
    for path in paths:
        most_measures = 0
        for part in load_root(path).findall("part"):
            measures = part.findall("measure")
            most_measures = max(most_measures, len(measures))
            divisions = 1.0
            for index, measure in enumerate(measures, start=first_measure):
                position = 0.0
                for child in measure:
                    length = float(child.findtext("duration") or 0)
                    chord = None
                    if child.tag == "attributes":
                        divisions = float(child.findtext("divisions") or divisions)
                    elif child.tag == "harmony":
                        chord = chord_from_harmony(child)
                        position_here = position + float(child.findtext("offset") or 0) / divisions
                        if chord is not None:
                            found.append((str(chord), index, round(position_here, 3)))
                    elif child.tag == "backup":
                        position -= length / divisions
                    elif child.tag == "forward":
                        position += length / divisions
                    elif child.tag == "note":
                        if include_text:
                            for text in child.findall("lyric/text"):
                                chord = parse_chord(text.text or "")
                                if chord is not None:
                                    found.append((str(chord), index, round(position, 3)))
                        if child.find("chord") is None and child.find("grace") is None:
                            position += length / divisions
                    elif child.tag == "direction" and include_text:
                        for text in child.findall(".//words"):
                            chord = parse_chord(text.text or "")
                            if chord is not None:
                                found.append((str(chord), index, round(position, 3)))
        first_measure += most_measures
    return found


def read_chords(paths: list[Path], include_text: bool = True) -> list[str]:
    return [chord for chord, _, _ in read_placed_chords(paths, include_text)]


def read_times(paths: list[Path]) -> list[str]:
    """Return the time signature in force in each bar of the top part."""
    found: list[str] = []
    for path in paths:
        parts = load_root(path).findall("part")
        if parts:
            found.extend(f"{context.beats}/{context.beat_type}" for context in contexts(parts[0]))
    return found


def share_of_bars_full(paths: list[Path]) -> float:
    bars = problems = 0
    for path in paths:
        root = load_root(path)
        parts = root.findall("part")
        if parts:
            bars += len(parts[0].findall("measure"))
            problems += len(check_measures(root))
    return 1 - problems / bars if bars else 0.0


def matched(truth: list, predicted: list) -> int:
    matcher = SequenceMatcher(a=truth, b=predicted, autojunk=False)
    return sum(block.size for block in matcher.get_matching_blocks())


def f1(truth: list, predicted: list) -> float:
    if not truth or not predicted:
        return 0.0
    return 2 * matched(truth, predicted) / (len(truth) + len(predicted))


def compare(truth_path: Path, predicted_paths: list[Path]) -> Score:
    truth = read_notes([truth_path])
    predicted = read_notes(predicted_paths)
    truth_placed = read_placed_chords([truth_path], include_text=False)
    predicted_placed = read_placed_chords(predicted_paths)
    truth_chords = [chord for chord, _, _ in truth_placed]
    predicted_chords = [chord for chord, _, _ in predicted_placed]
    truth_times = read_times([truth_path])
    predicted_times = read_times(predicted_paths)
    chord_recall = chord_placed = None
    if truth_chords:
        chord_recall = matched(truth_chords, predicted_chords) / len(truth_chords)
        chord_placed = sum(item in set(predicted_placed) for item in truth_placed) / len(truth_placed)

    return Score(
        pitch_f1=f1([midi for _, midi, _ in truth], [midi for _, midi, _ in predicted]),
        pitch_class_f1=f1([midi % 12 for _, midi, _ in truth], [midi % 12 for _, midi, _ in predicted]),
        note_f1=f1(
            [(midi, length) for _, midi, length in truth],
            [(midi, length) for _, midi, length in predicted],
        ),
        chord_recall=chord_recall,
        chord_placed=chord_placed,
        truth_notes=len(truth),
        predicted_notes=len(predicted),
        truth_chords=len(truth_chords),
        predicted_chords=len(predicted_chords),
        time_right=sum(map(str.__eq__, truth_times, predicted_times)) / max(len(truth_times), len(predicted_times), 1),
        bars_full=share_of_bars_full(predicted_paths),
        truth_time=truth_times[0] if truth_times else "",
    )
