"""Fretted instruments: their tuning and capo, a tablature staff, and the string and fret of each note.

A guitar can have a tablature staff linked under its standard staff, as MuseScore's "Guitar +
tablature" has it and as MusicXML stores it: a second staff in the part with a TAB clef, whose
<staff-details> tune each string and place the capo, holding a copy of every note with the string
and fret that play it in <technical>. The standard staff is the music. The TAB staff is made again
from it after every edit (see sync), so the two can't disagree, and each copy is named after its
note with "-tab" on the end, so a click on a fret number reaches the note it plays.

A copy keeps its string for as long as its note keeps its pitch and that string can still play
it. So a string someone chose holds through every edit that leaves the note alone, and through a
new tuning or capo, which only moves its fret. A note that's new, or has a new pitch, is given the
string a player would most likely use: one that's free at that moment, near where the hand already
is, with an open string while the hand is low on the neck, and every note of a chord on a string
of its own.

A guitar without tablature keeps a string chosen for a note on the note itself, as a <technical>
holding its <string> and <fret> (a <string> alone is a circled string number, a mark to read), and
the string goes onto the tablature when it's turned on and back onto the note when it's taken away.
A part can also be tablature alone, one TAB staff that is the music itself, drawn with its rhythm.

Frets are counted from the capo, as tablature is read, so with the capo at the second fret an
open string is a 0 and sounds two semitones above its tuning. Tunings are of the strings as they
sound, so a bass guitar's lowest string is E1 although its music is written an octave higher.
"""

import copy
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction

from pipeline.concert import transpositions
from pipeline.rhythm import NATURAL_SEMITONES, TAB_COPY, EditError, Event, contexts, is_tied, line_end, place, read_measure, write_measure
from pipeline.structure import (
    BASS_STRINGS,
    GUITAR_STRINGS,
    VOICES_PER_STAFF,
    at_sounding_pitch,
    ensure_attributes,
    instrument_of,
    opening_attributes,
    pitch_of_name,
    set_attribute,
    string_details,
    write_clef,
)

TAB_SIGN = "TAB"
MOST_CAPO = 12
MOST_FRETS = 24
FEWEST_STRINGS, MOST_STRINGS = 3, 10
LOWEST_STRING, HIGHEST_STRING = 12, 96  # C0 and C7, as MIDI keys: room for any bass or soprano tuning.
# Each tuning a guitar or a bass can be set to by name, from the bottom string up. Others have their own standard one.
TUNINGS = {
    GUITAR_STRINGS: {
        "standard": GUITAR_STRINGS,
        "dropD": ("D2", "A2", "D3", "G3", "B3", "E4"),
        "DADGAD": ("D2", "A2", "D3", "G3", "A3", "D4"),
        "openG": ("D2", "G2", "D3", "G3", "B3", "D4"),
        "openD": ("D2", "A2", "D3", "F#3", "A3", "D4"),
        "halfStepDown": ("Eb2", "Ab2", "Db3", "Gb3", "Bb3", "Eb4"),
    },
    BASS_STRINGS: {
        "standard": BASS_STRINGS,
        "dropD": ("D1", "A1", "D2", "G2"),
        "halfStepDown": ("Eb1", "Ab1", "Db2", "Gb2"),
    },
}
CUSTOM = "custom"
GRACE_LEAD = Fraction(1, 256)  # How long before its note a grace note is taken to be played, for choosing its string.
# A string someone chose for a note, which holds where one the program chose may be chosen again.
# It's the app's own mark on a fret number, and a saved file leaves it out (see technique.drawn_on_other_staves).
CHOSEN = "xrn-chosen"
# How a hand on the neck is judged, in frets. It covers four from where the first finger is, an open
# string is natural while it's this low, and a chord can stretch so far before it can't be played.
HAND_REACH = 3
LOW_POSITION = 5
COMFORTABLE_SPAN, LONGEST_SPAN = 3, 5
OPEN_HIGH_UP = 0.8  # What reaching back for an open string costs high on the neck, in frets.
LOWER_IS_EASIER = 0.02  # Lower frets break a tie: they're wider apart, but nearer the open strings.
STRETCH = 3.0
UNPLAYABLE = 1000.0
NAMES = {0: "C", 1: "C♯", 2: "D", 3: "E♭", 4: "E", 5: "F", 6: "F♯", 7: "G", 8: "A♭", 9: "A", 10: "B♭", 11: "B"}


def midi_of(step: str, alter: int, octave: int) -> int:
    return (octave + 1) * 12 + NATURAL_SEMITONES[step] + alter


def note_midi(note: ET.Element) -> int:
    pitch = note.find("pitch")
    return midi_of(pitch.findtext("step"), round(float(pitch.findtext("alter") or 0)), int(pitch.findtext("octave")))


def name_of(midi: int) -> str:
    return f"{NAMES[midi % 12]}{midi // 12 - 1}"


def tab_staves(part: ET.Element) -> list[str]:
    """Return the staves of a part that are tablature, by their numbers."""
    found = []
    for measure in part.findall("measure"):
        for attributes in measure.findall("attributes"):
            for clef in attributes.findall("clef"):
                number = clef.get("number", "1")
                if clef.findtext("sign") == TAB_SIGN and number not in found:
                    found.append(number)
    return sorted(found, key=int)


@dataclass
class Fretboard:
    """A fretted part: how its strings are tuned, the capo, and which staves show tablature.

    Strings are numbered as MusicXML numbers them, from 1 for the highest, and listed from the bottom
    line of a TAB staff up, which is the other way round.
    """

    names: tuple[str, ...]
    capo: int
    frets: int
    standard: tuple[str, ...]
    tab: list[str] = field(default_factory=list)
    source: str | None = None  # The standard staff the first TAB staff copies, if there is one.
    staves: int = 1

    @property
    def opens(self) -> list[int]:
        """The sound of each open string with no capo, from the bottom line up."""
        return [midi_of(*pitch_of_name(name)) for name in self.names]

    @property
    def count(self) -> int:
        return len(self.names)

    def open_of(self, string: int) -> int:
        return self.opens[self.count - string]

    def fret(self, string: int, midi: int) -> int | None:
        """Return the fret that plays this sound on this string, counted from the capo, if it has one.

        A capo doesn't add frets: above it are the instrument's frets less the ones behind it.
        """
        if not 1 <= string <= self.count:
            return None
        fret = midi - self.open_of(string) - self.capo
        return fret if 0 <= fret <= self.frets - self.capo else None

    @property
    def tuning(self) -> str:
        """Name the tuning, if it's one of the instrument's own."""
        return next((name for name, strings in tunings_for(self.standard).items() if strings == self.names), CUSTOM)

    @property
    def linked(self) -> str | None:
        return self.tab[0] if self.source and self.tab else None

    def describe(self) -> dict:
        return {"strings": list(self.names), "tuning": self.tuning, "capo": self.capo, "frets": self.frets, "tab": bool(self.tab), "tabStaff": int(self.linked or self.tab[0]) if self.tab else None, "tunings": sorted(tunings_for(self.standard))}


def tunings_for(standard: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    return TUNINGS.get(standard, {"standard": standard})


def tuning_of(details: ET.Element) -> tuple[str, ...]:
    """Read a <staff-details>'s tuning as pitch names, from the bottom line up."""
    found = []
    for tuning in sorted(details.findall("staff-tuning"), key=lambda element: int(element.get("line", "1"))):
        alter = round(float(tuning.findtext("tuning-alter") or 0))
        found.append(f"{tuning.findtext('tuning-step')}{'#' * alter if alter > 0 else 'b' * -alter}{tuning.findtext('tuning-octave')}")
    return tuple(found)


def tuned_details(part: ET.Element) -> ET.Element | None:
    """Return the <staff-details> that tune a part's strings, the TAB staff's first if it has one."""
    measure = part.find("measure")
    if measure is None:
        return None
    found = [details for attributes in measure.findall("attributes") for details in attributes.findall("staff-details") if details.find("staff-tuning") is not None]
    tabs = tab_staves(part)
    return next((details for details in found if details.get("number", "1") in tabs), found[0] if found else None)


def fretboard(part: ET.Element, entry: ET.Element | None) -> Fretboard | None:
    """Return a part's strings if it's a fretted instrument, or has a tablature staff."""
    instrument = instrument_of(entry)
    tabs = tab_staves(part)
    if not tabs and (instrument is None or not instrument.frets):
        return None
    measure = part.find("measure")
    staves = int(next((attributes.findtext("staves") for attributes in measure.findall("attributes") if attributes.findtext("staves")), "1")) if measure is not None else 1
    standard = instrument.strings if instrument is not None and instrument.strings else GUITAR_STRINGS
    details = tuned_details(part)
    names, capo = standard, 0
    if details is not None:
        names = tuning_of(details)
        capo = int(details.findtext("capo") or 0)
    elif tabs:
        lines = next((int(found.findtext("staff-lines")) for found in part.iter("staff-details") if found.findtext("staff-lines") and found.get("number", "1") == tabs[0]), len(standard))
        names = standard if lines == len(standard) else BASS_STRINGS if lines == len(BASS_STRINGS) else GUITAR_STRINGS
    frets = instrument.frets if instrument is not None and instrument.frets else MOST_FRETS
    sources = [str(number) for number in range(1, max(staves, 1) + 1) if str(number) not in tabs]
    return Fretboard(names, capo, frets, standard, tabs, sources[0] if tabs and sources else None, staves)


def boards(root: ET.Element) -> list[Fretboard | None]:
    entries = {entry.get("id"): entry for entry in root.findall("part-list/score-part")}
    return [fretboard(part, entries.get(part.get("id"))) for part in root.findall("part")]


def shifts_of(part: ET.Element) -> list[int]:
    """Return how many semitones each bar sounds from how it's written. While edited at concert pitch it's nothing."""
    found = transpositions(part)
    return [0 if at_sounding_pitch.get() else interval.semitones for interval in found]


@dataclass
class Placed:
    """A note on a TAB staff: when it sounds, its sound, and the string and fret that play it."""

    note: ET.Element  # What carries the string and fret: a copy on a linked TAB staff, else the note.
    source: ET.Element
    start: Fraction  # From the start of the part, in quarter notes.
    end: Fraction
    midi: int
    keep: int | None = None  # The string it had, if it may keep it.
    chosen: bool = False  # Whether that string was chosen for it, rather than by the program.
    string: int | None = None
    fret: int | None = None


def place_of(note: ET.Element) -> tuple[int, int] | None:
    """Return the string and fret a note is played at, where a <technical> of it holds both."""
    for technical in note.iterfind("notations/technical"):
        string, fret = (technical.findtext("string") or "").strip(), (technical.findtext("fret") or "").strip()
        if string.isdigit() and fret.isdigit():
            return int(string), int(fret)
    return None


def string_of(note: ET.Element) -> int | None:
    found = place_of(note)
    return found[0] if found else None


def string_number(note: ET.Element) -> int | None:
    """Return a note's circled string number: a <string> with no <fret> beside it, which is a mark to read."""
    for technical in note.iterfind("notations/technical"):
        found = (technical.findtext("string") or "").strip()
        if technical.find("fret") is None and found.isdigit():
            return int(found)
    return None


def tab_voice(voice: str, staff: str, taken: set[str]) -> str:
    """Number a copied voice as MusicXML numbers a staff's voices: voice 1 of staff 2 is voice 5.

    A file can number its voices any way, so a number already taken in the bar moves up to a free one.
    """
    number = (int(voice) - 1) % VOICES_PER_STAFF + 1 + VOICES_PER_STAFF * (int(staff) - 1)
    while str(number) in taken:
        number += 1
    taken.add(str(number))
    return str(number)


# What a fret number on a TAB staff shows of its note: its ties. Slides, bends, hammer-ons and the
# like are drawn there from the note (see decorate.py), as a copy of a mark joining two notes would
# be numbered like the note's own and taken for its other end. The rest belongs to the standard staff.
TAB_NOTATIONS = {"tied"}
LEFT_ON_STANDARD = {"lyric", "accidental", "beam", "stem", "notations", "staff", "voice", "play", "listen", "notehead", "notehead-text"}


def tab_copy(note: ET.Element, staff: str, voice: str) -> ET.Element:
    """Copy a note onto a TAB staff, with what tablature shows of it. Its rests aren't drawn."""
    copied = ET.Element("note", {"id": note.get("id") + TAB_COPY} if note.get("id") else {})
    if note.get("print-object") == "no":
        copied.set("print-object", "no")  # A note hidden on the staff is hidden on its tablature too.
    for child in note:
        if child.tag not in LEFT_ON_STANDARD:
            copied.append(copy.deepcopy(child))
    place(copied, element("voice", voice))
    place(copied, element("staff", staff))
    if note.find("pitch") is None:
        copied.set("print-object", "no")
    else:
        place(copied, element("stem", "none"))  # The standard staff above shows the rhythm.
    notations = note.find("notations")
    kept = ET.Element("notations")
    for child in notations if notations is not None else []:
        if child.tag in TAB_NOTATIONS:
            kept.append(copy.deepcopy(child))
    if len(kept):
        place(copied, kept)
    return copied


def element(tag: str, text: str) -> ET.Element:
    made = ET.Element(tag)
    made.text = text
    return made


def write_fret(note: ET.Element, string: int, fret: int) -> None:
    """Store the string and fret that play a note, in its <technical>.

    They go where its fret is already, or in a <technical> with no string number in it, so a
    circled number on a guitar's note without tablature stays a mark of its own.
    """
    notations = note.find("notations")
    if notations is None:
        notations = ET.Element("notations")
        place(note, notations)
    technicals = notations.findall("technical")
    technical = next((found for found in technicals if found.find("fret") is not None), None)
    technical = technical if technical is not None else next((found for found in technicals if found.find("string") is None), None)
    if technical is None:
        technical = ET.SubElement(notations, "technical")
    for tag in ("string", "fret"):
        for found in technical.findall(tag):
            technical.remove(found)
    technical.append(element("string", str(string)))
    technical.append(element("fret", str(fret)))


def clear_fret(note: ET.Element) -> None:
    """Take away the string and fret a note is played at, leaving its marks."""
    notations = note.find("notations")
    technical = next((found for found in note.iterfind("notations/technical") if found.find("fret") is not None), None)
    if technical is None:
        return
    for tag in ("string", "fret"):
        for found in technical.findall(tag):
            technical.remove(found)
    if len(technical) == 0:
        notations.remove(technical)
    if len(notations) == 0:
        note.remove(notations)


def hidden_rest(note: ET.Element) -> ET.Element:
    """Turn a copy whose note no string can play into a rest that isn't drawn, so the TAB staff's bar still adds up."""
    made = ET.Element("note", dict(note.attrib, **{"print-object": "no"}))
    made.append(ET.Element("rest"))
    for child in note:
        if child.tag in ("duration", "voice", "type", "dot", "time-modification", "staff"):
            made.append(copy.deepcopy(child))
    return made


def without_unplayable(event: Event, unplayable: set[int]) -> Event:
    """Leave out of a TAB staff's moment the notes no string can play, its grace notes too."""
    graces = [note for note in event.grace if id(note) not in unplayable]
    kept = [note for note in event.notes if id(note) not in unplayable]
    if not kept:
        return Event(event.onset, event.length, [hidden_rest(event.notes[0])], graces)
    if kept[0] is not event.notes[0] and kept[0].find("chord") is not None:
        kept[0].remove(kept[0].find("chord"))
    return Event(event.onset, event.length, kept, graces)


def note_cost(fret: int, hand: int) -> float:
    """What it takes to play a fret with the hand where it is: nothing within its reach."""
    if fret == 0:
        return 0.0 if hand <= LOW_POSITION else OPEN_HIGH_UP
    away = hand - fret if fret < hand else max(fret - hand - HAND_REACH, 0)
    return away + fret * LOWER_IS_EASIER


def span_cost(frets: list[int]) -> float:
    fretted = [fret for fret in frets if fret > 0]
    if not fretted:
        return 0.0
    span = max(fretted) - min(fretted)
    return max(span - COMFORTABLE_SPAN, 0) * STRETCH + (UNPLAYABLE / 10 if span > LONGEST_SPAN else 0)


def choose(free: list[Placed], taken: set[int], fixed: list[int], hand: int, board: Fretboard) -> list[tuple[int, int] | None]:
    """Find the strings for the notes of one moment that are easiest to play together.

    Each note gets a string of its own. If they can't all be played, as many as can be are.
    """
    options = [[(string, fret) for string in range(1, board.count + 1) if string not in taken and (fret := board.fret(string, item.midi)) is not None] for item in free]
    best: tuple[float, list] = (float("inf"), [None] * len(free))

    def search(number: int, used: set[int], chosen: list, cost: float) -> None:
        nonlocal best
        if cost >= best[0]:
            return
        if number == len(free):
            total = cost + span_cost([*fixed, *(choice[1] for choice in chosen if choice)])
            if total < best[0]:
                best = (total, list(chosen))
            return
        for string, fret in options[number]:
            if string not in used:
                search(number + 1, used | {string}, [*chosen, (string, fret)], cost + note_cost(fret, hand))
        search(number + 1, used, [*chosen, None], cost + UNPLAYABLE)

    search(0, set(), [], 0.0)
    return best[1]


def moved_hand(hand: int, frets: list[int]) -> int:
    """Return where the hand is after playing these frets: where it was, if it could reach them all."""
    fretted = [fret for fret in frets if fret > 0]
    if not fretted:
        return hand
    low, high = min(fretted), max(fretted)
    if hand <= low and high <= hand + HAND_REACH:
        return hand
    return low if low < hand else max(min(low, high - HAND_REACH), 1)


def assign(placed: list[Placed], board: Fretboard) -> None:
    """Give each note a string and fret, moment by moment through the part.

    A note tied from the one before carries on on its string, a string still sounding a longer note
    isn't free, and a note that may keep its string does unless another note at that moment had it
    first.
    """
    moments: dict[Fraction, list[Placed]] = {}
    for item in placed:
        moments.setdefault(item.start, []).append(item)
    ringing: list[Placed] = []
    tied: dict[int, Placed] = {}
    hand = 1
    for start in sorted(moments):
        ringing = [item for item in ringing if item.end > start]
        group = moments[start]
        # A grace note cuts short whatever was ringing, so it can take any string.
        busy = set() if all(item.source.find("grace") is not None for item in group) else {item.string for item in ringing}
        fixed: list[Placed] = []
        free: list[Placed] = []
        tied_on: list[Placed] = []
        for item in group:
            partner = tied.get(item.midi) if is_tied(item.source, "stop") else None
            if partner is not None and partner.end == start and partner.string not in {other.string for other in fixed}:
                item.string, item.fret = partner.string, partner.fret
                fixed.append(item)
                tied_on.append(item)
            elif item.keep is not None and item.keep not in busy and item.keep not in {other.string for other in fixed} and board.fret(item.keep, item.midi) is not None:
                item.string, item.fret = item.keep, board.fret(item.keep, item.midi)
                fixed.append(item)
            else:
                free.append(item)
        taken = busy | {item.string for item in fixed}
        for item, choice in zip(free, choose(free, taken, [item.fret for item in fixed], hand, board)):
            item.string, item.fret = choice if choice else (None, None)
        kept = [item for item in fixed if item not in tied_on and not item.chosen]
        if kept and span_cost([item.fret for item in group if item.fret is not None]) >= UNPLAYABLE / 10:
            # The strings these notes had make the moment unplayable, as a chord written a note at a
            # time would: they're chosen again together, all but the notes carrying on a tie.
            again = [*kept, *free]
            held = [item for item in fixed if item not in kept]
            for item, choice in zip(again, choose(again, busy | {item.string for item in held}, [item.fret for item in held], hand, board)):
                item.string, item.fret = choice if choice else (None, None)
        hand = moved_hand(hand, [item.fret for item in group if item.fret is not None])
        for item in group:
            if item.string is not None:
                ringing.append(item)
                if is_tied(item.source, "start"):
                    tied[item.midi] = item


def remembered(timeline, staves: set[str], shift: int) -> tuple[dict[str, tuple[int, int, bool]], dict[tuple[Fraction, int], int]]:
    """Read the strings a bar's TAB staves give their notes, by the note each copies and by where it
    sounds, with whether each was chosen for it."""
    by_note: dict[str, tuple[int, int, bool]] = {}
    by_place: dict[tuple[Fraction, int], int] = {}
    for key, line in timeline.lines.items():
        if key[0] not in staves:
            continue
        for event in line:
            for note in [*event.grace, *event.notes]:
                string = string_of(note)
                if note.find("pitch") is None or string is None:
                    continue
                midi = note_midi(note) + shift
                by_note[(note.get("id") or "").removesuffix(TAB_COPY)] = (string, midi, note.get(CHOSEN) == "yes")
                by_place.setdefault((event.onset, midi), string)
    return by_note, by_place


def sync_part(part: ET.Element, board: Fretboard) -> None:
    """Copy a part's standard staff onto its TAB staff, and give every note there a string and fret.

    A TAB staff with no standard staff to copy is the music itself, and its notes are given strings
    where they are.
    """
    linked = board.linked
    shifts = shifts_of(part)
    placed: list[Placed] = []
    bars = []
    start = Fraction(0)
    for context in contexts(part):
        shift = shifts[context.index] if context.index < len(shifts) else 0
        timeline = read_measure(context.measure, context.divisions)
        by_note, by_place = remembered(timeline, set(board.tab), shift)
        if linked:
            for key in [key for key in timeline.lines if key[0] == linked]:
                del timeline.lines[key]
            taken = {key[1] for key in timeline.lines}
            for key, line in list(timeline.lines.items()):
                if key[0] != board.source:
                    continue
                copies = []
                voice = tab_voice(key[1], linked, taken)
                for event in sorted(line, key=lambda item: item.onset):
                    made = [tab_copy(note, linked, voice) for note in event.notes]
                    graces = [tab_copy(note, linked, voice) for note in event.grace]
                    copies.append(Event(event.onset, event.length, made, graces))
                    # Grace notes are played just before their note, one after another, so each is a
                    # moment of its own, over before the note it leads to.
                    leads = [(note, copied, start + event.onset - GRACE_LEAD * (len(graces) - number), GRACE_LEAD) for number, (note, copied) in enumerate(zip(event.grace, graces))]
                    for note, copied, begins, length in [*leads, *((note, copied, start + event.onset, event.length) for note, copied in zip(event.notes, made))]:
                        if copied.find("pitch") is None:
                            continue
                        midi = note_midi(note) + shift
                        kept = by_note.get(note.get("id") or "")
                        keep = kept[0] if kept and kept[1] == midi else by_place.get((event.onset, midi))
                        chosen = bool(kept and kept[1] == midi and kept[2])
                        if keep is None:
                            # A string kept on the note while there was no tablature, or its circled number.
                            own = place_of(note)
                            keep = own[0] if own else string_number(note)
                            chosen = keep is not None
                        placed.append(Placed(copied, note, begins, begins + length, midi, keep, chosen))
                        if place_of(note) is not None:
                            clear_fret(note)  # The tablature keeps it from now on.
                timeline.lines[(linked, voice)] = copies
        for key, line in timeline.lines.items():
            if key[0] in board.tab and key[0] != linked:
                for event in line:
                    for note in event.notes:
                        if note.find("pitch") is not None:
                            placed.append(Placed(note, note, start + event.onset, start + event.end, note_midi(note) + shift, string_of(note), note.get(CHOSEN) == "yes"))
        bars.append((context, timeline))
        start += max((line_end(line) for line in timeline.lines.values()), default=Fraction(0)) or context.length
    assign(placed, board)
    unplayable = set()
    for item in placed:
        if item.string is None:
            unplayable.add(id(item.note))
            if item.note is item.source:
                clear_fret(item.note)
        else:
            write_fret(item.note, item.string, item.fret)
            if item.chosen and item.string == item.keep:
                item.note.set(CHOSEN, "yes")
            else:
                item.note.attrib.pop(CHOSEN, None)
    if linked:
        for context, timeline in bars:
            for key, line in timeline.lines.items():
                if key[0] == linked:
                    timeline.lines[key] = [without_unplayable(event, unplayable) for event in line]
            write_measure(context.measure, timeline, context.divisions)


def check_places(part: ET.Element, board: Fretboard) -> None:
    """Forget the string kept on a note of a guitar without tablature once it no longer plays the note there.

    That's when the note's pitch has changed, as a string chosen on the tablature is. A new tuning
    or capo moves the frets themselves (see set_strings).
    """
    shifts = shifts_of(part)
    for context in contexts(part):
        shift = shifts[context.index] if context.index < len(shifts) else 0
        for note in context.measure.iter("note"):
            found = place_of(note)
            if found is not None and (note.find("pitch") is None or board.fret(found[0], note_midi(note) + shift) != found[1]):
                clear_fret(note)


def sync(root: ET.Element) -> None:
    """Bring every TAB staff in line with its standard staff, after any edit."""
    for part, board in zip(root.findall("part"), boards(root)):
        if board is not None and board.tab:
            sync_part(part, board)
        elif board is not None:
            check_places(part, board)


def unplayable_reason(board: Fretboard, midi: int) -> str:
    opens = board.opens
    if midi < min(opens) + board.capo:
        return f"{name_of(midi)} is below this instrument's lowest string."
    if midi > max(opens) + board.frets:
        return f"{name_of(midi)} is above the {board.frets}th fret of the highest string."
    return f"No string is free to play {name_of(midi)} here."


def problems(root: ET.Element) -> list[dict]:
    """List the notes no string can play, which their TAB staff leaves out, and say why."""
    found = []
    for number, (part, board) in enumerate(zip(root.findall("part"), boards(root))):
        if board is None or not board.tab:
            continue
        shifts = shifts_of(part)
        copies = {note.get("id"): note for note in part.iter("note") if (note.findtext("staff") or "1") == board.linked}
        for context in contexts(part):
            for note in context.measure.iter("note"):
                # A linked TAB staff's notes are its standard staff's, and a TAB staff of its own is the music.
                if (note.findtext("staff") or "1") != (board.source or board.tab[0]) or note.find("pitch") is None or note.find("grace") is not None:
                    continue
                copied = copies.get((note.get("id") or "") + TAB_COPY) if board.linked else note
                if copied is None or string_of(copied) is None:
                    midi = note_midi(note) + (shifts[context.index] if context.index < len(shifts) else 0)
                    found.append({"note": note.get("id"), "part": number, "reason": unplayable_reason(board, midi)})
    return found


def part_and_board(root: ET.Element, number: int) -> tuple[ET.Element, Fretboard]:
    parts = root.findall("part")
    if not 0 <= number < len(parts):
        raise EditError("There's no such instrument.")
    board = boards(root)[number]
    if board is None:
        raise EditError("Only a fretted instrument has a tuning, a capo and tablature.")
    return parts[number], board


def checked_strings(names: list[str]) -> tuple[str, ...]:
    if not FEWEST_STRINGS <= len(names) <= MOST_STRINGS:
        raise EditError(f"An instrument here has {FEWEST_STRINGS} to {MOST_STRINGS} strings.")
    found = []
    for name in names:
        step, alter, octave = pitch_of_name(name)
        if not LOWEST_STRING <= midi_of(step, alter, octave) <= HIGHEST_STRING:
            raise EditError(f"{name} is too far from any string's tuning.")
        found.append(f"{step}{'#' * alter if alter > 0 else 'b' * -alter}{octave}")
    return tuple(found)


def first_attributes(part: ET.Element) -> ET.Element:
    return ensure_attributes(part.find("measure"))


def remove_details(part: ET.Element) -> None:
    """Take the tuning out of a part's first bar, wherever it was kept."""
    for attributes in opening_attributes(part.find("measure")):
        for details in attributes.findall("staff-details"):
            if details.find("staff-tuning") is not None or details.find("capo") is not None:
                attributes.remove(details)


def write_board(part: ET.Element, names: tuple[str, ...], capo: int, standard: tuple[str, ...]) -> None:
    """Store a part's tuning and capo where MusicXML keeps them: on its TAB staff, or else its own staff.

    A guitar with no tablature in standard tuning and no capo needs nothing written at all.
    """
    remove_details(part)
    tabs = tab_staves(part)
    if tabs:
        set_attribute(first_attributes(part), string_details(tabs[0], names, capo))
    elif names != standard or capo:
        staves = int(part.findtext("measure/attributes/staves") or 1)
        set_attribute(first_attributes(part), string_details("1" if staves > 1 else None, names, capo, lines=5))


def set_strings(root: ET.Element, number: int, tuning: str | None = None, strings: list[str] | None = None, capo: int | None = None) -> None:
    """Tune a fretted instrument by a tuning's name or string by string, or move its capo.

    The notes keep their sound. Their frets follow, and each stays on its string while that string
    can still play it.
    """
    part, board = part_and_board(root, number)
    names = board.names
    if strings is not None:
        names = checked_strings(strings)
    elif tuning is not None:
        known = tunings_for(board.standard)
        if tuning not in known:
            raise EditError("That isn't a tuning this instrument has.")
        names = known[tuning]
    if capo is not None and not 0 <= capo <= MOST_CAPO:
        raise EditError(f"A capo goes at a fret from 0 to {MOST_CAPO}.")
    write_board(part, names, board.capo if capo is None else capo, board.standard)
    if not board.tab:
        refret(part, board, boards_of_part(root, part))


def boards_of_part(root: ET.Element, part: ET.Element) -> Fretboard:
    return boards(root)[root.findall("part").index(part)]


def refret(part: ET.Element, old: Fretboard, new: Fretboard) -> None:
    """Move the frets kept on a guitar's notes to a new tuning or capo, each on its string while that string can still play it."""
    shifts = shifts_of(part)
    for context in contexts(part):
        shift = shifts[context.index] if context.index < len(shifts) else 0
        for note in context.measure.iter("note"):
            found = place_of(note)
            if found is None or note.find("pitch") is None:
                continue
            midi = note_midi(note) + shift
            fret = new.fret(found[0], midi) if old.fret(found[0], midi) == found[1] else None
            if fret is None:
                clear_fret(note)
            else:
                write_fret(note, found[0], fret)


def set_tab(root: ET.Element, number: int, on: bool) -> None:
    """Show a fretted instrument's tablature on a staff linked under its own, or take it away.

    Taken away, each note keeps the string the tablature played it on. A part that is tablature
    alone becomes a standard staff in the instrument's own clef, its notes keeping their strings.
    """
    part, board = part_and_board(root, number)
    if on == bool(board.tab):
        raise EditError("This instrument already has tablature." if on else "This instrument has no tablature to take away.")
    measure = part.find("measure")
    attributes = first_attributes(part)
    if not on and not board.linked:
        if board.staves != 1:
            raise EditError("Only a tablature staff on its own becomes a standard staff.")
        entry = next((found for found in root.findall("part-list/score-part") if found.get("id") == part.get("id")), None)
        instrument = instrument_of(entry)
        clef_name = next((name for name in (instrument.clefs if instrument is not None else ()) if name != "tab"), "treble8")
        for found in opening_attributes(measure):
            for clef in found.findall("clef"):
                found.remove(clef)
            for details in found.findall("staff-details"):
                found.remove(details)
        clef = ET.Element("clef")
        write_clef(clef, clef_name)
        set_attribute(attributes, clef)
        for found in opening_attributes(measure):
            if len(found) == 0:
                measure.remove(found)
        write_board(part, board.names, board.capo, board.standard)
        return
    if on:
        if board.staves != 1:
            raise EditError("Only an instrument with one staff can have tablature added.")
        for clef in [clef for found in opening_attributes(measure) for clef in found.findall("clef")]:
            clef.set("number", "1")
        for found in attributes.findall("staves"):
            attributes.remove(found)
        set_attribute(attributes, element("staves", "2"))
        tab = ET.Element("clef", number="2")
        write_clef(tab, "tab")
        set_attribute(attributes, tab)
        write_board(part, board.names, board.capo, board.standard)
        return
    staff = board.tab[0]
    copies = {note.get("id"): note for note in part.iter("note") if (note.findtext("staff") or "1") == staff}
    for note in part.iter("note"):
        carried = copies.get((note.get("id") or "") + TAB_COPY)
        found = place_of(carried) if carried is not None and carried.find("pitch") is not None else None
        if found is not None and note.find("pitch") is not None:
            write_fret(note, *found)  # Kept on the note, for a guitar without tablature.
    for context in contexts(part):
        timeline = read_measure(context.measure, context.divisions)
        for key in [key for key in timeline.lines if key[0] == staff]:
            del timeline.lines[key]
        write_measure(context.measure, timeline, context.divisions)
        for found in context.measure.findall("attributes"):
            for clef in found.findall("clef"):
                if clef.get("number") == staff:
                    found.remove(clef)
    for found in attributes.findall("staves"):
        attributes.remove(found)
    if board.staves > 2:
        set_attribute(attributes, element("staves", str(board.staves - 1)))
    for clef in attributes.findall("clef"):
        if board.staves <= 2:
            clef.attrib.pop("number", None)
    for found in opening_attributes(measure):
        if len(found) == 0:
            measure.remove(found)
    write_board(part, board.names, board.capo, board.standard)


def copy_of(root: ET.Element, part: ET.Element, board: Fretboard, note: ET.Element) -> ET.Element | None:
    """Return what carries a note's string and fret: its copy on a linked TAB staff, or itself on a
    TAB staff, or on a guitar without tablature."""
    if (note.findtext("staff") or "1") in board.tab or not board.tab:
        return note
    if not board.linked:
        return None
    wanted = (note.get("id") or "") + TAB_COPY
    return next((found for found in part.iter("note") if found.get("id") == wanted), None)


def sounding_now(part: ET.Element, board: Fretboard, target: ET.Element) -> tuple[Fraction, list[tuple[ET.Element, Fraction, Fraction]]]:
    """Return when a note starts, and every note that carries a string, with its start and end:
    those on the TAB staves, or a guitar's own notes without tablature."""
    found = []
    start = Fraction(0)
    begins = None
    for context in contexts(part):
        timeline = read_measure(context.measure, context.divisions)
        for key, line in timeline.lines.items():
            for event in line:
                # Grace notes are each a moment of their own just before their note (see sync_part).
                moments = [(note, start + event.onset - GRACE_LEAD * (len(event.grace) - number), GRACE_LEAD) for number, note in enumerate(event.grace)]
                moments += [(note, start + event.onset, event.length) for note in event.notes]
                for note, begun, length in moments:
                    if note is target:
                        begins = begun
                    if (key[0] in board.tab or not board.tab) and note.find("pitch") is not None:
                        found.append((note, begun, begun + length))
        start += max((line_end(line) for line in timeline.lines.values()), default=Fraction(0)) or context.length
    return begins, found


def set_string(root: ET.Element, part: ET.Element, board: Fretboard, note: ET.Element, string: int, midi: int, tied: list[ET.Element]) -> None:
    """Play a note on a chosen string. Its fret follows, and a string that can't play it is refused with the reason."""
    if not 1 <= string <= board.count:
        raise EditError(f"This instrument has strings 1 to {board.count}.")
    fret = midi - board.open_of(string) - board.capo
    tuned = name_of(board.open_of(string) + board.capo)
    if fret < 0:
        raise EditError(f"String {string} can't play {name_of(midi)}: the lowest it plays is {tuned}{' with the capo on' if board.capo else ''}.")
    if fret > board.frets - board.capo:
        top = f"fret {board.frets - board.capo} from the capo" if board.capo else f"fret {board.frets}"
        raise EditError(f"String {string} only goes up to {top}, and {name_of(midi)} would be fret {fret}.")
    carriers = [carrier for member in tied if (carrier := copy_of(root, part, board, member)) is not None]
    if not carriers:
        raise EditError(unplayable_reason(board, midi))
    mine = {id(carrier) for carrier in carriers}
    for member, carrier in zip(tied, carriers):
        begins, others = sounding_now(part, board, carrier)
        for other, start, end in others:
            if id(other) not in mine and start <= begins < end and string_of(other) == string:
                raise EditError(f"String {string} is already playing {name_of(note_midi(other) + (midi - note_midi(note)))} here.")
    for carrier in carriers:
        write_fret(carrier, string, fret)
        carrier.set(CHOSEN, "yes")


def choose_string(root: ET.Element, note_id: str, string: int) -> None:
    """Play a note on the string someone chose, with the notes tied to it."""
    from pipeline.edit import context_of, find_note, tied_notes  # The editor needs nothing from here.

    note = find_note(root, note_id)
    if note.find("pitch") is None:
        raise EditError("A rest isn't played on any string.")
    part, context = context_of(root, note)
    board = boards(root)[root.findall("part").index(part)]
    if board is None:
        raise EditError("Only a fretted instrument's notes are played on a string chosen here.")
    shifts = shifts_of(part)
    midi = note_midi(note) + (shifts[context.index] if context.index < len(shifts) else 0)
    if board.linked and copy_of(root, part, board, note) is None and board.fret(string, midi) is not None:
        raise EditError(unplayable_reason(board, midi))
    chain = [member for member in tied_notes(root, note) if member.find("pitch") is not None] if note.find("grace") is None else [note]
    set_string(root, part, board, note, string, midi, chain)


def play_at_fret(root: ET.Element, note_id: str, fret: int, string: int | None = None) -> int:
    """Make a note the sound of a fret on a string, as a fret number typed on the tablature does, and say which string.

    It's the string the note is played on now, unless another is given. The notes tied to it change
    with it, and it's spelled as the key spells that sound. The string itself is chosen once the
    tablature has the new pitch (see choose_string).
    """
    from pipeline.edit import context_of, find_note, spell_sound, tied_notes, write_alter  # The editor needs nothing from here.
    from pipeline.rhythm import repair_ties, respell

    note = find_note(root, note_id)
    if note.find("pitch") is None:
        raise EditError("A rest has no fret. Choose a note, or write one first.")
    part, context = context_of(root, note)
    board = boards(root)[root.findall("part").index(part)]
    if board is None:
        raise EditError("Only a fretted instrument's notes have frets.")
    if string is None:
        carrier = copy_of(root, part, board, note)
        string = string_of(carrier) if carrier is not None else None
        if string is None:
            raise EditError("No string plays this note yet. Choose one for it first.")
    if not 1 <= string <= board.count:
        raise EditError(f"This instrument has strings 1 to {board.count}.")
    if not 0 <= fret <= board.frets:
        raise EditError(f"This instrument's frets go from 0 to {board.frets}.")
    shifts = shifts_of(part)
    written = board.open_of(string) + board.capo + fret - (shifts[context.index] if context.index < len(shifts) else 0)
    step, alter, octave = spell_sound(written - 12, context.fifths, written > note_midi(note))
    if not 0 <= octave <= 9:
        raise EditError("That's too far from the staff.")
    chain = tied_notes(root, note) if note.find("grace") is None else [note]
    for member in chain:
        pitch = member.find("pitch")
        pitch.find("step").text = step
        pitch.find("octave").text = str(octave)
        write_alter(pitch, alter)
    for member in chain:
        owner, found = context_of(root, member)
        respell(found)
        repair_ties(owner, found.index)
    return string
