"""Change a score's shape: bars, key and time, repeats and endings, and the title.

This is also where bars are checked against the time signature.
"""

import xml.etree.ElementTree as ET
from fractions import Fraction

from pipeline.rhythm import (
    Context,
    EditError,
    alteration_in_key,
    contexts,
    fit_divisions,
    is_tied,
    line_end,
    make_bar_rest,
    read_measure,
    repair_ties,
    respell,
    units,
)

START_DIVISIONS = 96  # Fits every plain, dotted and triplet length a person can enter.
MOST_BARS = 1000
ATTRIBUTE_ORDER = ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol", "instruments", "clef", "staff-details", "transpose", "for-part", "directive", "measure-style"]
BEAT_TYPES = {1, 2, 4, 8, 16, 32}
CLEFS = {"treble": [("G", "2")], "bass": [("F", "4")], "piano": [("G", "2"), ("F", "4")]}
# The voice numbers MusicXML files conventionally use, four to a staff.
VOICES_PER_STAFF = 4
BARLINE_ORDER = ["bar-style", "footnote", "level", "wavy-line", "segno", "coda", "fermata", "ending", "repeat"]
REPEAT_SIDES = {"forward": ("left", "heavy-light"), "backward": ("right", "light-heavy")}
MOST_TITLE_LETTERS = 200


def ensure_measure_ids(root: ET.Element) -> None:
    """Remember each bar's original position, because adding a bar moves all the later ones.

    Chord boxes and correction labels are stored by measure number, so they'd point at the
    wrong bar. The id survives, so "m12" is still bar 12 of the original wherever it ends up.
    """
    for part_number, part in enumerate(root.findall("part")):
        prefix = f"p{part_number}" if part_number else ""
        for index, measure in enumerate(part.findall("measure")):
            if not measure.get("id"):
                measure.set("id", f"{prefix}m{index}")


def origin_of(measure: ET.Element, index: int) -> int | None:
    """Return a bar's position in the original score, or None if it was added later."""
    found = measure.get("id")
    if found is None:
        return index
    return int(found[1:]) if found[0] == "m" and found[1:].isdigit() else None


def new_measure_id(root: ET.Element) -> str:
    used = {measure.get("id") for measure in root.iter("measure")}
    number = 0
    while f"added{number}" in used:
        number += 1
    return f"added{number}"


def renumber(part: ET.Element) -> None:
    measures = part.findall("measure")
    first = 0 if measures and measures[0].get("implicit") == "yes" else 1
    for number, measure in enumerate(measures, start=first):
        measure.set("number", str(number))


def blank_measure(length: Fraction, divisions: int, staves: int) -> ET.Element:
    measure = ET.Element("measure")
    for staff in range(1, staves + 1):
        if staff > 1:
            ET.SubElement(ET.SubElement(measure, "backup"), "duration").text = units(length, divisions)
        measure.append(make_bar_rest(length, divisions, (str(staff), str((staff - 1) * VOICES_PER_STAFF + 1))))
    return measure


def ensure_attributes(measure: ET.Element) -> ET.Element:
    found = measure.find("attributes")
    if found is not None:
        return found
    attributes = ET.Element("attributes")
    children = list(measure)
    position = next((index for index, child in enumerate(children) if child.tag != "print"), len(children))
    measure.insert(position, attributes)
    return attributes


def set_attribute(attributes: ET.Element, element: ET.Element) -> None:
    rank = ATTRIBUTE_ORDER.index(element.tag)
    children = list(attributes)
    position = next((index for index, child in enumerate(children) if ATTRIBUTE_ORDER.index(child.tag) > rank), len(children))
    attributes.insert(position, element)


def drop_attribute(measure: ET.Element, tag: str) -> None:
    for attributes in measure.findall("attributes"):
        for found in attributes.findall(tag):
            attributes.remove(found)
        if len(attributes) == 0:
            measure.remove(attributes)


def insert_measure(root: ET.Element, index: int) -> None:
    """Add an empty bar before this position. A position past the end adds it at the end."""
    fit_divisions(root)
    new_id = new_measure_id(root)
    for part_number, part in enumerate(root.findall("part")):
        known = list(contexts(part))
        if len(known) >= MOST_BARS:
            raise EditError(f"A score can have at most {MOST_BARS} bars.")
        # A bar inserted just before a time change belongs to the time before it.
        like = known[max(min(index, len(known)) - 1, 0)]
        measure = blank_measure(like.length, like.divisions, like.staves)
        measure.set("id", new_id if part_number == 0 else f"p{part_number}{new_id}")
        measures = part.findall("measure")
        if index == 0:
            for attributes in measures[0].findall("attributes"):
                measures[0].remove(attributes)
                measure.insert(0, attributes)
        if index < len(measures):
            children = list(part)
            part.insert(children.index(measures[index]), measure)
        else:
            part.append(measure)
        renumber(part)
        repair_ties(part, min(index, len(measures)))


def carry_attributes(source: ET.Element, target: ET.Element) -> None:
    """Move anything still needed out of a bar about to be deleted.

    A key, time or clef applies until it's changed, so deleting the bar that states it would
    silently change every bar after it.
    """
    for attributes in source.findall("attributes"):
        for child in list(attributes):
            existing = ensure_attributes(target)
            if not any(other.tag == child.tag and other.get("number") == child.get("number") for other in existing):
                set_attribute(existing, child)


def delete_measure(root: ET.Element, index: int) -> None:
    fit_divisions(root)
    for part in root.findall("part"):
        measures = part.findall("measure")
        if len(measures) < 2:
            raise EditError("A score needs at least one bar.")
        if not 0 <= index < len(measures):
            raise EditError(f"No bar number {index + 1}")
        doomed = measures[index]
        if index + 1 < len(measures):
            carry_attributes(doomed, measures[index + 1])
        else:
            kept = measures[index - 1]
            if kept.find("barline") is None:
                kept.extend(doomed.findall("barline"))
        part.remove(doomed)
        renumber(part)
        repair_ties(part, max(index - 1, 0))


def reread_notes(context: Context, tied: dict[tuple[str, str, str], str | None]) -> None:
    """Give notes the sharps and flats of the new key, keeping them on the same staff lines.

    The usual reason to change the key here is that it was misread, so a note that was printed
    plainly should stay printed plainly. Only printed accidentals keep their own alteration.
    """
    in_force: dict[tuple[str, str, str], int] = {}
    for event in read_measure(context.measure, context.divisions).events():
        for note in [*event.grace, *event.notes]:
            pitch = note.find("pitch")
            if pitch is None:
                continue
            step, octave = pitch.findtext("step"), pitch.findtext("octave")
            where = (note.findtext("staff") or "1", step, octave)
            existing = pitch.find("alter")
            if is_tied(note, "stop") and tied.get(where) is not None:
                alter = int(tied[where])
            elif note.find("accidental") is not None:
                alter = int(float(pitch.findtext("alter") or 0))
            else:
                alter = in_force.get(where, alteration_in_key(step, context.fifths))
            if existing is not None:
                pitch.remove(existing)
            if alter:
                element = ET.Element("alter")
                element.text = str(alter)
                pitch.insert(1, element)
            in_force[where] = alter
            tied[where] = str(alter) if is_tied(note, "start") else None


def set_key(root: ET.Element, index: int, fifths: int) -> None:
    """Set the key from this bar until the next key change."""
    if not -7 <= fifths <= 7:
        raise EditError("A key has at most seven sharps or flats.")
    for part in root.findall("part"):
        known = list(contexts(part))
        if not 0 <= index < len(known):
            raise EditError(f"No bar number {index + 1}")
        measure = known[index].measure
        drop_attribute(measure, "key")
        if index == 0 or known[index - 1].fifths != fifths:
            key = ET.Element("key")
            ET.SubElement(key, "fifths").text = str(fifths)
            set_attribute(ensure_attributes(measure), key)
        tied: dict[tuple[str, str, str], str | None] = {}
        changed = []
        for context in list(contexts(part))[index:]:
            if context.index > index and context.measure.find("attributes/key") is not None:
                break
            reread_notes(context, tied)
            respell(context)
            changed.append(context.index)
        for number in changed:
            repair_ties(part, number)


def set_time(root: ET.Element, index: int, beats: int, beat_type: int) -> None:
    """Set the time from this bar until the next time change.

    The notes stay in their bars. Recognition has usually read the bars correctly and only
    misread the signature, and if it's wrong the bar check will show which bars don't fit.
    """
    if beat_type not in BEAT_TYPES or not 1 <= beats <= 32:
        raise EditError("That isn't a time signature this app can use.")
    fit_divisions(root)
    for part in root.findall("part"):
        known = list(contexts(part))
        if not 0 <= index < len(known):
            raise EditError(f"No bar number {index + 1}")
        measure = known[index].measure
        drop_attribute(measure, "time")
        if index == 0 or (known[index - 1].beats, known[index - 1].beat_type) != (beats, beat_type):
            time = ET.Element("time")
            ET.SubElement(time, "beats").text = str(beats)
            ET.SubElement(time, "beat-type").text = str(beat_type)
            set_attribute(ensure_attributes(measure), time)
        for context in list(contexts(part))[index:]:
            if context.index > index and context.measure.find("attributes/time") is not None:
                break
            for note in context.measure.findall("note"):  # Empty bars just change size.
                if note.find("rest") is not None and note.find("rest").get("measure") == "yes":
                    note.find("duration").text = units(context.length, context.divisions)


def check_measures(root: ET.Element) -> list[dict]:
    """List bars whose notes don't add up to the time signature.

    Recognition mistakes usually show up here, because a misread rhythm leaves a bar too long
    or too short. A short first bar is fine, because pieces often start with a pickup, and a
    piece that does usually makes up for it with a short last bar.
    """
    found: dict[int, dict] = {}
    for part in root.findall("part"):
        known = list(contexts(part))
        for context in known:
            timeline = read_measure(context.measure, context.divisions)
            ends = [line_end(line) for line in timeline.lines.values()]
            if not ends:
                continue
            problem = None
            if max(ends) > context.length:
                problem = "long"
            elif max(ends) < context.length and not context.is_pickup and context.index > 0:
                is_last = context.index == len(known) - 1
                first = read_measure(known[0].measure, known[0].divisions)
                started_short = max((line_end(line) for line in first.lines.values()), default=known[0].length) < known[0].length
                problem = None if is_last and started_short else "short"
            if problem is not None:
                first = timeline.events()[0].notes[0]
                found.setdefault(context.index, {"measure": context.index, "problem": problem, "note": first.get("id")})
    return list(found.values())


def barline_of(measure: ET.Element, location: str) -> ET.Element | None:
    return next((found for found in measure.findall("barline") if found.get("location", "right") == location), None)


def add_barline(measure: ET.Element, location: str) -> ET.Element:
    """Find or make a bar's left or right barline. Left ones go first, right ones last."""
    barline = barline_of(measure, location)
    if barline is None:
        barline = ET.Element("barline", location=location)
        if location == "left":
            measure.insert(0, barline)
        else:
            measure.append(barline)
    return barline


def tidy_barline(measure: ET.Element, barline: ET.Element, keep_style: bool) -> None:
    style = barline.find("bar-style")
    if style is not None and not keep_style:
        barline.remove(style)
    if len(barline) == 0:
        measure.remove(barline)


def set_barline_child(barline: ET.Element, element: ET.Element) -> None:
    """Add a child to a barline at the position the schema requires."""
    rank = BARLINE_ORDER.index(element.tag)
    children = list(barline)
    barline.insert(next((index for index, child in enumerate(children) if BARLINE_ORDER.index(child.tag) > rank), len(children)), element)


def toggle_repeat(root: ET.Element, index: int, direction: str) -> None:
    """Start or end a repeat at a bar, or take away the one that's there."""
    location, style = REPEAT_SIDES[direction]
    for part in root.findall("part"):
        measures = part.findall("measure")
        if not 0 <= index < len(measures):
            raise EditError(f"No bar number {index + 1}")
        measure = measures[index]
        barline = add_barline(measure, location)
        existing = barline.find("repeat")
        if existing is not None:
            barline.remove(existing)
            tidy_barline(measure, barline, keep_style=False)
            continue
        for found in barline.findall("bar-style"):
            barline.remove(found)
        set_barline_child(barline, element_with_text("bar-style", style))
        set_barline_child(barline, ET.Element("repeat", direction=direction))


def element_with_text(tag: str, text: str) -> ET.Element:
    element = ET.Element(tag)
    element.text = text
    return element


def clear_endings(measure: ET.Element) -> None:
    for barline in measure.findall("barline"):
        for found in barline.findall("ending"):
            barline.remove(found)
        tidy_barline(measure, barline, keep_style=barline.find("repeat") is not None)


def set_ending(root: ET.Element, first: int, last: int, number: int) -> None:
    """Mark bars as a first or second ending. Number 0 removes any ending from them.

    A first ending has a closed bracket and a second one is left open, as they're usually drawn.
    """
    if number not in (0, 1, 2):
        raise EditError("Only first and second endings are supported.")
    for part in root.findall("part"):
        measures = part.findall("measure")
        if not 0 <= first <= last < len(measures):
            raise EditError(f"No bar number {last + 1}")
        for measure in measures[first : last + 1]:
            clear_endings(measure)
        if number:
            set_barline_child(add_barline(measures[first], "left"), ET.Element("ending", number=str(number), type="start"))
            set_barline_child(add_barline(measures[last], "right"), ET.Element("ending", number=str(number), type="stop" if number == 1 else "discontinue"))


def title_of(root: ET.Element) -> dict:
    composer = next((found for found in root.iter("creator") if found.get("type") == "composer"), None)
    return {"title": (root.findtext("work/work-title") or "").strip(), "composer": (composer.text or "").strip() if composer is not None else ""}


def set_title(root: ET.Element, title: str, composer: str) -> None:
    """Set the title and composer, in the file and on the printed page.

    Verovio only prints a composer from a credit, and if a score has any credit it ignores the
    work title, so both are written as credits as well.
    """
    title, composer = title.strip(), composer.strip()
    if len(title) > MOST_TITLE_LETTERS or len(composer) > MOST_TITLE_LETTERS:
        raise EditError("That's too long for a title.")
    work = root.find("work")
    if work is None:
        work = ET.Element("work")
        root.insert(0, work)
    found = work.find("work-title")
    if found is None:
        found = ET.SubElement(work, "work-title")
    found.text = title
    identification = root.find("identification")
    if identification is None:
        identification = ET.Element("identification")
        root.insert(list(root).index(work) + 1, identification)
    for creator in identification.findall("creator"):
        if creator.get("type") == "composer":
            identification.remove(creator)
    if composer:
        creator = ET.Element("creator", type="composer")
        creator.text = composer
        identification.insert(0, creator)
    for credit in root.findall("credit"):
        root.remove(credit)
    before_list = list(root).index(root.find("part-list"))
    for offset, (kind, text, justify) in enumerate((("title", title, "center"), ("composer", composer, "right"))):
        if not text:
            continue
        credit = ET.Element("credit", page="1")
        ET.SubElement(credit, "credit-type").text = kind
        ET.SubElement(credit, "credit-words", justify=justify, valign="top").text = text
        root.insert(before_list + (offset if title else 0), credit)


def blank_score(fifths: int = 0, beats: int = 4, beat_type: int = 4, clef: str = "treble", measures: int = 16) -> ET.Element:
    """Make an empty score to write in from scratch."""
    if clef not in CLEFS:
        raise EditError("Choose a treble, bass or piano staff.")
    if not 1 <= measures <= MOST_BARS:
        raise EditError(f"A score can have 1 to {MOST_BARS} bars.")
    if beat_type not in BEAT_TYPES or not 1 <= beats <= 32:
        raise EditError("That isn't a time signature this app can use.")
    if not -7 <= fifths <= 7:
        raise EditError("A key has at most seven sharps or flats.")
    root = ET.Element("score-partwise", version="4.0")
    score_part = ET.SubElement(ET.SubElement(root, "part-list"), "score-part", id="P1")
    ET.SubElement(score_part, "part-name", {"print-object": "no"}).text = "Music"
    part = ET.SubElement(root, "part", id="P1")
    staves = len(CLEFS[clef])
    for index in range(measures):
        measure = blank_measure(Fraction(beats * 4, beat_type), START_DIVISIONS, staves)
        part.append(measure)
        if index:
            continue
        attributes = ET.Element("attributes")
        ET.SubElement(attributes, "divisions").text = str(START_DIVISIONS)
        ET.SubElement(ET.SubElement(attributes, "key"), "fifths").text = str(fifths)
        time = ET.SubElement(attributes, "time")
        ET.SubElement(time, "beats").text = str(beats)
        ET.SubElement(time, "beat-type").text = str(beat_type)
        if staves > 1:
            ET.SubElement(attributes, "staves").text = str(staves)
        for number, (sign, line) in enumerate(CLEFS[clef], start=1):
            element = ET.SubElement(attributes, "clef", number=str(number)) if staves > 1 else ET.SubElement(attributes, "clef")
            ET.SubElement(element, "sign").text = sign
            ET.SubElement(element, "line").text = line
        measure.insert(0, attributes)
    renumber(part)
    ensure_measure_ids(root)
    return root


def surviving_bars(root: ET.Element) -> set[int | None]:
    """Return which of the original bars are still in the score."""
    return {origin_of(measure, index) for index, measure in enumerate(root.find("part").findall("measure"))}


def bar_of(root: ET.Element, note_id: str) -> int:
    """Return which bar holds a note."""
    for part in root.findall("part"):
        for index, measure in enumerate(part.findall("measure")):
            if any(note.get("id") == note_id for note in measure.iter("note")):
                return index
    raise EditError(f"No note with id {note_id}")
