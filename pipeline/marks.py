"""The marks that say how music is played: articulations, slurs, dynamics, tempo and text.

Some marks belong to a note and move with it, like a staccato dot, so they live inside that note.
Others belong to a moment in the bar, like a dynamic, so they're directions, which the Timeline
keeps at their own moment even when the notes around them change. A slur or hairpin has two ends,
and an edit can take away either one, so both are repaired after every edit.
"""

import re
import xml.etree.ElementTree as ET
from fractions import Fraction

from chords.vocab import parse_chord
from pipeline.concert import transposition_at
from pipeline.edit import AFTER, Spot, copy_range, events_between, find_note, locate, next_note_id, paste
from pipeline.musicxml import chord_from_harmony, harmony_element
from pipeline.rhythm import LINE_STARTS, Context, EditError, Mark, contexts, indexed, make_note, place, read_measure, remove_wedge_end, repair_lines, repair_slurs, respell, write_measure
from pipeline.structure import element_with_text
from pipeline.transpose import Interval, move_chord, move_pitch

# A mark on a note: which group it sits in inside <notations>, and its tag.
NOTE_MARKS = {
    "staccato": ("articulations", "staccato"),
    "staccatissimo": ("articulations", "staccatissimo"),
    "tenuto": ("articulations", "tenuto"),
    "accent": ("articulations", "accent"),
    "marcato": ("articulations", "strong-accent"),
    "breath": ("articulations", "breath-mark"),
    "fermata": (None, "fermata"),
    "trill": ("ornaments", "trill-mark"),
    "mordent": ("ornaments", "mordent"),
    "prall": ("ornaments", "inverted-mordent"),
    "turn": ("ornaments", "turn"),
    "upbow": ("technical", "up-bow"),
    "downbow": ("technical", "down-bow"),
    "arpeggio": (None, "arpeggiate"),
}
# How loud each dynamic is, as a MIDI velocity. A <sound> stores it as a percentage of 90.
DYNAMICS = {"ppp": 20, "pp": 33, "p": 49, "mp": 64, "mf": 80, "f": 96, "ff": 112, "fff": 127}
ACCENTS = {"sf": 112, "sfz": 112, "sfp": 112, "fz": 112, "rfz": 112, "fp": 96}  # These last one note.
BEAT_UNITS = {"half": 2, "quarter": 1, "eighth": Fraction(1, 2)}
LOWEST_TEMPO, HIGHEST_TEMPO = 10, 400
MOST_TEXT_LETTERS = 120
MOST_SPANNERS = 6  # MusicXML numbers overlapping slurs and hairpins from 1 to 6.
SLASHED = "yes"
GRACE_LENGTH = Fraction(1, 2)
JUMPS = {
    "segno": ("start", None, {"segno": "segno"}),
    "coda": ("start", None, {"coda": "coda"}),
    "tocoda": ("end", "To Coda", {"tocoda": "coda"}),
    "fine": ("end", "Fine", {"fine": "yes"}),
    "dc": ("end", "D.C.", {"dacapo": "yes"}),
    "dcfine": ("end", "D.C. al Fine", {"dacapo": "yes"}),
    "dccoda": ("end", "D.C. al Coda", {"dacapo": "yes"}),
    "ds": ("end", "D.S.", {"dalsegno": "segno"}),
    "dsfine": ("end", "D.S. al Fine", {"dalsegno": "segno"}),
    "dscoda": ("end", "D.S. al Coda", {"dalsegno": "segno"}),
}
JUMP_TEXTS = {text for _, text, _ in JUMPS.values() if text}
SWING_FIRST, SWING_SECOND = "2", "1"
# MuseScore's octave lines, as MusicXML writes them: which way the notes are drawn from their pitch,
# by how much, and which side of the staff the line goes. An 8va's notes are drawn an octave down.
OTTAVAS = {"8va": ("down", "8", "above"), "8vb": ("up", "8", "below"), "15ma": ("down", "15", "above"), "15mb": ("up", "15", "below")}
# MuseScore's pedal lines that MusicXML can say: "Ped." and a star, or a line with a hook at each end.
PEDALS = {"pedstar": {"line": "no", "sign": "yes"}, "line": {"line": "yes", "sign": "no"}}


def new_id(root: ET.Element) -> str:
    used = {element.get("id") for element in root.iter() if element.get("id")}
    number = 0
    while f"mark{number}" in used:
        number += 1
    return f"mark{number}"


def ensure_mark_ids(root: ET.Element) -> None:
    """Give each mark an id, so a click on it can name it. Verovio keeps those of slurs and hairpins.

    Numbers go in document order, so a score saved without ids gets the same ones every time.
    """
    used = {element.get("id") for element in root.iter() if element.get("id")}
    number = 0
    for element in root.iter():
        wanted = element.tag == "direction" or (element.tag == "slur" and element.get("type") == "start") or (element.tag == "wedge" and element.get("type") != "stop")
        if wanted and not element.get("id"):
            while f"mark{number}" in used:
                number += 1
            element.set("id", f"mark{number}")
            used.add(f"mark{number}")


def notations_of(note: ET.Element) -> ET.Element:
    found = note.find("notations")
    if found is None:
        found = ET.Element("notations")
        place(note, found)
    return found


def drop_if_empty(note: ET.Element) -> None:
    found = note.find("notations")
    if found is not None and len(found) == 0:
        note.remove(found)


def has_mark(note: ET.Element, name: str) -> bool:
    group, tag = NOTE_MARKS[name]
    return note.find(f"notations/{group}/{tag}" if group else f"notations/{tag}") is not None


def add_mark(note: ET.Element, name: str) -> None:
    group, tag = NOTE_MARKS[name]
    if has_mark(note, name):
        return
    notations = notations_of(note)
    if group is None:
        ET.SubElement(notations, tag)
        return
    holder = notations.find(group)
    if holder is None:
        holder = ET.SubElement(notations, group)
    ET.SubElement(holder, tag)


def remove_mark(note: ET.Element, name: str) -> None:
    group, tag = NOTE_MARKS[name]
    notations = note.find("notations")
    if notations is None:
        return
    holder = notations.find(group) if group else notations
    if holder is None:
        return
    for found in holder.findall(tag):
        holder.remove(found)
    if group and len(holder) == 0:
        notations.remove(holder)
    drop_if_empty(note)


def event_head(root: ET.Element, note_id: str) -> ET.Element:
    """Return the note a chord's marks are stored on, which is its first."""
    note = find_note(root, note_id)
    if note.find("grace") is not None:
        return note
    return locate(root, note_id).event.notes[0]


def toggle_mark(root: ET.Element, note_ids: list[str], name: str) -> None:
    """Add a mark to everything chosen, or take it away if everything chosen already has it."""
    if name not in NOTE_MARKS:
        raise EditError("That isn't a mark this app knows.")
    with indexed(root):
        heads = list({id(note): note for note in (event_head(root, note_id) for note_id in note_ids)}.values())
    if name != "fermata":
        heads = [note for note in heads if note.find("pitch") is not None]
    if not heads:
        raise EditError("That mark goes on a note.")
    wanted = not all(has_mark(note, name) for note in heads)
    for note in heads:
        if wanted:
            add_mark(note, name)
        else:
            remove_mark(note, name)


def spanner_numbers(part: ET.Element, tag: str, first: int, last: int) -> set[int]:
    """Return the numbers taken by this kind of mark in these bars, or running through them."""
    taken: set[int] = set()
    running: set[int] = set()
    for index, measure in enumerate(part.findall("measure")):
        for element in measure.iter(tag):
            number = int(element.get("number", "1"))
            if element.get("type") == "stop":
                if first <= index <= last:
                    taken.add(number)
                running.discard(number)
            else:
                running.add(number)
                if first <= index <= last:
                    taken.add(number)
        if index >= first:
            taken |= running
        if index > last:
            break
    return taken


def free_number(part: ET.Element, tag: str, first: int, last: int) -> int:
    taken = spanner_numbers(part, tag, first, last)
    found = next((number for number in range(1, MOST_SPANNERS + 1) if number not in taken), None)
    if found is None:
        raise EditError("Too many of these overlap here already.")
    return found


def slur_between(start: ET.Element, stop: ET.Element) -> tuple[ET.Element, ET.Element] | None:
    for first in start.findall("notations/slur"):
        if first.get("type") == "start":
            for second in stop.findall("notations/slur"):
                if second.get("type") == "stop" and second.get("number", "1") == first.get("number", "1"):
                    return first, second
    return None


def drop_slur_end(note: ET.Element, slur: ET.Element) -> None:
    note.find("notations").remove(slur)
    drop_if_empty(note)


def toggle_slur(root: ET.Element, first_id: str, last_id: str) -> None:
    """Slur a stretch, or one note to the next. A slur that's already exactly there comes off."""
    start, chosen = events_between(root, first_id, last_id)
    sounding = [(context, event) for context, _, event in chosen if not event.is_rest]
    if not sounding:
        raise EditError("A slur joins notes.")
    first_context, first_event = sounding[0]
    if len(sounding) == 1:
        spot = locate(root, first_event.notes[0].get("id"))
        after = next_note_id(spot)
        if after is None:
            raise EditError("There's no note after this one to slur to.")
        last_context, last_note = locate(root, after).context, locate(root, after).event.notes[0]
    else:
        last_context, last_note = sounding[-1][0], sounding[-1][1].notes[0]
    first_note = first_event.notes[0]
    existing = slur_between(first_note, last_note)
    if existing is not None:
        drop_slur_end(first_note, existing[0])
        drop_slur_end(last_note, existing[1])
        return
    number = str(free_number(start.part, "slur", first_context.index, last_context.index))
    ET.SubElement(notations_of(first_note), "slur", {"id": new_id(root), "type": "start", "number": number})
    ET.SubElement(notations_of(last_note), "slur", {"type": "stop", "number": number})


def make_direction(placement: str, content: list[ET.Element], sound: dict | ET.Element | None = None, staff: str = "1", several_staves: bool = False) -> ET.Element:
    direction = ET.Element("direction", placement=placement)
    for element in content:
        ET.SubElement(direction, "direction-type").append(element)
    if several_staves:
        ET.SubElement(direction, "staff").text = staff
    if isinstance(sound, ET.Element):
        direction.append(sound)
    elif sound:
        ET.SubElement(direction, "sound", sound)
    return direction


def add_at(spot: Spot, element: ET.Element, onset: Fraction) -> None:
    """Put a direction at a moment in the bar, which needn't be where any note starts."""
    timeline = read_measure(spot.context.measure, spot.context.divisions)
    timeline.marks.append(Mark(onset, element))
    write_measure(spot.context.measure, timeline, spot.context.divisions)


def directions_at(spot: Spot, onset: Fraction, kinds: tuple[str, ...]) -> list[ET.Element]:
    found = []
    for mark in read_measure(spot.context.measure, spot.context.divisions).marks:
        if mark.element.tag == "direction" and mark.onset == onset and direction_kind(mark.element) in kinds and (mark.element.findtext("staff") or "1") == spot.key[0]:
            found.append(mark.element)
    return found


def direction_kind(direction: ET.Element) -> str:
    """Say what a direction is, which also says how it's drawn."""
    tags = {child.tag for holder in direction.findall("direction-type") for child in holder}
    sound = direction.find("sound")
    if "wedge" in tags:
        return "hairpin"
    if "octave-shift" in tags:
        return "ottava"
    if "pedal" in tags:
        return "pedal"
    if "dynamics" in tags:
        return "dynamic"
    if "rehearsal" in tags:
        return "rehearsal"
    if "metronome" in tags or (sound is not None and sound.get("tempo")):
        return "tempo"
    if tags & {"segno", "coda"}:
        return "repeat-sign"
    if sound is not None and {"dacapo", "dalsegno", "tocoda", "fine"} & set(sound.attrib):
        return "jump"
    return "text"


def several_staves(spot: Spot) -> bool:
    return spot.context.staves > 1


def dynamic_name(direction: ET.Element) -> str:
    found = direction.find("direction-type/dynamics")
    return found[0].tag if found is not None and len(found) else ""


def set_dynamic(root: ET.Element, note_id: str, name: str) -> None:
    """Put a dynamic at a note. The same one again takes it away, and another replaces it."""
    if name not in DYNAMICS and name not in ACCENTS:
        raise EditError("That isn't a dynamic this app knows.")
    spot = locate(root, note_id)
    existing = directions_at(spot, spot.event.onset, ("dynamic",))
    same = any(dynamic_name(found) == name for found in existing)
    for found in existing:
        spot.context.measure.remove(found)
    if same:
        return
    dynamics = ET.Element("dynamics")
    ET.SubElement(dynamics, name)
    level = DYNAMICS.get(name, ACCENTS.get(name))
    sound = {"dynamics": f"{level * 100 / 90:.2f}"}
    add_at(spot, make_direction("below", [dynamics], sound, spot.key[0], several_staves(spot)), spot.event.onset)


def toggle_hairpin(root: ET.Element, first_id: str, last_id: str, kind: str) -> None:
    """Draw a crescendo or diminuendo across a stretch, or across one note."""
    if kind not in ("crescendo", "diminuendo"):
        raise EditError("A hairpin is a crescendo or a diminuendo.")
    start, chosen = events_between(root, first_id, last_id)
    first_context, _, first_event = chosen[0]
    last_context, _, last_event = chosen[-1]
    first_spot = Spot(start.part, first_context, read_measure(first_context.measure, first_context.divisions), start.key, first_event, first_event.notes[0])
    last_spot = Spot(start.part, last_context, read_measure(last_context.measure, last_context.divisions), start.key, last_event, last_event.notes[0])
    for direction in directions_at(first_spot, first_event.onset, ("hairpin",)):
        wedge = direction.find("direction-type/wedge")
        if wedge.get("type") == kind:
            same_stop = [found for found in directions_at(last_spot, last_event.end, ("hairpin",)) if found.find("direction-type/wedge").get("type") == "stop" and found.find("direction-type/wedge").get("number", "1") == wedge.get("number", "1")]
            if same_stop:
                remove_wedge_end(first_context.measure, direction, wedge)
                remove_wedge_end(last_context.measure, same_stop[0], same_stop[0].find("direction-type/wedge"))
                return
    number = str(free_number(start.part, "wedge", first_context.index, last_context.index))
    begin = ET.Element("wedge", {"id": new_id(root), "type": kind, "number": number})
    add_at(first_spot, make_direction("below", [begin], None, start.key[0], several_staves(start)), first_event.onset)
    stop = ET.Element("wedge", {"type": "stop", "number": number})
    add_at(last_spot, make_direction("below", [stop], None, start.key[0], several_staves(start)), last_event.end)


def placed_lines(part: ET.Element, tag: str) -> list[tuple[tuple, tuple | None]]:
    """List a part's lines of one kind, with both ends: each end's bar, direction, element and moment.

    A line left open has no end, and runs to the end of the part.
    """
    found: list[tuple[tuple, tuple | None]] = []
    opened: dict[str, tuple] = {}
    for context in contexts(part):
        for mark in sorted(read_measure(context.measure, context.divisions).marks, key=lambda item: item.onset):
            if mark.element.tag != "direction":
                continue
            for line in mark.element.iter(tag):
                end = (context.measure, mark.element, line, (context.index, mark.onset))
                number = line.get("number", "1")
                if line.get("type") == "stop":
                    if number in opened:
                        found.append((opened.pop(number), end))
                elif line.get("type") in LINE_STARTS[tag]:
                    opened[number] = end
    found.extend((begun, None) for begun in opened.values())
    return found


def remove_line(ends: tuple[tuple, tuple | None]) -> None:
    for end in ends:
        if end is not None:
            remove_wedge_end(end[0], end[1], end[2])


def toggle_line(root: ET.Element, first_id: str, last_id: str, tag: str, start: dict, stop: dict, placement: str) -> None:
    """Draw a line over a stretch of one staff, or take it away if the same one is already there.

    Two lines of a kind can't lie over each other on a staff, so one the new line overlaps goes.
    """
    begin, chosen = events_between(root, first_id, last_id)
    first_context, _, first_event = chosen[0]
    last_context, _, last_event = chosen[-1]
    staff = begin.key[0]
    starts, ends = (first_context.index, first_event.onset), (last_context.index, last_event.end)
    overlapping = []
    for line in placed_lines(begin.part, tag):
        line_start = line[0][3]
        line_stop = line[1][3] if line[1] is not None else (len(begin.part.findall("measure")), Fraction(0))
        if (line[0][1].findtext("staff") or "1") == staff and line_start < ends and starts < line_stop:
            overlapping.append(line)
    same = any(line[0][3] == starts and line[1] is not None and line[1][3] == ends and all(line[0][2].get(name) == value for name, value in start.items()) for line in overlapping)
    for line in overlapping:
        remove_line(line)
    if same:
        return
    number = str(free_number(begin.part, tag, first_context.index, last_context.index))
    for context, onset, kind in ((first_context, first_event.onset, start), (last_context, last_event.end, stop)):
        spot = Spot(begin.part, context, read_measure(context.measure, context.divisions), begin.key, None, None)
        element = ET.Element(tag, {**kind, "number": number})
        add_at(spot, make_direction(placement, [element], None, staff, several_staves(begin)), onset)


def toggle_ottava(root: ET.Element, first_id: str, last_id: str, name: str) -> None:
    """Put an 8va, 8vb, 15ma or 15mb line over a stretch, or take it away.

    The notes under it keep their pitch, which is how they sound, and are drawn an octave or two
    from it, as MuseScore and MusicXML both have it. So adding one changes how they look, not how
    they're played.
    """
    if name not in OTTAVAS:
        raise EditError("That isn't an octave line this app knows.")
    kind, size, placement = OTTAVAS[name]
    toggle_line(root, first_id, last_id, "octave-shift", {"type": kind, "size": size}, {"type": "stop", "size": size}, placement)


def toggle_pedal(root: ET.Element, first_id: str, last_id: str, style: str) -> None:
    """Hold the sustain pedal down over a stretch, drawn as "Ped." and a star or as a line, or take it away."""
    if style not in PEDALS:
        raise EditError("That isn't a pedal line this app knows.")
    toggle_line(root, first_id, last_id, "pedal", {"type": "start", **PEDALS[style]}, {"type": "stop", **PEDALS[style]}, "below")


def top_spot(root: ET.Element, spot: Spot) -> Spot:
    """Return the same moment in the top part, because a tempo or a jump belongs to the whole score."""
    part = root.find("part")
    context = list(contexts(part))[spot.context.index]
    timeline = read_measure(context.measure, context.divisions)
    return Spot(part, context, timeline, next(iter(timeline.lines), ("1", "1")), spot.event, None)


def parse_tempo(text: str) -> tuple[str, int | None]:
    """Split "Allegro 132" into words and a speed. Either can be missing."""
    text = text.strip()
    found = re.fullmatch(r"(.*?)\s*(?:(?:♩|q)\s*=\s*)?(\d{1,3})", text)
    if found is None:
        return text, None
    return found.group(1).strip(), int(found.group(2))


def tempo_direction(words: str, bpm: int | None, unit: str, dots: int, staff: str, several: bool) -> ET.Element:
    content: list[ET.Element] = []
    if words:
        content.append(element_with_text("words", words))
    sound = None
    if bpm is not None:
        metronome = ET.Element("metronome")
        ET.SubElement(metronome, "beat-unit").text = unit
        for _ in range(dots):
            ET.SubElement(metronome, "beat-unit-dot")
        ET.SubElement(metronome, "per-minute").text = str(bpm)
        content.append(metronome)
        quarters = bpm * BEAT_UNITS[unit] * (2 - Fraction(1, 2**dots))
        sound = {"tempo": str(round(float(quarters), 2))}
    return make_direction("above", content, sound, staff, several)


def set_tempo(root: ET.Element, note_id: str, text: str, unit: str = "quarter", dots: int = 0) -> None:
    """Set the tempo from a note on, from text such as "Allegro 132" or just "120"."""
    words, bpm = parse_tempo(text)
    if unit not in BEAT_UNITS or dots not in (0, 1):
        raise EditError("That isn't a beat this app can show.")
    if bpm is None and not words:
        raise EditError("Type a speed, such as 120 or Allegro 132.")
    if bpm is not None and not LOWEST_TEMPO <= bpm <= HIGHEST_TEMPO:
        raise EditError(f"A tempo has to be {LOWEST_TEMPO} to {HIGHEST_TEMPO} beats a minute.")
    if len(words) > MOST_TEXT_LETTERS:
        raise EditError("That's too long for a tempo.")
    spot = top_spot(root, locate(root, note_id))
    for found in directions_at(spot, spot.event.onset, ("tempo",)):
        spot.context.measure.remove(found)
    add_at(spot, tempo_direction(words, bpm, unit, dots, "1", several_staves(spot)), spot.event.onset)


def add_text(root: ET.Element, note_id: str, text: str, kind: str = "staff") -> None:
    """Add words at a note: staff text above it, or an expression like "dolce" in italics below."""
    text = text.strip()
    if not text:
        raise EditError("Type something first.")
    if len(text) > MOST_TEXT_LETTERS:
        raise EditError("That's too long for one place.")
    spot = locate(root, note_id)
    words = element_with_text("words", text)
    if kind == "expression":
        words.set("font-style", "italic")
    elif kind != "staff":
        raise EditError("That isn't a kind of text this app knows.")
    add_at(spot, make_direction("above" if kind == "staff" else "below", [words], None, spot.key[0], several_staves(spot)), spot.event.onset)


def next_rehearsal(root: ET.Element) -> str:
    """Return the next rehearsal letter, so a section can be marked with one key."""
    used = {found.text for found in root.iter("rehearsal")}
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for doubled in (1, 2):
        for letter in letters:
            if letter * doubled not in used:
                return letter * doubled
    raise EditError("There are no rehearsal letters left.")


def add_rehearsal(root: ET.Element, note_id: str, text: str = "") -> None:
    spot = top_spot(root, locate(root, note_id))
    mark = element_with_text("rehearsal", text.strip()[:MOST_TEXT_LETTERS] or next_rehearsal(root))
    add_at(spot, make_direction("above", [mark], None, "1", several_staves(spot)), spot.event.onset)


def set_swing(root: ET.Element, note_id: str, on: bool) -> None:
    """Mark where eighth notes start or stop swinging, as written text and for playback."""
    spot = top_spot(root, locate(root, note_id))
    sound = ET.Element("sound")
    swing = ET.SubElement(sound, "swing")
    if on:
        ET.SubElement(swing, "first").text = SWING_FIRST
        ET.SubElement(swing, "second").text = SWING_SECOND
        ET.SubElement(swing, "swing-type").text = "eighth"
    else:
        ET.SubElement(swing, "straight")
    words = element_with_text("words", "Swing" if on else "Straight")
    add_at(spot, make_direction("above", [words], sound, "1", several_staves(spot)), spot.event.onset)


def set_jump(root: ET.Element, note_id: str, name: str) -> None:
    """Mark a bar with a segno or coda, or a jump such as D.S. al Coda."""
    if name not in JUMPS:
        raise EditError("That isn't a jump this app knows.")
    where, text, sound = JUMPS[name]
    spot = top_spot(root, locate(root, note_id))
    onset = Fraction(0) if where == "start" else spot.context.length
    existing = [found for found in spot.context.measure.findall("direction") if direction_kind(found) in ("repeat-sign", "jump") and direction_label(found) == (text or name.capitalize())]
    for found in existing:
        spot.context.measure.remove(found)
    if existing:
        return
    content = [ET.Element(name)] if text is None else [element_with_text("words", text)]
    add_at(spot, make_direction("above", content, dict(sound), "1", several_staves(spot)), onset)


def direction_label(direction: ET.Element) -> str:
    """Return a direction's text as a person would read it."""
    kind = direction_kind(direction)
    words = " ".join(found.text or "" for found in direction.iter("words")).strip()
    if kind == "dynamic":
        return dynamic_name(direction)
    if kind == "rehearsal":
        return direction.findtext("direction-type/rehearsal") or ""
    if kind == "tempo":
        bpm = direction.findtext("direction-type/metronome/per-minute")
        return f"{words} {bpm}".strip() if bpm else words
    if kind == "repeat-sign":
        return "Segno" if direction.find("direction-type/segno") is not None else "Coda"
    return words


# How each direction is drawn. Chords are tied to their drawings by order, and these are the same.
DRAWN_AS = {"dynamic": "dynam", "tempo": "tempo", "text": "dir", "jump": "dir", "rehearsal": "reh", "repeat-sign": "repeatMark", "ottava": "octave", "pedal": "pedal"}


def list_marks(root: ET.Element) -> list[dict]:
    """List the marks a person can click, bar by bar and in the order they're drawn.

    Every part draws into one bar, and that bar carries the top part's id.
    """
    top_bars = [measure.get("id") for measure in root.find("part").findall("measure")]
    found = []
    for part in root.findall("part"):
        # An octave line is drawn once, from its start. Each end of a pedal is drawn, and both are the one pedal.
        pedal = {id(end[1]): ends[0][1].get("id") for ends in placed_lines(part, "pedal") for end in ends if end is not None}
        ottava_ends = {id(ends[1][1]) for ends in placed_lines(part, "octave-shift") if ends[1] is not None}
        ottavas = []
        for index, measure in enumerate(part.findall("measure")):
            bar = top_bars[index] if index < len(top_bars) else None
            for direction in measure.findall("direction"):
                kind = direction_kind(direction)
                if kind == "ottava":
                    # Drawn staff by staff, as the lines are placed beside their staff's notes to be drawn.
                    if id(direction) not in ottava_ends:
                        ottavas.append(((index, int(direction.findtext("staff") or 1)), {"id": direction.get("id"), "bar": bar, "kind": kind, "drawn": DRAWN_AS[kind], "text": ""}))
                    continue
                mark_id = pedal.get(id(direction), direction.get("id")) if kind == "pedal" else direction.get("id")
                if kind in DRAWN_AS:
                    found.append({"id": mark_id, "bar": bar, "kind": kind, "drawn": DRAWN_AS[kind], "text": direction_label(direction)})
            for element in measure.iter("wedge"):
                if element.get("type") != "stop":
                    found.append({"id": element.get("id"), "bar": bar, "kind": "hairpin", "drawn": "hairpin", "text": ""})
            for element in measure.iter("slur"):
                if element.get("type") == "start":
                    found.append({"id": element.get("id"), "bar": bar, "kind": "slur", "drawn": "slur", "text": ""})
        found.extend(entry for _, entry in sorted(ottavas, key=lambda item: item[0]))
    return found


def find_direction(root: ET.Element, mark_id: str) -> tuple[ET.Element, ET.Element, ET.Element]:
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            for direction in measure.findall("direction"):
                if direction.get("id") == mark_id:
                    return part, measure, direction
    raise EditError(f"No mark with id {mark_id}")


def remove_wedge(root: ET.Element, mark_id: str) -> bool:
    """Take away a hairpin by removing its start, which leaves an end for repair to clear."""
    for part in root.findall("part"):
        for measure in part.findall("measure"):
            for direction in measure.findall("direction"):
                wedge = next((found for found in direction.iter("wedge") if found.get("id") == mark_id), None)
                if wedge is not None:
                    remove_wedge_end(measure, direction, wedge)
                    repair_lines(part)
                    return True
    return False


def remove_slur(root: ET.Element, mark_id: str) -> bool:
    for part in root.findall("part"):
        for note in part.iter("note"):
            for slur in note.findall("notations/slur"):
                if slur.get("id") == mark_id:
                    drop_slur_end(note, slur)
                    repair_slurs(part)
                    for other in part.iter("note"):
                        drop_if_empty(other)
                    return True
    return False


def remove_placed(root: ET.Element, mark_id: str) -> None:
    """Remove a mark someone clicked on. An octave line or a pedal goes with both its ends."""
    if remove_wedge(root, mark_id) or remove_slur(root, mark_id):
        return
    part, measure, direction = find_direction(root, mark_id)
    for tag in ("octave-shift", "pedal"):
        for ends in placed_lines(part, tag):
            if ends[0][1] is direction:
                remove_line(ends)
                return
    measure.remove(direction)


def set_label(root: ET.Element, mark_id: str, text: str) -> None:
    """Change what a mark says. Clearing it removes the mark."""
    if not text.strip():
        remove_placed(root, mark_id)
        return
    _, measure, direction = find_direction(root, mark_id)
    kind = direction_kind(direction)
    if kind == "dynamic":
        name = text.strip()
        if name not in DYNAMICS and name not in ACCENTS:
            raise EditError("That isn't a dynamic this app knows.")
        dynamics = direction.find("direction-type/dynamics")
        for child in list(dynamics):
            dynamics.remove(child)
        ET.SubElement(dynamics, name)
        direction.find("sound").set("dynamics", f"{DYNAMICS.get(name, ACCENTS.get(name)) * 100 / 90:.2f}")
    elif kind == "tempo":
        words, bpm = parse_tempo(text)
        if bpm is not None and not LOWEST_TEMPO <= bpm <= HIGHEST_TEMPO:
            raise EditError(f"A tempo has to be {LOWEST_TEMPO} to {HIGHEST_TEMPO} beats a minute.")
        unit = direction.findtext("direction-type/metronome/beat-unit") or "quarter"
        dots = len(direction.findall("direction-type/metronome/beat-unit-dot"))
        new = tempo_direction(words, bpm, unit, dots, "1", False)
        for child in [*direction.findall("direction-type"), *direction.findall("sound")]:
            direction.remove(child)
        for position, holder in enumerate(new.findall("direction-type")):
            direction.insert(position, holder)
        if new.find("sound") is not None:
            direction.append(new.find("sound"))
    elif kind == "rehearsal":
        direction.find("direction-type/rehearsal").text = text.strip()[:MOST_TEXT_LETTERS]
    elif kind == "text":
        next(direction.iter("words")).text = text.strip()[:MOST_TEXT_LETTERS]
    else:
        raise EditError("This mark can only be removed.")


def add_grace(root: ET.Element, note_id: str, slashed: bool = True) -> str:
    """Put a grace note before a note, at the same pitch, ready to be moved where it belongs."""
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("Grace notes go before a note.")
    source = spot.event.notes[0].find("pitch")
    pitch = (source.findtext("step"), round(float(source.findtext("alter") or 0)), int(source.findtext("octave")))
    note = make_note(GRACE_LENGTH, spot.context.divisions, spot.key, pitch)
    note.remove(note.find("duration"))
    grace = ET.Element("grace", {"slash": SLASHED} if slashed else {})
    place(note, grace)
    note.set("id", new_id(root))
    spot.event.grace.append(note)
    write_measure(spot.context.measure, spot.timeline, spot.context.divisions)
    respell(spot.context)
    return note.get("id")


def mark_names(note: ET.Element) -> list[str]:
    return [name for name in NOTE_MARKS if has_mark(note, name)]


def dynamic_at(context: Context, onset: Fraction) -> str | None:
    for mark in read_measure(context.measure, context.divisions).marks:
        if mark.element.tag == "direction" and mark.onset == onset and direction_kind(mark.element) == "dynamic":
            return dynamic_name(mark.element)
    return None


def copy_stretch(root: ET.Element, first_id: str, last_id: str) -> dict:
    """Copy a stretch of a voice with its marks. A slur only comes if both its ends do.

    Notes are copied as they're written, with how far their part sounds from that, so pasted
    into another instrument they sound the same, as they do in MuseScore.
    """
    with indexed(root):
        copied = copy_range(root, first_id, last_id)
        start = locate(root, first_id)
        moved = transposition_at(start.part, start.context.index)
        for item in copied["events"]:
            item["transposition"] = [moved.steps, moved.semitones]
        ends: dict[str, int] = {}
        for number, item in enumerate(copied["events"]):
            source = item.pop("source")
            item.update(marks=[], dynamic=None, slur=[])
            if source is None:
                continue
            note = find_note(root, source)
            spot = locate(root, source)
            item["marks"] = mark_names(note)
            item["dynamic"] = dynamic_at(spot.context, spot.event.onset)
            for slur in note.findall("notations/slur"):
                if slur.get("type") == "stop" and slur.get("number", "1") in ends:
                    copied["events"][ends.pop(slur.get("number", "1"))]["slur"].append(number)
            for slur in note.findall("notations/slur"):
                if slur.get("type") == "start":
                    ends[slur.get("number", "1")] = number
    return copied


def moved_chord(text: str, interval: Interval) -> str:
    chord = parse_chord(text)
    if chord is None:
        return text
    harmony = harmony_element(chord)
    move_chord(harmony, interval)
    return str(chord_from_harmony(harmony) or text)


def for_instrument(items: list[dict], spot: Spot) -> list[dict]:
    """Rewrite copied notes for the instrument they're pasted into, so they sound as they did.

    A copy that doesn't say how its part was transposed is pasted as it was written.
    """
    target = transposition_at(spot.part, spot.context.index)
    found = []
    for item in items:
        source = item.get("transposition")
        if source is None or Interval(*source) == target:
            found.append(item)
            continue
        interval = Interval(*source) + -target
        pitches = [list(move_pitch(step, alter, octave, interval)) for step, alter, octave in item["pitches"]]
        found.append(item | {"pitches": pitches, "chord": moved_chord(item["chord"], interval) if item.get("chord") else item.get("chord")})
    return found


def paste_stretch(root: ET.Element, at_id: str, items: list[dict]) -> dict:
    """Paste copied music, then put its marks back on the notes it became."""
    items = for_instrument(items, locate(root, at_id.removeprefix(AFTER)))
    done = paste(root, at_id, items)
    placed = done.pop("placed")
    for number, item in enumerate(items):
        head = placed[number]
        spot = locate(root, head)
        for name in item.get("marks") or []:
            if name in NOTE_MARKS and (name == "fermata" or not spot.event.is_rest):
                add_mark(spot.event.notes[0], name)
        if item.get("dynamic"):
            existing = directions_at(spot, spot.event.onset, ("dynamic",))
            if not any(dynamic_name(found) == item["dynamic"] for found in existing):
                set_dynamic(root, head, item["dynamic"])
        for last in item.get("slur") or []:
            if 0 <= last < len(items) and last > number:
                toggle_slur(root, head, placed[last])
    return done
