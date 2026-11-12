"""How a player is told to play a note: guitar techniques, fingerings and string numbers, and the words
and lines string players read over a passage.

All of it is MusicXML's own notation, so it goes wherever the score goes:

- On a note, in <technical>: <fingering>, <pluck> (p, i, m, a), <string> (a circled number on a
  guitar, a Roman numeral on a violin), <bend> with its <bend-alter> in semitones, <harmonic>
  (<natural/> or <artificial/>), and the first half of a <hammer-on> or <pull-off>. A dead note
  has an "x" <notehead>.
- Between two notes, in <notations>: <slide> and <glissando>, and the halves of a hammer-on or a
  pull-off, each end numbered as slurs are. A vibrato is an <ornaments><wavy-line>, drawn as
  MuseScore's vibrato.
- Over a passage, as <direction>s: palm mute ("P.M.") and let ring, and a violinist's "sul G",
  are words followed by a dashed line, ended by a direction of their own. "pizz." and "arco" are
  words with a <sound pizzicato>, which playback follows, and "III pos." is words.

A guitar's techniques are marked on its standard staff. Its tablature staff shows them too, as
the drawing reads them from the note each fret number copies.
"""

import copy
import xml.etree.ElementTree as ET
from fractions import Fraction

from pipeline import fretted
from pipeline.concert import transposition_at
from pipeline.edit import Spot, context_of, events_between, find_note, locate, next_note_id
from pipeline.marks import add_at, drop_if_empty, free_number, make_direction, notations_of, several_staves
from pipeline.rhythm import EditError, contexts, place, read_measure, repair_lines, repair_slurs, respell
from pipeline.structure import element_with_text, instrument_of, pitch_of_name
from pipeline.transpose import Interval, move_pitch

BOWED, PLUCKED, KEYBOARDS = "Strings - Bowed", "Strings - Plucked", "Keyboards"
# The fingers each family numbers: a pianist's thumb is 1, a string player's first finger is 1 and 0 is an open string.
FINGERS = {KEYBOARDS: "12345", BOWED: "01234", PLUCKED: "01234"}
ANY_FINGER = "012345"
PLUCKS = {"p", "i", "m", "a", "c"}  # Thumb, index, middle, ring and little finger, as classical guitarists name them.
BENDS = {"half": 1, "full": 2, "oneAndHalf": 3}  # In semitones, as <bend-alter> has them.
HARMONICS = {"natural", "artificial"}
TOUCHED_ABOVE = Interval(3, 5)  # An artificial harmonic's touched note is a fourth above the stopped one.
# Marks joining one note to the next: where each lives, its element, and the line or letter it carries.
PAIRS = {
    "hammerOn": ("technical", "hammer-on", "H"),
    "pullOff": ("technical", "pull-off", "P"),
    "slide": (None, "slide", "solid"),
    "glissando": (None, "glissando", "wavy"),
}
VIBRATO_GLYPH = "wiggleVibrato"  # SMuFL's name for a vibrato's wavy line, as MusicXML's smufl attribute takes it.
TEXT_LINES = {"palmMute": ("P.M.", "below"), "letRing": ("let ring", "above")}
SUL_PLACES = {"pont.", "tasto"}
ROMAN = ["½", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]
POSITION_WORDS = " pos."


def family_of(root: ET.Element, part: ET.Element) -> str | None:
    entry = next((found for found in root.findall("part-list/score-part") if found.get("id") == part.get("id")), None)
    instrument = instrument_of(entry)
    return instrument.family if instrument is not None else None


def pitched(root: ET.Element, note_ids: list[str]) -> list[ET.Element]:
    """Return the chosen notes, each once, leaving out rests. A fret number stands for its note."""
    found: dict[int, ET.Element] = {}
    for note_id in note_ids:
        note = find_note(root, note_id)
        if note.find("pitch") is not None:
            found.setdefault(id(note), note)
    if not found:
        raise EditError("Choose a note first.")
    return list(found.values())


def technical_of(note: ET.Element) -> ET.Element:
    notations = notations_of(note)
    technical = notations.find("technical")
    if technical is None:
        technical = ET.SubElement(notations, "technical")
    return technical


def drop_technical(note: ET.Element, tag: str) -> None:
    """Take every mark of one kind out of a note, and the <technical> holding it once it's empty."""
    for technical in note.findall("notations/technical"):
        for found in technical.findall(tag):
            technical.remove(found)
        if len(technical) == 0:
            note.find("notations").remove(technical)
    drop_if_empty(note)


def marks_of(note: ET.Element, tag: str) -> list[str]:
    return [(found.text or "").strip() for found in note.iterfind(f"notations/technical/{tag}")]


def set_text_mark(notes: list[ET.Element], tag: str, values: list[str], placements: list[str | None] | None = None) -> None:
    """Give each note one mark of a kind, as one fingering or one string number. Asked for again, it comes off."""
    if all(marks_of(note, tag) == [value] for note, value in zip(notes, values)):
        for note in notes:
            drop_technical(note, tag)
        return
    for note, value, placement in zip(notes, values, placements or [None] * len(notes)):
        drop_technical(note, tag)
        mark = element_with_text(tag, value)
        if placement:
            mark.set("placement", placement)
        technical_of(note).append(mark)


def set_fingering(root: ET.Element, note_ids: list[str], text: str) -> None:
    """Finger the chosen notes: one finger for them all, or a finger for each in turn, as "1 2 3" or "123".

    A pianist's fingers go above the right hand's staff and below the left hand's.
    """
    fingers = [character for character in text if not character.isspace() and character not in ",-"]
    notes = pitched(root, note_ids)
    if not fingers:
        raise EditError("Type a finger.")
    if len(fingers) == 1:
        fingers *= len(notes)
    if len(fingers) != len(notes):
        raise EditError(f"Type one finger, or one for each of the {len(notes)} notes chosen.")
    placements = []
    for note, finger in zip(notes, fingers):
        family = family_of(root, context_of(root, note)[0])
        allowed = FINGERS.get(family, ANY_FINGER)
        if finger not in allowed:
            raise EditError(f"Fingers here are numbered {allowed[0]} to {allowed[-1]}.")
        placements.append(("below" if (note.findtext("staff") or "1") != "1" else "above") if family == KEYBOARDS else None)
    set_text_mark(notes, "fingering", fingers, placements)


def set_pluck(root: ET.Element, note_ids: list[str], letter: str) -> None:
    """Mark which finger of the right hand plucks: p, i, m or a."""
    letter = letter.strip().lower()
    if letter not in PLUCKS:
        raise EditError("A plucking finger is p, i, m, a or c.")
    notes = pitched(root, note_ids)
    set_text_mark(notes, "pluck", [letter] * len(notes))


def string_names(root: ET.Element, part: ET.Element) -> tuple[str, ...]:
    """Return a string instrument's strings as they sound, from the lowest up."""
    board = fretted.boards(root)[root.findall("part").index(part)]
    if board is not None:
        return board.names
    entry = next((found for found in root.findall("part-list/score-part") if found.get("id") == part.get("id")), None)
    instrument = instrument_of(entry)
    return instrument.strings if instrument is not None else ()


def set_string_number(root: ET.Element, note_ids: list[str], number: int) -> None:
    """Say which string plays each note, in a circle on a guitar and as a Roman numeral on a violin.

    On a guitar with tablature, the tablature moves to that string too. Without a number, a guitar's
    notes are marked with the strings its tablature has them on.
    """
    notes = pitched(root, note_ids)
    values = []
    for note in notes:
        part, context = context_of(root, note)
        strings = string_names(root, part)
        board = fretted.boards(root)[root.findall("part").index(part)]
        if not strings:
            raise EditError("Only a string instrument's notes have string numbers.")
        chosen = number
        if not chosen and board is not None and board.tab:
            carrier = fretted.copy_of(root, part, board, note)
            chosen = fretted.string_of(carrier) if carrier is not None else None
        if not chosen or not 1 <= chosen <= len(strings):
            raise EditError(f"This instrument has strings 1 to {len(strings)}.")
        midi = fretted.note_midi(note) + transposition_at(part, context.index).semitones
        lowest = fretted.midi_of(*pitch_of_name(strings[len(strings) - chosen]))
        if (board is not None and board.fret(chosen, midi) is None) or (board is None and midi < lowest):
            raise EditError(f"String {chosen} can't play {fretted.name_of(midi)}.")
        values.append(str(chosen))
    marked = all(marks_of(note, "string") == [value] for note, value in zip(notes, values))
    set_text_mark(notes, "string", values)
    if not marked:
        for note, value in zip(notes, values):
            part, _ = context_of(root, note)
            board = fretted.boards(root)[root.findall("part").index(part)]
            if board is not None and board.tab:
                fretted.choose_string(root, note.get("id"), int(value))


def toggle_bend(root: ET.Element, note_ids: list[str], amount: str) -> None:
    """Bend the chosen notes up by half a tone, a whole one or one and a half, or take the bend away."""
    if amount not in BENDS:
        raise EditError("A bend is half, full or oneAndHalf.")
    notes = pitched(root, note_ids)
    wanted = str(BENDS[amount])
    if all([bend.findtext("bend-alter") for bend in note.iterfind("notations/technical/bend")] == [wanted] for note in notes):
        for note in notes:
            drop_technical(note, "bend")
        return
    for note in notes:
        drop_technical(note, "bend")
        bend = ET.SubElement(technical_of(note), "bend")
        ET.SubElement(bend, "bend-alter").text = wanted


def harmonic_kind(note: ET.Element) -> tuple[str | None, str | None]:
    """Say whether a note is a natural or an artificial harmonic, and which pitch of one it is."""
    harmonic = note.find("notations/technical/harmonic")
    if harmonic is None:
        return None, None
    kind = "artificial" if harmonic.find("artificial") is not None else "natural"
    which = next((found.tag for found in harmonic if found.tag in ("base-pitch", "touching-pitch", "sounding-pitch")), None)
    return kind, which


def touching_note(root: ET.Element, note: ET.Element) -> ET.Element | None:
    """Return the diamond a fourth above a violin's artificial harmonic, which is in the same chord."""
    event = locate(root, note.get("id")).event
    return next((other for other in event.notes if harmonic_kind(other) == ("artificial", "touching-pitch")), None)


def write_harmonic(note: ET.Element, kind: str, which: str | None = None) -> None:
    drop_technical(note, "harmonic")
    harmonic = ET.SubElement(technical_of(note), "harmonic")
    ET.SubElement(harmonic, kind)
    if which:
        ET.SubElement(harmonic, which)


def remove_harmonic(root: ET.Element, note: ET.Element) -> None:
    touched = touching_note(root, note) if harmonic_kind(note) == ("artificial", "base-pitch") else None
    drop_technical(note, "harmonic")
    if touched is not None:
        part, context = context_of(root, touched)
        context.measure.remove(touched)
        respell(context)


def add_touching_note(root: ET.Element, note: ET.Element) -> None:
    """Add the diamond a violinist touches a fourth above the stopped note, as one more note of its chord."""
    part, context = context_of(root, note)
    event = locate(root, note.get("id")).event
    step, alter, octave = move_pitch(note.findtext("pitch/step"), round(float(note.findtext("pitch/alter") or 0)), int(note.findtext("pitch/octave")), TOUCHED_ABOVE)
    touched = copy.deepcopy(note)
    touched.attrib.pop("id", None)
    for tag in ("tie", "accidental", "beam", "notations", "lyric", "chord", "notehead"):
        for found in touched.findall(tag):
            touched.remove(found)
    pitch = touched.find("pitch")
    for child in list(pitch):
        pitch.remove(child)
    ET.SubElement(pitch, "step").text = step
    if alter:
        ET.SubElement(pitch, "alter").text = str(alter)
    ET.SubElement(pitch, "octave").text = str(octave)
    place(touched, ET.Element("chord"))
    place(touched, ET.Element("notehead", filled="no"))
    touched.find("notehead").text = "diamond"
    write_harmonic(touched, "artificial", "touching-pitch")
    children = list(context.measure)
    context.measure.insert(children.index(event.notes[-1]) + 1, touched)
    respell(context)


def toggle_harmonic(root: ET.Element, note_ids: list[str], kind: str) -> None:
    """Make the chosen notes natural or artificial harmonics, or plain notes again.

    A natural harmonic has a small circle over it. A violin's artificial harmonic is the stopped note
    with a diamond a fourth above it, which sounds two octaves above the stopped note. A guitar's is
    the fretted note, marked A.H., and sounds an octave above it.
    """
    if kind not in HARMONICS:
        raise EditError("A harmonic is natural or artificial.")
    notes = [note for note in pitched(root, note_ids) if harmonic_kind(note)[1] != "touching-pitch"]
    if not notes:
        raise EditError("Choose the stopped note of the harmonic.")
    already = all(harmonic_kind(note)[0] == kind for note in notes)
    for note in notes:
        remove_harmonic(root, note)
        if already:
            continue
        part, _ = context_of(root, note)
        if kind == "artificial" and family_of(root, part) == BOWED:
            write_harmonic(note, kind, "base-pitch")
            add_touching_note(root, note)
        else:
            write_harmonic(note, kind)


def toggle_dead_note(root: ET.Element, note_ids: list[str]) -> None:
    """Mark the chosen notes as dead notes, muted and drawn as an x, or take that away."""
    notes = pitched(root, note_ids)
    dead = all(note.findtext("notehead") == "x" for note in notes)
    for note in notes:
        for found in note.findall("notehead"):
            note.remove(found)
        if not dead:
            place(note, element_with_text("notehead", "x"))


def heads_between(root: ET.Element, first_id: str, last_id: str) -> tuple[Spot, list]:
    """Return the first note of every sounding event from one note to another, and the next one if only one was chosen."""
    start, chosen = events_between(root, first_id, last_id)
    sounding = [(context, event) for context, _, event in chosen if not event.is_rest]
    if not sounding:
        raise EditError("Choose notes first.")
    if len(sounding) == 1:
        after = next_note_id(locate(root, sounding[0][1].notes[0].get("id")))
        if after is None:
            raise EditError("There's no note after this one to go to.")
        spot = locate(root, after)
        sounding.append((spot.context, spot.event))
    return start, [(context, event.notes[0]) for context, event in sounding]


def holder_of(note: ET.Element, group: str | None) -> ET.Element:
    return technical_of(note) if group else notations_of(note)


def pair_number(first: ET.Element, second: ET.Element, group: str | None, tag: str) -> str | None:
    """Return the number of a mark of this kind that starts on one note and stops on the other, if there is one."""
    path = f"notations/{group}/{tag}" if group else f"notations/{tag}"
    started = {found.get("number", "1") for found in first.iterfind(path) if found.get("type") == "start"}
    return next((found.get("number", "1") for found in second.iterfind(path) if found.get("type") == "stop" and found.get("number", "1") in started), None)


def remove_pair(first: ET.Element, second: ET.Element, group: str | None, tag: str, number: str) -> None:
    for note, kind in ((first, "start"), (second, "stop")):
        for holder in note.findall(f"notations/{group}") if group else note.findall("notations"):
            for found in holder.findall(tag):
                if found.get("type") == kind and found.get("number", "1") == number:
                    holder.remove(found)
            if group and len(holder) == 0:
                note.find("notations").remove(holder)
        drop_if_empty(note)


def toggle_pair(root: ET.Element, first_id: str, last_id: str, name: str) -> None:
    """Join each chosen note to the next with a hammer-on, pull-off, slide or glissando, or take them away.

    One note chosen is joined to the note after it. Several are joined one to the next, as 5h7h8.
    """
    if name not in PAIRS:
        raise EditError("That isn't a way of joining notes this app knows.")
    group, tag, look = PAIRS[name]
    start, heads = heads_between(root, first_id, last_id)
    pairs = list(zip(heads, heads[1:]))
    if all(pair_number(first[1], second[1], group, tag) for first, second in pairs):
        for first, second in pairs:
            remove_pair(first[1], second[1], group, tag, pair_number(first[1], second[1], group, tag))
        return
    for (first_context, first), (second_context, second) in pairs:
        if pair_number(first, second, group, tag):
            continue
        number = str(free_number(start.part, tag, first_context.index, second_context.index))
        begin = ET.Element(tag, {"type": "start", "number": number})
        if group:
            begin.text = look
        else:
            begin.set("line-type", look)
        holder_of(first, group).append(begin)
        holder_of(second, group).append(ET.Element(tag, {"type": "stop", "number": number}))
    repair_slurs(start.part)


def vibrato_ends(note: ET.Element, kind: str) -> list[ET.Element]:
    return [found for found in note.iterfind("notations/ornaments/wavy-line") if found.get("type") == kind]


def toggle_vibrato(root: ET.Element, first_id: str, last_id: str) -> None:
    """Draw a vibrato's wavy line over a note or a stretch, or take it away."""
    start, chosen = events_between(root, first_id, last_id)
    sounding = [(context, event.notes[0]) for context, _, event in chosen if not event.is_rest]
    if not sounding:
        raise EditError("A vibrato goes over notes.")
    (first_context, first), (last_context, last) = sounding[0], sounding[-1]
    stops = {found.get("number", "1") for found in vibrato_ends(last, "stop")}
    same = next((found for found in vibrato_ends(first, "start") if found.get("number", "1") in stops), None)
    if same is not None:
        number = same.get("number", "1")
        for note, kind in ((first, "start"), (last, "stop")):
            ornaments = note.find("notations/ornaments")
            for found in vibrato_ends(note, kind):
                if found.get("number", "1") == number:
                    ornaments.remove(found)
            if len(ornaments) == 0:
                note.find("notations").remove(ornaments)
            drop_if_empty(note)
        return
    number = str(free_number(start.part, "wavy-line", first_context.index, last_context.index))
    for note, kind in ((first, "start"), (last, "stop")):
        ornaments = notations_of(note).find("ornaments")
        if ornaments is None:
            ornaments = ET.SubElement(notations_of(note), "ornaments")
        ornaments.append(ET.Element("wavy-line", {"type": kind, "number": number, **({"smufl": VIBRATO_GLYPH} if kind == "start" else {})}))
    repair_slurs(start.part)


def text_lines(part: ET.Element) -> list[tuple[tuple, tuple | None, str]]:
    """List a part's text lines, each with both its ends and its words."""
    from pipeline.marks import placed_lines

    return [(begun, ended, " ".join((words.text or "").strip() for words in begun[1].iter("words"))) for begun, ended in placed_lines(part, "dashes")]


def remove_direction(end: tuple | None) -> None:
    if end is not None and any(child is end[1] for child in end[0]):
        end[0].remove(end[1])


def toggle_text_line(root: ET.Element, first_id: str, last_id: str, words: str, placement: str) -> None:
    """Put words with a dashed line over a stretch, as "P.M." or "let ring", or take them away.

    Two lines with the same words can't lie over each other on a staff, so one the new line overlaps goes.
    """
    begin, chosen = events_between(root, first_id, last_id)
    first_context, _, first_event = chosen[0]
    last_context, _, last_event = chosen[-1]
    staff = begin.key[0]
    starts, ends = (first_context.index, first_event.onset), (last_context.index, last_event.end)
    overlapping = []
    for line_start, line_stop, said in text_lines(begin.part):
        stop = line_stop[3] if line_stop is not None else (len(begin.part.findall("measure")), Fraction(0))
        if said == words and (line_start[1].findtext("staff") or "1") == staff and line_start[3] < ends and starts < stop:
            overlapping.append((line_start, line_stop))
    same = any(found[0][3] == starts and found[1] is not None and found[1][3] == ends for found in overlapping)
    for line_start, line_stop in overlapping:
        remove_direction(line_start)
        remove_direction(line_stop)
    if same:
        return
    number = str(free_number(begin.part, "dashes", first_context.index, last_context.index))
    opening = make_direction(placement, [element_with_text("words", words), ET.Element("dashes", type="start", number=number)], None, staff, several_staves(begin))
    closing = make_direction(placement, [ET.Element("dashes", type="stop", number=number)], None, staff, several_staves(begin))
    for context, onset, direction in ((first_context, first_event.onset, opening), (last_context, last_event.end, closing)):
        add_at(Spot(begin.part, context, read_measure(context.measure, context.divisions), begin.key, None, None), direction, onset)
    repair_lines(begin.part)


def toggle_named_line(root: ET.Element, first_id: str, last_id: str, name: str) -> None:
    if name not in TEXT_LINES:
        raise EditError("That isn't a line this app knows.")
    toggle_text_line(root, first_id, last_id, *TEXT_LINES[name])


def toggle_sul(root: ET.Element, first_id: str, last_id: str, where: str) -> None:
    """Ask for a passage to be played on one string, as "sul G", or near the bridge or over the fingerboard."""
    where = where.strip()
    part = locate(root, first_id).part
    names = {name.rstrip("0123456789").rstrip("#b") for name in string_names(root, part)}
    if where not in names and where not in SUL_PLACES:
        raise EditError(f"Say a string, such as {' or '.join(sorted(names)) or 'G'}, or pont. or tasto.")
    toggle_text_line(root, first_id, last_id, f"sul {where}", "above")


def directions_here(spot: Spot, wanted) -> list[ET.Element]:
    found = []
    for mark in read_measure(spot.context.measure, spot.context.divisions).marks:
        element = mark.element
        if element.tag == "direction" and mark.onset == spot.event.onset and (element.findtext("staff") or "1") == spot.key[0] and wanted(element):
            found.append(element)
    return found


def words_of(direction: ET.Element) -> str:
    return " ".join((words.text or "").strip() for words in direction.iter("words")).strip()


def toggle_words(root: ET.Element, note_id: str, text: str, kind_of, placement: str = "above", sound: dict | None = None) -> None:
    """Put words of one kind at a note, replacing others of that kind there. The same words again take them away."""
    spot = locate(root, note_id)
    existing = directions_here(spot, kind_of)
    same = any(words_of(found) == text for found in existing)
    for found in existing:
        spot.context.measure.remove(found)
    if same:
        return
    add_at(spot, make_direction(placement, [element_with_text("words", text)], sound, spot.key[0], several_staves(spot)), spot.event.onset)


def is_pizzicato_mark(direction: ET.Element) -> bool:
    return direction.find("sound[@pizzicato]") is not None


def set_pizzicato(root: ET.Element, note_id: str, on: bool) -> None:
    """Mark "pizz." or "arco" at a note. Playback plucks the strings from "pizz." until "arco"."""
    toggle_words(root, note_id, "pizz." if on else "arco", is_pizzicato_mark, sound={"pizzicato": "yes" if on else "no"})


def set_position(root: ET.Element, note_id: str, number: int) -> None:
    """Mark the position the hand plays in from a note, as "III pos.", with 0 for half position."""
    if not 0 <= number < len(ROMAN):
        raise EditError(f"A position is from ½ to {ROMAN[-1]}.")
    toggle_words(root, note_id, ROMAN[number] + POSITION_WORDS, lambda direction: words_of(direction).endswith(POSITION_WORDS))


HANDS = {"RH": "R.H.", "LH": "L.H."}
# The staff a note is drawn on, where that isn't the staff whose voice it belongs to. MusicXML says that
# with the note's <staff> alone, which this app's bars are read by, so a note kept here stays in its
# own staff's voice and only the drawing and a saved file move it (see drawn_on_other_staves).
CROSS_STAFF = "xrn-staff"


def toggle_hand(root: ET.Element, note_ids: list[str], hand: str) -> None:
    """Mark which hand plays the chosen notes, as "R.H." or "L.H." over them."""
    if hand not in HANDS:
        raise EditError("A hand is RH or LH.")
    for note in pitched(root, note_ids):
        toggle_words(root, note.get("id"), HANDS[hand], lambda direction: words_of(direction) in HANDS.values())


def cross_staff(root: ET.Element, note_id: str, change: int) -> None:
    """Draw a note, with the rest of its chord, on the staff above or below its own, as a pianist's other hand plays it.

    It stays a note of its own staff's voice, so its bar keeps adding up. Moved back, it's drawn where it belongs.
    """
    spot = locate(root, note_id)
    if spot.event.is_rest:
        raise EditError("A rest stays on its staff.")
    home = int(spot.key[0])
    now = int(spot.event.notes[0].get(CROSS_STAFF) or home)
    wanted = now + change
    tabs = set(fretted.tab_staves(spot.part))
    if not 1 <= wanted <= spot.context.staves or str(wanted) in tabs:
        raise EditError("There's no staff there to move it to.")
    for note in [*spot.event.grace, *spot.event.notes]:
        if wanted == home:
            note.attrib.pop(CROSS_STAFF, None)
        else:
            note.set(CROSS_STAFF, str(wanted))


def drawn_on_other_staves(root: ET.Element) -> ET.Element:
    """Put each note moved to another staff on that staff, as MusicXML and Verovio have it, for a file or a drawing."""
    for note in root.iter("note"):
        staff = note.attrib.pop(CROSS_STAFF, None)
        if staff is None:
            continue
        found = note.find("staff")
        if found is None:
            found = ET.Element("staff")
            place(note, found)
        found.text = staff
    return root


def keep_cross_staff(root: ET.Element) -> None:
    """Read a file's notes drawn on another staff as notes of their own voice, moved there.

    In MusicXML each voice's notes come one after another until a <backup>, and a pianist's note
    drawn on the other staff is one of them with that staff's number. So in a run of one voice's
    notes on two staves, the notes on the staff that isn't the voice's are moved there. Which staff
    is the voice's goes by how MuseScore and this app number voices, 1 to 4 on the first staff and 5
    to 8 on the second, or else by where most of its notes are. A run drawn wholly on the other
    staff is moved too, unless that staff's voice of that number has notes of its own in the bar:
    many programs number each staff's voices from 1.
    """
    from pipeline.structure import VOICES_PER_STAFF

    for part in root.findall("part"):
        staves = int(part.findtext("measure/attributes/staves") or 1)
        if staves < 2:
            continue
        for measure in part.findall("measure"):
            runs: list[list[ET.Element]] = [[]]
            for child in measure:
                if child.tag in ("backup", "forward"):
                    runs.append([])
                elif child.tag == "note":
                    runs[-1].append(child)
            groups = []
            for run in runs:
                voices: dict[str, list[ET.Element]] = {}
                for note in run:
                    voices.setdefault(note.findtext("voice") or "1", []).append(note)
                groups.extend(voices.items())
            natural = {(notes[0].findtext("staff") or "1", voice) for voice, notes in groups if len({note.findtext("staff") or "1" for note in notes}) == 1}
            for voice, notes in groups:
                drawn = [note.findtext("staff") or "1" for note in notes]
                usual = str((int(voice) - 1) // VOICES_PER_STAFF + 1) if voice.isdigit() and int(voice) >= 1 else None
                usual = usual if usual is not None and int(usual) <= staves else None
                if len(set(drawn)) > 1:
                    home = usual if usual in drawn else max(dict.fromkeys(drawn), key=drawn.count)
                elif usual is not None and drawn[0] != usual and (usual, voice) not in natural:
                    home = usual
                else:
                    continue
                for note, staff in zip(notes, drawn):
                    if staff != home:
                        note.set(CROSS_STAFF, staff)
                        found = note.find("staff")
                        if found is None:
                            place(note, element_with_text("staff", home))
                        else:
                            found.text = home
