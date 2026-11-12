"""The timeline: the whole score at a glance, a column for each measure, as MuseScore's timeline shows it.

Its first rows say where the tempo, time signature, rehearsal marks, key signature, barlines and
jumps change. Then each instrument has a row that says which of its measures have notes in them.
Those all belong to the score as a whole, so they're read from the top instrument, where this app
keeps them.
"""

import xml.etree.ElementTree as ET

from pipeline.marks import direction_kind, direction_label
from pipeline.rhythm import contexts
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
