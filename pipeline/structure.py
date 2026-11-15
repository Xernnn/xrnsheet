"""Change a score's shape: bars, key and time, repeats and endings, and the title.

This is also where bars are checked against the time signature.
"""

import copy
import re
import xml.etree.ElementTree as ET
from contextvars import ContextVar
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
    repair_lines,
    repair_slurs,
    repair_ties,
    respell,
    rests,
    units,
    write_measure,
)

START_DIVISIONS = 96  # Fits every plain, dotted and triplet length a person can enter.
MOST_BARS = 1000
ATTRIBUTE_ORDER = ["footnote", "level", "divisions", "key", "time", "staves", "part-symbol", "instruments", "clef", "staff-details", "transpose", "for-part", "directive", "measure-style"]
BEAT_TYPES = {1, 2, 4, 8, 16, 32}
# Each clef as MusicXML writes it: its sign, the line it sits on, and how many octaves its 8 or 15 moves
# the music. A percussion clef has no line, and notes under it stand where a treble clef puts them.
CLEF_SHAPES = {
    "treble": ("G", "2", 0), "treble8va": ("G", "2", 1), "treble15ma": ("G", "2", 2), "treble8": ("G", "2", -1), "treble15mb": ("G", "2", -2),
    "french": ("G", "1", 0), "soprano": ("C", "1", 0), "mezzo": ("C", "2", 0), "alto": ("C", "3", 0), "tenor": ("C", "4", 0),
    "baritoneC": ("C", "5", 0), "baritoneF": ("F", "3", 0), "bass": ("F", "4", 0), "bass8va": ("F", "4", 1), "bass15ma": ("F", "4", 2),
    "bass8vb": ("F", "4", -1), "bass15mb": ("F", "4", -2), "subbass": ("F", "5", 0), "percussion": ("percussion", "", 0),
}
# The voice numbers MusicXML files conventionally use, four to a staff.
VOICES_PER_STAFF = 4
MOST_KEY = 7
MOST_PARTS = 40
BREAK_PREFIX = "linebreak"  # Marks a line break a person asked for, unlike those a page was read with.
BARS_PER_LINE = "xrnsheet-bars-per-line"
MOST_BARS_PER_LINE = 16
# Set while a score shown at concert pitch is being edited there, where every part's notes and keys
# already stand as they sound, so no part's key is offset from another's.
at_sounding_pitch: ContextVar[bool] = ContextVar("at_sounding_pitch", default=False)


@dataclass(frozen=True)
class Instrument:
    """What a part needs to know: its staves, and how far it sounds from how it's written.

    The sound is MusicXML's name for the instrument, which other programs read to know what it is.
    A few instruments are shown at concert pitch in a clef of their own, as a baritone saxophone is
    shown in a bass clef. The rest take an octave clef there if they sound an octave away.

    A string instrument has its strings' tuning, as they sound, from the bottom line of a TAB staff
    up, which is the lowest string first except on a ukulele. A fretted one has its frets too, and a
    "tab" clef is a tablature staff linked under its standard staff (see fretted.py).
    """

    label: str
    clefs: tuple[str, ...] = ("treble",)
    steps: int = 0
    semitones: int = 0
    octaves: int = 0
    program: int = 1  # General MIDI, counted from 1.
    hidden_name: bool = False
    family: str = "Plain staves"
    sound: str = ""
    concert_clefs: tuple[str, ...] = ()
    strings: tuple[str, ...] = ()
    frets: int = 0


GRAND_STAFF = ("treble", "bass")
TAB_CLEF = "tab"  # A tablature staff, which takes its lines from the instrument's strings.
GUITAR_STRINGS = ("E2", "A2", "D3", "G3", "B3", "E4")
BASS_STRINGS = ("E1", "A1", "D2", "G2")
# MuseScore's common instruments, family by family in its order, with their clefs and transpositions.
INSTRUMENTS = {
    "treble": Instrument("Music", hidden_name=True),
    "bass": Instrument("Music", ("bass",), hidden_name=True),
    "piccolo": Instrument("Piccolo", octaves=1, program=73, family="Woodwinds", sound="wind.flutes.flute.piccolo"),
    "flute": Instrument("Flute", program=74, family="Woodwinds", sound="wind.flutes.flute"),
    "alto-flute": Instrument("Alto Flute", steps=-3, semitones=-5, program=74, family="Woodwinds", sound="wind.flutes.flute.alto"),
    "soprano-recorder": Instrument("Soprano Recorder", ("treble8va",), program=75, family="Woodwinds", sound="wind.flutes.recorder.soprano"),
    "alto-recorder": Instrument("Alto Recorder", program=75, family="Woodwinds", sound="wind.flutes.recorder.alto"),
    "oboe": Instrument("Oboe", program=69, family="Woodwinds", sound="wind.reed.oboe"),
    "english-horn": Instrument("English Horn", steps=-4, semitones=-7, program=70, family="Woodwinds", sound="wind.reed.english-horn"),
    "eb-clarinet": Instrument("Clarinet in E♭", steps=2, semitones=3, program=72, family="Woodwinds", sound="wind.reed.clarinet.eflat"),
    "clarinet": Instrument("Clarinet in B♭", steps=-1, semitones=-2, program=72, family="Woodwinds", sound="wind.reed.clarinet.bflat"),
    "a-clarinet": Instrument("Clarinet in A", steps=-2, semitones=-3, program=72, family="Woodwinds", sound="wind.reed.clarinet.a"),
    "bass-clarinet": Instrument("Bass Clarinet", steps=-1, semitones=-2, octaves=-1, program=72, family="Woodwinds", sound="wind.reed.clarinet.bass"),
    "soprano-sax": Instrument("Soprano Saxophone", steps=-1, semitones=-2, program=65, family="Woodwinds", sound="wind.reed.saxophone.soprano"),
    "alto-sax": Instrument("Alto Saxophone", steps=-5, semitones=-9, program=66, family="Woodwinds", sound="wind.reed.saxophone.alto"),
    "tenor-sax": Instrument("Tenor Saxophone", steps=-1, semitones=-2, octaves=-1, program=67, family="Woodwinds", sound="wind.reed.saxophone.tenor"),
    "baritone-sax": Instrument("Baritone Saxophone", steps=-5, semitones=-9, octaves=-1, program=68, family="Woodwinds", sound="wind.reed.saxophone.baritone", concert_clefs=("bass",)),
    "bassoon": Instrument("Bassoon", ("bass",), program=71, family="Woodwinds", sound="wind.reed.bassoon"),
    "contrabassoon": Instrument("Contrabassoon", ("bass",), octaves=-1, program=71, family="Woodwinds", sound="wind.reed.contrabassoon"),
    "horn": Instrument("Horn in F", steps=-4, semitones=-7, program=61, family="Brass", sound="brass.french-horn"),
    "c-trumpet": Instrument("Trumpet in C", program=57, family="Brass", sound="brass.trumpet.c"),
    "trumpet": Instrument("Trumpet in B♭", steps=-1, semitones=-2, program=57, family="Brass", sound="brass.trumpet.bflat"),
    "cornet": Instrument("Cornet in B♭", steps=-1, semitones=-2, program=57, family="Brass", sound="brass.cornet"),
    "flugelhorn": Instrument("Flugelhorn", steps=-1, semitones=-2, program=57, family="Brass", sound="brass.flugelhorn"),
    "trombone": Instrument("Trombone", ("bass",), program=58, family="Brass", sound="brass.trombone"),
    "bass-trombone": Instrument("Bass Trombone", ("bass",), program=58, family="Brass", sound="brass.trombone.bass"),
    "euphonium": Instrument("Euphonium", ("bass",), program=59, family="Brass", sound="brass.euphonium"),
    "tuba": Instrument("Tuba", ("bass",), program=59, family="Brass", sound="brass.tuba"),
    "timpani": Instrument("Timpani", ("bass",), program=48, family="Percussion - Pitched", sound="drum.timpani"),
    "glockenspiel": Instrument("Glockenspiel", octaves=2, program=10, family="Percussion - Pitched", sound="pitched-percussion.glockenspiel"),
    "xylophone": Instrument("Xylophone", octaves=1, program=14, family="Percussion - Pitched", sound="pitched-percussion.xylophone"),
    "vibraphone": Instrument("Vibraphone", program=12, family="Percussion - Pitched", sound="pitched-percussion.vibraphone"),
    "marimba": Instrument("Marimba", GRAND_STAFF, program=13, family="Percussion - Pitched", sound="pitched-percussion.marimba"),
    "piano": Instrument("Piano", GRAND_STAFF, hidden_name=True, family="Keyboards", sound="keyboard.piano"),
    "electric-piano": Instrument("Electric Piano", GRAND_STAFF, program=5, family="Keyboards", sound="keyboard.piano.electric"),
    "harpsichord": Instrument("Harpsichord", GRAND_STAFF, program=7, family="Keyboards", sound="keyboard.harpsichord"),
    "celesta": Instrument("Celesta", GRAND_STAFF, octaves=1, program=9, family="Keyboards", sound="keyboard.celesta"),
    "organ": Instrument("Organ", ("treble", "bass", "bass"), program=20, family="Keyboards", sound="keyboard.organ"),
    "accordion": Instrument("Accordion", GRAND_STAFF, program=22, family="Free Reed", sound="keyboard.accordion"),
    "voice": Instrument("Voice", program=53, family="Vocals", sound="voice.vocals"),
    "soprano": Instrument("Soprano", program=53, family="Vocals", sound="voice.soprano"),
    "mezzo-soprano": Instrument("Mezzo-soprano", program=53, family="Vocals", sound="voice.mezzo-soprano"),
    "alto": Instrument("Alto", program=53, family="Vocals", sound="voice.alto"),
    "tenor": Instrument("Tenor", ("treble8",), program=53, family="Vocals", sound="voice.tenor"),
    "baritone": Instrument("Baritone", ("bass",), program=53, family="Vocals", sound="voice.baritone"),
    "bass-voice": Instrument("Bass", ("bass",), program=53, family="Vocals", sound="voice.bass"),
    "guitar": Instrument("Guitar", ("treble8",), program=25, family="Strings - Plucked", sound="pluck.guitar.nylon-string", strings=GUITAR_STRINGS, frets=19),
    "guitar-tab": Instrument("Guitar", ("treble8", TAB_CLEF), program=25, family="Strings - Plucked", sound="pluck.guitar.nylon-string", strings=GUITAR_STRINGS, frets=19),
    "acoustic-guitar": Instrument("Acoustic Guitar", ("treble8",), program=26, family="Strings - Plucked", sound="pluck.guitar.acoustic", strings=GUITAR_STRINGS, frets=20),
    "acoustic-guitar-tab": Instrument("Acoustic Guitar", ("treble8", TAB_CLEF), program=26, family="Strings - Plucked", sound="pluck.guitar.acoustic", strings=GUITAR_STRINGS, frets=20),
    "electric-guitar": Instrument("Electric Guitar", ("treble8",), program=28, family="Strings - Plucked", sound="pluck.guitar.electric", strings=GUITAR_STRINGS, frets=24),
    "electric-guitar-tab": Instrument("Electric Guitar", ("treble8", TAB_CLEF), program=28, family="Strings - Plucked", sound="pluck.guitar.electric", strings=GUITAR_STRINGS, frets=24),
    "bass-guitar": Instrument("Bass Guitar", ("bass",), octaves=-1, program=34, family="Strings - Plucked", sound="pluck.bass", strings=BASS_STRINGS, frets=20),
    "bass-guitar-tab": Instrument("Bass Guitar", ("bass", TAB_CLEF), octaves=-1, program=34, family="Strings - Plucked", sound="pluck.bass", strings=BASS_STRINGS, frets=20),
    # Tablature alone, with its rhythm drawn on it, as many guitarists and bassists read.
    "guitar-tab-only": Instrument("Guitar", (TAB_CLEF,), program=25, family="Strings - Plucked", sound="pluck.guitar.nylon-string", strings=GUITAR_STRINGS, frets=19),
    "bass-guitar-tab-only": Instrument("Bass Guitar", (TAB_CLEF,), octaves=-1, program=34, family="Strings - Plucked", sound="pluck.bass", strings=BASS_STRINGS, frets=20),
    "electric-bass": Instrument("Electric Bass", ("bass",), octaves=-1, program=34, family="Strings - Plucked", sound="pluck.bass.electric", strings=BASS_STRINGS, frets=24),
    "harp": Instrument("Harp", GRAND_STAFF, program=47, family="Strings - Plucked", sound="pluck.harp"),
    "ukulele": Instrument("Ukulele", program=25, family="Strings - Plucked", sound="pluck.ukulele", strings=("G4", "C4", "E4", "A4"), frets=15),
    "mandolin": Instrument("Mandolin", program=26, family="Strings - Plucked", sound="pluck.mandolin", strings=("G3", "D4", "A4", "E5"), frets=17),
    "banjo": Instrument("Banjo", ("treble8",), program=106, family="Strings - Plucked", sound="pluck.banjo", strings=("G4", "D3", "G3", "B3", "D4"), frets=22),
    "violin": Instrument("Violin", program=41, family="Strings - Bowed", sound="strings.violin", strings=("G3", "D4", "A4", "E5")),
    "viola": Instrument("Viola", ("alto",), program=42, family="Strings - Bowed", sound="strings.viola", strings=("C3", "G3", "D4", "A4")),
    "cello": Instrument("Cello", ("bass",), program=43, family="Strings - Bowed", sound="strings.cello", strings=("C2", "G2", "D3", "A3")),
    "double-bass": Instrument("Double Bass", ("bass",), octaves=-1, program=44, family="Strings - Bowed", sound="strings.contrabass", strings=("E1", "A1", "D2", "G2")),
}
# The names a plain staff goes by where instruments are chosen, as "Music" says nothing there.
STAFF_NAMES = {
    "treble": "Treble staff", "bass": "Bass staff", "guitar-tab": "Guitar + tablature", "acoustic-guitar-tab": "Acoustic Guitar + tablature",
    "electric-guitar-tab": "Electric Guitar + tablature", "bass-guitar-tab": "Bass Guitar + tablature",
    "guitar-tab-only": "Guitar, tablature only", "bass-guitar-tab-only": "Bass Guitar, tablature only",
}
BARLINE_ORDER = ["bar-style", "footnote", "level", "wavy-line", "segno", "coda", "fermata", "ending", "repeat"]
# A time signature can be drawn as a sign instead of numbers: C for 4/4 and a struck-through C for 2/2.
TIME_SYMBOLS = {"common": (4, 4), "cut": (2, 2)}
REPEAT_SIDES = {"forward": ("left", "heavy-light"), "backward": ("right", "light-heavy")}
# How the barline ending a measure can be drawn, and what MusicXML calls each. A single one is the plain line.
BAR_STYLES = {"single": None, "double": "light-light", "dashed": "dashed", "final": "light-heavy", "heavy": "heavy", "heavyDouble": "heavy-heavy", "dotted": "dotted"}
FINAL_BARLINE = BAR_STYLES["final"]
MOST_TITLE_LETTERS = 200
PAGE_TOP_SHARE = 0.75  # A credit in the top quarter of the first page may be its title or composer.


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


def opening_attributes(measure: ET.Element) -> list[ET.Element]:
    """Return the attributes a bar starts with. A clef change partway through isn't one of them."""
    found = []
    for child in measure:
        if child.tag in ("note", "backup", "forward"):
            break
        if child.tag == "attributes":
            found.append(child)
    return found


def ensure_attributes(measure: ET.Element) -> ET.Element:
    """Return the attributes a bar starts with, adding them if it has none."""
    found = opening_attributes(measure)
    if found:
        return found[0]
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
            # Only what the first bar starts with moves. A clef change partway through stays in it.
            for position, attributes in enumerate(opening_attributes(measures[0])):
                measures[0].remove(attributes)
                measure.insert(position, attributes)
        if index < len(measures):
            children = list(part)
            part.insert(children.index(measures[index]), measure)
        else:
            part.append(measure)
            move_final_barline(measures[-1], measure)
        renumber(part)
        repair_ties(part, min(index, len(measures)))


def carry_attributes(source: ET.Element, target: ET.Element) -> None:
    """Move anything still needed out of a bar about to be deleted.

    A key, time or clef applies until it's changed, so deleting the bar that states it would
    silently change every bar after it. What's carried is what's in force where the bar ends, so
    the last of each counts, and it goes at the start of the next bar unless that bar starts by
    changing it.
    """
    for child in reversed([child for attributes in source.findall("attributes") for child in attributes]):
        stated = [other for attributes in opening_attributes(target) for other in attributes]
        if not any(other.tag == child.tag and other.get("number") == child.get("number") for other in stated):
            set_attribute(ensure_attributes(target), child)


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
            else:
                move_final_barline(doomed, kept)
        part.remove(doomed)
        renumber(part)
        repair_ties(part, max(index - 1, 0))
        repair_slurs(part)
        repair_lines(part)


def key_offset(steps: int, semitones: int) -> int:
    """Return how many sharps a part is written with beyond the key it sounds in.

    A trumpet sounds a tone lower than written, so its music is written with two sharps more.
    """
    return -fifths_of(steps, semitones)


def key_for_part(part: ET.Element, fifths: int) -> int:
    """Return a part's written key, wrapped round to a key that exists if it goes past seven."""
    found = part.find("measure/attributes/transpose")
    offset = 0 if found is None or at_sounding_pitch.get() else key_offset(int(found.findtext("diatonic") or 0), int(found.findtext("chromatic") or 0))
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


def write_clef(clef: ET.Element, name: str) -> None:
    """Fill in a clef element as one of the clefs this app knows, or as a tablature staff's TAB."""
    sign, line, shift = ("TAB", "5", 0) if name == TAB_CLEF else CLEF_SHAPES[name]
    ET.SubElement(clef, "sign").text = sign
    if line:
        ET.SubElement(clef, "line").text = line
    if shift:
        ET.SubElement(clef, "clef-octave-change").text = str(shift)


def set_clef(root: ET.Element, index: int, part_number: int, staff: str, name: str) -> None:
    """Change a staff's clef from this bar on. Notes keep their pitch, so they move on the staff.

    A tablature staff's clef isn't one to change: its lines are strings, not pitches.
    """
    if name not in CLEF_SHAPES:
        raise EditError("That isn't a clef this app knows.")
    parts = root.findall("part")
    known = list(contexts(parts[part_number]))
    if not 0 <= index < len(known):
        raise EditError(f"No bar number {index + 1}")
    if known[index].clefs.get(staff) == "TAB":
        raise EditError("A tablature staff keeps its TAB clef.")
    measure = known[index].measure
    for attributes in measure.findall("attributes"):
        for found in attributes.findall("clef"):
            if found.get("number", "1") == staff:
                attributes.remove(found)
        if len(attributes) == 0:
            measure.remove(attributes)
    clef = ET.Element("clef", number=staff) if known[index].staves > 1 else ET.Element("clef")
    write_clef(clef, name)
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


def time_element(beats: int, beat_type: int, symbol: str = "") -> ET.Element:
    """Make a time signature. Common and cut time are 4/4 and 2/2 drawn as their signs."""
    if beat_type not in BEAT_TYPES or not 1 <= beats <= 32:
        raise EditError("That isn't a time signature this app can use.")
    if symbol and TIME_SYMBOLS.get(symbol) != (beats, beat_type):
        raise EditError("Common time is 4/4 and cut time is 2/2.")
    time = ET.Element("time", {"symbol": symbol} if symbol else {})
    ET.SubElement(time, "beats").text = str(beats)
    ET.SubElement(time, "beat-type").text = str(beat_type)
    return time


def time_symbol(time: ET.Element | None) -> str:
    """Say which sign a time signature is drawn as, or nothing for plain numbers."""
    found = time.get("symbol") if time is not None else None
    return found if found in TIME_SYMBOLS else ""


def set_time(root: ET.Element, index: int, beats: int, beat_type: int, symbol: str = "") -> None:
    """Set the time from this bar until the next time change.

    The notes stay in their bars. Recognition has usually read the bars correctly and only
    misread the signature, and if it's wrong the bar check will show which bars don't fit.
    """
    wanted = time_element(beats, beat_type, symbol)
    fit_divisions(root)
    for part in root.findall("part"):
        known = list(contexts(part))
        if not 0 <= index < len(known):
            raise EditError(f"No bar number {index + 1}")
        measure = known[index].measure
        drop_attribute(measure, "time")
        # The sign in force before this bar: 4/4 after common time is a change that has to be shown.
        before = ""
        for context in known[:index]:
            earlier = context.measure.find("attributes/time")
            before = time_symbol(earlier) if earlier is not None else before
        if index == 0 or (known[index - 1].beats, known[index - 1].beat_type, before) != (beats, beat_type, symbol):
            set_attribute(ensure_attributes(measure), copy.deepcopy(wanted))
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


def set_barline(root: ET.Element, index: int, style: str) -> None:
    """Draw the barline that ends a measure as a double, dashed, final, heavy or dotted one, or plainly again.

    It replaces a repeat sign there, as choosing a barline does in MuseScore. An ending's bracket stays.
    """
    if style not in BAR_STYLES:
        raise EditError("That isn't a barline this app can draw.")
    for part in root.findall("part"):
        measures = part.findall("measure")
        if not 0 <= index < len(measures):
            raise EditError(f"No bar number {index + 1}")
        barline = add_barline(measures[index], "right")
        for found in [*barline.findall("bar-style"), *barline.findall("repeat")]:
            barline.remove(found)
        if BAR_STYLES[style]:
            set_barline_child(barline, element_with_text("bar-style", BAR_STYLES[style]))
        tidy_barline(measures[index], barline, keep_style=True)


def is_final(barline: ET.Element | None) -> bool:
    """Say whether this is the thin and thick barline that ends a piece, and not a repeat drawn the same way."""
    return barline is not None and barline.findtext("bar-style") == FINAL_BARLINE and barline.find("repeat") is None


def move_final_barline(source: ET.Element, target: ET.Element) -> None:
    """Keep the final barline at the end when the last measure changes.

    The measure that's now last takes it, unless its own barline already says something else.
    """
    barline, existing = barline_of(source, "right"), barline_of(target, "right")
    if not is_final(barline) or (existing is not None and (existing.find("bar-style") is not None or existing.find("repeat") is not None)):
        return
    style = barline.find("bar-style")
    barline.remove(style)
    tidy_barline(source, barline, keep_style=True)
    set_barline_child(add_barline(target, "right"), style)


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
        tidy_barline(measure, barline, keep_style=True)  # The style is the barline's own, or its repeat's.


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


def credit_text(credit: ET.Element) -> str:
    """The words a credit prints, on one line."""
    return " ".join(" ".join((found.text or "").split()) for found in credit.findall("credit-words")).strip()


def number_or(text: str | None, otherwise: float | None) -> float | None:
    try:
        return float(text)
    except (TypeError, ValueError):
        return otherwise


def printed_title(root: ET.Element) -> tuple[str, str]:
    """Find the title and composer among the words printed on the first page, which don't say what they are.

    MuseScore 2 wrote a score's title and composer only that way. The title is the largest words at
    the top of the page, the composer's name the highest on its right, as a score is laid out.
    """
    height = number_or(root.findtext("defaults/page-layout/page-height"), None)
    found = []
    for credit in root.findall("credit"):
        words, text = credit.findall("credit-words"), credit_text(credit)
        if credit.get("page", "1") != "1" or credit.find("credit-type") is not None or not text:
            continue
        high = max((number_or(item.get("default-y"), -1.0) for item in words), default=-1.0)
        if height and high >= 0 and high < height * PAGE_TOP_SHARE:
            continue  # A footer, such as a copyright line, however large.
        size = max(number_or(item.get("font-size"), 0.0) for item in words)
        right = any("right" in (item.get("justify"), item.get("halign")) for item in words)
        found.append((text, size, high, right))
    title = max((item for item in found if not item[3]), key=lambda item: (item[1], item[2]), default=None)
    composer = max((item for item in found if item[3]), key=lambda item: item[2], default=None)
    return (title[0] if title else "", composer[0] if composer else "")


def title_of(root: ET.Element) -> dict:
    """Say what a score is called and who wrote it, as the top bar, the inspector and the library show it.

    set_title writes them as the work title and the composer, with credits to print them. A file
    from elsewhere may name its title as the movement's instead, or have only the credits that
    print them: those that say they're the title and composer, or failing that, the words on the
    first page that look it. A score titled here always has a work, if only with an empty title
    when it was cleared, and then the other words on its page are left as they are.
    """
    creator = next((found for found in root.iter("creator") if found.get("type") == "composer"), None)
    credited = {}
    for credit in root.findall("credit"):
        for kind in credit.findall("credit-type"):
            credited.setdefault((kind.text or "").strip(), credit_text(credit))
    title = (root.findtext("work/work-title") or "").strip() or (root.findtext("movement-title") or "").strip() or credited.get("title", "")
    composer = ((creator.text or "").strip() if creator is not None else "") or credited.get("composer", "")
    if root.find("work") is None and not (title and composer):
        guessed_title, guessed_composer = printed_title(root)
        title, composer = title or guessed_title, composer or guessed_composer
    return {"title": title, "composer": composer}


def prints_title(credit: ET.Element, old: set[str]) -> bool:
    """Say whether a credit prints the title or the composer, which are the ones set_title writes."""
    kinds = {found.text for found in credit.findall("credit-type")}
    if kinds:
        return bool(kinds & {"title", "composer"})
    # Older files don't say what a credit is, so it's known by its words.
    return credit_text(credit) in old


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
    # A movement title is the title title_of finds when the work title is empty, so the one chosen
    # here takes its place too, or clearing the title would bring it back.
    for movement in root.findall("movement-title"):
        root.remove(movement)
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


def pitch_of_name(name: str) -> tuple[str, int, int]:
    """Read a pitch written as "E2", "F#3" or "Bb3": its letter, its sharps or flats and its octave."""
    found = re.fullmatch(r"([A-Ga-g])(#{1,2}|b{1,2}|♯|♭)?(-?\d)", name.strip())
    if found is None:
        raise EditError(f"{name!r} isn't a note such as E2, F#3 or Bb3.")
    sign = found.group(2) or ""
    alter = {"#": 1, "##": 2, "♯": 1, "b": -1, "bb": -2, "♭": -1}.get(sign, 0)
    return found.group(1).upper(), alter, int(found.group(3))


def string_details(staff: str | None, strings: tuple[str, ...], capo: int = 0, lines: int | None = None) -> ET.Element:
    """Say how a string instrument is tuned, as MusicXML's <staff-details> does: string by string from the bottom line up."""
    details = ET.Element("staff-details", {"number": staff} if staff else {})
    ET.SubElement(details, "staff-lines").text = str(len(strings) if lines is None else lines)
    for line, name in enumerate(strings, start=1):
        step, alter, octave = pitch_of_name(name)
        tuning = ET.SubElement(details, "staff-tuning", line=str(line))
        ET.SubElement(tuning, "tuning-step").text = step
        if alter:
            ET.SubElement(tuning, "tuning-alter").text = str(alter)
        ET.SubElement(tuning, "tuning-octave").text = str(octave)
    if capo:
        ET.SubElement(details, "capo").text = str(capo)
    return details


def part_attributes(instrument: Instrument, divisions: int, fifths: int, beats: int, beat_type: int, symbol: str = "") -> ET.Element:
    """Make the opening of a part: what it counts in, its key and time, and its clefs.

    A tablature staff has as many lines as the instrument has strings, and says how they're tuned.
    """
    attributes = ET.Element("attributes")
    ET.SubElement(attributes, "divisions").text = str(divisions)
    ET.SubElement(ET.SubElement(attributes, "key"), "fifths").text = str(fifths)
    attributes.append(time_element(beats, beat_type, symbol))
    staves = len(instrument.clefs)
    if staves > 1:
        ET.SubElement(attributes, "staves").text = str(staves)
    for number, name in enumerate(instrument.clefs, start=1):
        write_clef(ET.SubElement(attributes, "clef", number=str(number)) if staves > 1 else ET.SubElement(attributes, "clef"), name)
    for number, name in enumerate(instrument.clefs, start=1):
        if name == TAB_CLEF:
            attributes.append(string_details(str(number), instrument.strings))
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
    if instrument.sound:
        ET.SubElement(inner, "instrument-sound").text = instrument.sound
    midi = ET.SubElement(element, "midi-instrument", id=f"{part_id}-I1")
    ET.SubElement(midi, "midi-program").text = str(instrument.program)
    return element


def blank_score(fifths: int = 0, beats: int = 4, beat_type: int = 4, clef: str = "treble", measures: int = 16, symbol: str = "") -> ET.Element:
    """Make an empty score to write in from scratch, for a kind of staff or an instrument.

    The key is the one the music sounds in, so a B flat trumpet asked for C major reads D major, as
    any instrument added to it later would.
    """
    if clef not in INSTRUMENTS:
        raise EditError("Choose a treble, bass or piano staff, or an instrument.")
    if not 1 <= measures <= MOST_BARS:
        raise EditError(f"A score can have 1 to {MOST_BARS} bars.")
    if beat_type not in BEAT_TYPES or not 1 <= beats <= 32:
        raise EditError("That isn't a time signature this app can use.")
    if not -MOST_KEY <= fifths <= MOST_KEY:
        raise EditError("A key has at most seven sharps or flats.")
    instrument = INSTRUMENTS[clef]
    written = fifths + key_offset(instrument.steps, instrument.semitones)
    written = written - 12 * (1 if written > 0 else -1) if abs(written) > MOST_KEY else written
    root = ET.Element("score-partwise", version="4.0")
    ET.SubElement(root, "part-list").append(score_part("P1", instrument))
    part = ET.SubElement(root, "part", id="P1")
    for index in range(measures):
        measure = blank_measure(Fraction(beats * 4, beat_type), START_DIVISIONS, len(instrument.clefs))
        part.append(measure)
        if index == 0:
            measure.insert(0, part_attributes(instrument, START_DIVISIONS, written, beats, beat_type, symbol))
    # A piece ends with a final barline, which is how MuseScore starts a score too.
    set_barline_child(add_barline(measure, "right"), element_with_text("bar-style", FINAL_BARLINE))
    renumber(part)
    ensure_measure_ids(root)
    return root


def instrument_of(part_entry: ET.Element | None) -> Instrument | None:
    """Return which of this app's instruments a part is, by the sound MusicXML names for it."""
    sound = part_entry.findtext("score-instrument/instrument-sound") if part_entry is not None else None
    return next((instrument for instrument in INSTRUMENTS.values() if sound and instrument.sound == sound), None)


def instrument_list() -> list[dict]:
    """List what a part can be written for, grouped the way the chooser shows them."""
    return [{"id": name, "label": STAFF_NAMES.get(name, instrument.label), "family": instrument.family} for name, instrument in INSTRUMENTS.items()]


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
    offset = 0 if at_sounding_pitch.get() else key_offset(instrument.steps, instrument.semitones) - top_offset
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
            measure.insert(0, part_attributes(instrument, START_DIVISIONS, written, context.beats, context.beat_type, time_symbol(context.measure.find("attributes/time"))))
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


def part_staves(root: ET.Element) -> list[int]:
    """Return how many staves each instrument has, so a staff on the page can be traced to its instrument."""
    return [int(part.findtext("measure/attributes/staves") or 1) for part in root.findall("part")]


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
