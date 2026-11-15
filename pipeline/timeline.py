"""The timeline: the whole score at a glance, a column for each measure, as MuseScore's timeline shows it.

Its first rows say where the tempo, time signature, rehearsal marks, key signature, barlines and
jumps change. Then each instrument has a row that says which of its measures have notes in them.
Those all belong to the score as a whole, so they're read from the top instrument, where this app
keeps them.
"""

import xml.etree.ElementTree as ET
from fractions import Fraction

from pipeline.marks import direction_kind, direction_label
from pipeline.rhythm import contexts, octave_lines, read_measure
from pipeline.structure import BAR_STYLES, TIME_SYMBOLS, is_final, time_symbol

KEY_NAMES = ["C♭", "G♭", "D♭", "A♭", "E♭", "B♭", "F", "C", "G", "D", "A", "E", "B", "F♯", "C♯"]
TIME_SIGNS = {"common": "C", "cut": "¢"}  # As text, which any font can show.
BARLINE_NAMES = {style: name for name, style in BAR_STYLES.items() if style}


def time_text(time: ET.Element) -> str:
    symbol = time_symbol(time)
    return TIME_SIGNS[symbol] if symbol in TIME_SYMBOLS else f"{time.findtext('beats')}/{time.findtext('beat-type')}"


def barline_text(measure: ET.Element) -> str:
    """Say how a measure's barlines are drawn, if they're anything but plain: a repeat, a double bar or the end."""
    found = []
    for barline in measure.findall("barline"):
        repeat = barline.find("repeat")
        if repeat is not None:
            found.append("start repeat" if repeat.get("direction") == "forward" else "end repeat")
        elif is_final(barline):
            found.append("final")
        elif barline.findtext("bar-style") in BARLINE_NAMES:
            found.append(BARLINE_NAMES[barline.findtext("bar-style")])
    return ", ".join(found)


def has_notes(measure: ET.Element) -> bool:
    return any(note.find("pitch") is not None or note.find("unpitched") is not None for note in measure.iter("note"))


def timeline(root: ET.Element) -> dict:
    """Return the timeline's rows: what changes at each measure, and which measures each instrument plays in."""
    parts = root.findall("part")
    measures = []
    fifths = None
    for context in contexts(parts[0]):
        measure = context.measure
        time = next((element for attributes in measure.findall("attributes") for element in attributes.findall("time")), None)
        directions = [(direction_kind(direction), direction) for direction in measure.findall("direction")]
        key = KEY_NAMES[context.fifths + 7] if fifths != context.fifths and -7 <= context.fifths <= 7 else ""
        fifths = context.fifths
        measures.append({
            "number": measure.get("number") or str(context.index + 1),
            "tempo": " ".join(direction_label(direction) for kind, direction in directions if kind == "tempo"),
            "time": time_text(time) if time is not None else "",
            "rehearsal": " ".join(direction_label(direction) for kind, direction in directions if kind == "rehearsal"),
            "key": key,
            "barline": barline_text(measure),
            "jump": " ".join(direction_label(direction) for kind, direction in directions if kind in ("jump", "repeat-sign")),
        })
    # Each instrument's own key, measure by measure: a transposing one's is written in another key.
    keys = [[context.fifths for context in contexts(part)] for part in parts]
    return {"measures": measures, "parts": [[has_notes(measure) for measure in part.findall("measure")] for part in parts], "keys": keys}


def places(root: ET.Element) -> dict[str, list]:
    """Say where each note and rest is, and what a place on its staff means there, by its id.

    Each has its bar's index and its place in the bar in quarter notes, then the height of its
    staff's top line (as rhythm.diatonic numbers a pitch), how many octaves above where they're
    drawn its notes sound, under an 8va or the like, and the beat it's on, counted from 1 as a player
    counts, in dotted beats in 6/8, 9/8 and 12/8, and a pickup's beats as the last of its bar. The
    page names the note a click would write and when it falls from these, before it's written, as
    edit.pitch_on_line works it out once it is.
    """
    found: dict[str, list] = {}
    for part in root.findall("part"):
        lines = octave_lines(part)
        for context in contexts(part):
            timeline = read_measure(context.measure, context.divisions)
            compound = context.beat_type == 8 and context.beats % 3 == 0 and context.beats > 3
            unit = Fraction(4 * (3 if compound else 1), context.beat_type)
            filled = max((event.end for event in timeline.events()), default=context.length)
            begins = filled - context.length if context.is_pickup and filled < context.length else Fraction(0)
            for (staff, _), line in timeline.lines.items():
                for event in line:
                    octaves = next((shift.octaves for shift in lines if shift.covers(staff, (context.index, event.onset))), 0)
                    top = context.tops.get(staff)
                    beat = float((event.onset - begins) / unit + 1)
                    for note in event.notes:
                        if note.get("id"):
                            found[note.get("id")] = [context.index, float(event.onset), top, octaves, round(beat, 4)]
    return found
