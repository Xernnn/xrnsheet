"""Change a score's shape: bars, key and time, repeats and endings, and the title.

This is also where bars are checked against the time signature.
"""

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from fractions import Fraction

from pipeline.rhythm import (
    Context,
    EditError,
    alteration_in_key,
    contexts,
    fifths_of,
    fit_divisions,
    is_tied,
    line_end,
    make_bar_rest,
    read_measure,
    repair_slurs,
    repair_ties,
    repair_wedges,
    respell,
    rests,
    units,
    write_measure,
)

START_DIVISIONS = 96  # Fits every plain, dotted and triplet length a person can enter.
MOST_BARS = 1000
ATTRIBUTE_ORDER = ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol", "instruments", "clef", "staff-details", "transpose", "for-part", "directive", "measure-style"]
BEAT_TYPES = {1, 2, 4, 8, 16, 32}
CLEF_SHAPES = {"treble": ("G", "2", 0), "treble8": ("G", "2", -1), "bass": ("F", "4", 0), "alto": ("C", "3", 0), "tenor": ("C", "4", 0)}
# The voice numbers MusicXML files conventionally use, four to a staff.
VOICES_PER_STAFF = 4
MOST_KEY = 7
MOST_PARTS = 40
BREAK_PREFIX = "linebreak"  # Marks a line break a person asked for, unlike those a page was read with.
BARS_PER_LINE = "xrnsheet-bars-per-line"
MOST_BARS_PER_LINE = 16


@dataclass(frozen=True)
class Instrument:
    """What a part needs to know: its staves, and how far it sounds from how it's written."""

    label: str
    clefs: tuple[str, ...] = ("treble",)
    steps: int = 0
    semitones: int = 0
    octaves: int = 0
    program: int = 1  # General MIDI, counted from 1.
    hidden_name: bool = False


INSTRUMENTS = {
    "treble": Instrument("Music", hidden_name=True),
    "bass": Instrument("Music", ("bass",), hidden_name=True),
    "piano": Instrument("Piano", ("treble", "bass"), hidden_name=True),
    "voice": Instrument("Voice", program=53),
    "guitar": Instrument("Guitar", ("treble8",), program=25),
    "bass-guitar": Instrument("Bass Guitar", ("bass",), octaves=-1, program=34),
    "violin": Instrument("Violin", program=41),
    "viola": Instrument("Viola", ("alto",), program=42),
    "cello": Instrument("Cello", ("bass",), program=43),
    "double-bass": Instrument("Double Bass", ("bass",), octaves=-1, program=44),
    "flute": Instrument("Flute", program=74),
    "oboe": Instrument("Oboe", program=69),
    "clarinet": Instrument("Clarinet in B♭", steps=-1, semitones=-2, program=72),
    "alto-sax": Instrument("Alto Saxophone", steps=-5, semitones=-9, program=66),
    "tenor-sax": Instrument("Tenor Saxophone", steps=-1, semitones=-2, octaves=-1, program=67),
    "baritone-sax": Instrument("Baritone Saxophone", steps=-5, semitones=-9, octaves=-1, program=68),
    "trumpet": Instrument("Trumpet in B♭", steps=-1, semitones=-2, program=57),
    "horn": Instrument("Horn in F", steps=-4, semitones=-7, program=61),
    "trombone": Instrument("Trombone", ("bass",), program=58),
}
BARLINE_ORDER = ["bar-style", "footnote", "level", "wavy-line", "segno", "coda", "fermata", "ending", "repeat"]
REPEAT_SIDES = {"forward": ("left", "heavy-light"), "backward": ("right", "light-heavy")}
MOST_TITLE_LETTERS = 200


def read_setting(root: ET.Element, name: str) -> str | None:
    """Read something the app keeps with a score that isn't music, stored as a named field."""
    return next((field.text for field in root.iter("miscellaneous-field") if field.get("name") == name), None)


def write_setting(root: ET.Element, name: str, value: str | None) -> None:
    """Store a named field, or remove it when there's no value."""
    identification = root.find("identification")
    if identification is None:
        if value is None:
            return
        identification = ET.Element("identification")
        before = [index for index, child in enumerate(root) if child.tag in ("work", "movement-number", "movement-title")]
        root.insert(before[-1] + 1 if before else 0, identification)
    fields = identification.find("miscellaneous")
    if fields is None:
        fields = ET.SubElement(identification, "miscellaneous")
    found = next((field for field in fields if field.get("name") == name), None)
    if value is None and found is not None:
        fields.remove(found)
    elif value is not None:
        if found is None:
            found = ET.SubElement(fields, "miscellaneous-field", name=name)
        found.text = value
    if len(fields) == 0:
        identification.remove(fields)
    if len(identification) == 0:
        root.remove(identification)


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


def set_pickup(root: ET.Element, quarters: Fraction) -> None:
    """Make the first bar a pickup of this many quarter notes, or a whole bar again if it's 0.

    Rests come and go to fit. Notes never do, so a pickup too short for them is refused.
    """
    fit_divisions(root)
    for part in root.findall("part"):
        context = next(contexts(part))
        if quarters >= context.length:
            raise EditError("A pickup has to be shorter than a bar.")
        end = quarters or context.length
        timeline = read_measure(context.measure, context.divisions)
        if any(mark.onset >= end for mark in timeline.marks):
            raise EditError("There are marks past the end of that pickup. Remove them first.")
        for key, line in timeline.lines.items():
            kept = sorted((event for event in line if not event.is_rest or event.in_tuplet), key=lambda event: event.onset)
            if any(event.end > end for event in kept):
                raise EditError("There are notes past the end of that pickup. Delete them first.")
            filled, position = [], Fraction(0)
            for event in kept:
                filled += [*rests(position, event.onset, context, key), event]
                position = event.end
            timeline.lines[key] = [*filled, *rests(position, end, context, key)]
        write_measure(context.measure, timeline, context.divisions)
        if quarters:
            context.measure.set("implicit", "yes")
        else:
            context.measure.attrib.pop("implicit", None)
        renumber(part)


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
        repair_slurs(part)
        repair_wedges(part)


def key_offset(steps: int, semitones: int) -> int:
    """Return how many sharps a part is written with beyond the key it sounds in.

    A trumpet sounds a tone lower than written, so its music is written with two sharps more.
    """
    return -fifths_of(steps, semitones)


def key_for_part(part: ET.Element, fifths: int) -> int:
    """Return a part's written key, wrapped round to a key that exists if it goes past seven."""
    found = part.find("measure/attributes/transpose")
    offset = 0 if found is None else key_offset(int(found.findtext("diatonic") or 0), int(found.findtext("chromatic") or 0))
    written = fifths + offset
    return written - 12 * (1 if written > 0 else -1) if abs(written) > MOST_KEY else written


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
            if not is_tied(note, "stop"):  # A sharp tied over the barline holds for the tied note alone.
                in_force[where] = alter
            tied[where] = str(alter) if is_tied(note, "start") else None


def set_clef(root: ET.Element, index: int, part_number: int, staff: str, name: str) -> None:
    """Change a staff's clef from this bar on. Notes keep their pitch, so they move on the staff."""
    if name not in CLEF_SHAPES:
        raise EditError("That isn't a clef this app knows.")
    parts = root.findall("part")
    known = list(contexts(parts[part_number]))
    if not 0 <= index < len(known):
        raise EditError(f"No bar number {index + 1}")
    measure = known[index].measure
    for attributes in measure.findall("attributes"):
        for found in attributes.findall("clef"):
            if found.get("number", "1") == staff:
                attributes.remove(found)
        if len(attributes) == 0:
            measure.remove(attributes)
    sign, line, shift = CLEF_SHAPES[name]
    clef = ET.Element("clef", number=staff) if known[index].staves > 1 else ET.Element("clef")
    ET.SubElement(clef, "sign").text = sign
    ET.SubElement(clef, "line").text = line
    if shift:
        ET.SubElement(clef, "clef-octave-change").text = str(shift)
    set_attribute(ensure_attributes(measure), clef)


def set_key(root: ET.Element, index: int, fifths: int) -> None:
    """Set the key from this bar until the next key change."""
    if not -MOST_KEY <= fifths <= MOST_KEY:
        raise EditError("A key has at most seven sharps or flats.")
    for part in root.findall("part"):
        known = list(contexts(part))
        if not 0 <= index < len(known):
            raise EditError(f"No bar number {index + 1}")
        measure = known[index].measure
        written = key_for_part(part, fifths)
        drop_attribute(measure, "key")
        if index == 0 or known[index - 1].fifths != written:
            key = ET.Element("key")
            ET.SubElement(key, "fifths").text = str(written)
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
        filled = [max((line_end(line) for line in read_measure(item.measure, item.divisions).lines.values()), default=Fraction(0)) for item in known]
        for context in known:
            timeline = read_measure(context.measure, context.divisions)
            ends = [line_end(line) for line in timeline.lines.values()]
            if not ends:
                continue
            problem = None
            # Printed editions split a bar across a repeat or a line, as bars "4" and "4a".
            split = any(0 <= other < len(known) and filled[other] + filled[context.index] == context.length for other in (context.index - 1, context.index + 1))
            if max(ends) > context.length:
                problem = "long"
            elif max(ends) < context.length and not context.is_pickup and context.index > 0 and not split:
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


def prints_title(credit: ET.Element, old: set[str]) -> bool:
    """Say whether a credit prints the title or the composer, which are the ones set_title writes."""
    kinds = {found.text for found in credit.findall("credit-type")}
    if kinds:
        return bool(kinds & {"title", "composer"})
    # Older files don't say what a credit is, so it's known by its words.
    return " ".join((found.text or "").strip() for found in credit.findall("credit-words")).strip() in old


def set_title(root: ET.Element, title: str, composer: str) -> None:
    """Set the title and composer, in the file and on the printed page.

    Verovio only prints a composer from a credit, and if a score has any credit it ignores the
    work title, so both are written as credits as well. Any other credit, such as a subtitle or a
    copyright line, stays as it was.
    """
    title, composer = title.strip(), composer.strip()
    if len(title) > MOST_TITLE_LETTERS or len(composer) > MOST_TITLE_LETTERS:
        raise EditError("That's too long for a title.")
    old = {text for text in [*title_of(root).values(), (root.findtext("movement-title") or "").strip()] if text}
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
    credits = root.findall("credit")
    replaced = [credit for credit in credits if prints_title(credit, old)]
    # Verovio prints credits in order, so the title goes where the old one was, or above the rest.
    position = list(root).index((replaced or credits or [root.find("part-list")])[0])
    for credit in replaced:
        root.remove(credit)
    for offset, (kind, text, justify) in enumerate((("title", title, "center"), ("composer", composer, "right"))):
        if not text:
            continue
        credit = ET.Element("credit", page="1")
        ET.SubElement(credit, "credit-type").text = kind
        ET.SubElement(credit, "credit-words", justify=justify, valign="top").text = text
        root.insert(position + (offset if title else 0), credit)


def part_attributes(instrument: Instrument, divisions: int, fifths: int, beats: int, beat_type: int) -> ET.Element:
    """Make the opening of a part: what it counts in, its key and time, and its clefs."""
    attributes = ET.Element("attributes")
    ET.SubElement(attributes, "divisions").text = str(divisions)
    ET.SubElement(ET.SubElement(attributes, "key"), "fifths").text = str(fifths)
    time = ET.SubElement(attributes, "time")
    ET.SubElement(time, "beats").text = str(beats)
    ET.SubElement(time, "beat-type").text = str(beat_type)
    staves = len(instrument.clefs)
    if staves > 1:
        ET.SubElement(attributes, "staves").text = str(staves)
    for number, name in enumerate(instrument.clefs, start=1):
        sign, line, shift = CLEF_SHAPES[name]
        element = ET.SubElement(attributes, "clef", number=str(number)) if staves > 1 else ET.SubElement(attributes, "clef")
        ET.SubElement(element, "sign").text = sign
        ET.SubElement(element, "line").text = line
        if shift:
            ET.SubElement(element, "clef-octave-change").text = str(shift)
    if instrument.steps or instrument.semitones or instrument.octaves:
        transpose = ET.SubElement(attributes, "transpose")
        ET.SubElement(transpose, "diatonic").text = str(instrument.steps)
        ET.SubElement(transpose, "chromatic").text = str(instrument.semitones)
        if instrument.octaves:
            ET.SubElement(transpose, "octave-change").text = str(instrument.octaves)
    return attributes


def score_part(part_id: str, instrument: Instrument) -> ET.Element:
    element = ET.Element("score-part", id=part_id)
    ET.SubElement(element, "part-name", {"print-object": "no"} if instrument.hidden_name else {}).text = instrument.label
    inner = ET.SubElement(element, "score-instrument", id=f"{part_id}-I1")
    ET.SubElement(inner, "instrument-name").text = instrument.label
    midi = ET.SubElement(element, "midi-instrument", id=f"{part_id}-I1")
    ET.SubElement(midi, "midi-program").text = str(instrument.program)
    return element


def blank_score(fifths: int = 0, beats: int = 4, beat_type: int = 4, clef: str = "treble", measures: int = 16) -> ET.Element:
    """Make an empty score to write in from scratch, for a kind of staff or an instrument."""
    if clef not in INSTRUMENTS:
        raise EditError("Choose a treble, bass or piano staff, or an instrument.")
    if not 1 <= measures <= MOST_BARS:
        raise EditError(f"A score can have 1 to {MOST_BARS} bars.")
    if beat_type not in BEAT_TYPES or not 1 <= beats <= 32:
        raise EditError("That isn't a time signature this app can use.")
    if not -MOST_KEY <= fifths <= MOST_KEY:
        raise EditError("A key has at most seven sharps or flats.")
    instrument = INSTRUMENTS[clef]
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(root, "part-list").append(score_part("P1", instrument))
    part = ET.SubElement(root, "part", id="P1")
    for index in range(measures):
        measure = blank_measure(Fraction(beats * 4, beat_type), START_DIVISIONS, len(instrument.clefs))
        part.append(measure)
        if index == 0:
            measure.insert(0, part_attributes(instrument, START_DIVISIONS, fifths, beats, beat_type))
    renumber(part)
    ensure_measure_ids(root)
    return root


def new_part_id(root: ET.Element) -> str:
    used = {part.get("id") for part in root.findall("part")}
    number = len(used) + 1
    while f"P{number}" in used:
        number += 1
    return f"P{number}"


def add_part(root: ET.Element, name: str) -> None:
    """Add an instrument below the others, with the same bars, keys, times and repeats."""
    if name not in INSTRUMENTS:
        raise EditError("That isn't an instrument this app knows.")
    parts = root.findall("part")
    if len(parts) >= MOST_PARTS:
        raise EditError(f"A score can have at most {MOST_PARTS} instruments.")
    fit_divisions(root)
    instrument = INSTRUMENTS[name]
    known = list(contexts(parts[0]))
    top_offset = 0
    found = parts[0].find("measure/attributes/transpose")
    if found is not None:
        top_offset = key_offset(int(found.findtext("diatonic") or 0), int(found.findtext("chromatic") or 0))
    offset = key_offset(instrument.steps, instrument.semitones) - top_offset
    part_id = new_part_id(root)
    root.find("part-list").append(score_part(part_id, instrument))
    part = ET.Element("part", id=part_id)
    list_position = list(root).index(parts[-1]) + 1
    root.insert(list_position, part)
    prefix = f"p{len(parts)}"
    staves = len(instrument.clefs)
    for context in known:
        timeline = read_measure(context.measure, context.divisions)
        length = max((line_end(line) for line in timeline.lines.values()), default=context.length) if context.is_pickup else context.length
        measure = blank_measure(length, START_DIVISIONS, staves)
        measure.set("id", prefix + (context.measure.get("id") or f"m{context.index}"))
        if context.is_pickup:
            measure.set("implicit", "yes")
        written = context.fifths + offset
        written = written - 12 * (1 if written > 0 else -1) if abs(written) > MOST_KEY else written
        if context.index == 0:
            measure.insert(0, part_attributes(instrument, START_DIVISIONS, written, context.beats, context.beat_type))
        else:
            carried = context.measure.find("attributes/key"), context.measure.find("attributes/time")
            if carried[0] is not None:
                key = ET.Element("key")
                ET.SubElement(key, "fifths").text = str(written)
                set_attribute(ensure_attributes(measure), key)
            if carried[1] is not None:
                set_attribute(ensure_attributes(measure), copy.deepcopy(carried[1]))
        for barline in context.measure.findall("barline"):
            if barline.get("location") == "left":
                measure.insert(0, copy.deepcopy(barline))
            else:
                measure.append(copy.deepcopy(barline))
        part.append(measure)
    renumber(part)


def remove_part(root: ET.Element, index: int) -> None:
    parts = root.findall("part")
    if len(parts) < 2:
        raise EditError("A score needs at least one instrument.")
    if not 0 <= index < len(parts):
        raise EditError("There's no such instrument.")
    doomed = parts[index]
    listing = root.find("part-list")
    for entry in listing.findall("score-part"):
        if entry.get("id") == doomed.get("id"):
            listing.remove(entry)
    root.remove(doomed)


def part_names(root: ET.Element) -> list[str]:
    return [entry.findtext("part-name") or "" for entry in root.findall("part-list/score-part")]


def part_of(root: ET.Element, note_id: str) -> int:
    for index, part in enumerate(root.findall("part")):
        if any(note.get("id") == note_id for note in part.iter("note")):
            return index
    raise EditError(f"No note with id {note_id}")


def only_part(root: ET.Element, index: int) -> ET.Element:
    """Return a copy of the score with one instrument, for printing that player's part."""
    parts = root.findall("part")
    if not 0 <= index < len(parts):
        raise EditError("There's no such instrument.")
    single = copy.deepcopy(root)
    kept = single.findall("part")[index]
    for part in single.findall("part"):
        if part is not kept:
            single.remove(part)
    listing = single.find("part-list")
    for entry in listing.findall("score-part"):
        if entry.get("id") != kept.get("id"):
            listing.remove(entry)
    return single


def toggle_line_break(root: ET.Element, index: int) -> None:
    """Start a new line at this bar, or stop doing so."""
    for part in root.findall("part")[:1]:
        measures = part.findall("measure")
        if not 0 <= index < len(measures):
            raise EditError(f"No bar number {index + 1}")
        if index == 0:
            raise EditError("The first bar already starts a line.")
        measure = measures[index]
        existing = [found for found in measure.findall("print") if (found.get("id") or "").startswith(BREAK_PREFIX)]
        for found in existing:
            measure.remove(found)
        if not existing:
            number = sum((found.get("id") or "").startswith(BREAK_PREFIX) for found in root.iter("print"))
            used = {found.get("id") for found in root.iter("print")}
            while f"{BREAK_PREFIX}{number}" in used:
                number += 1
            measure.insert(0, ET.Element("print", {"new-system": "yes", "id": f"{BREAK_PREFIX}{number}"}))


def set_bars_per_line(root: ET.Element, count: int) -> None:
    """Keep this many bars on each line, or leave it to the page if the count is 0."""
    if not 0 <= count <= MOST_BARS_PER_LINE:
        raise EditError(f"A line can have up to {MOST_BARS_PER_LINE} bars.")
    write_setting(root, BARS_PER_LINE, str(count) if count else None)


def bars_per_line(root: ET.Element) -> int:
    return int(read_setting(root, BARS_PER_LINE) or 0)


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
